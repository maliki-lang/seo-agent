"""
Shopline Publisher Module

Publishes draft articles to Shopline as hidden blog posts via the REST API.
Drop-in replacement for wordpress_publisher.py — same draft file format,
same parse_draft_file / markdown_to_html interface.
"""

import os
import re
import requests
from typing import Dict, Optional, List
from pathlib import Path


class ShoplinePublisher:
    """Shopline REST API client for publishing blog articles"""

    def __init__(
        self,
        store_handle: Optional[str] = None,
        access_token: Optional[str] = None,
        api_version: Optional[str] = None,
    ):
        self.store_handle = store_handle or os.getenv('SHOPLINE_STORE_HANDLE', 'mysunnystep')
        self.access_token = access_token or os.getenv('SHOPLINE_ACCESS_TOKEN')
        self.api_version = api_version or os.getenv('SHOPLINE_API_VERSION', 'v20250301')

        if not self.access_token:
            raise ValueError("SHOPLINE_ACCESS_TOKEN must be set")

        self.base_url = (
            f"https://{self.store_handle}.myshopline.com"
            f"/admin/openapi/{self.api_version}"
        )
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json; charset=utf-8',
            'User-Agent': 'SEOMachine/1.0 (Shopline Content Publisher)',
        })

        self._blogs_cache: Optional[Dict[str, str]] = None  # title.lower() -> id

    # ─────────────────────────────────────────────
    # Blog collection helpers
    # ─────────────────────────────────────────────

    def get_blogs(self) -> Dict[str, str]:
        """Return all blog collections as {title.lower(): id}"""
        if self._blogs_cache is not None:
            return self._blogs_cache

        r = self.session.get(f"{self.base_url}/store/blogs.json")
        r.raise_for_status()
        blogs = {}
        for blog in r.json().get('blogs', []):
            blogs[blog['title'].lower()] = str(blog['id'])
        self._blogs_cache = blogs
        return blogs

    def resolve_blog_id(self, name_or_id: str) -> str:
        """
        Resolve a blog collection name or ID to a string ID.
        Resolution order:
          1. SHOPLINE_BLOG_COLLECTION_ID env var (if set)
          2. Numeric ID passed directly
          3. Name match against store blogs
          4. First blog in the store (fallback)
        """
        env_id = os.getenv('SHOPLINE_BLOG_COLLECTION_ID', '').strip()
        if env_id:
            return env_id

        if name_or_id and str(name_or_id).isdigit():
            return str(name_or_id)

        blogs = self.get_blogs()

        if name_or_id:
            name_lower = name_or_id.lower().strip()
            if name_lower in blogs:
                return blogs[name_lower]

        if blogs:
            return next(iter(blogs.values()))

        raise ValueError(
            f"No blog collections found in Shopline store '{self.store_handle}'. "
            "Set SHOPLINE_BLOG_COLLECTION_ID in your .env."
        )

    # ─────────────────────────────────────────────
    # Draft file parsing (same format as wordpress_publisher)
    # ─────────────────────────────────────────────

    def parse_draft_file(self, file_path: str) -> Dict:
        """
        Parse a markdown draft file and extract metadata and content.
        Identical format to wordpress_publisher.parse_draft_file.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Draft file not found: {file_path}")

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Extract H1 title
        h1_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        title = h1_match.group(1).strip() if h1_match else ''

        def extract_field(field_name: str) -> str:
            pattern = rf'\*\*{field_name}\*\*:\s*(.+?)(?:\n|$)'
            match = re.search(pattern, content, re.IGNORECASE)
            return match.group(1).strip() if match else ''

        meta_title = extract_field('Meta Title')
        meta_description = extract_field('Meta Description')
        target_keyword = extract_field('Target Keyword')
        secondary_keywords = extract_field('Secondary Keywords')
        category = extract_field('Category')
        tags = extract_field('Tags')

        # Extract URL slug
        slug = ''
        slug_match = re.search(
            r'\*\*URL Slug\*\*:\s*/?(?:blog(?:s)?/)?([^\s/]+)/?',
            content, re.IGNORECASE
        )
        if slug_match:
            slug = slug_match.group(1).strip()
        else:
            slug = re.sub(r'[^\w\s-]', '', title.lower())
            slug = re.sub(r'[\s_]+', '-', slug)

        # Strip metadata lines from body
        body_content = content
        body_content = re.sub(r'^#\s+.+\n', '', body_content, count=1)

        metadata_patterns = [
            r'\*\*Meta Title\*\*:.+\n?',
            r'\*\*Meta Description\*\*:.+\n?',
            r'\*\*Target Keyword\*\*:.+\n?',
            r'\*\*Secondary Keywords\*\*:.+\n?',
            r'\*\*URL Slug\*\*:.+\n?',
            r'\*\*Category\*\*:.+\n?',
            r'\*\*Tags\*\*:.+\n?',
            r'\*\*Internal Links\*\*:.+\n?',
            r'\*\*External Links\*\*:.+\n?',
            r'\*\*Word Count\*\*:.+\n?',
        ]
        for pattern in metadata_patterns:
            body_content = re.sub(pattern, '', body_content, flags=re.IGNORECASE)

        body_content = re.sub(r'^[\s\-]*\n', '', body_content)
        body_content = body_content.strip()

        return {
            'title': title,
            'meta_title': meta_title or title,
            'meta_description': meta_description,
            'target_keyword': target_keyword,
            'secondary_keywords': secondary_keywords,
            'slug': slug,
            'category': category,  # used to resolve blog collection
            'tags': tags,
            'content': body_content,
        }

    def markdown_to_html(self, markdown_content: str) -> str:
        """Convert markdown to HTML for Shopline's rich-text body field."""
        try:
            import markdown
            md = markdown.Markdown(extensions=['extra', 'nl2br', 'sane_lists'])
            return md.convert(markdown_content)
        except ImportError:
            # Fallback: basic markdown conversion
            html = markdown_content
            html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
            html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
            html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
            html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
            html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)
            html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)
            html = re.sub(r'^- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
            paragraphs = html.split('\n\n')
            wrapped = []
            for p in paragraphs:
                p = p.strip()
                if p and not p.startswith('<'):
                    wrapped.append(f'<p>{p}</p>')
                else:
                    wrapped.append(p)
            html = '\n\n'.join(wrapped)
            return html

    # ─────────────────────────────────────────────
    # Article creation
    # ─────────────────────────────────────────────

    def create_article(
        self,
        blog_collection_id: str,
        title: str,
        content: str,
        digest: str = '',
        handle: str = '',
        tags: Optional[List[str]] = None,
        author: str = '',
        published: bool = False,
        meta_title: str = '',
        meta_description: str = '',
    ) -> Dict:
        """
        Create a blog article in Shopline.

        published=False → status "Hide" in the Shopline UI (safe draft equivalent).
        published=True  → immediately live on the blog.
        """
        article_data: Dict = {
            'title': title,
            'content': content,
            'published': published,
        }
        if digest:
            # Shopline Overview field has a 1000-char limit shown in the UI
            article_data['digest'] = digest[:1000]
        if handle:
            article_data['handle'] = handle
        if tags:
            article_data['tags'] = ','.join(tags)
        if author:
            article_data['author'] = author
        # SEO fields — Shopline accepts these on article create/update
        if meta_title:
            article_data['meta_title'] = meta_title
        if meta_description:
            article_data['meta_description'] = meta_description

        r = self.session.post(
            f"{self.base_url}/store/blogs/{blog_collection_id}/articles.json",
            json={'article': article_data},
        )
        r.raise_for_status()
        data = r.json()
        return data.get('article', data)

    # ─────────────────────────────────────────────
    # Main entry point
    # ─────────────────────────────────────────────

    def publish_draft(self, file_path: str) -> Dict:
        """
        Publish a markdown draft file to Shopline as a hidden article.

        Args:
            file_path: Path to the markdown draft file

        Returns:
            Dict with article_id, blog_collection_id, edit_url, title,
            slug, word_count, tags, meta
        """
        draft = self.parse_draft_file(file_path)
        html_content = self.markdown_to_html(draft['content'])
        word_count = len(draft['content'].split())

        blog_collection_id = self.resolve_blog_id(draft['category'] or '')

        tags = []
        if draft['tags']:
            tags = [t.strip() for t in draft['tags'].split(',') if t.strip()]

        article = self.create_article(
            blog_collection_id=blog_collection_id,
            title=draft['title'],
            content=html_content,
            digest=draft['meta_description'],
            handle=draft['slug'],
            tags=tags,
            published=False,
            meta_title=draft['meta_title'],
            meta_description=draft['meta_description'],
        )

        article_id = str(article.get('id', ''))
        # Shopline admin URL — navigate to Online Store > Blog posts to find the article
        edit_url = (
            f"https://{self.store_handle}.myshopline.com/admin"
            f"/online/blogs/{blog_collection_id}/articles/{article_id}/edit"
        )

        # Record to publish ledger — best-effort, never blocks the publish
        try:
            from .ledger import record_publish
            secondary = (
                [k.strip() for k in draft['secondary_keywords'].split(',') if k.strip()]
                if draft.get('secondary_keywords') else []
            )
            public_base = os.getenv('SHOPLINE_PUBLIC_URL_BASE', '').rstrip('/')
            blog_handle = os.getenv('SHOPLINE_PUBLIC_BLOG_HANDLE', '').strip('/')
            if public_base and blog_handle:
                view_url = f"{public_base}/blogs/{blog_handle}/{draft['slug']}"
            elif public_base:
                view_url = f"{public_base}/blogs/{draft['slug']}"
            else:
                view_url = ''
            record_publish(
                slug=draft['slug'],
                target_keyword=draft['target_keyword'],
                platform='shopline',
                post_id=article_id,
                source_file=file_path,
                url=view_url,
                secondary_keywords=secondary,
                cluster=draft.get('category', ''),
            )
        except Exception as e:
            print(f"⚠ Ledger write failed (publish succeeded): {e}")

        return {
            'article_id': article_id,
            'blog_collection_id': blog_collection_id,
            'edit_url': edit_url,
            'title': draft['title'],
            'slug': draft['slug'],
            'word_count': word_count,
            'tags': tags,
            'meta': {
                'title': draft['meta_title'],
                'description': draft['meta_description'],
                'focus_keyphrase': draft['target_keyword'],
            },
        }


def main():
    """CLI entry point"""
    import sys
    import argparse
    from dotenv import load_dotenv

    # Try the seomachine config path first, then repo root
    env_path = Path(__file__).parent.parent / 'config' / '.env'
    if not env_path.exists():
        env_path = Path(__file__).parent.parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)

    parser = argparse.ArgumentParser(description='Publish a draft to Shopline')
    parser.add_argument('file_path', help='Path to the markdown draft file')
    args = parser.parse_args()

    try:
        publisher = ShoplinePublisher()
        result = publisher.publish_draft(args.file_path)

        print(f"\n✓ Parsed draft file")
        print(f"✓ Converted {result['word_count']:,} words to HTML")
        print(f"✓ Created Shopline article (ID: {result['article_id']}) — status: Hidden")
        if result['tags']:
            print(f"✓ Tags: {', '.join(result['tags'])}")
        print(f"✓ SEO meta title + description set")
        print(f"\nArticle created as Hidden in Shopline.")
        print(f"Edit URL: {result['edit_url']}")

    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"Shopline API Error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
        sys.exit(1)
    except ValueError as e:
        print(f"Configuration Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
