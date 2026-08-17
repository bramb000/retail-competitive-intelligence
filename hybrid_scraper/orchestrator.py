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
from typing import Any, Dict, List, Optional, Tuple

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
    ) -> Tuple[Dict[str, List[Product]], Dict[str, Exception]]:
        """Run `get_all_products` concurrently for every resolved store target.

        Returns (results, failures) keyed by `"<retailer> - <store_id>"` so a
        single store's exhausted retries or hard failure doesn't abort the
        rest of the batch.
        """

        logger.info("run_for_stores start scrape_date=%s targets=%d", scrape_date, len(targets))

        async def _run_one(store_location: StoreLocation) -> Tuple[str, Any]:
            key = f"{store_location.retailer} - {store_location.store_id}"
            try:
                products = await self.get_all_products(
                    store_location, scrape_date, max_pages_per_term, max_search_terms
                )
                return key, products
            except (MaxRetriesExceededError, NetworkError, ParsingError) as exc:
                logger.error("Store failed permanently key=%s error_type=%s: %s", key, type(exc).__name__, exc)
                return key, exc

        outcomes = await asyncio.gather(*(_run_one(target) for target in targets))

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
