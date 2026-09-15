import unittest

from data_sources.modules.shopify_publisher import ShopifyPublisher


class ShopifyUrlTests(unittest.TestCase):
    def test_admin_api_base_url_is_exact(self):
        self.assertEqual(
            ShopifyPublisher.admin_api_base_url("gosunnystep.myshopify.com", "2026-01"),
            "https://gosunnystep.myshopify.com/admin/api/2026-01",
        )

    def test_oauth_token_url_is_exact(self):
        self.assertEqual(
            ShopifyPublisher.oauth_token_url("gosunnystep.myshopify.com"),
            "https://gosunnystep.myshopify.com/admin/oauth/access_token",
        )

    def test_article_admin_url_is_exact(self):
        self.assertEqual(
            ShopifyPublisher.article_admin_url("gosunnystep", "94480597052"),
            "https://gosunnystep.myshopify.com/admin/articles/94480597052",
        )

    def test_article_admin_url_strips_shop_suffix(self):
        self.assertEqual(
            ShopifyPublisher.article_admin_url("gosunnystep.myshopify.com", "1"),
            "https://gosunnystep.myshopify.com/admin/articles/1",
        )

    def test_rejects_literal_braces_in_shop(self):
        with self.assertRaises(ValueError):
            ShopifyPublisher.admin_api_base_url("{gosunnystep.myshopify.com}", "2026-01")
        with self.assertRaises(ValueError):
            ShopifyPublisher.oauth_token_url("https://{shop}")

    def test_publisher_uses_admin_api_base_without_network(self):
        publisher = ShopifyPublisher(
            shop="gosunnystep.myshopify.com",
            access_token="test-token",
            api_version="2026-01",
        )
        self.assertEqual(
            publisher.base_url,
            "https://gosunnystep.myshopify.com/admin/api/2026-01",
        )
        self.assertNotIn("{", publisher.base_url)
        self.assertNotIn("}", publisher.base_url)
