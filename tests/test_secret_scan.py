import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "secret_scan.py"


def load_secret_scan():
    spec = importlib.util.spec_from_file_location("secret_scan_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SecretScanTests(unittest.TestCase):
    def test_flags_hardcoded_lark_token_assignment(self):
        module = load_secret_scan()
        assignment = "BASE_TOKEN = " + '"' + ("a" * 24) + '"'
        findings = module.scan_lines([assignment], Path(__file__))
        self.assertEqual(len(findings), 1)

    def test_allows_env_lookup(self):
        module = load_secret_scan()
        findings = module.scan_lines(
            ['token = os.getenv("LARK_BASE_APP_TOKEN", "").strip()'],
            Path(__file__),
        )
        self.assertEqual(findings, [])

    def test_allows_placeholders(self):
        module = load_secret_scan()
        findings = module.scan_lines(
            ['SHOPIFY_ACCESS_TOKEN="your_access_token_here"'],
            Path(__file__),
        )
        self.assertEqual(findings, [])
