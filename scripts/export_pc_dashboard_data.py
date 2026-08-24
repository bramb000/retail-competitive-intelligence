#!/usr/bin/env python3
"""Export Personal Care gold slice to apps/ashfield-pc/public/data/personal_care.json."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import duckdb

REPO = Path(__file__).resolve().parents[1]
GOLD_DB = REPO / "lake" / "gold" / "ashfield_compare.duckdb"
OUT = REPO / "apps" / "ashfield-pc" / "public" / "data" / "personal_care.json"
CATEGORY = "Personal Care"


def _median(vals: Sequence[float]) -> Optional[float]:
    nums = sorted(v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v)))
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2:
        return float(nums[mid])
    return float((nums[mid - 1] + nums[mid]) / 2.0)


def _pctile(vals: Sequence[float], p: float) -> Optional[float]:
    nums = sorted(v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v)))
    if not nums:
        return None
    if len(nums) == 1:
        return float(nums[0])
    idx = (len(nums) - 1) * p
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(nums[lo])
    return float(nums[lo] * (hi - idx) + nums[hi] * (idx - lo))


def _clean(v: Any) -> Any:
    """Coerce pandas/numpy nulls to JSON-safe None (never NaN)."""
    if v is None:
        return None
    try:
        # pandas NA / NaT
        if v is getattr(__import__("pandas"), "NA", object()) or (hasattr(v, "__bool__") is False and str(v) == "<NA>"):
            return None
    except Exception:
        pass
    # pandas/numpy missing
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
            inner = v.item()
            return _clean(inner)
        except Exception:
            pass
    if isinstance(v, str) and v.strip().lower() in {"nan", "none", "null", "<na>"}:
        return None
    return v


def export() -> Path:
    if not GOLD_DB.exists():
        raise FileNotFoundError(f"Missing {GOLD_DB} — run scrape_ashfield_deep.py --phase etl first")

    conn = duckdb.connect(str(GOLD_DB), read_only=True)
    facts = conn.execute(
        """
        SELECT retailer, retailer_product_id, name, clean_brand, unified_subcategory,
               price_now, price_was, is_promo, bay_key, indoor_x, indoor_y, location_class
        FROM gold.sku_facts
        WHERE unified_category = ?
        """,
        [CATEGORY],
    ).fetchdf()
    space = conn.execute(
        """
        SELECT retailer, bay_count, store_bay_count, pct_store_bays, placed_skus, facing_sum
        FROM gold.category_space
        WHERE unified_category = ?
        """,
        [CATEGORY],
    ).fetchdf()
    matches_df = conn.execute(
        """
        SELECT coles_id, ww_id, coles_name, ww_name, brand, score, ww_l1
        FROM gold.sku_matches
        WHERE ww_l0 = ?
        """,
        [CATEGORY],
    ).fetchdf()
    conn.close()

    coles = facts[facts["retailer"] == "Coles"]
    ww = facts[facts["retailer"] == "Woolworths"]
    matched_coles = set(int(x) for x in matches_df["coles_id"].tolist()) if len(matches_df) else set()
    matched_ww = set(int(x) for x in matches_df["ww_id"].tolist()) if len(matches_df) else set()

    def row_side(retailer: str, pid: int) -> str:
        if retailer == "Coles":
            return "matched" if pid in matched_coles else "coles_only"
        return "matched" if pid in matched_ww else "ww_only"

    match_partner: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for _, m in matches_df.iterrows():
        match_partner[("Coles", int(m["coles_id"]))] = {
            "partner_id": int(m["ww_id"]),
            "partner_name": m["ww_name"],
            "score": float(m["score"]),
        }
        match_partner[("Woolworths", int(m["ww_id"]))] = {
            "partner_id": int(m["coles_id"]),
            "partner_name": m["coles_name"],
            "score": float(m["score"]),
        }

    skus: List[Dict[str, Any]] = []
    for _, r in facts.iterrows():
        pid = int(r["retailer_product_id"])
        retailer = str(r["retailer"])
        partner = match_partner.get((retailer, pid))
        skus.append(
            {
                "retailer": retailer,
                "id": pid,
                "name": _clean(r["name"]),
                "brand": _clean(r["clean_brand"]),
                "subcategory": _clean(r["unified_subcategory"]),
                "price_now": _clean(r["price_now"]),
                "price_was": _clean(r["price_was"]),
                "is_promo": bool(r["is_promo"]) if _clean(r["is_promo"]) is not None else False,
                "bay_key": _clean(r["bay_key"]),
                "indoor_x": _clean(r["indoor_x"]),
                "indoor_y": _clean(r["indoor_y"]),
                "location_class": _clean(r["location_class"]),
                "side": row_side(retailer, pid),
                "match_partner_id": partner["partner_id"] if partner else None,
                "match_partner_name": partner["partner_name"] if partner else None,
                "match_score": partner["score"] if partner else None,
            }
        )

    def examples(side: str, n: int = 5) -> List[str]:
        return [s["name"] for s in skus if s["side"] == side][:n]

    coles_only_n = sum(1 for s in skus if s["side"] == "coles_only")
    ww_only_n = sum(1 for s in skus if s["side"] == "ww_only")
    matched_n = len(matches_df)

    space_rows = []
    for _, sp in space.iterrows():
        space_rows.append(
            {
                "retailer": sp["retailer"],
                "bay_count": int(sp["bay_count"]) if sp["bay_count"] is not None else 0,
                "store_bay_count": int(sp["store_bay_count"]) if sp["store_bay_count"] is not None else 0,
                "pct_store_bays": float(sp["pct_store_bays"]) if sp["pct_store_bays"] is not None else 0.0,
                "placed_skus": int(sp["placed_skus"]) if sp["placed_skus"] is not None else 0,
                "facing_sum": _clean(sp["facing_sum"]),
            }
        )
    # Ensure both banners present even if space empty
    for retailer in ("Coles", "Woolworths"):
        if not any(s["retailer"] == retailer for s in space_rows):
            space_rows.append(
                {
                    "retailer": retailer,
                    "bay_count": 0,
                    "store_bay_count": 0,
                    "pct_store_bays": 0.0,
                    "placed_skus": 0,
                    "facing_sum": None,
                }
            )

    coles_promo = float(coles["is_promo"].fillna(False).mean() * 100) if len(coles) else 0.0
    ww_promo = float(ww["is_promo"].fillna(False).mean() * 100) if len(ww) else 0.0
    coles_med = _median([_clean(x) for x in coles["price_now"].tolist()])
    ww_med = _median([_clean(x) for x in ww["price_now"].tolist()])
    coles_bays_pct = next((s["pct_store_bays"] * 100 for s in space_rows if s["retailer"] == "Coles"), 0.0)
    ww_bays_pct = next((s["pct_store_bays"] * 100 for s in space_rows if s["retailer"] == "Woolworths"), 0.0)
    ratio = (len(coles) / len(ww)) if len(ww) else None

    kpis = {
        "coles_skus": len(coles),
        "ww_skus": len(ww),
        "sku_ratio_coles_per_ww": round(ratio, 1) if ratio else None,
        "matched_pairs": matched_n,
        "coles_only": coles_only_n,
        "ww_only": ww_only_n,
        "coles_pct_store_bays": round(coles_bays_pct, 1),
        "ww_pct_store_bays": round(ww_bays_pct, 1),
        "coles_pct_promo": round(coles_promo, 1),
        "ww_pct_promo": round(ww_promo, 1),
        "coles_median_price": coles_med,
        "ww_median_price": ww_med,
        "insights": {
            "assortment": (
                f"Coles lists ~{ratio:.0f}× more Personal Care SKUs in gold than Woolworths."
                if ratio
                else "Assortment comparison needs both banners."
            ),
            "overlap": (
                f"{matched_n} fuzzy-matched pairs — exclusive counts understate true overlap "
                "while WW Iris / match coverage is thin."
            ),
            "space": (
                f"Coles uses {coles_bays_pct:.0f}% of store bays for PC vs Woolworths {ww_bays_pct:.0f}% "
                "(bay share is the cross-banner space signal)."
            ),
            "promo": (
                f"Coles {coles_promo:.0f}% on promo (median ${coles_med:.2f}) vs "
                f"Woolworths {ww_promo:.0f}% (median ${ww_med:.2f})."
                if coles_med is not None and ww_med is not None
                else "Promo comparison incomplete."
            ),
        },
    }

    # Price distribution stats
    price_dist = []
    for retailer, frame in (("Coles", coles), ("Woolworths", ww)):
        prices = [_clean(x) for x in frame["price_now"].tolist()]
        prices = [p for p in prices if p is not None]
        price_dist.append(
            {
                "retailer": retailer,
                "n": len(prices),
                "median": _median(prices),
                "p25": _pctile(prices, 0.25),
                "p75": _pctile(prices, 0.75),
                "min": min(prices) if prices else None,
                "max": max(prices) if prices else None,
            }
        )

    # Histogram bins $0–80 step 5
    hist = []
    bins = list(range(0, 85, 5))
    for retailer, frame in (("Coles", coles), ("Woolworths", ww)):
        prices = [min(79.99, p) for p in (_clean(x) for x in frame["price_now"].tolist()) if p is not None]
        counts = [0] * (len(bins) - 1)
        for p in prices:
            i = min(int(p // 5), len(counts) - 1)
            counts[i] += 1
        for i, count in enumerate(counts):
            hist.append(
                {
                    "retailer": retailer,
                    "bin_start": bins[i],
                    "bin_end": bins[i + 1],
                    "label": f"${bins[i]}–{bins[i + 1]}",
                    "count": count,
                }
            )

    # Subcategory
    sub_rows = []
    grouped = facts.groupby(["retailer", facts["unified_subcategory"].fillna("(none)")], dropna=False)
    for (retailer, sub), g in grouped:
        prices = [_clean(x) for x in g["price_now"].tolist()]
        prices = [p for p in prices if p is not None]
        promo = float(g["is_promo"].fillna(False).mean() * 100)
        sub_rows.append(
            {
                "retailer": retailer,
                "subcategory": str(sub),
                "skus": len(g),
                "median_price": _median(prices),
                "pct_promo": round(promo, 1),
            }
        )
    sub_rows.sort(key=lambda r: (-r["skus"], r["retailer"], r["subcategory"]))

    # Promo ladder
    ladder = []
    for retailer, frame in (("Coles", coles), ("Woolworths", ww)):
        promo = frame[frame["is_promo"].fillna(False) & frame["price_was"].notna() & frame["price_now"].notna()]
        regular = frame[~frame["is_promo"].fillna(False)]
        if len(promo) == 0:
            continue
        ladder.append(
            {
                "retailer": retailer,
                "promo_skus": len(promo),
                "median_was": _median([_clean(x) for x in promo["price_was"].tolist()]),
                "median_now": _median([_clean(x) for x in promo["price_now"].tolist()]),
                "median_regular": _median([_clean(x) for x in regular["price_now"].tolist()]),
            }
        )

    matches = [
        {
            "coles_id": int(m["coles_id"]),
            "ww_id": int(m["ww_id"]),
            "coles_name": m["coles_name"],
            "ww_name": m["ww_name"],
            "brand": m["brand"],
            "score": float(m["score"]),
            "ww_l1": m["ww_l1"],
        }
        for _, m in matches_df.iterrows()
    ]

    payload = {
        "meta": {
            "category": CATEGORY,
            "stores": {"Coles": "791", "Woolworths": "1213"},
            "suburb": "Ashfield",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "gold_db": str(GOLD_DB),
            "caveats": [
                "Woolworths Personal Care placement in Iris is thin — space charts understate WW until more aisle-enriched bronze exists.",
                "Matched pairs use fuzzy brand+name; exclusive SKU counts understate true assortment overlap.",
                "Price ladder is a single scrape snapshot (was→now on promo SKUs), not a multi-day trend.",
                "Indoor coordinates use different CRS per banner — compare bay share, not absolute map areas.",
            ],
        },
        "kpis": kpis,
        "space": space_rows,
        "venn": {
            "coles_only": coles_only_n,
            "matched": matched_n,
            "ww_only": ww_only_n,
            "examples": {
                "coles_only": examples("coles_only"),
                "matched": [m["coles_name"] for m in matches[:5]],
                "ww_only": examples("ww_only"),
            },
        },
        "matches": matches,
        "skus": skus,
        "price_distribution": price_dist,
        "price_histogram": hist,
        "price_by_subcategory": sub_rows,
        "promo_ladder": ladder,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(f"wrote {OUT} skus={len(skus)} matches={len(matches)} bytes={OUT.stat().st_size}")
    return OUT


if __name__ == "__main__":
    export()
