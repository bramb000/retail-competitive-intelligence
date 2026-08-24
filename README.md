# Retail Competitive Intelligence

Full-store Coles ↔ Woolworths competitive intelligence — category × location scoreboards, pricing, shelf space, and assortment overlap.

**Live dashboard:** [planoverse.github.io/retail-competitive-intelligence](https://planoverse.github.io/retail-competitive-intelligence/)

## Local development

```bash
# Export latest gold data (requires lake/gold/ashfield_compare.duckdb from ETL)
.venv/bin/python scripts/export_store_ci_data.py

# Run the Vite dashboard
./run_store_ci
```

→ http://localhost:5174

After scrapers / ETL:

```bash
.venv/bin/python scrape_ashfield_deep.py --phase etl
./run_store_ci
```

## Updating the live site

Dashboard data is a static JSON export committed to the repo. After ETL:

```bash
.venv/bin/python scripts/export_store_ci_data.py
git add apps/store-ci/public/data/store_ci.json
git commit -m "Update dashboard export"
git push rci master
```

GitHub Actions rebuilds and deploys to Pages automatically.

## Documentation

- [SCRAPING.md](SCRAPING.md) — how data is scraped (end-to-end)
- [lake/SILVER_GOLD.md](lake/SILVER_GOLD.md) — bronze → silver → gold transforms
- [apps/store-ci/README.md](apps/store-ci/README.md) — dashboard scoreboards
