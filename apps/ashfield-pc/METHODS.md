# Methods wiki — Ashfield medallion & Personal Care dashboard

Technical reference for how space, bays, assortment overlap, and prices are derived.  
Code sources: [`lake/etl/bay_inference.py`](../../lake/etl/bay_inference.py), [`lake/etl/silver_to_gold.py`](../../lake/etl/silver_to_gold.py), [`lake/etl/sku_matcher.py`](../../lake/etl/sku_matcher.py), [`lake/etl/category_crosswalk.py`](../../lake/etl/category_crosswalk.py).

The dashboard’s first screen stays category-manager simple; this page is for analysts who need formulas and edge cases.

---

## 1. Bay share (primary space metric)

### Plain meaning

Of all shelf bays we can identify in the store, what fraction have at least one Personal Care product on them?

### Formula

Only **placed** SKUs count: `location_class = 'aisle'` and `bay_key` is not null.

\[
\text{bay\_count}(c) = \bigl|\{\,bay\_key \mid \text{SKU in category } c\,\}\bigr|
\]

\[
\text{store\_bay\_count} = \bigl|\{\,bay\_key \mid \text{any placed SKU in store}\,\}\bigr|
\]

\[
\text{pct\_store\_bays}(c) = \frac{\text{bay\_count}(c)}{\text{store\_bay\_count}}
\]

Implemented in `gold.category_space` (`silver_to_gold.py`).

### What it is not

- Not floor area in m²  
- Not comparable via raw `indoor_x` / `indoor_y` across Coles vs Woolworths (different maps)  
- Unplaced SKUs do not enter the numerator or the store-wide bay inventory used in the denominator for that retailer  

---

## 2. What is a `bay_key`?

\[
bay\_key = \text{“}\{aisle\}|\{bay\}”
\]

Example: `"6|2"` = aisle 6, bay 2.

| Banner | `aisle` | `bay` |
|--------|---------|-------|
| Woolworths | App `aisleNumber` | App `bayNumber` (native) |
| Coles | App aisle text | **Inferred** integer (see §3) |

---

## 3. Coles bay inference (detailed)

Coles mobile placement gives **aisle** and **indoor (x, y)** but **no bay number**. We invent bay ids so Coles can be counted like Woolworths for bay-share comparison.

Implementation: `infer_coles_bays()` in `lake/etl/bay_inference.py`.  
Note: a Woolworths “bay pitch” is calibrated for QA logging (`calibrate_ww_bay_pitch`) but **is not applied** to the Coles gap threshold today (it is only logged).

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

Each group is one side of one aisle.

### Step C — Dominant axis

For the group’s points \((x_i, y_i)\):

\[
\text{axis} =
\begin{cases}
x & \text{if } \operatorname{range}(x) \ge \operatorname{range}(y) \\
y & \text{otherwise}
\end{cases}
\]

Products are ordered along that axis (walk down the aisle).

### Step D — Gaps and threshold

Let \(p_{(1)} \le p_{(2)} \le \cdots \le p_{(n)}\) be positions on the chosen axis.

Consecutive gaps:

\[
d_i = p_{(i+1)} - p_{(i)}, \quad i = 1..n-1
\]

Positive gaps only: \(d_i > 10^{-6}\), sorted ascending → \(d'_{(1)} \le \cdots\).

**Same-bay scale** = median of the **smaller half** of those positive gaps (so rare long jumps between bay clusters don’t inflate the “typical” spacing):

\[
\text{small} = \bigl(d'_{(1)}, \ldots, d'_{(\max(1,\lfloor m/2 \rfloor))}\bigr), \quad m = |\{d'\}|
\]

\[
\text{median\_gap} = \operatorname{median}(\text{small})
\]

**New-bay threshold:**

\[
\tau =
\begin{cases}
4 \times \text{median\_gap} & \text{if median\_gap} > 0 \\
+\infty & \text{otherwise (single bay for the whole group)}
\end{cases}
\]

### Step E — Assign bay numbers

Walk products in axis order. Start `bay = 1`. When the gap to the previous product \(d > \tau\), increment `bay`.

\[
bay\_key = “\{aisle\}|\{bay\}”, \quad location\_class = aisle
\]

### Intuition

Products sitting close together on the map are treated as the same bay; a gap much larger than the usual within-bay spacing (4× the typical small gap) starts the next bay.

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
