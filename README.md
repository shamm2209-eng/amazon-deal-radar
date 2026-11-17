# 🛰️ Deal Radar — Amazon Egypt Used-Price Hunter

A fully automated deal hunter for **amazon.eg**: it continuously scans Amazon's
**Resale / used offers**, compares each one with the **NEW price of the exact
same product**, and sends you a **Telegram alert** when something is unusually
cheap — for example a phone listed used for EGP 800 while the new one sells
for EGP 30,000.

You do not need to write code, and your computer does not need to stay on:
everything runs automatically on **GitHub Actions** (free).

---

## How it works

```
every 15 min (GitHub Actions cloud)
   │
   ▼
1. DISCOVER   ── scan Amazon Resale (used) category pages: electronics,
   │             home & kitchen, gaming, fashion, tools… no manual watchlist
   ▼
2. VERIFY     ── for each promising used offer, open the product page and
   │             read the live NEW price, condition, seller, shipping
   ▼
3. COMPARE    ── Savings % = ((New − Used) / New) × 100
   │             ≥50% → alert   ≥70% → high priority   ≥90% → exceptional
   ▼
4. ALERT      ── Telegram message with photo, prices, condition, seller,
                 shipping, ASIN, direct link, verification status
                 (no repeated notifications for unchanged offers)
```

**Honest data-source notes** (no pretending):

| Source | Status |
|---|---|
| Amazon Resale (used) category search pages | ✅ **Works** — this is where used offers are discoverable |
| Product pages (`/dp/ASIN`) | ✅ **Works** — live NEW price + used offer details |
| Full offer-listing page (every seller's used offers) | ❌ **Not scraped** — Amazon's robots.txt (`robots.txt`) explicitly disallows it. We respect that. |
| Plain HTTP scripts (requests) | ❌ Blocked by Amazon's anti-bot page — that's why the tool uses a **real headless Chromium browser**. It never solves CAPTCHAs: if Amazon serves a challenge, the run backs off, stops, and tells you. |
| What is covered | The used offers visible on Amazon Resale search + the used offer shown on each product page (typically the cheapest used offer, usually "Sold by: Amazon Resale"). It is not *guaranteed* to cover every third-party used offer on Amazon. |

---

## Setup guide (no coding required)

Follow these steps once. Time needed: ~15 minutes.

### Step 1 — Create your Telegram bot (3 minutes)

1. Open Telegram and search for **@BotFather** (blue verified checkmark).
2. Send `/newbot`.
3. Choose a display name, e.g. `Deal Radar`.
4. Choose a username ending in `bot`, e.g. `mydealradar123_bot`.
5. BotFather replies with a **token** like:
   `123456789:AAH9xQ2-...` — **copy it and keep it private.**
6. Now search for **@userinfobot**, press Start, and it replies with your
   numeric **user ID** (e.g. `123456789`). Copy it.
7. Press **Start** on your new bot (open its chat and tap Start). It cannot
   message you before you do this once.

### Step 2 — Create the GitHub repository (5 minutes)

1. Go to <https://github.com> and sign in (create a free account if needed).
2. Click the **+** icon (top right) → **New repository**.
3. Repository name: `amazon-deal-radar`. Choose **Private** (recommended) or
   Public (Public = free unlimited Actions minutes; see note in workflow).
   Do **not** tick "Add a README".
4. Click **Create repository**.

### Step 3 — Upload the project (2 minutes)

On the new empty repository page, click **"uploading an existing file"**.
Drag the **entire contents of this folder** (all files and sub-folders,
including `.github`) into the page, then click **Commit changes**.

> Tip: if you don't see `.github` in the drag list, use
> "Add file → Upload files" again and drag the `.github` folder itself.

### Step 4 — Add your two secrets (2 minutes)

⚠️ **Never paste your bot token into any file in the repository.** It goes
only into GitHub Secrets, which is encrypted.

1. In your repository, click **Settings** (top bar) → left menu
   **Secrets and variables** → **Actions**.
2. Click **New repository secret**:
   - Name: `TELEGRAM_BOT_TOKEN` — Secret: *paste your BotFather token*
3. Click **New repository secret** again:
   - Name: `TELEGRAM_CHAT_ID` — Secret: *paste your numeric user ID*

### Step 5 — Test it right now (1 minute)

1. In your repository click the **Actions** tab.
2. Click **Deal Radar** in the left list.
3. Click **Run workflow** → **Run workflow** (green button).
4. After ~3–5 minutes, check your Telegram: if any qualifying used deal
   exists right now, you'll get alerts; the Actions log always shows a
   summary like `alerts_sent: N`.

From now on it runs automatically every 15 minutes. (To check every 5
minutes instead: open `.github/workflows/radar.yml` in GitHub, click the
pencil ✏️ icon, change `*/15` to `*/5`, Commit. Works best on a Public repo —
see the note in the workflow file.)

---

## Understanding the alerts

Each alert shows: product name + photo, used price, new price, savings %,
condition, seller, shipping, ASIN, direct link, and discovery time — plus a
**verification status** so you always know how much to trust it:

| Status | Meaning |
|---|---|
| ✅ Verified deal | Live NEW price and used offer both confirmed on the product page. Savings ≥ 50%. |
| ⚠️ Potential price error | Verified AND ≥ 90% below new price — extremely rare; double-check the listing yourself before buying. |
| ❓ Unverified price | Cheap offer seen in search; product page not (yet) confirmed. Shown only when suspiciously cheap. |
| 🧩 Incomplete product information | Used price known but NEW price not shown on the page. |

**Rules built in:** suspiciously low prices are flagged even when the
comparison is incomplete (e.g. an "iPhone" at EGP 800); an offer is **never**
labelled a confirmed bargain unless both prices were verified live; unchanged
offers never re-notify (re-alerts only on ≥5% price drops or better savings
after a 24h cooldown).

---

## Configuration (optional)

All thresholds are repository **variables/secrets** or in
`src/deal_radar/config.py`:

| Env var | Default | Meaning |
|---|---|---|
| `ALERT_THRESHOLD` | 50 | alert at ≥ this % below new price |
| `HIGH_PRIORITY_THRESHOLD` | 70 | high-priority alert |
| `EXCEPTIONAL_THRESHOLD` | 90 | exceptional / likely price error |
| `DISCOVERY_PAGES_PER_CATEGORY` | 2 | pages scanned per category per run |
| `MAX_CANDIDATES_PER_RUN` | 30 | product pages verified per run |
| `FETCH_DELAY_MIN/MAX` | 2.5 / 6.0 | polite random delay between page loads |

---

## Running locally (optional)

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python run.py --dry-run        # full run, no Telegram messages
python run.py                  # needs .env with the two secrets
```

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -v
```

61 automated tests cover the parsers (against real captured Amazon.eg HTML),
savings math and tiers, verification statuses, duplicate prevention, Telegram
messages, robots.txt compliance, block detection, and a full simulated
pipeline — see `tests/test_report.md`.

---

## Limitations (the honest list)

1. **Coverage** — monitors used offers discoverable through Amazon Resale
   search pages + product-page used offers. Third-party-only used offers that
   appear nowhere in those places are not covered. The full per-seller offers
   page is disallowed by Amazon's robots.txt and is deliberately not scraped.
2. **Anti-bot variability** — Amazon may serve challenges to automated
   browsers, especially from datacenter IPs (GitHub Actions). The system
   detects this, backs off, stops the run, and sends you a Telegram health
   message. Runs may occasionally find nothing instead of failing silently.
3. **Scheduling** — GitHub Actions cron is best-effort: runs can start a few
   minutes late and a run is skipped if the previous one is still executing.
   Think "every ~15 minutes", not real-time.
4. **Price changes between runs** — a used offer can be bought or corrected
   within minutes on Amazon. Alerts arrive within one scheduling cycle, but
   the price you see may have changed by then.
5. **Variant matching** — comparison is done on the same ASIN (exact
   product). Variant mixups within one ASIN can still occur (rare); the
   ⚠️ status exists precisely so you double-check before buying.
6. **Database persistence** — the SQLite database is carried between runs
   via the GitHub Actions cache, which GitHub may occasionally evict. After
   an eviction the system rebuilds its state automatically; you may get a
   few repeated alerts once.
7. **No Associates account needed** — the tool does not use Amazon's
   Product Advertising API (that requires an Associates account); it reads
   the public pages like your browser does.

## Compliance

* Respects `https://www.amazon.eg/robots.txt` (checked before every fetch;
  disallowed URLs are never requested).
* No CAPTCHA solving, no IP-proxy evasion, no fingerprint forgery, no
  authentication bypass. When Amazon says "stop", the system stops.
* Only public product data is read; nothing is purchased and no account is
  accessed.
