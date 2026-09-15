"""
Agent contract layer (Layer 1 of the Control Tower).

Every agent that produces or changes a deliverable must declare a contract:
business KPI, source of truth, trigger, allowed tools, output audience,
escalation rule, safety boundary, and kill criteria. A pipeline step with no
contract is itself a finding.

This is the single source of truth; docs/agent-contracts.md is generated from it.
"""

from typing import Dict, List, Optional

CONTRACTS: Dict[str, Dict] = {
    "seo-writer": {
        "kpi": "Non-branded organic clicks + target-cluster rank movement for the published article",
        "source_of_truth": "GSC + GA4 (via ledger baseline); target keyword from the brief",
        "trigger": "/write or /article command",
        "allowed_tools": ["Read", "Write", "Bash", "research modules"],
        "output_audience": "SunnyStep readers (women 35–55, SG) + the editor/critic",
        "escalation": "Gate < 70 -> regenerate with critic feedback; < 50 or hard-fail -> human owner",
        "safety_boundary": "YMYL compliance gate must pass; no medical claims without citation + disclaimer",
        "kill_criteria": "If drafts repeatedly hard-fail compliance, disable auto-write for that cluster",
    },
    "critic": {
        "kpi": "Catch generic/unsupported/non-compliant drafts the deterministic graders miss",
        "source_of_truth": "Draft content + deterministic scorecard + brand/SEO context files",
        "trigger": "After deterministic draft eval, before publish",
        "allowed_tools": ["Read", "Bash"],
        "output_audience": "The eval runner (machine) + human owner on block",
        "escalation": "hard_fail -> block; otherwise lowers dimension scores",
        "safety_boundary": "Can only lower scores, never raise; independent of the writer",
        "kill_criteria": "If critic scores never correlate with outcomes (see calibration), recalibrate or replace",
    },
    "seo-optimizer": {
        "kpi": "On-page SEO quality (SEOQualityRater overall_score) without keyword stuffing",
        "source_of_truth": "Draft content + context/seo-guidelines.md",
        "trigger": "/optimize, or auto after /write",
        "allowed_tools": ["Read", "Write", "Bash"],
        "output_audience": "The draft pipeline",
        "escalation": "Stuffing or no heading structure -> block; < 70 -> regenerate",
        "safety_boundary": "Never inflate keyword density past guideline max",
        "kill_criteria": "n/a",
    },
    "publisher": {
        "kpi": "Content reaches the live Shopify site with correct SEO metadata, zero ranking regressions",
        "source_of_truth": "Shopify Admin API; ledger for baseline capture",
        "trigger": "/publish-draft",
        "allowed_tools": ["Bash", "shopify_publisher"],
        "output_audience": "Live site readers + GSC",
        "escalation": "Pre-publish gate must be ship-ready (>=85) and compliance-clean; else block",
        "safety_boundary": "Publishes as Hidden/draft first; never auto-publishes YMYL without passing gate",
        "kill_criteria": "If post-publish tracking shows ranking losses, halt and audit",
    },
    "opportunity-scorer": {
        "kpi": "Prioritize topics that map to a target cluster + stage 1–3 and move the north-star",
        "source_of_truth": "DataForSEO + GSC + learned_weights",
        "trigger": "/priorities, research commands",
        "allowed_tools": ["Bash", "research modules"],
        "output_audience": "Content planner / human owner",
        "escalation": "Topic with no cluster/stage -> owner review (band at_risk)",
        "safety_boundary": "n/a",
        "kill_criteria": "n/a",
    },
}

REQUIRED_FIELDS = [
    "kpi", "source_of_truth", "trigger", "allowed_tools", "output_audience",
    "escalation", "safety_boundary", "kill_criteria",
]


def get_contract(agent: str) -> Optional[Dict]:
    return CONTRACTS.get(agent)


def missing_contracts(agents: List[str]) -> List[str]:
    """Return agents (by name) that have no contract — each is a finding."""
    return [a for a in agents if a not in CONTRACTS]


def validate() -> Dict[str, List[str]]:
    """Return {agent: [missing fields]} for any incomplete contract."""
    problems = {}
    for name, c in CONTRACTS.items():
        missing = [f for f in REQUIRED_FIELDS if not c.get(f)]
        if missing:
            problems[name] = missing
    return problems


def to_markdown() -> str:
    lines = ["# Agent Contracts",
             "",
             "Generated from `data_sources/modules/eval/contracts.py` "
             "(Control Tower Layer 1). Every agent that produces or changes a "
             "deliverable declares its contract here.",
             ""]
    for name, c in CONTRACTS.items():
        lines.append(f"## {name}")
        lines.append("")
        for f in REQUIRED_FIELDS:
            label = f.replace("_", " ").title()
            val = c.get(f, "—")
            if isinstance(val, list):
                val = ", ".join(val)
            lines.append(f"- **{label}:** {val}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "markdown":
        print(to_markdown())
    else:
        problems = validate()
        if problems:
            print("Incomplete contracts:")
            for name, fields in problems.items():
                print(f"  {name}: missing {', '.join(fields)}")
        else:
            print(f"All {len(CONTRACTS)} contracts valid.")
