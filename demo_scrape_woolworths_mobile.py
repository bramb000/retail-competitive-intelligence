"""Ashfield Woolworths mobile proof: session + Product Finder price/placement.

Usage:
  python demo_scrape_woolworths_mobile.py
  python demo_scrape_woolworths_mobile.py --store-id 1213 --product-ids 36066,277728
  python demo_scrape_woolworths_mobile.py --store-id 1213 --term "Tim tam" --limit 2

Uses captured app session headers when present (`.tools/woolworths_mobile_session.json`
/ mitm capture with `x-acf-sensor-data`); otherwise mints a guest token.
GraphQL goes through local mitmdump when available.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date
from typing import List, Optional

from curl_cffi.requests import AsyncSession

from hybrid_scraper.bootstrapper import PlaywrightBootstrapper
from hybrid_scraper.engine import CurlCffiEngine
from hybrid_scraper.logging_config import configure_logging
from hybrid_scraper.models import Product
from hybrid_scraper.storage import ProductStore, store_id_for
from hybrid_scraper.woolworths_aisle_enrichment import (
    fetch_products_by_ids,
    product_summary,
    search_products,
)
from hybrid_scraper.woolworths_mobile_session import get_woolworths_mobile_session

logger = logging.getLogger("hybrid_scraper.demo_woolworths_mobile")

# Handoff / prior DuckDB: Woolworths Ashfield was store 1213.
DEFAULT_ASHFIELD_STORE_ID = "1213"


async def _resolve_ashfield_store_id() -> str:
    try:
        async with PlaywrightBootstrapper() as bootstrapper, CurlCffiEngine(concurrency=2) as engine:
            store = await engine.resolve_store_id("Woolworths", "Ashfield NSW 2131")
            logger.info(
                "Resolved Woolworths Ashfield -> store_id=%s name=%r",
                store.store_id,
                store.store_name,
            )
            return str(store.store_id)
    except Exception as exc:
        logger.warning("Store resolve failed (%s); using default %s", exc, DEFAULT_ASHFIELD_STORE_ID)
        return DEFAULT_ASHFIELD_STORE_ID


def _cards_to_products(cards: list, store_id: str, scrape_date: str) -> List[Product]:
    from datetime import datetime, timezone

    products: List[Product] = []
    scraped_at = datetime.now(timezone.utc).isoformat()
    for card in cards:
        summary = product_summary(card)
        pid = summary.get("product_id")
        if not pid:
            continue
        try:
            retailer_product_id = int(pid)
        except (TypeError, ValueError):
            continue
        products.append(
            Product(
                retailer="Woolworths",
                retailer_product_id=retailer_product_id,
                name=summary.get("name") or f"SKU {pid}",
                category="Unknown",
                stock_status="Available" if summary.get("is_available") else "Unknown",
                price_display=summary.get("price"),
                price_per_uom=summary.get("unit_price"),
                prev_price=str(summary["was_price"]) if summary.get("was_price") is not None else None,
                aisle_number=summary.get("aisle_number"),
                bay_number=summary.get("bay_number"),
                indoor_x=summary.get("indoor_x"),
                indoor_y=summary.get("indoor_y"),
                scrape_date=scrape_date,
                scraped_at=scraped_at,
            )
        )
    return products


async def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-id", default=None, help="Woolworths store id (default: resolve Ashfield)")
    parser.add_argument("--term", default=None, help="Search term (GraphQL; needs app sensor session)")
    parser.add_argument(
        "--product-ids",
        default="36066,277728",
        help="Comma-separated SKUs (default: Ashfield Tim Tam + white bread from live capture)",
    )
    parser.add_argument("--limit", type=int, default=2, help="Max products from search")
    parser.add_argument("--emulator", action="store_true", help="Prefer mitm capture over guest mint")
    parser.add_argument("--record", action="store_true", help="Also write rows into DuckDB")
    parser.add_argument("--force-session", action="store_true", help="Force fresh mobile session")
    args = parser.parse_args(argv)

    configure_logging()
    get_woolworths_mobile_session(force_refresh=args.force_session, prefer_emulator=args.emulator, postcode="2131")

    store_id = args.store_id or await _resolve_ashfield_store_id()
    scrape_date = date.today().isoformat()

    async with AsyncSession() as session:
        if args.term:
            cards = await search_products(session, store_id, args.term, page_size=max(args.limit, 2))
            cards = cards[: args.limit]
        else:
            ids = [p.strip() for p in (args.product_ids or "").split(",") if p.strip()]
            cards = await fetch_products_by_ids(session, store_id, ids)
    if not cards:
        logger.error("No products returned for store_id=%s", store_id)
        return 1

    summaries = [product_summary(c) for c in cards]
    # Attach REST raw price object for proof visibility when dollar amount missing.
    for summary, card in zip(summaries, cards):
        if card.get("_rest_raw_price") is not None:
            summary["rest_price_object"] = card["_rest_raw_price"]
            summary["source"] = card.get("_source")
    print(json.dumps({"store_id": store_id, "products": summaries}, indent=2))

    with_placement = [
        s
        for s in summaries
        if (s.get("aisle_number") or s.get("location_text"))
        and "not available" not in str(s.get("aisle_number") or s.get("location_text") or "").lower()
        and "see in store" not in str(s.get("aisle_number") or s.get("location_text") or "").lower()
    ]
    with_identity = [s for s in summaries if s.get("product_id") and s.get("name")]
    with_price = [s for s in summaries if s.get("price") is not None]
    print(
        f"\nProof: {len(summaries)} product(s), "
        f"{len(with_identity)} with identity, {len(with_price)} with price, "
        f"{len(with_placement)} with real placement",
        file=sys.stderr,
    )
    if not with_identity:
        logger.error("Proof failed: no products")
        return 2
    if not with_price:
        logger.warning(
            "No dollar price on these SKUs yet; GraphQL ProductCard (sensor + mitm) is required"
        )
    if not with_placement:
        logger.warning(
            "No real inStoreLocation yet (placeholder or missing). "
            "Need GraphQL with x-acf-sensor-data routed via local mitmdump."
        )

    if args.record:
        products = _cards_to_products(cards, store_id, scrape_date)
        store_key = store_id_for("Woolworths", store_id)
        with ProductStore() as product_store:
            from hybrid_scraper.models import StoreLocation

            product_store.record_scrape(
                StoreLocation(
                    retailer="Woolworths",
                    store_id=str(store_id),
                    store_name=f"Woolworths Ashfield ({store_id})",
                    suburb_name="Ashfield",
                    state="NSW",
                    postcode="2131",
                    latitude=-33.8895,
                    longitude=151.1250,
                ),
                products,
                scrape_date,
            )
            logger.info("Recorded %d products under %s", len(products), store_key)

    # Plan success: at least one SKU with both dollar price and real placement.
    if with_price and with_placement:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
