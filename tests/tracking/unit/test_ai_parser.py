from data_sources.modules.dataforseo import DataForSEO
from data_sources.modules.serper import SerperClient
from data_sources.tracking.transforms.ai_parser import parse_answer, stable_rates
from data_sources.tracking.enums import Engine
from types import SimpleNamespace


def test_dataforseo_is_serper_alias():
    assert issubclass(DataForSEO, SerperClient)


def test_parser_handles_punctuation_markdown_and_false_positives():
    catalogue = [{"name": "Anothersole", "aliases": ["anothersole"]}]
    parsed = parse_answer(
        "SunnyStep is comfortable. See [site](https://sunnystep.com/blogs/news/x?utm=1) "
        "and anothersole.com vs sunny weather shoes.",
        competitor_catalogue=catalogue,
    )
    assert parsed.mentioned_sunnystep is True
    assert parsed.cited_sunnystep is True
    assert any("sunnystep.com" in url for url in parsed.cited_urls)
    assert "Anothersole" in parsed.named_competitors

    negative = parse_answer("A sunny weather walk can help you step up your commute.")
    assert negative.mentioned_sunnystep is False
    assert negative.cited_sunnystep is False

    bare = parse_answer("Try www.sunnystep.com/collections/flats today.")
    assert bare.cited_sunnystep is True


def test_on_competitor_ignores_slip_on_and_particle_false_positives():
    from data_sources.tracking.transforms.ai_parser import (
        competitor_catalogue_from_config,
        named_competitors,
    )

    catalogue = competitor_catalogue_from_config("config/competitors.yaml")
    for q in (
        "slip on",
        "slips on",
        "slip on style",
        "put on shoes",
        "yoga influencers on instagram",
    ):
        assert "On" not in named_competitors(q, catalogue), q

    for q in (
        "on shoes",
        "on cloud shoes singapore",
        "where to buy on cloud shoes in singapore",
        "on running singapore",
    ):
        assert "On" in named_competitors(q, catalogue), q

def test_incomplete_repetitions_are_excluded_from_rates():
    rows = [
        SimpleNamespace(
            question_id="q001",
            engine=Engine.CHATGPT,
            mentioned_sunnystep=True,
            cited_urls=["https://sunnystep.com/"],
            repetition_number=1,
        ),
        SimpleNamespace(
            question_id="q001",
            engine=Engine.CHATGPT,
            mentioned_sunnystep=True,
            cited_urls=[],
            repetition_number=2,
        ),
    ]
    rates = stable_rates(rows)
    assert rates["eligible_questions"] == 0
    assert rates["mention_rate"] is None
