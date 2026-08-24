# Ashfield silver & gold: transformation and visualisation

This note describes how Ashfield bronze scrape data becomes **silver** (clean product + placement facts) and **gold** (comparison tables), and how to visualise the gold outputs.

Ashfield stores:

| Banner | Store ID |
|--------|----------|
| Coles | `791` |
| Woolworths | `1213` |

---

## Medallion layout

```
lake/
├── bronze/          # raw scrape JSONL (immutable per run)
│   ├── coles/791/<run_id>/products_list.jsonl
│   └── woolworths/1213/<run_id>/
│       ├── search_pages.jsonl
│       └── product_details.jsonl
├── silver/<stamp>/  # normalised product rows + category venn
│   ├── coles/products.jsonl
│   ├── woolworths/products.jsonl
│   ├── sku_matches.jsonl         # fuzzy Coles↔WW pairs used for learning
│   ├── category_crosswalk_used.csv
│   ├── category_venn.jsonl
│   └── manifest.json
├── gold/
│   ├── ashfield_compare.duckdb   # analytical tables
│   ├── exports/*.csv             # spreadsheet-ready copies
│   └── manifest.json
└── ref/
    ├── category_crosswalk_recommended.csv  # SKU-match learned (regenerated each ETL)
    └── category_crosswalk_overrides.csv    # manual wins over recommendations
```


Run ETL after scrapes have written bronze:

```bash
.venv/bin/python scrape_ashfield_deep.py --phase etl
```

That calls `run_bronze_to_silver()` then `run_silver_to_gold(silver)` (`lake/etl/bronze_to_silver.py`, `lake/etl/silver_to_gold.py`).

---

## Bronze → silver

**Goal:** one row per SKU per banner, shared field names, unified category taxonomy, and a comparable bay key for floor-space analysis.

### Inputs

| Banner | Preferred bronze | Fallback |
|--------|------------------|----------|
| Coles | Latest `products_list.jsonl` | — |
| Woolworths | Latest Iris `product_details.jsonl` cards | Website `search_pages.jsonl` parsed fields |

### Category unification (SKU-based)

`unified_category` is always a **literal Woolworths L0** name (or `Unmapped`). No invented labels.

1. **Fuzzy match** Coles bronze SKUs to WW Iris `product_details` cards (`lake/etl/sku_matcher.py`): same normalised brand, token Jaccard on name ≥ 0.72. WW `search_pages` are **not** used for learning (almost all `Everyday Market`).
2. **Vote** the Coles native slug on each matched SKU that best token-aligns with that pair’s WW L0 (avoids multi-tag pollution, e.g. milk tagged `bakery`+`dairy-eggs-fridge`). Keep a mapping when support ≥ 3, or ≥ 2 with vote share ≥ 60%.
3. Write recommendations to `lake/ref/category_crosswalk_recommended.csv`. Manual rows in `category_crosswalk_overrides.csv` replace by `coles_category_slug`.
4. Effective crosswalk maps Coles slugs → WW L0 (many-to-one). Unmatched Coles slugs stay `Unmapped` until more Iris grocery coverage or an override.

Audit artefacts per silver run: `sku_matches.jsonl`, `category_crosswalk_used.csv`.

### Coles transform

For each mobile `products/list` item:

1. Extract identity (`id`, `name`, `brand`), pricing (`now` / `was` / unit), stock, and first `locations[]` entry (aisle, side, facing, order, indoor x/y).
2. Look up native Coles catalogue categories from `data/coles_catalogue_categories.csv.csv`.
3. Map those slugs onto Woolworths L0 names via the effective crosswalk (first matching slug wins). Unmatched → `Unmapped`.
4. Set `location_class` to `aisle` when aisle text is real (not “see in store” / empty).
5. **Infer bays** from indoor coordinates (`infer_coles_bays`): cluster products along the dominant aisle axis; large gaps start a new bay. Writes `inferred_bay` and `bay_key` (`"{aisle}|{bay}"`).

Coles does not expose a native bay number; bay keys are inferred so category space can be compared to Woolworths.

### Woolworths transform

Prefer Iris `productDetailsPage` cards:

1. Parse via `product_summary()` (price in dollars, aisle/bay, breadcrumb categories, promo flags).
2. Use breadcrumb level-0 as `unified_category` (skip “Specials” into next level when needed) — already a WW native name.
3. `location_class = aisle` when a bay number exists; else `unplaced`.
4. Attach `bay_key` from native aisle + bay (`attach_ww_bay_keys`).

If Iris PDP bronze is empty, silver falls back to search-page parsed rows (weaker placement; do not use for crosswalk learning).


### Shared silver product schema (JSONL)

| Field | Meaning |
|-------|---------|
| `retailer`, `store_id` | Banner + Ashfield store |
| `retailer_product_id`, `name`, `clean_brand` | Identity |
| `native_categories` | Banner-native category list |
| `unified_category`, `unified_subcategory` | Cross-banner taxonomy |
| `price_now`, `price_was`, `unit_price` | Pricing |
| `is_promo`, `promo_type`, `promo_label` | Promo flags |
| `stock_status` | Availability |
| `aisle_number`, `aisle_side`, `bay_number`, `aisle_facing`, `aisle_order` | Placement |
| `indoor_x`, `indoor_y` | Store map coordinates |
| `location_class` | `aisle` / `unplaced` / `other` |
| `bay_key` | Comparable bay id (`aisle\|bay`) |
| `inferred_bay` | Coles-only inferred bay |

### Category venn

`category_venn.jsonl` lists unified categories as:

- `both` — present at Coles and Woolworths Ashfield
- `coles_only` / `ww_only` — one banner only

Used for assortment overlap charts.

### Important caveats

- **Coordinates are not cross-banner comparable.** Coles and Woolworths use different indoor maps; only **bay counts / share of store bays** after inference are meant for comparison, not raw x/y overlays.
- WW “bay pitch” is calibrated for QA logging; it is **not** applied to Coles CRS.
- Silver is rebuilt from the **latest** bronze run dirs unless you pass explicit paths into `run_bronze_to_silver`.
- **Category recommendations need WW Iris grocery coverage.** Thin or skewed Iris PDP samples leave many Coles slugs `Unmapped`; thicken Iris or add rows to `category_crosswalk_overrides.csv`.

---

## Silver → gold

**Goal:** DuckDB tables and CSV exports for Ashfield banner comparison (pricing, promo intensity, floor space by category).

Database: `lake/gold/ashfield_compare.duckdb`  
Schema: `gold.*`

### Tables

#### `gold.sku_facts`

Union of silver Coles + Woolworths product JSONL (`UNION ALL BY NAME`). Grain: one row per retailer SKU in the silver run.

#### `gold.category_pricing`

Per `(retailer, unified_category)`:

| Column | Definition |
|--------|------------|
| `sku_count` | SKUs in category |
| `median_price`, `mean_price` | On `price_now` |
| `pct_on_promo` | % of SKUs with `is_promo` |
| `median_discount` | Median `(was − now) / was` when was &gt; now |

Excludes `Unmapped`.

#### `gold.category_space`

Per `(retailer, unified_category)` among **placed** SKUs (`location_class = 'aisle'` and `bay_key` set):

| Column | Definition |
|--------|------------|
| `bay_count` | Distinct bays for that category |
| `store_bay_count` | Distinct bays for the whole store |
| `pct_store_bays` | Category’s share of store bay inventory |
| `placed_skus` | Placed SKU count |
| `facing_sum` | Sum of aisle facings (when present) |

This is the main **space** comparison signal (not map overlays).

#### `gold.category_venn`

Loaded from silver `category_venn.jsonl` (`side`, `unified_category`).

#### `gold.sku_matches`

Fuzzy Coles↔WW product pairs from silver `sku_matches.jsonl` (score, brands, native categories, WW L0/L1). Grain: one accepted match pair.

#### `gold.category_crosswalk`

Effective Coles slug → Woolworths L0 mapping used for that silver run (`category_crosswalk_used.csv`), including `source` (`recommended` / `override`) and vote evidence columns.

#### `gold.banner_compare`

Full outer join of Coles vs Woolworths category pricing + space on `unified_category`:

| Column | Use |
|--------|-----|
| `coles_skus` / `ww_skus` | Assortment depth |
| `coles_median_price` / `ww_median_price` | Price level |
| `coles_pct_promo` / `ww_pct_promo` | Promo intensity |
| `coles_bay_count` / `ww_bay_count` | Absolute space |
| `coles_pct_store_bays` / `ww_pct_store_bays` | Relative space |

### CSV exports

Written under `lake/gold/exports/`:

- `category_pricing.csv`
- `category_space.csv`
- `category_venn.csv`
- `banner_compare.csv`
- `sku_matches.csv`
- `category_crosswalk.csv`

---

## Visualisation guide

Gold is designed for charts, not map heatmaps. Prefer DuckDB SQL or the CSV exports in Excel / Observable / Streamlit / Metabase.

### 1. Assortment overlap (venn)

**Data:** `gold.category_venn` or `category_venn.csv`

- Counts by `side`: both vs Coles-only vs WW-only.
- Bar chart of category counts, or a two-circle Venn with those three set sizes.
- Optional table of category names filtered by `side`.

```sql
SELECT side, count(*) AS categories
FROM gold.category_venn
GROUP BY 1;
```

### 2. Price by category (side-by-side)

**Data:** `gold.banner_compare`

- Grouped bar: `coles_median_price` vs `ww_median_price` for categories present in both.
- Scatter: Coles median (x) vs WW median (y); y = x reference line shows who is dearer.
- Filter: `coles_skus IS NOT NULL AND ww_skus IS NOT NULL`.

```sql
SELECT unified_category, coles_median_price, ww_median_price,
       ww_median_price - coles_median_price AS ww_minus_coles
FROM gold.banner_compare
WHERE coles_median_price IS NOT NULL AND ww_median_price IS NOT NULL
ORDER BY abs(ww_minus_coles) DESC;
```

### 3. Promo intensity

**Data:** `banner_compare` or `category_pricing`

- Side-by-side bars of `coles_pct_promo` vs `ww_pct_promo` by category.
- Store-level average from `category_pricing` weighted by `sku_count` if needed.

### 4. Floor space (bay share)

**Data:** `gold.category_space` / `banner_compare`

- Stacked or grouped bars of `pct_store_bays` by category for each retailer (sums ≈ 1.0 per banner if all placed SKUs are categorised).
- Compare `coles_pct_store_bays` vs `ww_pct_store_bays` for overlapping categories — answers “who gives more bay share to Dairy / Pantry / …”.

Do **not** plot Coles and WW `indoor_x`/`indoor_y` on one map; CRS differ.

### 5. Assortment depth

**Data:** `banner_compare`

- Dual-axis or paired bars: `coles_skus` vs `ww_skus`.
- Ratio `ww_skus / coles_skus` as a slope chart or heatmap column.

### 6. Placement quality QA

**Data:** `gold.sku_facts`

```sql
SELECT retailer, location_class, count(*) AS n
FROM gold.sku_facts
GROUP BY 1, 2;
```

High `unplaced` share means silver/Iris coverage is still thin for that banner — charts on space will understate that retailer until more aisle-enriched bronze exists.

### Suggested dashboard layout

| Panel | Source | Chart |
|-------|--------|--------|
| Overlap summary | `category_venn` | Counts by side |
| Price gap leaderboard | `banner_compare` | Sorted bar of price delta |
| Promo heatmap | `category_pricing` | Retailer × category, colour = `% promo` |
| Space share | `banner_compare` | Grouped bar `% store bays` |
| Coverage QA | `sku_facts` | Stacked bar placed vs unplaced |

### Querying DuckDB

```bash
.venv/bin/python -c "
import duckdb
c = duckdb.connect('lake/gold/ashfield_compare.duckdb')
print(c.execute('SELECT * FROM gold.banner_compare LIMIT 10').df())
"
```

Or open `lake/gold/exports/banner_compare.csv` directly in a spreadsheet.

---

## End-to-end checklist

1. Scrape Coles + Woolworths into bronze (watchdog / `./scrape_ashfield`).
2. Confirm bronze has Coles `products_list.jsonl` and WW `product_details.jsonl` (Iris) for placement quality.
3. Run `--phase etl`.
4. Check `lake/silver/<stamp>/manifest.json` row counts and `lake/gold/manifest.json`.
5. Build charts from `gold.banner_compare` + `gold.category_venn`; use `sku_facts` only for QA or SKU drill-down.

---

## Code map

| Step | Module |
|------|--------|
| Bronze helpers / paths | `lake/io.py` |
| Bronze → silver | `lake/etl/bronze_to_silver.py` |
| Bay inference | `lake/etl/bay_inference.py` |
| Fuzzy SKU matcher | `lake/etl/sku_matcher.py` |
| Category crosswalk (recommend + overrides) | `lake/etl/category_crosswalk.py`, `lake/ref/category_crosswalk_*.csv` |
| Silver → gold | `lake/etl/silver_to_gold.py` |
| CLI entry | `scrape_ashfield_deep.py --phase etl` |
