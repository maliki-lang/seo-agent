# Metric definitions

Timezone for reporting dates: `Asia/Singapore`. Storage timestamps: UTC.

| Metric | Grain | Source | Formula / definition | Freshness | Caveats |
|---|---|---|---|---|---|
| `gsc_clicks` | period | GSC | `SUM(clicks)` | ~3 days lag | Country filter when configured |
| `gsc_impressions` | period | GSC | `SUM(impressions)` | ~3 days lag | |
| `gsc_weighted_ctr` | period | GSC | clicks / impressions | | Never average row CTR |
| `gsc_weighted_avg_position` | period | GSC | impression-weighted mean position | | |
| `gsc_branded_clicks` | period | GSC | clicks where `is_brand` | | Brand rules `brand_rules_v1` |
| `gsc_nonbranded_clicks` | period | GSC | clicks where not brand | | |
| `ga4_organic_sessions` | period | GA4 | sessions with `channel_class=organic_search` | ~2 days lag | |
| `ga4_organic_engaged_sessions` | period | GA4 | engaged sessions, organic | | |
| `ga4_organic_purchases` | period | GA4 | purchases, organic | | |
| `ga4_organic_revenue` | period | GA4 | sum revenue, organic | | Decimal text in hashes |
| `ga4_organic_engagement_rate` | period | GA4 | engaged / sessions | | Unavailable if sessions=0 |
| `ga4_organic_conversion_rate` | period | GA4 | purchases / sessions | | Unavailable if sessions=0 |
| `serp_visibility_top3_rate` | latest SERP day | Serper | share of tracked keywords with position 1–3 | ~30h | Position `0` = absent |
| `serp_visibility_top10_rate` | latest SERP day | Serper | positions 1–10 | | |
| `serp_visibility_absent_rate` | latest SERP day | Serper | position 0 or >20 | | |
| `geo_mention_rate` | question×engine | AI | share of questions with ≥2/3 mentions | ~30h | Incomplete sets excluded |
| `geo_citation_rate` | question×engine | AI | share with ≥2/3 Sunnystep citations | | Host must be sunnystep.com |
| `geo_combined_mention_rate` | question | AI | across engines using stable_rates | | |
| Opportunity `priority_score` | action | derived | Impact × Confidence ÷ Effort | | SEO/GEO impacts normalized separately |

Missing values must display as unavailable, not zero, in reports.
