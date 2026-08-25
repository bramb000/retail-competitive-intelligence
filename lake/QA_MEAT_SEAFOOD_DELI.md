# QA plan: Woolworths Meat / Seafood / Deli in store-CI

## Problem statement

Store-CI shows **Meat Seafood & Deli** with Coles SKUs but **Woolworths SKUs = 0**, even though the Ashfield scrape captured hundreds of meat and deli products.

This is a **taxonomy / export mismatch**, not a scrape gap.

| Layer | Coles | Woolworths |
|-------|-------|------------|
| Native L0 | `Meat Seafood & Deli` (via crosswalk) | `Poultry, Meat & Seafood`, `Deli` |
| Glossary `ww_label` | — | `Meat Seafood & Deli` (not a WW gold key) |
| Store-CI rollup | ✓ attaches to shared dept | ✗ no match → `ww_skus=0` |

## Automated eval

Run after every ETL + store-CI export:

```bash
.venv/bin/python scripts/eval_store_ci_category_rollup.py
.venv/bin/python scripts/eval_meat_seafood_deli.py
.venv/bin/python scripts/eval_store_ci_category_rollup.py --json lake/eval/store_ci_category_rollup.json
```

Exit code **0** = all critical checks pass.

Rollup is implemented in `lake/etl/category_glossary.py` (`WW_GOLD_TO_SHARED`) and applied in `scripts/export_store_ci_data.py`.

### Critical checks

| ID | What it verifies |
|----|------------------|
| `silver_ww_meat_present` | ≥400 SKUs with L0 `Poultry, Meat & Seafood` in latest silver |
| `silver_ww_deli_present` | ≥100 SKUs with L0 `Deli` in latest silver |
| `bronze_categories_populated` | Bronze PDP rows carry Iris L0 categories for meat and deli |
| `gold_ww_native_l0s` | ≥600 combined WW SKUs in gold for native L0s |
| `glossary_ww_label_matches_gold` | Glossary `ww_label` maps to a key that exists in WW gold |
| `store_ci_unified_dept_has_ww_skus` | Shared dept in `store_ci.json` has `ww_skus ≥ 600` |

### Informational checks

| ID | What it verifies |
|----|------------------|
| `venn_not_coles_only_for_shared_name` | `category_venn.csv` does not list shared name as `coles_only` |
| `counter_skus_documented` | Counter SKUs at `Deli Department` are counted (excluded from bay share) |

## Manual QA checklist

### 1. Scrape integrity (bronze)

- [ ] Latest bronze run exists: `lake/bronze/woolworths/1213/<run_id>/`
- [ ] `product_details.jsonl` has rows with `summary.categories[0]` = `Poultry, Meat & Seafood` or `Deli`
- [ ] Spot-check 3 meat SKUs and 3 deli SKUs in bronze — names, prices, and categories look sane
- [ ] No systematic empty `categories` on Iris PDP rows (would indicate enrichment regression)

**Commands:**

```bash
# Sample bronze categories
.venv/bin/python - <<'PY'
from lake.io import iter_jsonl, latest_bronze_dir
b = latest_bronze_dir("woolworths", "1213")
p = b / "product_details.jsonl"
for row in iter_jsonl(p):
    cats = (row.get("summary") or {}).get("categories") or []
    if cats and cats[0] in ("Poultry, Meat & Seafood", "Deli"):
        print(row.get("product_id"), cats[:3])
        break
PY
```

### 2. Silver ETL (native L0 preserved)

- [ ] Latest silver stamp matches post-scrape ETL (not stale pre-reset data)
- [ ] `lake/silver/<stamp>/woolworths/products.jsonl` has `unified_category` = `Poultry, Meat & Seafood` (~528) and `Deli` (~182)
- [ ] Coles silver uses unified `Meat Seafood & Deli` via crosswalk — unchanged

**Commands:**

```bash
.venv/bin/python scripts/eval_meat_seafood_deli.py
```

### 3. Gold + venn

- [ ] `lake/gold/ashfield_compare.duckdb` → `gold.sku_facts` counts match silver totals for WW native L0s
- [ ] `lake/gold/exports/category_venn.csv`:
  - Before fix: `Meat Seafood & Deli` = `coles_only`; `Deli` + `Poultry, Meat & Seafood` = `ww_only`
  - After fix: shared dept should appear as `both` or single merged row

**Commands:**

```bash
.venv/bin/python - <<'PY'
import duckdb
c = duckdb.connect("lake/gold/ashfield_compare.duckdb", read_only=True)
print(c.execute("""
  SELECT retailer, unified_category, count(*)
  FROM gold.sku_facts
  WHERE unified_category IN ('Deli','Poultry, Meat & Seafood','Meat Seafood & Deli')
  GROUP BY 1,2 ORDER BY 1,2
""").fetchall())
PY
```

### 4. Glossary / crosswalk

- [ ] `lake/ref/category_glossary.csv` rows for meat/deli: decide whether `ww_label` stays unified or splits to native L0s
- [ ] If unified: export must roll `Deli` + `Poultry, Meat & Seafood` into `Meat Seafood & Deli` for WW
- [ ] If split: store-CI shows two WW-only departments (acceptable but different UX)

**Files:**

- `lake/ref/category_glossary.csv` — shared department labels
- `lake/ref/category_crosswalk_overrides.csv` — Coles → `Meat Seafood & Deli`
- `scripts/export_store_ci_data.py` — `gloss_for()` / `gold_by_shared` rollup logic

### 5. Store-CI export + dashboard

- [ ] Re-run export after ETL: `.venv/bin/python scripts/export_store_ci_data.py`
- [ ] `apps/store-ci/public/data/store_ci.json` `generated_at` is newer than silver stamp
- [ ] Department **Meat Seafood & Deli** shows `ww_skus > 0` and reasonable `coles_skus`
- [ ] Bay share / placement views: note counter SKUs at `Deli Department` may show `location_class=other` (87 SKUs) — separate follow-up

**Dashboard smoke test:**

1. Open store-CI → Ashfield comparison
2. Find **Meat Seafood & Deli** department row
3. Confirm Woolworths SKU count ≈ 710 (528 meat + 182 deli, minus any dedupe)
4. Drill into sample WW SKUs — names match known meat/deli products

### 6. Regression guards

- [ ] Other WW departments still populate (not broken by rollup change)
- [ ] Coles meat/deli counts unchanged
- [ ] Eval script added to CI or pre-export hook (optional)

## Remediation options

### Option A — Export rollup (implemented)

`WW_GOLD_TO_SHARED` in `lake/etl/category_glossary.py` maps WW Iris L0 names to shared departments. Export uses `rollup_gold_by_shared()` so native keys attach to the correct shared aisle.

Current rollups:

| Shared department | WW native gold keys |
|-------------------|---------------------|
| Meat Seafood & Deli | `Deli`, `Poultry, Meat & Seafood` |
| Fruit & Vegetables | `Fruit & Veg` |
| Liquor | `Beer, Wine & Spirits` |
| Pantry | `Snacks & Confectionery` |
| Personal Care | `Health & Wellness`, `Beauty` |

### Option B — Glossary split

Set glossary `ww_label` to native L0 strings (two rows or comma-separated mapping). Store-CI shows WW-native names instead of one shared aisle.

### Option C — Silver crosswalk for WW

Add WW crosswalk overrides so `unified_category` becomes `Meat Seafood & Deli` at silver time (mirrors Coles). Higher blast radius across all gold consumers.

## Sign-off criteria

Release is OK when:

1. `scripts/eval_meat_seafood_deli.py` exits **0**
2. Manual dashboard check confirms **Meat Seafood & Deli** has WW SKUs ≈ silver combined count
3. No regression in total WW SKU count or other department rollups
4. `category_venn.csv` reflects aligned taxonomy (shared dept not `coles_only`)

## Runbook (full refresh)

```bash
# 1. ETL (if silver/gold stale)
.venv/bin/python scrape_ashfield_deep.py --phase etl

# 2. Export store-CI
.venv/bin/python scripts/export_store_ci_data.py

# 3. Eval
.venv/bin/python scripts/eval_meat_seafood_deli.py --json lake/eval/meat_seafood_deli.json

# 4. Restart dashboard if running
./run_pc_dashboard   # or refresh browser cache
```

## Known limitations

- **Counter placement**: ~87 meat/deli SKUs use aisle `Deli Department` → `location_class=other`; excluded from bay-share numerators until placement rules are updated.
- **Stale JSON**: If export was run before the Aug 24 scrape completed, regenerate export before QA.
- **Venn semantics**: Until taxonomy fix lands, expect `coles_only` / `ww_only` split for meat categories — eval documents this as `high` severity, not blocking scrape QA.
