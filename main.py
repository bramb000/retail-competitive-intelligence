"""Execution example: hybrid Playwright + curl_cffi scrape of Coles & Woolworths.

Resolves `hybrid_scraper.config.TEST_SUBURB` ("Ashfield NSW 2131" — has both
a Coles and a Woolworths) to real, nearby stores for both retailers, fetches
real product data for each, and appends the results as a new timestamped
snapshot in a local DuckDB file (`scraper_data.duckdb`). Each row carries the
resolved suburb name/postcode/lat/lng so distance between any two stores can
be computed later via `ProductStore.distance_between_stores`.

Requirements for a real (non-import-only) run:
    pip install -r requirements.txt
    playwright install chromium

This also requires live network access to coles.com.au / woolworths.com.au.

Store resolution (`CurlCffiEngine.resolve_store_id`) was reverse-engineered
from each retailer's own JS, confirmed live — see engine.py's docstrings:
- Coles: GraphQL (`GetStoreLocationSuggestions` + `FindStores`), needs a
  subscription key first (any bootstrap call obtains one — it's a static
  per-client key, not store-specific).
- Woolworths: two REST endpoints (`StoreLocator/Suburbs`, `StoreLocator/Stores`),
  no key needed, entirely via curl_cffi (no Playwright involved).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from hybrid_scraper.bootstrapper import PlaywrightBootstrapper
from hybrid_scraper.config import TEST_SUBURB
from hybrid_scraper.engine import CurlCffiEngine
from hybrid_scraper.logging_config import configure_logging
from hybrid_scraper.models import StoreLocation
from hybrid_scraper.orchestrator import ScraperOrchestrator
from hybrid_scraper.storage import ProductStore, store_id_for

# NOT logging.getLogger(__name__): when this file is run directly (`python
# main.py`), __name__ is "__main__" — a logger outside the "hybrid_scraper"
# tree that configure_logging() attaches handlers to, so anything logged
# through it would be silently dropped (verified live: every logger.info()
# call in this file produced zero output until this was fixed).
logger = logging.getLogger("hybrid_scraper.main")


async def _resolve_targets(bootstrapper: PlaywrightBootstrapper, engine: CurlCffiEngine) -> list[StoreLocation]:
    """Resolve TEST_SUBURB to a real Coles store and a real Woolworths store."""
    # Coles' store-resolution GraphQL calls need a subscription key; any
    # bootstrap call yields one (it's a static per-client key, not tied to
    # the placeholder store id used here).
    placeholder_session = await bootstrapper.bootstrap("Coles", store_id="0")
    coles_key = placeholder_session.headers["ocp-apim-subscription-key"]

    coles_store = await engine.resolve_store_id("Coles", TEST_SUBURB, subscription_key=coles_key)
    woolworths_store = await engine.resolve_store_id("Woolworths", TEST_SUBURB)

    for store in (coles_store, woolworths_store):
        logger.info(
            "Resolved %s -> store_id=%s name=%r suburb=%s %s postcode=%s lat=%s lon=%s",
            store.retailer,
            store.store_id,
            store.store_name,
            store.suburb_name,
            store.state,
            store.postcode,
            store.latitude,
            store.longitude,
        )

    return [coles_store, woolworths_store]


async def main() -> None:
    async with PlaywrightBootstrapper() as bootstrapper, CurlCffiEngine(concurrency=8) as engine:
        targets = await _resolve_targets(bootstrapper, engine)
        targets_by_key = {store_id_for(t.retailer, t.store_id): t for t in targets}

        orchestrator = ScraperOrchestrator(bootstrapper, engine, max_retries=3)

        with ProductStore() as product_store:
            migration = product_store.migrate_legacy_snapshots()
            if migration:
                logger.info(
                    "Migrated legacy data: %d rows across %d runs", migration["rows_read"], migration["runs_migrated"]
                )

            scrape_date = date.today().isoformat()
            logger.info("Starting scrape_date=%s across %d stores", scrape_date, len(targets))

            results, failures = await orchestrator.run_for_stores(
                targets, scrape_date, max_pages_per_term=5, max_search_terms=30
            )

            for store_key, products in results.items():
                stats = product_store.record_scrape(targets_by_key[store_key], products, scrape_date)
                logger.info(
                    "%s: scraped %d SKUs (new=%d changed=%d unchanged=%d)",
                    store_key,
                    len(products),
                    stats.new,
                    stats.changed,
                    stats.unchanged,
                )

            for store_key, error in failures.items():
                logger.error("%s: FAILED — %s", store_key, error)

            logger.info("Summary (current prices per store, read back from DuckDB):")
            current_rows = product_store.current_prices().fetchall()
            counts: dict[str, int] = {}
            for row in current_rows:
                store_label = row[0]  # current_prices view: store_id is the first column
                counts[store_label] = counts.get(store_label, 0) + 1
            for store_label, count in sorted(counts.items()):
                logger.info("  %s: %d SKUs", store_label, count)

            if len(targets) == 2:
                store_a = store_id_for(targets[0].retailer, targets[0].store_id)
                store_b = store_id_for(targets[1].retailer, targets[1].store_id)
                distance_km = product_store.distance_between_stores(store_a, store_b)
                logger.info("Distance between %s and %s: %s km", store_a, store_b, distance_km)


if __name__ == "__main__":
    configure_logging()
    try:
        asyncio.run(main())
    except Exception:
        # Logged with full traceback (not just printed) so a crashed run can
        # be diagnosed from scraper.log alone, without needing to reproduce it.
        logger.exception("main() crashed with an unhandled exception")
        raise
