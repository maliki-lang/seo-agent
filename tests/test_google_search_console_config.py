import os
import unittest
from unittest import mock

from data_sources.modules import google_search_console as gsc_module


class FakeSearchAnalytics:
    def __init__(self):
        self.calls = []

    def query(self, siteUrl, body):
        self.calls.append({"siteUrl": siteUrl, "body": body})
        return types_namespace(execute=lambda: {"rows": []})


def types_namespace(**kwargs):
    return type("NS", (), kwargs)


class GoogleSearchConsoleConfigTests(unittest.TestCase):
    def make_client(self, env, **kwargs):
        fake = FakeSearchAnalytics()
        service = types_namespace(searchanalytics=lambda: fake)
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(gsc_module.os.path, "exists", return_value=True), \
                mock.patch.object(gsc_module.service_account.Credentials,
                                  "from_service_account_file", return_value=None), \
                mock.patch.object(gsc_module, "build", return_value=service):
            client = gsc_module.GoogleSearchConsole(**kwargs)
        return client, fake

    def test_property_takes_precedence_over_site_url(self):
        client, _ = self.make_client({
            "GSC_PROPERTY": "sc-domain:sunnystep.com",
            "GSC_SITE_URL": "https://www.sunnystep.com/",
            "GSC_CREDENTIALS_PATH": "key.json",
        })
        self.assertEqual(client.site_url, "sc-domain:sunnystep.com")

    def test_site_url_used_when_property_missing(self):
        client, _ = self.make_client({
            "GSC_SITE_URL": "https://www.sunnystep.com/",
            "GSC_CREDENTIALS_PATH": "key.json",
        })
        self.assertEqual(client.site_url, "https://www.sunnystep.com/")

    def test_country_filter_added_to_every_query(self):
        client, fake = self.make_client({
            "GSC_PROPERTY": "sc-domain:sunnystep.com",
            "GSC_COUNTRY": "SGP",
            "GSC_CREDENTIALS_PATH": "key.json",
        })
        client.get_keyword_positions(days=7)
        client.get_page_performance("/collections/women")

        # get_page_performance stops after its first query when there are no rows
        self.assertEqual(len(fake.calls), 2)
        country = {"dimension": "country", "operator": "equals", "expression": "sgp"}
        for call in fake.calls:
            self.assertEqual(call["siteUrl"], "sc-domain:sunnystep.com")
            for group in call["body"]["dimensionFilterGroups"]:
                self.assertIn(country, group["filters"])

        # The page filter is kept alongside the country filter
        page_filters = fake.calls[1]["body"]["dimensionFilterGroups"][0]["filters"]
        self.assertEqual(page_filters[0]["dimension"], "page")

    def test_empty_country_means_no_filter(self):
        client, fake = self.make_client({
            "GSC_PROPERTY": "sc-domain:sunnystep.com",
            "GSC_COUNTRY": "sgp",
            "GSC_CREDENTIALS_PATH": "key.json",
        }, country="")
        client.get_keyword_positions(days=7)
        self.assertEqual(fake.calls[0]["body"]["dimensionFilterGroups"], [])


if __name__ == "__main__":
    unittest.main()
