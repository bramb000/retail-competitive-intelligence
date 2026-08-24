"""Bronze JSONL → silver product + placement tables.

Coles categories are mapped many-to-one onto Woolworths L0 department names
via SKU fuzzy-match recommendations (lake/etl/category_crosswalk.py), with
optional manual overrides in lake/ref/category_crosswalk_overrides.csv.
"""

from __future__ import annotations

import csv
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from lake.etl.bay_inference import attach_ww_bay_keys, calibrate_ww_bay_pitch, infer_coles_bays
from lake.etl.category_crosswalk import (
    ensure_overrides_stub,
    load_effective_crosswalk,
    recommend_from_matches,
    unify_coles_category,
    write_crosswalk_used,
    write_recommended_csv,
)
from lake.etl.sku_matcher import (
    build_coles_subcategory_lookup,
    load_coles_match_rows,
    load_ww_iris_match_rows,
    match_skus,
)
from lake.io import (
    SILVER_ROOT,
    append_jsonl,
    iter_jsonl,
    latest_bronze_dir,
    utc_now_iso,
)

logger = logging.getLogger("hybrid_scraper.lake.bronze_to_silver")

COLES_CATALOGUE = Path(__file__).resolve().parents[2] / "data" / "coles_catalogue_categories.csv.csv"


def load_coles_category_sets(path: Path = COLES_CATALOGUE) -> Dict[int, List[str]]:
    by_sku: Dict[int, List[str]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                sku = int(row["retailer_product_id"])
            except (KeyError, ValueError):
                continue
            cat = (row.get("category") or "").strip()
            if cat and cat not in by_sku[sku]:
                by_sku[sku].append(cat)
    logger.info("coles catalogue categories skus=%d path=%s", len(by_sku), path)
    return dict(by_sku)


def _is_placeholder_aisle(text: Optional[str]) -> bool:
    if not text:
        return True
    lowered = text.lower()
    return "not available" in lowered or "see in store" in lowered


def _coles_items_from_bronze(bronze_dir: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    path = bronze_dir / "products_list.jsonl"
    for rec in iter_jsonl(path):
        for item in rec.get("results") or []:
            if isinstance(item, dict):
                items.append(item)
    logger.info("bronze coles items path=%s n=%d", path, len(items))
    return items


def _ww_cards_from_bronze(bronze_dir: Path) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    pdp = bronze_dir / "product_details.jsonl"
    for rec in iter_jsonl(pdp):
        card = rec.get("card")
        if isinstance(card, dict) and card.get("productId"):
            cards.append(card)
    if cards:
        logger.info("bronze ww iris cards path=%s n=%d", pdp, len(cards))
        return cards
    search = bronze_dir / "search_pages.jsonl"
    seen = set()
    for rec in iter_jsonl(search):
        for fields in rec.get("parsed") or []:
            pid = fields.get("retailer_product_id")
            if pid is None or pid in seen:
                continue
            seen.add(pid)
            cards.append({"_from_search": True, **fields})
    logger.info("bronze ww search-only products path=%s n=%d", search, len(cards))
    return cards


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    if path.exists():
        path.unlink()
    for row in rows:
        append_jsonl(path, row)
    logger.info("silver wrote path=%s rows=%d", path, len(rows))


def build_category_crosswalk(
    coles_run: Path,
    ww_run: Path,
    silver_out: Path,
    cat_sets: Dict[int, List[str]],
) -> tuple[List[Dict[str, str]], Dict[int, Dict[str, Any]]]:
    """Match SKUs → recommend Coles→WW mappings → merge overrides → audit files."""
    ensure_overrides_stub()
    coles_match_rows = load_coles_match_rows(coles_run, cat_sets)
    ww_match_rows = load_ww_iris_match_rows(ww_run)
    matches = match_skus(coles_match_rows, ww_match_rows)
    _write_jsonl(silver_out / "sku_matches.jsonl", matches)

    recommended = recommend_from_matches(matches)
    write_recommended_csv(recommended)
    crosswalk = load_effective_crosswalk()
    write_crosswalk_used(crosswalk, silver_out / "category_crosswalk_used.csv")
    logger.info(
        "category crosswalk ready matches=%d recommended=%d effective=%d",
        len(matches),
        len(recommended),
        len(crosswalk),
    )
    slug_to_l0 = {
        str(row.get("coles_category_slug") or "").strip().lower(): str(row.get("woolworths_category") or "").strip()
        for row in crosswalk
        if row.get("coles_category_slug") and row.get("woolworths_category")
    }
    subcategory_lookup = build_coles_subcategory_lookup(coles_match_rows, ww_match_rows, matches, slug_to_l0)
    return crosswalk, subcategory_lookup


def transform_coles(
    bronze_dir: Path,
    out_dir: Path,
    crosswalk: List[Dict[str, str]],
    subcategory_lookup: Optional[Dict[int, Dict[str, Any]]] = None,
    cat_sets: Optional[Dict[int, List[str]]] = None,
) -> List[Dict[str, Any]]:
    cat_sets = cat_sets if cat_sets is not None else load_coles_category_sets()
    rows: List[Dict[str, Any]] = []
    unmapped = 0
    for item in _coles_items_from_bronze(bronze_dir):
        sku = item.get("id")
        if sku is None:
            continue
        try:
            rpid = int(sku)
        except (TypeError, ValueError):
            continue
        pricing = item.get("pricing") or {}
        loc = (item.get("locations") or [{}])[0] or {}
        coords = loc.get("indoorCoordinates") or {}
        native = cat_sets.get(rpid) or [item.get("category") or ""]
        unified, unified_sub, matched = unify_coles_category(native, crosswalk)
        subcategory_hint = (subcategory_lookup or {}).get(rpid) or {}
        if subcategory_hint.get("unified_category") == unified and subcategory_hint.get("unified_subcategory"):
            unified_sub = subcategory_hint["unified_subcategory"]
        if unified == "Unmapped":
            unmapped += 1
        now = pricing.get("now")
        was = pricing.get("was")
        aisle = loc.get("aisle")
        rows.append(
            {
                "retailer": "Coles",
                "store_id": "791",
                "retailer_product_id": rpid,
                "name": item.get("name"),
                "clean_brand": item.get("brand"),
                "native_categories": native,
                "unified_category": unified,
                "unified_subcategory": unified_sub or None,
                "subcategory_source": subcategory_hint.get("subcategory_source"),
                "subcategory_confidence": subcategory_hint.get("subcategory_confidence"),
                "crosswalk_matched": matched,
                "price_now": now,
                "price_was": was if was else None,
                "unit_price": pricing.get("comparable"),
                "is_promo": bool(pricing.get("onlineSpecial") or (was and now and was > now)),
                "promo_type": pricing.get("promotionType"),
                "promo_label": "Special" if pricing.get("onlineSpecial") else None,
                "stock_status": item.get("availability"),
                "aisle_number": aisle,
                "aisle_side": loc.get("aisleSide"),
                "bay_number": loc.get("aisleSide"),
                "aisle_facing": loc.get("facing"),
                "aisle_order": loc.get("order"),
                "indoor_x": coords.get("productX"),
                "indoor_y": coords.get("productY"),
                "location_class": "aisle" if aisle and not _is_placeholder_aisle(str(aisle)) else "unplaced",
            }
        )
    infer_coles_bays(rows)
    mapped = sum(1 for r in rows if r["unified_category"] != "Unmapped")
    logger.info(
        "silver coles rows=%d mapped=%d unmapped=%d mapped_pct=%.1f",
        len(rows),
        mapped,
        unmapped,
        (100.0 * mapped / len(rows)) if rows else 0.0,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "products.jsonl", rows)
    return rows


def transform_woolworths(bronze_dir: Path, out_dir: Path) -> List[Dict[str, Any]]:
    from hybrid_scraper.woolworths_aisle_enrichment import product_summary

    rows: List[Dict[str, Any]] = []
    specials = 0
    for card in _ww_cards_from_bronze(bronze_dir):
        if card.get("_from_search"):
            cat = card.get("category") or "Unmapped"
            if str(cat).lower() == "specials":
                specials += 1
                cat = "Unmapped"
            pid = card.get("retailer_product_id")
            rows.append(
                {
                    "retailer": "Woolworths",
                    "store_id": "1213",
                    "retailer_product_id": pid,
                    "name": card.get("name"),
                    "clean_brand": card.get("clean_brand"),
                    "native_categories": [cat] if cat else [],
                    "unified_category": cat if cat != "Unmapped" else "Unmapped",
                    "unified_subcategory": card.get("sub_category_1"),
                    "crosswalk_matched": "",
                    "price_now": card.get("price_display"),
                    "price_was": float(card["prev_price"]) if card.get("prev_price") else None,
                    "unit_price": card.get("price_per_uom"),
                    "is_promo": bool(card.get("product_badge") == "Special" or card.get("prev_price")),
                    "promo_type": card.get("product_badge"),
                    "promo_label": card.get("product_badge"),
                    "stock_status": card.get("stock_status"),
                    "aisle_number": card.get("aisle_number"),
                    "aisle_side": None,
                    "bay_number": card.get("bay_number"),
                    "aisle_facing": None,
                    "aisle_order": None,
                    "indoor_x": card.get("indoor_x"),
                    "indoor_y": card.get("indoor_y"),
                    "location_class": "unplaced",
                }
            )
            continue
        summary = product_summary(card)
        pid = summary.get("product_id")
        try:
            rpid = int(pid)
        except (TypeError, ValueError):
            continue
        breadcrumb = summary.get("categories") or []
        unified = breadcrumb[0] if breadcrumb else (summary.get("category") or "Unmapped")
        if str(unified).lower() == "specials":
            specials += 1
            unified = breadcrumb[1] if len(breadcrumb) > 1 else "Unmapped"
        was = summary.get("was_price")
        now = summary.get("price")
        rows.append(
            {
                "retailer": "Woolworths",
                "store_id": "1213",
                "retailer_product_id": rpid,
                "name": summary.get("name"),
                "clean_brand": card.get("brandName") or card.get("brand"),
                "native_categories": breadcrumb,
                "unified_category": unified or "Unmapped",
                "unified_subcategory": breadcrumb[1] if len(breadcrumb) > 1 else None,
                "crosswalk_matched": "",
                "price_now": now,
                "price_was": was,
                "unit_price": summary.get("unit_price"),
                "is_promo": bool(summary.get("is_promo")),
                "promo_type": summary.get("promo_type"),
                "promo_label": summary.get("promo_label"),
                "stock_status": "In Stock" if summary.get("is_available") else "Out of Stock",
                "aisle_number": summary.get("aisle_number"),
                "aisle_side": summary.get("aisle_side"),
                "bay_number": summary.get("bay_number"),
                "aisle_facing": None,
                "aisle_order": None,
                "indoor_x": summary.get("indoor_x"),
                "indoor_y": summary.get("indoor_y"),
                "location_class": "aisle" if summary.get("bay_number") else "unplaced",
            }
        )
    attach_ww_bay_keys(rows)
    logger.info("silver ww rows=%d specials_relabeled=%d", len(rows), specials)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "products.jsonl", rows)
    return rows


def category_venn(coles_rows: List[Dict[str, Any]], ww_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    coles_cats = {r["unified_category"] for r in coles_rows if r.get("unified_category") and r["unified_category"] != "Unmapped"}
    ww_cats = {r["unified_category"] for r in ww_rows if r.get("unified_category") and r["unified_category"] != "Unmapped"}
    both = sorted(coles_cats & ww_cats)
    coles_only = sorted(coles_cats - ww_cats)
    ww_only = sorted(ww_cats - coles_cats)
    rows = (
        [{"side": "both", "unified_category": c} for c in both]
        + [{"side": "coles_only", "unified_category": c} for c in coles_only]
        + [{"side": "ww_only", "unified_category": c} for c in ww_only]
    )
    logger.info(
        "category venn both=%d coles_only=%d ww_only=%d",
        len(both),
        len(coles_only),
        len(ww_only),
    )
    return rows


def run_bronze_to_silver(coles_run: Optional[Path] = None, ww_run: Optional[Path] = None) -> Path:
    coles_run = coles_run or latest_bronze_dir("coles", "791")
    ww_run = ww_run or latest_bronze_dir("woolworths", "1213")
    stamp = utc_now_iso().replace(":", "").split(".")[0]
    out = SILVER_ROOT / stamp
    out.mkdir(parents=True, exist_ok=True)
    logger.info("bronze_to_silver start out=%s coles_bronze=%s ww_bronze=%s", out, coles_run, ww_run)

    cat_sets = load_coles_category_sets()
    crosswalk: List[Dict[str, str]] = []
    subcategory_lookup: Dict[int, Dict[str, Any]] = {}
    if coles_run and ww_run:
        crosswalk, subcategory_lookup = build_category_crosswalk(coles_run, ww_run, out, cat_sets)
    else:
        ensure_overrides_stub()
        crosswalk = load_effective_crosswalk()
        logger.warning("crosswalk built without both bronze dirs — using existing recommended/overrides only")

    coles_rows: List[Dict[str, Any]] = []
    ww_rows: List[Dict[str, Any]] = []
    if coles_run:
        coles_rows = transform_coles(
            coles_run,
            out / "coles",
            crosswalk,
            subcategory_lookup=subcategory_lookup,
            cat_sets=cat_sets,
        )
    else:
        logger.warning("no coles bronze dir — silver coles skipped")
    if ww_run:
        ww_rows = transform_woolworths(ww_run, out / "woolworths")
        pitch = calibrate_ww_bay_pitch(ww_rows)
        logger.info("ww_pitch_for_qa=%s (not applied to Coles CRS)", pitch)
    else:
        logger.warning("no woolworths bronze dir — silver ww skipped")
    venn = category_venn(coles_rows, ww_rows)
    _write_jsonl(out / "category_venn.jsonl", venn)

    mapped_slugs = {r["coles_category_slug"] for r in crosswalk}
    native_slugs = set()
    for cats in cat_sets.values():
        for c in cats:
            native_slugs.add(c.strip().lower().replace(" ", "-"))
    unmapped_slugs = sorted(native_slugs - mapped_slugs)
    meta = {
        "created_at": utc_now_iso(),
        "coles_bronze": str(coles_run) if coles_run else None,
        "ww_bronze": str(ww_run) if ww_run else None,
        "coles_rows": len(coles_rows),
        "ww_rows": len(ww_rows),
        "crosswalk_rules": len(crosswalk),
        "crosswalk_mapped_slugs": sorted(mapped_slugs),
        "crosswalk_unmapped_slugs": unmapped_slugs,
    }
    (out / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    logger.info(
        "bronze_to_silver done out=%s crosswalk_rules=%d unmapped_slugs=%d",
        out,
        len(crosswalk),
        len(unmapped_slugs),
    )
    return out
