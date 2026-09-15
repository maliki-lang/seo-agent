#!/usr/bin/env python3
"""
Track Published Articles

Reads the publish ledger, identifies articles that have reached a checkpoint
age (default: 14, 30, 60, 90 days post-publish), pulls fresh GSC + GA4 data,
computes deltas vs. the T-0 baseline, classifies the outcome, and appends a
row to checkpoints.jsonl.

Designed to run from cron daily. Idempotent: each (slug, milestone_day) is
written at most once.

Usage:
    python3 track_published.py                # capture due checkpoints
    python3 track_published.py --dry-run      # show what would be written
    python3 track_published.py --verbose      # extra logging
"""

import argparse
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from dotenv import load_dotenv

load_dotenv()
_alt_env = Path(__file__).parent / "data_sources" / "config" / ".env"
if _alt_env.exists():
    load_dotenv(_alt_env)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "data_sources"))
from modules.ledger import (  # noqa: E402
    read_ledger,
    read_checkpoints,
    append_checkpoint,
)


CHECKPOINT_DAYS = [14, 30, 60, 90]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _days_since(publish_date_iso: str) -> int:
    return (date.today() - date.fromisoformat(publish_date_iso)).days


def _captured_milestones(slug: str, checkpoints: List[Dict[str, Any]]) -> List[int]:
    return [c.get("milestone_day") for c in checkpoints if c.get("slug") == slug]


def _due_milestones(
    entry: Dict[str, Any], checkpoints: List[Dict[str, Any]]
) -> List[int]:
    age = _days_since(entry["publish_date"])
    already = set(_captured_milestones(entry["slug"], checkpoints))
    return [m for m in CHECKPOINT_DAYS if age >= m and m not in already]


def _fetch_gsc(url: str, slug: str) -> Dict[str, Any]:
    try:
        from modules.google_search_console import GoogleSearchConsole

        gsc = GoogleSearchConsole()
        data = gsc.get_page_performance(url or slug, days=28)
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


def _fetch_ga4(path: str, slug: str) -> Dict[str, Any]:
    try:
        from modules.google_analytics import GoogleAnalytics

        ga4 = GoogleAnalytics()
        data = ga4.get_page_performance(path or f"/{slug}", days=28)
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


def _delta(current: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if current is None or baseline is None:
        return None
    return round(float(current) - float(baseline), 2)


def _compute_deltas(
    baseline: Optional[Dict[str, Any]], current: Dict[str, Any]
) -> Dict[str, Any]:
    b = baseline or {}
    bg = (b.get("gsc") or {}) if isinstance(b, dict) else {}
    ba = (b.get("ga4") or {}) if isinstance(b, dict) else {}
    cg = current.get("gsc", {})
    ca = current.get("ga4", {})
    return {
        # Negative position delta = rank improvement (lower number is better)
        "position": _delta(cg.get("position"), bg.get("position")),
        "impressions": _delta(cg.get("impressions"), bg.get("impressions", 0)),
        "clicks": _delta(cg.get("clicks"), bg.get("clicks", 0)),
        "sessions": _delta(ca.get("sessions"), ba.get("sessions", 0)),
    }


def _classify(
    deltas: Dict[str, Any],
    baseline: Optional[Dict[str, Any]],
) -> str:
    """
    Outcome labels:
      winner — rank ↑ ≥3 positions  OR  clicks grew ≥50%
      loser  — rank ↓ ≥3 positions  OR  clicks fell ≥30%
      mover  — any directional positive change
      flat   — no meaningful movement
    """
    pos_d = deltas.get("position")
    clicks_d = deltas.get("clicks") or 0
    baseline_clicks = 0
    if baseline and isinstance(baseline, dict):
        baseline_clicks = (baseline.get("gsc") or {}).get("clicks", 0) or 0

    clicks_growth = (clicks_d / baseline_clicks) if baseline_clicks else None

    # Winner conditions
    if pos_d is not None and pos_d <= -3:
        return "winner"
    if clicks_growth is not None and clicks_growth >= 0.5:
        return "winner"
    # From-zero growth: at least 5 absolute new clicks counts as a winner
    if baseline_clicks == 0 and clicks_d >= 5:
        return "winner"

    # Loser conditions
    if pos_d is not None and pos_d >= 3:
        return "loser"
    if clicks_growth is not None and clicks_growth <= -0.3:
        return "loser"

    # Mover: any positive directional movement
    if (pos_d is not None and pos_d < 0) or clicks_d > 0:
        return "mover"
    return "flat"


def run(dry_run: bool = False, verbose: bool = False) -> Dict[str, int]:
    entries = read_ledger()
    checkpoints = read_checkpoints()

    if not entries:
        print("No entries in publish ledger. Nothing to track.")
        return {"checked": 0, "captured": 0}

    captured = 0
    for entry in entries:
        try:
            due = _due_milestones(entry, checkpoints)
        except Exception as e:
            print(f"⚠ Skipping {entry.get('slug', '?')}: {e}")
            continue
        if not due:
            if verbose:
                age = _days_since(entry["publish_date"])
                print(f"  · {entry['slug']} @ T+{age}d — no milestone due")
            continue

        # Capture the largest due milestone this run; earlier missed ones get
        # picked up on subsequent runs since they remain "due" until written.
        # We capture one per article per run to keep GSC/GA4 calls bounded.
        milestone = max(due)

        if verbose:
            print(f"  → {entry['slug']} @ T+{milestone}d (fetching live data)")

        current = {
            "gsc": _fetch_gsc(entry.get("url", ""), entry["slug"]),
            "ga4": _fetch_ga4(entry.get("path", ""), entry["slug"]),
        }
        deltas = _compute_deltas(entry.get("baseline"), current)
        outcome = _classify(deltas, entry.get("baseline"))

        row = {
            "slug": entry["slug"],
            "url": entry.get("url", ""),
            "target_keyword": entry.get("target_keyword", ""),
            "cluster": entry.get("cluster", ""),
            "opportunity_type": entry.get("opportunity_type", ""),
            "platform": entry.get("platform", ""),
            "publish_date": entry["publish_date"],
            "milestone_day": milestone,
            "age_days": _days_since(entry["publish_date"]),
            "captured_at": _now_iso(),
            "current": current,
            "baseline": entry.get("baseline"),
            "deltas": deltas,
            "outcome": outcome,
        }

        if dry_run:
            print(
                f"DRY RUN: {entry['slug']} @ T+{milestone}d → {outcome} "
                f"(pos Δ={deltas['position']}, clicks Δ={deltas['clicks']})"
            )
        else:
            append_checkpoint(row)
            captured += 1
            print(
                f"✓ {entry['slug']} @ T+{milestone}d → {outcome} "
                f"(pos Δ={deltas['position']}, clicks Δ={deltas['clicks']})"
            )

    print(
        f"\nDone. Checked {len(entries)} entr"
        f"{'y' if len(entries) == 1 else 'ies'}, "
        f"captured {captured} new checkpoint{'s' if captured != 1 else ''}."
    )

    # Refresh learned weights so the next opportunity_scorer run picks up new signal.
    if not dry_run:
        try:
            from modules.learned_weights import refresh as refresh_weights
            weights = refresh_weights()
            n_clusters = len(weights.get("cluster_weights", {}))
            n_types = len(weights.get("type_weights", {}))
            print(
                f"✓ Refreshed learned weights "
                f"({weights.get('total_checkpoints', 0)} checkpoint(s) → "
                f"{n_clusters} cluster, {n_types} type group(s))"
            )
        except Exception as e:
            print(f"⚠ Could not refresh learned weights: {e}")

    return {"checked": len(entries), "captured": captured}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Track published article performance"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without writing",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Verbose output"
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run, verbose=args.verbose)
