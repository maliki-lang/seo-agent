"""
Brief eval (research step — "is the plan good enough to write from?").

A thin brief guarantees a thin article, so the brief is gated on completeness
*before* the writer starts — far cheaper than fixing a finished piece. This is
the `research_brief` step in the pipeline.

Required elements (each present = credit):
  - target keyword            (what we're ranking for)
  - search intent             (informational / discovery / etc.)
  - SERP gap / competitor read (what format wins, what's missing)
  - subtopics to cover        (>= 3 — from People Also Ask / outline)
  - internal-link targets     (>= 2 — collection/product pages)
  - credible sources          (REQUIRED for YMYL/foot-comfort topics)
  - word-count / format target
  - cluster + awareness stage

YMYL rule: if the brief is foot-comfort/health, missing sources is a HARD FAIL
(can't write cited health content from an uncited brief). Gate threshold 70.
Persists to eval_log (step=research_brief).
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .compliance import is_ymyl
from .scorecard import append_eval_row, ship_decision

# element -> (weight, regex signals that indicate it's present)
CHECKS = {
    "target_keyword":   (15, [r"target keyword", r"primary keyword", r"focus keyphrase"]),
    "search_intent":    (15, [r"\bintent\b", r"informational", r"transactional",
                              r"commercial", r"discovery"]),
    "serp_gap":         (15, [r"serp", r"competitor", r"who ranks", r"ranking",
                              r"gap", r"what'?s missing"]),
    "subtopics":        (15, [r"subtopic", r"outline", r"sections? to cover",
                              r"people also ask", r"\bH2\b"]),
    "internal_links":   (10, [r"internal link", r"/collections/", r"/products/",
                              r"link to"]),
    "sources":          (15, [r"\bsource", r"\bcite", r"citation", r"https?://",
                              r"study", r"reference"]),
    "format_target":    (10, [r"word count", r"\b\d{3,4}\s*words", r"listicle",
                              r"\bguide\b", r"format", r"comparison"]),
    "cluster_stage":    (5,  [r"cluster", r"awareness stage", r"problem-aware",
                              r"solution-aware", r"unaware"]),
}


@dataclass
class BriefGrade:
    total: float
    passed: bool
    band: str
    hard_fail: bool = False
    present: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    def summary(self) -> str:
        head = (f"BRIEF {'PASS' if self.passed else 'FAIL'} — {self.total:.0f}/100 "
                f"({self.band})")
        if self.hard_fail:
            head += "  ⛔ HARD FAIL"
        if self.missing:
            head += "\n  missing: " + ", ".join(self.missing)
        for r in self.reasons:
            head += f"\n  - {r}"
        return head


def grade_brief(
    brief_text: str,
    cluster: str = "",
    slug: str = "",
    persist: bool = True,
) -> BriefGrade:
    text = brief_text.lower()
    present, missing = [], []
    score = 0.0
    total_weight = sum(w for w, _ in CHECKS.values())

    for name, (weight, signals) in CHECKS.items():
        hit = any(re.search(p, text) for p in signals)
        if hit:
            present.append(name); score += weight
        else:
            missing.append(name)

    total = round(100.0 * score / total_weight, 1)
    reasons: List[str] = []
    hard_fail = False

    # YMYL: sources are mandatory.
    ymyl = is_ymyl(brief_text, cluster)
    if ymyl and "sources" in missing:
        hard_fail = True
        reasons.append("YMYL brief with no credible sources — cannot write cited "
                       "health content. Add sources before drafting.")

    passed = (not hard_fail) and total >= 70
    band = ship_decision(total, hard_fail)
    result = BriefGrade(total=total, passed=passed, band=band, hard_fail=hard_fail,
                        present=present, missing=missing, reasons=reasons)

    if persist:
        append_eval_row({
            "step": "research_brief", "slug": slug or "(brief)",
            "cluster": cluster, "grader": "brief_gate",
            "total": total, "band": band, "passed": passed,
            "hard_fail": hard_fail,
            "scores": {k: (1 if k in present else 0) for k in CHECKS},
            "meta": {"missing": missing, "ymyl": ymyl},
        })
    return result


def main(argv=None) -> int:
    import argparse, sys
    from pathlib import Path
    ap = argparse.ArgumentParser(description="Grade a content brief")
    ap.add_argument("brief", help="Path to the brief markdown file")
    ap.add_argument("--cluster", default="")
    ap.add_argument("--no-persist", action="store_true")
    a = ap.parse_args(argv)
    text = Path(a.brief).read_text(encoding="utf-8")
    g = grade_brief(text, cluster=a.cluster, slug=Path(a.brief).stem,
                    persist=not a.no_persist)
    print(g.summary())
    return 3 if (g.hard_fail or g.band == "block") else (2 if not g.passed else 0)


if __name__ == "__main__":
    import sys
    sys.exit(main())
