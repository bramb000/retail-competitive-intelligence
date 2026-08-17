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
requires an `x-d-token` device-attestation header this project hasn't
reverse-engineered a generator for yet — the token below is one captured
live from the real app and reused as-is. Confirmed short-lived: it worked
for the initial scrape+enrichment run, then a follow-up backfill run
~15-20 minutes later got 403s on every batch — if this 403s, a fresh one
needs capturing from the app again (see the `models.py` comment for how
that capture was done).

Woolworths has no equivalent enrichment here — its own in-app "Product
Finder" endpoint was never reverse-engineered this session (this file only
adds the aisle enrichment pass for Coles).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import date
from typing import Dict, List

from curl_cffi.requests import AsyncSession

from hybrid_scraper.bootstrapper import PlaywrightBootstrapper
from hybrid_scraper.config import IMPERSONATE_TARGET
from hybrid_scraper.engine import CurlCffiEngine
from hybrid_scraper.logging_config import configure_logging
from hybrid_scraper.models import StoreLocation
from hybrid_scraper.orchestrator import ScraperOrchestrator
from hybrid_scraper.storage import AisleEnrichment, ProductStore, store_id_for

logger = logging.getLogger("hybrid_scraper.scrape_burwood")

SUBURB = sys.argv[1] if len(sys.argv) > 1 else "Burwood East VIC 3151"

# Captured live from the real Coles app (see models.py's Product.aisle_number
# comment) — an app-only endpoint on apigw.coles.com.au, NOT the
# www.coles.com.au/api/bff/ endpoint the rest of this project uses. Short
# -lived (confirmed ~15-20min TTL) — re-capture from the app whenever this
# starts 403ing.
#
# Deliberately NOT hardcoded: both are real captured credentials (the
# subscription key may be a longer-lived static per-client key, not just a
# session token) and must never be committed to source control. Set them
# locally (e.g. in a git-ignored `.env` sourced before running, or exported
# in your shell) before running this script.
_COLES_SUBSCRIPTION_KEY = os.environ.get("COLES_APP_SUBSCRIPTION_KEY")
_COLES_X_D_TOKEN = os.environ.get("COLES_APP_X_D_TOKEN")
if not _COLES_SUBSCRIPTION_KEY or not _COLES_X_D_TOKEN:
    raise RuntimeError(
        "Set COLES_APP_SUBSCRIPTION_KEY and COLES_APP_X_D_TOKEN env vars before running "
        "(captured live from the real app — see models.py's Product.aisle_number comment "
        "for how; both are short-lived/rotatable, never hardcode or commit them)"
    )

_COLES_APP_HEADERS = {
    "ocp-apim-subscription-key": _COLES_SUBSCRIPTION_KEY,
    "accept-language": "en-AU;q=1",
    "x-d-token": _COLES_X_D_TOKEN,
    "client": "Android 6.84.0",
    "x-app-version": "Release:6.84.0(20001)",
    "x-device-model": "OnePlus NE2211",
    "x-device-id": "8e12ce38-a8e9-4ffb-a838-4ad17a9cabc7",
    "x-client-os": "Android:9",
    "content-type": "application/json; charset=utf-8",
    "accept-encoding": "gzip",
    "user-agent": "okhttp/5.3.2",
}
_COLES_APP_BASE_URL = "https://apigw.coles.com.au/digital/colesappbff/v3/api/2/products/list"
_BATCH_SIZE = 10  # matches the batch size captured live from the real app


async def fetch_coles_instore_locations(
    session: AsyncSession, store_id: str, skus: List[str]
) -> Dict[int, AisleEnrichment]:
    """Batch-fetch real in-store aisle data for a list of Coles SKUs.

    Returns {retailer_product_id: AisleEnrichment} — only for SKUs whose
    `locations[]` entry actually carried real data (not the dead "Aisle
    information is not available..." placeholder every other call in this
    project sees).
    """
    result: Dict[int, AisleEnrichment] = {}

    async def _fetch_batch(batch: List[str]) -> None:
        url = (
            f"{_COLES_APP_BASE_URL}?storeId={store_id}&shoppingMethod=inStore"
            f"&limit={len(batch)}&includeLiquor=true&includeTobacco=true"
        )
        response = await session.post(
            url,
            headers=_COLES_APP_HEADERS,
            json={"skus": batch},
            impersonate=IMPERSONATE_TARGET,
            timeout=20,
        )
        if response.status_code != 200:
            logger.warning(
                "Coles in-store location batch failed status=%d skus=%s body=%s",
                response.status_code,
                batch,
                response.text[:300],
            )
            return
        payload = response.json()
        for item in payload.get("results") or []:
            sku = item.get("id")
            locations = item.get("locations") or []
            if sku is None or not locations:
                continue
            location = locations[0]
            aisle = location.get("aisle")
            if not aisle:  # real data has "aisle": "Aisle N"; the dead placeholder has no such key
                continue
            coordinates = location.get("indoorCoordinates") or {}
            result[int(sku)] = AisleEnrichment(
                aisle_number=aisle,
                bay_number=location.get("aisleSide"),
                aisle_facing=location.get("facing"),
                aisle_order=location.get("order"),
                indoor_x=coordinates.get("productX"),
                indoor_y=coordinates.get("productY"),
            )

    batches = [skus[i : i + _BATCH_SIZE] for i in range(0, len(skus), _BATCH_SIZE)]
    await asyncio.gather(*(_fetch_batch(batch) for batch in batches))
    return result


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
                logger.info("Fetching real in-store aisle data for %d Coles SKUs at store %s", len(skus), coles_store.store_id)
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
