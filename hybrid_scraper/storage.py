"""Local DuckDB persistence layer — a small star schema for daily price tracking.

Three tables, not one flat table:
  - `stores`   — dimension: one row per physical store, rarely changes.
  - `products` — dimension: retailer catalog attributes (name/brand/category/
                 pack size/...), rarely change.
  - `price_history` — fact: the things that actually change day to day
                 (price, stock, promo badge, aisle/bay), using **SCD Type 2**
                 (`valid_from`/`valid_to` date ranges) — a new row is written
                 only when a value actually changes since the last scrape;
                 an unchanged day costs zero writes.

`current_prices` is a VIEW joining all three, filtered to `valid_to IS NULL`,
reconstructing a "full row as of today" shape for easy querying (what
`main.py`'s summary and `dashboard.py` read from).

Why this shape: moving to a **daily** scrape cadence with the old flat
append-only table would mean ~365 near-identical rows/year per SKU for any
price that doesn't change, repeating static catalog/store fields on every
one. Splitting dimension (stored once) from fact (stored only on change)
is the standard pattern for exactly this problem.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import duckdb

from hybrid_scraper.models import Product, RetailerName, StoreLocation

logger = logging.getLogger(__name__)

_PRICE_HISTORY_COLUMNS: Tuple[str, ...] = (
    "store_id",
    "product_key",
    "price_display",
    "prev_price",
    "loyalty_price",
    "price_per_uom",
    "stock_status",
    "product_badge",
    "No_of_reviews",
    "Star_rating",
    "PLV_ID",
    "aisle_number",
    "bay_number",
    "aisle_facing",
    "aisle_order",
    "indoor_x",
    "indoor_y",
    "scraped_at",
    "valid_from",
    "valid_to",
)

_QUOTED_COLUMNS = {"No_of_reviews", "Star_rating", "PLV_ID"}


def _quote(column: str) -> str:
    return f'"{column}"' if column in _QUOTED_COLUMNS else column


_CREATE_STORES_SQL = """
CREATE TABLE IF NOT EXISTS stores (
    store_id VARCHAR PRIMARY KEY,
    retailer VARCHAR,
    native_store_id VARCHAR,
    store_name VARCHAR,
    suburb_name VARCHAR,
    state VARCHAR,
    postcode VARCHAR,
    latitude DOUBLE,
    longitude DOUBLE,
    first_seen_date DATE,
    last_confirmed_date DATE
)
"""

_CREATE_PRODUCTS_SQL = """
CREATE TABLE IF NOT EXISTS products (
    product_key VARCHAR PRIMARY KEY,
    retailer VARCHAR,
    retailer_product_id BIGINT,
    child_product_id VARCHAR,
    name VARCHAR,
    clean_brand VARCHAR,
    category VARCHAR,
    sub_category_1 VARCHAR,
    sub_category_2 VARCHAR,
    sub_category_3 VARCHAR,
    pack_size DOUBLE,
    clean_uom VARCHAR,
    product_page VARCHAR,
    image_url VARCHAR,
    first_seen_date DATE,
    last_confirmed_date DATE
)
"""

_CREATE_PRICE_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS price_history (
    store_id VARCHAR,
    product_key VARCHAR,
    price_display DOUBLE,
    prev_price VARCHAR,
    loyalty_price VARCHAR,
    price_per_uom VARCHAR,
    stock_status VARCHAR,
    product_badge VARCHAR,
    "No_of_reviews" VARCHAR,
    "Star_rating" DOUBLE,
    "PLV_ID" VARCHAR,
    aisle_number VARCHAR,
    bay_number VARCHAR,
    -- Richer in-store positioning, Coles-only for now (see
    -- Product.aisle_number's docstring in models.py): `facing`/`order` come
    -- straight off the app's `locations[]` entry; `indoor_x`/`indoor_y` are
    -- the precise map-pin coordinates used to render the app's store map.
    aisle_facing INTEGER,
    aisle_order DOUBLE,
    indoor_x DOUBLE,
    indoor_y DOUBLE,
    scraped_at VARCHAR,
    valid_from DATE NOT NULL,
    valid_to DATE,
    UNIQUE (store_id, product_key, valid_from)
)
"""

_CREATE_CATALOG_CATEGORIES_SQL = """
CREATE TABLE IF NOT EXISTS catalog_categories (
    retailer VARCHAR,
    retailer_product_id BIGINT,
    category VARCHAR,
    PRIMARY KEY (retailer, retailer_product_id, category)
)
"""

_CREATE_CURRENT_PRICES_VIEW_SQL = """
CREATE OR REPLACE VIEW current_prices AS
SELECT
    st.store_id,
    st.retailer,
    st.store_name,
    st.suburb_name,
    st.state,
    st.postcode,
    st.latitude,
    st.longitude,
    p.product_key,
    p.retailer_product_id,
    p.child_product_id,
    p.name,
    p.clean_brand,
    p.category,
    p.sub_category_1,
    p.sub_category_2,
    p.sub_category_3,
    p.pack_size,
    p.clean_uom,
    p.product_page,
    p.image_url,
    ph.price_display,
    ph.prev_price,
    ph.loyalty_price,
    ph.price_per_uom,
    ph.stock_status,
    ph.product_badge,
    ph."No_of_reviews",
    ph."Star_rating",
    ph."PLV_ID",
    ph.aisle_number,
    ph.bay_number,
    ph.aisle_facing,
    ph.aisle_order,
    ph.indoor_x,
    ph.indoor_y,
    ph.scraped_at,
    ph.valid_from AS price_since
FROM price_history ph
JOIN stores st ON st.store_id = ph.store_id
JOIN products p ON p.product_key = ph.product_key
WHERE ph.valid_to IS NULL
"""

_STAGING_TABLE_SQL = """
CREATE OR REPLACE TEMP TABLE staging_prices (
    product_key VARCHAR,
    price_display DOUBLE,
    prev_price VARCHAR,
    loyalty_price VARCHAR,
    price_per_uom VARCHAR,
    stock_status VARCHAR,
    product_badge VARCHAR,
    no_of_reviews VARCHAR,
    star_rating DOUBLE,
    plv_id VARCHAR,
    aisle_number VARCHAR,
    bay_number VARCHAR,
    aisle_facing INTEGER,
    aisle_order DOUBLE,
    indoor_x DOUBLE,
    indoor_y DOUBLE,
    scraped_at VARCHAR
)
"""

# Every fact column checked for a "did this change" comparison — kept as one
# list so the changed-keys query and the docstring below stay in sync.
_FACT_COMPARISON_COLUMNS: Tuple[str, ...] = (
    "price_display",
    "prev_price",
    "loyalty_price",
    "price_per_uom",
    "stock_status",
    "product_badge",
    "aisle_number",
    "bay_number",
    "aisle_facing",
    "aisle_order",
    "indoor_x",
    "indoor_y",
)
# Deliberately excluded from change comparison: No_of_reviews/Star_rating —
# these can drift by tiny amounts from routine review activity unrelated to
# the price/stock trend this table exists to track, and would otherwise
# force a new history row on essentially every scrape.


def haversine_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    earth_radius_km = 6371.0
    phi1, phi2 = math.radians(lat_a), math.radians(lat_b)
    delta_phi = math.radians(lat_b - lat_a)
    delta_lambda = math.radians(lon_b - lon_a)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return earth_radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def store_id_for(retailer: RetailerName, native_store_id: str) -> str:
    """The composite store key used throughout (dimension PK, fact FK, dashboard labels)."""
    return f"{retailer} - {native_store_id}"


@dataclass(frozen=True)
class AisleEnrichment:
    """One SKU's in-store location fields, as passed to `apply_aisle_enrichment`."""

    aisle_number: Optional[str]
    bay_number: Optional[str]
    aisle_facing: Optional[int] = None
    aisle_order: Optional[float] = None
    indoor_x: Optional[float] = None
    indoor_y: Optional[float] = None


@dataclass(frozen=True)
class ScrapeStats:
    """Row-level outcome of one `record_scrape` call — the storage payoff, made visible."""

    new: int
    changed: int
    unchanged: int


class ProductStore:
    """Thin wrapper around a local DuckDB file. Use as a context manager.

    with ProductStore() as store:
        stats = store.record_scrape(store_location, products, date.today().isoformat())
    """

    def __init__(self, db_path: str = "scraper_data.duckdb") -> None:
        self._db_path = db_path
        self._conn: Optional[duckdb.DuckDBPyConnection] = None

    def __enter__(self) -> "ProductStore":
        logger.info("Opening DuckDB connection db_path=%s", self._db_path)
        self._conn = duckdb.connect(self._db_path)
        self._conn.execute(_CREATE_STORES_SQL)
        self._conn.execute(_CREATE_PRODUCTS_SQL)
        self._conn.execute(_CREATE_PRICE_HISTORY_SQL)
        self._migrate_price_history_columns()
        self._conn.execute(_CREATE_CATALOG_CATEGORIES_SQL)
        self._conn.execute(_CREATE_CURRENT_PRICES_VIEW_SQL)
        return self

    def _migrate_price_history_columns(self) -> None:
        """Add columns introduced after a `price_history` table already existed on disk.

        `CREATE TABLE IF NOT EXISTS` above only covers a brand-new database
        file — an existing one predates newer columns (e.g. `indoor_x`) and
        needs them added in place. Idempotent either way.
        """
        conn = self._require_conn()
        for column, sql_type in (
            ("aisle_facing", "INTEGER"),
            ("aisle_order", "DOUBLE"),
            ("indoor_x", "DOUBLE"),
            ("indoor_y", "DOUBLE"),
        ):
            conn.execute(f"ALTER TABLE price_history ADD COLUMN IF NOT EXISTS {column} {sql_type}")

    def __exit__(self, *_exc_info: Any) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        logger.info("Closed DuckDB connection db_path=%s", self._db_path)

    def _require_conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            raise RuntimeError("ProductStore must be used as a context manager")
        return self._conn

    def import_catalog_categories(self, csv_path: str, retailer: str = "Coles") -> int:
        """Bulk-load a retailer-wide (product_id, category) reference CSV.

        Source: a BigQuery export of `market_intelligence.MI_raw_history`
        (`retailer_product_id,category` columns, header row). This is a
        national catalogue, not store-scoped — a product can legitimately
        appear under more than one category, hence the composite primary key
        rather than one row per product_id. Existing rows are left alone; a
        re-import only adds pairs not already present.
        """
        conn = self._require_conn()
        conn.execute(
            """
            INSERT INTO catalog_categories (retailer, retailer_product_id, category)
            SELECT ?, retailer_product_id, category
            FROM read_csv_auto(?, header=true)
            ON CONFLICT DO NOTHING
            """,
            [retailer, csv_path],
        )
        return conn.execute("SELECT COUNT(*) FROM catalog_categories WHERE retailer = ?", [retailer]).fetchone()[0]

    # --- Dimension upserts --------------------------------------------------

    def _upsert_store(self, store_location: StoreLocation, scrape_date: str) -> str:
        conn = self._require_conn()
        composite_store_id = store_id_for(store_location.retailer, store_location.store_id)
        conn.execute(
            """
            INSERT INTO stores (store_id, retailer, native_store_id, store_name, suburb_name,
                                 state, postcode, latitude, longitude, first_seen_date, last_confirmed_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (store_id) DO UPDATE SET
                store_name = EXCLUDED.store_name,
                suburb_name = EXCLUDED.suburb_name,
                state = EXCLUDED.state,
                postcode = EXCLUDED.postcode,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                last_confirmed_date = EXCLUDED.last_confirmed_date
            """,
            [
                composite_store_id,
                store_location.retailer,
                store_location.store_id,
                store_location.store_name,
                store_location.suburb_name,
                store_location.state,
                store_location.postcode,
                store_location.latitude,
                store_location.longitude,
                scrape_date,
                scrape_date,
            ],
        )
        return composite_store_id

    def _upsert_products(self, products: List[Product], scrape_date: str) -> None:
        if not products:
            return
        conn = self._require_conn()
        # Dedup within the batch itself — a product can appear once per
        # search term it matched; keep the last-seen values for each key.
        by_key: Dict[str, Product] = {p.product_key: p for p in products}
        rows = [
            (
                product.product_key,
                product.retailer,
                product.retailer_product_id,
                product.child_product_id,
                product.name,
                product.clean_brand,
                product.category,
                product.sub_category_1,
                product.sub_category_2,
                product.sub_category_3,
                product.pack_size,
                product.clean_uom,
                product.product_page,
                product.image_url,
                scrape_date,
                scrape_date,
            )
            for product in by_key.values()
        ]
        conn.executemany(
            """
            INSERT INTO products (product_key, retailer, retailer_product_id, child_product_id,
                                   name, clean_brand, category, sub_category_1, sub_category_2,
                                   sub_category_3, pack_size, clean_uom, product_page, image_url,
                                   first_seen_date, last_confirmed_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (product_key) DO UPDATE SET
                name = EXCLUDED.name,
                clean_brand = EXCLUDED.clean_brand,
                category = EXCLUDED.category,
                sub_category_1 = EXCLUDED.sub_category_1,
                sub_category_2 = EXCLUDED.sub_category_2,
                sub_category_3 = EXCLUDED.sub_category_3,
                pack_size = EXCLUDED.pack_size,
                clean_uom = EXCLUDED.clean_uom,
                product_page = EXCLUDED.product_page,
                image_url = EXCLUDED.image_url,
                last_confirmed_date = EXCLUDED.last_confirmed_date
            """,
            rows,
        )

    # --- Fact: SCD2 change detection ----------------------------------------

    def _apply_price_changes(self, store_id: str, products: List[Product], scrape_date: str) -> ScrapeStats:
        conn = self._require_conn()
        by_key: Dict[str, Product] = {p.product_key: p for p in products}
        if not by_key:
            return ScrapeStats(new=0, changed=0, unchanged=0)

        conn.execute(_STAGING_TABLE_SQL)
        conn.executemany(
            "INSERT INTO staging_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    product.product_key,
                    product.price_display,
                    product.prev_price,
                    product.loyalty_price,
                    product.price_per_uom,
                    product.stock_status,
                    product.product_badge,
                    product.no_of_reviews,
                    product.star_rating,
                    product.plv_id,
                    product.aisle_number,
                    product.bay_number,
                    product.aisle_facing,
                    product.aisle_order,
                    product.indoor_x,
                    product.indoor_y,
                    product.scraped_at,
                )
                for product in by_key.values()
            ],
        )

        # Every name in _FACT_COMPARISON_COLUMNS is plain lowercase snake_case
        # (the mixed-case columns are deliberately excluded — see its comment),
        # so it matches the staging table's column name directly.
        comparison = " OR ".join(f"p.{c} IS DISTINCT FROM s.{c}" for c in _FACT_COMPARISON_COLUMNS)
        conn.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE changed_keys AS
            SELECT s.product_key, (p.product_key IS NULL) AS is_new
            FROM staging_prices s
            LEFT JOIN price_history p
                ON p.store_id = ? AND p.product_key = s.product_key AND p.valid_to IS NULL
            WHERE p.product_key IS NULL OR ({comparison})
            """,
            [store_id],
        )

        (new_count,) = conn.execute("SELECT COUNT(*) FROM changed_keys WHERE is_new").fetchone()
        (changed_count,) = conn.execute("SELECT COUNT(*) FROM changed_keys WHERE NOT is_new").fetchone()

        # Close out the old open row for anything that changed (not new — a
        # brand-new product has no prior open row to close) — but only if
        # that open row is from a PRIOR day. The table's grain is one row
        # per (store, product, day): if this store was already scraped
        # earlier TODAY, its currently-open row already has
        # valid_from = scrape_date, so there's no distinct "old day" to
        # close — that row gets updated in place below instead (via
        # ON CONFLICT), rather than being closed-and-reopened with an
        # identical valid_from, which would collide on the
        # (store_id, product_key, valid_from) unique constraint.
        conn.execute(
            """
            UPDATE price_history
            SET valid_to = ?
            WHERE store_id = ? AND valid_to IS NULL AND valid_from < ?
              AND product_key IN (SELECT product_key FROM changed_keys WHERE NOT is_new)
            """,
            [scrape_date, store_id, scrape_date],
        )

        # Open a new row for everything changed-or-new. ON CONFLICT handles
        # the same-day-rescrape case above: if a row for this exact
        # (store, product, day) already exists (not closed, since it wasn't
        # from a prior day), overwrite its fact columns in place instead of
        # inserting a colliding duplicate.
        columns_sql = ", ".join(_quote(c) for c in _PRICE_HISTORY_COLUMNS)
        update_columns = [
            c for c in _PRICE_HISTORY_COLUMNS if c not in ("store_id", "product_key", "valid_from", "valid_to")
        ]
        update_sql = ", ".join(f"{_quote(c)} = EXCLUDED.{_quote(c)}" for c in update_columns)
        conn.execute(
            f"""
            INSERT INTO price_history ({columns_sql})
            SELECT ?, s.product_key, s.price_display, s.prev_price, s.loyalty_price, s.price_per_uom,
                   s.stock_status, s.product_badge, s.no_of_reviews, s.star_rating, s.plv_id,
                   s.aisle_number, s.bay_number, s.aisle_facing, s.aisle_order, s.indoor_x, s.indoor_y,
                   s.scraped_at, ?, NULL
            FROM staging_prices s
            JOIN changed_keys c ON c.product_key = s.product_key
            ON CONFLICT (store_id, product_key, valid_from) DO UPDATE SET {update_sql}
            """,
            [store_id, scrape_date],
        )

        unchanged_count = len(by_key) - new_count - changed_count
        return ScrapeStats(new=int(new_count), changed=int(changed_count), unchanged=int(unchanged_count))

    def apply_aisle_enrichment(
        self,
        composite_store_id: str,
        aisle_by_retailer_product_id: Dict[int, AisleEnrichment],
    ) -> int:
        """Patch in-store location fields onto today's already-recorded current row.

        Unlike `record_scrape`'s SCD2 diffing (which only opens a new
        `price_history` row when a fact value changes), this updates the
        already-open row for today in place — location data here comes from
        a follow-up enrichment call for a SKU set already scraped today, not
        a fresh price scrape, so it belongs on the same row rather than
        opening a spurious new one. `aisle_facing`/`aisle_order`/`indoor_x`/
        `indoor_y` are the richer fields the app's `locations[]` entry
        carries beyond a bare aisle number — see `Product.aisle_number`'s
        docstring in models.py for the source shape.
        """
        conn = self._require_conn()
        if not aisle_by_retailer_product_id:
            return 0

        retailer = composite_store_id.split(" - ", 1)[0]
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE aisle_enrichment ("
            "retailer_product_id BIGINT, aisle_number VARCHAR, bay_number VARCHAR, "
            "aisle_facing INTEGER, aisle_order DOUBLE, indoor_x DOUBLE, indoor_y DOUBLE)"
        )
        conn.executemany(
            "INSERT INTO aisle_enrichment VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (rpid, e.aisle_number, e.bay_number, e.aisle_facing, e.aisle_order, e.indoor_x, e.indoor_y)
                for rpid, e in aisle_by_retailer_product_id.items()
            ],
        )
        conn.execute(
            """
            UPDATE price_history
            SET aisle_number = e.aisle_number, bay_number = e.bay_number,
                aisle_facing = e.aisle_facing, aisle_order = e.aisle_order,
                indoor_x = e.indoor_x, indoor_y = e.indoor_y
            FROM aisle_enrichment e
            JOIN products p ON p.retailer_product_id = e.retailer_product_id AND p.retailer = ?
            WHERE price_history.product_key = p.product_key
              AND price_history.store_id = ?
              AND price_history.valid_to IS NULL
            """,
            [retailer, composite_store_id],
        )
        updated = conn.execute(
            "SELECT COUNT(*) FROM price_history "
            "WHERE store_id = ? AND valid_to IS NULL AND aisle_number IS NOT NULL",
            [composite_store_id],
        ).fetchone()
        return int(updated[0]) if updated else 0

    # --- Public entry point --------------------------------------------------

    def record_scrape(self, store_location: StoreLocation, products: List[Product], scrape_date: str) -> ScrapeStats:
        """Record one store's daily scrape: upsert dimensions, then change-detect the fact rows.

        The storage-optimization payoff lives here: an unchanged SKU costs
        zero writes to `price_history` (see `ScrapeStats.unchanged`), and
        `stores`/`products` are touched once regardless of how many facts
        are in the batch, not repeated per row.
        """
        conn = self._require_conn()
        if not products:
            logger.debug("record_scrape called with empty product list — nothing to record")
            return ScrapeStats(new=0, changed=0, unchanged=0)

        conn.execute("BEGIN TRANSACTION")
        try:
            composite_store_id = self._upsert_store(store_location, scrape_date)
            self._upsert_products(products, scrape_date)
            stats = self._apply_price_changes(composite_store_id, products, scrape_date)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        logger.info(
            "record_scrape store=%s scrape_date=%s new=%d changed=%d unchanged=%d",
            composite_store_id,
            scrape_date,
            stats.new,
            stats.changed,
            stats.unchanged,
        )
        return stats

    # --- Queries --------------------------------------------------------------

    def current_prices(self, store: Optional[str] = None) -> duckdb.DuckDBPyConnection:
        """Current (`valid_to IS NULL`) full row per SKU, optionally filtered to one store.

        A plain predicate on the view, not a re-rank of the whole history —
        cheap regardless of how much history has accumulated.
        """
        conn = self._require_conn()
        if store:
            return conn.execute("SELECT * FROM current_prices WHERE store_id = ?", [store])
        return conn.execute("SELECT * FROM current_prices")

    def distance_between_stores(self, store_a: str, store_b: str) -> Optional[float]:
        """Great-circle distance in km between two stores, straight from the `stores` dimension."""
        conn = self._require_conn()
        rows = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT store_id, latitude, longitude FROM stores WHERE store_id IN (?, ?)",
                [store_a, store_b],
            ).fetchall()
        }
        coords_a = rows.get(store_a)
        coords_b = rows.get(store_b)
        if coords_a is None or coords_b is None:
            logger.warning("distance_between_stores: no rows found for store_a=%s or store_b=%s", store_a, store_b)
            return None
        lat_a, lon_a = coords_a
        lat_b, lon_b = coords_b
        if None in (lat_a, lon_a, lat_b, lon_b):
            logger.warning("distance_between_stores: null coordinates for store_a=%s or store_b=%s", store_a, store_b)
            return None
        return haversine_km(lat_a, lon_a, lat_b, lon_b)

    # --- One-off migration of the old flat-table schema ------------------------

    def migrate_legacy_snapshots(self) -> Optional[Dict[str, int]]:
        """Carry the old flat `product_snapshots` table forward into the star schema.

        Run once. Replays each distinct historical `run_number` (oldest
        first) through the same `record_scrape` path real daily scrapes use,
        assigning synthetic sequential dates ending "today" — the legacy
        data has no real calendar-day granularity to recover (all test runs
        happened the same day), but this preserves relative change history
        (what changed between runs) rather than collapsing it. The original
        table is renamed to `product_snapshots_legacy_backup` afterward,
        never dropped.
        """
        conn = self._require_conn()
        existing_tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        if "product_snapshots" not in existing_tables:
            logger.info("No legacy product_snapshots table found — nothing to migrate")
            return None

        legacy_columns = (
            "scraped_at",
            "run_number",
            "location",
            "store",
            "suburb_name",
            "postcode",
            "latitude",
            "longitude",
            "category",
            "sub_category_1",
            "sub_category_2",
            "sub_category_3",
            "retailer_product_id",
            "child_product_id",
            "name",
            "pack_size",
            "clean_uom",
            "price_display",
            "loyalty_price",
            "price_per_uom",
            "clean_brand",
            "prev_price",
            "stock_status",
            "product_badge",
            "product_page",
            "image_url",
            '"No_of_reviews"',
            '"Star_rating"',
            '"PLV_ID"',
        )
        legacy_rows = conn.execute(
            f"SELECT {', '.join(legacy_columns)} FROM product_snapshots ORDER BY run_number, scraped_at"
        ).fetchall()

        if not legacy_rows:
            conn.execute("ALTER TABLE product_snapshots RENAME TO product_snapshots_legacy_backup")
            logger.info("Legacy product_snapshots table was empty — renamed to backup, nothing to replay")
            return {"runs_migrated": 0, "rows_read": 0}

        col_index = {name.strip('"'): i for i, name in enumerate(legacy_columns)}

        def get(row: Tuple[Any, ...], col: str) -> Any:
            return row[col_index[col]]

        runs = sorted({get(row, "run_number") for row in legacy_rows})
        today = date.today()
        run_to_synthetic_date = {
            run: (today - timedelta(days=len(runs) - 1 - i)).isoformat() for i, run in enumerate(runs)
        }

        rows_by_run_and_store: Dict[Tuple[Any, str], List[Tuple[Any, ...]]] = {}
        for row in legacy_rows:
            key = (get(row, "run_number"), get(row, "store"))
            rows_by_run_and_store.setdefault(key, []).append(row)

        for (run_number, store_label), rows in rows_by_run_and_store.items():
            synthetic_date = run_to_synthetic_date[run_number]
            first = rows[0]
            retailer, _, native_store_id = store_label.partition(" - ")
            if retailer not in ("Coles", "Woolworths"):
                raise ValueError(f"Unrecognized retailer in legacy store label {store_label!r}: {retailer!r}")
            store_location = StoreLocation(
                retailer=retailer,
                store_id=native_store_id or store_label,
                store_name=native_store_id or store_label,
                suburb_name=get(first, "suburb_name") or "",
                state="",
                postcode=get(first, "postcode") or "",
                latitude=get(first, "latitude"),
                longitude=get(first, "longitude"),
            )
            products = [
                Product(
                    retailer=retailer,
                    retailer_product_id=int(get(row, "retailer_product_id")),
                    child_product_id=get(row, "child_product_id"),
                    scrape_date=synthetic_date,
                    scraped_at=get(row, "scraped_at"),
                    name=get(row, "name"),
                    clean_brand=get(row, "clean_brand"),
                    category=get(row, "category"),
                    sub_category_1=get(row, "sub_category_1"),
                    sub_category_2=get(row, "sub_category_2"),
                    sub_category_3=get(row, "sub_category_3"),
                    pack_size=get(row, "pack_size"),
                    clean_uom=get(row, "clean_uom"),
                    product_page=get(row, "product_page"),
                    image_url=get(row, "image_url"),
                    price_display=get(row, "price_display"),
                    loyalty_price=get(row, "loyalty_price"),
                    price_per_uom=get(row, "price_per_uom"),
                    prev_price=get(row, "prev_price"),
                    stock_status=get(row, "stock_status"),
                    product_badge=get(row, "product_badge"),
                    no_of_reviews=get(row, "No_of_reviews"),
                    star_rating=get(row, "Star_rating"),
                    plv_id=get(row, "PLV_ID"),
                    aisle_number=None,
                    bay_number=None,
                )
                for row in rows
            ]
            self.record_scrape(store_location, products, synthetic_date)

        conn.execute("ALTER TABLE product_snapshots RENAME TO product_snapshots_legacy_backup")
        logger.info(
            "Migrated %d legacy rows across %d runs into the star schema; "
            "original table renamed to product_snapshots_legacy_backup",
            len(legacy_rows),
            len(runs),
        )
        return {"runs_migrated": len(runs), "rows_read": len(legacy_rows)}
