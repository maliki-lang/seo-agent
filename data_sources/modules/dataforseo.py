"""
SERP Data Integration via Serper.dev

Fetches organic results, competitor rankings, and related questions.
Sign up at serper.dev — 2,500 free queries, no credit card needed.

Set SERPER_API_KEY in data_sources/config/.env.
"""

import os
import requests
from typing import Dict, List, Optional, Any


class DataForSEO:
    """
    Serper.dev-backed SERP client.
    Named DataForSEO for drop-in compatibility with existing code.
    """

    def __init__(self, api_key: Optional[str] = None, **_kwargs):
        self.api_key = api_key or os.getenv('SERPER_API_KEY')
        if not self.api_key:
            raise ValueError("SERPER_API_KEY must be set in your .env")

        self.session = requests.Session()
        self.session.headers.update({
            'X-API-KEY': self.api_key,
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0',
        })
        self.base_url = 'https://google.serper.dev'

    def _search(self, keyword: str, gl: str = 'sg', hl: str = 'en', num: int = 10) -> Dict:
        r = self.session.post(
            f'{self.base_url}/search',
            json={'q': keyword, 'gl': gl, 'hl': hl, 'num': num},
        )
        r.raise_for_status()
        return r.json()

    def get_serp_data(
        self, keyword: str, location_code: int = 2702, limit: int = 10
    ) -> Dict[str, Any]:
        """
        Get organic SERP results for a keyword.
        Note: search_volume, cpc, competition are not available via Serper.
        """
        data = self._search(keyword, num=min(limit, 100))

        organic = []
        for item in data.get('organic', []):
            link = item.get('link', '')
            domain = link.split('/')[2] if link.startswith('http') else ''
            organic.append({
                'position': item.get('position'),
                'url': link,
                'domain': domain,
                'title': item.get('title'),
                'description': item.get('snippet'),
                'breadcrumb': None,
            })

        return {
            'keyword': keyword,
            'search_volume': None,
            'cpc': None,
            'competition': None,
            'organic_results': organic,
            'features': [],
            'total_results': len(organic),
        }

    def get_rankings(
        self,
        domain: str,
        keywords: List[str],
        location_code: int = 2702,
        language_code: str = 'en',
    ) -> List[Dict[str, Any]]:
        """Check if domain appears in top results for each keyword."""
        results = []
        for keyword in keywords:
            data = self._search(keyword, num=20)
            position = None
            url = None
            for item in data.get('organic', []):
                link = item.get('link', '')
                item_domain = link.split('/')[2] if link.startswith('http') else ''
                if domain in item_domain:
                    position = item.get('position')
                    url = link
                    break
            results.append({
                'keyword': keyword,
                'domain': domain,
                'position': position,
                'url': url,
                'ranking': position is not None,
                'search_volume': None,
                'cpc': None,
            })
        return results

    def get_keyword_ideas(
        self, seed_keyword: str, location_code: int = 2702, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Return related searches. No volume/CPC data available."""
        data = self._search(seed_keyword)
        ideas = []
        for item in data.get('relatedSearches', []):
            ideas.append({
                'keyword': item.get('query'),
                'search_volume': None,
                'cpc': None,
                'competition': None,
                'avg_position': None,
            })
        return ideas[:limit]

    def get_questions(
        self, keyword: str, location_code: int = 2702, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Return People Also Ask questions."""
        data = self._search(keyword)
        questions = []
        for item in data.get('peopleAlsoAsk', []):
            questions.append({
                'question': item.get('question'),
                'search_volume': None,
                'cpc': None,
            })
        return questions[:limit]

    def analyze_competitor(
        self,
        competitor_domain: str,
        keywords: List[str],
        your_domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compare your domain vs competitor across keywords."""
        comparison = []
        for keyword in keywords:
            data = self._search(keyword, num=20)
            competitor_pos = None
            your_pos = None
            for item in data.get('organic', []):
                link = item.get('link', '')
                item_domain = link.split('/')[2] if link.startswith('http') else ''
                pos = item.get('position')
                if competitor_domain in item_domain:
                    competitor_pos = pos
                if your_domain and your_domain in item_domain:
                    your_pos = pos
            gap = None
            if competitor_pos and your_pos:
                gap = your_pos - competitor_pos
            elif competitor_pos and not your_pos:
                gap = 'Not ranking'
            comparison.append({
                'keyword': keyword,
                'competitor_position': competitor_pos,
                'your_position': your_pos,
                'gap': gap,
                'opportunity': 'high' if competitor_pos and not your_pos
                               else 'medium' if isinstance(gap, (int, float)) and gap > 10
                               else 'low',
            })
        return {'competitor': competitor_domain, 'your_domain': your_domain, 'comparison': comparison}

    def get_domain_metrics(self, domain: str) -> Dict[str, Any]:
        """Not available via Serper."""
        return {}

    def check_ranking_history(
        self, domain: str, keyword: str, months_back: int = 3
    ) -> List[Dict[str, Any]]:
        """Not available via Serper."""
        return []


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv('data_sources/config/.env')

    client = DataForSEO()

    print("Top results for 'most comfortable walking shoes Singapore':")
    serp = client.get_serp_data('most comfortable walking shoes Singapore', limit=10)
    for r in serp['organic_results']:
        print(f"  {r['position']}. {r['domain']} — {r['title']}")

    print("\nPeople Also Ask:")
    questions = client.get_questions('comfortable walking shoes')
    for q in questions[:5]:
        print(f"  - {q['question']}")

    print("\nChecking sunnystep.com rankings:")
    rankings = client.get_rankings('sunnystep.com', ['walking shoes singapore', 'comfortable shoes singapore'])
    for r in rankings:
        pos = r['position'] or 'Not ranking'
        print(f"  '{r['keyword']}' → {pos}")
