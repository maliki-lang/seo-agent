"""Deprecated import path. The live implementation is SerperClient."""

from __future__ import annotations

from data_sources.modules.serper import SerperClient


class DataForSEO(SerperClient):
    """Compatibility alias for callers that still import DataForSEO."""


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv("data_sources/config/.env")
    client = DataForSEO()
    print("Top results for 'most comfortable walking shoes Singapore':")
    serp = client.get_serp_data("most comfortable walking shoes Singapore", limit=10)
    for row in serp["organic_results"]:
        print(f"  {row['position']}. {row['domain']} — {row['title']}")
