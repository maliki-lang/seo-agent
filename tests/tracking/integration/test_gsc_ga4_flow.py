from datetime import date
from decimal import Decimal

from data_sources.tracking.collectors.ga4 import Ga4Collector, fake_ga4_row
from data_sources.tracking.collectors.gsc import GscCollector
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import RunStatus
from data_sources.tracking.runner import TrackingRunner
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.brand_label import BrandClassifier


class _Response:
    def __init__(self, rows):
        self.rows = rows


def _store(tmp_path) -> TrackingStore:
    config = TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'phase2.db'}",
        gsc_country="sgp",
    )
    store = TrackingStore(config)
    store.migrate()
    return store, config


def test_daily_gsc_ga4_upsert_and_rerun(tmp_path):
    store, config = _store(tmp_path)

    def gsc_query(request):
        if request["dimensions"] == ["date"]:
            return {"rows": [{"clicks": 5, "impressions": 70}]}
        return {
            "rows": [{
                "keys": ["2026-09-10", "comfortable shoes singapore", "https://sunnystep.com/", "sgp"],
                "clicks": 5,
                "impressions": 70,
                "ctr": 0.0714,
                "position": 8.0,
            }]
        }

    def ga4_report(spec):
        if spec["dimensions"] == ["date"]:
            return _Response([fake_ga4_row(["20260910"], ["12"])])
        return _Response(
            [
                fake_ga4_row(
                    ["20260910", "google", "organic", "/"],
                    ["12", "9", "1", "10.00"],
                )
            ]
        )

    runner = TrackingRunner(
        config,
        store=store,
        gsc_collector=GscCollector(
            config, query_fn=gsc_query, brand_classifier=BrandClassifier(terms=["sunnystep"])
        ),
        ga4_collector=Ga4Collector(config, run_report_fn=ga4_report),
    )
    as_of = date(2026, 9, 13)  # GSC delay 3 -> 2026-09-10
    first = runner.run_daily(as_of, sources=["gsc", "ga4"])
    second = runner.run_daily(as_of, sources=["gsc", "ga4"])
    assert first["status"] == RunStatus.SUCCEEDED.value
    assert store.count("gsc_daily") == 1
    assert store.count("ga4_daily") == 1
    assert second["collectors"][0]["stats"]["unchanged"] == 1
    assert store.count("gsc_daily") == 1
    assert store.count("quality_check_log") >= 2


def test_partial_failure_keeps_other_source(tmp_path):
    store, config = _store(tmp_path)

    def gsc_query(_request):
        err = type("Http", (Exception,), {"resp": type("R", (), {"status": 403})()})("denied")
        raise err

    def ga4_report(spec):
        if spec["dimensions"] == ["date"]:
            return _Response([fake_ga4_row(["20260910"], ["3"])])
        return _Response(
            [fake_ga4_row(["20260910", "google", "organic", "/"], ["3", "2", "0", "0"])]
        )

    runner = TrackingRunner(
        config,
        store=store,
        gsc_collector=GscCollector(
            config, query_fn=gsc_query, brand_classifier=BrandClassifier(terms=["sunnystep"])
        ),
        ga4_collector=Ga4Collector(config, run_report_fn=ga4_report),
    )
    result = runner.run_daily(date(2026, 9, 13), sources=["gsc", "ga4"])
    assert result["status"] == RunStatus.PARTIAL.value
    assert store.count("gsc_daily") == 0
    assert store.count("ga4_daily") == 1
    assert "gsc" in result["failed_collectors"]
