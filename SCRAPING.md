# How we scrape data — complete reference

This document describes **every scraping path** in this repository: website hybrid, Coles mobile API, Woolworths Iris GraphQL, session capture, pacing, storage, ETL handoff, entry points, env vars, and known caveats.

Related docs (downstream of scrape, not scrape itself):

- [`lake/SILVER_GOLD.md`](lake/SILVER_GOLD.md) — bronze → silver → gold transforms
- [`lake/METHODS.md`](lake/METHODS.md) — bay share, Coles bay inference, overlap
- [`apps/store-ci/METHODS.md`](apps/store-ci/METHODS.md) — dashboard methods

---

## Table of contents

1. [Big picture](#1-big-picture)
2. [Two scraping eras](#2-two-scraping-eras)
3. [Package layout](#3-package-layout)
4. [Tech stack](#4-tech-stack)
5. [Retailers, stores, and catalogues](#5-retailers-stores-and-catalogues)
6. [Anti-bot layers (why the architecture looks like this)](#6-anti-bot-layers-why-the-architecture-looks-like-this)
7. [Website hybrid path](#7-website-hybrid-path)
8. [Coles mobile path (apigw)](#8-coles-mobile-path-apigw)
9. [Woolworths mobile path (Iris GraphQL)](#9-woolworths-mobile-path-iris-graphql)
10. [Ashfield deep scrape (production)](#10-ashfield-deep-scrape-production)
11. [Legacy / multi-store website scrapes](#11-legacy--multi-store-website-scrapes)
12. [Session capture (emulator + mitm)](#12-session-capture-emulator--mitm)
13. [Data models](#13-data-models)
14. [Storage](#14-storage)
15. [ETL after scrape](#15-etl-after-scrape)
16. [Pacing, concurrency, checkpoints, watchdog](#16-pacing-concurrency-checkpoints-watchdog)
17. [Error handling and logging](#17-error-handling-and-logging)
18. [Configuration and environment variables](#18-configuration-and-environment-variables)
19. [Entry points and CLI cheatsheet](#19-entry-points-and-cli-cheatsheet)
20. [Dependencies and one-time setup](#20-dependencies-and-one-time-setup)
21. [Known bugs, caveats, and design decisions](#21-known-bugs-caveats-and-design-decisions)
22. [File index](#22-file-index)

---

## 1. Big picture

We scrape **Coles** and **Woolworths** (Australia) for:

| Signal | Coles source | Woolworths source |
|--------|--------------|-------------------|
| Identity (SKU, name, brand, size) | Website BFF **or** mobile `products/list` | Website Search **or** Iris `productList` / `productDetailsPage` |
| Price / was / unit price | Same | Same (Iris prices are **cents**) |
| Stock | Same | Same |
| **Physical aisle / bay / map coords** | Mobile only (`shoppingMethod=inStore`) | Iris only (`INSTORE` mode) |
| Categories | Website `onlineHeirs` **or** static catalogue CSV | Website category tree / Iris breadcrumbs |

**Website APIs alone cannot give reliable in-store placement.** Coles website `locations[].aisle` is usually null unless the mobile `inStore` shopping method is used. Woolworths website has no physical aisle field (its “Aisle” labels are online taxonomy).

```
┌──────────────────────────────────────────────────────────────────────────┐
│ PRODUCTION (Ashfield deep scrape)                                        │
│                                                                          │
│  Coles 791: catalogue CSV SKUs                                           │
│      → Android token (x-d-token)                                         │
│      → POST apigw .../products/list?shoppingMethod=inStore               │
│      → lake/bronze/coles/791/<run_id>/products_list.jsonl                │
│                                                                          │
│  Woolworths 1213:                                                        │
│      → guest Bearer + optional Akamai sensor via mitm                    │
│      → Iris productList (discovery) + productDetailsPage INSTORE (PDP)   │
│      → lake/bronze/woolworths/1213/<run_id>/{search_pages,product_details}.jsonl
│                                                                          │
│  Then: bronze → silver → gold → store-ci dashboard export                │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│ LEGACY / MULTI-STORE (DuckDB star schema)                                │
│                                                                          │
│  Playwright bootstrap (cookies + Coles APIM key)                         │
│      → curl_cffi website search by category terms                        │
│      → Product rows → scraper_data.duckdb (SCD2 price_history)           │
│      → optional Coles aisle enrich via same mobile products/list         │
│                                                                          │
│  Entry: main.py, daily_scrape.py, scrape_burwood.py, demo_scrape*.py     │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Two scraping eras

### Era A — Website hybrid (still present)

`PlaywrightBootstrapper` solves Imperva/Akamai in a real Chromium, then `CurlCffiEngine` reuses a TLS-impersonating HTTP client for search/store APIs. Discovery is **search-term driven** (category leaves → paginated search). Used by `main.py`, `daily_scrape.py`, demos.

**Problem observed (Aug 2026 pilot):** ~15–30% of Coles stores failed because the search box never became visible under load. Website discovery is flaky for full-store coverage.

### Era B — Mobile-first Ashfield (current production)

- **Coles:** Skip website discovery. Use a known nationwide SKU list (`data/coles_catalogue_categories.csv.csv`, ~29.6k SKUs) and batch-lookup via the app’s private API.
- **Woolworths:** Use Iris GraphQL (`productList` for discovery, `productDetailsPage` for INSTORE placement). Website search is a **fallback** if Iris list fails.

Orchestration: `scrape_ashfield_deep.py`, wrappers `./scrape_ashfield` and `./watch_ashfield`.

---

## 3. Package layout

### Core: `hybrid_scraper/`

| Module | Role |
|--------|------|
| `config.py` | Retailer URLs, UA, stealth JS, suburb lists |
| `models.py` | `Product`, `StoreLocation`, `SessionContext`, `MobileSessionContext` |
| `bootstrapper.py` | Playwright Chromium → cookies / Coles subscription key |
| `engine.py` | curl_cffi store resolve + category + search + parsers |
| `orchestrator.py` | Bootstrap + retry loop across stores |
| `storage.py` | DuckDB star schema (`scraper_data.duckdb`) |
| `exceptions.py` | Typed errors for retry decisions |
| `logging_config.py` | Console + rotating file logs |
| `aisle_enrichment.py` | Coles mobile batch fetcher + aisle-only enrich |
| `mobile_products.py` | Full Coles `Product` rows from mobile API |
| `mobile_session.py` | Emulator + mitm → Coles `x-d-token` |
| `mobile_capture_addon.py` | mitmproxy addon (Coles header capture) |
| `woolworths_mobile_session.py` | Guest mint + optional WW mitm capture |
| `woolworths_mobile_capture_addon.py` | mitmproxy addon (WW sensor/auth) |
| `woolworths_aisle_enrichment.py` | Iris GraphQL list/PDP + placement parse |
| `woolworths_queries/*` | Live GraphQL documents + defaults JSON |
| `ashfield_session.py` | One-shot warmup for Ashfield deep scrape |
| `coles_ui_nav.py` | UIAutomator taps to trigger token-bearing requests |
| `emulator_utils.py` | adb / emulator / APK / geo / proxy helpers |
| `process_lock.py` | Cross-process flock so Coles+WW captures don’t collide |
| `demo_stores.py` | Load `demo_stores.csv` (42-store pilot) |

### Lake: `lake/`

| Path | Role |
|------|------|
| `io.py` | Bronze JSONL, checkpoints, run IDs, secret-safe logging |
| `etl/bronze_to_silver.py` | Normalize bronze → silver |
| `etl/silver_to_gold.py` | Gold DuckDB + CSV exports |
| `etl/bay_inference.py` | Infer Coles bays from indoor coords |
| `etl/sku_matcher.py` | Fuzzy Coles ↔ WW matching |
| `etl/category_crosswalk.py` | Coles slug → WW L0 |
| `bronze/`, `silver/`, `gold/`, `ref/` | Medallion dirs |

### Top-level scripts

| Script | Purpose |
|--------|---------|
| `scrape_ashfield_deep.py` | **Primary** Ashfield scrape + ETL |
| `scrape_ashfield` | Bash wrapper (ports, AVD, banner) |
| `watch_ashfield` | Unattended restart until checkpoints complete |
| `main.py` | Ashfield website hybrid demo → DuckDB |
| `daily_scrape.py` | Multi-suburb website scrape + Coles aisle enrich |
| `scrape_burwood.py` | One-off suburb scrape + Coles aisle |
| `demo_scrape.py` | 42-store Coles website pilot |
| `demo_scrape_mobile.py` | Mobile Coles multi-store |
| `demo_scrape_woolworths_mobile.py` | WW Iris smoke |
| `refresh_mobile_session.py` | Force Coles token capture |
| `dashboard.py` | Streamlit over `scraper_data.duckdb` |
| `run_store_ci` / `run_pc_dashboard` | Vite apps (consume gold export; do not scrape) |

---

## 4. Tech stack

| Layer | Technology | Used for |
|-------|------------|----------|
| Browser | **Playwright** Chromium (headless) | Website anti-bot challenge + observe Coles APIM key |
| HTTP | **curl_cffi** `AsyncSession` with `impersonate="chrome120"` | All website APIs + mobile HTTP |
| TLS fingerprint | Locked to Chrome 120 UA (`config.DEFAULT_USER_AGENT`) | Must match impersonate target |
| Mobile intercept | **mitmproxy / mitmdump** | Capture app attestation / Akamai sensor headers |
| Device | **Android Emulator + adb** | Run patched apps, set proxy, geo, UI nav |
| UI automation | UIAutomator XML dumps (`coles_ui_nav.py`) | Tap Welcome / store browse for Coles token |
| Models | **Pydantic** | `Product`, session contexts |
| Analytics store | **DuckDB** | Legacy star schema + gold compare DB |
| Progress UI | **rich** | Long scrape progress bars |

**Not used:** Selenium, BeautifulSoup, plain `requests`/`httpx` for retailer APIs, CAPTCHA solvers.

---

## 5. Retailers, stores, and catalogues

### Fixed Ashfield stores (deep scrape)

| Banner | Store ID | Name | Lat / Lon |
|--------|----------|------|-----------|
| Coles | `791` | Coles Ashfield | −33.889879, 151.124763 |
| Woolworths | `1213` | Woolworths Ashfield | −33.8895, 151.1250 |

Defined in `scrape_ashfield_deep.py` as `COLES_STORE` / `WW_STORE`.

### Suburb-driven resolution (website path)

- `TEST_SUBURB = "Ashfield NSW 2131"`
- `SAMPLE_SUBURBS` — Bondi Junction, Chatswood, Parramatta, Melbourne CBD, Brisbane CBD
- `DAILY_SCRAPE_SUBURBS` — Ashfield NSW 2131, Burwood East VIC 3151

Each suburb independently resolves to nearest Coles **and** nearest Woolworths via `CurlCffiEngine.resolve_store_id`.

### Coles catalogue CSV

Path: `data/coles_catalogue_categories.csv.csv`

- ~29,616 rows: `retailer_product_id` → `category` slug
- Loaded by `mobile_products.load_catalogue_categories`
- **Ashfield Coles deep scrape iterates this list** (not live discovery)
- Mobile API does not return category taxonomy; CSV supplies `Product.category`

### Canary SKUs

`ashfield_sample_skus.csv`, or hardcoded fallbacks:

- Coles: `7667368`, `329607`, `3646151`
- WW: `36066`, `277728`

---

## 6. Anti-bot layers (why the architecture looks like this)

### Coles website — Imperva / Incapsula

Cookies that matter: `incap_ses_*`, `visid_incap_*` (and related). Minted only after client-side JS challenge runs in a real browser.

Also required for BFF/GraphQL: Azure APIM header `ocp-apim-subscription-key`, observed on outgoing search XHRs from Playwright (not scraped from a JS bundle by regex).

### Woolworths website — Akamai Bot Manager

Cookies: `_abck`, `bm_sz`. `_abck`’s sensor-state segment only validates after Akamai’s obfuscated telemetry POST runs in a real browser — curl_cffi alone cannot do that.

### Coles mobile — Incapsula + device attestation

Host: `apigw.coles.com.au`. Requires:

- Different `ocp-apim-subscription-key` than the website
- Device identity headers (`client`, `x-app-version`, `x-device-model`, `x-device-id`, `x-client-os`, okhttp UA)
- Opaque **`x-d-token`** — device attestation blob; requests without a valid one **403**. We **do not forge** it; we capture it from the real app via mitm.

### Woolworths mobile — guest auth + Akamai sensor

- Guest Bearer from `POST .../wow/v2/commerce/guest` with static `x-api-key`
- Iris GraphQL preferably routed through local mitmdump with captured `x-acf-sensor-data` to avoid **CDN-poisoned** responses (payload looks like GraphQL but wrong/missing ops)

### Stealth (Playwright)

Injected before page scripts (`config.STEALTH_INIT_SCRIPT`):

- Hide `navigator.webdriver`
- Fake `languages` → `en-AU`, `en`
- Fake `plugins` length
- Stub `window.chrome`
- Patch `permissions.query` for notifications

Chromium launch args: `--disable-blink-features=AutomationControlled`, `--disable-dev-shm-usage`, `--no-sandbox`.

Viewport 1920×1080, locale `en-AU`, timezone `Australia/Sydney`.

### Cookie fingerprint isolation

Playwright cookies are **not** copied blindly into curl_cffi. Cookies are **re-harvested** with a curl_cffi homepage GET so TLS fingerprint and cookie jar stay consistent (binding mismatch is itself a bot signal).

### No CAPTCHA solver

Challenges are solved by real browser/app only. Timeouts warn and continue or fall back (WW can use curl_cffi-only cookie path if Chromium gets 403).

---

## 7. Website hybrid path

### Endpoints (`hybrid_scraper/config.py`)

#### Coles

| Purpose | URL | Method |
|---------|-----|--------|
| Base / cookie harvest | `https://www.coles.com.au` | GET |
| Store finder page | `https://www.coles.com.au/find-stores` | browser |
| Store resolve GraphQL | `https://www.coles.com.au/api/graphql` | POST |
| Categories (broken) | `https://www.coles.com.au/api/bff/categories` | GET — **404s in practice** |
| Product search | `https://www.coles.com.au/api/bff/products/search` | GET |

Search query params (`engine.fetch_products_page`):

```
storeId, start, pageSize=48, searchTerm, sortBy=salesDescending,
excludeAds=true, authenticated=false
```

Response: `results[]`, `noOfResults`.

GraphQL ops (in `engine.py`): `GetStoreLocationSuggestions`, `FindStores` with `brandIds: ["COL"]`.

#### Woolworths

| Purpose | URL | Method |
|---------|-----|--------|
| Base | `https://www.woolworths.com.au` | GET |
| Categories | `https://www.woolworths.com.au/apis/ui/PiesCategoriesWithSpecials` | GET |
| Search | `https://www.woolworths.com.au/apis/ui/Search/products` | POST |
| Suburb geocode | `.../apis/ui/StoreLocator/Suburbs?SearchTerm=` | GET |
| Nearby stores | `.../apis/ui/StoreLocator/Stores?Latitude=&Longitude=&Max=3&Division=SUPERMARKETS` | GET |

Search body:

```json
{ "SearchTerm": "...", "PageNumber": 1, "PageSize": 36 }
```

Response: `Products` bundles (each with inner `Products` list to flatten), `SearchResultsCount`.

### Bootstrap flow (`bootstrapper.py`)

1. Launch Chromium with stealth context.
2. Optionally set store cookie before nav: Coles `selectedStoreId`, WW `swSelectedStore`.
3. Navigate homepage; wait for anti-bot cookies.
4. Interact with search UI so XHRs fire:
   - Coles: `#search-text-input`
   - WW: `get_by_role("searchbox")`
5. Listen on `page.on("request")` for Coles `ocp-apim-subscription-key`.
6. Re-harvest cookies via curl_cffi GET to base URL.
7. Return `SessionContext` (cookies, headers, store_id, `created_at`, TTL **900s**).

### Engine flow (`engine.py`)

1. `resolve_store_id(retailer, suburb)` → `StoreLocation`
2. `fetch_category_tree` — WW from PiesCategories; Coles website categories often fail → mobile categories or fallback terms
3. `fetch_all_products_for_store`:
   - Default: max **30** diverse search terms, max **5** pages per term
   - Concurrent pages under a semaphore (default concurrency **8**)
   - Dedup by `product_key`
4. Parse each item → dict of Product fields → caller builds `Product`

### Coles website item fields parsed

From live capture: `id`, `name`, `brand`, `size`, `availability`, `pricing.{now,was,comparable}`, `onlineHeirs[]`, `imageUris[]`, `locations[].{aisle,shelf}`.

Pack size regex: `^\s*([\d.]+)\s*([a-zA-Z]+)\s*$` — multipacks like `"2 x 250mL"` → `(None, None)`.

**Known bug:** image URL prefix `https://www.coles.com.au` + relative `uri` is wrong (real CDN host unknown); relative path portion is fine.

### Woolworths website item fields parsed

`Stockcode`, `Barcode`, `Name`/`DisplayName`, `Brand`, `PackageSize`, `Price`, `WasPrice`, `IsInStock`/`IsAvailable`, `CupString`, `SmallImageFile`, `Rating.{ReviewCount,Average}`, `UrlFriendlyName`, `IsOnSpecial`/`IsNew`.

Product page: `https://www.woolworths.com.au/shop/productdetails/{stockcode}/{url_name}`.

Physical aisle/bay left **null** on website path.

### Orchestrator fallback (`orchestrator.py`)

Per store, up to **3** attempts:

| Error | Action |
|-------|--------|
| `AuthExpiredError` (401/403), `RateLimitError` (429), `SessionExpiredError`, `BootstrapError` | Re-bootstrap and retry; sleep on Retry-After if present |
| `NetworkError`, `ParsingError` | Hard fail for that store (no retry) |
| Exhausted retries | `MaxRetriesExceededError` |

`run_for_stores` isolates failures per store; optional `max_concurrent_stores` and `launch_stagger_seconds`.

---

## 8. Coles mobile path (apigw)

### Why

Same `products/list` endpoint returns **pricing + name + brand + stock + in-store locations** in one payload when `shoppingMethod=inStore`. With a known SKU list, no Playwright search crawl is required.

### Endpoint

```
POST https://apigw.coles.com.au/digital/colesappbff/v3/api/2/products/list
     ?storeId={id}
     &shoppingMethod=inStore
     &limit={n}
     &includeLiquor=true
     &includeTobacco=true

Body: {"skus": ["1788756", "9735935", ...]}
```

Batch size **10** (matches live app). Constant: `aisle_enrichment.APP_PRODUCTS_LIST_URL`, `BATCH_SIZE = 10`.

Related: category tree (for website fallback / demos):

```
GET .../v3/api/2/products/categories
    ?storeId={id}&shoppingMethod=clickAndCollect&includeLiquor=true&includeTobacco=true
```

Walk `subCategories`; skip top-level “special”/“value”; leaf if `productCount > 0`.

### Headers

From app capture or env override:

| Header | Notes |
|--------|------|
| `ocp-apim-subscription-key` | App APIM key (≠ website) |
| `x-d-token` | Required attestation |
| `client` | e.g. `Android 6.84.0` |
| `x-app-version` | e.g. `Release:6.84.0(20001)` |
| `x-device-model` | e.g. `OnePlus NE2211` |
| `x-device-id` | UUID |
| `x-client-os` | e.g. `Android:9` |
| `accept-language` | `en-AU;q=1` |
| `user-agent` | `okhttp/5.3.2` |
| `content-type` | `application/json; charset=utf-8` |

Env override (skips emulator): `COLES_APP_SUBSCRIPTION_KEY` + `COLES_APP_X_D_TOKEN`.

Session TTL default **600s** (`MobileSessionContext`). Observed useful lifetime often ~15–20 minutes before 403s.

### Location payload shape (inStore)

```json
{
  "aisle": "Aisle 9",
  "aisleSide": "Right",
  "facing": 1,
  "order": 9.0,
  "description": "Located in Aisle 9 at $STORE",
  "indoorCoordinates": { "productX": 5484.292, "productY": 2760.252 }
}
```

Mapped to: `aisle_number`, `bay_number` (from side for enrichment / later inference), `aisle_facing`, `aisle_order`, `indoor_x`, `indoor_y`.

Coles does **not** expose a native bay number; gold layer **infers** bays from coordinates (`lake/etl/bay_inference.py`).

### `MobileBatchFetcher` behaviour

- Shared headers + single-flight refresh lock across a run
- Semaphore: Ashfield uses `max_concurrent_batches=1`
- Hard timeout **25s** per request (`asyncio.wait_for`) — Imperva can half-close sockets without returning
- **8 consecutive timeouts** → abort as WAF soft-block (`NetworkError`)
- On 403: pause 20–45s, try pooled token, else recreate (max **3** auth recoveries)
- Pacing: triangular random between min/max delay; optional rare 8–25s extra pause if `COLES_HUMAN_EXTRA_PAUSE` not `0`

### Token pool

- `.tools/coles_mobile_session.json` — current session
- `.tools/coles_mobile_session_pool.json` — pool (default size 3)
- Mint gap between pool captures: 8s

---

## 9. Woolworths mobile path (Iris GraphQL)

### Auth

```
POST https://prod.mobile-api.woolworths.com.au/wow/v2/commerce/guest
Headers: x-api-key (static SHOP_IRIS_API_KEY), region/capability headers
Body: {"device_auth_token": "<uuid>", "postcode": "2131"}
→ Bearer access_token
```

Default API key is embedded in `woolworths_mobile_session.py` (app BuildConfig client key, overridable via `WOOLWORTHS_APP_API_KEY`).

### GraphQL

```
POST https://prod.mobile-api.woolworths.com.au/hermes/iris/v1/graphql
```

Documents under `hybrid_scraper/woolworths_queries/`:

| File | Use |
|------|-----|
| `productList.graphql` + `productList_defaults.json` | Discovery (`type=search`) |
| `productDetailsPage.graphql` + `pdp_defaults.json` | Exact SKU, `mode=INSTORE` + `storeId` |

### Headers (typical)

- `authorization: Bearer ...`
- `x-api-key`
- `x-woolies-region: AU`
- `x-apigee-location: apigeeEdge`
- `wx-user-timezone: Australia/Sydney`
- `x-shop-supported-capabilities: ...`
- `user-agent: Supers/26.16.0 (AndroidPhone; 37)`
- Optional from mitm capture: `x-acf-sensor-data`, `x-adobe-ecid`, `x-tealium-visitor-id`, `x-dynatrace`
- Per-request `x-correlation-id` (new UUID)

### Proxy preference

Prefer routing Iris through local mitmdump (`WOOLWORTHS_MITM_PROXY`, Ashfield wrapper sets `http://127.0.0.1:8083`). Disable with `WOOLWORTHS_DISABLE_MITM_PROXY=1`.

CDN poison detection: response contains `productsByCategory` but missing expected ops → treat as bad HIT.

### Placement fields

```
inStoreLocation.details.{aisleNumber, bayNumber, aisleSide, x, y, z, ...}
inStoreLocation.displayInfo.locationText
```

`ProductCard.price` is **integer cents** → dollars via `_parse_price`.

Promo: `promotionInfo`, `wasPrice`, `multiBuyPriceInfo`, `memberPriceInfo`.

Categories sorted by `categoryLevel`.

### Discovery vs PDP in Ashfield

1. **search phase:** website category tree → Iris `productList` per leaf (skip non-grocery roots) → accumulate `discovered_ids`; fallback website search if Iris list dies
2. **iris phase:** each pending ID → `productDetailsPage` INSTORE → bronze PDP JSONL

Skipped WW root categories:

```
Everyday Market, Computers, Home & Lifestyle, Entertainment, Baby & Child
```

Iris list page size: **40**. Iris PDP delays: **0.65–0.9s** (from `scripts/bench_ww_iris_rate.py`). Search delays: **4–9s**.

---

## 10. Ashfield deep scrape (production)

### Entry

```bash
./scrape_ashfield --max-hours 8
./scrape_ashfield --banner woolworths --ww-phase search
./scrape_ashfield --banner woolworths --ww-phase iris
.venv/bin/python scrape_ashfield_deep.py --phase etl
./watch_ashfield                 # until checkpoints complete
./watch_ashfield --status
```

### Wrapper behaviour (`scrape_ashfield`)

- AVD default `WW_Rootable`, serial `emulator-5554`
- Coles: `EMULATOR_PROXY_PORT=8082`, unset `WOOLWORTHS_MITM_PROXY`
- WW: `EMULATOR_PROXY_PORT=8083`, `WOOLWORTHS_MITM_PROXY=http://127.0.0.1:8083`
- Forwards to `scrape_ashfield_deep.py`

### CLI flags (`scrape_ashfield_deep.py`)

| Flag | Meaning |
|------|---------|
| `--banner coles\|woolworths\|both` | Which banner |
| `--phase scrape\|etl\|all` | Write bronze / run ETL / both |
| `--canary` | 5-SKU smoke, slow delays, no long pauses |
| `--max-hours` | Stop between batches (default **8**) |
| `--min-delay` / `--max-delay` | Override pacing |
| `--pause-every` / `--pause-seconds` | Optional long anti-bot pauses |
| `--ww-phase search\|iris\|both` | WW discovery vs PDP |
| `--limit-skus` / `--limit-terms` | Caps |
| `--reset-checkpoint` | New `run_id`, ignore resume |
| `--record-duckdb` | Also write parsed products to `scraper_data.duckdb` |
| `--force-session` | Force fresh mobile capture |
| `--skip-session-warmup` | Assume sessions already valid |
| `--quiet` | Console WARNING only |

Default delays:

| Path | Min–max seconds |
|------|-----------------|
| Coles batches | 4–10 |
| WW search / productList | 4–9 |
| WW Iris PDP | 0.65–0.9 |

### Session warmup (`ashfield_session.warmup_ashfield_sessions`)

Unless `--skip-session-warmup`:

1. Start emulator / ensure device ready
2. For WW: set Ashfield geo
3. Coles: capture or reuse `x-d-token` (or env overrides)
4. WW: mint guest session; prefer mitm sensor headers

Cross-process lock (`process_lock`) so Coles and WW captures queue instead of fighting over mitm ports.

### Coles deep scrape step-by-step

1. Load checkpoint `lake/bronze/coles/791/checkpoint.json` (resume default)
2. Ensure `run_id` (UTC stamp `YYYYMMDDTHHMMSSZ`)
3. Load all SKUs from catalogue CSV (or canary / `--limit-skus`)
4. Resume at `next_batch_index`
5. `MobileBatchFetcher` sequential batches of 10 → POST products/list
6. Append raw record to `products_list.jsonl`:

```json
{
  "captured_at": "<iso>",
  "store_id": "791",
  "batch_index": 0,
  "skus": ["..."],
  "n_results": 10,
  "results": [ /* raw API items */ ]
}
```

7. Checkpoint after each batch: `next_batch_index`, `run_id`, `updated_at`
8. Stop on: time budget, WAF consecutive timeouts, auth exhaustion, or all batches done (`stop_reason=complete`)
9. Optional DuckDB record

Rough scale: ~29.6k SKUs ÷ 10 ≈ **2962 batches**; at ~10s/batch → multi-hour run (hence `--max-hours` + watchdog).

### Woolworths deep scrape step-by-step

1. Warmup guest + mitm
2. **search:** walk grocery category leaves → Iris `productList` (or website fallback) → merge `discovered_ids` / `completed_terms` into checkpoint (fcntl lock for parallel workers)
3. **iris:** poll discovered IDs every `WW_IRIS_POLL_SECONDS` (30s) while discovery may still run; fetch `productDetailsPage`; append `product_details.jsonl`; track `iris_completed_ids`
4. Bronze also may write `search_pages.jsonl` for website fallback pages
5. Complete when search done and every discovered ID has Iris PDP

### Watchdog (`watch_ashfield`)

- Runs Coles + WW search + WW iris as **three** parallel workers
- Poll every `WATCH_POLL_SECONDS` (default 60)
- Restarts dead processes; after 3 rapid exits, backoff `WATCH_BACKOFF_SECONDS` (120)
- Ensures WW mitm via `scripts/ensure_ww_mitm.py`
- Completeness:
  - Coles: `stop_reason == complete` or `next_batch_index >= batches_total`
  - WW: `search_complete` and all `discovered_ids` ⊆ `iris_completed_ids`
- Logs: `ashfield_watchdog.log`, `ashfield_coles.log`, `ashfield_ww_search.log`, `ashfield_ww_iris.log`
- PID file: `.tools/ashfield_watchdog.pid`

---

## 11. Legacy / multi-store website scrapes

### `main.py`

Ashfield `TEST_SUBURB` hybrid demo → `scraper_data.duckdb`.

### `daily_scrape.py`

1. Bootstrap Coles placeholder store `0` to get APIM key
2. Resolve each `DAILY_SCRAPE_SUBURBS` suburb → Coles + WW stores
3. `ScraperOrchestrator.run_for_stores`
4. `ProductStore.record_scrape` (SCD2)
5. Coles aisle enrich via `fetch_coles_instore_locations` (skip with warning if emulator/token unavailable)
6. Exit 0 if ≥1 store succeeded; 1 if all failed

Scheduler-oriented (e.g. Windows Task Scheduler). Close Streamlit/`dashboard.py` first — DuckDB single-writer.

### `scrape_burwood.py`

One-off suburb scrape + Coles aisle enrich (original home of aisle enrichment logic).

### `demo_scrape.py`

42-store Coles pilot from `demo_stores.csv`; max 2 concurrent stores, 5s stagger.

### `demo_scrape_mobile.py` / `demo_scrape_woolworths_mobile.py`

Mobile-first multi-store / Iris smoke tests.

### `refresh_mobile_session.py`

Force Coles token capture (`--force`).

---

## 12. Session capture (emulator + mitm)

### Prerequisites (one-time)

1. Android Emulator with AVD (default `WW_Rootable`)
2. Coles / Woolworths APKs installed — Coles APK **cert-pinning patched** / DEBUGGABLE so user CA is trusted
3. mitmproxy CA installed on device as user cert
4. APKs under `.tools/apk/{coles|woolworths}/install_ready/` (helpers: `scripts/fetch_coles_arm64.py`, `build_coles_install_ready.sh`)

### Coles capture flow (`mobile_session.py`)

1. Start `mitmdump` with `mobile_capture_addon.py` on `EMULATOR_PROXY_PORT`
2. `adb` set global `http_proxy` to `EMULATOR_PROXY_HOST:PORT` (default host `10.0.2.2` — emulator’s host loopback)
3. Force-stop + relaunch `com.coles.android.shopmate`
4. `navigate_for_token_capture` (UIAutomator) to screens that hit apigw
5. Poll addon output until request with `x-d-token` to host matching `coles.com.au` / `coles.opapi.au`
6. Write session JSON; clear proxy; stop mitm
7. On timeout: `MobileTokenCaptureError` includes `hosts_seen_log` of every host contacted

### Woolworths capture / mint (`woolworths_mobile_session.py`)

- **Default:** mint guest token over HTTP (no emulator required once API key known)
- **Optional:** emulator + mitm to capture sensor/auth headers for safer Iris calls
- Cache: `.tools/woolworths_mobile_session.json`

### Ports (Ashfield convention)

| Banner | mitm port |
|--------|-----------|
| Coles | 8082 |
| Woolworths | 8083 |

---

## 13. Data models

### `StoreLocation`

`retailer`, `store_id`, `store_name`, `suburb_name`, `state`, `postcode`, `latitude`, `longitude`.

### `SessionContext` (website)

`retailer`, `cookies`, `headers`, `store_id`, `created_at`, `ttl_seconds=900`.

### `MobileSessionContext`

`headers`, `created_at`, `ttl_seconds=600`.

### `Product`

Identity: `retailer`, `retailer_product_id`, `child_product_id`, `scrape_date`, `scraped_at`

Catalog: `name`, `clean_brand`, `category`, `sub_category_1..3`, `pack_size`, `clean_uom`, `product_page`, `image_url`

Facts: `price_display`, `loyalty_price`, `price_per_uom`, `prev_price`, `stock_status`, `product_badge`, `no_of_reviews`, `star_rating`, `plv_id`

Placement: `aisle_number`, `bay_number`, `aisle_facing`, `aisle_order`, `indoor_x`, `indoor_y`

Key: `{retailer}:{retailer_product_id}:{child_product_id or ''}`

Does **not** embed store geo — that lives in the `stores` dimension.

---

## 14. Storage

### Legacy DuckDB — `scraper_data.duckdb` (`storage.py`)

Star schema:

| Table | Role |
|-------|------|
| `stores` | Dimension: physical stores |
| `products` | Dimension: catalog attributes |
| `price_history` | Fact SCD2: price/stock/badge/aisle when values change |
| `current_prices` | VIEW: `valid_to IS NULL` join |
| `catalog_categories` | Category helper |

Mixed-case SQL columns: `"No_of_reviews"`, `"Star_rating"`, `"PLV_ID"`.

SCD2: new fact row only when monitored columns change; same-day re-scrape can update in place.

Surrogate store key: `store_id_for(retailer, native_store_id)`.

### Ashfield lake bronze

```
lake/bronze/coles/791/<run_id>/products_list.jsonl
lake/bronze/woolworths/1213/<run_id>/search_pages.jsonl
lake/bronze/woolworths/1213/<run_id>/product_details.jsonl
lake/bronze/{banner}/{store}/checkpoint.json
```

Immutable per `run_id`. Resume via checkpoint, not by rewriting bronze.

### Session / tool caches (gitignored under `.tools/`)

- `coles_mobile_session.json`, `coles_mobile_session_pool.json`
- `woolworths_mobile_session.json`
- Capture result / hosts logs
- APK install dirs
- Watchdog PID

### Secrets policy (`lake/io.py`)

Logs may list header **names**, never token values. Markers include: token, authorization, cookie, sensor, subscription, api-key, bearer, x-d-token, x-acf.

---

## 15. ETL after scrape

Run:

```bash
.venv/bin/python scrape_ashfield_deep.py --phase etl
```

### Bronze → silver

- Normalize field names; unify categories to Woolworths L0 names
- Fuzzy SKU match (same brand + name Jaccard ≥ **0.72**) to learn Coles slug → WW L0 crosswalk
- Coles: infer `bay_key` from indoor coords (`infer_coles_bays`)
- WW: `bay_key` from native `aisle|bay`
- Outputs under `lake/silver/<stamp>/`

### Silver → gold

- `lake/gold/ashfield_compare.duckdb` tables: `sku_facts`, `category_pricing`, `category_space`, `category_venn`, `sku_matches`, `category_crosswalk`, `banner_compare`
- CSV exports under `lake/gold/exports/`

### Dashboard export

`scripts/export_store_ci_data.py` → `apps/store-ci/public/data/store_ci.json`

Full transform detail: [`lake/SILVER_GOLD.md`](lake/SILVER_GOLD.md). Bay math: [`lake/METHODS.md`](lake/METHODS.md).

---

## 16. Pacing, concurrency, checkpoints, watchdog

| Mechanism | Behaviour |
|-----------|-----------|
| Website engine semaphore | Default 8 in-flight HTTP |
| Orchestrator retries | Max 3 bootstrap cycles |
| Coles Ashfield | Concurrency 1, 4–10s triangular delay |
| WW Iris PDP | 0.65–0.9s |
| WW search | 4–9s |
| demo_scrape | Max 2 stores, 5s stagger |
| Checkpoints | Resume batch index / discovered IDs / iris done |
| `--max-hours` | Soft stop between units of work |
| `watch_ashfield` | Respawns until `stop_reason=complete` / Iris coverage |
| WW iris worker | Polls new discoveries every 30s |
| Parallel WW search+iris | Checkpoint merge under `fcntl` lock |

---

## 17. Error handling and logging

### Exception hierarchy (`exceptions.py`)

| Type | Typical cause | Orchestrator |
|------|---------------|--------------|
| `BootstrapError` | Chromium / challenge fail | Retry with re-bootstrap |
| `SessionExpiredError` | TTL exceeded | Refresh |
| `AuthExpiredError` | 401/403 | Re-bootstrap / recreate mobile token |
| `RateLimitError` | 429 (+ optional Retry-After) | Sleep and retry |
| `ParsingError` | Schema drift | Hard fail |
| `NetworkError` | Timeout / DNS / unexpected status | Hard fail (website); abort on consecutive mobile timeouts |
| `MaxRetriesExceededError` | Retry budget spent | Store failed |
| `MobileTokenCaptureError` | mitm/adb/app never sent x-d-token | Capture failed |

### Logging (`logging_config.py`)

- Logger tree `hybrid_scraper.*`
- Format: `%(asctime)s %(levelname)-8s %(name)s: %(message)s`
- Rotating file: 5MB × 3 backups (`scraper.log` or `ashfield_deep.log`)
- Never log secret values

---

## 18. Configuration and environment variables

### Static config (`hybrid_scraper/config.py`)

- `DEFAULT_USER_AGENT` — Chrome 120 Windows
- `IMPERSONATE_TARGET = "chrome120"`
- `COLES_CONFIG` / `WOOLWORTHS_CONFIG` / `RETAILER_CONFIGS`
- `STEALTH_INIT_SCRIPT`
- `TEST_SUBURB`, `SAMPLE_SUBURBS`, `DAILY_SCRAPE_SUBURBS`

### Environment variables

| Variable | Default / purpose |
|----------|-------------------|
| `EMULATOR_ADB_PATH` | `~/Library/Android/sdk/platform-tools/adb` |
| `EMULATOR_DEVICE_SERIAL` | `emulator-5554` |
| `EMULATOR_AVD` / `COLES_AVD` | `WW_Rootable` |
| `EMULATOR_PROXY_HOST` | `10.0.2.2` |
| `EMULATOR_PROXY_PORT` | `8080` (wrapper forces 8082/8083) |
| `COLES_APP_PACKAGE` | `com.coles.android.shopmate` |
| `COLES_APK_DIR` | `.tools/apk/coles/install_ready` |
| `COLES_APP_SUBSCRIPTION_KEY` | Skip emulator if set with token |
| `COLES_APP_X_D_TOKEN` | Skip emulator if set with key |
| `COLES_HUMAN_EXTRA_PAUSE` | `0` = off; else rare extra pauses |
| `WOOLWORTHS_APP_PACKAGE` | `com.woolworths` |
| `WOOLWORTHS_APP_API_KEY` | Iris static client key |
| `WOOLWORTHS_MITM_PROXY` | e.g. `http://127.0.0.1:8083` |
| `WOOLWORTHS_DISABLE_MITM_PROXY` | `1` / `true` / `yes` to disable |
| `WATCH_POLL_SECONDS` | Watchdog poll (60) |
| `WATCH_BACKOFF_SECONDS` | Rapid-exit backoff (120) |
| `WATCH_MAX_HOURS` | Chunk size for restarted scrapes (8) |

No `.env` file is required for the happy path; live tokens live under `.tools/`.

---

## 19. Entry points and CLI cheatsheet

```bash
# Production Ashfield
./scrape_ashfield --max-hours 8
./scrape_ashfield --banner woolworths --ww-phase search --max-hours 8
./scrape_ashfield --banner woolworths --ww-phase iris --max-hours 8
./watch_ashfield
./watch_ashfield --status
./watch_ashfield --stop
.venv/bin/python scrape_ashfield_deep.py --phase etl
.venv/bin/python scrape_ashfield_deep.py --canary

# Website hybrid / DuckDB
.venv/bin/python main.py
.venv/bin/python daily_scrape.py
.venv/bin/python scrape_burwood.py [suburb]
.venv/bin/python demo_scrape.py [--limit N]
.venv/bin/python demo_scrape_mobile.py
.venv/bin/python demo_scrape_woolworths_mobile.py

# Sessions
.venv/bin/python refresh_mobile_session.py --force
.venv/bin/python -m hybrid_scraper.mobile_session --pool 3
.venv/bin/python -m hybrid_scraper.woolworths_mobile_session [--emulator] [--postcode 2131]

# Supporting
.venv/bin/python scripts/ensure_ww_mitm.py
.venv/bin/python scripts/bench_ww_iris_rate.py
.venv/bin/python scripts/export_store_ci_data.py

# Dashboards (read-only consumers)
streamlit run dashboard.py
./run_store_ci
```

---

## 20. Dependencies and one-time setup

### Python (`requirements.txt`)

```
playwright>=1.45.0
curl_cffi>=0.7.1
pydantic>=2.7.0
duckdb>=1.0.0
streamlit>=1.38.0
pandas>=2.2.0
mitmproxy>=12.0.0
rich>=13.0.0
```

Also:

```bash
pip install -r requirements.txt
playwright install chromium
```

### Device / APK

- Android SDK + emulator
- Patched Coles APK (cert pinning bypass / DEBUGGABLE)
- mitm CA on device
- Optional WW install-ready APK for sensor capture

There is **no GitHub Actions cron** for scraping (CI has super-linter only). Scheduling is local (`daily_scrape.py` / `watch_ashfield`).

---

## 21. Known bugs, caveats, and design decisions

1. **Coles `image_url` domain wrong** — prefix `https://www.coles.com.au` is a guess; CDN host TBD. Relative `uri` is correct; fixable with UPDATE once host known.
2. **Coles website categories URL 404s** — use mobile categories or catalogue CSV.
3. **Website `locations` usually null** — need `shoppingMethod=inStore` on apigw.
4. **Wayfinding is per-store allowlisted** — historically docs mentioned Burwood East 584 as enabled; Ashfield **791** is used successfully for deep scrape placement.
5. **WW Playwright sometimes 403** — curl_cffi cookie fallback (no APIM key needed for WW).
6. **DuckDB single-writer** — close Streamlit before writing `scraper_data.duckdb`.
7. **Coordinates not cross-banner comparable** — only bay counts / share after inference.
8. **Coles bays are inferred** — not planogram truth; gap threshold = 4× median of smaller half of positive gaps along dominant axis.
9. **We do not reverse-engineer `x-d-token`** — capture only.
10. **Catalogue CSV is nationwide** — a SKU may not be ranged at Ashfield; empty location / missing result is expected for some IDs.
11. **WW Everyday Market / non-grocery roots skipped** — avoid polluting grocery discovery.
12. **CDN poison on Iris** — route through mitm + sensor; detect poisoned shapes.
13. **Anti-bot first** — Ashfield deliberately slow; speed is not the primary goal.
14. **Module docstring on Imperva vs Akamai assignment** — bootstrapper comments were corrected via live capture: **Coles = Imperva**, **Woolworths = Akamai** (opposite of an earlier assumption).

---

## 22. File index

| Topic | Start here |
|-------|------------|
| Architecture overview | `hybrid_scraper/__init__.py`, `models.py` |
| Endpoints & stealth | `hybrid_scraper/config.py` |
| Website scrape loop | `hybrid_scraper/engine.py`, `orchestrator.py` |
| Playwright bootstrap | `hybrid_scraper/bootstrapper.py` |
| Coles mobile API | `aisle_enrichment.py`, `mobile_products.py` |
| Coles token capture | `mobile_session.py`, `mobile_capture_addon.py`, `coles_ui_nav.py` |
| WW Iris | `woolworths_aisle_enrichment.py`, `woolworths_mobile_session.py`, `woolworths_queries/` |
| Ashfield production | `scrape_ashfield_deep.py`, `scrape_ashfield`, `watch_ashfield`, `ashfield_session.py` |
| Emulator helpers | `emulator_utils.py`, `process_lock.py` |
| Lake I/O | `lake/io.py` |
| DuckDB schema | `hybrid_scraper/storage.py` |
| ETL | `lake/etl/*`, `lake/SILVER_GOLD.md`, `lake/METHODS.md` |
| Errors | `hybrid_scraper/exceptions.py` |
| Logging | `hybrid_scraper/logging_config.py` |

---

*Generated from the codebase as of the Ashfield mobile-first scrape era. When endpoints or anti-bot behaviour drift, re-verify against live network captures (browser DevTools / mitm) and update this file alongside the modules above.*
