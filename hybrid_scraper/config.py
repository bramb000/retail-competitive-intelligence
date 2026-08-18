"""Static, retailer-specific configuration: endpoints, stealth JS, and demo data.

Endpoint paths for store-locator/category browsing are best-effort based on
each retailer's public web app structure; they are the pieces most likely to
drift and should be re-verified against the live site (via the browser's
network tab) if a run starts 404ing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from hybrid_scraper.models import RetailerName

# Kept in lock-step with curl_cffi's impersonate="chrome120": the Playwright
# session that harvests cookies and the curl_cffi session that reuses them
# should present the same declared browser version, or the TLS/JA3
# fingerprint (real Chrome 120) won't match the UA string (a mismatch is
# itself a bot signal some vendors check for).
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Shared between engine.py and bootstrapper.py's curl_cffi fallback path —
# defined once here so both modules depend on config rather than each other.
IMPERSONATE_TARGET = "chrome120"


@dataclass(frozen=True)
class RetailerConfig:
    name: RetailerName
    base_url: str
    store_finder_url: str
    categories_url: str
    search_url: str
    # Header name carrying the retailer's API key, when one is required
    # (e.g. Coles' Azure APIM "ocp-apim-subscription-key"). None if the
    # retailer instead relies purely on cookies for the search API.
    subscription_key_header: Optional[str]
    page_size: int
    default_headers: Dict[str, str] = field(default_factory=dict)
    # Store-resolution endpoints — confirmed via live capture (see
    # engine.py's `resolve_store_id`), not guessed:
    # Coles resolves stores through a general-purpose GraphQL endpoint
    # (`graphql_url`, queries defined in engine.py); Woolworths has two
    # dedicated REST endpoints instead (`store_suburb_search_url` geocodes
    # a suburb name to lat/lng, `store_nearby_url` finds stores near a
    # lat/lng). Whichever a retailer doesn't use stays None.
    graphql_url: Optional[str] = None
    store_suburb_search_url: Optional[str] = None
    store_nearby_url: Optional[str] = None


COLES_CONFIG = RetailerConfig(
    name="Coles",
    base_url="https://www.coles.com.au",
    # Confirmed via live capture — Coles' 404 page itself links here.
    store_finder_url="https://www.coles.com.au/find-stores",
    categories_url="https://www.coles.com.au/api/bff/categories",
    search_url="https://www.coles.com.au/api/bff/products/search",
    # Confirmed via live capture: Coles' real header name (their Azure APIM
    # gateway rejects "subscription-key" with 401 "missing subscription key").
    subscription_key_header="ocp-apim-subscription-key",
    page_size=48,
    default_headers={
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    },
    graphql_url="https://www.coles.com.au/api/graphql",
)

WOOLWORTHS_CONFIG = RetailerConfig(
    name="Woolworths",
    base_url="https://www.woolworths.com.au",
    # Confirmed via live capture (extracted from the real Angular "wowssr"
    # store-locator bundle's StoreLocatorService, not guessed).
    store_finder_url="https://www.woolworths.com.au/shop/storelocator",
    categories_url="https://www.woolworths.com.au/apis/ui/PiesCategoriesWithSpecials",
    search_url="https://www.woolworths.com.au/apis/ui/Search/products",
    subscription_key_header=None,
    page_size=36,
    default_headers={
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    },
    store_suburb_search_url="https://www.woolworths.com.au/apis/ui/StoreLocator/Suburbs",
    store_nearby_url="https://www.woolworths.com.au/apis/ui/StoreLocator/Stores",
)

RETAILER_CONFIGS: Dict[RetailerName, RetailerConfig] = {
    "Coles": COLES_CONFIG,
    "Woolworths": WOOLWORTHS_CONFIG,
}

# Injected via BrowserContext.add_init_script before any page script runs, so
# automation fingerprints are patched prior to the anti-bot vendor's own
# detection script executing.
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-AU', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = { runtime: {} };
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
);
"""

# Single-suburb test scope: Ashfield NSW 2131 has both a Coles and a
# Woolworths store, confirmed live via resolve_store_id. Kept deliberately
# small to limit live-request volume against actively-defended sites; the
# larger SAMPLE_SUBURBS list below is for a later, bigger run once this is
# proven out.
TEST_SUBURB = "Ashfield NSW 2131"

# Same suburb name used against both retailers' store locators so the demo
# compares like-for-like locations rather than arbitrary store pairs.
SAMPLE_SUBURBS = [
    "Bondi Junction NSW",
    "Chatswood NSW",
    "Parramatta NSW",
    "Melbourne CBD VIC",
    "Brisbane CBD QLD",
]

# Store targets for the scheduled master scrape (top-level daily_scrape.py).
# Edit this list to add/remove suburbs — each one is independently resolved
# to its nearest Coles store AND its nearest Woolworths store (see
# CurlCffiEngine.resolve_store_id); a suburb with only one retailer nearby
# still yields whichever one resolves, rather than failing the whole list.
DAILY_SCRAPE_SUBURBS: List[str] = [
    "Ashfield NSW 2131",
    "Burwood East VIC 3151",
]
