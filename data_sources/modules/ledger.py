"""
Publish Ledger

Append-only log of every published article with a T-0 baseline snapshot of
GSC + GA4 metrics. Enables post-publish tracking and rank/organic-impact
feedback via track_published.py.

Files (auto-created under data_sources/ledger/):
  - published.jsonl    one row per publish event
  - checkpoints.jsonl  one row per T+N day checkpoint (written by tracker)
"""

import json
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse


LEDGER_DIR = Path(__file__).parent.parent / "ledger"
PUBLISHED_LEDGER = LEDGER_DIR / "published.jsonl"
CHECKPOINTS_LOG = LEDGER_DIR / "checkpoints.jsonl"


def _ensure_dir() -> None:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).path or ""
    except Exception:
        return ""


def _capture_gsc_baseline(url: str, slug: str) -> Dict[str, Any]:
    """Pull current GSC metrics. Returns zeros + _error if unavailable."""
    try:
        from .google_search_console import GoogleSearchConsole

        gsc = GoogleSearchConsole()
        data = gsc.get_page_performance(url or slug, days=30)
        if "error" in data:
            return {"position": None, "impressions": 0, "clicks": 0, "ctr": 0.0}
        return {
            "position": data.get("avg_position"),
            "impressions": data.get("impressions", 0),
            "clicks": data.get("clicks", 0),
            "ctr": data.get("ctr", 0.0),
        }
    except Exception as e:
        return {
            "position": None,
            "impressions": 0,
            "clicks": 0,
            "ctr": 0.0,
            "_error": str(e),
        }


def _capture_ga4_baseline(path: str, slug: str) -> Dict[str, Any]:
    """Pull current GA4 metrics. Returns zeros + _error if unavailable."""
    try:
        from .google_analytics import GoogleAnalytics

        ga4 = GoogleAnalytics()
        target = path or f"/{slug}"
        data = ga4.get_page_performance(target, days=30)
        if not data:
            return {"sessions": 0, "pageviews": 0, "engagement_rate": 0.0}
        return {
            "sessions": data.get("sessions", 0),
            "pageviews": data.get("pageviews", 0),
            "engagement_rate": data.get("engagement_rate", 0.0),
        }
    except Exception as e:
        return {
            "sessions": 0,
            "pageviews": 0,
            "engagement_rate": 0.0,
            "_error": str(e),
        }


def record_publish(
    slug: str,
    target_keyword: str,
    platform: str,
    post_id: str,
    source_file: str = "",
    url: str = "",
    secondary_keywords: Optional[List[str]] = None,
    cluster: str = "",
    opportunity_type: str = "",
    publish_date: Optional[str] = None,
    capture_baseline: bool = True,
) -> Dict[str, Any]:
    """
    Append a publish event to the ledger.

    Captures a T-0 baseline from GSC + GA4 best-effort — zeros if unavailable.
    Designed to fail gracefully so a publish is never blocked by a tracking
    error.

    Args:
        slug: URL slug of the article
        target_keyword: Primary keyword the article targets
        platform: "wordpress", "shopline", "backfill", etc.
        post_id: Platform-side post/article ID
        source_file: Path to the source markdown file (optional)
        url: Public live URL (optional — tracker can fall back to slug match)
        secondary_keywords: Supporting keywords
        cluster: Topic cluster / category
        publish_date: ISO date (YYYY-MM-DD). Defaults to today UTC.
        capture_baseline: If False, skip the GSC/GA4 baseline pull.

    Returns:
        The entry dict that was appended to the ledger.
    """
    _ensure_dir()
    entry: Dict[str, Any] = {
        "slug": slug,
        "url": url,
        "path": _path_from_url(url),
        "target_keyword": target_keyword,
        "secondary_keywords": secondary_keywords or [],
        "cluster": cluster,
        "opportunity_type": opportunity_type,
        "platform": platform,
        "post_id": str(post_id) if post_id else "",
        "source_file": source_file,
        "publish_date": publish_date
        or datetime.now(timezone.utc).date().isoformat(),
        "recorded_at": _now_iso(),
    }
    if capture_baseline:
        entry["baseline"] = {
            "captured_at": _now_iso(),
            "gsc": _capture_gsc_baseline(url, slug),
            "ga4": _capture_ga4_baseline(entry["path"], slug),
        }
    else:
        entry["baseline"] = None

    with open(PUBLISHED_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read_ledger() -> List[Dict[str, Any]]:
    """Read all published entries from the ledger."""
    if not PUBLISHED_LEDGER.exists():
        return []
    entries: List[Dict[str, Any]] = []
    with open(PUBLISHED_LEDGER, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def read_checkpoints() -> List[Dict[str, Any]]:
    """Read all checkpoint rows."""
    if not CHECKPOINTS_LOG.exists():
        return []
    entries: List[Dict[str, Any]] = []
    with open(CHECKPOINTS_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def append_checkpoint(checkpoint: Dict[str, Any]) -> None:
    """Append a checkpoint row to checkpoints.jsonl."""
    _ensure_dir()
    with open(CHECKPOINTS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(checkpoint) + "\n")
