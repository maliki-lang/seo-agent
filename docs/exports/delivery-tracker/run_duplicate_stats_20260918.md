# Collection duplicate statistics — 2026-09-18T03:21:44+00:00

## Controlled failure run
- run_id: `f7e255c9-f49b-4da0-83aa-7d77e8f72232`
- simulate-failure: `serper:error`
- alerts sent: `collector_failure:serper:TrackingError`, `collector_failure:ai_visibility:RateLimitError` (external_reference=webhook-ok)

## Rerun cycle (duplicate stats)
- run_id: `553f9eca-cc9a-47ff-afa1-2292ddf353e8`
- status: `partial`
- failed_collectors: `{"ai_visibility": "RateLimitError"}`

| source | status | rows | inserted | updated | unchanged | duplicate_or_existing |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| gsc | succeeded | 531 | 0 | 34 | 497 | 531 |
| ga4 | succeeded | 244 | 0 | 0 | 244 | 244 |
| serper | succeeded | 55 | 55 | 0 | 0 | 0 |
| ai_visibility | failed | None | None | None | None | 0 |

Notes:
- `unchanged` = natural-key upsert found identical existing row (true duplicate skip).
- `updated` = same key, payload changed.
- `ai_visibility` still blocked by OpenAI credit balance (`RateLimitError` / insufficient_quota).
