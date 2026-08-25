# QA: Store-CI subcategory (L1) full audit

_Generated 2026-08-24T22:54:41.788387+00:00_

## Verdict

Subcategory competitive views are **systemically misaligned**, not broken in a single aisle. L0 (category) rollup was fixed; **L1 export still keys off native gold parents**, Coles L1 coverage is ~47%, and coarse crosswalk L1 overrides force Coles meat into `Meat`/`Deli` instead of WW labels (`Poultry`, `Seafood`, …).

## Headline numbers

| Metric | Value |
|--------|-------|
| Coles SKUs with subcategory | 16415/28558 (57.5%) |
| WW SKUs with subcategory | 22960/22997 (99.8%) |
| Native (parent, sub) pairs | 286 (both=112, one-sided=174) |
| Shared-parent pairs | 234 (both=126, one-sided=108) |
| Parent splits | 32 |
| Near-duplicate label groups | 10 |

## Systemic issues (fix)

- **[PASS]** `l1_parents_use_shared_label` (critical): 0 store-CI L1 parents still use native gold keys (expect 0 after export rollup)
- **[PASS]** `l1_parent_splits_in_store_ci` (critical): Store-CI L1 must use shared parents so native parent splits merge (32 gold-level splits remain for ETL)
- **[PASS]** `coles_subcategory_coverage` (high): Coles subcategory coverage 57.5% (target ≥55% after shared-L0 matcher fix)
- **[PASS]** `coarse_crosswalk_l1_overrides` (high): 0 crosswalk overrides stamp a fixed L1 (blocks fine WW labels like Poultry)
- **[PASS]** `meat_coles_has_poultry_or_fine_l1` (high): Coles should have Poultry (or other fine WW L1) SKUs under Meat Seafood & Deli after matcher fix

## Root causes (ordered)

### 1. L1 export does not use shared-parent rollup (critical)

`scripts/export_store_ci_data.py` builds L1 from raw `unified_category` + `unified_subcategory`. L0 already rolls WW natives via `WW_GOLD_TO_SHARED`; L1 does not. Effect:

- Coles meat under parent `Meat Seafood & Deli`
- WW poultry under parent `Poultry, Meat & Seafood`
- Same subcategory label `Meat` appears as two one-sided rows instead of one both-banner row

Store-CI L1 parents needing rollup:


### 2. Parent splits (critical)

Same L1 label under multiple native parents that share one glossary department. Export without rollup double-counts aisles and breaks joins.

- **Fruit & Vegetables** / `Fruit`:
  - `Fruit & Veg`: Coles=0, WW=117
  - `Fruit & Vegetables`: Coles=103, WW=0
- **Fruit & Vegetables** / `Salad`:
  - `Fruit & Veg`: Coles=0, WW=12
  - `Fruit & Vegetables`: Coles=68, WW=0
- **Fruit & Vegetables** / `Vegetables`:
  - `Fruit & Veg`: Coles=0, WW=256
  - `Fruit & Vegetables`: Coles=143, WW=0
- **Liquor** / `Beer`:
  - `Beer, Wine & Spirits`: Coles=0, WW=2
  - `Liquor`: Coles=322, WW=0
- **Liquor** / `Spirits`:
  - `Beer, Wine & Spirits`: Coles=0, WW=1
  - `Liquor`: Coles=518, WW=0
- **Meat Seafood & Deli** / `Biscuits & Crackers`:
  - `Meat Seafood & Deli`: Coles=7, WW=0
  - `Poultry, Meat & Seafood`: Coles=0, WW=6
- **Meat Seafood & Deli** / `Cheese`:
  - `Meat Seafood & Deli`: Coles=29, WW=0
  - `Poultry, Meat & Seafood`: Coles=0, WW=14
- **Meat Seafood & Deli** / `Deli Meats`:
  - `Deli`: Coles=0, WW=114
  - `Meat Seafood & Deli`: Coles=100, WW=0
  - `Poultry, Meat & Seafood`: Coles=0, WW=61
- **Meat Seafood & Deli** / `Deli Specialties`:
  - `Deli`: Coles=0, WW=57
  - `Meat Seafood & Deli`: Coles=28, WW=0
  - `Poultry, Meat & Seafood`: Coles=0, WW=11
- **Meat Seafood & Deli** / `Entertaining Meats & Platters`:
  - `Deli`: Coles=0, WW=7
  - `Meat Seafood & Deli`: Coles=3, WW=0
- **Meat Seafood & Deli** / `Ham, Bacon & Smallgoods`:
  - `Deli`: Coles=0, WW=4
  - `Meat Seafood & Deli`: Coles=48, WW=0
  - `Poultry, Meat & Seafood`: Coles=0, WW=53
- **Meat Seafood & Deli** / `Meat`:
  - `Meat Seafood & Deli`: Coles=263, WW=0
  - `Poultry, Meat & Seafood`: Coles=0, WW=136
- **Meat Seafood & Deli** / `Poultry`:
  - `Meat Seafood & Deli`: Coles=163, WW=0
  - `Poultry, Meat & Seafood`: Coles=0, WW=133
- **Meat Seafood & Deli** / `Ready to Cook Meats`:
  - `Meat Seafood & Deli`: Coles=5, WW=0
  - `Poultry, Meat & Seafood`: Coles=0, WW=26
- **Meat Seafood & Deli** / `Seafood`:
  - `Meat Seafood & Deli`: Coles=124, WW=0
  - `Poultry, Meat & Seafood`: Coles=0, WW=72
- **Pantry** / `Biscuits & Crackers`:
  - `Pantry`: Coles=383, WW=13
  - `Snacks & Confectionery`: Coles=0, WW=257
- **Pantry** / `Chips`:
  - `Pantry`: Coles=267, WW=16
  - `Snacks & Confectionery`: Coles=0, WW=129
- **Pantry** / `Confectionery`:
  - `Pantry`: Coles=782, WW=19
  - `Snacks & Confectionery`: Coles=0, WW=436
- **Pantry** / `European, UK & Irish`:
  - `Pantry`: Coles=10, WW=31
  - `Snacks & Confectionery`: Coles=0, WW=1
- **Pantry** / `Gum, Mints & Lozenges`:
  - `Pantry`: Coles=2, WW=0
  - `Snacks & Confectionery`: Coles=0, WW=1
- **Pantry** / `Snacks`:
  - `Pantry`: Coles=348, WW=279
  - `Snacks & Confectionery`: Coles=0, WW=53
- **Personal Care** / `Beauty Tools & Nails`:
  - `Beauty`: Coles=0, WW=4
  - `Personal Care`: Coles=35, WW=85
- **Personal Care** / `Confectionery`:
  - `Health & Wellness`: Coles=0, WW=2
  - `Personal Care`: Coles=1, WW=0
- **Personal Care** / `Cosmetics`:
  - `Beauty`: Coles=0, WW=37
  - `Personal Care`: Coles=165, WW=308
- **Personal Care** / `Diet & Sports Nutrition`:
  - `Health & Wellness`: Coles=0, WW=100
  - `Personal Care`: Coles=7, WW=0
- **Personal Care** / `First Aid & Medicinal`:
  - `Health & Wellness`: Coles=0, WW=537
  - `Personal Care`: Coles=441, WW=5
- **Personal Care** / `Hair Care`:
  - `Beauty`: Coles=0, WW=6
  - `Personal Care`: Coles=852, WW=1168
- **Personal Care** / `Health Foods`:
  - `Health & Wellness`: Coles=0, WW=629
  - `Personal Care`: Coles=2, WW=0
- **Personal Care** / `Shower, Bath & Body`:
  - `Health & Wellness`: Coles=0, WW=1
  - `Personal Care`: Coles=514, WW=607
- **Personal Care** / `Skincare & Body`:
  - `Beauty`: Coles=0, WW=7
  - `Personal Care`: Coles=465, WW=627
- **Personal Care** / `Snacks`:
  - `Health & Wellness`: Coles=0, WW=59
  - `Personal Care`: Coles=0, WW=2
- **Personal Care** / `Vitamins`:
  - `Health & Wellness`: Coles=0, WW=401
  - `Personal Care`: Coles=456, WW=47

### 3. Coarse crosswalk L1 overrides (high)

`lake/ref/category_crosswalk_overrides.csv` stamps a single L1 for whole Coles slugs. Matcher can only override when `subcategory_hint` L0 matches; many meat SKUs keep override L1.


Meat inventory today: Coles L1 = `Meat` + `Deli` only; WW = `Poultry`, `Seafood`, `Deli Meats`, …

### 4. Coles L1 coverage ~47% (high)

WW Iris breadcrumb L1 ≈ 100%. Coles relies on SKU match + inference. Zero coverage depts: Liquor, Fruit & Vegetables, International Foods.

### 5. Residual one-sided L1 after rollup (expected + label debt)

Even with shared parents, many WW-only L1s remain (true assortment: Everyday Market, Lunch Box; or label mismatch: Coles `Deli` vs WW `Deli Meats`). Near-dupes need a subcategory glossary.

## Per shared parent

| Parent | L1 rows | Both | Coles-only | WW-only | Coles null L1 | Splits |
|--------|---------|------|------------|---------|---------------|--------|
| Baby | 12 | 5 | 0 | 7 | 13 | 0 |
| Bakery | 8 | 4 | 3 | 1 | 340 | 0 |
| Cleaning & Maintenance | 29 | 10 | 0 | 19 | 441 | 0 |
| Dairy Eggs & Fridge | 26 | 18 | 2 | 6 | 796 | 0 |
| Dinner | 5 | 0 | 0 | 5 | 0 | 0 |
| Drinks | 13 | 8 | 1 | 4 | 64 | 0 |
| Electronics | 4 | 0 | 0 | 4 | 0 | 0 |
| Everyday Market | 21 | 0 | 0 | 21 | 0 | 0 |
| Frozen | 16 | 13 | 2 | 1 | 676 | 0 |
| Fruit & Vegetables | 3 | 3 | 0 | 0 | 478 | 3 |
| Home & Lifestyle | 14 | 7 | 1 | 6 | 1430 | 0 |
| International Foods | 5 | 0 | 1 | 4 | 491 | 0 |
| Liquor | 3 | 2 | 1 | 0 | 2227 | 2 |
| Lunch Box | 6 | 0 | 0 | 6 | 0 | 0 |
| Meat Seafood & Deli | 10 | 10 | 0 | 0 | 654 | 10 |
| Pantry | 38 | 28 | 0 | 10 | 3431 | 6 |
| Personal Care | 17 | 14 | 0 | 3 | 1032 | 11 |
| Pet | 4 | 4 | 0 | 0 | 70 | 0 |

## Systemic fix plan (do together, not aisle-by-aisle)

1. **Export:** Build L1 on `shared_label_for_gold(unified_category)` + subcategory; merge gold_keys; point SKU `subcategory_id` / `parent_category` at shared parent ids.
2. **Crosswalk:** Clear `woolworths_subcategory` on overrides (`meat-seafood`, `deli`, `chips-…`); keep L0 only. Let matcher/inference assign WW L1.
3. **ETL re-run:** bronze→silver→gold after override change so Coles meat gets Poultry/Seafood where matched.
4. **Subcategory glossary (optional phase 2):** map near-dupes (`Deli`↔`Deli Meats`, `Coffee`↔`Tea & Coffee`).
5. **Coverage push (phase 2):** improve Coles L1 for Fruit, Liquor, International, Home & Lifestyle.
6. **Gate:** `scripts/audit_store_ci_subcategories.py` exit 0 before treating store-CI L1 as trustworthy.

## Commands

```bash
.venv/bin/python scripts/audit_store_ci_subcategories.py
.venv/bin/python scripts/audit_store_ci_subcategories.py --json lake/eval/subcategory_audit.json
```

## Sign-off

- [ ] Critical issues PASS (L1 shared parents, zero parent splits)
- [ ] Meat/poultry: Coles SKUs appear under fine WW L1s where matches exist
- [ ] Coles L1 coverage ≥ 70% (or waived with documented dept exceptions)
- [ ] Dashboard subcategory grain smoke-tested on Meat, Pantry, Personal Care
