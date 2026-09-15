from datetime import date
from decimal import Decimal

from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import ChannelClass, RunStatus, RunType
from data_sources.tracking.exceptions import RunLockError
from data_sources.tracking.models import Ga4DailyRow, GscDailyRow, RunLog
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _store(tmp_path) -> TrackingStore:
    config = TrackingConfig(storage_url=f"sqlite:///{tmp_path / 't.db'}")
    store = TrackingStore(config)
    store.migrate()
    return store


def test_migrate_from_empty(tmp_path):
    store = _store(tmp_path)
    applied = store.migrate()
    assert applied == []
    assert store.count("gsc_daily") == 0


def test_gsc_upsert_is_idempotent(tmp_path):
    store = _store(tmp_path)
    row = GscDailyRow(
        run_id="run-1",
        as_of_date=date(2026, 9, 15),
        date=date(2026, 9, 10),
        query="comfortable shoes singapore",
        page="https://sunnystep.com/",
        country="sgp",
        clicks=5,
        impressions=50,
        ctr=0.1,
        position=8.0,
        is_brand=False,
        brand_rule_version="v1",
    )
    first = store.upsert_gsc([row])
    second = store.upsert_gsc([row])
    assert first.inserted == 1
    assert second.unchanged == 1
    assert store.count("gsc_daily") == 1

    changed = GscDailyRow(
        run_id="run-2",
        as_of_date=date(2026, 9, 15),
        date=date(2026, 9, 10),
        query="comfortable shoes singapore",
        page="https://sunnystep.com/",
        country="sgp",
        clicks=9,
        impressions=50,
        ctr=0.18,
        position=7.0,
        is_brand=False,
        brand_rule_version="v1",
    )
    third = store.upsert_gsc([changed])
    assert third.updated == 1
    assert store.count("gsc_daily") == 1


def test_ga4_unique_constraint(tmp_path):
    store = _store(tmp_path)
    row = Ga4DailyRow(
        run_id="run-1",
        as_of_date=date(2026, 9, 15),
        date=date(2026, 9, 10),
        session_source="google",
        session_medium="organic",
        landing_page="/",
        channel_class=ChannelClass.ORGANIC_SEARCH,
        sessions=10,
        engaged_sessions=8,
        purchases=1,
        total_revenue=Decimal("20.00"),
    )
    store.upsert_ga4([row])
    store.upsert_ga4([row])
    assert store.count("ga4_daily") == 1


def test_run_lock_prevents_overlap(tmp_path):
    store = _store(tmp_path)
    store.acquire_lock("2026-09-15", "daily", "run-a", ttl_seconds=3600)
    try:
        store.acquire_lock("2026-09-15", "daily", "run-b", ttl_seconds=3600)
        raised = False
    except RunLockError:
        raised = True
    assert raised
    store.release_lock("2026-09-15", "daily", "run-a")
    store.acquire_lock("2026-09-15", "daily", "run-b", ttl_seconds=3600)


def test_run_log_persists(tmp_path):
    store = _store(tmp_path)
    run = RunLog(
        run_id="abc",
        run_type=RunType.DAILY,
        as_of_date=date(2026, 9, 15),
        started_at=utc_now_iso(),
        status=RunStatus.RUNNING,
    )
    store.insert_run(run)
    store.update_run("abc", status=RunStatus.SUCCEEDED, finished_at=utc_now_iso())
    loaded = store.get_run("abc")
    assert loaded["status"] == "succeeded"
