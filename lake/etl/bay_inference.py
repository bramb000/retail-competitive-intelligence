"""Infer Coles bay ids from indoor x/y; keep Woolworths native bayNumber.

WW and Coles indoor CRS are different stores/maps — only bay *counts* after
inference are cross-banner comparable, not raw coordinates.

Coles does not expose bay numbers. Their in-store map already snaps many SKUs
onto a small set of pins along each aisle side (typically ~6–14 unique pins).
Treating each distinct pin cluster as one bay matches that geometry. Gap-based
merging (large multiples of within-pin spacing) under-clustered whole aisles
into 1–2 fake bays and made category mixes look worse than they are.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("hybrid_scraper.lake.bay_inference")

# Map pins that land within this Euclidean distance (Coles indoor units) are
# the same bay. Real adjacent pins are typically 180–400 units apart.
_PIN_MERGE_EPS = 25.0


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _aisle_side(row: Dict[str, Any]) -> str:
    return str(row.get("aisle_side") or row.get("bay_number") or "").strip() or "_"


def bay_key(aisle: Any, bay: Any, side: Any = None) -> Optional[str]:
    """Stable bay id. Include side when present so left/right of the same aisle
    do not collide (Coles renumbers 1..N per side independently)."""
    if aisle is None or aisle == "" or bay is None or bay == "":
        return None
    aisle_text = str(aisle).replace("Aisle ", "").strip()
    side_text = str(side).strip() if side not in (None, "") else ""
    if side_text and side_text != "_":
        return f"{aisle_text}|{side_text}|{bay}"
    return f"{aisle_text}|{bay}"


def calibrate_ww_bay_pitch(ww_rows: Iterable[Dict[str, Any]]) -> Optional[float]:
    """Median Euclidean distance between consecutive bay centroids on the same aisle.

    Logged for QA only — WW and Coles maps use different CRS, so this pitch
    must not be applied as a Coles threshold.
    """
    by_aisle: Dict[str, Dict[str, List[Tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    for row in ww_rows:
        aisle = row.get("aisle_number")
        bay = row.get("bay_number")
        x, y = _num(row.get("indoor_x")), _num(row.get("indoor_y"))
        if aisle is None or bay is None or x is None or y is None:
            continue
        if not str(bay).isdigit():
            continue
        by_aisle[str(aisle)][str(bay)].append((x, y))

    pitches: List[float] = []
    for aisle, bays in by_aisle.items():
        centroids: List[Tuple[int, float, float]] = []
        for bay, points in bays.items():
            try:
                bay_n = int(bay)
            except ValueError:
                continue
            cx = statistics.mean(p[0] for p in points)
            cy = statistics.mean(p[1] for p in points)
            centroids.append((bay_n, cx, cy))
        centroids.sort(key=lambda item: item[0])
        for left, right in zip(centroids, centroids[1:]):
            if right[0] == left[0] + 1:
                dist = math.hypot(right[1] - left[1], right[2] - left[2])
                if dist > 0:
                    pitches.append(dist)
    if not pitches:
        logger.warning("ww bay pitch: no consecutive bay centroids with x/y — QA only")
        return None
    pitch = statistics.median(pitches)
    logger.info("ww bay pitch calibrated samples=%d median=%.4f (QA only; not applied to Coles)", len(pitches), pitch)
    return pitch


def _project_axis(points: List[Tuple[float, float]]) -> str:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if max(xs) - min(xs) >= max(ys) - min(ys):
        return "x"
    return "y"


def _cluster_pins(
    points: List[Tuple[float, float]], eps: float = _PIN_MERGE_EPS
) -> List[Tuple[float, float, List[int]]]:
    """Greedy merge of near-identical map pins. Returns (cx, cy, member_indices)."""
    clusters: List[Tuple[float, float, List[int]]] = []
    for idx, (x, y) in enumerate(points):
        placed = False
        for c_i, (cx, cy, members) in enumerate(clusters):
            if math.hypot(x - cx, y - cy) <= eps:
                members.append(idx)
                n = len(members)
                clusters[c_i] = (
                    cx + (x - cx) / n,
                    cy + (y - cy) / n,
                    members,
                )
                placed = True
                break
        if not placed:
            clusters.append((x, y, [idx]))
    return clusters


def infer_coles_bays(coles_rows: List[Dict[str, Any]], ww_pitch: Optional[float] = None) -> List[Dict[str, Any]]:
    """Assign inferred_bay / bay_key on Coles placement rows in-place and return them.

    Each distinct map-pin cluster on an (aisle, side) is one bay. Products that
    share a pin (common in Coles data) share that bay.
    """
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    skipped = 0
    for row in coles_rows:
        aisle = row.get("aisle_number")
        x, y = _num(row.get("indoor_x")), _num(row.get("indoor_y"))
        if not aisle or x is None or y is None:
            row["inferred_bay"] = None
            row["bay_key"] = None
            row["location_class"] = "other" if aisle else "unplaced"
            skipped += 1
            continue
        groups[(str(aisle), _aisle_side(row))].append(row)

    assigned = 0
    total_bays = 0
    pin_gaps: List[float] = []
    for (aisle, side), members in groups.items():
        points = [(_num(r["indoor_x"]), _num(r["indoor_y"])) for r in members]
        if any(p[0] is None or p[1] is None for p in points):
            continue
        typed_points = [(float(p[0]), float(p[1])) for p in points]
        clusters = _cluster_pins(typed_points)
        axis = _project_axis([(c[0], c[1]) for c in clusters])
        clusters.sort(key=lambda c: c[0] if axis == "x" else c[1])

        for i in range(1, len(clusters)):
            gap = abs(
                (clusters[i][0] - clusters[i - 1][0])
                if axis == "x"
                else (clusters[i][1] - clusters[i - 1][1])
            )
            if gap > 1e-6:
                pin_gaps.append(gap)

        for bay_n, (_cx, _cy, member_idxs) in enumerate(clusters, start=1):
            key = bay_key(aisle, bay_n, side)
            for idx in member_idxs:
                member = members[idx]
                member["inferred_bay"] = str(bay_n)
                member["bay_key"] = key
                member["location_class"] = "aisle"
                assigned += 1
        total_bays += len(clusters)
        logger.debug(
            "coles aisle=%s side=%s n=%d pin_clusters=%d axis=%s",
            aisle,
            side,
            len(members),
            len(clusters),
            axis,
        )

    med_pin_gap = statistics.median(pin_gaps) if pin_gaps else None
    logger.info(
        "coles bay inference mode=pin_cluster rows=%d assigned=%d skipped=%d "
        "groups=%d inferred_bays=%d median_pin_gap=%s merge_eps=%.1f ww_pitch_qa=%s",
        len(coles_rows),
        assigned,
        skipped,
        len(groups),
        total_bays,
        f"{med_pin_gap:.3f}" if med_pin_gap is not None else "n/a",
        _PIN_MERGE_EPS,
        ww_pitch,
    )
    return coles_rows


def attach_ww_bay_keys(ww_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    placed = 0
    for row in ww_rows:
        key = bay_key(row.get("aisle_number"), row.get("bay_number"), row.get("aisle_side"))
        row["inferred_bay"] = str(row.get("bay_number")) if row.get("bay_number") not in (None, "") else None
        row["bay_key"] = key
        if key:
            row["location_class"] = "aisle"
            placed += 1
        else:
            row["location_class"] = "other" if row.get("aisle_number") else "unplaced"
    logger.info("ww bay keys rows=%d placed=%d", len(ww_rows), placed)
    return ww_rows
