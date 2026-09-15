#!/usr/bin/env python3
"""
SEO Agent — Daily Sync to Sunnystep Agent Operating System
Runs nightly at midnight SGT via launchd.
Updates Agent Daily Sync Manifest in Ting's Agents Control Plane Base.
"""

import os
import json
import subprocess
import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent
BASE_TOKEN = "Ewbyb06HzamwGJs2Oqkl7bUggWg"
MANIFEST_TABLE = "tbl4WOkvWqh2QXZy"
REGISTRY_TABLE = "tbllUUb7MtE0vB60"
REGISTRY_RECORD = "recvk3Bwf51Hzm"
AGENT_FOLDER = "https://zlsbwg9eee.sg.larksuite.com/drive/folder/WCWCfOsg7lOeAPdgCpYlJ5i7gEh"


def count_files(folder: str) -> int:
    p = REPO_ROOT / folder
    if not p.exists():
        return 0
    return len([f for f in p.iterdir() if f.is_file() and not f.name.startswith(".")])


def get_latest_publish_date() -> str:
    published = REPO_ROOT / "published"
    if not published.exists():
        return "No published articles yet"
    files = sorted(published.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return "No published articles yet"
    ts = datetime.datetime.fromtimestamp(files[0].stat().st_mtime)
    return ts.strftime("%Y-%m-%d")


def run_lark_cli(args: list) -> dict:
    result = subprocess.run(
        ["lark-cli"] + args,
        capture_output=True, text=True
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": result.stderr}


def main():
    now_sgt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    sync_date = now_sgt.strftime("%Y-%m-%d")
    sync_title = f"SEO Agent daily sync — {sync_date}"

    drafts = count_files("drafts")
    published = count_files("published")
    research = count_files("research")
    total_files = drafts + published + research
    last_publish = get_latest_publish_date()

    health_notes = (
        f"Published: {published} articles | Drafts: {drafts} | Research briefs: {research} | "
        f"Last publish: {last_publish}"
    )

    # Write manifest row
    manifest_row = {
        "Sync Title": sync_title,
        "Agent ID": "SEO Agent",
        "Plane": "General",
        "Local Workspace Path": str(REPO_ROOT),
        "Agent Folder Link": AGENT_FOLDER,
        "Files Included": total_files,
        "Files Excluded": 0,
        "Replication Health Notes": health_notes,
        "Blocker / Risk": "None",
        "Evidence Link": AGENT_FOLDER,
    }

    result = run_lark_cli([
        "base", "+record-batch-create",
        "--base-token", BASE_TOKEN,
        "--table-id", MANIFEST_TABLE,
        "--json", json.dumps({
            "fields": list(manifest_row.keys()),
            "rows": [list(manifest_row.values())]
        })
    ])

    if result.get("ok"):
        print(f"[OK] Manifest row written: {sync_title}")
    else:
        print(f"[ERROR] Manifest write failed: {result}")

    # Update registry row Last Folder Update At
    update_result = run_lark_cli([
        "base", "+record-upsert",
        "--base-token", BASE_TOKEN,
        "--table-id", REGISTRY_TABLE,
        "--record-id", REGISTRY_RECORD,
        "--json", json.dumps({"Last Folder Update At": sync_date})
    ])

    if update_result.get("ok"):
        print(f"[OK] Registry Last Folder Update At updated to {sync_date}")
    else:
        print(f"[ERROR] Registry update failed: {update_result}")


if __name__ == "__main__":
    main()
