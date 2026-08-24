# Methods wiki — Ashfield medallion lake

Technical reference for bay share, Coles bay inference, overlap, and prices across the **full store**.

The product UI is **category × location** (`apps/store-ci`). Personal Care is one category among many.

Code sources: [`lake/etl/bay_inference.py`](etl/bay_inference.py), [`lake/etl/silver_to_gold.py`](etl/silver_to_gold.py), [`lake/etl/sku_matcher.py`](etl/sku_matcher.py), [`lake/etl/category_crosswalk.py`](etl/category_crosswalk.py).


---

## 1. Bay share (primary space metric)

### Plain meaning

Of all shelf bays we can identify in the store, what fraction of bay-equivalents belongs to this category?

### Formula

Only **placed** SKUs count: `location_class = 'aisle'` and `bay_key` is not null.

For each bay, split 1.0 across categories by SKU share:

\[
\text{bay\_fraction}(b,c) = \frac{|\{SKU \in b \cap c\}|}{|\{SKU \in b\}|}
\]

\[
\text{bay\_count}(c) = \sum_b \text{bay\_fraction}(b,c)
\]

\[
\text{store\_bay\_count} = \bigl|\{\,bay\_key \mid \text{any placed SKU in store}\,\}\bigr|
\]

\[
\text{pct\_store\_bays}(c) = \frac{\text{bay\_count}(c)}{\text{store\_bay\_count}}
\]

Implemented in `gold.category_space` (`silver_to_gold.py`). Across categories, \(\sum_c \text{bay\_count}(c) = \text{store\_bay\_count}\).

### What it is not

- Not floor area in m²  
- Not comparable via raw `indoor_x` / `indoor_y` across Coles vs Woolworths (different maps)  
- Unplaced SKUs do not enter the numerator or the store-wide bay inventory used in the denominator for that retailer  
- Not majority-owner / winner-take-all (fractional slices preserve shared bays)  

---

## 2. What is a `bay_key`?

\[
bay\_key = \text{“}\{aisle\}|\{side\}|\{bay\}”
\]

Example: `"6|Right|2"` = aisle 6, right side, bay 2. Side is omitted when unknown (`"6|2"`).

| Banner | `aisle` | `side` | `bay` |
|--------|---------|--------|-------|
| Woolworths | App `aisleNumber` | App side when present | App `bayNumber` (native) |
| Coles | App aisle text | App `aisle_side` | **Inferred** integer per pin cluster (see §3) |

---

## 3. Coles bay inference (detailed)

Coles mobile placement gives **aisle** and **indoor (x, y)** but **no bay number**. We invent bay ids so Coles can be counted like Woolworths for bay-share comparison.

Implementation: `infer_coles_bays()` in `lake/etl/bay_inference.py`.  
Note: a Woolworths “bay pitch” is calibrated for QA logging (`calibrate_ww_bay_pitch`) but **must not** be applied to Coles (different map CRS).

### Step A — Eligibility

For each Coles SKU:

- Need non-empty `aisle_number` and numeric `indoor_x`, `indoor_y`  
- Else: `bay_key = null`; `location_class = unplaced` (no aisle) or `other` (aisle text but no coords)

### Step B — Grouping

Group eligible SKUs by:

\[
(\text{aisle}, \text{side})
\]

where `side` = `aisle_side` if present, else a placeholder `"_"`.

### Step C — Pin clusters

Coles already snaps many SKUs onto a small set of map pins per aisle side (often ~6–14). Cluster pins within Euclidean distance \(\varepsilon = 25\) indoor units (merge jitter only; real adjacent pins are typically 180–400 apart).

### Step D — Order and assign

Sort cluster centroids along the dominant axis of the group (larger of \(x\)-range vs \(y\)-range). Number bays \(1..N\) in that order. Every SKU in a cluster gets:

\[
bay\_key = “\{aisle\}|\{bay\}”, \quad location\_class = aisle
\]

### Intuition

Each distinct map pin ≈ one shelf bay. Gap-threshold merging of *products* under-clustered whole aisles when pins were already bay-level.

### Limitations

- Bay boundaries are heuristic, not planogram truth  
- Sensitive to missing coords and aisle-side quality  
- Cross-banner comparison uses **counts of bay_keys**, not physical metres  

---

## 4. Woolworths bays

`attach_ww_bay_keys()`: if both aisle and bay exist → `bay_key = "{aisle}|{bay}"`, `location_class = aisle`. Otherwise unplaced/other. No inference.

---

## 5. Assortment overlap (venn)

- **Matched:** fuzzy brand + name pairs (`sku_matcher.py`), Jaccard ≥ 0.72 within brand, WW L0 = Personal Care  
- **Coles only / WW only:** Personal Care SKUs not in those pairs  
- Exclusive counts **understate** true overlap (matching is incomplete)

---

## 6. Category unification

Coles catalogue slugs → Woolworths L0 names via SKU co-occurrence votes + optional overrides (`category_crosswalk.py`). Unmatched slugs stay `Unmapped` and are excluded from category space rollups.

---

## 7. Promo & price (dashboard)

- Snapshot from `gold.sku_facts` (`price_now`, `price_was`, `is_promo`)  
- Was→now ladder = medians on promo SKUs with both prices — **not** a multi-day time series unless history is joined later  

---

## Code map

| Topic | Module |
|-------|--------|
| Coles bay inference | `lake/etl/bay_inference.py` → `infer_coles_bays` |
| WW bay keys | `lake/etl/bay_inference.py` → `attach_ww_bay_keys` |
| Bay share tables | `lake/etl/silver_to_gold.py` → `gold.category_space` |
| SKU matching | `lake/etl/sku_matcher.py` |
| Crosswalk votes | `lake/etl/category_crosswalk.py` |
| Dashboard export | `scripts/export_pc_dashboard_data.py` |
