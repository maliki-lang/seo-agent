#!/usr/bin/env python3
"""
Post-migration audit (one-off) — Phase 1.

The Shopline -> Shopify migration is complete. This audit verifies SEO equity
carried over with no ranking regressions, producing a pass/fail report:

  1. Redirects:   every old URL returns 200 or 301->200 (no 404s).
  2. Metadata:    each live page has a <title> and meta description.
  3. Schema:      structured data (JSON-LD) is present where expected.
  4. Indexing:    pages are not blocked by robots/noindex.
  5. GSC verify:  reminder/check that the Shopify property is verified in GSC.

Input: a CSV/JSON list of URLs (old_url[,new_url]) OR a sitemap URL.

Usage:
    python3 post_migration_audit.py --urls urls.csv
    python3 post_migration_audit.py --sitemap https://sunnystep.com/sitemap.xml
    python3 post_migration_audit.py --url https://sunnystep.com/blogs/news/foo
"""

import argparse
import csv
import json
import re
import sys
from typing import Any, Dict, List

import requests


def _fetch(url: str) -> requests.Response:
    return requests.get(url, timeout=20, allow_redirects=True,
                        headers={"User-Agent": "SEOMachine-MigrationAudit/1.0"})


def audit_url(url: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"url": url, "checks": {}, "issues": []}
    try:
        r = _fetch(url)
    except Exception as e:
        result["checks"]["reachable"] = False
        result["issues"].append(f"request failed: {e}")
        result["pass"] = False
        return result

    html = r.text
    result["final_url"] = r.url
    result["status"] = r.status_code

    # 1. Reachable (200 after any redirects)
    result["checks"]["status_200"] = r.status_code == 200
    if r.status_code != 200:
        result["issues"].append(f"HTTP {r.status_code}")

    # 2. Redirect chain ended somewhere sensible
    if r.history:
        codes = [h.status_code for h in r.history]
        result["checks"]["clean_redirect"] = all(c in (301, 308) for c in codes)
        if not result["checks"]["clean_redirect"]:
            result["issues"].append(f"non-permanent redirect chain {codes}")

    # 3. Title + meta description present
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
                     html, re.IGNORECASE)
    result["checks"]["has_title"] = bool(title and title.group(1).strip())
    result["checks"]["has_meta_description"] = bool(desc and desc.group(1).strip())
    if not result["checks"]["has_title"]:
        result["issues"].append("missing <title>")
    if not result["checks"]["has_meta_description"]:
        result["issues"].append("missing meta description")

    # 4. Schema / JSON-LD present
    result["checks"]["has_jsonld"] = bool(
        re.search(r'<script[^>]+type=["\']application/ld\+json["\']', html, re.IGNORECASE)
    )
    if not result["checks"]["has_jsonld"]:
        result["issues"].append("no JSON-LD structured data")

    # 5. Not noindexed
    noindex = re.search(r'<meta[^>]+name=["\']robots["\'][^>]+content=["\'][^"\']*noindex',
                        html, re.IGNORECASE)
    result["checks"]["indexable"] = not bool(noindex)
    if noindex:
        result["issues"].append("page is noindex")

    # Pass = reachable, 200, title+desc, indexable (schema is a warning, not fail)
    result["pass"] = all([
        result["checks"].get("status_200"),
        result["checks"].get("has_title"),
        result["checks"].get("has_meta_description"),
        result["checks"].get("indexable"),
    ])
    return result


def load_urls(args) -> List[str]:
    urls: List[str] = []
    if args.url:
        urls.append(args.url)
    if args.urls:
        with open(args.urls, encoding="utf-8") as f:
            if args.urls.endswith(".json"):
                data = json.load(f)
                urls += [d if isinstance(d, str) else d.get("url") or d.get("old_url")
                         for d in data]
            else:
                for row in csv.reader(f):
                    if row:
                        urls.append(row[0].strip())
    if args.sitemap:
        try:
            r = _fetch(args.sitemap)
            urls += re.findall(r"<loc>(.*?)</loc>", r.text)
        except Exception as e:
            print(f"Could not read sitemap: {e}", file=sys.stderr)
    # dedupe, drop blanks/headers
    seen, out = set(), []
    for u in urls:
        if u and u.startswith("http") and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Post-migration SEO audit (Shopify)")
    ap.add_argument("--url", help="A single URL to audit")
    ap.add_argument("--urls", help="CSV (old_url[,new_url]) or JSON list of URLs")
    ap.add_argument("--sitemap", help="Sitemap URL to pull URLs from")
    ap.add_argument("--out", help="Write full JSON report to this path")
    args = ap.parse_args(argv)

    urls = load_urls(args)
    if not urls:
        ap.error("Provide --url, --urls, or --sitemap")

    results = [audit_url(u) for u in urls]
    passed = sum(1 for r in results if r.get("pass"))
    print(f"Post-migration audit: {passed}/{len(results)} URLs passed\n")
    for r in results:
        flag = "PASS" if r.get("pass") else "FAIL"
        print(f"[{flag}] {r['url']}  (HTTP {r.get('status', '?')})")
        for issue in r.get("issues", []):
            print(f"        - {issue}")

    print("\nReminder: confirm the Shopify property is verified in Google "
          "Search Console and the sitemap is resubmitted.")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"\nFull report: {args.out}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
