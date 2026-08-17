"""Hybrid Playwright + curl_cffi scraping engine for Coles / Woolworths pricing data."""

from hybrid_scraper.models import Product, RetailerName, SessionContext, StoreLocation
from hybrid_scraper.exceptions import (
    ScraperError,
    BootstrapError,
    SessionExpiredError,
    AuthExpiredError,
    RateLimitError,
    ParsingError,
    NetworkError,
    MaxRetriesExceededError,
)
from hybrid_scraper.bootstrapper import PlaywrightBootstrapper
from hybrid_scraper.engine import CurlCffiEngine
from hybrid_scraper.logging_config import configure_logging
from hybrid_scraper.orchestrator import ScraperOrchestrator
from hybrid_scraper.storage import ProductStore, ScrapeStats, store_id_for

__all__ = [
    "Product",
    "RetailerName",
    "SessionContext",
    "StoreLocation",
    "ScraperError",
    "BootstrapError",
    "SessionExpiredError",
    "AuthExpiredError",
    "RateLimitError",
    "ParsingError",
    "NetworkError",
    "MaxRetriesExceededError",
    "PlaywrightBootstrapper",
    "CurlCffiEngine",
    "ScraperOrchestrator",
    "ProductStore",
    "ScrapeStats",
    "store_id_for",
    "configure_logging",
]
