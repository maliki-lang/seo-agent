"""
Eval dashboard export (local-first, Lark-Base-ready).

Per the standard's operating principle — "do not start with a giant eval
platform; start local-first with a deterministic eval runner and a Lark Base
dashboard" — this produces:

  1. A console/markdown summary of every scored run (band distribution,
     hard-fail count, per-step averages, north-star coverage).
  2. A flat CSV (data_sources/ledger/eval_dashboard.csv) that is the exact shape
     to push into a Lark Base table via the lark-base skill.

Lark Base sync: import eval_dashboard.csv into the "Agent Registry & Health
Dashboard" base. Wiring the live Lark Base API is a follow-up; the CSV is the
contract so the local runner is the source of truth (Agent OS owns the data).

Usage:
    python3 -m data_sources.modules.eval.dashboard            # markdown summary
    python3 -m data_sources.modules.eval.dashboard --csv      # also write CSV
"""

import csv
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List

from .scorecard import read_eval_log, DIMENSIONS
from ..ledger import LEDGER_DIR

CSV_PATH = LEDGER_DIR / "eval_dashboard.csv"


def summarize() -> Dict[str, Any]:
    rows = read_eval_log()
    if not rows:
        return {"n": 0}

    bands = Counter(r.get("band", "?") for r in rows)
    hard_fails = sum(1 for r in rows if r.get("hard_fail"))
    by_step = defaultdict(list)
    for r in rows:
        by_step[r.get("step", "?")].append(r.get("total", 0))

    step_avg = {s: round(sum(v) / len(v), 1) for s, v in by_step.items()}
    with_cluster = sum(1 for r in rows if r.get("cluster"))
    with_stage = sum(1 for r in rows if r.get("awareness_stage"))

    dim_avg = {}
    for d in DIMENSIONS:
        vals = [r["scores"][d] for r in rows if d in r.get("scores", {})]
        if vals:
            dim_avg[d] = round(sum(vals) / len(vals), 1)

    return {
        "n": len(rows),
        "bands": dict(bands),
        "hard_fails": hard_fails,
        "step_avg": step_avg,
        "cluster_coverage": f"{with_cluster}/{len(rows)}",
        "stage_coverage": f"{with_stage}/{len(rows)}",
        "dimension_avg": dim_avg,
    }


def to_markdown() -> str:
    s = summarize()
    if not s.get("n"):
        return "# Eval Dashboard\n\nNo scored runs yet."
    lines = ["# Eval Dashboard", "",
             f"- **Runs scored:** {s['n']}",
             f"- **Hard fails (blocked):** {s['hard_fails']}",
             f"- **Band distribution:** {s['bands']}",
             f"- **Cluster coverage:** {s['cluster_coverage']}",
             f"- **Awareness-stage coverage:** {s['stage_coverage']}",
             "",
             "## Average total by step", ""]
    for step, avg in sorted(s["step_avg"].items()):
        lines.append(f"- {step}: {avg}/100")
    lines += ["", "## Average by dimension", ""]
    for dim, avg in s["dimension_avg"].items():
        lines.append(f"- {dim} (w{DIMENSIONS[dim]}%): {avg}/100")
    return "\n".join(lines)


def write_csv() -> str:
    rows = read_eval_log()
    cols = ["recorded_at", "step", "slug", "target_keyword", "cluster",
            "awareness_stage", "grader", "total", "band", "passed",
            "hard_fail"] + list(DIMENSIONS.keys())
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            flat = {k: r.get(k, "") for k in cols if k not in DIMENSIONS}
            for d in DIMENSIONS:
                flat[d] = r.get("scores", {}).get(d, "")
            w.writerow(flat)
    return str(CSV_PATH)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    print(to_markdown())
    if "--csv" in argv:
        path = write_csv()
        print(f"\nCSV written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
