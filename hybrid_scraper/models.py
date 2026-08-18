"""Pydantic data models shared across the hybrid scraping engine.

`Product` mirrors an externally-supplied target schema (the columns of an
existing "final table after deduplication and cleaning") exactly, so that
rows produced here can be loaded straight into that table without renaming.
Python attribute names are kept snake_case; `hybrid_scraper.storage` maps
them onto the literal target column names (including the two mixed-case
ones, `No_of_reviews` / `Star_rating` / `PLV_ID`) at the SQL boundary.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Dict, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict

RetailerName = Literal["Coles", "Woolworths"]

_SIZE_PATTERN = re.compile(r"^\s*([\d.]+)\s*([a-zA-Z]+)\s*$")


def parse_pack_size(raw_size: Optional[str]) -> Tuple[Optional[float], Optional[str]]:
    """Split a raw size string (e.g. "500g", "1.5L") into (amount, unit).

    Multi-part sizes (e.g. "2 x 250mL" multipacks) don't match the simple
    pattern and fall back to (None, None) rather than guessing.
    """
    if not raw_size:
        return None, None
    match = _SIZE_PATTERN.match(raw_size)
    if not match:
        return None, None
    amount, unit = match.groups()
    try:
        return float(amount), unit
    except ValueError:
        return None, None


@dataclass(frozen=True)
class StoreLocation:
    """A store resolved from a suburb query — the output of `CurlCffiEngine.resolve_store_id`.

    Carries enough geographic detail (`postcode`, `latitude`, `longitude`)
    to compare distance between any two resolved stores later, on top of
    the `store_id` needed to actually query that store's product API.
    """

    retailer: RetailerName
    store_id: str
    store_name: str
    suburb_name: str
    state: str
    postcode: str
    latitude: float
    longitude: float


class SessionContext(BaseModel):
    """A bundle of everything curl_cffi needs to call a retailer's internal API
    without triggering the anti-bot layer that Playwright already solved.
    """

    model_config = ConfigDict(frozen=False)

    retailer: RetailerName
    cookies: Dict[str, str]
    headers: Dict[str, str]
    store_id: str
    created_at: float
    # Akamai/Imperva session cookies and bearer tokens are short-lived by
    # design; 900s is a conservative default and can be overridden per
    # observed vendor behaviour.
    ttl_seconds: float = 900.0

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds


class MobileSessionContext(BaseModel):
    """Device-attestation headers captured live off the real Coles mobile app.

    Unlike `SessionContext`'s cookies (harvested from an automatable browser
    challenge), the app's `x-d-token` is opaque device-attestation output
    produced by the app's own native code (see `Product.aisle_number`'s
    comment) — this context can only be produced by
    `hybrid_scraper.mobile_session` capturing one off the wire from a real
    app session, never synthesized.
    """

    model_config = ConfigDict(frozen=False)

    headers: Dict[str, str]
    created_at: float
    # Observed live: a captured token worked for an initial run then started
    # 403ing on a follow-up run ~15-20 minutes later (see scrape_burwood.py's
    # module docstring) — kept deliberately conservative relative to that
    # observed ceiling.
    ttl_seconds: float = 600.0

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds


class Product(BaseModel):
    """One scraped fact for a single SKU at a single store on a single day.

    Deliberately does NOT carry store attributes (name/suburb/postcode/
    lat/lon) — those are store-dimension data, supplied once per batch via
    `StoreLocation` rather than repeated on every row (see
    `hybrid_scraper.storage`'s star schema: `stores` + `products` dimension
    tables, `price_history` fact table). `scrape_date` (not a run counter)
    is the temporal grain, matching the daily scrape cadence directly.
    """

    retailer: RetailerName
    retailer_product_id: int
    child_product_id: Optional[str] = None
    scrape_date: str  # ISO "YYYY-MM-DD" — the day this fact was captured
    scraped_at: str  # precise capture timestamp, for audit only

    # --- Dimension-ish attributes (rarely change; still carried per-row
    # here since the scrape only sees "the current value" — storage.py's
    # upsert logic is what actually keeps them from being duplicated on disk).
    name: str
    clean_brand: Optional[str] = None
    category: str
    sub_category_1: Optional[str] = None
    sub_category_2: Optional[str] = None
    sub_category_3: Optional[str] = None
    pack_size: Optional[float] = None
    clean_uom: Optional[str] = None
    product_page: Optional[str] = None
    image_url: Optional[str] = None

    # --- Fact attributes (the things that actually change day to day —
    # storage.py's SCD2 logic only opens a new price_history row when one
    # of these differs from the last recorded value).
    price_display: Optional[float] = None
    loyalty_price: Optional[str] = None
    price_per_uom: Optional[str] = None
    prev_price: Optional[str] = None
    stock_status: str
    product_badge: Optional[str] = None
    no_of_reviews: Optional[str] = None
    star_rating: Optional[float] = None
    plv_id: Optional[str] = None
    # In-store physical location. Confirmed live: Coles' *website* product
    # schema has `locations[].aisle`/`.shelf` fields but they were null for
    # every sampled product ("Aisle information is not available for this
    # product"); Woolworths' product-detail response has no physical
    # aisle/bay field at all (its `PrimaryCategory.Aisle` is an unrelated
    # *online* "shop by aisle" taxonomy label, e.g. "full cream milk", not a
    # physical in-store position).
    #
    # This IS a real, separate native-app-only feature on Coles' side —
    # internally called "Wayfinding" (`com.coles.android.instorewayfinding`,
    # confirmed via decompiling the real APK). It is gated per-store by a
    # remote-config allowlist (`AppConfigWayfinding.inStoreDetection`) —
    # confirmed live: store 584 (Coles Burwood East) is enabled and returns
    # real data; store 791 (this project's usual Ashfield-area test store)
    # is not, and still returns the dead "Aisle information is not
    # available..." placeholder seen on the website.
    #
    # FULLY CONFIRMED LIVE (via mitmproxy + a cert-pinning bypass patched
    # into the app's smali — see conversation/session history, not a repo
    # file, for the exact patch): this is the SAME product endpoint the app
    # always uses, just called with a different query param than the
    # website:
    #
    #   POST https://apigw.coles.com.au/digital/colesappbff/v3/api/2/products/list
    #        ?storeId={storeId}&shoppingMethod=inStore&limit=10
    #        &includeLiquor=true&includeTobacco=true
    #   body: {"skus": ["1788756", "9735935", ...]}   (batch lookup by SKU)
    #
    # `shoppingMethod=inStore` is the key — the website/existing scraper
    # effectively requests `clickAndCollect`, which is exactly why
    # `locations[].aisle` always came back null before. With `inStore`, the
    # response's `locations[]` entries look like:
    #   {"aisle": "Aisle 9", "aisleSide": "Right", "facing": 1, "order": 9.0,
    #    "description": "Located in Aisle 9 at $STORE",
    #    "indoorCoordinates": {"productX": 5484.292, "productY": 2760.252}}
    # i.e. richer than a bare number — a human-readable string, left/right
    # side, sort order within the aisle, and precise map pin coordinates.
    #
    # This is on a completely different host from the website:
    # `apigw.coles.com.au` (not `www.coles.com.au`), a different
    # subscription key (`ocp-apim-subscription-key`, a different value from
    # the website's — captured live from app version 6.84.0, treat as
    # rotatable; not reproduced here since it's a real credential — see
    # scrape_burwood.py's env-var setup for where a captured one goes),
    # device-identity headers (`client`, `x-app-version`,
    # `x-device-model`, `x-device-id`, `x-client-os`), and — the actual
    # remaining blocker — an `x-d-token` header: an opaque, encrypted,
    # colon-delimited blob that looks like a device-attestation/anti-bot
    # token (requests without a valid one 403 at `apigw.coles.com.au`'s
    # Incapsula/Imperva WAF; a live capture from the real app's `.../v1/
    # challenge` handshake is what produced a working one here). How the app
    # generates this token client-side is NOT yet reverse-engineered — that
    # is the next real blocker before this can be called outside the app,
    # and is likely comparable in difficulty to the Akamai problem already
    # blocking Woolworths in this project.
    aisle_number: Optional[str] = None
    bay_number: Optional[str] = None
    # The rest of the app's `locations[]` entry beyond a bare aisle/side —
    # `facing`/`order` as captured, `indoor_x`/`indoor_y` from
    # `indoorCoordinates.productX`/`.productY` (the precise store-map pin
    # position). Coles-only, same source/caveats as aisle_number above.
    aisle_facing: Optional[int] = None
    aisle_order: Optional[float] = None
    indoor_x: Optional[float] = None
    indoor_y: Optional[float] = None

    @property
    def product_key(self) -> str:
        """Surrogate key for the `products` dimension table.

        A plain composite (retailer, retailer_product_id, child_product_id)
        avoids NULL-in-primary-key issues (`child_product_id` is frequently
        absent) and matches the dedup pattern already used elsewhere
        (`fetch_all_products_for_store`'s `seen_keys`).
        """
        return f"{self.retailer}:{self.retailer_product_id}:{self.child_product_id or ''}"
