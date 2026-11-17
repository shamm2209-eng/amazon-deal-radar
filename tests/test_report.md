# Deal Radar — Test Report

**Date:** 2026-11-17
**Environment:** Python 3.12.10, Windows 11, pytest 8.x
**Result: ✅ 61 passed, 0 failed, 0 errors** (`python -m pytest -v`)

## Test suites

| Suite | Tests | What it covers |
|---|---|---|
| `tests/test_amazon.py` | 10 | Search-card parser (used offers, prices with commas, skipping sponsored/no-used cards, result counts, safe empty pages) and product-page parser (NEW price, list price, used price, condition, seller, dispatch origin, shipping, used-buy-box variant, non-product pages) — all against **real captured amazon.eg HTML** in `tests/fixtures/`. |
| `tests/test_deals.py` | 18 | Savings formula ((New−Used)/New×100), exact-threshold edges (50%), tier classification (alert/high/exceptional), verification statuses (verified / potential_price_error / unverified / incomplete), "never label unverified as a bargain", suspicious absolute-price flagging (the "EGP 800 iPhone" rule), list-price fallback reference. |
| `tests/test_database.py` | 10 | Offer upsert/dedup, alert gating (no repeats for unchanged offers, re-alert on ≥5% price drop or ≥5-point savings improvement, 24h cooldown), per-ASIN isolation, price history, run lifecycle. |
| `tests/test_telegram.py` | 9 | Message contains all required fields (title, used price, new price, savings, condition, seller, shipping, ASIN, direct link, discovery time, verification status), HTML escaping, exceptional-deal warning, sendPhoto with fallback to text, dry-run sends nothing, unconfigured bot skips silently. All HTTP mocked — nothing is sent during tests. |
| `tests/test_http_client.py` | 10 | robots.txt compliance against the **real amazon.eg robots.txt** (offer-listing disallowed; search/product pages allowed; unreachable robots.txt fails-safe), politeness delays, block/challenge detection (503, 429, CAPTCHA markers). |
| `tests/test_pipeline.py` | 4 | Full simulated end-to-end runs: discovery → verification → comparison → alert (including the EGP-800-vs-EGP-30,000 scenario → exceptional + potential price error), duplicate suppression on second run, re-alert on price drop, blocked-run health message. |

## Live integration check (same day)

A real run against live amazon.eg (dry-run mode, no Telegram):

```
robots.txt loaded (6,711 bytes) ✔
Playwright Chromium fetches: 1 category page + 4 product pages, 0 blocks
candidates: 24   compared: 4   alerts: 3 (across two runs)
results included: 51.2% verified deal, 52.9% verified deal,
                  95.9% potential price error (flagged, not confirmed)
second run with identical data: 0 duplicate alerts ✔
```

## Notes

* All fixtures in `tests/fixtures/` are genuine page fragments captured from
  amazon.eg on 2026-11-17 (collapsed for readability), so parser tests fail
  loudly if Amazon changes its markup.
* `tests/fixtures/real_robots.txt` is the live robots.txt; if Amazon changes
  its rules, `test_offer_listing_is_disallowed` will fail and flag the drift.
