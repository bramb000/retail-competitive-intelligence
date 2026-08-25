"""Known-value items (KVIs) — staples that shape store price perception.

Comparisons only call a winner when pack sizes (or unit prices) are comparable.
Different sizes without a shared unit rate are marked not_comparable.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[2]
KVI_CSV = REPO / "lake" / "ref" / "known_value_items.csv"

OWN_BRAND_TOKENS = (
    "coles",
    "woolworths",
    "woolies",
    "essentials",
    "macro",
    "simply",
    "homebrand",
    "black & gold",
    "budget",
)

# Relative pack-size difference above this → not comparable on shelf price alone.
PACK_TOLERANCE = 0.30
# Unit-price relative difference under this → treat as a tie.
UNIT_TIE_EPS = 0.02


@dataclass(frozen=True)
class PackSize:
    qty: float
    unit: str  # normalised: l | ml | kg | g | ea | pack

    @property
    def base_qty(self) -> Optional[float]:
        """Quantity in a comparable base (litres, kg, or each)."""
        if self.unit == "l":
            return self.qty
        if self.unit == "ml":
            return self.qty / 1000.0
        if self.unit == "kg":
            return self.qty
        if self.unit == "g":
            return self.qty / 1000.0
        if self.unit in {"ea", "pack"}:
            return self.qty
        return None

    @property
    def family(self) -> Optional[str]:
        if self.unit in {"l", "ml"}:
            return "volume"
        if self.unit in {"kg", "g"}:
            return "mass"
        if self.unit in {"ea", "pack"}:
            return "count"
        return None

    def label(self) -> str:
        q = int(self.qty) if abs(self.qty - int(self.qty)) < 1e-6 else self.qty
        return f"{q}{self.unit}"


@dataclass(frozen=True)
class UnitRate:
    rate: float  # $ per base unit
    family: str  # volume | mass | count
    raw: str


def load_kvi_defs(path: Path = KVI_CSV) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    rows: List[Dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("kvi_id"):
                continue
            rows.append({k: (v or "").strip() for k, v in row.items()})
    return rows


def _is_own_brand(brand: Optional[str], name: Optional[str]) -> bool:
    hay = f"{brand or ''} {name or ''}".lower()
    return any(tok in hay for tok in OWN_BRAND_TOKENS)


_SIZE_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>l|lt|ltr|litre|liter|ml|kg|g|gram|grams|ea|pk|pack)\b",
    re.I,
)
_PACK_COUNT_RE = re.compile(r"\b(?P<n>\d+)\s*(?:pack|pk|pk\.|piece|pcs)\b", re.I)
_UNIT_PRICE_RE = re.compile(
    r"\$?\s*(?P<rate>\d+(?:\.\d+)?)\s*(?:/|per)\s*(?P<qty>\d+(?:\.\d+)?)?\s*(?P<unit>l|lt|kg|g|ea|each|ml)\b",
    re.I,
)


def parse_pack_size(name: Optional[str]) -> Optional[PackSize]:
    if not name:
        return None
    m = _SIZE_RE.search(name)
    if m:
        qty = float(m.group("qty"))
        unit = m.group("unit").lower()
        if unit in {"lt", "ltr", "litre", "liter"}:
            unit = "l"
        elif unit in {"gram", "grams"}:
            unit = "g"
        elif unit in {"pk", "pack"}:
            unit = "pack"
        return PackSize(qty=qty, unit=unit)
    m2 = _PACK_COUNT_RE.search(name)
    if m2:
        return PackSize(qty=float(m2.group("n")), unit="pack")
    return None


def parse_unit_rate(unit_price: Optional[str]) -> Optional[UnitRate]:
    if not unit_price or not str(unit_price).strip():
        return None
    text = str(unit_price).strip()
    m = _UNIT_PRICE_RE.search(text.replace(",", ""))
    if not m:
        return None
    rate = float(m.group("rate"))
    qty = float(m.group("qty") or 1)
    unit = m.group("unit").lower()
    if unit in {"lt", "l"}:
        family, base = "volume", "l"
    elif unit == "ml":
        family, base = "volume", "l"
        qty = qty / 1000.0
    elif unit == "kg":
        family, base = "mass", "kg"
    elif unit == "g":
        family, base = "mass", "kg"
        qty = qty / 1000.0
    else:
        family, base = "count", "ea"
    if qty <= 0:
        return None
    return UnitRate(rate=rate / qty, family=family, raw=text)


def infer_pack_from_unit(price: Optional[float], unit_rate: Optional[UnitRate]) -> Optional[PackSize]:
    """Back out pack size from shelf $ ÷ unit rate when the name has no size."""
    if price is None or unit_rate is None or unit_rate.rate <= 0:
        return None
    qty = float(price) / unit_rate.rate
    if qty <= 0 or qty > 1000:
        return None
    if unit_rate.family == "volume":
        return PackSize(qty=round(qty, 2), unit="l")
    if unit_rate.family == "mass":
        # Prefer grams for small packs.
        if qty < 1:
            return PackSize(qty=round(qty * 1000), unit="g")
        return PackSize(qty=round(qty, 2), unit="kg")
    if unit_rate.family == "count":
        return PackSize(qty=round(qty), unit="ea")
    return None


def resolve_pack(
    name: Optional[str], price: Optional[float], unit_price: Optional[str]
) -> Tuple[Optional[PackSize], Optional[UnitRate]]:
    unit_rate = parse_unit_rate(unit_price)
    pack = parse_pack_size(name) or infer_pack_from_unit(price, unit_rate)
    return pack, unit_rate


def _target_pack(defn: Dict[str, str]) -> Optional[PackSize]:
    qty_s = (defn.get("target_qty") or "").strip()
    unit = (defn.get("target_unit") or "").strip().lower()
    if not qty_s or not unit:
        return None
    try:
        qty = float(qty_s)
    except ValueError:
        return None
    if unit in {"lt", "ltr", "litre", "liter"}:
        unit = "l"
    return PackSize(qty=qty, unit=unit)


def _pack_distance(pack: Optional[PackSize], target: Optional[PackSize]) -> float:
    if target is None:
        return 0.0
    if pack is None or pack.family != target.family:
        return 999.0
    tb = target.base_qty
    pb = pack.base_qty
    if tb is None or pb is None or tb <= 0:
        return 999.0
    return abs(pb - tb) / tb


def _packs_comparable(a: Optional[PackSize], b: Optional[PackSize]) -> bool:
    if not a or not b or a.family != b.family:
        return False
    ab, bb = a.base_qty, b.base_qty
    if ab is None or bb is None or ab <= 0 or bb <= 0:
        return False
    return abs(ab - bb) / max(ab, bb) <= PACK_TOLERANCE


def pick_kvi_sku(
    skus: Sequence[Dict[str, Any]],
    *,
    include_re: str,
    exclude_re: str = "",
    category_re: str = "",
    prefer_own_brand: bool = True,
    target: Optional[PackSize] = None,
    require_size: bool = False,
) -> Optional[Dict[str, Any]]:
    """Pick one representative priced SKU for a KVI definition."""
    if not include_re:
        return None
    try:
        inc = re.compile(include_re, re.I)
    except re.error:
        return None
    exc = None
    if exclude_re:
        try:
            exc = re.compile(exclude_re, re.I)
        except re.error:
            exc = None
    cat_re = None
    if category_re:
        try:
            cat_re = re.compile(category_re, re.I)
        except re.error:
            cat_re = None

    candidates: List[Dict[str, Any]] = []
    for s in skus:
        name = s.get("name") or ""
        price = s.get("price_now")
        if price is None:
            continue
        try:
            price_f = float(price)
        except (TypeError, ValueError):
            continue
        if not inc.search(name):
            continue
        if exc and exc.search(name):
            continue
        pack, unit_rate = resolve_pack(name, price_f, s.get("unit_price"))
        if require_size and target is not None and pack is None:
            continue
        if require_size and target is not None and _pack_distance(pack, target) > PACK_TOLERANCE:
            continue
        cat = str(s.get("category") or s.get("unified_category") or "")
        candidates.append(
            {
                **s,
                "_cat_ok": bool(cat_re.search(cat)) if cat_re else True,
                "_price_f": price_f,
                "_pack": pack,
                "_unit_rate": unit_rate,
                "_size_dist": _pack_distance(pack, target),
            }
        )

    if not candidates and require_size and target is not None:
        # Soften: allow any sized candidate in family, still prefer closest.
        return pick_kvi_sku(
            skus,
            include_re=include_re,
            exclude_re=exclude_re,
            category_re=category_re,
            prefer_own_brand=prefer_own_brand,
            target=target,
            require_size=False,
        )

    if not candidates:
        return None

    in_cat = [c for c in candidates if c.get("_cat_ok")]
    pool = in_cat or candidates

    def sort_key(s: Dict[str, Any]) -> Tuple:
        own = _is_own_brand(s.get("brand") or s.get("clean_brand"), s.get("name"))
        return (
            float(s.get("_size_dist") or 999.0),
            0 if (prefer_own_brand and own) else 1,
            0 if own else 1,
            0 if s.get("_unit_rate") else 1,
            float(s["_price_f"]),
            len(str(s.get("name") or "")),
        )

    pool.sort(key=sort_key)
    return pool[0]


def _compare_pair(
    c: Optional[Dict[str, Any]],
    w: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return cheaper / gap / comparability using unit rate when possible."""
    if c and not w:
        return {
            "cheaper": "coles_only",
            "gap_pct_coles_vs_ww": None,
            "comparable": False,
            "compare_basis": None,
            "incomparable_reason": "Woolworths match not found yet",
        }
    if w and not c:
        return {
            "cheaper": "ww_only",
            "gap_pct_coles_vs_ww": None,
            "comparable": False,
            "compare_basis": None,
            "incomparable_reason": "Coles match not found yet",
        }
    if not c or not w:
        return {
            "cheaper": None,
            "gap_pct_coles_vs_ww": None,
            "comparable": False,
            "compare_basis": None,
            "incomparable_reason": "No priced match on either side",
        }

    cu: Optional[UnitRate] = c.get("_unit_rate")
    wu: Optional[UnitRate] = w.get("_unit_rate")
    cp = float(c["_price_f"])
    wp = float(w["_price_f"])
    c_pack: Optional[PackSize] = c.get("_pack")
    w_pack: Optional[PackSize] = w.get("_pack")

    # 1) Same unit-price family → compare $/base
    if cu and wu and cu.family == wu.family and wu.rate > 0:
        gap = round(100.0 * (cu.rate - wu.rate) / wu.rate, 1)
        if abs(cu.rate - wu.rate) / wu.rate <= UNIT_TIE_EPS:
            cheaper = "tie"
        else:
            cheaper = "Coles" if cu.rate < wu.rate else "Woolworths"
        return {
            "cheaper": cheaper,
            "gap_pct_coles_vs_ww": gap,
            "comparable": True,
            "compare_basis": "unit_price",
            "incomparable_reason": None,
        }

    # 2) Similar pack sizes → compare shelf price (or normalise by qty)
    if _packs_comparable(c_pack, w_pack):
        assert c_pack and w_pack and c_pack.base_qty and w_pack.base_qty
        # Normalise to $/base so 1.8L vs 2L is fairer than raw shelf $.
        c_norm = cp / c_pack.base_qty
        w_norm = wp / w_pack.base_qty
        gap = round(100.0 * (c_norm - w_norm) / w_norm, 1) if w_norm else None
        if gap is None:
            cheaper = None
        elif abs(c_norm - w_norm) / w_norm <= UNIT_TIE_EPS:
            cheaper = "tie"
        else:
            cheaper = "Coles" if c_norm < w_norm else "Woolworths"
        return {
            "cheaper": cheaper,
            "gap_pct_coles_vs_ww": gap,
            "comparable": True,
            "compare_basis": "similar_pack",
            "incomparable_reason": None,
        }

    # 3) Different packs / missing size — do not invent a winner
    reason_bits = []
    if c_pack and w_pack:
        reason_bits.append(f"Pack sizes differ ({c_pack.label()} vs {w_pack.label()})")
    elif c_pack or w_pack:
        reason_bits.append("Pack size missing on one side")
    else:
        reason_bits.append("Pack sizes not clear from product names")
    if not (cu and wu and cu.family == wu.family):
        reason_bits.append("no matching unit price to compare")
    return {
        "cheaper": "not_comparable",
        "gap_pct_coles_vs_ww": None,
        "comparable": False,
        "compare_basis": None,
        "incomparable_reason": "; ".join(reason_bits),
    }


def build_kvi_scoreboard(
    sku_rows: Sequence[Dict[str, Any]],
    defs: Optional[Sequence[Dict[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Compare Coles vs WW representatives for each KVI."""
    defs = list(defs) if defs is not None else load_kvi_defs()
    coles = [s for s in sku_rows if s.get("retailer") == "Coles"]
    ww = [s for s in sku_rows if s.get("retailer") == "Woolworths"]

    out: List[Dict[str, Any]] = []
    for d in defs:
        prefer = str(d.get("prefer_own_brand", "true")).lower() in {"1", "true", "yes", "y"}
        require = str(d.get("require_size", "true")).lower() in {"1", "true", "yes", "y"}
        target = _target_pack(d)
        kwargs = dict(
            include_re=d.get("include_re", ""),
            exclude_re=d.get("exclude_re", ""),
            category_re=d.get("category_re", ""),
            prefer_own_brand=prefer,
            target=target,
            require_size=require and target is not None,
        )
        c = pick_kvi_sku(coles, **kwargs)
        w = pick_kvi_sku(ww, **kwargs)
        cmp = _compare_pair(c, w)
        out.append(
            {
                "kvi_id": d["kvi_id"],
                "label": d.get("label") or d["kvi_id"],
                "perception_role": d.get("perception_role") or "",
                "notes": d.get("notes") or "",
                "target_pack": target.label() if target else None,
                **cmp,
                "coles": _sku_brief(c) if c else None,
                "ww": _sku_brief(w) if w else None,
            }
        )
    return out


def _sku_brief(s: Dict[str, Any]) -> Dict[str, Any]:
    pack: Optional[PackSize] = s.get("_pack")
    unit_rate: Optional[UnitRate] = s.get("_unit_rate")
    if pack is None or unit_rate is None:
        p2, u2 = resolve_pack(
            s.get("name"),
            float(s["price_now"]) if s.get("price_now") is not None else None,
            s.get("unit_price"),
        )
        pack = pack or p2
        unit_rate = unit_rate or u2
    return {
        "id": int(s["id"]) if s.get("id") is not None else None,
        "name": s.get("name"),
        "brand": s.get("brand") or s.get("clean_brand"),
        "price_now": float(s["price_now"]) if s.get("price_now") is not None else None,
        "is_promo": bool(s.get("is_promo")),
        "category": s.get("category") or s.get("unified_category"),
        "own_brand": _is_own_brand(s.get("brand") or s.get("clean_brand"), s.get("name")),
        "pack_label": pack.label() if pack else None,
        "unit_price": s.get("unit_price"),
        "unit_rate": round(unit_rate.rate, 4) if unit_rate else None,
        "unit_family": unit_rate.family if unit_rate else None,
    }
