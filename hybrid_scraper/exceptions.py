"""Custom exception hierarchy for the hybrid scraping engine.

Distinct exception types (rather than reusing generic HTTPError/ValueError)
let the orchestrator's fallback loop pattern-match on *why* a request failed
and decide whether a Playwright re-bootstrap can plausibly fix it, versus a
failure that retrying will never resolve.
"""

from __future__ import annotations

from typing import Optional


class ScraperError(Exception):
    """Base class for all errors raised by the hybrid scraper package."""


class BootstrapError(ScraperError):
    """Raised when the Playwright bootstrapper cannot produce a usable SessionContext.

    Covers browser launch failures, navigation timeouts, and anti-bot
    challenges that fail to resolve within the allotted wait window.
    """


class SessionExpiredError(ScraperError):
    """Raised when a cached SessionContext has passed its TTL and must be refreshed."""


class AuthExpiredError(ScraperError):
    """Raised when curl_cffi receives 401/403 or an otherwise invalid/missing token.

    This is the primary trigger for the orchestrator's fallback-to-Playwright
    path: Akamai/Imperva cookies and bearer tokens are short-lived and rotate
    frequently, so a 401/403 on a previously-working session usually means the
    token expired or the anti-bot vendor flagged the fingerprint, not that the
    account/IP is permanently banned.
    """

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(ScraperError):
    """Raised on HTTP 429 or a retailer-specific rate-limit payload."""

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ParsingError(ScraperError):
    """Raised when an API response cannot be decoded or mapped into the Product model."""


class NetworkError(ScraperError):
    """Raised for connection failures, timeouts, or unexpected non-2xx/401/403/429 statuses.

    Kept distinct from AuthExpiredError/RateLimitError because a re-bootstrap
    won't fix a DNS failure or a 500 from the origin — the orchestrator
    treats this as a hard failure for the current store rather than a
    trigger to refresh the session.
    """


class MaxRetriesExceededError(ScraperError):
    """Raised when the orchestrator exhausts its bootstrap-and-retry budget."""

    def __init__(self, message: str, attempts: int, last_error: Optional[Exception] = None) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error
