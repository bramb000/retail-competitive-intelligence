"""One-off scrape: Coles Burwood East + the nearest Woolworths, Coles enriched
with real in-store aisle/coordinate data.

Follows `main.py`'s exact resolve -> scrape -> record pattern, just for a
different suburb, plus one extra step Coles-only: the website/app's
`/api/2/products/list` endpoint only returns real `locations[]` data when
called with `shoppingMethod=inStore` (confirmed live — see the comment on
`Product.aisle_number` in `hybrid_scraper/models.py`) instead of the
`clickAndCollect` shape the main site (and this project's regular scrape)
uses. That endpoint lives on a different host (`apigw.coles.com.au`, the
app's backend, not `www.coles.com.au`) behind an Incapsula/Imperva WAF that
requires an `x-d-token` device-attestation header — an opaque blob only the
real app's own code can produce, so this project has never reverse-engineered
a generator for it. Instead, `hybrid_scraper.mobile_session` captures a fresh
one live off the real (already-logged-in) Coles app running in BlueStacks —
see that module's docstring for the full mechanism — on every run by
default, and again automatically mid-run if a batch 401/403s (the captured
token's TTL is short and unpredictable, confirmed live at ~15-20min).

Woolworths has no equivalent enrichment here — its own in-app "Product
Finder" endpoint was never reverse-engineered this session (this file only
adds the aisle enrichment pass for Coles).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date
from typing import List

from curl_cffi.requests import AsyncSession

from hybrid_scraper.aisle_enrichment import fetch_coles_instore_locations
from hybrid_scraper.bootstrapper import PlaywrightBootstrapper
from hybrid_scraper.engine import CurlCffiEngine
from hybrid_scraper.logging_config import configure_logging
from hybrid_scraper.models import StoreLocation
from hybrid_scraper.orchestrator import ScraperOrchestrator
from hybrid_scraper.storage import ProductStore, store_id_for

logger = logging.getLogger("hybrid_scraper.scrape_burwood")

SUBURB = sys.argv[1] if len(sys.argv) > 1 else "Burwood East VIC 3151"


async def _resolve_targets(bootstrapper: PlaywrightBootstrapper, engine: CurlCffiEngine) -> List[StoreLocation]:
    placeholder_session = await bootstrapper.bootstrap("Coles", store_id="0")
    coles_key = placeholder_session.headers["ocp-apim-subscription-key"]

    coles_store = await engine.resolve_store_id("Coles", SUBURB, subscription_key=coles_key)
    woolworths_store = await engine.resolve_store_id("Woolworths", SUBURB)

    for store in (coles_store, woolworths_store):
        logger.info(
            "Resolved %s -> store_id=%s name=%r suburb=%s %s postcode=%s",
            store.retailer,
            store.store_id,
            store.store_name,
            store.suburb_name,
            store.state,
            store.postcode,
        )
    return [coles_store, woolworths_store]


async def main() -> None:
    async with PlaywrightBootstrapper() as bootstrapper, CurlCffiEngine(concurrency=8) as engine:
        targets = await _resolve_targets(bootstrapper, engine)
        targets_by_key = {store_id_for(t.retailer, t.store_id): t for t in targets}

        orchestrator = ScraperOrchestrator(bootstrapper, engine, max_retries=3)

        with ProductStore() as product_store:
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

            # Coles-only aisle enrichment pass, over whatever SKUs were just scraped.
            coles_key = next((k for k in results if k.startswith("Coles")), None)
            if coles_key:
                coles_store = targets_by_key[coles_key]
                skus = [str(p.retailer_product_id) for p in results[coles_key]]
                logger.info(
                    "Fetching real in-store aisle data for %d Coles SKUs at store %s", len(skus), coles_store.store_id
                )
                async with AsyncSession() as app_session:
                    aisle_by_sku = await fetch_coles_instore_locations(app_session, coles_store.store_id, skus)
                logger.info("Got real aisle data for %d/%d Coles SKUs", len(aisle_by_sku), len(skus))
                updated_count = product_store.apply_aisle_enrichment(coles_key, aisle_by_sku)
                logger.info("%s: %d current rows now have non-null aisle_number", coles_key, updated_count)

            logger.info("Summary (current prices per store, read back from DuckDB):")
            current_rows = product_store.current_prices().fetchall()
            counts: dict[str, int] = {}
            for row in current_rows:
                store_label = row[0]
                counts[store_label] = counts.get(store_label, 0) + 1
            for store_label, count in sorted(counts.items()):
                logger.info("  %s: %d SKUs", store_label, count)


if __name__ == "__main__":
    configure_logging()
    try:
        asyncio.run(main())
    except Exception:
        logger.exception("scrape_burwood.py crashed with an unhandled exception")
        raise
