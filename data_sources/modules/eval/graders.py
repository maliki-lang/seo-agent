"""
Deterministic graders.

Maps the repo's existing scorers (ContentScorer, SEOQualityRater,
ReadabilityScorer) plus the YMYL compliance gate onto the company-standard
9-dimension scorecard. This is the authoritative, code-enforced layer — the
critic agent can only lower these scores, never raise them.

Every dimension below is computed from a real grader output. Nothing here is
prompt prose.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from ..content_scorer import ContentScorer
    from ..seo_quality_rater import SEOQualityRater
except ImportError:  # standalone
    from content_scorer import ContentScorer
    from seo_quality_rater import SEOQualityRater

from .scorecard import Scorecard
from .compliance import check_compliance

# The verified target clusters — source of truth is the Lark Base "Clusters"
# table (RQvLbkxBmaJSE9s8CumluZAQgae / tblq04mv4Iw7mLSa). Keys are the canonical
# cluster names; values are signal keywords used to map a draft to a cluster.
# Order matters: map_cluster breaks ties toward the EARLIER entry, so the pillar
# (a collection/hub page, not a blog cluster) is intentionally last and given a
# narrow signal so generic "walking shoes" prose doesn't land there.
TARGET_CLUSTERS = {
    # 1 — fastest win: best-X / discovery
    "comfortable shoes (discovery)": [
        "comfortable shoes", "comfortable shoes singapore", "most comfortable",
        "shoe brands", "best shoes",
    ],
    # 2 — biggest non-branded prize; YMYL education + discovery (no transactional
    # medical-orthotic intent — that is deliberately dropped)
    "foot comfort education & arch support": [
        "arch support", "flat feet", "flat foot", "plantar fasciitis",
        "heel pain", "foot pain", "high arch", "overpronation",
    ],
    # 3 — highest winnable volume
    "everyday flats & loafers": [
        "ballet flats", "ballet flat", "flats", "loafers", "loafer",
        "ballerina", "mules",
    ],
    # 4 — work / office comfort
    "work / office comfort shoes": [
        "work shoes", "office shoes", "work shoes singapore",
        "comfortable work shoes",
    ],
    # Pillar / hub — every Sunnystep shoe is a walking shoe; competed as a
    # collection page, not a blog. Narrow signal so it only catches the head term.
    "comfortable walking shoes (pillar)": ["walking shoes singapore"],
}

AWARENESS_STAGES = {"unaware", "problem-aware", "solution-aware",
                    "product-aware", "ready-to-buy", "1", "2", "3", "4", "5"}

# Sunnystep word-count standard is ~800 words (see .claude/commands/write.md),
# not the SEO rater's generic 2000-word default. Align the grader so a compliant
# brand article is not falsely penalised.
BRAND_GUIDELINES = {"min_word_count": 600, "optimal_word_count": 800,
                    "max_word_count": 1300}


def parse_metadata(content: str) -> Dict[str, Any]:
    """
    Extract metadata from a draft.

    Supports both YAML-style frontmatter (--- ... ---) and the repo's
    `**Field**: value` markdown convention used by content_scorer.
    """
    meta: Dict[str, Any] = {}

    # YAML frontmatter
    fm = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if fm:
        for line in fm.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip().lower().replace(" ", "_")] = v.strip().strip('"')

    # **Field**: value lines
    for m in re.finditer(r"^\*\*([^*]+)\*\*:\s*(.+)$", content, re.MULTILINE):
        key = m.group(1).strip().lower().replace(" ", "_")
        meta.setdefault(key, m.group(2).strip())

    # Normalise common aliases
    def pick(*keys):
        for k in keys:
            if meta.get(k):
                return meta[k]
        return ""

    primary = pick("primary_keyword", "target_keyword", "keyword")
    secondary_raw = pick("secondary_keywords", "secondary_keyword")
    secondary = [s.strip() for s in re.split(r"[,;]", secondary_raw) if s.strip()]

    return {
        "meta_title": pick("meta_title", "seo_title", "title"),
        "meta_description": pick("meta_description", "seo_description", "description"),
        "primary_keyword": primary,
        "secondary_keywords": secondary,
        "cluster": pick("cluster", "category", "topic_cluster"),
        "awareness_stage": pick("awareness_stage", "stage", "funnel_stage").lower(),
        "raw": meta,
    }


def map_cluster(cluster: str, primary_keyword: str, content: str) -> str:
    """Return the canonical target cluster this draft belongs to, or ''."""
    blob = f"{cluster} {primary_keyword} {content[:600]}".lower()
    if cluster:
        for canonical in TARGET_CLUSTERS:
            if canonical in cluster.lower() or cluster.lower() in canonical:
                return canonical
    best = ""
    best_hits = 0
    for canonical, signals in TARGET_CLUSTERS.items():
        hits = sum(1 for s in signals if s in blob) + (1 if canonical in blob else 0)
        if hits > best_hits:
            best_hits, best = hits, canonical
    return best if best_hits > 0 else ""


def _count_links(content: str) -> Tuple[int, int]:
    internal = len(re.findall(r"\[[^\]]+\]\((?!https?://)", content))
    external = len(re.findall(r"\[[^\]]+\]\(https?://", content))
    return internal, external


def grade_draft(
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    step: str = "draft",
) -> Scorecard:
    """
    Run all deterministic graders and return a populated Scorecard.

    `metadata` may be supplied or parsed from the content. The Scorecard's
    `self_improvement` dimension is left at a provisional 100 here; the runner
    finalises it based on whether the run was logged / a regression captured.
    """
    meta = metadata or parse_metadata(content)
    primary = meta.get("primary_keyword", "")
    secondary = meta.get("secondary_keywords", [])
    cluster_raw = meta.get("cluster", "")
    stage = meta.get("awareness_stage", "")

    cluster = map_cluster(cluster_raw, primary, content)
    internal, external = _count_links(content)

    errors: List[str] = []

    # --- run the existing scorers ---
    try:
        cs = ContentScorer().score(content, meta)
    except Exception as e:  # never let a grader crash the gate
        cs = {"dimensions": {}, "composite_score": 0}
        errors.append(f"ContentScorer: {e}")

    try:
        seo = SEOQualityRater(guidelines={**SEOQualityRater()._default_guidelines(),
                                          **BRAND_GUIDELINES}).rate(
            content,
            meta_title=meta.get("meta_title") or None,
            meta_description=meta.get("meta_description") or None,
            primary_keyword=primary or None,
            secondary_keywords=secondary or None,
            internal_link_count=internal,
            external_link_count=external,
        )
    except Exception as e:
        seo = {"overall_score": 0, "details": {}, "critical_issues": []}
        errors.append(f"SEOQualityRater: {e}")

    comp = check_compliance(content, cluster=cluster_raw or cluster, target_keyword=primary)

    dims = cs.get("dimensions", {})
    humanity = dims.get("humanity", {}).get("score", 0)
    specificity = dims.get("specificity", {}).get("score", 0)
    seo_overall = seo.get("overall_score", 0)
    seo_details = seo.get("details", {})

    sc = Scorecard(
        step=step,
        slug=meta.get("raw", {}).get("slug", "") or _slug_from_title(meta),
        target_keyword=primary,
        cluster=cluster,
        awareness_stage=stage,
        grader="deterministic",
    )

    # 1. Source grounding (15) — external sources / citations present.
    if comp.is_ymyl:
        # YMYL: grounding is the compliance score (citations required).
        sc.add("source_grounding", comp.score,
               f"YMYL: {external} external links; "
               f"{len(comp.uncited_claims)} uncited claim(s)")
    else:
        grounding = min(100, 40 + external * 30)
        sc.add("source_grounding", grounding, f"{external} external source link(s)")

    # 2. Tool / trajectory correctness (15) — required artifacts present.
    checks = {
        "H1 present": seo_details.get("has_h1", False),
        "primary keyword set": bool(primary),
        "meta title": bool(meta.get("meta_title")),
        "meta description": bool(meta.get("meta_description")),
        "≥4 H2 sections": seo_details.get("h2_count", 0) >= 4,
    }
    traj = 100.0 * sum(1 for v in checks.values() if v) / len(checks)
    missing = [k for k, v in checks.items() if not v]
    sc.add("tool_trajectory", traj, *( [f"missing: {', '.join(missing)}"] if missing else [] ))

    # 3. Metric & action quality (15) — overall SEO quality.
    sc.add("metric_action_quality", seo_overall,
           *(seo.get("critical_issues", [])[:2]))

    # 4. Safety & compliance (15) — YMYL gate.
    sc.add("safety_compliance", comp.score, *comp.reasons)
    if comp.hard_fail:
        for r in comp.reasons:
            sc.trip_hard_fail(r)

    # 5. Verification evidence (10) — concrete data / specifics.
    sc.add("verification_evidence", specificity,
           *(["lacks specific numbers/data"] if specificity < 70 else []))

    # 6. Noise control (10) — anti-AI-genericity (humanity score).
    ai_found = dims.get("humanity", {}).get("details", {}).get("ai_phrases_found", [])
    sc.add("noise_control", humanity,
           *([f"AI/generic phrasing: {', '.join(ai_found[:3])}"] if ai_found else []))

    # 7. Business impact (10) — maps to a target cluster + a top-funnel stage.
    biz_checks = {
        "maps to a target cluster": bool(cluster),
        "awareness stage set": stage in AWARENESS_STAGES,
        "targets a keyword": bool(primary),
    }
    biz = 100.0 * sum(1 for v in biz_checks.values() if v) / len(biz_checks)
    biz_missing = [k for k, v in biz_checks.items() if not v]
    sc.add("business_impact", biz, *([f"missing: {', '.join(biz_missing)}"] if biz_missing else []))

    # 8. Reliability (5) — graders ran cleanly.
    sc.add("reliability", 100 if not errors else 50, *errors)

    # 9. Self-improvement (5) — provisional; runner finalises.
    sc.add("self_improvement", 100)

    sc.meta.update({
        "word_count": seo_details.get("word_count", 0),
        "content_composite": cs.get("composite_score", 0),
        "internal_links": internal,
        "external_links": external,
        "is_ymyl": comp.is_ymyl,
        "priority_fixes": cs.get("priority_fixes", [])[:5],
    })
    return sc


def _slug_from_title(meta: Dict[str, Any]) -> str:
    title = meta.get("meta_title", "") or ""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:80]
