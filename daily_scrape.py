"""Master scrape: every store implied by hybrid_scraper.config.DAILY_SCRAPE_SUBURBS,
both Coles and Woolworths, Coles enriched with real in-store aisle data.

To change which stores get scraped, edit DAILY_SCRAPE_SUBURBS in
hybrid_scraper/config.py — each suburb resolves independently to its
nearest Coles store AND its nearest Woolworths store.

Run manually:
    python daily_scrape.py

Meant to be run on a schedule (e.g. Windows Task Scheduler, daily at a fixed
time) — see this repo's README/setup notes for wiring that up. Exit codes are
scheduler-friendly:
    0 — the run completed and at least one store succeeded (check the
        per-store summary in the log for any individual failures — a single
        flaky store doesn't fail the whole run, matching
        ScraperOrchestrator.run_for_stores' per-store fault isolation)
    1 — every configured store failed, or the script crashed outright
        (e.g. the DuckDB file is locked by another connection, or a
        Playwright/Chromium launch failure)

Prerequisites for a real run:
    pip install -r requirements.txt
    playwright install chromium
And, for the Coles aisle-enrichment pass specifically: BlueStacks running
with the Coles app already logged in (see
hybrid_scraper/mobile_session.py's docstring for the one-time setup this
assumes). If BlueStacks isn't reachable, that pass is skipped per-store with
a warning rather than failing the run — the base price/product scrape above
it doesn't depend on it.

Known gotcha: scraper_data.duckdb can only be opened for writing while no
other connection has it open — close `streamlit run dashboard.py` before
running this, or the scrape will fail with a DuckDB IOException.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date
from typing import Dict, List

from curl_cffi.requests import AsyncSession

from hybrid_scraper.aisle_enrichment import fetch_coles_instore_locations
from hybrid_scraper.bootstrapper import PlaywrightBootstrapper
from hybrid_scraper.config import DAILY_SCRAPE_SUBURBS
from hybrid_scraper.engine import CurlCffiEngine
from hybrid_scraper.logging_config import configure_logging
from hybrid_scraper.models import Product, StoreLocation
from hybrid_scraper.orchestrator import ScraperOrchestrator
from hybrid_scraper.storage import ProductStore, store_id_for

logger = logging.getLogger("hybrid_scraper.daily_scrape")


async def _resolve_targets(
    bootstrapper: PlaywrightBootstrapper, engine: CurlCffiEngine, suburbs: List[str]
) -> List[StoreLocation]:
    """Resolve every configured suburb to its nearest Coles + Woolworths store.

    A suburb that fails to resolve for one retailer is logged and skipped
    rather than aborting the whole configured list — the same per-target
    fault isolation `ScraperOrchestrator.run_for_stores` applies one level
    up, applied here to store *resolution* too.
    """
    # Coles' store-resolution GraphQL calls need a subscription key; any
    # bootstrap call yields one (it's a static per-client key, not tied to
    # the placeholder store id used here) — see main.py's identical pattern.
    placeholder_session = await bootstrapper.bootstrap("Coles", store_id="0")
    coles_key = placeholder_session.headers["ocp-apim-subscription-key"]

    targets: List[StoreLocation] = []
    for suburb in suburbs:
        try:
            targets.append(await engine.resolve_store_id("Coles", suburb, subscription_key=coles_key))
        except Exception as exc:
            logger.error("Failed to resolve a Coles store for suburb=%r: %s", suburb, exc)
        try:
            targets.append(await engine.resolve_store_id("Woolworths", suburb))
        except Exception as exc:
            logger.error("Failed to resolve a Woolworths store for suburb=%r: %s", suburb, exc)

    for store in targets:
        logger.info(
            "Resolved %s -> store_id=%s name=%r suburb=%s %s postcode=%s",
            store.retailer,
            store.store_id,
            store.store_name,
            store.suburb_name,
            store.state,
            store.postcode,
        )
    return targets


async def _enrich_coles_stores(
    results: Dict[str, List[Product]],
    targets_by_key: Dict[str, StoreLocation],
    product_store: ProductStore,
) -> None:
    """Best-effort in-store aisle enrichment for every Coles store just scraped.

    Wrapped in its own per-store try/except (on top of
    fetch_coles_instore_locations' own internal per-batch handling) — a
    BlueStacks problem (not running, capture timeout) shouldn't fail the
    daily run, since the base price/product scrape above already succeeded
    and is independently valuable without aisle data.
    """
    coles_keys = [key for key in results if key.startswith("Coles")]
    if not coles_keys:
        return
    async with AsyncSession() as app_session:
        for store_key in coles_keys:
            store_location = targets_by_key[store_key]
            skus = [str(p.retailer_product_id) for p in results[store_key]]
            try:
                logger.info(
                    "Fetching in-store aisle data for %d Coles SKUs at store %s", len(skus), store_location.store_id
                )
                aisle_by_sku = await fetch_coles_instore_locations(app_session, store_location.store_id, skus)
            except Exception as exc:
                logger.warning("Coles aisle enrichment skipped for %s: %s", store_key, exc)
                continue
            updated = product_store.apply_aisle_enrichment(store_key, aisle_by_sku)
            logger.info("%s: %d/%d current rows now have real aisle data", store_key, updated, len(skus))


async def main() -> int:
    """Runs the full daily scrape; returns a process exit code (see module docstring)."""
    async with PlaywrightBootstrapper() as bootstrapper, CurlCffiEngine(concurrency=8) as engine:
        targets = await _resolve_targets(bootstrapper, engine, DAILY_SCRAPE_SUBURBS)
        if not targets:
            logger.error("No stores resolved from DAILY_SCRAPE_SUBURBS=%r — nothing to scrape", DAILY_SCRAPE_SUBURBS)
            return 1
        targets_by_key = {store_id_for(t.retailer, t.store_id): t for t in targets}

        orchestrator = ScraperOrchestrator(bootstrapper, engine, max_retries=3)

        with ProductStore() as product_store:
            migration = product_store.migrate_legacy_snapshots()
            if migration:
                logger.info(
                    "Migrated legacy data: %d rows across %d runs", migration["rows_read"], migration["runs_migrated"]
                )

            scrape_date = date.today().isoformat()
            logger.info("Starting daily scrape_date=%s across %d stores", scrape_date, len(targets))

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

            await _enrich_coles_stores(results, targets_by_key, product_store)

            logger.info(
                "Daily scrape done scrape_date=%s: %d/%d stores succeeded",
                scrape_date,
                len(results),
                len(targets),
            )
            for store_key in sorted(results):
                logger.info("  OK   %s", store_key)
            for store_key in sorted(failures):
                logger.info("  FAIL %s", store_key)

            return 0 if results else 1


if __name__ == "__main__":
    configure_logging()
    try:
        exit_code = asyncio.run(main())
    except Exception:
        # Logged with full traceback so a scheduled (unattended) run that
        # crashed outright is still fully diagnosable from scraper.log alone.
        logger.exception("daily_scrape.py crashed with an unhandled exception")
        sys.exit(1)
    sys.exit(exit_code)
