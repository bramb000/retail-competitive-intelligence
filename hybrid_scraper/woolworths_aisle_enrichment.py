"""Woolworths in-store placement enrichment via mobile GraphQL (Iris).

Live-confirmed via mitmproxy + Frida on com.woolworths 26.16.0 (Ashfield 1213):

  GraphQL: POST https://prod.mobile-api.woolworths.com.au/hermes/iris/v1/graphql
  Auth:    Authorization: Bearer <guest/shopper token>
           x-api-key: BuildConfig.SHOP_IRIS_API_KEY
           x-acf-sensor-data: Akamai sensor from the real app
  Proxy:   Prefer local mitmdump so CDN does not return poisoned HITs.

Exact-SKU lookup: `productDetailsPage` (INSTORE mode + storeId).
Discovery:        `productList` (type=search, storeId, argument).

Both documents are the live app queries under `woolworths_queries/` (no obsolete
`lists` field). ProductCard.price is in cents on this API.

Placement maps into shared `AisleEnrichment` (aisle_number, bay_number, indoor_x/y).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from curl_cffi.requests import AsyncSession

from hybrid_scraper.config import IMPERSONATE_TARGET
from hybrid_scraper.exceptions import AuthExpiredError, NetworkError
from hybrid_scraper.storage import AisleEnrichment
from hybrid_scraper.woolworths_mobile_session import (
    GRAPHQL_URL,
    get_woolworths_mobile_session,
    mitm_proxies,
)

logger = logging.getLogger(__name__)

_QUERY_DIR = Path(__file__).resolve().parent / "woolworths_queries"
_PRODUCT_LIST_QUERY = (_QUERY_DIR / "productList.graphql").read_text(encoding="utf-8")
_PRODUCT_DETAILS_QUERY = (_QUERY_DIR / "productDetailsPage.graphql").read_text(encoding="utf-8")
_PDP_DEFAULTS = json.loads((_QUERY_DIR / "pdp_defaults.json").read_text(encoding="utf-8"))
_LIST_DEFAULTS = json.loads((_QUERY_DIR / "productList_defaults.json").read_text(encoding="utf-8"))


def _headers(force_refresh: bool = False) -> Dict[str, str]:
    session = get_woolworths_mobile_session(force_refresh=force_refresh)
    headers = dict(session.headers)
    headers["x-correlation-id"] = str(uuid.uuid4())
    headers["content-type"] = "application/json; charset=utf-8"
    headers["accept"] = "application/json"
    return headers


def _request_kwargs() -> Dict[str, Any]:
    proxies = mitm_proxies()
    kwargs: Dict[str, Any] = {"impersonate": IMPERSONATE_TARGET, "timeout": 45}
    if proxies:
        kwargs["proxies"] = proxies
        kwargs["verify"] = False
    return kwargs


def _pad_product_id(product_id: str) -> str:
    digits = re.sub(r"\D", "", str(product_id)) or str(product_id)
    if digits.isdigit() and len(digits) < 18:
        return digits.zfill(18)
    return str(product_id)


def _parse_price(value: Any) -> Optional[float]:
    """WW Iris ProductCard.price is integer cents (e.g. 600 → $6.00)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value / 100.0
    if isinstance(value, float):
        # Already dollars if small; cents if large.
        return value / 100.0 if value >= 100 else value
    text = str(value).strip()
    match = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    if not match:
        return None
    amount = float(match.group(1))
    if "." not in match.group(1) and amount >= 100:
        return amount / 100.0
    return amount


def parse_woolworths_placement(card: Dict[str, Any]) -> Optional[AisleEnrichment]:
    loc = card.get("inStoreLocation") or {}
    details = loc.get("details") or {}
    display = loc.get("displayInfo") or {}
    aisle = details.get("aisleNumber")
    if aisle is None and not display.get("locationText"):
        details_fallback = card.get("inStoreDetails") or {}
        text = details_fallback.get("locationText")
        if not text or "see in store" in str(text).lower() or "not available" in str(text).lower():
            return None
        return AisleEnrichment(aisle_number=str(text), bay_number=None)

    bay = details.get("bayNumber") or details.get("bayNumberAlternate") or details.get("aisleSide")
    if aisle is None and display.get("locationText"):
        aisle = display["locationText"]
    text = str(aisle) if aisle is not None else None
    if text and ("not available" in text.lower() or "see in store" in text.lower()):
        return None
    return AisleEnrichment(
        aisle_number=text,
        bay_number=str(bay) if bay is not None else None,
        aisle_facing=None,
        aisle_order=None,
        indoor_x=details.get("x"),
        indoor_y=details.get("y"),
    )


def _category_breadcrumb(card: Dict[str, Any]) -> List[str]:
    raw = card.get("categories") or []
    named: List[tuple] = []
    for node in raw:
        if not isinstance(node, dict) or not node.get("name"):
            continue
        level = node.get("categoryLevel")
        try:
            named.append((int(level) if level is not None else 99, str(node["name"])))
        except (TypeError, ValueError):
            named.append((99, str(node["name"])))
    named.sort(key=lambda item: item[0])
    return [name for _, name in named]


def _promo_fields(card: Dict[str, Any]) -> Dict[str, Any]:
    promo = card.get("promotionInfo") if isinstance(card.get("promotionInfo"), dict) else {}
    secondary = card.get("secondaryPromotionInfo") if isinstance(card.get("secondaryPromotionInfo"), dict) else {}
    member = card.get("memberPriceInfo") if isinstance(card.get("memberPriceInfo"), dict) else {}
    multibuy = card.get("multiBuyPriceInfo") if isinstance(card.get("multiBuyPriceInfo"), dict) else {}
    was = card.get("wasPrice")
    label = promo.get("label") or secondary.get("label")
    promo_type = promo.get("type") or secondary.get("type")
    is_promo = bool(label or was or multibuy or member.get("title"))
    return {
        "is_promo": is_promo,
        "promo_type": promo_type,
        "promo_label": label,
        "member_price_title": member.get("title"),
        "multibuy_price": _parse_price(multibuy.get("price")) if multibuy else None,
    }


def product_summary(card: Dict[str, Any]) -> Dict[str, Any]:
    enrichment = parse_woolworths_placement(card)
    pid = card.get("productId")
    if isinstance(pid, str):
        pid = pid.lstrip("0") or pid
    breadcrumb = _category_breadcrumb(card)
    promo = _promo_fields(card)
    loc = card.get("inStoreLocation") or {}
    details = loc.get("details") or {}
    return {
        "product_id": pid,
        "name": card.get("name"),
        "price": _parse_price(card.get("price")),
        "price_raw": card.get("price"),
        "unit_price": card.get("unitPriceDescription"),
        "was_price": _parse_price(card.get("wasPrice")),
        "is_available": card.get("isAvailable"),
        "aisle_number": enrichment.aisle_number if enrichment else None,
        "bay_number": enrichment.bay_number if enrichment else None,
        "aisle_side": details.get("aisleSide"),
        "indoor_x": enrichment.indoor_x if enrichment else None,
        "indoor_y": enrichment.indoor_y if enrichment else None,
        "indoor_z": details.get("z"),
        "location_text": ((loc.get("displayInfo") or {}).get("locationText"))
        or ((card.get("inStoreDetails") or {}).get("locationText")),
        "categories": breadcrumb,
        "category": breadcrumb[0] if breadcrumb else None,
        "sub_category_1": breadcrumb[1] if len(breadcrumb) > 1 else None,
        "source": card.get("_source"),
        **promo,
    }


def _graphql_poisoned(payload: Dict[str, Any]) -> bool:
    data = payload.get("data") or {}
    if "productsByCategory" in data and "productDetailsPage" not in data and "productList" not in data:
        return True
    return False


def _walk_product_cards(node: Any, out: List[Dict[str, Any]], depth: int = 0) -> None:
    if depth > 16:
        return
    if isinstance(node, dict):
        if node.get("productId") and node.get("name") is not None:
            out.append(node)
            return
        for value in node.values():
            _walk_product_cards(value, out, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _walk_product_cards(item, out, depth + 1)


def _graphql_url(operation_name: Optional[str]) -> str:
    """Iris rejects productList with HTTP 400 BAD_USER_INPUT unless operationName is on the URL.

    Live app posts to `/hermes/iris/v1/graphql?operationName=productList` (mitm recon).
    PDP often works without the query string; productList does not.
    """
    if not operation_name:
        return GRAPHQL_URL
    return f"{GRAPHQL_URL}?operationName={operation_name}"


async def _graphql(session: AsyncSession, headers: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    operation_name = (
        headers.get("x-apollo-operation-name")
        or (body.get("operationName") if isinstance(body.get("operationName"), str) else None)
    )
    response = await session.post(_graphql_url(operation_name), headers=headers, json=body, **_request_kwargs())
    if response.status_code in (401, 403):
        raise AuthExpiredError(f"Woolworths GraphQL HTTP {response.status_code}", response.status_code)
    if response.status_code >= 400:
        raise NetworkError(f"Woolworths GraphQL HTTP {response.status_code}: {response.text[:400]}")
    payload = response.json()
    if payload.get("errors"):
        logger.warning("Woolworths GraphQL errors: %s", payload["errors"][:3])
    if _graphql_poisoned(payload):
        timing = response.headers.get("server-timing", "")
        raise NetworkError(
            f"Woolworths GraphQL poisoned CDN response (need x-acf-sensor-data); "
            f"server-timing={timing[:80]}"
        )
    return payload


async def fetch_product_details(
    session: AsyncSession,
    store_id: str,
    product_id: str,
    force_refresh: bool = False,
) -> Optional[Dict[str, Any]]:
    """Fetch one SKU via live `productDetailsPage` (INSTORE)."""
    headers = _headers(force_refresh=force_refresh)
    headers["x-apollo-operation-name"] = "productDetailsPage"
    variables = {
        "productDetailsPageInput": {
            "productId": _pad_product_id(product_id),
            "mode": _PDP_DEFAULTS["mode"],
            "storeId": str(store_id),
            "supportedActions": _PDP_DEFAULTS["supportedActions"],
            "supportedLinks": _PDP_DEFAULTS["supportedLinks"],
        }
    }
    body = {
        "operationName": "productDetailsPage",
        "variables": variables,
        "query": _PRODUCT_DETAILS_QUERY,
        "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.3.3"}},
    }
    try:
        payload = await _graphql(session, headers, body)
    except AuthExpiredError:
        headers = _headers(force_refresh=True)
        headers["x-apollo-operation-name"] = "productDetailsPage"
        payload = await _graphql(session, headers, body)

    cards: List[Dict[str, Any]] = []
    _walk_product_cards(((payload.get("data") or {}).get("productDetailsPage")), cards)
    if not cards:
        return None
    card = dict(cards[0])
    card["_source"] = "productDetailsPage"
    return card


async def fetch_products_by_ids(
    session: AsyncSession,
    store_id: str,
    product_ids: List[str],
    force_refresh: bool = False,
) -> List[Dict[str, Any]]:
    """Exact SKUs via productDetailsPage (live app path for Product Finder PDP)."""
    out: List[Dict[str, Any]] = []
    for pid in product_ids:
        try:
            card = await fetch_product_details(session, store_id, pid, force_refresh=force_refresh)
        except (AuthExpiredError, NetworkError) as exc:
            logger.warning("productDetailsPage failed for %s (%s)", pid, exc)
            continue
        if card:
            out.append(card)
    return out


def product_id_from_card(card: Dict[str, Any]) -> Optional[str]:
    pid = card.get("productId")
    if pid is None:
        return None
    text = str(pid).lstrip("0") or str(pid)
    return text


def is_ashfield_instore_product(card: Dict[str, Any]) -> bool:
    """Keep store-scoped Iris hits that look stocked in-store at Ashfield."""
    if not product_id_from_card(card):
        return False
    if card.get("isAvailable") is False:
        return False
    if card.get("isMarketPlaceProduct") or card.get("marketplace"):
        return False
    loc_text = (
        ((card.get("inStoreLocation") or {}).get("displayInfo") or {}).get("locationText")
        or ((card.get("inStoreDetails") or {}).get("locationText"))
        or ""
    )
    lowered = str(loc_text).lower()
    if lowered and ("not available" in lowered or "see in store" in lowered):
        return False
    return True


def _product_list_argument(term: str) -> str:
    """Match live app search argument shape when term is a plain leaf/search string."""
    text = (term or "").strip()
    if not text:
        return text
    if "searchTerm=" in text or text.startswith("http"):
        return text
    return f"{text}?searchTerm={text}"


async def fetch_product_list_page(
    session: AsyncSession,
    store_id: str,
    term: str,
    *,
    page_size: int = 40,
    next_page: Optional[Any] = None,
    force_refresh: bool = False,
) -> tuple[List[Dict[str, Any]], Optional[Any], int]:
    """One page of Iris `productList` (type=search, storeId). Returns cards, nextPage, total.

    Store-scoped: pass Ashfield ``storeId`` (1213). Cards are still filtered by the caller
    with ``is_ashfield_instore_product``.

    Pagination: response field ``nextPage`` is an Int page index; the app sends it back as
    input ``pageNumber`` (ProductListInput has pageNumber/pageSize, not nextPage).
    """
    headers = _headers(force_refresh=force_refresh)
    headers["x-apollo-operation-name"] = "productList"
    # Live app (26.16.0) includes this hash on productList; harmless if schema drifts.
    headers.setdefault(
        "x-apollo-operation-id",
        "4fa23e33d70f030895ed214d6f4b9e4fd01dc80b2cd962fd257a625afc1b8883",
    )
    page_number: Optional[int] = None
    if next_page is not None and next_page != "":
        try:
            page_number = int(next_page)
        except (TypeError, ValueError):
            page_number = None
    product_list_input: Dict[str, Any] = {
        "type": "search",
        "argument": _product_list_argument(term),
        "storeId": str(store_id),
        "chips": _LIST_DEFAULTS.get("chips") or {"selected": [], "toggleOn": [], "toggleOff": []},
        "initialLoad": page_number is None or page_number <= 1,
        "supportedLinks": _LIST_DEFAULTS["supportedLinks"],
        "persistedChipIds": [],
    }
    if page_size:
        product_list_input["pageSize"] = int(page_size)
    if page_number is not None and page_number > 1:
        product_list_input["pageNumber"] = page_number
        product_list_input["initialLoad"] = False
    variables = {"productListInput": product_list_input}
    body = {
        "operationName": "productList",
        "variables": variables,
        "query": _PRODUCT_LIST_QUERY,
        "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.3.3"}},
    }
    try:
        payload = await _graphql(session, headers, body)
    except AuthExpiredError:
        headers = _headers(force_refresh=True)
        headers["x-apollo-operation-name"] = "productList"
        headers.setdefault(
            "x-apollo-operation-id",
            "4fa23e33d70f030895ed214d6f4b9e4fd01dc80b2cd962fd257a625afc1b8883",
        )
        payload = await _graphql(session, headers, body)

    product_list = (payload.get("data") or {}).get("productList") or {}
    feed = product_list.get("productsFeed") or []
    cards = [dict(p, _source="productList") for p in feed if isinstance(p, dict) and p.get("productId")]
    total = int(product_list.get("totalNumberOfProducts") or 0)
    return cards, product_list.get("nextPage"), total


async def search_products(
    session: AsyncSession,
    store_id: str,
    term: str,
    page_size: int = 20,
    force_refresh: bool = False,
) -> List[Dict[str, Any]]:
    """Search via live `productList` query (type=search) — first page only."""
    cards, _, _ = await fetch_product_list_page(
        session, store_id, term, page_size=page_size, force_refresh=force_refresh
    )
    return cards


async def fetch_woolworths_instore_locations(
    session: AsyncSession,
    store_id: str,
    product_ids: List[str],
) -> Dict[int, AisleEnrichment]:
    """Coles-parity helper: SKU int → AisleEnrichment for apply_aisle_enrichment."""
    cards = await fetch_products_by_ids(session, store_id, product_ids)
    out: Dict[int, AisleEnrichment] = {}
    for card in cards:
        enrichment = parse_woolworths_placement(card)
        if enrichment is None:
            continue
        try:
            pid = str(card.get("productId") or "").lstrip("0") or str(card.get("productId"))
            out[int(pid)] = enrichment
        except (TypeError, ValueError):
            continue
    return out
