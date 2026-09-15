"""
Regression layer (close the loop).

Every gate failure becomes a permanent fixture under regression/fixtures/. The
regression runner replays all fixtures and asserts the gate still makes the
expected decision — so a fixed failure can never silently come back. This is the
"failures become tests" requirement from the eval standard.

Fixture format (one JSON per file):
    {
      "id": "...", "step": "draft", "created_at": "...",
      "content": "<markdown>",
      "expect": {"hard_fail": true, "max_total": 70, "decision": "block"},
      "origin": "production-failure | curated",
      "note": "why this is here"
    }

Usage:
    python3 -m data_sources.modules.eval.regression run
    python3 -m data_sources.modules.eval.regression list
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

FIXTURES_DIR = Path(__file__).parent / "regression" / "fixtures"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "fixture"


def capture_failure(content: str, scorecard, gate_result) -> Path:
    """Persist a failing run as a regression fixture (idempotent-ish by id)."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    fid = f"{scorecard.step}-{_slug(scorecard.slug or scorecard.target_keyword)}"
    path = FIXTURES_DIR / f"{fid}.json"
    fixture = {
        "id": fid,
        "step": scorecard.step,
        "created_at": _now_iso(),
        "origin": "production-failure",
        "content": content,
        "expect": {
            "hard_fail": scorecard.hard_fail,
            "max_total": round(scorecard.total + 5, 1),
            "decision": gate_result.decision,
        },
        "note": "; ".join(gate_result.reasons[:3]),
    }
    path.write_text(json.dumps(fixture, indent=2), encoding="utf-8")
    return path


def load_fixtures() -> List[Dict[str, Any]]:
    if not FIXTURES_DIR.exists():
        return []
    out = []
    for p in sorted(FIXTURES_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def run_regressions() -> Dict[str, Any]:
    """Replay every fixture; assert the gate still decides as expected."""
    from .eval_runner import evaluate_draft

    fixtures = load_fixtures()
    results = []
    passed = 0
    for fx in fixtures:
        sc, gate_result = evaluate_draft(
            fx["content"], step=fx.get("step", "draft"), persist=False
        )
        expect = fx.get("expect", {})
        ok = True
        why = []
        if "hard_fail" in expect and bool(expect["hard_fail"]) != sc.hard_fail:
            ok = False
            why.append(f"hard_fail expected {expect['hard_fail']} got {sc.hard_fail}")
        if "decision" in expect and expect["decision"] != gate_result.decision:
            ok = False
            why.append(f"decision expected {expect['decision']} got {gate_result.decision}")
        if "max_total" in expect and sc.total > float(expect["max_total"]):
            ok = False
            why.append(f"total {sc.total} > max_total {expect['max_total']}")
        if "min_total" in expect and sc.total < float(expect["min_total"]):
            ok = False
            why.append(f"total {sc.total} < min_total {expect['min_total']}")
        passed += 1 if ok else 0
        results.append({"id": fx.get("id"), "ok": ok, "total": sc.total,
                        "decision": gate_result.decision, "why": why})

    return {"total": len(fixtures), "passed": passed,
            "failed": len(fixtures) - passed, "results": results}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "run"
    if cmd == "list":
        for fx in load_fixtures():
            print(f"{fx.get('id'):40} step={fx.get('step'):14} "
                  f"origin={fx.get('origin')}")
        return 0
    report = run_regressions()
    print(f"Regression: {report['passed']}/{report['total']} passed")
    for r in report["results"]:
        flag = "OK " if r["ok"] else "FAIL"
        print(f"  [{flag}] {r['id']}  total={r['total']} decision={r['decision']}")
        for w in r["why"]:
            print(f"         {w}")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
