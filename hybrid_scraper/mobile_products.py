"""Module: full Coles product/price rows fetched entirely via the app's private mobile API.

Why this exists: the website-based crawl (`engine.py`/`bootstrapper.py`,
driven by `ScraperOrchestrator`) *discovers* SKUs by running ~30 search
terms through a real Playwright browser per store — a flaky flow, since it
depends on Coles' anti-bot layer letting the searchbox render, which it
increasingly doesn't under load (see the 2026-08-18 pilot run: 15-30% of
stores failed with "search box never became visible").

That discovery step is unnecessary once a SKU list is already known. This
project already has one: `data/coles_catalogue_categories.csv.csv`, a
29,616-SKU nationwide catalogue with a category per SKU, built from earlier
scrapes. `hybrid_scraper.aisle_enrichment`'s
`POST apigw.coles.com.au/.../products/list?shoppingMethod=inStore` endpoint
looks products up directly BY SKU — no browser, no search terms — and its
response already carries full pricing/name/brand/availability data
alongside the in-store aisle location in one payload (confirmed live, see
`coles_raw_sku_dump.csv`'s captured response). So for any store where the
known-SKU list is a good enough proxy for that store's real assortment,
this endpoint alone can produce every `Product` row this project needs,
with no Playwright/curl_cffi website engine involved at all.

Caveat carried over unchanged from `Product.aisle_number`'s docstring in
`models.py`: this still depends on a live `x-d-token` device-attestation
header, obtainable only by capturing one off the real (BlueStacks-hosted)
app via `hybrid_scraper.mobile_session` — not reverse-engineered, and not
to be reverse-engineered without the user explicitly re-confirming that.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from curl_cffi.requests import AsyncSession

from hybrid_scraper.aisle_enrichment import MobileBatchFetcher
from hybrid_scraper.models import Product, parse_pack_size

logger = logging.getLogger(__name__)

DEFAULT_CATALOGUE_CSV = Path(__file__).resolve().parent.parent / "data" / "coles_catalogue_categories.csv.csv"
UNKNOWN_CATEGORY = "Uncategorized"


def load_catalogue_categories(csv_path: Path = DEFAULT_CATALOGUE_CSV) -> Dict[int, str]:
    """Loads the {retailer_product_id: category} map this module's SKU-by-ID lookups rely on.

    This endpoint (unlike the website's search results) carries no category
    taxonomy of its own — `Product.category` is populated from this
    external map instead, keyed by SKU.
    """
    category_by_sku: Dict[int, str] = {}
    with open(csv_path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                category_by_sku[int(row["retailer_product_id"])] = row["category"]
            except (KeyError, ValueError):
                continue
    logger.info("Loaded %d SKU->category mappings from %s", len(category_by_sku), csv_path)
    return category_by_sku


def _parse_mobile_product(item: Dict, category_by_sku: Dict[int, str], scrape_date: str) -> Optional[Product]:
    """Maps one `products/list?shoppingMethod=inStore` result item straight to a `Product` row.

    Mirrors `CurlCffiEngine._parse_coles_item`'s website-response field
    mapping (same pricing/imageUris/size shape, confirmed live) with two
    differences: `category` has no onlineHeirs equivalent here, so it comes
    from `category_by_sku` instead of the search term's category node; and
    `product_badge`/`aisle_number`/`bay_number`/etc. are populated directly,
    since this response carries real aisle data the website's never did.
    """
    sku = item.get("id")
    if sku is None:
        return None
    try:
        pricing = item.get("pricing") or {}
        pack_size, clean_uom = parse_pack_size(item.get("size"))
        images = item.get("imageUris") or []
        # Same known-unverified CDN prefix caveat as engine.py's
        # `_parse_coles_item` — real image_url values may 404; the relative
        # path (images[0]['uri']) is confirmed correct, only the domain
        # prefix is a guess pending live recon of the real CDN host.
        image_url = f"https://www.coles.com.au{images[0]['uri']}" if images and images[0].get("uri") else None
        was_price = pricing.get("was")
        now_price = pricing.get("now")
        location = (item.get("locations") or [{}])[0]
        aisle = location.get("aisle")
        coordinates = location.get("indoorCoordinates") or {}

        retailer_product_id = int(sku)
        category = category_by_sku.get(retailer_product_id)
        if category is None:
            category = UNKNOWN_CATEGORY

        return Product(
            retailer="Coles",
            retailer_product_id=retailer_product_id,
            scrape_date=scrape_date,
            scraped_at=datetime.now(timezone.utc).isoformat(),
            name=item.get("name", ""),
            clean_brand=item.get("brand"),
            category=category,
            pack_size=pack_size,
            clean_uom=clean_uom,
            image_url=image_url,
            price_display=now_price,
            price_per_uom=pricing.get("comparable"),
            prev_price=str(was_price) if was_price else None,
            stock_status="In Stock" if item.get("availability") == "available" else "Out of Stock",
            product_badge="Special" if pricing.get("onlineSpecial") else None,
            aisle_number=str(aisle) if aisle else None,
            bay_number=location.get("aisleSide"),
            aisle_facing=location.get("facing"),
            aisle_order=location.get("order"),
            indoor_x=coordinates.get("productX"),
            indoor_y=coordinates.get("productY"),
        )
    except (TypeError, ValueError, KeyError) as exc:
        logger.error("Failed to parse Coles mobile product item sku=%r: %s", sku, exc)
        return None


async def fetch_coles_products_via_mobile(
    fetcher: MobileBatchFetcher,
    session: AsyncSession,
    store_id: str,
    skus: List[str],
    category_by_sku: Dict[int, str],
    scrape_date: str,
    on_batch_done: Optional[Callable[[], None]] = None,
) -> List[Product]:
    """Fetches every SKU in `skus` for one store, parsed straight into `Product` rows.

    `fetcher` should be ONE `MobileBatchFetcher` shared across every store
    in a multi-store run (see that class's docstring) — construct it once
    in the calling script, not per store. `on_batch_done`, if given, is
    forwarded to `MobileBatchFetcher.fetch` for per-batch progress reporting.
    """
    products = await fetcher.fetch(
        session,
        store_id,
        skus,
        parse_item=lambda item: _parse_mobile_product(item, category_by_sku, scrape_date),
        on_batch_done=on_batch_done,
    )
    uncategorized = sum(1 for p in products if p.category == UNKNOWN_CATEGORY)
    if uncategorized:
        logger.warning(
            "store_id=%s: %d/%d product(s) had no entry in the master catalogue's SKU->category map",
            store_id,
            uncategorized,
            len(products),
        )
    return products
