from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "1"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def as_of_date(now: datetime | None = None, tz_name: str = "Asia/Singapore") -> date:
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(ZoneInfo(tz_name)).date()


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def canonical_json(payload: Mapping[str, Any]) -> str:
    def _default(obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return format(obj, "f")
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        raise TypeError(f"Unsupported type {type(obj)!r}")

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_default, ensure_ascii=True)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def natural_key(parts: Iterable[Any]) -> str:
    normalized = ["" if part is None else str(part).strip() for part in parts]
    return sha256_hex("\x1f".join(normalized))


def row_hash(payload: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json(payload))


def normalize_host(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = text.split("/")[0]
    text = text.split(":")[0]
    if text.startswith("www."):
        text = text[4:]
    return text


def canonicalize_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith(("javascript:", "data:", "file:", "vbscript:")):
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    if scheme not in {"http", "https"}:
        return ""
    host = normalize_host(parsed.netloc)
    if not host:
        return ""
    path = parsed.path or "/"
    path = re.sub(r"/{2,}", "/", path)
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunparse((scheme, host, path, "", parsed.query, ""))


def normalize_query(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


_APOSTROPHE_CHARS = {
    "\u2019": "'",  # right single quotation mark
    "\u2018": "'",  # left single quotation mark
    "\u02bc": "'",  # modifier letter apostrophe
    "`": "'",
    "´": "'",
}
_CATALOGUE_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_CATALOGUE_SPACE = re.compile(r"\s+")


def normalize_catalogue_query(value: str) -> str:
    """Deterministic catalogue key: NFKC, lower, apostrophe-safe, punct→space, collapse.

    Apostrophes are removed after canonicalization so approved variants such as
    "women's" / "womens" share one reviewable normalized key. No fuzzy merge.
    """
    import unicodedata

    text = unicodedata.normalize("NFKC", value or "")
    text = text.lower()
    for src, dst in _APOSTROPHE_CHARS.items():
        text = text.replace(src, dst)
    text = text.replace("'", "")
    text = _CATALOGUE_PUNCT.sub(" ", text)
    text = _CATALOGUE_SPACE.sub(" ", text).strip()
    return text


def catalogue_transformation_method(raw_query: str, normalized_keyword: str, *, merged: bool) -> str:
    """Classify how a raw GSC query relates to the normalized candidate key."""
    if merged:
        return "merged_variants"
    basic = normalize_query(raw_query).lower()
    if basic == normalized_keyword:
        return "exact"
    if normalize_catalogue_query(raw_query) == normalized_keyword:
        return "normalized"
    return "normalized"


def normalize_blank(value: str, default: str = "(not set)") -> str:
    text = (value or "").strip()
    lowered = text.lower()
    if lowered in {"(direct)", "direct"}:
        return "(direct)"
    if not text or lowered in {"(not set)", "not set", "(none)", "none", "null"}:
        return default
    return text
