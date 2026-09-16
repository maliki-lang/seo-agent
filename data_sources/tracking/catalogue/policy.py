"""Validated catalogue selection policy (catalogue_selection_v2)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..exceptions import ConfigurationError
from . import METHODOLOGY_VERSION, SELECTION_METHODOLOGY_V2

POLICY_VERSION = "selection_policy_v2"


DEFAULT_PRODUCT_TERMS: Tuple[str, ...] = (
    "shoe",
    "shoes",
    "sneaker",
    "sneakers",
    "sandal",
    "sandals",
    "loafer",
    "loafers",
    "mule",
    "mules",
    "slipper",
    "slippers",
    "flat",
    "flats",
)

DEFAULT_NEED_TERMS: Tuple[str, ...] = (
    "comfort",
    "comfortable",
    "walking",
    "standing",
    "arch support",
    "wide feet",
    "cushioning",
    "arch",
    "plantar",
    "orthotic",
    "cushion",
    "heel",
    "wide",
    "foot",
    "feet",
)

DEFAULT_COMMERCIAL_TERMS: Tuple[str, ...] = (
    "buy",
    "shop",
    "store",
    "best",
    "recommended",
    "near me",
)

DEFAULT_LOCATION_TERMS: Tuple[str, ...] = ("singapore", "sg")

DEFAULT_CLAIMS_TERMS: Tuple[str, ...] = (
    "plantar fasciitis",
    "orthotic",
    "pain relief",
    "medical",
    "injury",
    "bunion",
    "pain",
)

DEFAULT_AMBIGUOUS_BRAND_PHRASES: Tuple[str, ...] = (
    "sunny shoes",
    "sunny feet",
    "sunny feet shoes",
    "sunny shoe",
)

DEFAULT_APPROVED_BRAND_TYPOS: Tuple[str, ...] = ()

DEFAULT_LANE_QUOTAS: Dict[str, int] = {
    "need_state": 15,
    "product_category": 12,
    "use_case_audience": 8,
    "local_store": 8,
    "commercial_discovery": 5,
    "competitor_discovery": 4,
    "strategic_gap": 3,
}

DEFAULT_LANE_CAPS: Dict[str, int] = {
    "competitor_discovery": 4,
    "local_store": 8,
    "broad_head_term": 4,
}

DEFAULT_SCORE_WEIGHTS: Dict[str, float] = {
    "intent_fit": 0.25,
    "product_need_relevance": 0.20,
    "target_actionability": 0.15,
    "gsc_opportunity": 0.15,
    "serp_feasibility": 0.10,
    "incremental_coverage": 0.10,
    "evidence_confidence": 0.05,
}

DEFAULT_PENALTIES: Dict[str, float] = {
    "duplicate_non_primary": 1.00,
    "ambiguous_brand": 1.00,
    "competitor_without_strategy": 0.25,
    "unresolved_target_page": 0.15,
}


def _string_list(value: Any, *, field_name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigurationError(f"catalogue.{field_name} must be a list")
    return [str(item).strip().lower() for item in value if str(item).strip()]


def _float_map(value: Any, *, field_name: str, defaults: Mapping[str, float]) -> Dict[str, float]:
    if value is None:
        return dict(defaults)
    if not isinstance(value, dict):
        raise ConfigurationError(f"catalogue.{field_name} must be a mapping")
    out = dict(defaults)
    for key, raw in value.items():
        try:
            out[str(key)] = float(raw)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"catalogue.{field_name}.{key} must be numeric") from exc
    return out


def _int_map(value: Any, *, field_name: str, defaults: Mapping[str, int]) -> Dict[str, int]:
    if value is None:
        return dict(defaults)
    if not isinstance(value, dict):
        raise ConfigurationError(f"catalogue.{field_name} must be a mapping")
    out = dict(defaults)
    for key, raw in value.items():
        try:
            out[str(key)] = int(raw)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"catalogue.{field_name}.{key} must be an integer") from exc
    return out


@dataclass(frozen=True)
class CatalogueSelectionPolicy:
    policy_version: str = POLICY_VERSION
    methodology_version: str = METHODOLOGY_VERSION
    min_impressions: int = 10
    min_clicks_protect: int = 1
    selected_limit: int = 55
    alternate_limit: int = 15
    serper_preselection_limit: int = 100
    product_terms: Tuple[str, ...] = DEFAULT_PRODUCT_TERMS
    need_terms: Tuple[str, ...] = DEFAULT_NEED_TERMS
    commercial_terms: Tuple[str, ...] = DEFAULT_COMMERCIAL_TERMS
    location_terms: Tuple[str, ...] = DEFAULT_LOCATION_TERMS
    claims_terms: Tuple[str, ...] = DEFAULT_CLAIMS_TERMS
    prohibited_location_only: bool = True
    ambiguous_brand_phrases: Tuple[str, ...] = DEFAULT_AMBIGUOUS_BRAND_PHRASES
    approved_brand_typos: Tuple[str, ...] = DEFAULT_APPROVED_BRAND_TYPOS
    family_singular_plural: bool = True
    family_shop_store_equivalence: bool = True
    family_safe_word_order: bool = True
    family_fuzzy_brand_routing: bool = True
    family_min_confidence: float = 0.90
    lane_quotas: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LANE_QUOTAS))
    lane_caps: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LANE_CAPS))
    score_weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SCORE_WEIGHTS))
    penalties: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PENALTIES))

    def validate(self) -> None:
        weight_sum = round(sum(self.score_weights.values()), 6)
        if abs(weight_sum - 1.0) > 1e-6:
            raise ConfigurationError(
                f"catalogue.score_weights must total 1.0 (got {weight_sum})"
            )
        quota_sum = sum(self.lane_quotas.values())
        if quota_sum != self.selected_limit:
            raise ConfigurationError(
                f"catalogue.lane_quotas must total selected_limit={self.selected_limit} "
                f"(got {quota_sum})"
            )
        if self.serper_preselection_limit < self.selected_limit:
            raise ConfigurationError(
                "catalogue.serper_preselection_limit must be >= selected_limit"
            )
        if self.alternate_limit < 0:
            raise ConfigurationError("catalogue.alternate_limit must be >= 0")
        if self.family_min_confidence < 0 or self.family_min_confidence > 1:
            raise ConfigurationError("catalogue.family_rules.minimum_confidence_for_auto_family must be in [0, 1]")

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        # Tuples → lists for JSON stability.
        for key, value in list(payload.items()):
            if isinstance(value, tuple):
                payload[key] = list(value)
        return payload

    def fingerprint(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def flat_relevance_terms(self) -> Tuple[str, ...]:
        """Legacy flat list for v1 scorers; excludes location-only tokens when prohibited."""
        terms = list(self.product_terms) + list(self.need_terms) + list(self.commercial_terms)
        if not self.prohibited_location_only:
            terms.extend(self.location_terms)
        # Preserve uniqueness, stable order.
        seen = set()
        out: List[str] = []
        for term in terms:
            if term not in seen:
                seen.add(term)
                out.append(term)
        return tuple(out)


def policy_from_catalogue_raw(
    catalogue_raw: Optional[Mapping[str, Any]],
    *,
    defaults: Optional[CatalogueSelectionPolicy] = None,
) -> CatalogueSelectionPolicy:
    raw = dict(catalogue_raw or {})
    base = defaults or CatalogueSelectionPolicy()
    relevance = raw.get("relevance") or {}
    if relevance is None:
        relevance = {}
    if not isinstance(relevance, dict):
        raise ConfigurationError("catalogue.relevance must be a mapping")
    family_rules = raw.get("family_rules") or {}
    if family_rules is None:
        family_rules = {}
    if not isinstance(family_rules, dict):
        raise ConfigurationError("catalogue.family_rules must be a mapping")
    brand_cfg = raw.get("brand") or {}
    if brand_cfg is None:
        brand_cfg = {}
    if not isinstance(brand_cfg, dict):
        raise ConfigurationError("catalogue.brand must be a mapping")

    # Backward compatible flat relevance_terms: fold into product_terms if structured absent.
    flat_terms = _string_list(raw.get("relevance_terms"), field_name="relevance_terms")
    product_terms = _string_list(relevance.get("product_terms"), field_name="relevance.product_terms")
    need_terms = _string_list(relevance.get("need_terms"), field_name="relevance.need_terms")
    commercial_terms = _string_list(
        relevance.get("commercial_terms"), field_name="relevance.commercial_terms"
    )
    location_terms = _string_list(
        relevance.get("location_terms"), field_name="relevance.location_terms"
    )
    claims_terms = _string_list(relevance.get("claims_terms"), field_name="relevance.claims_terms")
    if not product_terms and not need_terms and flat_terms:
        # Legacy config: keep non-location flat terms as product-ish bag.
        location_set = set(base.location_terms)
        product_terms = [t for t in flat_terms if t not in location_set]
        location_terms = [t for t in flat_terms if t in location_set] or list(base.location_terms)

    prohibited = relevance.get("prohibited_location_only")
    if prohibited is None:
        prohibited = base.prohibited_location_only
    elif not isinstance(prohibited, bool):
        raise ConfigurationError("catalogue.relevance.prohibited_location_only must be a boolean")

    methodology = str(raw.get("methodology_version") or METHODOLOGY_VERSION).strip()
    if methodology not in {METHODOLOGY_VERSION, SELECTION_METHODOLOGY_V2}:
        raise ConfigurationError(
            f"Unsupported catalogue.methodology_version: {methodology}"
        )

    policy = CatalogueSelectionPolicy(
        policy_version=str(raw.get("selection_policy_version") or POLICY_VERSION),
        methodology_version=methodology,
        min_impressions=int(raw.get("min_impressions", base.min_impressions)),
        min_clicks_protect=int(raw.get("min_clicks_protect", base.min_clicks_protect)),
        selected_limit=int(raw.get("selected_limit", base.selected_limit)),
        alternate_limit=int(raw.get("alternate_limit", base.alternate_limit)),
        serper_preselection_limit=int(
            raw.get("serper_preselection_limit", base.serper_preselection_limit)
        ),
        product_terms=tuple(product_terms or base.product_terms),
        need_terms=tuple(need_terms or base.need_terms),
        commercial_terms=tuple(commercial_terms or base.commercial_terms),
        location_terms=tuple(location_terms or base.location_terms),
        claims_terms=tuple(claims_terms or base.claims_terms),
        prohibited_location_only=prohibited,
        ambiguous_brand_phrases=tuple(
            _string_list(
                brand_cfg.get("ambiguous_phrases"), field_name="brand.ambiguous_phrases"
            )
            or base.ambiguous_brand_phrases
        ),
        approved_brand_typos=tuple(
            _string_list(
                brand_cfg.get("approved_typos"), field_name="brand.approved_typos"
            )
            or base.approved_brand_typos
        ),
        family_singular_plural=bool(
            family_rules.get("singular_plural", base.family_singular_plural)
        ),
        family_shop_store_equivalence=bool(
            family_rules.get("shop_store_equivalence", base.family_shop_store_equivalence)
        ),
        family_safe_word_order=bool(
            family_rules.get("safe_word_order_equivalence", base.family_safe_word_order)
        ),
        family_fuzzy_brand_routing=bool(
            family_rules.get("fuzzy_brand_routing", base.family_fuzzy_brand_routing)
        ),
        family_min_confidence=float(
            family_rules.get("minimum_confidence_for_auto_family", base.family_min_confidence)
        ),
        lane_quotas=_int_map(raw.get("lane_quotas"), field_name="lane_quotas", defaults=base.lane_quotas),
        lane_caps=_int_map(raw.get("lane_caps"), field_name="lane_caps", defaults=base.lane_caps),
        score_weights=_float_map(
            raw.get("score_weights"), field_name="score_weights", defaults=base.score_weights
        ),
        penalties=_float_map(raw.get("penalties"), field_name="penalties", defaults=base.penalties),
    )
    policy.validate()
    return policy


def policy_from_config(config: Any) -> CatalogueSelectionPolicy:
    stored = getattr(config, "catalogue_selection_policy", None)
    if isinstance(stored, CatalogueSelectionPolicy):
        stored.validate()
        return stored
    return CatalogueSelectionPolicy()
