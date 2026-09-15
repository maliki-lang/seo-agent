"""
Eval runner — the single entry point that gates a pipeline step.

Usage (from repo root):
    python3 -m data_sources.modules.eval.eval_runner <draft.md> [--step draft] \
        [--critic critic_scores.json] [--persist] [--json]

Behaviour:
  1. Parse metadata + run all deterministic graders -> Scorecard.
  2. If a critic JSON is supplied (separate LLM-judge agent), blend it DOWN.
  3. Finalise the self_improvement dimension.
  4. Persist the scorecard to eval_log.jsonl (close-the-loop) unless --no-persist.
  5. On failure, capture a regression fixture.
  6. Print the report and exit non-zero if the gate blocks — so callers
     (commands, CI, cron) actually halt. This is the anti-eval-theater contract.

Exit codes: 0 ship/ship_with_fixes(passed) · 2 regenerate · 3 block/hard-fail.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .scorecard import Scorecard
from .graders import grade_draft, parse_metadata
from .gates import gate, GateResult


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _load_critic(path: Optional[str]) -> Optional[Scorecard]:
    if not path:
        return None
    data = json.loads(_read(path))
    sc = Scorecard.from_dict(data)
    sc.grader = "critic"
    return sc


def evaluate_draft(
    content: str,
    step: str = "draft",
    metadata: Optional[Dict[str, Any]] = None,
    critic: Optional[Scorecard] = None,
    persist: bool = True,
) -> tuple[Scorecard, GateResult]:
    """Programmatic API: grade -> blend critic -> finalise -> persist -> gate."""
    sc = grade_draft(content, metadata=metadata, step=step)
    if critic is not None:
        sc = sc.blend_down(critic)

    result = gate(sc, step=step)

    # Self-improvement: full credit only if logged and (on failure) captured.
    captured = False
    if persist:
        if not result.passed:
            try:
                from .regression import capture_failure
                capture_failure(content, sc, result)
                captured = True
            except Exception:
                captured = False
        sc.add("self_improvement", 100 if (result.passed or captured) else 60)
        sc.persist()
        result = gate(sc, step=step)  # re-gate after final dimension set

    return sc, result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the eval gate on a draft.")
    ap.add_argument("draft", help="Path to the draft markdown file")
    ap.add_argument("--step", default="draft",
                    choices=["topic_selection", "research_brief", "draft",
                             "optimize", "publish"])
    ap.add_argument("--critic", help="Path to a critic-agent scorecard JSON")
    ap.add_argument("--no-persist", action="store_true",
                    help="Do not append to eval_log.jsonl")
    ap.add_argument("--json", action="store_true", help="Emit JSON only")
    args = ap.parse_args(argv)

    content = _read(args.draft)
    critic = _load_critic(args.critic)
    sc, result = evaluate_draft(
        content, step=args.step, critic=critic, persist=not args.no_persist
    )

    if args.json:
        print(json.dumps({"scorecard": sc.to_dict(),
                          "gate": result.__dict__}, indent=2))
    else:
        print(sc.format_report())
        print()
        print(result.summary())

    if result.hard_fail or result.should_block:
        return 3
    if result.should_regenerate:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
