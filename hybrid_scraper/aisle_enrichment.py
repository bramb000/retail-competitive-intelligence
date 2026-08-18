"""Module: Coles-only in-store aisle/location enrichment via the app's private API.

Extracted from `scrape_burwood.py` (its original, one-off home) so
`daily_scrape.py` can reuse the exact same logic rather than duplicating it.
See `Product.aisle_number`'s docstring in `models.py` for the full technical
writeup of this endpoint (why it exists, its exact request/response shape,
and the `x-d-token` blocker `hybrid_scraper.mobile_session` works around).

`MobileBatchFetcher` is the shared batching/session-refresh harness — it
also backs `hybrid_scraper.mobile_products`, which uses the SAME endpoint to
fetch full product/price rows (not just aisle data) directly by SKU,
bypassing the website entirely. See that module for why: this endpoint's
response already carries pricing/name/brand/availability alongside aisle
location in one payload (confirmed live, see `coles_raw_sku_dump.csv`), so
once a store's SKU list is known there's no need to discover it via the
website's flaky search-based crawl at all.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Callable, Dict, List, Optional, TypeVar

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from hybrid_scraper.config import IMPERSONATE_TARGET
from hybrid_scraper.exceptions import NetworkError
from hybrid_scraper.mobile_session import get_mobile_session
from hybrid_scraper.storage import AisleEnrichment

logger = logging.getLogger(__name__)

APP_PRODUCTS_LIST_URL = "https://apigw.coles.com.au/digital/colesappbff/v3/api/2/products/list"
BATCH_SIZE = 10  # matches the batch size captured live from the real app
# Wraps curl_cffi's own `timeout=20` per-request — observed live (2026-08-18
# pilot) that Imperva can silently half-close a connection (server FIN, no
# response) under sustained high-volume traffic against this endpoint,
# which curl_cffi's own timeout did not reliably catch: the socket sat in
# CLOSE_WAIT indefinitely and the request coroutine never returned. This
# `asyncio.wait_for` is a hard backstop that forces the coroutine to give up
# regardless of what curl_cffi/libcurl does underneath.
_REQUEST_HARD_TIMEOUT_SECONDS = 25.0
# If this many requests in a row hard-time-out, treat it as the WAF blocking
# this session outright rather than isolated blips, and abort the whole
# fetch rather than grinding through tens of thousands of 25s timeouts one
# at a time (124k batches x 25s would be ~36 hours of pure stalling).
_MAX_CONSECUTIVE_TIMEOUTS = 8

T = TypeVar("T")

# Only used when COLES_APP_SUBSCRIPTION_KEY/COLES_APP_X_D_TOKEN env vars
# override the BlueStacks capture (see get_coles_app_headers) — these are
# the device-identity fields captured alongside the original manual token,
# app v6.84.0. If overriding with a token from a different app
# install/device, replace these too (they're sent as real request headers,
# not just cosmetic).
_ENV_OVERRIDE_DEVICE_HEADERS = {
    "client": "Android 6.84.0",
    "x-app-version": "Release:6.84.0(20001)",
    "x-device-model": "OnePlus NE2211",
    "x-device-id": "8e12ce38-a8e9-4ffb-a838-4ad17a9cabc7",
    "x-client-os": "Android:9",
    "accept-language": "en-AU;q=1",
    "user-agent": "okhttp/5.3.2",
}


def using_mobile_session() -> bool:
    """True unless both env-var overrides are set (see get_coles_app_headers)."""
    return not (os.environ.get("COLES_APP_SUBSCRIPTION_KEY") and os.environ.get("COLES_APP_X_D_TOKEN"))


def get_coles_app_headers(force_refresh: bool = False) -> Dict[str, str]:
    """Build headers for apigw.coles.com.au.

    Prefers COLES_APP_SUBSCRIPTION_KEY/COLES_APP_X_D_TOKEN env vars (reuse a
    manually-captured token without touching BlueStacks at all); falls back
    to `hybrid_scraper.mobile_session.get_mobile_session` — a cached capture
    if still fresh, otherwise a brand-new live one.
    """
    subscription_key = os.environ.get("COLES_APP_SUBSCRIPTION_KEY")
    x_d_token = os.environ.get("COLES_APP_X_D_TOKEN")
    if subscription_key and x_d_token and not force_refresh:
        headers = {
            **_ENV_OVERRIDE_DEVICE_HEADERS,
            "ocp-apim-subscription-key": subscription_key,
            "x-d-token": x_d_token,
        }
    else:
        headers = get_mobile_session(force_refresh=force_refresh).headers
    return {**headers, "content-type": "application/json; charset=utf-8", "accept-encoding": "gzip"}


class MobileBatchFetcher:
    """Batches SKU lookups through `apigw.coles.com.au`'s products/list endpoint.

    One instance owns one device-attestation session's worth of shared
    state (`headers`/`generation`/`refresh_lock`) — construct ONE instance
    per logical run (whether that run covers one store or many) and reuse
    it for every store, rather than one instance per store. The captured
    `x-d-token` has a short, unpredictable TTL (confirmed live: ~15-20min);
    sharing state means a 401/403 anywhere triggers exactly ONE BlueStacks
    re-capture (via `_refresh_lock` + `_generation`'s single-flight guard),
    with every other in-flight batch just waiting for that result and
    retrying — two concurrent captures would otherwise both try to bind
    mitmdump's proxy port and step on each other.

    `max_concurrent_batches` bounds in-flight requests across the *whole*
    instance (all stores sharing it), not per-store — a store with ~800
    SKUs is ~80 batches, and firing every store's batches uncapped at once
    would burn the device-attestation identity's request budget in one
    uncontrolled burst.

    `pace_min_seconds`/`pace_max_seconds` (both 0 by default, i.e. no
    pacing) add a randomized delay *after* each batch completes and
    *before* its semaphore slot is released for the next one — with
    `max_concurrent_batches=1` this serializes every request into a slow,
    jittered drip rather than a steady bot-like cadence. Added for the
    multi-night 42-store pilot (2026-08): a fast, uncapped run is what
    tripped Imperva's soft-block earlier; deliberately trading speed for
    safety here since that run has days, not minutes, to finish in.
    """

    def __init__(
        self,
        max_concurrent_batches: int = 5,
        pace_min_seconds: float = 0.0,
        pace_max_seconds: float = 0.0,
    ) -> None:
        self._headers = get_coles_app_headers()
        self._generation = 0
        self._refresh_lock = asyncio.Lock()
        self._batch_semaphore = asyncio.Semaphore(max_concurrent_batches)
        self._consecutive_timeouts = 0
        self.timed_out_batches = 0  # cumulative count, for run-end reporting
        self._pace_min_seconds = pace_min_seconds
        self._pace_max_seconds = max(pace_max_seconds, pace_min_seconds)

    async def _maybe_refresh(self, seen_generation: int) -> None:
        if not using_mobile_session():
            return  # env-var override mode has no way to mint a new token
        async with self._refresh_lock:
            if self._generation == seen_generation:
                logger.warning("Coles app session appears expired — forcing a fresh BlueStacks capture")
                self._headers = get_coles_app_headers(force_refresh=True)
                self._generation += 1

    async def _fetch_batch_items(
        self, session: AsyncSession, store_id: str, batch: List[str], retry_on_auth_failure: bool = True
    ) -> List[Dict]:
        current_generation = self._generation
        url = (
            f"{APP_PRODUCTS_LIST_URL}?storeId={store_id}&shoppingMethod=inStore"
            f"&limit={len(batch)}&includeLiquor=true&includeTobacco=true"
        )
        try:
            response = await asyncio.wait_for(
                session.post(
                    url,
                    headers=self._headers,
                    json={"skus": batch},
                    impersonate=IMPERSONATE_TARGET,
                    timeout=20,
                ),
                timeout=_REQUEST_HARD_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, RequestException) as exc:
            # Two distinct failure modes land here, both treated the same
            # way — a single bad batch must not sink the other few thousand
            # in the same store's fetch (they're independent `asyncio.gather`
            # tasks; an uncaught exception in one cancels every sibling task
            # and re-raises, which is what silently lost an entire store's
            # 3000 SKUs in the 2026-08-18 stress test before this except
            # clause covered `RequestException` too):
            #   1. asyncio.TimeoutError — our own `_REQUEST_HARD_TIMEOUT_SECONDS`
            #      backstop fired because the request never returned.
            #   2. RequestException — curl_cffi's own internal timeout/
            #      connection error fired first (confirmed live: "curl: (28)
            #      Operation timed out after 59730ms" — curl's own timeout
            #      clearly isn't bounded by the `timeout=20` kwarg here, and
            #      is not a plain asyncio.TimeoutError, so it needs its own
            #      except arm rather than being folded into the case above).
            self.timed_out_batches += 1
            self._consecutive_timeouts += 1
            logger.warning(
                "Coles mobile product batch failed with %s (possible WAF soft-block) "
                "store_id=%s skus=%s — consecutive_timeouts=%d",
                type(exc).__name__,
                store_id,
                batch,
                self._consecutive_timeouts,
            )
            if self._consecutive_timeouts >= _MAX_CONSECUTIVE_TIMEOUTS:
                raise NetworkError(
                    f"Aborting: {self._consecutive_timeouts} consecutive request failures against "
                    "apigw.coles.com.au — this looks like a sustained WAF block, not isolated blips"
                ) from exc
            return []
        self._consecutive_timeouts = 0
        if response.status_code in (401, 403) and retry_on_auth_failure:
            await self._maybe_refresh(current_generation)
            return await self._fetch_batch_items(session, store_id, batch, retry_on_auth_failure=False)
        if response.status_code != 200:
            logger.warning(
                "Coles mobile product batch failed store_id=%s status=%d skus=%s body=%s",
                store_id,
                response.status_code,
                batch,
                response.text[:300],
            )
            return []
        return (response.json().get("results")) or []

    async def fetch(
        self,
        session: AsyncSession,
        store_id: str,
        skus: List[str],
        parse_item: Callable[[Dict], Optional[T]],
        on_batch_done: Optional[Callable[[], None]] = None,
    ) -> List[T]:
        """Batch-fetch `skus` for one store, mapping each raw result item through `parse_item`.

        SKUs the store doesn't stock simply don't appear in a batch's
        `results` (confirmed live) rather than raising, so a store with a
        smaller assortment than the full SKU list naturally yields fewer
        rows — no per-SKU error handling needed for that case.

        `on_batch_done`, if given, is called synchronously (not awaited)
        right after each batch's items are parsed — a large SKU list can be
        thousands of batches, so callers use this for real progress
        reporting rather than an indeterminate spinner for the whole fetch.
        """
        result_lock = asyncio.Lock()
        results: List[T] = []

        async def _run_batch(batch: List[str]) -> None:
            async with self._batch_semaphore:
                items = await self._fetch_batch_items(session, store_id, batch)
                if self._pace_max_seconds > 0:
                    # Deliberately still holding the semaphore slot during
                    # this sleep — with max_concurrent_batches=1 that's what
                    # turns this into a real minimum-interval pace rather
                    # than just a concurrency cap (which alone doesn't stop
                    # requests firing back-to-back the instant one finishes).
                    await asyncio.sleep(random.uniform(self._pace_min_seconds, self._pace_max_seconds))
            for item in items:
                parsed = parse_item(item)
                if parsed is None:
                    continue
                async with result_lock:
                    results.append(parsed)
            if on_batch_done is not None:
                on_batch_done()

        batches = [skus[i : i + BATCH_SIZE] for i in range(0, len(skus), BATCH_SIZE)]
        await asyncio.gather(*(_run_batch(batch) for batch in batches))
        return results


def _parse_aisle_item(item: Dict) -> Optional[tuple]:
    """(sku, AisleEnrichment) for one products/list result item, or None if it has no real aisle data."""
    sku = item.get("id")
    locations = item.get("locations") or []
    if sku is None or not locations:
        return None
    location = locations[0]
    aisle = location.get("aisle")
    if not aisle:  # real data has "aisle": "Aisle N"; the dead placeholder has no such key
        return None
    coordinates = location.get("indoorCoordinates") or {}
    return (
        int(sku),
        AisleEnrichment(
            aisle_number=aisle,
            bay_number=location.get("aisleSide"),
            aisle_facing=location.get("facing"),
            aisle_order=location.get("order"),
            indoor_x=coordinates.get("productX"),
            indoor_y=coordinates.get("productY"),
        ),
    )


async def fetch_coles_instore_locations(
    session: AsyncSession, store_id: str, skus: List[str], max_concurrent_batches: int = 5
) -> Dict[int, AisleEnrichment]:
    """Batch-fetch real in-store aisle data for a list of Coles SKUs.

    Returns {retailer_product_id: AisleEnrichment} — only for SKUs whose
    `locations[]` entry actually carried real data (not the dead "Aisle
    information is not available..." placeholder every other call in this
    project sees).

    Constructs its own single-use `MobileBatchFetcher` — fine for this
    function's existing callers (`daily_scrape.py`, and any single-store
    enrichment pass run right after a website scrape for that one store),
    since only one store's worth of batches are ever in flight through it
    at a time. A multi-store run sharing one session/refresh state across
    stores should build one `MobileBatchFetcher` itself instead — see
    `hybrid_scraper.mobile_products`.
    """
    fetcher = MobileBatchFetcher(max_concurrent_batches=max_concurrent_batches)
    pairs = await fetcher.fetch(session, store_id, skus, parse_item=_parse_aisle_item)
    return dict(pairs)
