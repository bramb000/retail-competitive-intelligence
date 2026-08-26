# Retail CI

**Grain:** category × location (Ashfield is the only live location today).

Full-store Coles ↔ Woolworths competitive intelligence in shared commercial
language — aisle space, dominance, price, everyday staples, and macrospace layout.
Empty categories show as *data still filling in* while coverage completes.

## Run

```bash
./run_store_ci
```

→ <http://localhost:5174> · exports `apps/store-ci/public/data/store_ci.json`

After scrapers / ETL:

```bash
.venv/bin/python scrape_ashfield_deep.py --phase etl
./run_store_ci
```

## Boards (at the active location)

- **Overview** — aisle space and range; bilingual labels
- **Dominance** — who owns each category (bay → assortment)
- **Price** — category median gaps
- **Staples** — everyday products that shape price perception
- **Macrospace** — store layout by aisle
- Category drill-in for any department at this location
- **Methods** — plain-language guide to how numbers are built

Deep links use hash routes, e.g. `#/dominance`, `#/overview/dept/<id>`,
`#/methods/grain`.

## Deprecated

`apps/ashfield-pc` (Personal Care–only) is superseded.
`./run_pc_dashboard` redirects here.
