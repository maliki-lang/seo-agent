"""
Agent Performance Control Tower — eval package.

Wires the repo's deterministic scorers into the company evaluation standard
(Lark "05 Evaluation & Quality"): a 0–100 nine-dimension scorecard, threshold
gates on every pipeline step, a zero-tolerance YMYL compliance gate, a separate
critic agent (LLM-judge), score persistence, regression fixtures, and
scorecard calibration.

Design rule (anti "eval-theater"): every rubric here is computed by code that
actually scores an output and returns a gate decision — not prose injected into
a prompt. See docs/seo-agent-strategy-and-eval-plan.md.
"""

from .scorecard import Scorecard, DIMENSIONS, ship_decision
from .graders import grade_draft, parse_metadata
from .compliance import check_compliance, is_ymyl
from .gates import gate, GateResult
from .keyword_grader import grade_keyword, KeywordGrade
from .brief_gate import grade_brief, BriefGrade

__all__ = [
    "Scorecard",
    "DIMENSIONS",
    "ship_decision",
    "grade_draft",
    "parse_metadata",
    "check_compliance",
    "is_ymyl",
    "gate",
    "GateResult",
    "grade_keyword",
    "KeywordGrade",
    "grade_brief",
    "BriefGrade",
]
