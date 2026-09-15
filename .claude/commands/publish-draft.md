# Publish Draft to Shopify

Publishes a draft article to Shopify as an unpublished (hidden) blog post, with
SEO metadata auto-populated. The site migrated from Shopline to Shopify, so
Shopify is the live target. A **pre-publish eval gate** runs first: a draft that
fails the YMYL compliance gate or scores below ship-ready (85) is blocked and
never reaches the store.

## Usage
`/publish-draft [filename]`

### Example
```
/publish-draft drafts/best-walking-shoes-2026-04-16.md
```

## What This Command Does

1. **Parses the draft file** — extracts metadata and body content
2. **Runs the pre-publish eval gate** — deterministic scorecard + YMYL compliance
   at the `publish` bar (must be ship-ready, no hard fail). Blocks otherwise.
3. **Converts Markdown to HTML** — formats content for Shopify's `body_html`
4. **Resolves the blog** — uses the `Category` field or `SHOPIFY_BLOG_ID` env var
5. **Creates the Shopify article** — unpublished (safe draft; never auto-publishes)
6. **Sets SEO fields** — meta title + description via Shopify `global` metafields
7. **Records to the publish ledger** — captures a T-0 GSC/GA4 baseline for tracking
8. **Returns edit URL** — direct link to review in Shopify admin

## Metadata Mapping

| Draft Field | Shopify Field |
|---|---|
| H1 Title | Article title |
| Meta Title | `global.title_tag` metafield (SEO title) |
| Meta Description | `global.description_tag` metafield + `summary_html` |
| URL Slug | Article handle (URL path) |
| Category | Blog (resolves which blog to post to) |
| Tags | Article tags |
| Content | Article body (`body_html`) |

## Required Environment Variables

These live in `data_sources/config/.env` (shared with the inventory-and-sales
agent). Auth uses a custom-app `client_credentials` grant that auto-refreshes the
24h token — you do not paste a static token.
```
SHOPIFY_SHOP=gosunnystep.myshopify.com
SHOPIFY_CLIENT_ID=...            # custom-app client id
SHOPIFY_CLIENT_SECRET=...        # custom-app client secret
SHOPIFY_API_VERSION=2026-01
SHOPIFY_ACCESS_TOKEN=...         # cached; auto-refreshed
SHOPIFY_TOKEN_ISSUED_AT=...      # managed automatically

# Target blog — Sunnystep has two: "news" (93845225532) and
# "sunnystep blog" (94480597052). Default is the first ("news"); set this or the
# draft's Category field to choose.
SHOPIFY_BLOG_ID=94480597052
# For the ledger's public view URL (rank tracking)
SHOPIFY_PUBLIC_URL_BASE=https://sunnystep.com
SHOPIFY_PUBLIC_BLOG_HANDLE=blogs
```

> The custom app must have **`write_content`** scope to create articles. Read
> access is confirmed working; the first real publish confirms write scope (a
> 403 means add the scope to the app).

## Process

### Step 1: Validate + show what will publish
Confirm the draft exists, parse metadata, display extracted fields.

### Step 2: Publish to Shopify (gate runs automatically)
```bash
cd /path/to/seomachine
python3 data_sources/modules/shopify_publisher.py "$FILE_PATH"
```
- The publisher runs the gate first. If blocked, it exits `2` and prints the
  reasons — **fix the draft and re-run; do not bypass.**
- Only use `--allow-below-gate` with explicit human sign-off (it is logged).

### Step 3: Confirm success
Display the Shopify edit URL and the gate score so the user can review and
publish from the admin.

## Notes

- Articles are always created **unpublished** — never auto-published.
- The pre-publish gate is the enforced barrier between generation and a live
  page. On the foot-condition (YMYL) cluster it is zero-tolerance: uncited
  health claims, a missing medical disclaimer, or forbidden absolutes ("cures",
  "FDA-approved") block the publish.
- Cover image is not uploaded via API — add it in the Shopify UI after publishing.
- The legacy `shopline_publisher.py` is retained for reference only.

## Troubleshooting

### "⛔ BLOCKED — not published"
The eval gate stopped the draft. Read the printed reasons (lowest dimensions /
compliance hard-fails), fix them in the draft, and re-run.

### "SHOPIFY_ACCESS_TOKEN must be set"
Add the Admin API token to `data_sources/config/.env` (Shopify admin > Settings >
Apps and sales channels > Develop apps > your app > Admin API access token).

### "No blogs found"
Set `SHOPIFY_BLOG_ID` directly in `.env`. Find it in the admin URL when viewing a
blog's posts.

### "401 Unauthorized" / "403"
Regenerate the token and ensure the app has `write_content` (and `read_content`) scope.

### "422 Unprocessable Entity"
Usually a field validation error — commonly a duplicate handle (slug).
