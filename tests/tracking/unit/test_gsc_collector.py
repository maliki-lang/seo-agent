from datetime import date

from data_sources.tracking.collectors.gsc import GscCollector
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.exceptions import PermissionDenied
from data_sources.tracking.transforms.brand_label import BrandClassifier


def test_gsc_collector_parses_and_paginates(monkeypatch):
    monkeypatch.setattr("data_sources.tracking.collectors.gsc.GSC_ROW_LIMIT", 1)
    calls = []

    def query(request):
        calls.append(request)
        assert request["dimensions"] == ["date", "query", "page", "country"]
        if request["startRow"] == 0:
            return {
                "rows": [{
                    "keys": ["2026-09-10", "Sunnystep shoes", "https://sunnystep.com/", "sgp"],
                    "clicks": 4,
                    "impressions": 20,
                    "ctr": 0.2,
                    "position": 3.1,
                }]
            }
        if request["startRow"] == 1:
            return {
                "rows": [{
                    "keys": ["2026-09-10", "comfortable shoes singapore", "https://sunnystep.com/blogs/news/x", "sgp"],
                    "clicks": 1,
                    "impressions": 50,
                    "ctr": 0.02,
                    "position": 12.0,
                }]
            }
        return {"rows": []}

    collector = GscCollector(
        TrackingConfig(gsc_country="sgp"),
        query_fn=query,
        brand_classifier=BrandClassifier(terms=["sunnystep"]),
    )
    result = collector.collect(
        run_id="r1",
        as_of_date=date(2026, 9, 15),
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 10),
    )
    assert len(result.rows) == 2
    assert result.rows[0].is_brand is True
    assert result.rows[1].is_brand is False
    assert result.rows[0].brand_rule_version == "brand_rules_v1"
    assert calls[0]["startRow"] == 0
    assert len(calls) == 3


def test_gsc_permission_denied_does_not_yield_rows():
    class FakeHttpError(Exception):
        resp = type("R", (), {"status": 403})()

    def query(_request):
        raise FakeHttpError("permission denied")

    collector = GscCollector(
        TrackingConfig(),
        query_fn=query,
        brand_classifier=BrandClassifier(terms=["sunnystep"]),
    )
    try:
        collector.collect(
            run_id="r1",
            as_of_date=date(2026, 9, 15),
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 10),
        )
        assert False, "expected PermissionDenied"
    except PermissionDenied:
        pass
