from datetime import date

from data_sources.tracking.catalogs import active_keywords
from data_sources.tracking.collectors.serper import SerperCollector
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import AIOverviewStatus, RunStatus
from data_sources.tracking.runner import TrackingRunner
from data_sources.tracking.storage import TrackingStore


def test_serper_collector_persists_fifty_plus_keywords(tmp_path):
    config = TrackingConfig(storage_url=f"sqlite:///{tmp_path / 'serp.db'}")
    keywords = active_keywords(config)
    assert len(keywords) >= 50

    def search(keyword):
        payload = {
            "organic": [
                {"position": 1, "link": "https://anothersole.com/shoes"},
                {"position": 3, "link": "https://sunnystep.com/collections/walking-shoes"},
            ]
        }
        if keyword.startswith("comfortable"):
            payload["aiOverview"] = {"links": [{"link": "https://sunnystep.com/"}]}
        elif keyword.startswith("walking"):
            payload["aiOverview"] = None
        return payload

    store = TrackingStore(config)
    store.migrate()
    collector = SerperCollector(config, search_fn=search, keywords=keywords)
    runner = TrackingRunner(config, store=store, serper_collector=collector)
    first = runner.collect_source("serper", date(2026, 9, 15))
    assert first["status"] == RunStatus.SUCCEEDED.value
    assert store.count("serp_daily") == len(keywords)
    assert store.count("raw_records") == len(keywords)

    statuses = {
        row["ai_overview_status"]
        for row in store.fetchall("SELECT ai_overview_status FROM serp_daily")
    }
    assert AIOverviewStatus.PRESENT.value in statuses
    assert AIOverviewStatus.ABSENT.value in statuses
    assert AIOverviewStatus.UNVERIFIED.value in statuses

    zero = store.fetchall(
        "SELECT COUNT(*) AS n FROM serp_daily WHERE sunnystep_position = 0"
    )[0]["n"]
    assert zero == 0
    assert store.fetchall(
        "SELECT COUNT(*) AS n FROM serp_daily WHERE sunnystep_position = 3"
    )[0]["n"] == len(keywords)

    second = runner.collect_source("serper", date(2026, 9, 15))
    assert store.count("serp_daily") == len(keywords)
    assert second["result"]["stats"]["unchanged"] == len(keywords)
