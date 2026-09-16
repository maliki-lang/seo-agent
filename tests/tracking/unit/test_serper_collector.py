from datetime import date

from data_sources.tracking.catalogs import KeywordRecord
from data_sources.tracking.collectors.serper import SerperCollector, detect_ai_overview, sunnystep_position
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import AIOverviewStatus


def _kw(keyword_id: str, keyword: str) -> KeywordRecord:
    return KeywordRecord(
        keyword_id=keyword_id,
        keyword=keyword,
        cluster="comfortable shoes (discovery)",
        target_page="https://sunnystep.com/",
        country="sgp",
        device="all",
        language="en",
        active=True,
        valid_from="2026-01-01",
        valid_to="",
    )


def test_ai_overview_states():
    assert detect_ai_overview({})[0] == AIOverviewStatus.UNVERIFIED
    assert detect_ai_overview({"aiOverview": None})[0] == AIOverviewStatus.ABSENT
    assert detect_ai_overview({"aiOverview": {}})[0] == AIOverviewStatus.ABSENT
    status, citations = detect_ai_overview({
        "aiOverview": {"links": [{"link": "https://example.com/a"}]}
    })
    assert status == AIOverviewStatus.PRESENT
    assert citations == ["https://example.com/a"]


def test_position_zero_when_sunnystep_absent():
    organic = [
        {"position": 1, "link": "https://anothersole.com/shoes"},
        {"position": 2, "link": "https://luccavudor.com/"},
    ]
    assert sunnystep_position(organic) == 0


def test_serper_collector_parses_top_domains_and_position():
    def search(keyword):
        return {
            "organic": [
                {"position": 1, "link": "https://www.anothersole.com/a"},
                {"position": 2, "link": "https://sunnystep.com/collections/walking-shoes"},
            ]
        }

    collector = SerperCollector(
        TrackingConfig(),
        search_fn=search,
        keywords=[_kw("k001", "walking shoes singapore")],
    )
    result = collector.collect(
        run_id="r1",
        as_of_date=date(2026, 9, 15),
        start_date=date(2026, 9, 15),
        end_date=date(2026, 9, 15),
    )
    assert len(result.rows) == 1
    assert result.rows[0].sunnystep_position == 2
    assert result.rows[0].top_10_domains[0] == "anothersole.com"
    assert result.rows[0].ai_overview_status == AIOverviewStatus.UNVERIFIED
