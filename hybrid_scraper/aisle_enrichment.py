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
from hybrid_scraper.exceptions import AuthExpiredError, MobileTokenCaptureError, NetworkError
from hybrid_scraper.mobile_session import get_mobile_session, pool_fresh_count
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
# override the Android Emulator capture (see get_coles_app_headers) — these are
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
    manually-captured token without touching Android Emulator at all); falls back
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
        headers = get_mobile_session(
            force_refresh=force_refresh,
            capture_timeout=150.0,
            allow_stale=not force_refresh,
        ).headers
    return {**headers, "content-type": "application/json; charset=utf-8", "accept-encoding": "gzip"}


class MobileBatchFetcher:
    """Batches SKU lookups through `apigw.coles.com.au`'s products/list endpoint.

    One instance owns one device-attestation session's worth of shared
    state (`headers`/`generation`/`refresh_lock`) — construct ONE instance
    per logical run (whether that run covers one store or many) and reuse
    it for every store, rather than one instance per store. The captured
    `x-d-token` has a short, unpredictable TTL (confirmed live: ~15-20min);
    sharing state means a 401/403 anywhere triggers exactly ONE Android Emulator
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
        self._auth_failures = 0
        self._max_auth_recoveries = 3

    async def _human_pace_sleep(self) -> None:
        """Randomized inter-request gap: triangular mid-bias + rare short pause.

        Extra "distraction" pauses are off when COLES_HUMAN_EXTRA_PAUSE=0 (default
        for Ashfield deep scrape speed targets), otherwise ~1 in 40 batches get
        a short bump so pacing isn't perfectly metronomic.
        """
        if self._pace_max_seconds <= 0:
            return
        mode = (self._pace_min_seconds + self._pace_max_seconds) / 2.0
        delay = random.triangular(self._pace_min_seconds, self._pace_max_seconds, mode)
        extra_on = os.environ.get("COLES_HUMAN_EXTRA_PAUSE", "0").strip() not in ("0", "false", "False", "")
        if extra_on and random.random() < (1.0 / 40.0):
            extra = random.uniform(8.0, 25.0)
            logger.info(
                "human-like extra pause base=%.1fs extra=%.1fs total=%.1fs",
                delay,
                extra,
                delay + extra,
            )
            delay += extra
        await asyncio.sleep(delay)

    async def _pause_and_recreate_session(self) -> None:
        """On 403: pause, try pooled token, else mint a fresh one. Never keep a dead token."""
        pause = random.uniform(20.0, 45.0)
        logger.warning(
            "coles auth expired — pausing %.0fs then recreating token (pool_fresh=%d)",
            pause,
            pool_fresh_count(),
        )
        await asyncio.sleep(pause)
        try:
            session = await asyncio.to_thread(
                lambda: get_mobile_session(force_refresh=True, capture_timeout=150.0, allow_stale=False)
            )
        except MobileTokenCaptureError as exc:
            raise AuthExpiredError(
                f"Coles token recreate failed after pause: {exc}",
                status_code=403,
            ) from exc
        self._headers = {
            **session.headers,
            "content-type": "application/json; charset=utf-8",
            "accept-encoding": "gzip",
        }
        self._generation += 1
        self._auth_failures = 0
        logger.info("coles auth recreated generation=%d headers=%d", self._generation, len(self._headers))

    async def _maybe_refresh(self, seen_generation: int) -> None:
        if not using_mobile_session():
            return  # env-var override mode has no way to mint a new token
        async with self._refresh_lock:
            if self._generation != seen_generation:
                return  # another coroutine already refreshed
            self._auth_failures += 1
            if self._auth_failures > self._max_auth_recoveries:
                raise AuthExpiredError(
                    f"Coles auth recovery failed {self._auth_failures} times — stopping to avoid empty batches",
                    status_code=403,
                )
            await self._pause_and_recreate_session()

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
        if response.status_code in (401, 403):
            if retry_on_auth_failure:
                await self._maybe_refresh(current_generation)
                return await self._fetch_batch_items(session, store_id, batch, retry_on_auth_failure=False)
            # Refresh already tried and this request still unauthorized — stop the run.
            raise AuthExpiredError(
                f"Coles products/list still {response.status_code} after token recreate "
                f"(store_id={store_id}, skus={batch[:3]}…)",
                status_code=response.status_code,
            )
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
                # Deliberately still holding the semaphore slot during this sleep —
                # with max_concurrent_batches=1 that's a real minimum-interval pace.
                await self._human_pace_sleep()
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

    async def fetch_sequential(
        self,
        session: AsyncSession,
        store_id: str,
        skus: List[str],
        parse_item: Callable[[Dict], Optional[T]],
        start_batch_index: int = 0,
        on_raw_batch: Optional[Callable[[int, List[str], List[Dict]], None]] = None,
        on_batch_done: Optional[Callable[[int], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> List[T]:
        """Like `fetch`, but strictly sequential and resumable by batch index.

        Used by the Ashfield deep scrape so a Ctrl+C / time-budget stop can
        resume without re-hitting already-completed batches. `on_raw_batch`
        receives (batch_index, sku_batch, raw_result_items) for bronze writes.
        """
        batches = [skus[i : i + BATCH_SIZE] for i in range(0, len(skus), BATCH_SIZE)]
        results: List[T] = []
        logger.info(
            "fetch_sequential start store_id=%s batches=%d start_batch_index=%d skus=%d pace=%.1f-%.1fs",
            store_id,
            len(batches),
            start_batch_index,
            len(skus),
            self._pace_min_seconds,
            self._pace_max_seconds,
        )
        for batch_index, batch in enumerate(batches):
            if batch_index < start_batch_index:
                continue
            if should_stop is not None and should_stop():
                logger.warning(
                    "fetch_sequential stop requested store_id=%s at batch_index=%d/%d",
                    store_id,
                    batch_index,
                    len(batches),
                )
                break
            async with self._batch_semaphore:
                items = await self._fetch_batch_items(session, store_id, batch)
                await self._human_pace_sleep()
            if on_raw_batch is not None:
                on_raw_batch(batch_index, batch, items)
            for item in items:
                parsed = parse_item(item)
                if parsed is not None:
                    results.append(parsed)
            if on_batch_done is not None:
                on_batch_done(batch_index)
        logger.info(
            "fetch_sequential done store_id=%s parsed=%d timed_out_batches=%d",
            store_id,
            len(results),
            self.timed_out_batches,
        )
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
