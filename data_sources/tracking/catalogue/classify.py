"""Catalogue routing, brand, competitor, intent and relevance classification (Phase 10)."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import TrackingConfig
from ..enums import (
    BrandMatchType,
    BrandStatus,
    BusinessRelevanceStatus,
    CandidateDecision,
    CompetitorStatus,
    EligibilityStatus,
    RoutingBucket,
    SearchIntent,
    StrategicLane,
)
from ..exceptions import DataQualityError
from ..storage import TrackingStore
from ..transforms.ai_parser import competitor_catalogue_from_config, named_competitors
from ..transforms.brand_label import BrandClassifier, normalize_for_brand
from ..transforms.normalize import utc_now_iso
from . import CLASSIFIER_VERSION, SELECTION_METHODOLOGY_V2
from .policy import CatalogueSelectionPolicy, policy_from_config

_LOCAL_PLACE_TOKENS = frozenset(
    {
        "nex",
        "westgate",
        "bugis",
        "jurong",
        "orchard",
        "ion",
        "vivocity",
        "tampines",
        "jem",
        "plaza",
        "singpost",
        "outlet",
        "outlets",
        "mall",
    }
)
_AUDIENCE_TOKENS = frozenset(
    {"kids", "men", "mens", "women", "womens", "office", "work", "pregnancy", "nursing"}
)
_COMMERCIAL_INVESTIGATION = frozenset(
    {"best", "recommended", "review", "reviews", "vs", "versus", "alternative", "alternatives"}
)
_CAMPAIGN_TOKENS = frozenset({"walkathon", "sale", "birthday", "promo", "promotion", "event"})
_PROBLEM_TOKENS = frozenset(
    {"pain", "plantar", "fasciitis", "bunion", "arch", "wide", "swollen", "injury", "orthotic"}
)


@dataclass(frozen=True)
class ClassificationResult:
    brand_status: str
    brand_match_type: str
    brand_confidence: float
    competitor_status: str
    competitor_name: Optional[str]
    search_intent: str
    strategic_lane: str
    routing_bucket: str
    business_relevance_status: str
    business_relevance_reason: str
    business_relevance_score: float
    claims_review_required: bool
    eligibility_status: str
    eligibility_reasons: Tuple[str, ...]
    methodology_version: str = SELECTION_METHODOLOGY_V2
    classifier_version: str = CLASSIFIER_VERSION

    def as_update_fields(self) -> Dict[str, Any]:
        return {
            "methodology_version": self.methodology_version,
            "brand_status": self.brand_status,
            "brand_match_type": self.brand_match_type,
            "brand_confidence": self.brand_confidence,
            "competitor_status": self.competitor_status,
            "competitor_name": self.competitor_name,
            "search_intent": self.search_intent,
            "strategic_lane": self.strategic_lane,
            "routing_bucket": self.routing_bucket,
            "business_relevance_status": self.business_relevance_status,
            "business_relevance_reason": self.business_relevance_reason,
            "business_relevance_score": self.business_relevance_score,
            "claims_review_required": int(self.claims_review_required),
            "eligibility_status": self.eligibility_status,
            "eligibility_reasons_json": list(self.eligibility_reasons),
        }


def _tokens(text: str) -> List[str]:
    return [t for t in normalize_for_brand(text).split() if t]


def _contains_phrase(normalized: str, phrase: str) -> bool:
    hay = f" {normalize_for_brand(normalized)} "
    needle = f" {normalize_for_brand(phrase)} "
    return bool(phrase) and needle in hay


def _has_any_phrase(normalized: str, phrases: Sequence[str]) -> Optional[str]:
    for phrase in phrases:
        if _contains_phrase(normalized, phrase):
            return phrase
    return None


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def classify_brand(
    query: str,
    classifier: BrandClassifier,
    policy: CatalogueSelectionPolicy,
) -> Tuple[str, str, float]:
    """Return (brand_status, match_type, confidence)."""
    normalized = normalize_for_brand(query)
    if classifier.is_brand(normalized):
        # Distinguish exact configured term vs approved alias by whether the matched
        # term appears in the brand terms list (all current terms are configured).
        return BrandStatus.BRANDED.value, BrandMatchType.EXACT_CONFIGURED_TERM.value, 1.0

    typo = _has_any_phrase(normalized, policy.approved_brand_typos)
    if typo:
        return BrandStatus.BRANDED.value, BrandMatchType.APPROVED_TYPO.value, 0.95

    ambiguous = _has_any_phrase(normalized, policy.ambiguous_brand_phrases)
    if ambiguous:
        return BrandStatus.AMBIGUOUS_BRAND.value, BrandMatchType.FUZZY_SUSPECT.value, 0.7

    tokens = _tokens(normalized)
    if "sunny" in tokens and not classifier.is_brand(normalized):
        # Conservative: bare "sunny" + other tokens is brand-like leakage risk.
        return BrandStatus.AMBIGUOUS_BRAND.value, BrandMatchType.FUZZY_SUSPECT.value, 0.65

    # Near-miss to configured brand tokens (e.g. sunnysteo ≈ sunnystep).
    for term in classifier.terms:
        compact_term = term.replace(" ", "")
        compact_query = normalized.replace(" ", "")
        if not compact_term or len(compact_term) < 6:
            continue
        if abs(len(compact_query) - len(compact_term)) > 2:
            continue
        if _levenshtein(compact_query, compact_term) <= 2:
            return BrandStatus.AMBIGUOUS_BRAND.value, BrandMatchType.FUZZY_SUSPECT.value, 0.6

    return BrandStatus.NON_BRANDED.value, BrandMatchType.NO_MATCH.value, 1.0


def classify_competitor(
    query: str,
    competitor_catalogue: Sequence[Dict[str, Any]],
) -> Tuple[str, Optional[str]]:
    names = named_competitors(query, competitor_catalogue)
    if not names:
        return CompetitorStatus.NONE.value, None
    if len(names) == 1:
        return CompetitorStatus.EXPLICIT.value, names[0]
    return CompetitorStatus.AMBIGUOUS.value, names[0]


def claims_review_required(query: str, policy: CatalogueSelectionPolicy) -> bool:
    return _has_any_phrase(query, policy.claims_terms) is not None


def classify_business_relevance(
    query: str,
    *,
    policy: CatalogueSelectionPolicy,
    competitor_status: str,
    reviewer_override: bool = False,
) -> Tuple[str, str, float]:
    """Return (status, reason, score in 0..1). Location-only never passes when prohibited."""
    if reviewer_override:
        return BusinessRelevanceStatus.RELEVANT.value, "explicit_reviewer_override", 1.0

    product_hit = _has_any_phrase(query, policy.product_terms)
    need_hit = _has_any_phrase(query, policy.need_terms)
    commercial_hit = _has_any_phrase(query, policy.commercial_terms)
    location_hit = _has_any_phrase(query, policy.location_terms)
    competitor_ok = competitor_status in {
        CompetitorStatus.EXPLICIT.value,
        CompetitorStatus.AMBIGUOUS.value,
    }

    reasons: List[str] = []
    score = 0.0
    if product_hit:
        reasons.append(f"product_term:{product_hit}")
        score += 0.45
    if need_hit:
        reasons.append(f"need_term:{need_hit}")
        score += 0.35
    if commercial_hit and (product_hit or need_hit or competitor_ok):
        reasons.append(f"commercial_term:{commercial_hit}")
        score += 0.15
    elif commercial_hit and not product_hit and not need_hit and not competitor_ok:
        # Commercial tokens alone (shop/store) without footwear evidence are weak.
        reasons.append(f"commercial_without_product:{commercial_hit}")
    if competitor_ok:
        reasons.append("approved_competitor_evidence")
        score += 0.35
    if location_hit:
        reasons.append(f"location_term:{location_hit}")

    if policy.prohibited_location_only and location_hit and not (
        product_hit or need_hit or competitor_ok
    ):
        return (
            BusinessRelevanceStatus.LOCATION_ONLY.value,
            "location_only_insufficient",
            0.0,
        )

    if product_hit or need_hit or competitor_ok or (
        commercial_hit and (product_hit or need_hit)
    ):
        return (
            BusinessRelevanceStatus.RELEVANT.value,
            ",".join(reasons) or "relevant",
            min(1.0, round(score, 4)),
        )

    if commercial_hit:
        return (
            BusinessRelevanceStatus.IRRELEVANT.value,
            ",".join(reasons) or "commercial_without_footwear_evidence",
            0.0,
        )
    return BusinessRelevanceStatus.IRRELEVANT.value, "no_relevance_evidence", 0.0


def classify_search_intent(
    query: str,
    *,
    brand_status: str,
    competitor_status: str,
    policy: CatalogueSelectionPolicy,
) -> str:
    tokens = set(_tokens(query))
    if brand_status == BrandStatus.BRANDED.value:
        return SearchIntent.NAVIGATIONAL_BRAND.value
    if brand_status == BrandStatus.AMBIGUOUS_BRAND.value:
        return SearchIntent.AMBIGUOUS.value
    if competitor_status != CompetitorStatus.NONE.value and not (
        tokens & {"near", "me"} or tokens & _LOCAL_PLACE_TOKENS
    ):
        # Competitor name with shopping intent still competitor-navigational when bare-ish.
        if not (_has_any_phrase(query, policy.product_terms) or _has_any_phrase(query, policy.need_terms)):
            return SearchIntent.NAVIGATIONAL_COMPETITOR.value
        return SearchIntent.COMMERCIAL_INVESTIGATION.value
    if tokens & _CAMPAIGN_TOKENS:
        return SearchIntent.CAMPAIGN_EVENT.value
    if "near" in tokens and "me" in tokens:
        return SearchIntent.LOCAL_STORE.value
    if tokens & _LOCAL_PLACE_TOKENS or (
        _has_any_phrase(query, ("shop", "store"))
        and _has_any_phrase(query, policy.location_terms)
    ):
        return SearchIntent.LOCAL_STORE.value
    if tokens & _PROBLEM_TOKENS or _has_any_phrase(query, policy.claims_terms):
        return SearchIntent.PROBLEM_SOLUTION.value
    if tokens & _COMMERCIAL_INVESTIGATION or _has_any_phrase(query, ("where to buy", "buy")):
        return SearchIntent.COMMERCIAL_INVESTIGATION.value
    if _has_any_phrase(query, policy.product_terms):
        return SearchIntent.TRANSACTIONAL_CATEGORY.value
    if _has_any_phrase(query, policy.need_terms):
        return SearchIntent.PROBLEM_SOLUTION.value
    return SearchIntent.AMBIGUOUS.value


def classify_strategic_lane(
    *,
    search_intent: str,
    brand_status: str,
    competitor_status: str,
    query: str,
) -> str:
    tokens = set(_tokens(query))
    if brand_status in {BrandStatus.BRANDED.value, BrandStatus.AMBIGUOUS_BRAND.value}:
        return StrategicLane.INFORMATIONAL_EDITORIAL.value
    if competitor_status != CompetitorStatus.NONE.value:
        return StrategicLane.COMPETITOR_DISCOVERY.value
    if search_intent == SearchIntent.LOCAL_STORE.value:
        return StrategicLane.LOCAL_STORE.value
    if search_intent == SearchIntent.CAMPAIGN_EVENT.value:
        return StrategicLane.STRATEGIC_GAP.value
    if search_intent == SearchIntent.PROBLEM_SOLUTION.value:
        return StrategicLane.NEED_STATE.value
    if search_intent == SearchIntent.COMMERCIAL_INVESTIGATION.value:
        return StrategicLane.COMMERCIAL_DISCOVERY.value
    if tokens & _AUDIENCE_TOKENS:
        return StrategicLane.USE_CASE_AUDIENCE.value
    if search_intent == SearchIntent.TRANSACTIONAL_CATEGORY.value:
        return StrategicLane.PRODUCT_CATEGORY.value
    if search_intent == SearchIntent.INFORMATIONAL.value:
        return StrategicLane.INFORMATIONAL_EDITORIAL.value
    return StrategicLane.STRATEGIC_GAP.value


def classify_routing_bucket(
    *,
    brand_status: str,
    competitor_status: str,
    search_intent: str,
    relevance_status: str,
) -> str:
    if brand_status == BrandStatus.BRANDED.value:
        return RoutingBucket.BRANDED_BENCHMARK.value
    if brand_status == BrandStatus.AMBIGUOUS_BRAND.value:
        return RoutingBucket.AMBIGUOUS_BRAND.value
    if relevance_status in {
        BusinessRelevanceStatus.IRRELEVANT.value,
        BusinessRelevanceStatus.LOCATION_ONLY.value,
    }:
        return RoutingBucket.IRRELEVANT.value
    if competitor_status != CompetitorStatus.NONE.value:
        return RoutingBucket.COMPETITOR_BENCHMARK.value
    if search_intent == SearchIntent.LOCAL_STORE.value:
        return RoutingBucket.LOCAL_STORE.value
    return RoutingBucket.NONBRAND_DISCOVERY.value


def classify_eligibility(
    *,
    brand_status: str,
    relevance_status: str,
    claims_required: bool,
    routing_bucket: str,
) -> Tuple[str, Tuple[str, ...]]:
    reasons: List[str] = []
    if brand_status == BrandStatus.BRANDED.value:
        return EligibilityStatus.INELIGIBLE_BRAND.value, ("branded_excluded_from_nonbrand_catalogue",)
    if brand_status == BrandStatus.AMBIGUOUS_BRAND.value:
        return EligibilityStatus.INELIGIBLE_BRAND.value, ("ambiguous_brand_requires_review",)
    if relevance_status in {
        BusinessRelevanceStatus.IRRELEVANT.value,
        BusinessRelevanceStatus.LOCATION_ONLY.value,
    }:
        reasons.append(f"relevance_{relevance_status}")
        return EligibilityStatus.INELIGIBLE_IRRELEVANT.value, tuple(reasons)
    if claims_required:
        return EligibilityStatus.ELIGIBLE_WITH_REVIEW.value, ("claims_review_required",)
    if routing_bucket == RoutingBucket.MANUAL_REVIEW.value:
        return EligibilityStatus.PENDING_CLASSIFICATION.value, ("manual_review_routing",)
    return EligibilityStatus.ELIGIBLE.value, ("passes_phase10_classification",)


def classify_query(
    query: str,
    *,
    policy: CatalogueSelectionPolicy,
    brand_classifier: BrandClassifier,
    competitor_catalogue: Sequence[Dict[str, Any]],
    reviewer_override: bool = False,
) -> ClassificationResult:
    brand_status, brand_match_type, brand_confidence = classify_brand(
        query, brand_classifier, policy
    )
    competitor_status, competitor_name = classify_competitor(query, competitor_catalogue)
    relevance_status, relevance_reason, relevance_score = classify_business_relevance(
        query,
        policy=policy,
        competitor_status=competitor_status,
        reviewer_override=reviewer_override,
    )
    claims = claims_review_required(query, policy)
    intent = classify_search_intent(
        query,
        brand_status=brand_status,
        competitor_status=competitor_status,
        policy=policy,
    )
    lane = classify_strategic_lane(
        search_intent=intent,
        brand_status=brand_status,
        competitor_status=competitor_status,
        query=query,
    )
    routing = classify_routing_bucket(
        brand_status=brand_status,
        competitor_status=competitor_status,
        search_intent=intent,
        relevance_status=relevance_status,
    )
    eligibility, eligibility_reasons = classify_eligibility(
        brand_status=brand_status,
        relevance_status=relevance_status,
        claims_required=claims,
        routing_bucket=routing,
    )
    return ClassificationResult(
        brand_status=brand_status,
        brand_match_type=brand_match_type,
        brand_confidence=brand_confidence,
        competitor_status=competitor_status,
        competitor_name=competitor_name,
        search_intent=intent,
        strategic_lane=lane,
        routing_bucket=routing,
        business_relevance_status=relevance_status,
        business_relevance_reason=relevance_reason,
        business_relevance_score=relevance_score,
        claims_review_required=claims,
        eligibility_status=eligibility,
        eligibility_reasons=eligibility_reasons,
    )


def classify_build(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    demote_ineligible_selected: bool = True,
    policy: Optional[CatalogueSelectionPolicy] = None,
) -> Dict[str, Any]:
    """Classify all candidates on a build. Does not rewrite historical builds in place
    beyond additive v2 fields; may demote selected→pending for ineligible rows when asked.
    """
    store.migrate()
    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (build_id,))
    if not builds:
        raise DataQualityError(f"Unknown build_id {build_id}")

    selection_policy = policy or policy_from_config(config)
    brand_classifier = BrandClassifier.from_config(config)
    competitors = competitor_catalogue_from_config("config/competitors.yaml")
    now = utc_now_iso()

    candidates = [
        dict(row)
        for row in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ? ORDER BY normalized_keyword",
            (build_id,),
        )
    ]
    routing_counts: Counter = Counter()
    eligibility_counts: Counter = Counter()
    brand_counts: Counter = Counter()
    intent_counts: Counter = Counter()
    demoted = 0
    examples: Dict[str, List[str]] = {
        "ambiguous_brand": [],
        "location_only": [],
        "competitor": [],
        "claims_review": [],
    }

    for cand in candidates:
        result = classify_query(
            cand["normalized_keyword"] or cand["canonical_keyword"],
            policy=selection_policy,
            brand_classifier=brand_classifier,
            competitor_catalogue=competitors,
        )
        fields = result.as_update_fields()
        fields["updated_at"] = now

        if (
            demote_ineligible_selected
            and cand.get("decision") == CandidateDecision.SELECTED.value
            and result.eligibility_status
            in {
                EligibilityStatus.INELIGIBLE_BRAND.value,
                EligibilityStatus.INELIGIBLE_IRRELEVANT.value,
                EligibilityStatus.INELIGIBLE_UNSUPPORTED_CLAIM.value,
            }
        ):
            fields["decision"] = CandidateDecision.PENDING.value
            prefix = "demoted_phase10_ineligible"
            existing = cand.get("decision_reason") or ""
            fields["decision_reason"] = f"{prefix};{existing}" if existing else prefix
            demoted += 1

        store.update_keyword_candidate(cand["candidate_id"], fields)

        routing_counts[result.routing_bucket] += 1
        eligibility_counts[result.eligibility_status] += 1
        brand_counts[result.brand_status] += 1
        intent_counts[result.search_intent] += 1
        text = cand["normalized_keyword"]
        if (
            result.brand_status == BrandStatus.AMBIGUOUS_BRAND.value
            and len(examples["ambiguous_brand"]) < 10
        ):
            examples["ambiguous_brand"].append(text)
        if (
            result.business_relevance_status == BusinessRelevanceStatus.LOCATION_ONLY.value
            and len(examples["location_only"]) < 10
        ):
            examples["location_only"].append(text)
        if result.competitor_status != CompetitorStatus.NONE.value and len(examples["competitor"]) < 10:
            examples["competitor"].append(text)
        if result.claims_review_required and len(examples["claims_review"]) < 10:
            examples["claims_review"].append(text)

    report = {
        "build_id": build_id,
        "classifier_version": CLASSIFIER_VERSION,
        "methodology_version": SELECTION_METHODOLOGY_V2,
        "policy_version": selection_policy.policy_version,
        "policy_fingerprint": selection_policy.fingerprint(),
        "candidate_count": len(candidates),
        "routing_counts": dict(routing_counts),
        "eligibility_counts": dict(eligibility_counts),
        "brand_counts": dict(brand_counts),
        "intent_counts": dict(intent_counts),
        "demoted_selected_to_pending": demoted,
        "examples": examples,
        "classified_at": now,
    }
    store.update_catalogue_build(
        build_id,
        {
            "selection_policy_version": selection_policy.policy_version,
            "selection_policy_json": selection_policy.to_dict(),
            "routing_report_json": report,
            "notes": (
                f"Phase 10 classification applied ({CLASSIFIER_VERSION}); "
                f"demoted_selected={demoted}"
            ),
        },
    )
    return report
