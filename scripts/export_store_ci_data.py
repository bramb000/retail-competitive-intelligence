#!/usr/bin/env python3
"""Export category/subcategory × location competitive-intelligence snapshot for apps/store-ci."""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import duckdb  # noqa: E402

from lake.etl.category_glossary import (  # noqa: E402
    bilingual_blurb,
    build_gloss_index,
    load_glossary,
    resolve_shared_l1,
    rollup_gold_by_shared,
    shared_label_for_gold,
    subcategory_alias_map,
    subcategory_cross_parent_map,
)
from lake.etl.known_value_items import build_kvi_scoreboard  # noqa: E402

GOLD_DB = REPO / "lake" / "gold" / "ashfield_compare.duckdb"
COLES_CATALOGUE = REPO / "data" / "coles_catalogue_categories.csv.csv"
OUT = REPO / "apps" / "store-ci" / "public" / "data" / "store_ci.json"

# Active location cell — product grain is category × location.
LOCATION = {
    "id": "ashfield",
    "name": "Ashfield",
    "state": "NSW",
    "country": "AU",
    "stores": {"Coles": "791", "Woolworths": "1213"},
}

# Contested when absolute bay-share gap is under this (percentage points).
DOMINANCE_BAY_EPS = 1.5
# Contested on SKU share when abs gap under this (percentage points of that banner's store).
DOMINANCE_SKU_EPS = 2.0
# Category medians within this % are "price-aligned"; beyond = clear gap.
PRICE_ALIGNED_PCT = 5.0
PRICE_HOT_PCT = 15.0
# Hide aisle for both banners when either has this share of SKUs on department
# fixtures without bay numbers (location_class=other — e.g. Produce Department).
# Unplaced-only (missing map data) does NOT trigger — that is data gaps, not fixtures.
DEPT_FIXTURE_SHARE_MAX = 0.50


def _empty_dept(cat_id: str, g: Dict[str, str], *, taxonomy: str) -> Dict[str, Any]:
    return {
        "id": cat_id,
        "location_id": LOCATION["id"],
        "parent_category": None,
        "shared_label": g["shared_label"],
        "coles_label": g["coles_label"],
        "ww_label": g["ww_label"],
        "blurb": g["blurb"],
        "taxonomy": taxonomy,
        "data_status": "awaiting_scrape",
        "coles_skus": 0,
        "ww_skus": 0,
        "coles_pct_store_bays": None,
        "ww_pct_store_bays": None,
        "coles_bay_count": None,
        "ww_bay_count": None,
        "coles_pct_promo": None,
        "ww_pct_promo": None,
        "coles_median_price": None,
        "ww_median_price": None,
        "in_venn": "unknown",
        "gold_keys": [],
        "bay_comparable": True,
        "coles_bay_coverage_pct": None,
        "ww_bay_coverage_pct": None,
    }


def _clean(v: Any) -> Any:
    if v is None:
        return None
    try:
        import pandas as pd

        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item"):
        try:
            return _clean(v.item())
        except Exception:
            pass
    return v


def _median(vals: Sequence[Optional[float]]) -> Optional[float]:
    nums = sorted(float(v) for v in vals if v is not None)
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2:
        return nums[mid]
    return (nums[mid - 1] + nums[mid]) / 2.0


def _finish_departments(departments: List[Dict[str, Any]], coles_store_n: int, ww_store_n: int) -> List[Dict[str, Any]]:
    departments.sort(
        key=lambda d: (
            0 if d["data_status"] == "ready" else 1,
            -(d["coles_skus"] + d["ww_skus"]),
            d.get("parent_category") or "",
            d["shared_label"],
        )
    )
    for d in departments:
        d["coles_pct_store_skus"] = round(100.0 * d["coles_skus"] / coles_store_n, 1) if d["coles_skus"] else 0.0
        d["ww_pct_store_skus"] = round(100.0 * d["ww_skus"] / ww_store_n, 1) if d["ww_skus"] else 0.0
        cm, wm = d.get("coles_median_price"), d.get("ww_median_price")
        if cm is not None and wm is not None and wm != 0:
            d["median_gap_pct_coles_vs_ww"] = round(100.0 * (float(cm) - float(wm)) / float(wm), 1)
        else:
            d["median_gap_pct_coles_vs_ww"] = None
    return departments


def _ci_visible(d: Dict[str, Any]) -> bool:
    """Hide the aisle for both banners when bay coverage is not comparable."""
    if d.get("bay_comparable") is False:
        return False
    return True


def _load_coles_catalogue(path: Path = COLES_CATALOGUE) -> Dict[int, List[str]]:
    """Coles product id → catalogue department slug(s)."""
    if not path.exists():
        return {}
    import csv
    from collections import defaultdict

    by_sku: Dict[int, List[str]] = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                pid = int(row.get("retailer_product_id") or 0)
            except (TypeError, ValueError):
                continue
            slug = (row.get("category") or "").strip().lower()
            if pid and slug and slug not in by_sku[pid]:
                by_sku[pid].append(slug)
    return dict(by_sku)


def _coles_slug_to_shared(glossary_rows: List[Dict[str, str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in glossary_rows:
        slug = (row.get("coles_slug") or "").strip().lower()
        shared = (row.get("shared_label") or "").strip()
        if slug and shared:
            out[slug] = shared
    return out


def _pick_coles_native_label(
    slugs: List[str],
    unified_cat: Optional[str],
    slug_labels: Dict[str, str],
    slug_to_shared: Dict[str, str],
    gloss: Dict[str, Any],
) -> Optional[str]:
    if not slugs:
        return None
    if len(slugs) == 1:
        return _format_coles_slug(slugs[0], slug_labels)
    target_shared = shared_label_for_gold(str(unified_cat), gloss) if unified_cat else None
    if target_shared:
        for slug in slugs:
            if slug_to_shared.get(slug) == target_shared:
                return _format_coles_slug(slug, slug_labels)
    return _format_coles_slug(slugs[0], slug_labels)


def _coles_slug_labels(glossary_rows: List[Dict[str, str]]) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for row in glossary_rows:
        slug = (row.get("coles_slug") or "").strip().lower()
        label = (row.get("coles_label") or "").strip()
        if slug and label:
            labels[slug] = label
    return labels


def _format_coles_slug(slug: str, slug_labels: Dict[str, str]) -> str:
    key = slug.strip().lower()
    if key in slug_labels:
        return slug_labels[key]
    return key.replace("-", " ").title()


# Weak / catch-all WW parents — Coles-empty L1s here merge into stronger parents
# when the same subcategory label has Coles SKUs elsewhere.
_WEAK_L1_PARENTS = frozenset({"Everyday Market", "Dinner", "Lunch Box"})


def _auto_merge_coles_empty_l1(
    mapped_l1,
) -> Dict[Tuple[str, str], Tuple[str, str]]:
    """Map (parent, sub) → (target_parent, sub) when Coles is empty but same sub has Coles elsewhere."""
    from collections import defaultdict

    counts: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: {"Coles": 0, "Woolworths": 0})
    for parent, sub, retailer in zip(
        mapped_l1["_shared_parent"].tolist(),
        mapped_l1["_shared_sub"].tolist(),
        mapped_l1["retailer"].tolist(),
    ):
        if parent and sub:
            counts[(str(parent), str(sub))][str(retailer)] += 1

    by_sub: Dict[str, List[Tuple[str, int, int]]] = defaultdict(list)
    for (parent, sub), c in counts.items():
        by_sub[sub].append((parent, c["Coles"], c["Woolworths"]))

    merge: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for (parent, sub), c in counts.items():
        if c["Coles"] > 0:
            continue
        options = [(p, oc, ow) for p, oc, ow in by_sub[sub] if p != parent and oc > 0 and p not in _WEAK_L1_PARENTS]
        if not options:
            # Allow merge into weak only if that's the only Coles home (rare).
            options = [(p, oc, ow) for p, oc, ow in by_sub[sub] if p != parent and oc > 0]
        if not options:
            continue
        # Prefer non-weak targets, then most Coles SKUs.
        options.sort(key=lambda t: (0 if t[0] not in _WEAK_L1_PARENTS else 1, -t[1]))
        target = options[0][0]
        # Always merge weak sources; also merge non-weak sources when clearly misplaced
        # (same L1 name with Coles living under another strong parent).
        if parent in _WEAK_L1_PARENTS or target != parent:
            merge[(parent, sub)] = (target, sub)
    return merge


def export() -> Path:
    if not GOLD_DB.exists():
        payload = _empty_payload("Gold DB missing — run scrape_ashfield_deep.py --phase etl")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        print(f"wrote empty scaffold {OUT}")
        return OUT

    conn = duckdb.connect(str(GOLD_DB), read_only=True)
    facts_all = conn.execute(
        """
        SELECT retailer, retailer_product_id, name, clean_brand, unified_category,
               unified_subcategory, price_now, price_was, unit_price, is_promo, bay_key,
               indoor_x, indoor_y, location_class
        FROM gold.sku_facts
        """
    ).fetchdf()
    space_l0 = conn.execute("SELECT * FROM gold.category_space").fetchdf()
    space_l1 = conn.execute("SELECT * FROM gold.subcategory_space").fetchdf()
    pricing_l0 = conn.execute("SELECT * FROM gold.category_pricing").fetchdf()
    matches = conn.execute("SELECT * FROM gold.sku_matches").fetchdf()
    crosswalk = conn.execute("SELECT * FROM gold.category_crosswalk").fetchdf()
    conn.close()

    # Fair bay comparison: only aisle-placed SKUs for both banners. Department
    # fixtures (Produce Department, Deli Department, etc. → location_class=other)
    # and unplaced SKUs are excluded from assortment, promo, price, and match views.
    # Bay-share tables are already aisle-only in gold.
    facts = facts_all[facts_all["location_class"] == "aisle"].copy()
    excluded_non_bay = int(len(facts_all) - len(facts))

    glossary_rows = load_glossary()
    gloss = build_gloss_index(glossary_rows)
    coles_catalogue = _load_coles_catalogue()
    coles_slug_labels = _coles_slug_labels(glossary_rows)
    coles_slug_to_shared = _coles_slug_to_shared(glossary_rows)

    def gloss_for(cat: str) -> Dict[str, str]:
        shared = shared_label_for_gold(cat, gloss)
        g = gloss.get(shared) or gloss.get(cat) or {}
        if g:
            return {
                "shared_label": g.get("shared_label") or shared,
                "coles_label": g.get("coles_aliases") or g.get("coles_label") or "—",
                "ww_label": g.get("ww_label") or cat,
                "blurb": bilingual_blurb(g.get("shared_label") or shared, gloss),
            }
        return {
            "shared_label": shared,
            "coles_label": "—" if shared != "Unmapped" else "Unmapped",
            "ww_label": cat,
            "blurb": f"Coles: — · Woolworths: {cat}",
        }

    def row_id(parent: str, sub: str) -> str:
        return f"{parent}::{sub}"

    def space_pct(frame) -> Optional[float]:
        if frame.empty:
            return None
        bay = 0.0
        store_bays = None
        for _, row in frame.iterrows():
            v = _clean(row.get("bay_count"))
            if v is not None:
                bay += float(v)
            sb = _clean(row.get("store_bay_count"))
            if sb is not None:
                store_bays = int(sb)
        if not store_bays:
            return None
        return round(100.0 * bay / store_bays, 1)

    def bay_count(frame) -> Optional[float]:
        if frame.empty:
            return None
        total = 0.0
        any_row = False
        for _, row in frame.iterrows():
            v = _clean(row.get("bay_count"))
            if v is not None:
                total += float(v)
                any_row = True
        return round(total, 3) if any_row else None

    unmapped_coles = int(len(facts[(facts["unified_category"] == "Unmapped") & (facts["retailer"] == "Coles")]))
    unmapped_ww = int(len(facts[(facts["unified_category"] == "Unmapped") & (facts["retailer"] == "Woolworths")]))
    coles_store_n = max(int(len(facts[facts["retailer"] == "Coles"])), 1)
    ww_store_n = max(int(len(facts[facts["retailer"] == "Woolworths"])), 1)

    mapped = facts[facts["unified_category"].notna() & (facts["unified_category"] != "Unmapped")].copy()
    mapped_l1 = mapped[mapped["unified_subcategory"].notna() & (mapped["unified_subcategory"] != "")].copy()

    def _class_rate(frame, location_class: str) -> Optional[float]:
        if frame is None or len(frame) == 0:
            return None
        n = int((frame["location_class"] == location_class).sum())
        return n / len(frame)

    def make_dept(
        *,
        cat_id,
        parent,
        shared,
        coles_label,
        ww_label,
        blurb,
        taxonomy,
        cf,
        wf,
        cs,
        ws,
        gold_keys,
        all_cf=None,
        all_wf=None,
    ):
        coles_promo = float(cf["is_promo"].fillna(False).mean() * 100) if len(cf) else None
        ww_promo = float(wf["is_promo"].fillna(False).mean() * 100) if len(wf) else None
        has_data = len(cf) + len(wf) > 0
        src_c = all_cf if all_cf is not None else cf
        src_w = all_wf if all_wf is not None else wf
        c_aisle = _class_rate(src_c, "aisle")
        w_aisle = _class_rate(src_w, "aisle")
        c_other = _class_rate(src_c, "other")
        w_other = _class_rate(src_w, "other")
        # Only hide when a banner's range is mostly department fixtures (other),
        # not when SKUs are merely unplaced / missing coordinates.
        bay_ok = (c_other is None or c_other < DEPT_FIXTURE_SHARE_MAX) and (
            w_other is None or w_other < DEPT_FIXTURE_SHARE_MAX
        )
        c_bay_pct = space_pct(cs) if bay_ok else None
        w_bay_pct = space_pct(ws) if bay_ok else None
        c_bay_n = bay_count(cs) if bay_ok else None
        w_bay_n = bay_count(ws) if bay_ok else None
        return {
            "id": cat_id,
            "location_id": LOCATION["id"],
            "parent_category": parent,
            "shared_label": shared,
            "coles_label": coles_label,
            "ww_label": ww_label,
            "blurb": blurb,
            "taxonomy": taxonomy,
            "data_status": "ready" if has_data else "awaiting_scrape",
            "gold_keys": gold_keys,
            "bay_comparable": bay_ok,
            "coles_bay_coverage_pct": round(100.0 * c_aisle, 1) if c_aisle is not None else None,
            "ww_bay_coverage_pct": round(100.0 * w_aisle, 1) if w_aisle is not None else None,
            "coles_dept_fixture_pct": round(100.0 * c_other, 1) if c_other is not None else None,
            "ww_dept_fixture_pct": round(100.0 * w_other, 1) if w_other is not None else None,
            "coles_skus": int(len(cf)),
            "ww_skus": int(len(wf)),
            "coles_pct_store_bays": c_bay_pct,
            "ww_pct_store_bays": w_bay_pct,
            "coles_bay_count": c_bay_n,
            "ww_bay_count": w_bay_n,
            "coles_store_bay_count": (
                int(_clean(cs.iloc[0]["store_bay_count"]))
                if bay_ok and not cs.empty and _clean(cs.iloc[0].get("store_bay_count")) is not None
                else None
            ),
            "ww_store_bay_count": (
                int(_clean(ws.iloc[0]["store_bay_count"]))
                if bay_ok and not ws.empty and _clean(ws.iloc[0].get("store_bay_count")) is not None
                else None
            ),
            "coles_pct_promo": round(coles_promo, 1) if coles_promo is not None else None,
            "ww_pct_promo": round(ww_promo, 1) if ww_promo is not None else None,
            "coles_median_price": _median([_clean(x) for x in cf["price_now"].tolist()]),
            "ww_median_price": _median([_clean(x) for x in wf["price_now"].tolist()]),
            "in_venn": (
                "both" if len(cf) and len(wf) else "coles_only" if len(cf) else "ww_only" if len(wf) else "unknown"
            ),
        }

    gold_cats = {c for c in mapped["unified_category"].dropna().unique() if c}
    glossary_shared: List[str] = []
    seen_shared = set()
    for row in glossary_rows:
        shared = (row.get("shared_label") or "").strip()
        if shared and shared not in seen_shared:
            seen_shared.add(shared)
            glossary_shared.append(shared)

    gold_by_shared = rollup_gold_by_shared(gold_cats, glossary_rows)
    for shared in glossary_shared:
        gold_by_shared.setdefault(shared, [])
    orphan_gold = [
        gc
        for gc in sorted(gold_cats)
        if shared_label_for_gold(gc, gloss) not in seen_shared and shared_label_for_gold(gc, gloss) == gc
    ]

    l0: List[Dict[str, Any]] = []
    specs_l0: List[tuple] = [(s, gold_by_shared.get(s) or [], "glossary") for s in glossary_shared]
    specs_l0 += [(gc, [gc], "observed") for gc in orphan_gold]
    for cat_id, gold_keys, taxonomy in specs_l0:
        g = gloss_for(cat_id)
        if not gold_keys:
            l0.append(_empty_dept(cat_id, g, taxonomy=taxonomy))
            continue
        key_set = set(gold_keys)
        all_mask = facts_all["unified_category"].isin(key_set)
        l0.append(
            make_dept(
                cat_id=cat_id,
                parent=None,
                shared=g["shared_label"],
                coles_label=g["coles_label"],
                ww_label=g["ww_label"],
                blurb=g["blurb"],
                taxonomy=taxonomy,
                cf=mapped[(mapped["unified_category"].isin(key_set)) & (mapped["retailer"] == "Coles")],
                wf=mapped[(mapped["unified_category"].isin(key_set)) & (mapped["retailer"] == "Woolworths")],
                cs=space_l0[(space_l0["unified_category"].isin(key_set)) & (space_l0["retailer"] == "Coles")],
                ws=space_l0[(space_l0["unified_category"].isin(key_set)) & (space_l0["retailer"] == "Woolworths")],
                gold_keys=gold_keys,
                all_cf=facts_all[all_mask & (facts_all["retailer"] == "Coles")],
                all_wf=facts_all[all_mask & (facts_all["retailer"] == "Woolworths")],
            )
        )
    l0 = _finish_departments(l0, coles_store_n, ww_store_n)
    l0_bay_ok = {d["id"]: bool(d.get("bay_comparable", True)) for d in l0}

    alias_map = subcategory_alias_map()
    cross_parent = subcategory_cross_parent_map()

    def shared_parent_sub(cat: Any, sub: Any) -> Optional[Tuple[str, str]]:
        if cat is None or sub is None:
            return None
        parent = shared_label_for_gold(str(cat), gloss)
        return resolve_shared_l1(parent, str(sub), alias_map=alias_map, cross_parent=cross_parent)

    # Annotate mapped_l1 with shared parent + canonical subcategory for rollup.
    mapped_l1 = mapped_l1.copy()
    shared_pairs: List[Optional[Tuple[str, str]]] = []
    for cat, sub in zip(mapped_l1["unified_category"].tolist(), mapped_l1["unified_subcategory"].tolist()):
        shared_pairs.append(shared_parent_sub(cat, sub))
    mapped_l1["_shared_parent"] = [p[0] if p else None for p in shared_pairs]
    mapped_l1["_shared_sub"] = [p[1] if p else None for p in shared_pairs]
    mapped_l1 = mapped_l1[mapped_l1["_shared_parent"].notna() & mapped_l1["_shared_sub"].notna()].copy()

    # Second pass: merge Coles-empty L1 rows into parents that already have Coles for that label.
    l1_merge = _auto_merge_coles_empty_l1(mapped_l1)
    if l1_merge:
        new_parents = []
        new_subs = []
        for sp, ss in zip(mapped_l1["_shared_parent"].tolist(), mapped_l1["_shared_sub"].tolist()):
            dest = l1_merge.get((str(sp), str(ss)))
            if dest:
                new_parents.append(dest[0])
                new_subs.append(dest[1])
            else:
                new_parents.append(sp)
                new_subs.append(ss)
        mapped_l1["_shared_parent"] = new_parents
        mapped_l1["_shared_sub"] = new_subs

    space_l1 = space_l1.copy()
    space_pairs: List[Optional[Tuple[str, str]]] = []
    for cat, sub in zip(space_l1["unified_category"].tolist(), space_l1["unified_subcategory"].tolist()):
        space_pairs.append(shared_parent_sub(cat, sub))
    space_l1["_shared_parent"] = [p[0] if p else None for p in space_pairs]
    space_l1["_shared_sub"] = [p[1] if p else None for p in space_pairs]
    if l1_merge:
        new_parents = []
        new_subs = []
        for sp, ss in zip(space_l1["_shared_parent"].tolist(), space_l1["_shared_sub"].tolist()):
            if sp is None or ss is None:
                new_parents.append(sp)
                new_subs.append(ss)
                continue
            dest = l1_merge.get((str(sp), str(ss)))
            if dest:
                new_parents.append(dest[0])
                new_subs.append(dest[1])
            else:
                new_parents.append(sp)
                new_subs.append(ss)
        space_l1["_shared_parent"] = new_parents
        space_l1["_shared_sub"] = new_subs

    l1: List[Dict[str, Any]] = []
    specs_l1 = sorted(
        {
            (str(p), str(s))
            for p, s in zip(mapped_l1["_shared_parent"].tolist(), mapped_l1["_shared_sub"].tolist())
            if p and s
        }
    )
    for shared_parent, shared_sub in specs_l1:
        parent_gloss = gloss_for(shared_parent)
        cat_id = row_id(shared_parent, shared_sub)
        native_keys = sorted(
            {
                f"{c}::{s}"
                for c, s, sp, ss in zip(
                    mapped_l1["unified_category"].tolist(),
                    mapped_l1["unified_subcategory"].tolist(),
                    mapped_l1["_shared_parent"].tolist(),
                    mapped_l1["_shared_sub"].tolist(),
                )
                if sp == shared_parent and ss == shared_sub
            }
        )
        mask = (mapped_l1["_shared_parent"] == shared_parent) & (mapped_l1["_shared_sub"] == shared_sub)
        space_mask = (space_l1["_shared_parent"] == shared_parent) & (space_l1["_shared_sub"] == shared_sub)
        dept = make_dept(
            cat_id=cat_id,
            parent=shared_parent,
            shared=shared_sub,
            coles_label=shared_sub,
            ww_label=shared_sub,
            blurb=(
                f"{parent_gloss['shared_label']} subcategory · "
                f"Coles parent: {parent_gloss['coles_label']} · WW parent: {parent_gloss['ww_label']}"
            ),
            taxonomy="observed",
            cf=mapped_l1[mask & (mapped_l1["retailer"] == "Coles")],
            wf=mapped_l1[mask & (mapped_l1["retailer"] == "Woolworths")],
            cs=space_l1[space_mask & (space_l1["retailer"] == "Coles")],
            ws=space_l1[space_mask & (space_l1["retailer"] == "Woolworths")],
            gold_keys=native_keys,
        )
        # Inherit parent bay gate — produce L1 must not surface as bay-dominant.
        if not l0_bay_ok.get(shared_parent, True):
            dept["bay_comparable"] = False
            dept["coles_pct_store_bays"] = None
            dept["ww_pct_store_bays"] = None
            dept["coles_bay_count"] = None
            dept["ww_bay_count"] = None
            dept["coles_store_bay_count"] = None
            dept["ww_store_bay_count"] = None
        l1.append(dept)
    l1 = _finish_departments(l1, coles_store_n, ww_store_n)

    # Drop non-comparable aisles for BOTH banners (e.g. Fruit & Vegetables when WW
    # produce is mostly Produce Department). Showing Coles alone is not a fair CI cell.
    hidden_l0 = [d for d in l0 if not _ci_visible(d)]
    hidden_parent_ids = {d["id"] for d in hidden_l0}
    hidden_parent_labels = {d["shared_label"] for d in hidden_l0}
    hidden_gold_keys = {gk for d in hidden_l0 for gk in (d.get("gold_keys") or [])}
    hidden_gold_keys |= hidden_parent_ids | hidden_parent_labels

    l0 = [d for d in l0 if _ci_visible(d)]
    l1 = [
        d
        for d in l1
        if _ci_visible(d)
        and (d.get("parent_category") or "") not in hidden_parent_ids
        and (d.get("parent_category") or "") not in hidden_parent_labels
    ]

    # Store % denominators exclude hidden aisles so remaining rows stay consistent.
    visible_mapped = mapped[~mapped["unified_category"].isin(hidden_gold_keys)].copy()
    coles_store_n = max(int(len(visible_mapped[visible_mapped["retailer"] == "Coles"])), 1)
    ww_store_n = max(int(len(visible_mapped[visible_mapped["retailer"] == "Woolworths"])), 1)
    l0 = _finish_departments(l0, coles_store_n, ww_store_n)
    l1 = _finish_departments(l1, coles_store_n, ww_store_n)
    hidden_n = len(hidden_l0)

    l0_canon: Dict[str, str] = {}
    for d in l0:
        for gk in d.get("gold_keys") or []:
            l0_canon[gk] = d["id"]
        l0_canon[d["id"]] = d["id"]
    l1_canon: Dict[tuple[str, str], str] = {(d["parent_category"], d["shared_label"]): d["id"] for d in l1}
    # Also resolve native (gold_cat, gold_sub) → shared L1 id for SKU rows / matches.
    l1_native_canon: Dict[tuple[str, str], str] = {}
    for d in l1:
        for gk in d.get("gold_keys") or []:
            if "::" in gk:
                nat_cat, nat_sub = gk.split("::", 1)
                l1_native_canon[(nat_cat, nat_sub)] = d["id"]
            l1_native_canon[(d["parent_category"], d["shared_label"])] = d["id"]

    grain_l0 = {
        "departments": l0,
        "scoreboards": {
            "dominance": _build_dominance(l0),
            "price_competition": _build_price_competition(l0),
        },
    }
    grain_l1 = {
        "departments": l1,
        "scoreboards": {
            "dominance": _build_dominance(l1),
            "price_competition": _build_price_competition(l1),
        },
    }
    departments = l0

    skus: List[Dict[str, Any]] = []
    kvi_sku_pool: List[Dict[str, Any]] = []
    for _, r in facts.iterrows():
        cat = _clean(r.get("unified_category"))
        sub = _clean(r.get("unified_subcategory"))
        if cat and (str(cat) in hidden_gold_keys or shared_label_for_gold(str(cat), gloss) in hidden_parent_labels):
            continue
        l0_id = l0_canon.get(str(cat)) if cat and cat != "Unmapped" else None
        shared_parent = shared_label_for_gold(str(cat), gloss) if cat and cat != "Unmapped" else None
        resolved = (
            resolve_shared_l1(shared_parent, str(sub), alias_map=alias_map, cross_parent=cross_parent)
            if shared_parent and sub
            else None
        )
        if resolved and resolved in l1_merge:
            resolved = l1_merge[resolved]
        shared_sub = resolved[1] if resolved else None
        if resolved:
            shared_parent = resolved[0]
        l1_id = None
        if cat and sub:
            l1_id = l1_native_canon.get((str(cat), str(sub)))
            if not l1_id and shared_parent and shared_sub:
                l1_id = l1_canon.get((shared_parent, shared_sub))
        row = {
            "retailer": r["retailer"],
            "id": int(r["retailer_product_id"]),
            "name": _clean(r["name"]),
            "brand": _clean(r["clean_brand"]),
            "location_id": LOCATION["id"],
            "category": l0_id,
            "subcategory_id": l1_id,
            "gold_category": str(cat) if cat else None,
            "shared_label": gloss_for(str(cat))["shared_label"] if l0_id else None,
            "subcategory": shared_sub or _clean(sub),
            "native_category": None,
            "native_subcategory": None,
            "coles_mapped_category": None,
            "coles_mapped_subcategory": None,
            "ww_mapped_category": None,
            "price_now": _clean(r["price_now"]),
            "price_was": _clean(r["price_was"]),
            "unit_price": _clean(r["unit_price"]),
            "is_promo": bool(r["is_promo"]) if _clean(r["is_promo"]) is not None else False,
            "bay_key": _clean(r["bay_key"]),
            "indoor_x": _clean(r["indoor_x"]),
            "indoor_y": _clean(r["indoor_y"]),
            "location_class": _clean(r["location_class"]),
        }
        if row["retailer"] == "Coles":
            slugs = coles_catalogue.get(row["id"]) or []
            if slugs:
                row["native_category"] = _pick_coles_native_label(
                    slugs, str(cat) if cat else None, coles_slug_labels, coles_slug_to_shared, gloss
                )
            g = gloss_for(str(cat)) if cat else {}
            row["ww_mapped_category"] = shared_sub or _clean(sub) or g.get("ww_label")
        elif row["retailer"] == "Woolworths":
            row["native_category"] = str(cat) if cat else None
            row["native_subcategory"] = _clean(sub)
            shared = gloss_for(str(cat))["shared_label"] if cat else None
            g = gloss.get(shared) or {}
            row["coles_mapped_category"] = g.get("coles_label") or (
                (g.get("coles_aliases") or [None])[0] if isinstance(g.get("coles_aliases"), list) else None
            )
            # Shared L1 is the finest Coles↔WW mapping we have when Coles catalogue has no native sub.
            row["coles_mapped_subcategory"] = shared_sub or _clean(sub)
        if row["price_now"] is not None:
            kvi_sku_pool.append(row)
        if l0_id:
            skus.append(row)

    known_value = build_kvi_scoreboard(kvi_sku_pool)
    for k in known_value:
        k["location_id"] = LOCATION["id"]
    kvi_summary = {
        "defined": len(known_value),
        "both_priced": sum(1 for k in known_value if k.get("coles") and k.get("ww")),
        "comparable": sum(1 for k in known_value if k.get("comparable")),
        "not_comparable": sum(1 for k in known_value if k.get("cheaper") == "not_comparable"),
        "coles_cheaper": sum(1 for k in known_value if k.get("cheaper") == "Coles"),
        "ww_cheaper": sum(1 for k in known_value if k.get("cheaper") == "Woolworths"),
        "ties": sum(1 for k in known_value if k.get("cheaper") == "tie"),
        "coles_only": sum(1 for k in known_value if k.get("cheaper") == "coles_only"),
        "ww_only": sum(1 for k in known_value if k.get("cheaper") == "ww_only"),
    }

    bay_ids = {(str(r["retailer"]), int(r["retailer_product_id"])) for _, r in facts.iterrows()}

    match_rows = []
    for _, m in matches.iterrows():
        coles_id = int(m["coles_id"])
        ww_id = int(m["ww_id"])
        if ("Coles", coles_id) not in bay_ids or ("Woolworths", ww_id) not in bay_ids:
            continue
        ww_l0 = _clean(m["ww_l0"])
        if ww_l0 and (
            str(ww_l0) in hidden_gold_keys or shared_label_for_gold(str(ww_l0), gloss) in hidden_parent_labels
        ):
            continue
        ww_l1 = _clean(m["ww_l1"])
        shared_parent = shared_label_for_gold(str(ww_l0), gloss) if ww_l0 else None
        resolved = (
            resolve_shared_l1(shared_parent, str(ww_l1), alias_map=alias_map, cross_parent=cross_parent)
            if shared_parent and ww_l1
            else None
        )
        if resolved and resolved in l1_merge:
            resolved = l1_merge[resolved]
        shared_sub = resolved[1] if resolved else None
        if resolved:
            shared_parent = resolved[0]
        match_rows.append(
            {
                "location_id": LOCATION["id"],
                "coles_id": coles_id,
                "ww_id": ww_id,
                "coles_name": _clean(m["coles_name"]),
                "ww_name": _clean(m["ww_name"]),
                "brand": _clean(m["brand"]),
                "score": float(m["score"]) if _clean(m["score"]) is not None else None,
                "ww_l0": ww_l0,
                "ww_l1": ww_l1,
                "category": l0_canon.get(str(ww_l0)) if ww_l0 else None,
                "subcategory_id": (
                    (
                        l1_native_canon.get((str(ww_l0), str(ww_l1)))
                        or (l1_canon.get((shared_parent, shared_sub)) if shared_parent and shared_sub else None)
                    )
                    if ww_l0 and ww_l1
                    else None
                ),
            }
        )

    glossary_export = [
        {
            "shared_label": row.get("shared_label"),
            "coles_slug": row.get("coles_slug"),
            "coles_label": row.get("coles_label"),
            "ww_label": row.get("ww_label"),
            "notes": row.get("notes"),
        }
        for row in glossary_rows
    ]

    awaiting_l0 = sum(1 for d in l0 if d["data_status"] == "awaiting_scrape")
    store_totals = {
        "location_id": LOCATION["id"],
        "coles_skus": int(len(visible_mapped[visible_mapped["retailer"] == "Coles"])),
        "ww_skus": int(len(visible_mapped[visible_mapped["retailer"] == "Woolworths"])),
        "coles_mapped_skus": int(len(visible_mapped[visible_mapped["retailer"] == "Coles"])),
        "ww_mapped_skus": int(len(visible_mapped[visible_mapped["retailer"] == "Woolworths"])),
        "unmapped_coles": unmapped_coles,
        "unmapped_ww": unmapped_ww,
        "excluded_non_bay_skus": excluded_non_bay,
        "hidden_bay_incomplete_categories": hidden_n,
        "departments": len(l0),
        "departments_ready": len(l0) - awaiting_l0,
        "departments_awaiting": awaiting_l0,
        "subcategories": len(l1),
        "matched_pairs": len(match_rows),
    }

    scoreboards = {
        "dominance": grain_l0["scoreboards"]["dominance"],
        "price_competition": grain_l0["scoreboards"]["price_competition"],
        "known_value": known_value,
        "known_value_summary": kvi_summary,
    }

    location_payload = {
        **LOCATION,
        "store_totals": store_totals,
        "departments": departments,
        "scoreboards": scoreboards,
        "matches": match_rows,
        "skus": skus,
        "space": [
            {
                "location_id": LOCATION["id"],
                "retailer": _clean(r["retailer"]),
                "category": l0_canon.get(str(_clean(r["unified_category"])), _clean(r["unified_category"])),
                "gold_category": _clean(r["unified_category"]),
                "bay_count": float(r["bay_count"]) if _clean(r["bay_count"]) is not None else 0.0,
                "store_bay_count": int(r["store_bay_count"]) if _clean(r["store_bay_count"]) is not None else 0,
                "pct_store_bays": float(r["pct_store_bays"]) if _clean(r["pct_store_bays"]) is not None else 0.0,
                "placed_skus": int(r["placed_skus"]) if _clean(r["placed_skus"]) is not None else 0,
            }
            for _, r in space_l0.iterrows()
        ],
        "pricing": [
            {
                "location_id": LOCATION["id"],
                "retailer": _clean(r["retailer"]),
                "category": l0_canon.get(str(_clean(r["unified_category"])), _clean(r["unified_category"])),
                "gold_category": _clean(r["unified_category"]),
                "sku_count": int(r["sku_count"]) if _clean(r["sku_count"]) is not None else 0,
                "median_price": _clean(r["median_price"]),
                "pct_on_promo": _clean(r["pct_on_promo"]),
            }
            for _, r in pricing_l0.iterrows()
        ],
        "venn": [],
    }

    payload = {
        "meta": {
            "product": "Macro store competitive intelligence",
            "grain": "category × location",
            "default_grain": "category",
            "assumes_full_store": True,
            "location_id": LOCATION["id"],
            "location_name": LOCATION["name"],
            "suburb": LOCATION["name"],
            "stores": LOCATION["stores"],
            "locations": [
                {
                    "id": LOCATION["id"],
                    "name": LOCATION["name"],
                    "state": LOCATION["state"],
                    "stores": LOCATION["stores"],
                    "active": True,
                }
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "gold_db": str(GOLD_DB),
            "status": "ready",
            "caveats": [
                (
                    "Toggle Category vs Subcategory. Category is the full aisle family. "
                    "Subcategory uses Woolworths labels; Coles is mapped onto them."
                ),
                (
                    "Only bay-stocked products count for both banners (aisle + bay). "
                    "Department fixtures without bay numbers — e.g. Produce Department, "
                    "Deli Department — are excluded so assortment and bay share stay "
                    "comparable."
                ),
                (
                    "If either banner has most of a category on department fixtures "
                    "without bay numbers (location_class=other — e.g. Produce "
                    "Department), that whole aisle is hidden for both Coles and "
                    "Woolworths, including subcategories. Missing map pins "
                    "(unplaced) alone do not hide an aisle."
                ),
                (
                    "Same-product matching is brand + similar name (not barcodes). "
                    "Coles subcategories: inherit from a matched WW product first, else "
                    "infer inside that parent aisle — never stamp a whole Coles "
                    "department as one subcategory."
                ),
                (
                    "Bay share is “what share of this store’s identified shelf bays "
                    "belongs to this aisle?” Mixed bays are split by product mix "
                    "(fractional)."
                ),
                (
                    "Coles does not publish bay numbers; we treat each distinct "
                    "in-store map pin (per aisle side) as one bay. Woolworths bay "
                    "numbers come from their app as reported."
                ),
                (
                    "Price gaps use the middle shelf price — pack sizes can differ, "
                    "so treat large gaps as a prompt to look closer."
                ),
                (
                    "Refresh after new product data is loaded; thin or empty cells "
                    "usually mean collection is still running."
                ),
            ],
        },
        "grains": {
            "category": grain_l0,
            "subcategory": grain_l1,
        },
        "location": location_payload,
        "store_totals": store_totals,
        "glossary": glossary_export,
        "departments": departments,
        "scoreboards": scoreboards,
        "venn": location_payload["venn"],
        "crosswalk": [
            {
                "coles_slug": _clean(r["coles_category_slug"]),
                "ww_label": _clean(r["woolworths_category"]),
                "source": _clean(r["source"]),
                "match_support": _clean(r["match_support"]),
            }
            for _, r in crosswalk.iterrows()
        ],
        "matches": match_rows,
        "skus": skus,
        "space": location_payload["space"],
        "pricing": location_payload["pricing"],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(
        f"wrote {OUT} location={LOCATION['id']} categories={len(l0)} subcategories={len(l1)} "
        f"skus={len(skus)} excluded_non_bay={excluded_non_bay} "
        f"hidden_bay_incomplete={hidden_n} "
        f"kvi_both={kvi_summary['both_priced']}/{kvi_summary['defined']} "
        f"bytes={OUT.stat().st_size}"
    )
    return OUT


def _build_dominance(departments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for d in departments:
        if not d.get("bay_comparable", True):
            # Not fair to crown a space winner when either banner's range is
            # mostly off numbered bays (e.g. Produce Department).
            continue
        cb = d.get("coles_pct_store_bays")
        wb = d.get("ww_pct_store_bays")
        cs = d.get("coles_pct_store_skus")
        ws = d.get("ww_pct_store_skus")
        bay_gap = None if cb is None or wb is None else round(float(cb) - float(wb), 1)
        sku_gap = None if cs is None or ws is None else round(float(cs) - float(ws), 1)

        both = d["coles_skus"] > 0 and d["ww_skus"] > 0
        if not both:
            winner = "Coles" if d["coles_skus"] > 0 else "Woolworths" if d["ww_skus"] > 0 else None
            verdict = "one_sided"
        else:
            # Space-first: bay share is the floor signal; SKU share breaks ties.
            if bay_gap is not None and abs(bay_gap) >= DOMINANCE_BAY_EPS:
                winner = "Coles" if bay_gap > 0 else "Woolworths"
                verdict = "bay_dominant"
            elif sku_gap is not None and abs(sku_gap) >= DOMINANCE_SKU_EPS:
                winner = "Coles" if sku_gap > 0 else "Woolworths"
                verdict = "assortment_dominant"
            else:
                winner = None
                verdict = "contested"

        strength = 0.0
        if bay_gap is not None:
            strength += abs(bay_gap)
        if sku_gap is not None:
            strength += abs(sku_gap) * 0.5

        rows.append(
            {
                "id": d["id"],
                "location_id": d.get("location_id") or LOCATION["id"],
                "shared_label": d["shared_label"],
                "coles_label": d["coles_label"],
                "ww_label": d["ww_label"],
                "blurb": d["blurb"],
                "dominant": winner,
                "verdict": verdict,
                "strength": round(strength, 1),
                "data_status": d.get("data_status") or "ready",
                "coles_skus": d["coles_skus"],
                "ww_skus": d["ww_skus"],
                "coles_pct_store_skus": cs,
                "ww_pct_store_skus": ws,
                "sku_gap_pp": sku_gap,
                "coles_pct_store_bays": cb,
                "ww_pct_store_bays": wb,
                "bay_gap_pp": bay_gap,
            }
        )
    rows.sort(key=lambda r: (-(0 if r["verdict"] == "contested" else 1), -r["strength"]))
    return rows


def _build_price_competition(departments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for d in departments:
        cm, wm = d.get("coles_median_price"), d.get("ww_median_price")
        gap = d.get("median_gap_pct_coles_vs_ww")
        both = d["coles_skus"] > 0 and d["ww_skus"] > 0 and cm is not None and wm is not None
        if not both:
            status = "not_comparable"
            cheaper = None
            heat = None
        else:
            assert gap is not None
            if abs(gap) <= PRICE_ALIGNED_PCT:
                status = "aligned"
                cheaper = "tie"
                heat = "cool"
            elif abs(gap) >= PRICE_HOT_PCT:
                status = "hot_gap"
                cheaper = "Coles" if gap < 0 else "Woolworths"
                heat = "hot"
            else:
                status = "competing"
                cheaper = "Coles" if gap < 0 else "Woolworths"
                heat = "warm"

        promo_gap = None
        cp, wp = d.get("coles_pct_promo"), d.get("ww_pct_promo")
        if cp is not None and wp is not None:
            promo_gap = round(float(cp) - float(wp), 1)

        rows.append(
            {
                "id": d["id"],
                "location_id": d.get("location_id") or LOCATION["id"],
                "shared_label": d["shared_label"],
                "coles_label": d["coles_label"],
                "ww_label": d["ww_label"],
                "blurb": d["blurb"],
                "status": status,
                "heat": heat,
                "cheaper_on_median": cheaper,
                "data_status": d.get("data_status") or "ready",
                "median_gap_pct_coles_vs_ww": gap,
                "coles_median_price": cm,
                "ww_median_price": wm,
                "coles_pct_promo": cp,
                "ww_pct_promo": wp,
                "promo_gap_pp": promo_gap,
                "coles_skus": d["coles_skus"],
                "ww_skus": d["ww_skus"],
            }
        )
    # Hot gaps first, then competing, then aligned, then not comparable
    order = {"hot_gap": 0, "competing": 1, "aligned": 2, "not_comparable": 3}
    rows.sort(
        key=lambda r: (
            order.get(r["status"], 9),
            -(abs(r["median_gap_pct_coles_vs_ww"]) if r["median_gap_pct_coles_vs_ww"] is not None else -1),
        )
    )
    return rows


def _empty_payload(reason: str) -> Dict[str, Any]:
    empty_boards = {
        "dominance": [],
        "price_competition": [],
        "known_value": [],
        "known_value_summary": {
            "defined": 0,
            "both_priced": 0,
            "comparable": 0,
            "not_comparable": 0,
            "coles_cheaper": 0,
            "ww_cheaper": 0,
            "ties": 0,
            "coles_only": 0,
            "ww_only": 0,
        },
    }
    empty_totals = {
        "location_id": LOCATION["id"],
        "coles_skus": 0,
        "ww_skus": 0,
        "coles_mapped_skus": 0,
        "ww_mapped_skus": 0,
        "unmapped_coles": 0,
        "unmapped_ww": 0,
        "excluded_non_bay_skus": 0,
        "hidden_bay_incomplete_categories": 0,
        "departments": 0,
        "departments_ready": 0,
        "departments_awaiting": 0,
        "subcategories": 0,
        "matched_pairs": 0,
    }
    empty_grain = {"departments": [], "scoreboards": {"dominance": [], "price_competition": []}}
    location_payload = {
        **LOCATION,
        "store_totals": empty_totals,
        "departments": [],
        "scoreboards": empty_boards,
        "matches": [],
        "skus": [],
        "space": [],
        "pricing": [],
        "venn": [],
    }
    return {
        "meta": {
            "product": "Macro store competitive intelligence",
            "grain": "category × location",
            "default_grain": "category",
            "assumes_full_store": True,
            "location_id": LOCATION["id"],
            "location_name": LOCATION["name"],
            "suburb": LOCATION["name"],
            "stores": LOCATION["stores"],
            "locations": [
                {
                    "id": LOCATION["id"],
                    "name": LOCATION["name"],
                    "state": LOCATION["state"],
                    "stores": LOCATION["stores"],
                    "active": True,
                }
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "waiting_for_data",
            "caveats": [reason, "UI is ready — re-run export after ETL."],
        },
        "grains": {"category": empty_grain, "subcategory": empty_grain},
        "location": location_payload,
        "store_totals": empty_totals,
        "glossary": [],
        "departments": [],
        "scoreboards": empty_boards,
        "venn": [],
        "crosswalk": [],
        "matches": [],
        "skus": [],
        "space": [],
        "pricing": [],
    }


if __name__ == "__main__":
    export()
