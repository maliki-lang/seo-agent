"""
Scorecard — the company-standard 9-dimension, 0–100 scorecard.

Dimensions and weights are taken verbatim from the Lark "Agent Performance
Control Tower" default scorecard. Thresholds match the standard:

    85+   production-grade
    70-84 usable (fix within 7 days)
    50-69 at risk (owner fix before scale)
    <50   disable / rollback / rebuild

A Scorecard is built from deterministic graders (authoritative for hard rules)
and can be *blended down* by a separate critic agent (LLM-judge) — never blended
up, so judgment can only make the bar harder, never paper over a hard failure.

Persisted append-only to data_sources/ledger/eval_log.jsonl so every step's
score is saved, not just failures.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..ledger import LEDGER_DIR


EVAL_LOG = LEDGER_DIR / "eval_log.jsonl"

# Dimension -> weight (sums to 100). Order is the canonical display order.
DIMENSIONS: Dict[str, int] = {
    "source_grounding": 15,
    "tool_trajectory": 15,
    "metric_action_quality": 15,
    "safety_compliance": 15,
    "verification_evidence": 10,
    "noise_control": 10,
    "business_impact": 10,
    "reliability": 5,
    "self_improvement": 5,
}

# Threshold bands (inclusive lower bound).
BAND_SHIP = 85
BAND_USABLE = 70
BAND_AT_RISK = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ship_decision(score: float, hard_fail: bool = False) -> str:
    """Map a 0–100 score (+ any hard-fail flag) to a standard band label."""
    if hard_fail:
        return "block"
    if score >= BAND_SHIP:
        return "ship"
    if score >= BAND_USABLE:
        return "ship_with_fixes"
    if score >= BAND_AT_RISK:
        return "owner_fix"
    return "block"


@dataclass
class Scorecard:
    """A single evaluation of one artifact at one pipeline step."""

    step: str                       # e.g. "draft", "research_brief", "publish"
    slug: str = ""
    target_keyword: str = ""
    cluster: str = ""
    awareness_stage: str = ""
    grader: str = "deterministic"   # "deterministic" | "critic" | "blended"
    scores: Dict[str, float] = field(default_factory=dict)   # dim -> 0..100
    notes: Dict[str, List[str]] = field(default_factory=dict)  # dim -> messages
    hard_fail: bool = False         # zero-tolerance gate tripped (e.g. YMYL)
    hard_fail_reasons: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def total(self) -> float:
        """Weighted 0–100 total over the dimensions that were scored."""
        used = {d: w for d, w in DIMENSIONS.items() if d in self.scores}
        wsum = sum(used.values()) or 1
        return round(
            sum(self.scores[d] * w for d, w in used.items()) / wsum, 1
        )

    @property
    def band(self) -> str:
        return ship_decision(self.total, self.hard_fail)

    @property
    def passed(self) -> bool:
        """Ship-ready: usable band or better and no hard fail."""
        return (not self.hard_fail) and self.total >= BAND_USABLE

    def add(self, dimension: str, score: float, *messages: str) -> None:
        if dimension not in DIMENSIONS:
            raise KeyError(f"Unknown scorecard dimension: {dimension}")
        self.scores[dimension] = round(max(0.0, min(100.0, float(score))), 1)
        if messages:
            self.notes.setdefault(dimension, []).extend(m for m in messages if m)

    def trip_hard_fail(self, reason: str) -> None:
        self.hard_fail = True
        if reason:
            self.hard_fail_reasons.append(reason)

    def blend_down(self, critic: "Scorecard") -> "Scorecard":
        """
        Merge a critic (LLM-judge) scorecard into this deterministic one.

        Per the standard's quantitative > LLM-judge ordering, the critic can only
        *lower* a dimension (min of the two) and can *add* hard fails — it can
        never raise a deterministic score. Returns a new blended Scorecard.
        """
        merged = Scorecard(
            step=self.step,
            slug=self.slug,
            target_keyword=self.target_keyword,
            cluster=self.cluster,
            awareness_stage=self.awareness_stage,
            grader="blended",
            scores=dict(self.scores),
            notes={k: list(v) for k, v in self.notes.items()},
            hard_fail=self.hard_fail,
            hard_fail_reasons=list(self.hard_fail_reasons),
            meta={**self.meta, "critic_total": critic.total},
        )
        for dim, c_score in critic.scores.items():
            base = merged.scores.get(dim, c_score)
            if c_score < base:
                merged.scores[dim] = c_score
                merged.notes.setdefault(dim, []).append(
                    f"critic lowered {base}->{c_score}"
                )
            for msg in critic.notes.get(dim, []):
                merged.notes.setdefault(dim, []).append(f"critic: {msg}")
        if critic.hard_fail:
            merged.hard_fail = True
            merged.hard_fail_reasons.extend(
                f"critic: {r}" for r in critic.hard_fail_reasons
            )
        return merged

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recorded_at": _now_iso(),
            "step": self.step,
            "slug": self.slug,
            "target_keyword": self.target_keyword,
            "cluster": self.cluster,
            "awareness_stage": self.awareness_stage,
            "grader": self.grader,
            "total": self.total,
            "band": self.band,
            "passed": self.passed,
            "hard_fail": self.hard_fail,
            "hard_fail_reasons": self.hard_fail_reasons,
            "scores": self.scores,
            "weights": {d: DIMENSIONS[d] for d in self.scores},
            "notes": self.notes,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Scorecard":
        sc = cls(
            step=d.get("step", ""),
            slug=d.get("slug", ""),
            target_keyword=d.get("target_keyword", ""),
            cluster=d.get("cluster", ""),
            awareness_stage=d.get("awareness_stage", ""),
            grader=d.get("grader", "deterministic"),
            scores={k: float(v) for k, v in d.get("scores", {}).items()},
            notes=d.get("notes", {}) or {},
            hard_fail=bool(d.get("hard_fail", False)),
            hard_fail_reasons=d.get("hard_fail_reasons", []) or [],
            meta=d.get("meta", {}) or {},
        )
        return sc

    def persist(self) -> Dict[str, Any]:
        """Append this scorecard to the eval log (close-the-loop layer)."""
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        row = self.to_dict()
        with open(EVAL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return row

    def format_report(self) -> str:
        lines = [
            "=" * 56,
            f"SCORECARD — {self.step}  ({self.grader})",
            "=" * 56,
            f"Total: {self.total}/100   Band: {self.band.upper()}"
            f"   Ship-ready: {'YES' if self.passed else 'NO'}",
        ]
        if self.hard_fail:
            lines.append("HARD FAIL (zero-tolerance gate tripped):")
            for r in self.hard_fail_reasons:
                lines.append(f"  ⛔ {r}")
        lines.append("")
        for dim, weight in DIMENSIONS.items():
            if dim not in self.scores:
                continue
            s = self.scores[dim]
            mark = "OK" if s >= BAND_USABLE else "LOW"
            lines.append(f"  {dim:24} {s:5.1f}/100  (w {weight:>2}%) [{mark}]")
            for note in self.notes.get(dim, [])[:3]:
                lines.append(f"        - {note}")
        lines.append("=" * 56)
        return "\n".join(lines)


def append_eval_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Append an arbitrary eval row to the eval log — used by the research-step
    evals (keyword pick, brief) which don't use the 9-dimension article
    scorecard but must still be persisted so the dashboard and calibration see
    every step. Rows should carry at least: step, slug, total, band, passed.
    """
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    row.setdefault("recorded_at", _now_iso())
    with open(EVAL_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def read_eval_log() -> List[Dict[str, Any]]:
    if not EVAL_LOG.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with open(EVAL_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows
