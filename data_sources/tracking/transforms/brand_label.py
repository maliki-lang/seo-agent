from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from ..config import REPO_ROOT, TrackingConfig

BRAND_RULE_VERSION = "brand_rules_v1"

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalize_for_brand(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "")
    value = value.lower()
    value = value.replace("’", "'").replace("“", '"').replace("”", '"')
    value = _PUNCT.sub(" ", value)
    value = _SPACE.sub(" ", value).strip()
    return value


def load_brand_terms(path: Path) -> List[str]:
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        terms.append(normalize_for_brand(item))
    # Longest first so "sunny step" wins over a hypothetical "sunny".
    terms.sort(key=len, reverse=True)
    return terms


@dataclass(frozen=True)
class BrandClassifier:
    terms: Sequence[str]
    version: str = BRAND_RULE_VERSION

    @classmethod
    def from_config(cls, config: TrackingConfig) -> "BrandClassifier":
        path = Path(config.brand_terms_path)
        if not path.is_absolute():
            path = REPO_ROOT / path
        return cls(terms=load_brand_terms(path))

    def is_brand(self, query: str) -> bool:
        normalized = f" {normalize_for_brand(query)} "
        for term in self.terms:
            if not term:
                continue
            needle = f" {term} "
            if needle in normalized:
                return True
        return False


def classify_queries(queries: Iterable[str], classifier: BrandClassifier) -> List[bool]:
    return [classifier.is_brand(query) for query in queries]
