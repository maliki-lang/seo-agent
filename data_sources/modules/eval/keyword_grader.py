"""
Keyword-pick eval (research step — "are we writing the right thing?").

Grades a candidate keyword the same way we graded the backlog by hand:
hard filters first, then a transparent score → Grade A–D + recommended status.
This catches the failure the draft eval cannot — a perfectly written article on
the wrong keyword (branded, no demand, wrong intent, or one we already own).

Wraps the existing scorers:
  - search_intent_analyzer.py  → intent (informational/commercial good;
    transactional downranked for this comfort-fashion brand; navigational reject)
  - opportunity_scorer.py      → quantitative demand/position signal (optional)

Hard filters (cap the grade / reject):
  - Branded term            → reject (grade D, Parked)   [it's brand traffic we have]
  - Navigational intent     → reject
  - No target cluster       → cap at C (must belong to a cluster)
  - Cannibalization         → reject (we already target it)

Scoring (0–100): 0.45 demand + 0.35 intent-fit + 0.20 winnability.
Grade: A>=80 · B>=65 · C>=50 · else D. Persists to eval_log (step=topic_selection).
"""

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from ..search_intent_analyzer import SearchIntentAnalyzer
except ImportError:  # standalone
    from search_intent_analyzer import SearchIntentAnalyzer

from .graders import map_cluster
from .scorecard import append_eval_row, ship_decision

# Brand tokens — a keyword containing these is branded (we already win it).
BRAND_TOKENS = [
    "sunnystep", "sunny step", "sunnstep", "sunstep", "sunny steps",
    "sunnyshoes", "sunny shoes", "balance walker",  # product line names
]

# Intent → fit score for a non-branded comfort-fashion brand targeting
# informational + discovery intent (see strategy: target by intent, not topic).
INTENT_FIT = {
    "informational": 100,
    "commercial_investigation": 90,   # "best / vs / review" discovery
    "transactional": 40,              # condition-led buyers often want medical-grade
    "navigational": 0,                # someone else's brand / login etc.
}

# Medical/orthotic signals. The "arch support shoes singapore" trap (high clicks,
# 0 paid conversions) doesn't show in the keyword string — it shows in clinical
# tokens and in a SERP owned by podiatry/insole/orthotic players. When present
# AND the intent is shopping (not informational), downrank: that audience wants
# a medical-grade shoe Sunnystep doesn't make. Educational/discovery framing of
# the SAME topic is still fine (so we don't blanket-penalise "arch support").
CLINICAL_TOKENS = [
    "orthopedic", "orthopaedic", "orthotic", "insole", "plantar fasciitis",
    "bunion", "podiatr", "fasciitis",
]
MEDICAL_SERP_HOSTS = [
    "footkaki.com", "archangelshoes.com.sg", "feetcare.sg", "happywalker.sg",
    "thefootpractice.com", "feetfirstpodiatry.com.sg", "cfoot.co",
    "drscholls.com.sg",
]


@dataclass
class KeywordGrade:
    keyword: str
    grade: str                 # A | B | C | D
    score: float               # 0..100
    status: str                # Backlog | Parked
    intent: str
    cluster: str
    hard_fail: bool = False
    reasons: List[str] = field(default_factory=list)
    subscores: Dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        head = f"[{self.grade}] {self.score:.0f}  {self.keyword}  ({self.status})"
        return head + ("\n  - " + "\n  - ".join(self.reasons) if self.reasons else "")


def _demand_score(search_volume: Optional[int], impressions: Optional[int]) -> tuple:
    """Log-scaled demand from KP volume, or GSC impressions as a fallback proxy."""
    if search_volume and search_volume > 0:
        # 10/mo→~40, 100→~67, 500→~88, 1000→~100
        return min(100.0, 20 + 27.5 * math.log10(search_volume)), "volume"
    if impressions and impressions > 0:
        return min(100.0, 15 + 20 * math.log10(impressions)), "impressions"
    return 50.0, "unknown"  # neutral, low confidence


def _winnability_score(current_position, serp_top) -> tuple:
    notes = []
    score = 60.0  # neutral default
    if current_position:
        if 11 <= current_position <= 20:
            score = 95.0; notes.append(f"striking distance (#{current_position})")
        elif current_position <= 10:
            score = 70.0; notes.append(f"already page-1 (#{current_position})")
        else:
            score = 55.0
    # Penalise SERPs walled by global giants/marketplaces.
    if serp_top:
        giants = {"nike.com", "adidas.com.sg", "decathlon.sg", "amazon.com",
                  "skechers.com.sg", "charleskeith.com"}
        hosts = [ (d.get("link","").split("/")[2].replace("www.","") if d.get("link","").startswith("http") else "") for d in serp_top[:5] ]
        if sum(1 for h in hosts if h in giants) >= 3:
            score = min(score, 40.0); notes.append("SERP dominated by big retailers")
    return score, notes


def grade_keyword(
    keyword: str,
    cluster: Optional[str] = None,
    search_volume: Optional[int] = None,
    current_position: Optional[float] = None,
    impressions: Optional[int] = None,
    serp_top: Optional[List[Dict[str, str]]] = None,
    existing_targets: Optional[List[str]] = None,
    persist: bool = True,
) -> KeywordGrade:
    kw = keyword.strip()
    low = kw.lower()
    reasons: List[str] = []

    # Intent
    intent_res = SearchIntentAnalyzer().analyze(kw, top_results=serp_top)
    intent = intent_res.get("primary_intent", "informational")

    # Resolve cluster
    resolved_cluster = cluster or map_cluster(cluster or "", kw, "")

    # --- hard filters ---
    hard_fail = False
    branded = any(t in low for t in BRAND_TOKENS)
    if branded:
        hard_fail = True
        reasons.append("branded term — traffic we already win; not a discovery target")
    if intent == "navigational":
        hard_fail = True
        reasons.append("navigational intent — not ours to target")
    if existing_targets and low in {t.strip().lower() for t in existing_targets}:
        hard_fail = True
        reasons.append("cannibalization — we already target this keyword")

    # --- scores ---
    demand, demand_src = _demand_score(search_volume, impressions)
    intent_fit = INTENT_FIT.get(intent, 50)
    win, win_notes = _winnability_score(current_position, serp_top)
    reasons.extend(win_notes)
    if demand_src == "unknown":
        reasons.append("no volume/impression data — demand assumed neutral")
    if intent == "transactional":
        reasons.append("transactional intent — downranked (condition-led buyers may want medical-grade)")

    # Medical-orthotic downrank (only when it's a shopping intent, not learning).
    low_clinical = any(t in low for t in CLINICAL_TOKENS)
    serp_hosts = []
    if serp_top:
        serp_hosts = [(d.get("link", "").split("/")[2].replace("www.", "")
                       if d.get("link", "").startswith("http") else "")
                      for d in serp_top[:6]]
    medical_serp = sum(1 for h in serp_hosts if h in MEDICAL_SERP_HOSTS) >= 2
    if (low_clinical or medical_serp) and intent in ("transactional", "commercial_investigation"):
        intent_fit = min(intent_fit, 45)
        reasons.append("medical-orthotic signal (clinical term / podiatry-owned SERP) "
                       "on a shopping query — target only via educational/discovery angle")

    total = round(0.45 * demand + 0.35 * intent_fit + 0.20 * win, 1)

    # Cluster filter: no cluster caps the grade.
    if not resolved_cluster:
        total = min(total, 55.0)
        reasons.append("no target cluster — must map to one of the 5 clusters")

    if hard_fail:
        grade, status = "D", "Parked"
        total = min(total, 30.0)
    else:
        grade = "A" if total >= 80 else "B" if total >= 65 else "C" if total >= 50 else "D"
        status = "Parked" if grade == "D" else "Backlog"

    result = KeywordGrade(
        keyword=kw, grade=grade, score=total, status=status, intent=intent,
        cluster=resolved_cluster or "", hard_fail=hard_fail, reasons=reasons,
        subscores={"demand": round(demand, 1), "intent_fit": intent_fit,
                   "winnability": round(win, 1)},
    )

    if persist:
        append_eval_row({
            "step": "topic_selection", "slug": kw, "target_keyword": kw,
            "cluster": result.cluster, "grader": "keyword_grader",
            "total": total, "band": ship_decision(total, hard_fail),
            "passed": (not hard_fail) and total >= 50,
            "hard_fail": hard_fail, "grade": grade, "intent": intent,
            "scores": result.subscores,
        })
    return result


def main(argv=None) -> int:
    import argparse, sys
    ap = argparse.ArgumentParser(description="Grade a keyword pick")
    ap.add_argument("keyword")
    ap.add_argument("--cluster")
    ap.add_argument("--volume", type=int)
    ap.add_argument("--position", type=float)
    ap.add_argument("--impressions", type=int)
    ap.add_argument("--no-persist", action="store_true")
    a = ap.parse_args(argv)
    g = grade_keyword(a.keyword, cluster=a.cluster, search_volume=a.volume,
                      current_position=a.position, impressions=a.impressions,
                      persist=not a.no_persist)
    print(g.summary())
    print(f"  intent={g.intent} cluster={g.cluster or '-'} subscores={g.subscores}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
