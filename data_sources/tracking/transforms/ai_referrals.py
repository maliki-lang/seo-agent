from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, List, Set

import yaml

from ..config import REPO_ROOT, TrackingConfig
from ..enums import ChannelClass
from .normalize import normalize_blank, normalize_host

ORGANIC_MEDIUMS = {"organic"}
ORGANIC_SOURCES = {"google", "bing", "yahoo", "duckduckgo", "baidu", "yandex"}
ChannelClassifier = Callable[[str, str], ChannelClass]


def load_ai_referrers(path: Path) -> List[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(item).strip().lower() for item in data.get("ai_referrers", []) if str(item).strip()]


def _source_candidates(session_source: str) -> Set[str]:
    raw = normalize_blank(session_source).lower()
    candidates = {raw, normalize_host(raw)}
    for part in raw.replace("https://", "").replace("http://", "").split("/"):
        host = normalize_host(part)
        if host:
            candidates.add(host)
    return {item for item in candidates if item}


def _is_ai_referral(session_source: str, ai_entries: Iterable[str]) -> bool:
    raw = normalize_blank(session_source).lower()
    hosts = _source_candidates(session_source)
    for entry in ai_entries:
        entry_n = str(entry).strip().lower()
        if not entry_n:
            continue
        entry_host = normalize_host(entry_n)
        path = ""
        remainder = entry_n.split("://")[-1]
        if "/" in remainder:
            path = remainder.split("/", 1)[1]
        if path and entry_host in hosts and path in raw:
            return True
        if not path:
            for host in hosts:
                if host == entry_host or host.endswith("." + entry_host):
                    return True
    return False


def classify_channel(
    session_source: str,
    session_medium: str,
    ai_hosts: Iterable[str],
    organic_mediums: Iterable[str] = ORGANIC_MEDIUMS,
    organic_sources: Iterable[str] = ORGANIC_SOURCES,
) -> ChannelClass:
    source = normalize_blank(session_source)
    medium = normalize_blank(session_medium).lower()
    if _is_ai_referral(source, ai_hosts):
        return ChannelClass.AI_REFERRAL
    mediums = {item.lower() for item in organic_mediums}
    sources = {item.lower() for item in organic_sources}
    if medium in mediums:
        return ChannelClass.ORGANIC_SEARCH
    if source.lower() in sources and medium in mediums | {"(not set)", "none", "(none)"}:
        return ChannelClass.ORGANIC_SEARCH
    return ChannelClass.OTHER


def classifier_from_config(config: TrackingConfig) -> ChannelClassifier:
    path = Path(config.ai_referrers_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    ai_hosts = [str(item).strip().lower() for item in data.get("ai_referrers", []) if str(item).strip()]
    organic = data.get("organic_search") or {}
    mediums = [item.lower() for item in organic.get("mediums") or list(ORGANIC_MEDIUMS)]
    sources = [item.lower() for item in organic.get("sources") or list(ORGANIC_SOURCES)]

    def _classify(session_source: str, session_medium: str) -> ChannelClass:
        return classify_channel(session_source, session_medium, ai_hosts, mediums, sources)

    return _classify
