import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from data_sources.tracking.config import load_config
from data_sources.tracking.enums import ChannelClass, Source
from data_sources.tracking.models import Ga4DailyRow, GscDailyRow
from data_sources.tracking.transforms.normalize import (
    as_of_date,
    canonicalize_url,
    natural_key,
    normalize_host,
    row_hash,
)


def test_load_config_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("TRACKING_DAILY_COST_CAP_USD", raising=False)
    config = load_config()
    assert config.timezone == "Asia/Singapore"
    assert config.backfill_days == 90
    assert "gsc_property" in config.public_dict()
    assert "serper_api_key" not in json.dumps(config.public_dict())


def test_config_fail_fast_for_selected_fields():
    from data_sources.tracking.config import TrackingConfig
    from data_sources.tracking.exceptions import ConfigurationError

    config = TrackingConfig(openai_api_key="")
    try:
        config.require("openai_api_key")
        assert False, "expected ConfigurationError"
    except ConfigurationError as exc:
        assert "openai_api_key" in str(exc)


def test_natural_key_and_row_hash_ignore_collection_metadata():
    row_a = GscDailyRow(
        run_id="r1",
        as_of_date=date(2026, 9, 15),
        date=date(2026, 9, 10),
        query="walking shoes singapore",
        page="https://sunnystep.com/collections/walking-shoes",
        country="sgp",
        clicks=10,
        impressions=100,
        ctr=0.1,
        position=4.2,
        is_brand=False,
        brand_rule_version="v1",
        collected_at="2026-09-15T00:00:00+00:00",
    )
    row_b = GscDailyRow(
        run_id="r2",
        as_of_date=date(2026, 9, 15),
        date=date(2026, 9, 10),
        query="walking shoes singapore",
        page="https://sunnystep.com/collections/walking-shoes",
        country="sgp",
        clicks=10,
        impressions=100,
        ctr=0.1,
        position=4.2,
        is_brand=False,
        brand_rule_version="v1",
        collected_at="2026-09-16T00:00:00+00:00",
    )
    assert row_a.natural_key == row_b.natural_key
    assert row_a.row_hash == row_b.row_hash
    assert row_a.natural_key == natural_key(
        ["2026-09-10", "walking shoes singapore", "https://sunnystep.com/collections/walking-shoes", "sgp"]
    )


def test_url_and_host_normalization():
    assert normalize_host("https://WWW.Sunnystep.com/path") == "sunnystep.com"
    assert canonicalize_url("https://sunnystep.com/blogs/news/test/") == "https://sunnystep.com/blogs/news/test"
    assert canonicalize_url("javascript:alert(1)") == ""


def test_as_of_date_uses_singapore_timezone():
    utc = datetime(2026, 9, 14, 16, 30, tzinfo=timezone.utc)  # 00:30 SGT on 15 Sep
    assert as_of_date(utc, "Asia/Singapore").isoformat() == "2026-09-15"


def test_ga4_revenue_hash_uses_decimal_text():
    row = Ga4DailyRow(
        run_id="r1",
        as_of_date=date(2026, 9, 15),
        date=date(2026, 9, 10),
        session_source="google",
        session_medium="organic",
        landing_page="/collections/flats",
        channel_class=ChannelClass.ORGANIC_SEARCH,
        sessions=3,
        engaged_sessions=2,
        purchases=1,
        total_revenue=Decimal("12.50"),
    )
    assert Source.GA4.value in row.payload()["source"]
    assert row.payload()["total_revenue"] == "12.50"
    assert row_hash(row.payload()) == row.row_hash
