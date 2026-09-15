"""
External review loop — submit a draft to the manager's review group, collect the
reviewer-agent's free-text verdict, and track each revision round.

This is the *external* gate that sits AFTER our own internal eval gate. The flow
the command (`/submit-for-review`) drives:

    internal gate PASS
        -> post draft to the Lark review group (as the bot)
        -> wait for the reviewer-agent's reply (background poll, bounded)
        -> parse the free-text verdict (score + approved? + feedback)
        -> if approved: stop, hand to /publish-draft
        -> else: revise per feedback, RE-RUN internal gate, repost  (round++)
        -> cap at MAX_ROUNDS, then escalate to a human

This module owns only the deterministic parts: the Lark transport (via lark-cli),
a tolerant parser for the free-text reply, and an append-only round ledger. The
revision itself is an LLM step performed by the command — by design, so the
writer never grades or rubber-stamps its own resubmission.

The reviewer uses *their own* scoring system and threshold; we do not assume our
9-dimension scorecard maps to theirs. We record both each round so calibration
can later answer "does our internal score predict the manager-agent's verdict?".

CLI (from repo root):
    python3 -m data_sources.modules.review_loop post  --chat <oc_id> --file draft.md [--as bot] [--note "..."]
    python3 -m data_sources.modules.review_loop wait  --chat <oc_id> --since-ms <ms> --exclude-sender <id> \
                                                      [--reviewer <id>] [--timeout 1800] [--poll 60] --out reply.json
    python3 -m data_sources.modules.review_loop parse --file reply.json [--threshold 85]
    python3 -m data_sources.modules.review_loop log   --slug s --round 1 --internal-score 88 \
                                                      --manager-score 80 --approved false --reply-file reply.json
"""

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ledger import LEDGER_DIR

REVIEW_ROUNDS_LOG = LEDGER_DIR / "review_rounds.jsonl"

# Safety bound: stop revising after this many rounds and escalate to a human.
MAX_ROUNDS = 4
# Per-round wait before we give up listening (seconds). 4 * 30min ≈ the 2h cap.
DEFAULT_WAIT_TIMEOUT = 1800
DEFAULT_POLL_INTERVAL = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Free-text verdict parser
# --------------------------------------------------------------------------- #

# Score patterns, most explicit first. Each yields (value, scale).
_SCORE_PATTERNS = [
    (re.compile(r"(\d{1,3}(?:\.\d+)?)\s*/\s*100\b"), 100),
    (re.compile(r"(\d{1,2}(?:\.\d+)?)\s*/\s*10\b"), 10),
    (re.compile(r"\b(?:score|rating|rated|overall)\b[^\d]{0,12}(\d{1,3}(?:\.\d+)?)\b", re.I), 100),
]
_THRESHOLD_PATTERNS = [
    re.compile(r"\b(?:threshold|minimum|min|pass(?:ing)? (?:mark|bar|score)|bar)\b[^\d]{0,12}(\d{1,3})\b", re.I),
    re.compile(r"\bneeds?\s+(?:at least\s+)?(\d{1,3})\b", re.I),
]

_APPROVE_TOKENS = [
    "approved", "approve for publish", "approve to publish", "approve",
    "lgtm", "ship it", "ready to publish", "good to publish", "good to go",
    "publish away", "cleared to publish", "passes", "passed", "looks good",
    "no changes", "✅",
]
_REJECT_TOKENS = [
    "not approved", "do not publish", "don't publish", "not ready",
    "needs revision", "needs revisions", "needs work", "needs more work",
    "revise", "revision required", "changes required", "request changes",
    "rejected", "reject", "below threshold", "below the threshold",
    "fails", "failed", "not yet", "❌",
]


@dataclass
class ReviewVerdict:
    """Structured best-effort read of the reviewer-agent's free-text reply.

    `approved` is True / False / None. None means the text was ambiguous and the
    calling LLM must make the call from the raw reply — we never silently treat
    ambiguity as approval.
    """

    approved: Optional[bool]
    score: Optional[float] = None          # normalised to /100 when found
    threshold: Optional[float] = None
    feedback: List[str] = field(default_factory=list)
    confidence: str = "low"                # high | medium | low
    raw: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "score": self.score,
            "threshold": self.threshold,
            "feedback": self.feedback,
            "confidence": self.confidence,
        }


def _extract_score(text: str) -> Optional[float]:
    for pattern, scale in _SCORE_PATTERNS:
        m = pattern.search(text)
        if m:
            val = float(m.group(1))
            if scale == 10:
                val *= 10
            if 0 <= val <= 100:
                return round(val, 1)
    return None


def _extract_threshold(text: str) -> Optional[float]:
    for pattern in _THRESHOLD_PATTERNS:
        m = pattern.search(text)
        if m:
            val = float(m.group(1))
            if 0 <= val <= 100:
                return val
    return None


def _extract_feedback(text: str) -> List[str]:
    """Pull bullet / numbered / 'fix:'-style lines as actionable feedback."""
    items: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^([-*•]|\d+[.)])\s+", line):
            items.append(re.sub(r"^([-*•]|\d+[.)])\s+", "", line).strip())
        elif re.match(r"^(fix|add|remove|change|clarify|cite|shorten|expand|rewrite)\b",
                      line, re.I):
            items.append(line)
    return [i for i in items if i]


def parse_review_verdict(text: str, threshold: Optional[float] = None) -> ReviewVerdict:
    low = text.lower()
    score = _extract_score(text)
    found_threshold = _extract_threshold(text)
    eff_threshold = threshold if threshold is not None else found_threshold

    has_reject = any(tok in low for tok in _REJECT_TOKENS)
    has_approve = any(tok in low for tok in _APPROVE_TOKENS)

    approved: Optional[bool]
    confidence = "low"

    # Negative phrasing wins over positive ("not approved" contains "approve").
    if has_reject:
        approved, confidence = False, "high"
    elif has_approve:
        approved, confidence = True, "high"
    elif score is not None and eff_threshold is not None:
        approved = score >= eff_threshold
        confidence = "medium"
    else:
        approved = None  # ambiguous — defer to the LLM reading raw

    # A score below a stated threshold is a hard NO even if a stray positive word
    # appears.
    if score is not None and eff_threshold is not None and score < eff_threshold:
        approved, confidence = False, "high"

    return ReviewVerdict(
        approved=approved,
        score=score,
        threshold=eff_threshold,
        feedback=_extract_feedback(text),
        confidence=confidence,
        raw=text,
    )


# --------------------------------------------------------------------------- #
# Lark transport (thin wrappers over lark-cli)
# --------------------------------------------------------------------------- #

def _lark(args: List[str]) -> Dict[str, Any]:
    """Run a lark-cli subcommand and parse its JSON stdout."""
    proc = subprocess.run(
        ["lark-cli", *args],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"lark-cli {' '.join(args[:2])} failed ({proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    out = proc.stdout.strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"_raw": out}


def _message_text(item: Dict[str, Any]) -> str:
    """Best-effort plain-text extraction from a Lark message item body."""
    body = item.get("body") or {}
    content = body.get("content")
    if not content:
        return ""
    try:
        data = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError:
        return str(content)
    if isinstance(data, dict):
        if "text" in data:                       # text message
            return str(data["text"])
        # post message: {title, content: [[{tag, text}, ...], ...]}
        if "content" in data and isinstance(data["content"], list):
            parts: List[str] = []
            title = data.get("title")
            if title:
                parts.append(str(title))
            for line in data["content"]:
                if isinstance(line, list):
                    parts.append("".join(
                        seg.get("text", "") for seg in line
                        if isinstance(seg, dict)
                    ))
            return "\n".join(p for p in parts if p)
    return str(data)


def _sender_id(item: Dict[str, Any]) -> str:
    sender = item.get("sender") or {}
    return sender.get("id") or sender.get("sender_id", {}).get("open_id", "") or ""


def post_draft(chat_id: str, file_path: str, as_identity: str = "bot",
               note: str = "") -> Dict[str, Any]:
    """Post a draft markdown file to the review group. Returns send metadata."""
    text = Path(file_path).read_text(encoding="utf-8")
    header = note.strip() or (
        "SEO draft submitted for review. Please reply with a score and any "
        "changes needed (or approve to publish)."
    )
    markdown = f"**{header}**\n\n---\n\n{text}"
    resp = _lark([
        "im", "+messages-send",
        "--chat-id", chat_id,
        "--as", as_identity,
        "--markdown", markdown,
    ])
    data = resp.get("data", resp)
    message_id = data.get("message_id") or data.get("message_id", "")
    sender = _sender_id(data) if isinstance(data, dict) else ""
    return {
        "message_id": message_id,
        "self_sender": sender,
        "sent_at_ms": int(time.time() * 1000),
        "sent_at_iso": _now_iso(),
    }


def fetch_replies(chat_id: str, since_ms: int, as_identity: str = "bot",
                  exclude_sender: str = "", reviewer: str = "",
                  exclude_message_id: str = "") -> List[Dict[str, Any]]:
    """Return messages in `chat_id` newer than `since_ms`, oldest first,
    excluding our own (`exclude_sender` / `exclude_message_id`) and optionally
    restricted to a known reviewer open_id."""
    start_iso = datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).isoformat()
    resp = _lark([
        "im", "+chat-messages-list",
        "--chat-id", chat_id,
        "--as", as_identity,
        "--sort", "asc",
        "--start", start_iso,
        "--page-size", "50",
    ])
    data = resp.get("data", resp)
    items = data.get("items", []) if isinstance(data, dict) else []
    replies: List[Dict[str, Any]] = []
    for it in items:
        ct = int(it.get("create_time", "0") or 0)
        if ct <= since_ms:
            continue
        if exclude_message_id and it.get("message_id") == exclude_message_id:
            continue  # never mistake our own posted draft for a reply
        sid = _sender_id(it)
        if exclude_sender and sid == exclude_sender:
            continue
        if reviewer and sid != reviewer:
            continue
        text = _message_text(it)
        if not text.strip():
            continue
        replies.append({
            "message_id": it.get("message_id", ""),
            "sender": sid,
            "create_time_ms": ct,
            "text": text,
        })
    return replies


def wait_for_reply(chat_id: str, since_ms: int, as_identity: str = "bot",
                   exclude_sender: str = "", reviewer: str = "",
                   timeout: int = DEFAULT_WAIT_TIMEOUT,
                   poll: int = DEFAULT_POLL_INTERVAL,
                   exclude_message_id: str = "") -> Optional[Dict[str, Any]]:
    """Block (polling every `poll`s) until a qualifying reply appears or
    `timeout`s elapse. Returns the first qualifying reply or None on timeout.

    Designed to run in a background process: time.sleep here is the script
    sleeping, so it does not trip the foreground-sleep guard."""
    deadline = time.time() + timeout
    while True:
        try:
            replies = fetch_replies(chat_id, since_ms, as_identity,
                                    exclude_sender, reviewer, exclude_message_id)
        except RuntimeError as e:
            # Transient Lark/auth hiccup — report once and keep polling.
            print(f"poll error: {e}", file=sys.stderr)
            replies = []
        if replies:
            return replies[0]
        if time.time() >= deadline:
            return None
        time.sleep(min(poll, max(1, int(deadline - time.time()))))


# --------------------------------------------------------------------------- #
# Round ledger
# --------------------------------------------------------------------------- #

def record_round(slug: str, round_no: int, internal_score: Optional[float],
                 manager_score: Optional[float], approved: Optional[bool],
                 reviewer_text: str = "", changes: Optional[List[str]] = None,
                 cluster: str = "", target_keyword: str = "") -> Dict[str, Any]:
    """Append one external-review round to the round ledger AND a summary row to
    the eval log (so the dashboard / calibration see external rounds too)."""
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "recorded_at": _now_iso(),
        "slug": slug,
        "round": round_no,
        "internal_score": internal_score,
        "manager_score": manager_score,
        "approved": approved,
        "reviewer_text": reviewer_text[:4000],
        "changes_made": changes or [],
        "cluster": cluster,
        "target_keyword": target_keyword,
    }
    with open(REVIEW_ROUNDS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")

    try:
        from .eval.scorecard import append_eval_row
        append_eval_row({
            "step": "external_review",
            "slug": slug,
            "target_keyword": target_keyword,
            "cluster": cluster,
            "grader": "manager_agent",
            "total": manager_score,
            "internal_score": internal_score,
            "band": "ship" if approved else ("owner_fix" if approved is False else "pending"),
            "passed": bool(approved),
            "round": round_no,
        })
    except Exception:
        pass
    return row


def read_rounds(slug: str = "") -> List[Dict[str, Any]]:
    if not REVIEW_ROUNDS_LOG.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with open(REVIEW_ROUNDS_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not slug or r.get("slug") == slug:
                rows.append(r)
    return rows


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="External review loop helper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_post = sub.add_parser("post", help="Post a draft to the review group")
    p_post.add_argument("--chat", required=True)
    p_post.add_argument("--file", required=True)
    p_post.add_argument("--as", dest="as_identity", default="bot")
    p_post.add_argument("--note", default="")

    p_wait = sub.add_parser("wait", help="Block until the reviewer replies")
    p_wait.add_argument("--chat", required=True)
    p_wait.add_argument("--since-ms", type=int, required=True)
    p_wait.add_argument("--as", dest="as_identity", default="bot")
    p_wait.add_argument("--exclude-sender", default="")
    p_wait.add_argument("--exclude-message", default="")
    p_wait.add_argument("--reviewer", default="")
    p_wait.add_argument("--timeout", type=int, default=DEFAULT_WAIT_TIMEOUT)
    p_wait.add_argument("--poll", type=int, default=DEFAULT_POLL_INTERVAL)
    p_wait.add_argument("--out", default="")

    p_parse = sub.add_parser("parse", help="Parse a saved reply into a verdict")
    p_parse.add_argument("--file", required=True, help="reply JSON or raw text file")
    p_parse.add_argument("--threshold", type=float)

    p_log = sub.add_parser("log", help="Record a review round")
    p_log.add_argument("--slug", required=True)
    p_log.add_argument("--round", type=int, required=True)
    p_log.add_argument("--internal-score", type=float)
    p_log.add_argument("--manager-score", type=float)
    p_log.add_argument("--approved", choices=["true", "false", "unknown"], default="unknown")
    p_log.add_argument("--reply-file", default="")
    p_log.add_argument("--changes", default="")
    p_log.add_argument("--cluster", default="")
    p_log.add_argument("--keyword", default="")

    a = ap.parse_args(argv)

    if a.cmd == "post":
        meta = post_draft(a.chat, a.file, a.as_identity, a.note)
        print(json.dumps(meta))
        return 0

    if a.cmd == "wait":
        reply = wait_for_reply(
            a.chat, a.since_ms, a.as_identity, a.exclude_sender,
            a.reviewer, a.timeout, a.poll, a.exclude_message,
        )
        payload = reply or {"timeout": True}
        if a.out:
            Path(a.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload))
        return 0 if reply else 124

    if a.cmd == "parse":
        raw = Path(a.file).read_text(encoding="utf-8")
        try:
            obj = json.loads(raw)
            text = obj.get("text", raw) if isinstance(obj, dict) else raw
        except json.JSONDecodeError:
            text = raw
        verdict = parse_review_verdict(text, a.threshold)
        print(json.dumps(verdict.to_dict(), indent=2))
        return 0

    if a.cmd == "log":
        approved = {"true": True, "false": False, "unknown": None}[a.approved]
        text = ""
        if a.reply_file and Path(a.reply_file).exists():
            raw = Path(a.reply_file).read_text(encoding="utf-8")
            try:
                obj = json.loads(raw)
                text = obj.get("text", raw) if isinstance(obj, dict) else raw
            except json.JSONDecodeError:
                text = raw
        changes = [c.strip() for c in a.changes.split("|") if c.strip()]
        row = record_round(
            a.slug, a.round, a.internal_score, a.manager_score, approved,
            text, changes, a.cluster, a.keyword,
        )
        print(json.dumps(row))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
