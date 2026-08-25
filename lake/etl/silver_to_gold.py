"""Silver products → gold comparison tables (DuckDB)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import duckdb

from lake.io import GOLD_ROOT, SILVER_ROOT, utc_now_iso

logger = logging.getLogger("hybrid_scraper.lake.silver_to_gold")

GOLD_DB = GOLD_ROOT / "ashfield_compare.duckdb"

# Explicit casts — read_json_auto infers all-null WW fields as JSON, which
# breaks UNION ALL BY NAME against Coles VARCHAR/numeric columns.
_SKU_FACTS_SELECT = """
SELECT
    CAST(retailer AS VARCHAR) AS retailer,
    CAST(store_id AS VARCHAR) AS store_id,
    CAST(retailer_product_id AS BIGINT) AS retailer_product_id,
    CAST(name AS VARCHAR) AS name,
    CAST(clean_brand AS VARCHAR) AS clean_brand,
    CAST(unified_category AS VARCHAR) AS unified_category,
    CAST(unified_subcategory AS VARCHAR) AS unified_subcategory,
    TRY_CAST(price_now AS DOUBLE) AS price_now,
    TRY_CAST(price_was AS DOUBLE) AS price_was,
    CAST(unit_price AS VARCHAR) AS unit_price,
    CAST(is_promo AS BOOLEAN) AS is_promo,
    CAST(promo_type AS VARCHAR) AS promo_type,
    CAST(promo_label AS VARCHAR) AS promo_label,
    CAST(stock_status AS VARCHAR) AS stock_status,
    CAST(aisle_number AS VARCHAR) AS aisle_number,
    CAST(aisle_side AS VARCHAR) AS aisle_side,
    CAST(bay_number AS VARCHAR) AS bay_number,
    TRY_CAST(aisle_facing AS INTEGER) AS aisle_facing,
    TRY_CAST(aisle_order AS DOUBLE) AS aisle_order,
    TRY_CAST(indoor_x AS DOUBLE) AS indoor_x,
    TRY_CAST(indoor_y AS DOUBLE) AS indoor_y,
    CAST(location_class AS VARCHAR) AS location_class,
    CAST(inferred_bay AS VARCHAR) AS inferred_bay,
    CAST(bay_key AS VARCHAR) AS bay_key
FROM read_json_auto(?)
"""


def _latest_silver() -> Optional[Path]:
    if not SILVER_ROOT.exists():
        return None
    runs = sorted((p for p in SILVER_ROOT.iterdir() if p.is_dir()), reverse=True)
    return runs[0] if runs else None


def run_silver_to_gold(silver_dir: Optional[Path] = None) -> Path:
    silver_dir = silver_dir or _latest_silver()
    if silver_dir is None:
        raise FileNotFoundError("No silver run found under lake/silver — run --phase etl after a scrape")
    GOLD_ROOT.mkdir(parents=True, exist_ok=True)
    logger.info("silver_to_gold start silver=%s db=%s", silver_dir, GOLD_DB)
    coles = silver_dir / "coles" / "products.jsonl"
    ww = silver_dir / "woolworths" / "products.jsonl"
    venn = silver_dir / "category_venn.jsonl"

    conn = duckdb.connect(str(GOLD_DB))
    conn.execute("CREATE SCHEMA IF NOT EXISTS gold")
    conn.execute("DROP TABLE IF EXISTS gold.sku_facts")
    parts = []
    params = []
    if coles.exists():
        parts.append(f"({_SKU_FACTS_SELECT})")
        params.append(str(coles))
    if ww.exists():
        parts.append(f"({_SKU_FACTS_SELECT})")
        params.append(str(ww))
    if not parts:
        conn.execute("""
            CREATE TABLE gold.sku_facts (
                retailer VARCHAR, store_id VARCHAR, retailer_product_id BIGINT,
                name VARCHAR, unified_category VARCHAR, price_now DOUBLE, price_was DOUBLE,
                is_promo BOOLEAN, bay_key VARCHAR, location_class VARCHAR, aisle_facing INTEGER
            )
            """)
        logger.warning("silver_to_gold empty sku_facts — no silver product jsonl")
    elif len(parts) == 1:
        conn.execute("CREATE TABLE gold.sku_facts AS " + parts[0], params)
    else:
        conn.execute("CREATE TABLE gold.sku_facts AS " + " UNION ALL BY NAME ".join(parts), params)

    conn.execute("DROP TABLE IF EXISTS gold.category_pricing")
    conn.execute("""
        CREATE TABLE gold.category_pricing AS
        SELECT
            retailer,
            unified_category,
            count(*) AS sku_count,
            median(price_now) AS median_price,
            avg(price_now) AS mean_price,
            100.0 * avg(CASE WHEN is_promo THEN 1.0 ELSE 0.0 END) AS pct_on_promo,
            median(
                CASE
                    WHEN price_was IS NOT NULL AND price_now IS NOT NULL AND try_cast(price_was AS DOUBLE) > price_now
                    THEN (try_cast(price_was AS DOUBLE) - price_now) / try_cast(price_was AS DOUBLE)
                END
            ) AS median_discount
        FROM gold.sku_facts
        WHERE unified_category IS NOT NULL AND unified_category <> 'Unmapped'
        GROUP BY 1, 2
        """)

    conn.execute("DROP TABLE IF EXISTS gold.subcategory_pricing")
    conn.execute("""
        CREATE TABLE gold.subcategory_pricing AS
        SELECT
            retailer,
            unified_category,
            unified_subcategory,
            count(*) AS sku_count,
            median(price_now) AS median_price,
            avg(price_now) AS mean_price,
            100.0 * avg(CASE WHEN is_promo THEN 1.0 ELSE 0.0 END) AS pct_on_promo,
            median(
                CASE
                    WHEN price_was IS NOT NULL AND price_now IS NOT NULL AND try_cast(price_was AS DOUBLE) > price_now
                    THEN (try_cast(price_was AS DOUBLE) - price_now) / try_cast(price_was AS DOUBLE)
                END
            ) AS median_discount
        FROM gold.sku_facts
        WHERE unified_category IS NOT NULL AND unified_category <> 'Unmapped'
          AND unified_subcategory IS NOT NULL AND unified_subcategory <> ''
        GROUP BY 1, 2, 3
        """)

    conn.execute("DROP TABLE IF EXISTS gold.category_space")
    conn.execute("""
        CREATE TABLE gold.category_space AS
        WITH placed AS (
            SELECT retailer, bay_key, unified_category, aisle_facing
            FROM gold.sku_facts
            WHERE location_class = 'aisle'
              AND bay_key IS NOT NULL
              AND unified_category IS NOT NULL
              AND unified_category <> 'Unmapped'
        ),
        -- Fractional bay share: a bay with 70% dairy SKUs contributes 0.7 to dairy.
        -- Categories on a shared bay each get a slice; slices sum to 1 per bay.
        bay_cat AS (
            SELECT retailer, bay_key, unified_category, count(*) AS skus
            FROM placed
            GROUP BY 1, 2, 3
        ),
        bay_tot AS (
            SELECT retailer, bay_key, sum(skus) AS bay_skus
            FROM bay_cat
            GROUP BY 1, 2
        ),
        frac AS (
            SELECT
                bc.retailer,
                bc.bay_key,
                bc.unified_category,
                bc.skus,
                bt.bay_skus,
                bc.skus::DOUBLE / nullif(bt.bay_skus, 0) AS bay_fraction
            FROM bay_cat bc
            JOIN bay_tot bt USING (retailer, bay_key)
        ),
        store_tot AS (
            SELECT retailer, count(DISTINCT bay_key) AS store_bay_count
            FROM placed
            GROUP BY 1
        ),
        placed_skus AS (
            SELECT retailer, unified_category, count(*) AS placed_skus,
                   sum(try_cast(aisle_facing AS INTEGER)) AS facing_sum
            FROM placed
            GROUP BY 1, 2
        )
        SELECT
            f.retailer,
            f.unified_category,
            round(sum(f.bay_fraction), 3) AS bay_count,
            t.store_bay_count,
            sum(f.bay_fraction) / nullif(t.store_bay_count, 0) AS pct_store_bays,
            coalesce(p.placed_skus, 0) AS placed_skus,
            p.facing_sum,
            round(avg(f.bay_fraction) * 100, 1) AS avg_bay_share_pct
        FROM frac f
        JOIN store_tot t USING (retailer)
        LEFT JOIN placed_skus p
          ON p.retailer = f.retailer AND p.unified_category = f.unified_category
        GROUP BY f.retailer, f.unified_category, t.store_bay_count, p.placed_skus, p.facing_sum
        """)

    conn.execute("DROP TABLE IF EXISTS gold.subcategory_space")
    conn.execute("""
        CREATE TABLE gold.subcategory_space AS
        WITH placed AS (
            SELECT retailer, bay_key, unified_category, unified_subcategory, aisle_facing
            FROM gold.sku_facts
            WHERE location_class = 'aisle'
              AND bay_key IS NOT NULL
              AND unified_category IS NOT NULL
              AND unified_category <> 'Unmapped'
              AND unified_subcategory IS NOT NULL
              AND unified_subcategory <> ''
        ),
        bay_cat AS (
            SELECT retailer, bay_key, unified_category, unified_subcategory, count(*) AS skus
            FROM placed
            GROUP BY 1, 2, 3, 4
        ),
        bay_tot AS (
            SELECT retailer, bay_key, sum(skus) AS bay_skus
            FROM bay_cat
            GROUP BY 1, 2
        ),
        frac AS (
            SELECT
                bc.retailer,
                bc.bay_key,
                bc.unified_category,
                bc.unified_subcategory,
                bc.skus,
                bt.bay_skus,
                bc.skus::DOUBLE / nullif(bt.bay_skus, 0) AS bay_fraction
            FROM bay_cat bc
            JOIN bay_tot bt USING (retailer, bay_key)
        ),
        store_tot AS (
            SELECT retailer, count(DISTINCT bay_key) AS store_bay_count
            FROM placed
            GROUP BY 1
        ),
        placed_skus AS (
            SELECT retailer, unified_category, unified_subcategory, count(*) AS placed_skus,
                   sum(try_cast(aisle_facing AS INTEGER)) AS facing_sum
            FROM placed
            GROUP BY 1, 2, 3
        )
        SELECT
            f.retailer,
            f.unified_category,
            f.unified_subcategory,
            round(sum(f.bay_fraction), 3) AS bay_count,
            t.store_bay_count,
            sum(f.bay_fraction) / nullif(t.store_bay_count, 0) AS pct_store_bays,
            coalesce(p.placed_skus, 0) AS placed_skus,
            p.facing_sum,
            round(avg(f.bay_fraction) * 100, 1) AS avg_bay_share_pct
        FROM frac f
        JOIN store_tot t USING (retailer)
        LEFT JOIN placed_skus p
          ON p.retailer = f.retailer
         AND p.unified_category = f.unified_category
         AND p.unified_subcategory = f.unified_subcategory
        GROUP BY f.retailer, f.unified_category, f.unified_subcategory, t.store_bay_count, p.placed_skus, p.facing_sum
        """)

    conn.execute("DROP TABLE IF EXISTS gold.category_venn")
    if venn.exists():
        conn.execute("CREATE TABLE gold.category_venn AS SELECT * FROM read_json_auto(?)", [str(venn)])
    else:
        conn.execute("CREATE TABLE gold.category_venn (side VARCHAR, unified_category VARCHAR)")

    conn.execute("DROP TABLE IF EXISTS gold.banner_compare")
    conn.execute("""
        CREATE TABLE gold.banner_compare AS
        SELECT
            coalesce(c.unified_category, w.unified_category) AS unified_category,
            c.sku_count AS coles_skus,
            w.sku_count AS ww_skus,
            c.median_price AS coles_median_price,
            w.median_price AS ww_median_price,
            c.pct_on_promo AS coles_pct_promo,
            w.pct_on_promo AS ww_pct_promo,
            cs.bay_count AS coles_bay_count,
            ws.bay_count AS ww_bay_count,
            cs.pct_store_bays AS coles_pct_store_bays,
            ws.pct_store_bays AS ww_pct_store_bays
        FROM (SELECT * FROM gold.category_pricing WHERE retailer = 'Coles') c
        FULL OUTER JOIN (SELECT * FROM gold.category_pricing WHERE retailer = 'Woolworths') w
            USING (unified_category)
        LEFT JOIN (SELECT * FROM gold.category_space WHERE retailer = 'Coles') cs
            USING (unified_category)
        LEFT JOIN (SELECT * FROM gold.category_space WHERE retailer = 'Woolworths') ws
            USING (unified_category)
        """)

    # Category-unification audit: fuzzy matches + effective crosswalk from silver.
    matches_path = silver_dir / "sku_matches.jsonl"
    crosswalk_path = silver_dir / "category_crosswalk_used.csv"
    conn.execute("DROP TABLE IF EXISTS gold.sku_matches")
    if matches_path.exists():
        conn.execute(
            """
            CREATE TABLE gold.sku_matches AS
            SELECT
                CAST(coles_id AS BIGINT) AS coles_id,
                CAST(ww_id AS BIGINT) AS ww_id,
                CAST(coles_name AS VARCHAR) AS coles_name,
                CAST(ww_name AS VARCHAR) AS ww_name,
                CAST(brand AS VARCHAR) AS brand,
                TRY_CAST(score AS DOUBLE) AS score,
                CAST(coles_categories AS VARCHAR[]) AS coles_categories,
                CAST(ww_l0 AS VARCHAR) AS ww_l0,
                CAST(ww_l1 AS VARCHAR) AS ww_l1
            FROM read_json_auto(?)
            """,
            [str(matches_path)],
        )
    else:
        conn.execute("""
            CREATE TABLE gold.sku_matches (
                coles_id BIGINT, ww_id BIGINT, coles_name VARCHAR, ww_name VARCHAR,
                brand VARCHAR, score DOUBLE, coles_categories VARCHAR[],
                ww_l0 VARCHAR, ww_l1 VARCHAR
            )
            """)
        logger.warning("silver_to_gold empty sku_matches — missing %s", matches_path)

    conn.execute("DROP TABLE IF EXISTS gold.category_crosswalk")
    if crosswalk_path.exists():
        conn.execute(
            """
            CREATE TABLE gold.category_crosswalk AS
            SELECT
                TRY_CAST(priority AS INTEGER) AS priority,
                CAST(coles_category_slug AS VARCHAR) AS coles_category_slug,
                CAST(woolworths_category AS VARCHAR) AS woolworths_category,
                CAST(woolworths_subcategory AS VARCHAR) AS woolworths_subcategory,
                TRY_CAST(match_support AS INTEGER) AS match_support,
                TRY_CAST(vote_share AS DOUBLE) AS vote_share,
                CAST(source AS VARCHAR) AS source,
                CAST(notes AS VARCHAR) AS notes
            FROM read_csv_auto(?, header=true)
            """,
            [str(crosswalk_path)],
        )
    else:
        conn.execute("""
            CREATE TABLE gold.category_crosswalk (
                priority INTEGER, coles_category_slug VARCHAR, woolworths_category VARCHAR,
                woolworths_subcategory VARCHAR, match_support INTEGER, vote_share DOUBLE,
                source VARCHAR, notes VARCHAR
            )
            """)
        logger.warning("silver_to_gold empty category_crosswalk — missing %s", crosswalk_path)

    export_dir = GOLD_ROOT / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    for table, name in (
        ("gold.category_pricing", "category_pricing.csv"),
        ("gold.subcategory_pricing", "subcategory_pricing.csv"),
        ("gold.category_space", "category_space.csv"),
        ("gold.subcategory_space", "subcategory_space.csv"),
        ("gold.category_venn", "category_venn.csv"),
        ("gold.banner_compare", "banner_compare.csv"),
        ("gold.sku_matches", "sku_matches.csv"),
        ("gold.category_crosswalk", "category_crosswalk.csv"),
    ):
        dest = export_dir / name
        conn.execute(f"COPY {table} TO ? (HEADER, DELIMITER ',')", [str(dest)])
        logger.info("gold export table=%s path=%s", table, dest)

    counts = conn.execute("""
        SELECT
            (SELECT count(*) FROM gold.sku_facts) AS skus,
            (SELECT count(*) FROM gold.category_pricing) AS pricing_rows,
            (SELECT count(*) FROM gold.category_space) AS space_rows,
            (SELECT count(*) FROM gold.category_venn) AS venn_rows,
            (SELECT count(*) FROM gold.sku_matches) AS match_rows,
            (SELECT count(*) FROM gold.category_crosswalk) AS crosswalk_rows
        """).fetchone()
    logger.info(
        "silver_to_gold done skus=%s pricing_rows=%s space_rows=%s venn_rows=%s " "match_rows=%s crosswalk_rows=%s",
        counts[0],
        counts[1],
        counts[2],
        counts[3],
        counts[4],
        counts[5],
    )
    (GOLD_ROOT / "manifest.json").write_text(
        json.dumps(
            {
                "created_at": utc_now_iso(),
                "silver_dir": str(silver_dir),
                "db": str(GOLD_DB),
                "skus": counts[0],
                "pricing_rows": counts[1],
                "space_rows": counts[2],
                "venn_rows": counts[3],
                "match_rows": counts[4],
                "crosswalk_rows": counts[5],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    conn.close()
    return GOLD_DB
