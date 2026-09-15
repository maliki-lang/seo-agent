from datetime import date
from decimal import Decimal

from data_sources.tracking.collectors.ga4 import Ga4Collector, fake_ga4_row
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import ChannelClass


class _Response:
    def __init__(self, rows):
        self.rows = rows


def test_ga4_collector_normalizes_and_labels_channels():
    def run_report(spec):
        assert spec["dimensions"] == [
            "date",
            "sessionSource",
            "sessionMedium",
            "landingPagePlusQueryString",
        ]
        assert "sessions" in spec["metrics"]
        return _Response(
            [
                fake_ga4_row(
                    ["20260910", "google", "organic", "/collections/flats"],
                    ["10", "8", "1", "25.50"],
                ),
                fake_ga4_row(
                    ["20260910", "https://chatgpt.com", "referral", "(not set)"],
                    ["3", "2", "0", "0"],
                ),
                fake_ga4_row(
                    ["20260910", "(direct)", "(none)", ""],
                    ["5", "1", "0", "0"],
                ),
            ]
        )

    collector = Ga4Collector(TrackingConfig(), run_report_fn=run_report)
    result = collector.collect(
        run_id="r1",
        as_of_date=date(2026, 9, 15),
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 10),
    )
    assert len(result.rows) == 3
    assert result.rows[0].channel_class == ChannelClass.ORGANIC_SEARCH
    assert result.rows[0].total_revenue == Decimal("25.50")
    assert result.rows[1].channel_class == ChannelClass.AI_REFERRAL
    assert result.rows[1].landing_page == "(not set)"
    assert result.rows[2].channel_class == ChannelClass.OTHER
    assert result.rows[2].session_source == "(direct)"
