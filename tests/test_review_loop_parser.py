import importlib.util
import sys
import types
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data_sources" / "modules" / "review_loop.py"
)


def load_review_loop_module():
    """Load review_loop.py with its `.ledger` import stubbed (no deps)."""
    ledger_stub = types.ModuleType("data_sources.modules.ledger")
    ledger_stub.LEDGER_DIR = Path("/tmp/seomachine-test-ledger")

    pkg_ds = types.ModuleType("data_sources")
    pkg_ds.__path__ = []
    pkg_mod = types.ModuleType("data_sources.modules")
    pkg_mod.__path__ = []

    injected = {
        "data_sources": pkg_ds,
        "data_sources.modules": pkg_mod,
        "data_sources.modules.ledger": ledger_stub,
    }
    previous = {n: sys.modules.get(n) for n in injected}
    sys.modules.update(injected)
    try:
        spec = importlib.util.spec_from_file_location(
            "data_sources.modules.review_loop", MODULE_PATH
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["data_sources.modules.review_loop"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for n, v in previous.items():
            if v is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = v


class VerdictParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_review_loop_module()

    def parse(self, text, threshold=None):
        return self.m.parse_review_verdict(text, threshold)

    def test_explicit_approval(self):
        v = self.parse("Score: 92/100. Approved — ready to publish.")
        self.assertTrue(v.approved)
        self.assertEqual(v.score, 92.0)
        self.assertEqual(v.confidence, "high")

    def test_explicit_rejection_with_feedback(self):
        v = self.parse(
            "This needs revision. Score 68/100.\n"
            "- Add a citation for the arch-support claim\n"
            "- Shorten the intro"
        )
        self.assertFalse(v.approved)
        self.assertEqual(v.score, 68.0)
        self.assertIn("Add a citation for the arch-support claim", v.feedback)
        self.assertEqual(len(v.feedback), 2)

    def test_not_approved_beats_approve_substring(self):
        # "not approved" contains "approved" — negative must win.
        v = self.parse("Not approved yet, a few tweaks needed.")
        self.assertFalse(v.approved)

    def test_score_below_stated_threshold_is_rejection(self):
        v = self.parse("Rating 80. Threshold is 85.")
        self.assertFalse(v.approved)
        self.assertEqual(v.score, 80.0)
        self.assertEqual(v.threshold, 85.0)

    def test_score_meets_threshold_no_keywords(self):
        v = self.parse("Overall 88. Minimum 85.")
        self.assertTrue(v.approved)
        self.assertEqual(v.confidence, "medium")

    def test_ten_point_scale_normalised(self):
        v = self.parse("I'd rate this 9/10, ship it.")
        self.assertTrue(v.approved)
        self.assertEqual(v.score, 90.0)

    def test_ambiguous_returns_none(self):
        v = self.parse("Interesting draft. What audience is this for?")
        self.assertIsNone(v.approved)
        self.assertEqual(v.confidence, "low")

    def test_explicit_threshold_arg_overrides_text(self):
        v = self.parse("Score 82.", threshold=85)
        self.assertFalse(v.approved)

    def test_raw_always_preserved(self):
        text = "approve"
        self.assertEqual(self.parse(text).raw, text)


if __name__ == "__main__":
    unittest.main()
