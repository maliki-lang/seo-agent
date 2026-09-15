import os
import unittest
from unittest import mock

import daily_sync


class DailySyncConfigTests(unittest.TestCase):
    def test_lark_base_token_reads_environment(self):
        with mock.patch.dict(os.environ, {"LARK_BASE_APP_TOKEN": "env-token-value"}, clear=False):
            self.assertEqual(daily_sync.lark_base_token(), "env-token-value")

    def test_lark_base_token_fails_when_missing(self):
        env = {k: v for k, v in os.environ.items() if k != "LARK_BASE_APP_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit):
                daily_sync.lark_base_token()

    def test_source_does_not_hardcode_base_token(self):
        source = daily_sync.__file__
        text = open(source, encoding="utf-8").read()
        self.assertNotIn("BASE_TOKEN =", text)
        self.assertIn("LARK_BASE_APP_TOKEN", text)
