import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "data_sources" / "modules" / "dataforseo.py"
)


def load_dataforseo_module():
    spec = importlib.util.spec_from_file_location("dataforseo_under_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class DataForSEOResilienceTests(unittest.TestCase):
    """Serper-backed DataForSEO compatibility client uses session.post, not _post."""

    def setUp(self) -> None:
        module = load_dataforseo_module()
        self.client = object.__new__(module.DataForSEO)
        self.client.base_url = "https://google.serper.dev"
        self.client.session = SimpleNamespace(post=Mock())

    def _set_search_payload(self, payload):
        self.client.session.post.return_value = _FakeResponse(payload)

    def test_get_serp_data_returns_empty_organic_when_result_is_missing(self):
        self._set_search_payload({})

        result = self.client.get_serp_data("test keyword")

        self.assertEqual(result["organic_results"], [])
        self.assertEqual(result["total_results"], 0)
        self.client.session.post.assert_called_once()

    def test_get_keyword_ideas_returns_empty_list_when_related_searches_missing(self):
        self._set_search_payload({"organic": []})

        result = self.client.get_keyword_ideas("seed keyword")

        self.assertEqual(result, [])

    def test_analyze_competitor_handles_missing_organic_without_crashing(self):
        self._set_search_payload({})

        result = self.client.analyze_competitor(
            "competitor.com", ["keyword one"], your_domain="example.com"
        )

        self.assertEqual(len(result["comparison"]), 1)
        row = result["comparison"][0]
        self.assertIsNone(row["competitor_position"])
        self.assertIsNone(row["your_position"])


if __name__ == "__main__":
    unittest.main()
