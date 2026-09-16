"""Catalogue provenance constants and provisional candidate-v0.1 metadata."""

from __future__ import annotations

from typing import Dict

METHODOLOGY_VERSION = "catalogue_gsc_v1"
SELECTION_METHODOLOGY_V2 = "catalogue_selection_v2"
PROVISIONAL_CATALOGUE_VERSION = "candidate-v0.1"
CLASSIFIER_VERSION = "catalogue_classifier_v1"

PROVISIONAL_CATALOGUE_META: Dict[str, str] = {
    "catalogue_version": PROVISIONAL_CATALOGUE_VERSION,
    "status": "provisional",
    "origin": "existing repository strategy and manual mapping",
    "gsc_provenance": "unverified",
    "ga4_weighting": "not applied",
    "serper_validation": "not proven per item",
    "approval": "pending",
}

# Configuration-driven business relevance terms for Phase 6 funnel scoring.
# Not market-volume evidence; only relevance to Sunnystep comfort-footwear scope.
DEFAULT_RELEVANCE_TERMS = (
    "shoe",
    "shoes",
    "sandal",
    "sandals",
    "flat",
    "flats",
    "loafer",
    "loafers",
    "sneaker",
    "sneakers",
    "walking",
    "comfort",
    "comfortable",
    "office",
    "work",
    "standing",
    "arch",
    "plantar",
    "foot",
    "feet",
    "heel",
    "wide",
    "orthotic",
    "cushion",
    "singapore",
)

DEFAULT_MIN_IMPRESSIONS = 10
DEFAULT_MIN_CLICKS_PROTECT = 1
DEFAULT_SELECTED_LIMIT = 55
