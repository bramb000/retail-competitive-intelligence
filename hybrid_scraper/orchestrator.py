"""Module 3: orchestrator tying the Playwright bootstrapper and curl_cffi engine
together with an automatic bootstrap-and-retry fallback loop.

Fallback execution flow, per store:
    1. Attempt the full-catalog fetch via `CurlCffiEngine` using the cached
       (or freshly bootstrapped) `SessionContext`.
    2. If curl_cffi raises `AuthExpiredError` (401/403) or `RateLimitError`
       (429) — i.e. the vendor's session/token state changed underneath us —
       force a new Playwright bootstrap to obtain fresh cookies/headers and
       retry the same batch against the refreshed session.
    3. `NetworkError`/`ParsingError` are treated as hard failures for that
       store (re-bootstrapping a session can't fix a DNS failure or a
       changed response schema) and are surfaced immediately rather than
       retried.
    4. After `max_retries` bootstrap-and-retry cycles, raise
       `MaxRetriesExceededError` for that store rather than retrying forever.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from hybrid_scraper.bootstrapper import PlaywrightBootstrapper
from hybrid_scraper.engine import CurlCffiEngine
from hybrid_scraper.exceptions import (
    AuthExpiredError,
    BootstrapError,
    MaxRetriesExceededError,
    NetworkError,
    ParsingError,
    RateLimitError,
    SessionExpiredError,
)
from hybrid_scraper.models import Product, RetailerName, SessionContext, StoreLocation

logger = logging.getLogger(__name__)

StoreKey = Tuple[RetailerName, str]


class ScraperOrchestrator:
    def __init__(
        self,
        bootstrapper: PlaywrightBootstrapper,
        engine: CurlCffiEngine,
        max_retries: int = 3,
    ) -> None:
        self._bootstrapper = bootstrapper
        self._engine = engine
        self._max_retries = max_retries
        self._sessions: Dict[StoreKey, SessionContext] = {}

    async def _get_session(self, retailer: RetailerName, store_id: str, force_refresh: bool) -> SessionContext:
        key = (retailer, store_id)
        cached = self._sessions.get(key)
        if not force_refresh and cached is not None and not cached.is_expired():
            logger.debug("Session cache hit retailer=%s store_id=%s", retailer, store_id)
            return cached
        logger.info(
            "Session cache miss/refresh retailer=%s store_id=%s force_refresh=%s — bootstrapping",
            retailer,
            store_id,
            force_refresh,
        )
        session = await self._bootstrapper.bootstrap(retailer, store_id)
        self._sessions[key] = session
        return session

    async def get_all_products(
        self,
        store_location: StoreLocation,
        scrape_date: str,
        max_pages_per_term: int = 5,
        max_search_terms: Optional[int] = 30,
    ) -> List[Product]:
        """Fetch every SKU for one resolved store, bootstrapping/retrying on auth failures.

        `max_pages_per_term`/`max_search_terms` are forwarded to
        `CurlCffiEngine.fetch_all_products_for_store` — see its docstring
        for why an unbounded crawl can outlive a short-TTL anti-bot session.
        """
        retailer, store_id = store_location.retailer, store_location.store_id
        last_error: Optional[Exception] = None

        for attempt in range(1, self._max_retries + 1):
            logger.info(
                "get_all_products attempt=%d/%d retailer=%s store_id=%s scrape_date=%s",
                attempt,
                self._max_retries,
                retailer,
                store_id,
                scrape_date,
            )
            try:
                session = await self._get_session(retailer, store_id, force_refresh=(attempt > 1))
                return await self._engine.fetch_all_products_for_store(
                    session, scrape_date, max_pages_per_term, max_search_terms
                )
            except (AuthExpiredError, RateLimitError, SessionExpiredError, BootstrapError) as exc:
                last_error = exc
                logger.warning(
                    "Fallback triggered attempt=%d/%d retailer=%s store_id=%s error_type=%s: %s "
                    "— re-bootstrapping and retrying",
                    attempt,
                    self._max_retries,
                    retailer,
                    store_id,
                    type(exc).__name__,
                    exc,
                )
                if isinstance(exc, RateLimitError) and exc.retry_after:
                    logger.info("Sleeping %.1fs before retry per Retry-After", exc.retry_after)
                    await asyncio.sleep(exc.retry_after)
                # Loop back around: next iteration's force_refresh=True triggers a fresh bootstrap.
                continue

        logger.error(
            "Exhausted retries retailer=%s store_id=%s attempts=%d last_error=%s",
            retailer,
            store_id,
            self._max_retries,
            last_error,
        )
        raise MaxRetriesExceededError(
            f"Exhausted {self._max_retries} bootstrap-and-retry attempts for {retailer} store {store_id}",
            attempts=self._max_retries,
            last_error=last_error,
        )

    async def run_for_stores(
        self,
        targets: List[StoreLocation],
        scrape_date: str,
        max_pages_per_term: int = 5,
        max_search_terms: Optional[int] = 30,
        max_concurrent_stores: Optional[int] = None,
        launch_stagger_seconds: float = 0.0,
        on_store_start: Optional[Callable[[StoreLocation], Awaitable[None]]] = None,
        on_store_done: Optional[Callable[[StoreLocation, Any], Awaitable[None]]] = None,
    ) -> Tuple[Dict[str, List[Product]], Dict[str, Exception]]:
        """Run `get_all_products` concurrently for every resolved store target.

        Returns (results, failures) keyed by `"<retailer> - <store_id>"` so a
        single store's exhausted retries or hard failure doesn't abort the
        rest of the batch.

        By default every target launches at once (unchanged from before —
        existing callers don't pass the params below, so behavior for them
        is identical). At larger store counts this is unsafe: each store's
        `get_all_products` call may trigger a full Playwright anti-bot
        challenge-solve, and nothing here previously capped how many of
        those ran simultaneously.

        `max_concurrent_stores`, when set, bounds simultaneous store
        executions via a semaphore. `launch_stagger_seconds` delays the
        *attempt* to start each store's task (before it even queues for the
        semaphore) by `index * launch_stagger_seconds`, so a batch of
        targets doesn't all hit the semaphore — and the anti-bot vendor's
        edge — in the same instant. `on_store_start`, if given, is awaited
        right as a store's fetch actually begins — after its stagger delay
        and after it has acquired the semaphore slot — so it reflects real
        concurrency (e.g. exactly `max_concurrent_stores` "in flight" at
        once), not just launch order. `on_store_done`, if given, is awaited
        immediately once a store's outcome (product list or exception) is
        known, letting a caller persist results incrementally instead of
        waiting for every target to finish (so an interrupted run keeps
        whatever already completed).
        """

        logger.info("run_for_stores start scrape_date=%s targets=%d", scrape_date, len(targets))

        store_semaphore = asyncio.Semaphore(max_concurrent_stores) if max_concurrent_stores else None

        async def _execute(store_location: StoreLocation, key: str) -> Any:
            try:
                return await self.get_all_products(store_location, scrape_date, max_pages_per_term, max_search_terms)
            except (MaxRetriesExceededError, NetworkError, ParsingError) as exc:
                logger.error("Store failed permanently key=%s error_type=%s: %s", key, type(exc).__name__, exc)
                return exc

        async def _run_one(index: int, store_location: StoreLocation) -> Tuple[str, Any]:
            key = f"{store_location.retailer} - {store_location.store_id}"
            if launch_stagger_seconds:
                await asyncio.sleep(index * launch_stagger_seconds)

            if store_semaphore is not None:
                async with store_semaphore:
                    if on_store_start is not None:
                        await on_store_start(store_location)
                    outcome = await _execute(store_location, key)
            else:
                if on_store_start is not None:
                    await on_store_start(store_location)
                outcome = await _execute(store_location, key)

            if on_store_done is not None:
                await on_store_done(store_location, outcome)
            return key, outcome

        outcomes = await asyncio.gather(*(_run_one(i, target) for i, target in enumerate(targets)))

        results: Dict[str, List[Product]] = {}
        failures: Dict[str, Exception] = {}
        for key, outcome in outcomes:
            if isinstance(outcome, Exception):
                failures[key] = outcome
            else:
                results[key] = outcome
        logger.info(
            "run_for_stores done scrape_date=%s succeeded=%d failed=%d",
            scrape_date,
            len(results),
            len(failures),
        )
        return results, failures
