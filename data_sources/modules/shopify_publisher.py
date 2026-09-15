"""
Shopify Publisher Module

Publishes draft articles to Shopify as hidden (unpublished) blog posts via the
Admin REST API. Drop-in sibling of shopline_publisher.py / wordpress_publisher.py
— same draft file format and parse_draft_file / markdown_to_html interface.

The site migrated Shopline -> Shopify, so this is the live publishing target.

A pre-publish eval GATE runs before any article is created: a draft that
hard-fails the YMYL compliance gate, or scores below ship-ready (85), is blocked
and never reaches the store. This is the enforced gate between generation and a
live page — the thing the prior agent was flagged for missing. Override only with
allow_below_gate=True (logged).

Auth mirrors the inventory-and-sales-agent: a custom-app `client_credentials`
grant that auto-refreshes the 24h token and writes it back to .env. Set the same
credentials this repo shares with that agent.

Env (data_sources/config/.env or repo-root .env):
    SHOPIFY_SHOP              full domain, e.g. "gosunnystep.myshopify.com"
                             (or SHOPIFY_STORE_HANDLE = "gosunnystep")
    SHOPIFY_CLIENT_ID         custom-app client id (for token auto-refresh)
    SHOPIFY_CLIENT_SECRET     custom-app client secret
    SHOPIFY_ACCESS_TOKEN      cached token (auto-refreshed; optional to set)
    SHOPIFY_TOKEN_ISSUED_AT   token issue time (managed automatically)
    SHOPIFY_API_VERSION       default "2026-01"
    SHOPIFY_BLOG_ID           target blog id (optional; else resolved by name/first)
    SHOPIFY_PUBLIC_URL_BASE   e.g. "https://sunnystep.com" (for ledger view URL)
    SHOPIFY_PUBLIC_BLOG_HANDLE e.g. "news"

Note: the custom app must have the `write_content` scope to create blog
articles. If you get a 403, add that scope to the app (or use a content-scoped
app) and re-run.
"""

import os
import re
import requests
from datetime import datetime, timezone
from typing import Dict, Optional, List
from pathlib import Path


class PublishBlocked(Exception):
    """Raised when the pre-publish eval gate blocks a draft."""

    def __init__(self, message: str, gate=None, scorecard=None):
        super().__init__(message)
        self.gate = gate
        self.scorecard = scorecard


class ShopifyPublisher:
    """Shopify Admin REST API client for publishing blog articles."""

    def __init__(
        self,
        shop: Optional[str] = None,
        access_token: Optional[str] = None,
        api_version: Optional[str] = None,
        env_file: Optional[str] = None,
    ):
        # Accept full domain (SHOPIFY_SHOP) or bare handle (SHOPIFY_STORE_HANDLE).
        shop = shop or os.getenv("SHOPIFY_SHOP", "")
        if not shop:
            handle = os.getenv("SHOPIFY_STORE_HANDLE", "gosunnystep")
            shop = handle if handle.endswith(".myshopify.com") else f"{handle}.myshopify.com"
        self.shop = shop
        self.store_handle = shop.replace(".myshopify.com", "")
        self.api_version = api_version or os.getenv("SHOPIFY_API_VERSION", "2026-01")
        self.client_id = os.getenv("SHOPIFY_CLIENT_ID")
        self.client_secret = os.getenv("SHOPIFY_CLIENT_SECRET")
        self._env_file = env_file or os.getenv("SHOPIFY_ENV_FILE") or str(
            Path(__file__).parent.parent / "config" / ".env"
        )

        self.base_url = f"https://{self.shop}/admin/api/{self.api_version}"
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "SEOMachine/1.0 (Shopify Content Publisher)",
        })
        # Resolve a valid token now (refresh via client_credentials if needed).
        self.access_token = access_token or self.get_token()
        self.session.headers["X-Shopify-Access-Token"] = self.access_token
        self._blogs_cache: Optional[Dict[str, str]] = None

    # ── token management (mirrors inventory-and-sales-agent) ─────────
    def _fetch_token(self) -> str:
        if not (self.client_id and self.client_secret):
            raise ValueError(
                "No SHOPIFY_ACCESS_TOKEN and no SHOPIFY_CLIENT_ID/SECRET to refresh it."
            )
        resp = requests.post(
            f"https://{self.shop}/admin/oauth/access_token",
            json={"client_id": self.client_id, "client_secret": self.client_secret,
                  "grant_type": "client_credentials"},
            timeout=15,
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        try:
            from dotenv import set_key
            set_key(self._env_file, "SHOPIFY_ACCESS_TOKEN", token)
            set_key(self._env_file, "SHOPIFY_TOKEN_ISSUED_AT",
                    datetime.now(timezone.utc).isoformat())
        except Exception:
            pass  # writing back is best-effort
        os.environ["SHOPIFY_ACCESS_TOKEN"] = token
        os.environ["SHOPIFY_TOKEN_ISSUED_AT"] = datetime.now(timezone.utc).isoformat()
        return token

    def get_token(self) -> str:
        """Return a valid token, refreshing via client_credentials if expired."""
        token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        issued_raw = os.getenv("SHOPIFY_TOKEN_ISSUED_AT", "")
        try:
            issued = datetime.fromisoformat(issued_raw.replace("Z", "+00:00"))
            if issued.tzinfo is None:
                issued = issued.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - issued).total_seconds() / 3600
        except (ValueError, TypeError):
            age_hours = 999
        if not token or age_hours >= 23:
            if self.client_id and self.client_secret:
                token = self._fetch_token()
            elif not token:
                raise ValueError("SHOPIFY_ACCESS_TOKEN must be set (or client creds).")
        return token

    # ── blog helpers ────────────────────────────────────────────────
    def get_blogs(self) -> Dict[str, str]:
        if self._blogs_cache is not None:
            return self._blogs_cache
        r = self.session.get(f"{self.base_url}/blogs.json")
        r.raise_for_status()
        blogs = {b["title"].lower(): str(b["id"]) for b in r.json().get("blogs", [])}
        self._blogs_cache = blogs
        return blogs

    def resolve_blog_id(self, name_or_id: str) -> str:
        env_id = os.getenv("SHOPIFY_BLOG_ID", "").strip()
        if env_id:
            return env_id
        if name_or_id and str(name_or_id).isdigit():
            return str(name_or_id)
        blogs = self.get_blogs()
        if name_or_id:
            key = name_or_id.lower().strip()
            if key in blogs:
                return blogs[key]
        if blogs:
            return next(iter(blogs.values()))
        raise ValueError(
            f"No blogs found in Shopify store '{self.store_handle}'. "
            "Set SHOPIFY_BLOG_ID in your .env."
        )

    # ── draft parsing (identical format to the other publishers) ─────
    def parse_draft_file(self, file_path: str) -> Dict:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Draft file not found: {file_path}")
        content = path.read_text(encoding="utf-8")

        h1 = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title = h1.group(1).strip() if h1 else ""

        def field(name: str) -> str:
            m = re.search(rf"\*\*{name}\*\*:\s*(.+?)(?:\n|$)", content, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        meta_title = field("Meta Title")
        meta_description = field("Meta Description")
        target_keyword = field("Target Keyword")
        secondary_keywords = field("Secondary Keywords")
        category = field("Category")
        tags = field("Tags")

        slug_m = re.search(
            r"\*\*URL Slug\*\*:\s*/?(?:blog(?:s)?/)?([^\s/]+)/?", content, re.IGNORECASE
        )
        if slug_m:
            slug = slug_m.group(1).strip()
        else:
            slug = re.sub(r"[\s_]+", "-", re.sub(r"[^\w\s-]", "", title.lower()))

        body = re.sub(r"^#\s+.+\n", "", content, count=1)
        for pat in [
            r"\*\*Meta Title\*\*:.+\n?", r"\*\*Meta Description\*\*:.+\n?",
            r"\*\*Target Keyword\*\*:.+\n?", r"\*\*Secondary Keywords\*\*:.+\n?",
            r"\*\*URL Slug\*\*:.+\n?", r"\*\*Category\*\*:.+\n?", r"\*\*Tags\*\*:.+\n?",
            r"\*\*Internal Links\*\*:.+\n?", r"\*\*External Links\*\*:.+\n?",
            r"\*\*Word Count\*\*:.+\n?", r"\*\*Awareness Stage\*\*:.+\n?",
            r"\*\*Cluster\*\*:.+\n?",
        ]:
            body = re.sub(pat, "", body, flags=re.IGNORECASE)
        body = re.sub(r"^[\s\-]*\n", "", body).strip()

        return {
            "title": title,
            "meta_title": meta_title or title,
            "meta_description": meta_description,
            "target_keyword": target_keyword,
            "secondary_keywords": secondary_keywords,
            "slug": slug,
            "category": category,
            "tags": tags,
            "content": body,
            "raw": content,
        }

    def markdown_to_html(self, md_content: str) -> str:
        try:
            import markdown
            md = markdown.Markdown(extensions=["extra", "nl2br", "sane_lists"])
            return md.convert(md_content)
        except ImportError:
            html = md_content
            html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
            html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
            html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)
            html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
            html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)
            html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', html)
            html = re.sub(r"^- (.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)
            parts = []
            for p in html.split("\n\n"):
                p = p.strip()
                parts.append(f"<p>{p}</p>" if p and not p.startswith("<") else p)
            return "\n\n".join(parts)

    # ── pre-publish gate ────────────────────────────────────────────
    def run_publish_gate(self, draft: Dict):
        """Run the ship-ready eval gate. Returns (scorecard, gate_result)."""
        from .eval.eval_runner import evaluate_draft
        # Feed the gate the full raw draft (it parses its own metadata).
        return evaluate_draft(draft["raw"], step="publish", persist=True)

    # ── article creation ────────────────────────────────────────────
    def create_article(
        self,
        blog_id: str,
        title: str,
        body_html: str,
        summary_html: str = "",
        handle: str = "",
        tags: Optional[List[str]] = None,
        author: str = "",
        published: bool = False,
        meta_title: str = "",
        meta_description: str = "",
    ) -> Dict:
        article: Dict = {"title": title, "body_html": body_html, "published": published}
        if summary_html:
            article["summary_html"] = summary_html
        if handle:
            article["handle"] = handle
        if tags:
            article["tags"] = ",".join(tags)
        if author:
            article["author"] = author
        # Shopify stores SEO title/description as global metafields.
        metafields = []
        if meta_title:
            metafields.append({"key": "title_tag", "namespace": "global",
                               "value": meta_title, "type": "single_line_text_field"})
        if meta_description:
            metafields.append({"key": "description_tag", "namespace": "global",
                               "value": meta_description, "type": "single_line_text_field"})
        if metafields:
            article["metafields"] = metafields

        url = f"{self.base_url}/blogs/{blog_id}/articles.json"
        r = self.session.post(url, json={"article": article})
        if r.status_code == 401 and self.client_id and self.client_secret:
            # Token rejected mid-session — refresh once and retry.
            self.access_token = self._fetch_token()
            self.session.headers["X-Shopify-Access-Token"] = self.access_token
            r = self.session.post(url, json={"article": article})
        r.raise_for_status()
        data = r.json()
        return data.get("article", data)

    # ── main entry point ────────────────────────────────────────────
    def publish_draft(self, file_path: str, allow_below_gate: bool = False) -> Dict:
        draft = self.parse_draft_file(file_path)

        # GATE — block before anything reaches the store.
        scorecard, gate_result = self.run_publish_gate(draft)
        if (gate_result.should_block or not gate_result.passed) and not allow_below_gate:
            raise PublishBlocked(
                "Pre-publish eval gate blocked this draft:\n"
                + gate_result.summary(),
                gate=gate_result, scorecard=scorecard,
            )

        html = self.markdown_to_html(draft["content"])
        word_count = len(draft["content"].split())
        blog_id = self.resolve_blog_id(draft["category"] or "")
        tags = [t.strip() for t in draft["tags"].split(",") if t.strip()] if draft["tags"] else []

        article = self.create_article(
            blog_id=blog_id,
            title=draft["title"],
            body_html=html,
            summary_html=draft["meta_description"],
            handle=draft["slug"],
            tags=tags,
            published=False,
            meta_title=draft["meta_title"],
            meta_description=draft["meta_description"],
        )
        article_id = str(article.get("id", ""))
        edit_url = (
            f"https://{self.store_handle}.myshopify.com/admin/articles/{article_id}"
        )

        # Record to publish ledger (best-effort).
        try:
            from .ledger import record_publish
            secondary = (
                [k.strip() for k in draft["secondary_keywords"].split(",") if k.strip()]
                if draft.get("secondary_keywords") else []
            )
            public_base = os.getenv("SHOPIFY_PUBLIC_URL_BASE", "").rstrip("/")
            blog_handle = os.getenv("SHOPIFY_PUBLIC_BLOG_HANDLE", "").strip("/")
            if public_base and blog_handle:
                view_url = f"{public_base}/blogs/{blog_handle}/{draft['slug']}"
            elif public_base:
                view_url = f"{public_base}/blogs/{draft['slug']}"
            else:
                view_url = ""
            record_publish(
                slug=draft["slug"],
                target_keyword=draft["target_keyword"],
                platform="shopify",
                post_id=article_id,
                source_file=file_path,
                url=view_url,
                secondary_keywords=secondary,
                cluster=draft.get("category", ""),
            )
        except Exception as e:
            print(f"⚠ Ledger write failed (publish succeeded): {e}")

        return {
            "article_id": article_id,
            "blog_id": blog_id,
            "edit_url": edit_url,
            "title": draft["title"],
            "slug": draft["slug"],
            "word_count": word_count,
            "tags": tags,
            "gate": {"total": scorecard.total, "band": scorecard.band},
            "meta": {
                "title": draft["meta_title"],
                "description": draft["meta_description"],
                "focus_keyphrase": draft["target_keyword"],
            },
        }


def main():
    import sys
    import argparse
    from dotenv import load_dotenv

    env_path = Path(__file__).parent.parent / "config" / ".env"
    if not env_path.exists():
        env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    ap = argparse.ArgumentParser(description="Publish a draft to Shopify")
    ap.add_argument("file_path", help="Path to the markdown draft file")
    ap.add_argument("--allow-below-gate", action="store_true",
                    help="Override the pre-publish eval gate (logged)")
    args = ap.parse_args()

    try:
        publisher = ShopifyPublisher()
        result = publisher.publish_draft(args.file_path, allow_below_gate=args.allow_below_gate)
        print(f"\n✓ Parsed draft + passed gate ({result['gate']['total']}/100, {result['gate']['band']})")
        print(f"✓ Converted {result['word_count']:,} words to HTML")
        print(f"✓ Created Shopify article (ID: {result['article_id']}) — unpublished")
        if result["tags"]:
            print(f"✓ Tags: {', '.join(result['tags'])}")
        print(f"\nArticle created as unpublished in Shopify.")
        print(f"Edit URL: {result['edit_url']}")
    except PublishBlocked as e:
        print(f"\n⛔ BLOCKED — not published.\n{e}")
        sys.exit(2)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"Shopify API Error: {e}")
        if getattr(e, "response", None) is not None:
            print(f"Response: {e.response.text}")
        sys.exit(1)
    except ValueError as e:
        print(f"Configuration Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
