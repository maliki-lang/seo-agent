from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List

from .config import REPO_ROOT, TrackingConfig
from .exceptions import ConfigurationError, DataQualityError
from .storage import TrackingStore
from .transforms.normalize import natural_key, normalize_query, utc_now_iso


@dataclass(frozen=True)
class KeywordRecord:
    keyword_id: str
    keyword: str
    cluster: str
    target_page: str
    country: str
    device: str
    language: str
    active: bool
    valid_from: str
    valid_to: str


@dataclass(frozen=True)
class QuestionRecord:
    question_id: str
    question: str
    cluster: str
    target_page: str
    locale: str
    active: bool
    valid_from: str
    valid_to: str


def _path(config: TrackingConfig, relative: str) -> Path:
    path = Path(relative)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def load_keyword_catalog(config: TrackingConfig) -> List[KeywordRecord]:
    path = _path(config, config.keyword_catalog_path)
    if not path.exists():
        raise ConfigurationError(f"Keyword catalogue not found: {path}")
    rows: List[KeywordRecord] = []
    with path.open(encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            keyword = normalize_query(raw["keyword"]).lower()
            rows.append(
                KeywordRecord(
                    keyword_id=raw.get("keyword_id") or natural_key([keyword, raw.get("country", "sgp"), raw.get("device", "all")]),
                    keyword=keyword,
                    cluster=raw["cluster"].strip(),
                    target_page=raw["target_page"].strip(),
                    country=(raw.get("country") or "sgp").strip().lower(),
                    device=(raw.get("device") or "all").strip().lower(),
                    language=(raw.get("language") or "en").strip().lower(),
                    active=str(raw.get("active", "1")).strip() not in {"0", "false", "no"},
                    valid_from=(raw.get("valid_from") or date.min.isoformat()),
                    valid_to=(raw.get("valid_to") or ""),
                )
            )
    return rows


def load_question_catalog(config: TrackingConfig) -> List[QuestionRecord]:
    path = _path(config, config.ai_question_catalog_path)
    if not path.exists():
        raise ConfigurationError(f"AI question catalogue not found: {path}")
    rows: List[QuestionRecord] = []
    with path.open(encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            question = normalize_query(raw["question"])
            rows.append(
                QuestionRecord(
                    question_id=raw.get("question_id") or natural_key([question]),
                    question=question,
                    cluster=raw["cluster"].strip(),
                    target_page=raw["target_page"].strip(),
                    locale=(raw.get("locale") or "en-SG").strip(),
                    active=str(raw.get("active", "1")).strip() not in {"0", "false", "no"},
                    valid_from=(raw.get("valid_from") or date.min.isoformat()),
                    valid_to=(raw.get("valid_to") or ""),
                )
            )
    return rows


def sync_catalogues(store: TrackingStore, config: TrackingConfig) -> None:
    now = utc_now_iso()
    keywords = load_keyword_catalog(config)
    questions = load_question_catalog(config)
    store.executemany(
        """
        INSERT INTO keyword_catalog(
            keyword_id, keyword, cluster, target_page, country, device, language,
            active, valid_from, valid_to, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(keyword_id) DO UPDATE SET
            keyword = excluded.keyword,
            cluster = excluded.cluster,
            target_page = excluded.target_page,
            country = excluded.country,
            device = excluded.device,
            language = excluded.language,
            active = excluded.active,
            valid_from = excluded.valid_from,
            valid_to = excluded.valid_to,
            updated_at = excluded.updated_at
        """,
        [
            (
                row.keyword_id,
                row.keyword,
                row.cluster,
                row.target_page,
                row.country,
                row.device,
                row.language,
                int(row.active),
                row.valid_from,
                row.valid_to or None,
                now,
                now,
            )
            for row in keywords
        ],
    )
    store.executemany(
        """
        INSERT INTO ai_question_catalog(
            question_id, question, cluster, target_page, locale, active,
            valid_from, valid_to, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(question_id) DO UPDATE SET
            question = excluded.question,
            cluster = excluded.cluster,
            target_page = excluded.target_page,
            locale = excluded.locale,
            active = excluded.active,
            valid_from = excluded.valid_from,
            valid_to = excluded.valid_to,
            updated_at = excluded.updated_at
        """,
        [
            (
                row.question_id,
                row.question,
                row.cluster,
                row.target_page,
                row.locale,
                int(row.active),
                row.valid_from,
                row.valid_to or None,
                now,
                now,
            )
            for row in questions
        ],
    )


def active_keywords(config: TrackingConfig) -> List[KeywordRecord]:
    return [row for row in load_keyword_catalog(config) if row.active]


def active_questions(config: TrackingConfig) -> List[QuestionRecord]:
    return [row for row in load_question_catalog(config) if row.active]


def require_catalogue_minimums(config: TrackingConfig) -> None:
    keywords = active_keywords(config)
    questions = active_questions(config)
    if len(keywords) < 50:
        raise DataQualityError(f"Keyword catalogue has {len(keywords)} active rows; need at least 50")
    if len(questions) < 20:
        raise DataQualityError(f"AI question catalogue has {len(questions)} active rows; need at least 20")
