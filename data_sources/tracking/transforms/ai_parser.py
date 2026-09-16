from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import yaml

from ..config import REPO_ROOT
from .normalize import canonicalize_url, normalize_host

PARSER_VERSION = "ai_parser_v1"
SUNNYSTEP_HOSTS = {"sunnystep.com"}
_BRAND_TERMS = ("sunnystep", "sunny step")
_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)", re.IGNORECASE)
_BARE_DOMAIN_RE = re.compile(
    r"\b(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?:/[^\s]*)?",
    re.IGNORECASE,
)


def _normalize_text(value: str) -> str:
    text = (value or "").lower().replace("’", "'")
    text = re.sub(r"[^\w\s./:-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def mentioned_sunnystep(text: str) -> bool:
    normalized = f" {_normalize_text(text)} "
    for term in _BRAND_TERMS:
        if f" {term} " in normalized:
            return True
    return False


def extract_urls(text: str, extra: Iterable[str] = ()) -> List[str]:
    found: List[str] = []
    seen = set()
    candidates = list(extra)
    for match in _MD_LINK_RE.findall(text or ""):
        candidates.append(match)
    for match in _URL_RE.findall(text or ""):
        candidates.append(match.rstrip(".,;"))
    for match in _BARE_DOMAIN_RE.finditer(text or ""):
        host = match.group(0)
        if "." in host and not host.lower().startswith("http"):
            candidates.append("https://" + host.rstrip(".,;"))
    for raw in candidates:
        canon = canonicalize_url(raw)
        if not canon or canon in seen:
            continue
        seen.add(canon)
        found.append(canon)
    return found


def cites_sunnystep(urls: Sequence[str]) -> bool:
    for url in urls:
        host = normalize_host(url)
        if host == "sunnystep.com" or host.endswith(".sunnystep.com"):
            return True
    return False


def load_competitor_aliases(path: Path) -> List[Dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("competitors") or [])


def named_competitors(text: str, catalogue: Sequence[Dict[str, Any]]) -> List[str]:
    normalized = f" {_normalize_text(text)} "
    spaced = normalized.replace(".", " ").replace("/", " ")
    found: List[str] = []
    for row in catalogue:
        name = str(row.get("name") or "").strip()
        aliases = [name] + list(row.get("aliases") or [])
        matched = False
        for alias in aliases:
            alias_n = _normalize_text(alias)
            if not alias_n:
                continue
            if f" {alias_n} " in normalized or f" {alias_n} " in spaced:
                matched = True
                break
        if matched and name and name not in found:
            found.append(name)
    return found


@dataclass(frozen=True)
class ParsedAnswer:
    mentioned_sunnystep: bool
    cited_urls: List[str]
    cited_sunnystep: bool
    named_competitors: List[str]
    parser_version: str = PARSER_VERSION


def parse_answer(
    text: str,
    *,
    extra_urls: Iterable[str] = (),
    competitor_catalogue: Sequence[Dict[str, Any]] = (),
) -> ParsedAnswer:
    urls = extract_urls(text, extra_urls)
    return ParsedAnswer(
        mentioned_sunnystep=mentioned_sunnystep(text),
        cited_urls=urls,
        cited_sunnystep=cites_sunnystep(urls),
        named_competitors=named_competitors(text, competitor_catalogue),
    )


def stable_rates(rows: Sequence[Any]) -> Dict[str, Any]:
    """rows have question_id, engine, mentioned_sunnystep, cited_urls, repetition_number."""
    grouped: Dict[tuple, List[Any]] = {}
    for row in rows:
        key = (row.question_id, row.engine.value if hasattr(row.engine, "value") else row.engine)
        grouped.setdefault(key, []).append(row)

    def _complete(items: List[Any]) -> bool:
        numbers = {int(getattr(item, "repetition_number")) for item in items}
        return numbers >= {1, 2, 3} and len(items) >= 3

    eligible_keys = [key for key, items in grouped.items() if _complete(items)]
    questions = {key[0] for key in eligible_keys}
    mention_questions = set()
    citation_questions = set()
    by_engine: Dict[str, Dict[str, set]] = {}
    for question_id, engine in eligible_keys:
        items = grouped[(question_id, engine)]
        mentions = sum(1 for item in items if item.mentioned_sunnystep)
        citations = sum(1 for item in items if cites_sunnystep(item.cited_urls))
        engine_bucket = by_engine.setdefault(engine, {"questions": set(), "mentions": set(), "citations": set()})
        engine_bucket["questions"].add(question_id)
        if mentions >= 2:
            mention_questions.add(question_id)
            engine_bucket["mentions"].add(question_id)
        if citations >= 2:
            citation_questions.add(question_id)
            engine_bucket["citations"].add(question_id)
    eligible = len(questions)
    return {
        "eligible_questions": eligible,
        "mention_rate": (len(mention_questions) / eligible) if eligible else None,
        "citation_rate": (len(citation_questions) / eligible) if eligible else None,
        "engines": {
            engine: {
                "eligible_questions": len(values["questions"]),
                "mention_rate": (len(values["mentions"]) / len(values["questions"])) if values["questions"] else None,
                "citation_rate": (len(values["citations"]) / len(values["questions"])) if values["questions"] else None,
            }
            for engine, values in by_engine.items()
        },
        "incomplete_excluded": True,
    }


def competitor_catalogue_from_config(path: str) -> List[Dict[str, Any]]:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = REPO_ROOT / resolved
    if not resolved.exists():
        resolved = REPO_ROOT / "config" / "competitors.yaml"
    return load_competitor_aliases(resolved)
