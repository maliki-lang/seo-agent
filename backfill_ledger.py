#!/usr/bin/env python3
"""
Backfill Publish Ledger

One-time (or repeatable, idempotent) script to populate the publish ledger
from existing markdown files in published/. For each file, parses metadata,
derives a publish date from git first-add date (fallback: file mtime), and
writes a ledger entry capturing a current GSC/GA4 snapshot as a synthetic
baseline.

Note: for backfilled entries the "baseline" reflects current metrics, not
metrics at the original publish time. Deltas computed by track_published.py
will therefore measure change *from now onward*, not lifetime change.

Usage:
    python3 backfill_ledger.py              # scan ./published, write entries
    python3 backfill_ledger.py --dry-run    # show what would be added
    python3 backfill_ledger.py --dir path/  # scan a different directory
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
# Also try data_sources/config/.env (repo convention)
_alt_env = Path(__file__).parent / "data_sources" / "config" / ".env"
if _alt_env.exists():
    load_dotenv(_alt_env)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "data_sources"))
from modules.ledger import record_publish, read_ledger  # noqa: E402


def _extract_field(content: str, field_name: str) -> str:
    pattern = rf"\*\*{field_name}\*\*:\s*(.+?)(?:\n|$)"
    m = re.search(pattern, content, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _git_first_add_date(file_path: Path) -> str:
    """Return YYYY-MM-DD of the first commit that added this file, or ''."""
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--diff-filter=A",
                "--follow",
                "--format=%aI",
                "--",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            cwd=str(file_path.parent),
        )
        lines = [ln for ln in result.stdout.strip().split("\n") if ln]
        if lines:
            return lines[-1].split("T")[0]
    except Exception:
        pass
    return ""


def _parse_file(file_path: Path) -> dict:
    content = file_path.read_text(encoding="utf-8")
    slug_m = re.search(
        r"\*\*URL Slug\*\*:\s*/?(?:blog(?:s)?/)?([^\s/]+)/?",
        content,
        re.IGNORECASE,
    )
    slug = slug_m.group(1).strip() if slug_m else file_path.stem
    secondary_raw = _extract_field(content, "Secondary Keywords")
    return {
        "slug": slug,
        "target_keyword": _extract_field(content, "Target Keyword"),
        "secondary_keywords": [
            k.strip() for k in secondary_raw.split(",") if k.strip()
        ],
        "cluster": _extract_field(content, "Category"),
    }


def backfill(published_dir: Path, dry_run: bool = False) -> int:
    if not published_dir.exists():
        print(f"Directory not found: {published_dir}")
        return 0

    existing_slugs = {e.get("slug") for e in read_ledger()}
    files = sorted(published_dir.glob("*.md"))
    if not files:
        print(f"No .md files found in {published_dir}")
        return 0

    added = 0
    for f in files:
        meta = _parse_file(f)
        if meta["slug"] in existing_slugs:
            print(f"⊘ {meta['slug']} already in ledger, skipping")
            continue
        pub_date = _git_first_add_date(f) or date.fromtimestamp(
            f.stat().st_mtime
        ).isoformat()

        if dry_run:
            print(
                f"DRY RUN: would add {meta['slug']} "
                f"(target='{meta['target_keyword']}', published={pub_date})"
            )
            continue

        record_publish(
            slug=meta["slug"],
            target_keyword=meta["target_keyword"],
            platform="backfill",
            post_id="",
            source_file=str(f),
            url="",
            secondary_keywords=meta["secondary_keywords"],
            cluster=meta["cluster"],
            publish_date=pub_date,
            capture_baseline=True,
        )
        added += 1
        print(f"✓ Added {meta['slug']} (published {pub_date})")

    print(f"\nBackfill complete. Added {added} entr{'y' if added == 1 else 'ies'}.")
    return added


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill publish ledger")
    parser.add_argument(
        "--dir",
        default="published",
        help="Directory to scan (default: ./published)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be added without writing",
    )
    args = parser.parse_args()
    backfill(Path(args.dir), dry_run=args.dry_run)
