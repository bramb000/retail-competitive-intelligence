# Macro store competitive intelligence

**Grain:** category × location (Ashfield is the only live location today).

Full-store Coles ↔ Woolworths scoreboards in shared commercial language — not a single-category
app. Scrapers can still be filling bronze; empty categories show as *awaiting scrape*.

## Run

```bash
./run_store_ci
```

→ http://localhost:5174 · exports `apps/store-ci/public/data/store_ci.json`

After scrapers / ETL:

```bash
.venv/bin/python scrape_ashfield_deep.py --phase etl
./run_store_ci
```

## Scoreboards (at the active location)

- **Categories** — full glossary taxonomy + observed gold aisles; bilingual labels
- **Dominance** — who owns each category (bay → assortment)
- **Price race** — category median gaps
- **Known value** — staples that shape store price perception
- Category drill-in for any department at this location

## Deprecated

`apps/ashfield-pc` (Personal Care-only) is superseded. `./run_pc_dashboard` redirects here.
