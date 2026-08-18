"""Module 2: high-speed async HTTP engine built on curl_cffi.

Reuses a `SessionContext` harvested by `hybrid_scraper.bootstrapper` to make
fast, concurrent requests against each retailer's internal search API.
`impersonate="chrome120"` aligns the TLS/JA3 fingerprint with the declared
Chrome user-agent (see `config.DEFAULT_USER_AGENT`) — a client that claims to
be Chrome 120 over HTTP headers but negotiates TLS like Python's stdlib is
itself a bot signal several vendors fingerprint on.

Request shapes and response field names below are taken from live captures
against the real APIs (not guessed) — see the parser docstrings for what was
actually observed versus left as best-effort.

Enumeration strategy: both retailers' real search APIs are keyword-search
endpoints, not category-browse endpoints — a live capture confirmed Coles'
`/api/bff/products/search` and Woolworths' `/apis/ui/Search/products` both
require a non-empty search term (Woolworths 400s on an empty term; a
category-id-only filter 400s on both). So "enumerate all SKUs" is
implemented as paginating a list of search terms (sourced from each
retailer's own category-tree endpoint, when reachable) rather than walking a
category id — this is the only mechanism the live APIs actually support.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from curl_cffi.requests import AsyncSession

from hybrid_scraper.aisle_enrichment import get_coles_app_headers
from hybrid_scraper.config import IMPERSONATE_TARGET, RETAILER_CONFIGS
from hybrid_scraper.exceptions import AuthExpiredError, BootstrapError, NetworkError, ParsingError, RateLimitError
from hybrid_scraper.models import Product, RetailerName, SessionContext, StoreLocation, parse_pack_size

logger = logging.getLogger(__name__)

# Coles' website categories_url (config.COLES_CONFIG.categories_url) 404s in
# production and has never been confirmed working — see FALLBACK_SEARCH_TERMS
# below. The real, complete category tree instead lives on the mobile app's
# own backend, confirmed live: one GET returns the full department ->
# subcategory -> sub-subcategory tree (confirmed live: 20 top-level
# departments, ~1000 leaf categories) via the same device-attested session
# `aisle_enrichment.py` already manages. Its sibling category-driven PRODUCT
# search endpoint (`v3/api/3/products/search`) needs a SEPARATE OAuth/guest
# bearer token this project hasn't captured (a real in-app category browse
# would need to be driven to observe one) — so this only pulls category
# NAMES from the mobile side, then hands them to the existing, already-
# working website search API as search terms, same as FALLBACK_SEARCH_TERMS
# but with ~1000 real taxonomic names instead of 20 generic grocery words.
_COLES_MOBILE_CATEGORIES_URL = "https://apigw.coles.com.au/digital/colesappbff/v3/api/2/products/categories"

# Cross-cutting value/bundle groupings that duplicate items already
# reachable under their real department (same concept as
# `_is_promotional_branch` below, for Woolworths' "Specials" branch) — only
# one confirmed live example for Coles ("Big Pack Value"), matched only at
# the top level so a legitimately-named subcategory elsewhere isn't skipped.
_COLES_PROMOTIONAL_TOP_LEVEL_MARKERS = ("special", "value")

# Guards get_coles_app_headers() (which may synchronously trigger a live
# BlueStacks capture on a cache miss) so two stores' category-tree fetches
# running concurrently don't both try to bind mitmdump's proxy port at once
# — the same port-conflict risk `aisle_enrichment.py`'s single-flight
# refresh lock protects against, for this separate call site.
_coles_mobile_categories_lock = asyncio.Lock()

# --- Store resolution: GraphQL query documents captured verbatim from a live
# Coles JS bundle (_app-*.js, "query FindStores"/"query GetStoreLocationSuggestions"
# plus their fragment dependencies) — not hand-written guesses. See
# `CurlCffiEngine.resolve_store_id` for how these are used.
_COLES_SUBURB_SUGGESTIONS_QUERY = """
query GetStoreLocationSuggestions($term: String!, $count: Int) {
  localitySearch(term: $term, count: $count) {
    results {
      postcode
      state
      suburb
      latitude
      longitude
    }
  }
}
"""

_COLES_FIND_STORES_QUERY = """
query FindStores($latitude: Float!, $longitude: Float!, $brandIds: [BrandId!], $count: Float!, $distance: Float) {
  stores(
    latitude: $latitude
    longitude: $longitude
    brandIds: $brandIds
    count: $count
    distance: $distance
    isTrading: true
  ) {
    results {
      distance
      store {
        ...storeFields
        hours {
          ...hoursTodayFields
        }
      }
    }
  }
}

fragment storeFields on Store {
  id
  name
  address {
    state
    suburb
    addressLine
    postcode
  }
  position {
    latitude
    longitude
  }
  brand {
    name
    storeFinderId
    id
  }
  phone
  isTrading
  services {
    name
    type
  }
}

fragment hoursTodayFields on Hours {
  today {
    time
    holidayReason
    isOpen
  }
}
"""

_AU_STATES = {"NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"}


def _parse_suburb_query(suburb: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Split a suburb query like "Ashfield NSW 2131" into (term, postcode, state).

    `term` is what gets sent as the free-text search (e.g. "Ashfield");
    `postcode`/`state`, when present in the input, disambiguate between
    same-named suburbs in different states (both retailers' suburb-search
    endpoints returned multiple "Ashfield" matches live — NSW, QLD, WA).
    """
    postcode_match = re.search(r"\b(\d{4})\b", suburb)
    postcode = postcode_match.group(1) if postcode_match else None
    state = None
    terms = []
    for token in suburb.split():
        if token.upper() in _AU_STATES:
            state = token.upper()
            continue
        if token.isdigit() and len(token) == 4:
            continue
        terms.append(token)
    term = " ".join(terms).strip() or suburb
    return term, postcode, state


def _pick_best_suburb_match(
    results: List[Dict[str, Any]],
    postcode_filter: Optional[str],
    state_filter: Optional[str],
    postcode_key: str,
    state_key: str,
) -> Optional[Dict[str, Any]]:
    """Disambiguate multiple same-named-suburb results by postcode, then state."""
    if not results:
        return None
    if postcode_filter:
        for result in results:
            if str(result.get(postcode_key)) == postcode_filter:
                return result
    if state_filter:
        for result in results:
            if str(result.get(state_key, "")).upper() == state_filter:
                return result
    return results[0]


# Used only if a retailer's own category-tree endpoint is unreachable/empty —
# guarantees the engine still produces real results rather than nothing.
# (Coles' categories_url in particular is unverified; Woolworths' is
# confirmed working, so this fallback is Coles' realistic path today.)
FALLBACK_SEARCH_TERMS: Tuple[str, ...] = (
    "milk",
    "bread",
    "eggs",
    "cheese",
    "chicken",
    "beef",
    "bananas",
    "apples",
    "yoghurt",
    "pasta",
    "rice",
    "cereal",
    "coffee",
    "tea",
    "chips",
    "chocolate",
    "soft drink",
    "juice",
    "frozen vegetables",
    "toilet paper",
)

# Category-tree branches that are cross-cutting promotional filters rather
# than real product taxonomy (confirmed live: Woolworths' "specialsgroup" /
# "Specials" is the first top-level branch and would otherwise mislabel
# every item under it). Matched case-insensitively against both node id and
# display name, since either can carry the "specials" marker depending on
# the retailer's tree shape.
_PROMOTIONAL_BRANCH_MARKERS = ("specialsgroup", "specials")


def _is_promotional_branch(node_id: Optional[str], name: Optional[str]) -> bool:
    haystack = f"{node_id or ''} {name or ''}".lower()
    return any(marker in haystack for marker in _PROMOTIONAL_BRANCH_MARKERS)


@dataclass(frozen=True)
class CategoryNode:
    """A leaf category to search for, with its breadcrumb for row-tagging.

    `id` holds the retailer's native node id when available (kept for
    reference/debugging). `search_term` is the leaf's own specific name and
    is what actually gets sent as the query (neither retailer's search
    endpoint accepts a bare category id as a filter — confirmed via live 400
    responses). `category`/`sub_category_1/2/3` are the breadcrumb used only
    for row-tagging — keeping these separate from `search_term` matters:
    using the top-level ancestor (`category`) as the query previously caused
    every leaf under a large branch to issue the *same* search (e.g. every
    Woolworths "Specials" sub-leaf all querying literal "Specials"),
    confirmed live when 30 supposedly-distinct categories all collapsed to
    one repeated query and a tiny, skewed result set.
    """

    id: str
    search_term: str
    category: str
    sub_category_1: Optional[str] = None
    sub_category_2: Optional[str] = None
    sub_category_3: Optional[str] = None


def _select_diverse_terms(categories: List[CategoryNode], limit: int) -> List[CategoryNode]:
    """Cap to `limit` categories, round-robin across distinct top-level departments.

    A naive prefix slice (`categories[:limit]`) collapses to whichever
    top-level branch happens to come first in the tree — confirmed live:
    Woolworths' tree lists "Specials" first, with enough sub-leaves that the
    first 30 in tree order were *all* Specials sub-categories, each
    searching a near-duplicate term. Round-robining across `.category`
    (the top-level ancestor) spreads the cap across departments instead.
    """
    buckets: Dict[str, List[CategoryNode]] = {}
    for node in categories:
        buckets.setdefault(node.category, []).append(node)

    selected: List[CategoryNode] = []
    while len(selected) < limit and any(buckets.values()):
        for key in list(buckets.keys()):
            if not buckets[key]:
                continue
            selected.append(buckets[key].pop(0))
            if len(selected) >= limit:
                break
    return selected


class CurlCffiEngine:
    """Async engine for calling Coles/Woolworths search APIs with a bootstrapped session.

    Use as an async context manager so the underlying curl_cffi session
    (and its connection pool) is cleaned up deterministically:

        async with CurlCffiEngine() as engine:
            store_location = await engine.resolve_store_id("Coles", "Ashfield NSW 2131", subscription_key)
            products = await engine.fetch_all_products_for_store(session, store_location, run_number)
    """

    def __init__(self, concurrency: int = 8, request_timeout: float = 30.0) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._timeout = request_timeout
        self._session: Optional[AsyncSession] = None

    async def __aenter__(self) -> "CurlCffiEngine":
        self._session = AsyncSession()
        return self

    async def __aexit__(self, *_exc_info: Any) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _request(
        self,
        session_context: SessionContext,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> Any:
        if self._session is None:
            raise RuntimeError("CurlCffiEngine must be used as an async context manager")

        start = time.monotonic()
        try:
            response = await self._session.request(
                method,
                url,
                cookies=session_context.cookies,
                headers=session_context.headers,
                impersonate=IMPERSONATE_TARGET,
                timeout=self._timeout,
                **kwargs,
            )
        except Exception as exc:
            # curl_cffi surfaces connection/timeout failures as its own
            # exception types; caught broadly here since the exact hierarchy
            # varies by curl_cffi version, and any of them are equally a
            # hard network failure rather than something a session refresh
            # would fix.
            logger.error(
                "Network error method=%s url=%s retailer=%s store_id=%s elapsed=%.2fs: %s",
                method,
                url,
                session_context.retailer,
                session_context.store_id,
                time.monotonic() - start,
                exc,
            )
            raise NetworkError(f"Network error calling {url}: {exc}") from exc

        elapsed = time.monotonic() - start
        logger.debug(
            "HTTP %s %s -> status=%d retailer=%s store_id=%s elapsed=%.2fs",
            method,
            url,
            response.status_code,
            session_context.retailer,
            session_context.store_id,
            elapsed,
        )

        if response.status_code in (401, 403):
            logger.warning(
                "Auth failure status=%d method=%s url=%s retailer=%s store_id=%s — will trigger re-bootstrap",
                response.status_code,
                method,
                url,
                session_context.retailer,
                session_context.store_id,
            )
            raise AuthExpiredError(
                f"{method} {url} returned {response.status_code} — session likely expired/invalidated",
                status_code=response.status_code,
            )
        if response.status_code == 429:
            retry_after_header = response.headers.get("Retry-After")
            retry_after = float(retry_after_header) if retry_after_header else None
            logger.warning(
                "Rate limited method=%s url=%s retailer=%s store_id=%s retry_after=%s",
                method,
                url,
                session_context.retailer,
                session_context.store_id,
                retry_after,
            )
            raise RateLimitError(f"{method} {url} rate-limited (429)", retry_after=retry_after)
        if response.status_code >= 400:
            logger.error(
                "Unexpected status=%d method=%s url=%s retailer=%s store_id=%s body=%.200r",
                response.status_code,
                method,
                url,
                session_context.retailer,
                session_context.store_id,
                response.text,
            )
            raise NetworkError(f"{method} {url} returned unexpected status {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            logger.error("JSON decode failure url=%s retailer=%s: %s", url, session_context.retailer, exc)
            raise ParsingError(f"Could not decode JSON from {url}: {exc}") from exc

    async def _prime_cookies(self, session_context: SessionContext) -> None:
        """GET the retailer's homepage to populate `session_context.cookies` in place.

        Both retailers' store-locator endpoints need the same basic
        cookie handshake as the product APIs (confirmed live) before they'll
        respond with real data rather than a connection reset. Not routed
        through `_request` since the homepage returns HTML, not JSON.
        """
        if self._session is None:
            raise RuntimeError("CurlCffiEngine must be used as an async context manager")
        config = RETAILER_CONFIGS[session_context.retailer]
        response = await self._session.request(
            "GET",
            config.base_url,
            cookies=session_context.cookies,
            headers=session_context.headers,
            impersonate=IMPERSONATE_TARGET,
            timeout=self._timeout,
        )
        session_context.cookies.update(dict(response.cookies))

    async def resolve_store_id(
        self,
        retailer: RetailerName,
        suburb: str,
        subscription_key: Optional[str] = None,
    ) -> StoreLocation:
        """Resolve a suburb query (e.g. "Ashfield NSW 2131") to a real, nearby store.

        Confirmed live for both retailers (endpoints reverse-engineered from
        each site's own JS, not guessed):
        - **Coles**: `POST /api/graphql` with the captured
          `GetStoreLocationSuggestions` query (suburb text -> candidate
          suburbs with lat/lng), then the captured `FindStores` query
          (lat/lng -> nearest stores, filtered to `brandIds: ["COL"]`).
          Requires `subscription_key` — obtain one via any
          `PlaywrightBootstrapper.bootstrap("Coles", ...)` call first; the
          key is a static per-client APIM key, not store-specific, so a
          throwaway bootstrap (any placeholder store id) works fine.
        - **Woolworths**: `GET /apis/ui/StoreLocator/Suburbs?SearchTerm=...`
          (suburb text -> candidate suburbs with lat/lng), then
          `GET /apis/ui/StoreLocator/Stores?Latitude=...&Longitude=...`
          (lat/lng -> nearest stores). No key needed — this whole
          resolution runs on `curl_cffi` alone, no Playwright involved.
        """
        config = RETAILER_CONFIGS[retailer]
        term, postcode_filter, state_filter = _parse_suburb_query(suburb)

        if retailer == "Coles" and not subscription_key:
            raise BootstrapError(
                "resolve_store_id for Coles requires a subscription_key "
                "(bootstrap once with any store id to obtain one)"
            )

        headers = dict(config.default_headers)
        if subscription_key and config.subscription_key_header:
            headers[config.subscription_key_header] = subscription_key
        resolution_session = SessionContext(
            retailer=retailer, cookies={}, headers=headers, store_id="", created_at=time.time()
        )
        await self._prime_cookies(resolution_session)

        logger.info("resolve_store_id start retailer=%s suburb=%r term=%r", retailer, suburb, term)
        if retailer == "Coles":
            store_location = await self._resolve_coles_store(resolution_session, term, postcode_filter, state_filter)
        else:
            store_location = await self._resolve_woolworths_store(
                resolution_session, term, postcode_filter, state_filter
            )
        logger.info(
            "resolve_store_id success retailer=%s suburb=%r -> store_id=%s store_name=%r lat=%s lon=%s",
            retailer,
            suburb,
            store_location.store_id,
            store_location.store_name,
            store_location.latitude,
            store_location.longitude,
        )
        return store_location

    async def _resolve_coles_store(
        self,
        resolution_session: SessionContext,
        term: str,
        postcode_filter: Optional[str],
        state_filter: Optional[str],
    ) -> StoreLocation:
        config = RETAILER_CONFIGS["Coles"]
        assert config.graphql_url is not None, "Coles config must define graphql_url"
        suggestions = await self._request(
            resolution_session,
            "POST",
            config.graphql_url,
            json={"query": _COLES_SUBURB_SUGGESTIONS_QUERY, "variables": {"term": term, "count": 5}},
        )
        results = (suggestions.get("data") or {}).get("localitySearch", {}).get("results") or []
        suburb_match = _pick_best_suburb_match(
            results, postcode_filter, state_filter, postcode_key="postcode", state_key="state"
        )
        if suburb_match is None:
            raise BootstrapError(f"Coles localitySearch found no suburb matching {term!r}")

        stores_payload = await self._request(
            resolution_session,
            "POST",
            config.graphql_url,
            json={
                "query": _COLES_FIND_STORES_QUERY,
                "variables": {
                    "latitude": suburb_match["latitude"],
                    "longitude": suburb_match["longitude"],
                    "brandIds": ["COL"],
                    "count": 3,
                    "distance": 15,
                },
            },
        )
        store_results = (stores_payload.get("data") or {}).get("stores", {}).get("results") or []
        if not store_results:
            raise BootstrapError(f"Coles FindStores returned no stores near {term!r}")

        store = store_results[0]["store"]
        # Coles' GraphQL id is brand-prefixed (e.g. "COL:791"); the REST
        # search endpoint takes just the numeric part (confirmed live).
        store_id = str(store["id"]).split(":")[-1]
        address = store["address"]
        position = store["position"]
        return StoreLocation(
            retailer="Coles",
            store_id=store_id,
            store_name=store["name"],
            suburb_name=address["suburb"],
            state=address["state"],
            postcode=address["postcode"],
            latitude=position["latitude"],
            longitude=position["longitude"],
        )

    async def _resolve_woolworths_store(
        self,
        resolution_session: SessionContext,
        term: str,
        postcode_filter: Optional[str],
        state_filter: Optional[str],
    ) -> StoreLocation:
        config = RETAILER_CONFIGS["Woolworths"]
        assert config.store_suburb_search_url is not None, "Woolworths config must define store_suburb_search_url"
        suggestions = await self._request(
            resolution_session, "GET", config.store_suburb_search_url, params={"SearchTerm": term}
        )
        results = suggestions.get("Suburbs") or []
        suburb_match = _pick_best_suburb_match(
            results, postcode_filter, state_filter, postcode_key="PostCode", state_key="State"
        )
        if suburb_match is None:
            raise BootstrapError(f"Woolworths StoreLocator/Suburbs found no suburb matching {term!r}")

        assert config.store_nearby_url is not None, "Woolworths config must define store_nearby_url"
        stores_payload = await self._request(
            resolution_session,
            "GET",
            config.store_nearby_url,
            params={
                "Latitude": suburb_match["Latitude"],
                "Longitude": suburb_match["Longitude"],
                "Max": 3,
                "Division": "SUPERMARKETS",
            },
        )
        stores = stores_payload.get("Stores") or []
        if not stores:
            raise BootstrapError(f"Woolworths StoreLocator/Stores returned no stores near {term!r}")

        store = stores[0]
        return StoreLocation(
            retailer="Woolworths",
            store_id=str(store["StoreNo"]),
            store_name=store["Name"],
            suburb_name=store["Suburb"],
            state=store["State"],
            postcode=store["Postcode"],
            latitude=float(store["Latitude"]),
            longitude=float(store["Longitude"]),
        )

    async def fetch_categories(self, session_context: SessionContext) -> List[CategoryNode]:
        """Fetch and flatten the retailer's category tree into leaf categories.

        Confirmed live for Woolworths (`PiesCategoriesWithSpecials`, node
        shape `{NodeId, Description, Children}`). Coles' `categories_url` is
        unverified — if this call fails or returns nothing, callers should
        use `FALLBACK_SEARCH_TERMS` instead (see `fetch_all_products_for_store`).
        """
        config = RETAILER_CONFIGS[session_context.retailer]
        payload = await self._request(session_context, "GET", config.categories_url)
        raw_nodes = (
            payload if isinstance(payload, list) else payload.get("categories") or payload.get("Categories") or []
        )
        leaves = self._flatten_categories(raw_nodes)
        logger.info(
            "fetch_categories retailer=%s store_id=%s leaf_categories=%d",
            session_context.retailer,
            session_context.store_id,
            len(leaves),
        )
        return leaves

    @staticmethod
    async def fetch_coles_categories_via_mobile(store_id: str) -> List[CategoryNode]:
        """Coles' real category tree, fetched via the mobile app backend.

        See the module-level comment on `_COLES_MOBILE_CATEGORIES_URL` for
        why this exists (the website's categories_url 404s) and why it only
        pulls category names rather than calling the sibling
        category-driven product-search endpoint directly. Confirmed live:
        one GET returns the full nested tree for a store — no per-department
        fan-out calls needed, unlike Woolworths' `fetch_categories` path.
        """
        async with _coles_mobile_categories_lock:
            headers = get_coles_app_headers()
        url = (
            f"{_COLES_MOBILE_CATEGORIES_URL}?storeId={store_id}&shoppingMethod=clickAndCollect"
            f"&includeLiquor=true&includeTobacco=true"
        )
        async with AsyncSession() as session:
            response = await session.get(url, headers=headers, impersonate=IMPERSONATE_TARGET, timeout=20)
        if response.status_code != 200:
            raise BootstrapError(
                f"Coles mobile categories endpoint returned {response.status_code} for store {store_id}"
            )
        try:
            raw_top_level = response.json()
        except ValueError as exc:
            raise ParsingError(
                f"Could not decode Coles mobile categories response for store {store_id}: {exc}"
            ) from exc
        if not isinstance(raw_top_level, list):
            raise ParsingError(f"Coles mobile categories endpoint returned an unexpected shape for store {store_id}")

        leaves: List[CategoryNode] = []

        def _walk(nodes: List[Dict[str, Any]], path: Tuple[str, ...]) -> None:
            for node in nodes:
                name = node.get("name")
                node_id = node.get("id")
                product_count = node.get("productCount") or 0
                if not path and name and any(marker in name.lower() for marker in _COLES_PROMOTIONAL_TOP_LEVEL_MARKERS):
                    continue  # e.g. "Big Pack Value" — a cross-cutting bundle, not a real department
                children = node.get("subCategories") or []
                new_path = path + (name,) if name else path
                if children:
                    _walk(children, new_path)
                elif node_id and name and product_count > 0:
                    breadcrumb = (list(new_path) + [None, None, None])[:4]
                    category, sub_1, sub_2, sub_3 = breadcrumb
                    leaves.append(
                        CategoryNode(
                            id=str(node_id),
                            search_term=name,
                            category=category or name,
                            sub_category_1=sub_1,
                            sub_category_2=sub_2,
                            sub_category_3=sub_3,
                        )
                    )

        _walk(raw_top_level, ())
        logger.info("fetch_coles_categories_via_mobile store_id=%s leaf_categories=%d", store_id, len(leaves))
        return leaves

    @classmethod
    def _flatten_categories(cls, nodes: List[Dict[str, Any]], path: Tuple[str, ...] = ()) -> List[CategoryNode]:
        leaves: List[CategoryNode] = []
        for node in nodes:
            # "Description" confirmed as Woolworths' real name field; the
            # others are best-effort guesses for other possible shapes.
            name = node.get("Description") or node.get("name") or node.get("Name") or node.get("NodeName")
            node_id = node.get("id") or node.get("Id") or node.get("NodeId")
            if _is_promotional_branch(node_id, name):
                # "Specials" (Woolworths' first top-level branch, confirmed
                # live) is a cross-cutting promotional filter, not a
                # taxonomic category — an on-special item is still really
                # under e.g. "Dairy, Eggs & Fridge" > "Milk". Walking this
                # branch mislabels every item under it with
                # category="Specials"; skip the whole branch (don't recurse)
                # so real departments get walked instead. "On special"
                # status is already captured correctly at the fact level via
                # `product_badge` (see `_parse_woolworths_item`).
                continue
            children = node.get("children") or node.get("Children") or node.get("Categories") or []
            new_path = path + (name,) if name else path
            if children:
                leaves.extend(cls._flatten_categories(children, new_path))
            elif node_id and name:
                breadcrumb = (list(new_path) + [None, None, None])[:4]
                category, sub_1, sub_2, sub_3 = breadcrumb
                leaves.append(
                    CategoryNode(
                        id=str(node_id),
                        search_term=name,
                        category=category or name,
                        sub_category_1=sub_1,
                        sub_category_2=sub_2,
                        sub_category_3=sub_3,
                    )
                )
        return leaves

    async def fetch_products_page(
        self,
        session_context: SessionContext,
        category: CategoryNode,
        page: int,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Fetch one page of search results for `category.search_term`.

        Request/response shapes below are taken from live captures:
        - Coles: `GET /api/bff/products/search` with query params
          (`storeId`, `start`, `pageSize`, `searchTerm`, `sortBy`,
          `excludeAds`, `authenticated`); results live at `results`, total
          count at `noOfResults`.
        - Woolworths: `POST /apis/ui/Search/products` with a PascalCase JSON
          body (`SearchTerm`, `PageNumber`, `PageSize`); results are nested
          as a list of "bundles" under `Products`, each with its own inner
          `Products` list that needs flattening; total count at
          `SearchResultsCount`.
        """
        config = RETAILER_CONFIGS[session_context.retailer]

        if session_context.retailer == "Coles":
            start_offset = (page - 1) * config.page_size
            params = {
                "storeId": session_context.store_id,
                "start": start_offset,
                "pageSize": config.page_size,
                "searchTerm": category.search_term,
                "sortBy": "salesDescending",
                "excludeAds": "true",
                "authenticated": "false",
            }
            payload = await self._request(session_context, "GET", config.search_url, params=params)
            items = payload.get("results") or []
            total = payload.get("noOfResults", 0)
            has_more = (start_offset + len(items)) < total
            parse_item = self._parse_coles_item
        else:
            body = {
                "SearchTerm": category.search_term,
                "PageNumber": page,
                "PageSize": config.page_size,
            }
            payload = await self._request(session_context, "POST", config.search_url, json=body)
            bundles = payload.get("Products") or []
            items = [product for bundle in bundles for product in (bundle.get("Products") or [])]
            total = payload.get("SearchResultsCount", 0)
            has_more = (page * config.page_size) < total
            parse_item = self._parse_woolworths_item

        parsed: List[Dict[str, Any]] = []
        for raw_item in items:
            fields = parse_item(raw_item, category)
            if fields is not None:
                parsed.append(fields)

        skipped = len(items) - len(parsed)
        logger.debug(
            "fetch_products_page retailer=%s search_term=%r page=%d items=%d parsed=%d skipped=%d has_more=%s",
            session_context.retailer,
            category.search_term,
            page,
            len(items),
            len(parsed),
            skipped,
            has_more,
        )
        if skipped:
            logger.warning(
                "Skipped %d unparseable items retailer=%s search_term=%r page=%d — response shape may have drifted",
                skipped,
                session_context.retailer,
                category.search_term,
                page,
            )
        return parsed, has_more

    @staticmethod
    def _parse_coles_item(item: Dict[str, Any], category: CategoryNode) -> Optional[Dict[str, Any]]:
        """Map one Coles `/api/bff/products/search` result item to Product fields.

        Field names below (id, name, brand, size, availability, onlineHeirs,
        pricing.now/was/comparable, imageUris) are taken directly from a
        live capture. Fields with no evidenced source in that capture
        (product_page slug, review/rating data, promo badges, loyalty
        pricing, child/variant id) are left `None` rather than guessed.
        """
        stockcode = item.get("id")
        if stockcode is None:
            return None
        try:
            pricing = item.get("pricing") or {}
            pack_size, clean_uom = parse_pack_size(item.get("size"))
            heir = (item.get("onlineHeirs") or [{}])[0]
            images = item.get("imageUris") or []
            # KNOWN BUG (confirmed live, unfixed): "https://www.coles.com.au"
            # is an UNVERIFIED guessed prefix — real stored image_url values
            # 404 or return an Incapsula challenge page, not an image. Coles
            # almost certainly serves product images from a separate CDN
            # subdomain, not the main website domain. Fix requires live
            # recon (fetch a real product page, read its actual <img> src)
            # which was blocked this session by escalated anti-bot — even
            # the plain homepage GET started failing mid-session. TODO next
            # time Coles access is available: find the real CDN domain,
            # fix this line, then backfill existing rows with
            # `UPDATE products SET image_url = REPLACE(image_url,
            # 'https://www.coles.com.au', '<real domain>')` — no re-scrape
            # needed, the relative path portion (images[0]['uri']) is
            # already correct, only the prefix is wrong.
            image_url = f"https://www.coles.com.au{images[0]['uri']}" if images and images[0].get("uri") else None
            was_price = pricing.get("was")
            # In-store location: schema has this (confirmed live), but every
            # sampled product had it null ("Aisle information is not
            # available for this product") — wired up for whenever it is
            # populated, per Step 0 research (no separate store-map/aisle
            # feature found elsewhere on Coles' website).
            location_info = (item.get("locations") or [{}])[0]
            return {
                "category": heir.get("subCategory") or category.category,
                "sub_category_1": heir.get("category"),
                # NOTE: `onlineHeirs[].aisle` is an *online browsing* taxonomy
                # label (e.g. "Full Cream Milk"), unrelated to the physical
                # in-store `locations[].aisle` field used for aisle_number
                # below — same field name, two different concepts.
                "sub_category_2": heir.get("aisle"),
                "sub_category_3": None,
                "retailer_product_id": int(stockcode),
                "child_product_id": None,
                "name": item.get("name", ""),
                "pack_size": pack_size,
                "clean_uom": clean_uom,
                "price_display": pricing.get("now"),
                "loyalty_price": None,
                "price_per_uom": pricing.get("comparable"),
                "clean_brand": item.get("brand"),
                "prev_price": str(was_price) if was_price else None,
                "stock_status": "In Stock" if item.get("availability", True) else "Out of Stock",
                "product_badge": None,
                "product_page": None,
                "image_url": image_url,
                "no_of_reviews": None,
                "star_rating": None,
                "plv_id": None,
                "aisle_number": str(location_info["aisle"]) if location_info.get("aisle") is not None else None,
                "bay_number": str(location_info["shelf"]) if location_info.get("shelf") is not None else None,
            }
        except (TypeError, ValueError, KeyError) as exc:
            logger.error(
                "Failed to parse Coles item stockcode=%r search_term=%r: %s", stockcode, category.search_term, exc
            )
            raise ParsingError(f"Failed to parse Coles product item {stockcode!r}: {exc}") from exc

    @staticmethod
    def _parse_woolworths_item(item: Dict[str, Any], category: CategoryNode) -> Optional[Dict[str, Any]]:
        """Map one Woolworths `/apis/ui/Search/products` result item to Product fields.

        Field names below (Stockcode, Barcode, Name, Brand, PackageSize,
        Price, WasPrice, IsInStock, CupString, SmallImageFile, Rating,
        UrlFriendlyName, IsOnSpecial/IsNew) are taken directly from a live
        capture. `SapCategories` (a possible per-item category field) was
        `null` in that capture, so category is labeled with the search term
        actually used rather than guessed from an empty field.
        """
        stockcode = item.get("Stockcode")
        if stockcode is None:
            return None
        try:
            pack_size, clean_uom = parse_pack_size(item.get("PackageSize"))
            is_available = item.get("IsInStock", item.get("IsAvailable", True))
            url_name = item.get("UrlFriendlyName")
            rating = item.get("Rating") or {}
            price = item.get("Price")
            was_price = item.get("WasPrice")
            badge = "Special" if item.get("IsOnSpecial") else ("New" if item.get("IsNew") else None)
            return {
                "category": category.category,
                "sub_category_1": None,
                "sub_category_2": None,
                "sub_category_3": None,
                "retailer_product_id": int(stockcode),
                "child_product_id": str(item["Barcode"]) if item.get("Barcode") else None,
                "name": item.get("Name") or item.get("DisplayName") or "",
                "pack_size": pack_size,
                "clean_uom": clean_uom,
                "price_display": price,
                "loyalty_price": None,
                "price_per_uom": item.get("CupString"),
                "clean_brand": item.get("Brand"),
                "prev_price": str(was_price) if was_price and was_price != price else None,
                "stock_status": "In Stock" if is_available else "Out of Stock",
                "product_badge": badge,
                "product_page": (
                    f"https://www.woolworths.com.au/shop/productdetails/{stockcode}/{url_name}" if url_name else None
                ),
                "image_url": item.get("SmallImageFile") or item.get("MediumImageFile"),
                "no_of_reviews": str(rating["ReviewCount"]) if rating.get("ReviewCount") else None,
                "star_rating": rating.get("Average") or None,
                "plv_id": None,
                # No physical in-store location field exists anywhere in
                # Woolworths' search/detail responses (confirmed live, Step
                # 0 research). `PrimaryCategory.Aisle` on the product-detail
                # page is an unrelated *online* "shop by aisle" taxonomy
                # label (e.g. "full cream milk"), not a physical position —
                # left `None` rather than misusing that field.
                "aisle_number": None,
                "bay_number": None,
            }
        except (TypeError, ValueError, KeyError) as exc:
            logger.error(
                "Failed to parse Woolworths item stockcode=%r search_term=%r: %s", stockcode, category.search_term, exc
            )
            raise ParsingError(f"Failed to parse Woolworths product item {stockcode!r}: {exc}") from exc

    async def fetch_all_products_for_store(
        self,
        session_context: SessionContext,
        scrape_date: str,
        max_pages_per_term: int = 5,
        max_search_terms: Optional[int] = 30,
    ) -> List[Product]:
        """Enumerate SKUs at this store by paginating a list of search terms.

        Returns lean `Product` facts only — no store/geo fields stamped per
        row (those live once in the `stores` dimension table; the caller
        already holds the one `StoreLocation` for the batch and passes it
        separately to `ProductStore.record_scrape` alongside this method's
        return value). `scrape_date` (ISO `YYYY-MM-DD`) is the temporal grain
        for the daily cadence, replacing the old run-counter.

        Categories/search terms are paginated concurrently; the shared
        semaphore caps how many HTTP requests are in flight at once
        regardless of how many terms are being walked simultaneously.
        `max_pages_per_term` bounds runaway pagination on a single broad
        term — pass a higher value for a more exhaustive (but slower) run.

        `max_search_terms` caps how many terms get walked in this call.
        This matters because Woolworths' real category tree has ~1900 leaf
        categories, and live testing showed its Akamai session cookies can
        go stale in under a minute — walking all ~1900 in one session
        without a session-refresh cycle risks the later requests failing
        with 401/403 as the cookies expire mid-run (the orchestrator's
        retry loop would re-bootstrap and eventually finish, just slowly).
        Pass `None` to disable the cap for an exhaustive run.
        """
        start = time.monotonic()
        logger.info(
            "fetch_all_products_for_store start retailer=%s store_id=%s scrape_date=%s",
            session_context.retailer,
            session_context.store_id,
            scrape_date,
        )
        try:
            if session_context.retailer == "Coles":
                # Coles' website categories_url 404s (never confirmed
                # working) — the real tree lives on the mobile app backend
                # instead. See fetch_coles_categories_via_mobile's docstring.
                categories = await self.fetch_coles_categories_via_mobile(session_context.store_id)
            else:
                categories = await self.fetch_categories(session_context)
        except Exception as exc:
            categories = []
            logger.warning(
                "fetch_categories failed retailer=%s store_id=%s: %s — using FALLBACK_SEARCH_TERMS instead",
                session_context.retailer,
                session_context.store_id,
                exc,
            )
        if not categories:
            categories = [CategoryNode(id=term, search_term=term, category=term) for term in FALLBACK_SEARCH_TERMS]

        if max_search_terms is not None and len(categories) > max_search_terms:
            dropped = len(categories) - max_search_terms
            logger.warning(
                "Capping search terms retailer=%s store_id=%s: using %d of %d (dropping %d) — "
                "pass max_search_terms=None for an exhaustive run",
                session_context.retailer,
                session_context.store_id,
                max_search_terms,
                len(categories),
                dropped,
            )
            categories = _select_diverse_terms(categories, max_search_terms)

        seen_keys: Set[Tuple[int, Optional[str]]] = set()
        products: List[Product] = []
        lock = asyncio.Lock()

        async def _walk_category(category: CategoryNode) -> None:
            page = 1
            category_product_count = 0
            while page <= max_pages_per_term:
                async with self._semaphore:
                    page_fields, has_more = await self.fetch_products_page(session_context, category, page)

                async with lock:
                    for fields in page_fields:
                        key = (fields["retailer_product_id"], fields.get("child_product_id"))
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                        category_product_count += 1
                        products.append(
                            Product(
                                retailer=session_context.retailer,
                                scrape_date=scrape_date,
                                scraped_at=datetime.now(timezone.utc).isoformat(),
                                **fields,
                            )
                        )

                if not has_more or not page_fields:
                    break
                page += 1
            logger.debug(
                "Search term walk complete retailer=%s search_term=%r pages=%d products=%d",
                session_context.retailer,
                category.search_term,
                page,
                category_product_count,
            )

        await asyncio.gather(*(_walk_category(category) for category in categories))
        logger.info(
            "fetch_all_products_for_store done retailer=%s store_id=%s scrape_date=%s search_terms=%d "
            "products=%d elapsed=%.1fs",
            session_context.retailer,
            session_context.store_id,
            scrape_date,
            len(categories),
            len(products),
            time.monotonic() - start,
        )
        return products
