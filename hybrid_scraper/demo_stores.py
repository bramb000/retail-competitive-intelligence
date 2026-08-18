"""Curated Coles pilot-store target list.

Loads pre-resolved `StoreLocation`s directly from `demo_stores.csv`,
bypassing `CurlCffiEngine.resolve_store_id`'s suburb-search flow entirely —
store IDs/coordinates in that CSV are already confirmed against the live
811-store dataset (`coles_all_stores.csv`), so re-resolving via a live
`FindStores` call would be redundant traffic against a site with observed
anti-bot sensitivity, with a chance of landing on the wrong store if
"nearest to a suburb centroid" doesn't match the specific store intended.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import List

from hybrid_scraper.models import StoreLocation

logger = logging.getLogger(__name__)

DEFAULT_DEMO_STORES_CSV = Path(__file__).resolve().parent.parent / "demo_stores.csv"


def load_demo_store_locations(csv_path: Path = DEFAULT_DEMO_STORES_CSV) -> List[StoreLocation]:
    """Build StoreLocation objects straight from the curated demo-store CSV.

    `store_id` is stripped of its "COL:" brand prefix (e.g. "COL:205" ->
    "205") to match the bare numeric id engine.py's search API and
    aisle_enrichment.py's app API both require — mirrors
    CurlCffiEngine._resolve_coles_store's identical `.split(":")[-1]`.
    """
    targets: List[StoreLocation] = []
    with open(csv_path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw_id = row["store_id"]
            native_id = raw_id.split(":")[-1] if ":" in raw_id else raw_id
            targets.append(
                StoreLocation(
                    retailer="Coles",
                    store_id=native_id,
                    store_name=row["name"],
                    suburb_name=row["suburb"],
                    state=row["state"],
                    postcode=row["postcode"],
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                )
            )
    logger.info("Loaded %d demo store targets from %s", len(targets), csv_path)
    return targets
