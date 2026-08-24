"""Fuzzy brand+name matcher: Coles bronze ↔ Woolworths Iris PDP cards.

No barcodes in scrape data — match within brand using token Jaccard on
normalised product names. Everyday Market search pages are intentionally
not used (they lack grocery department breadcrumbs).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lake.io import iter_jsonl

logger = logging.getLogger("hybrid_scraper.lake.sku_matcher")

DEFAULT_MATCH_THRESHOLD = 0.72

_PACK_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ml|l|g|kg|pk|pack|packs|x)\b"
    r"|\b\d+\s*x\s*\d+(?:\.\d+)?\s*(?:ml|l|g|kg)?\b"
    r"|\bx\s*\d+\b"
    r"|\b\d+\s*pack\b",
    re.I,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_STOP = frozenset(
    {
        "the",
        "and",
        "or",
        "a",
        "an",
        "of",
        "with",
        "in",
        "for",
        "to",
        "each",
        "pack",
        "packs",
        "pk",
    }
)


def normalize_brand(value: Optional[str]) -> str:
    text = (value or "").lower().replace("'", "").replace("'", "")
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def name_token_list(value: Optional[str]) -> List[str]:
    text = (value or "").lower().replace("'", "").replace("'", "")
    text = _PACK_RE.sub(" ", text)
    text = _NON_ALNUM.sub(" ", text)
    return [t for t in text.split() if t and t not in _STOP and not t.isdigit()]


def name_tokens(value: Optional[str]) -> Set[str]:
    return set(name_token_list(value))


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / float(len(a | b))


def infer_brand_from_name(name: Optional[str], brand_vocab: Sequence[str]) -> str:
    """Longest Coles brand that is a prefix of the normalised name tokens."""
    tokens = name_token_list(name)
    if not tokens:
        return ""
    best = ""
    for brand in brand_vocab:
        btoks = brand.split()
        if not btoks:
            continue
        if tokens[: len(btoks)] == btoks and len(brand) > len(best):
            best = brand
    return best


def _coles_items_from_bronze(bronze_dir) -> List[Dict[str, Any]]:
    from pathlib import Path

    items: List[Dict[str, Any]] = []
    path = Path(bronze_dir) / "products_list.jsonl"
    for rec in iter_jsonl(path):
        for item in rec.get("results") or []:
            if isinstance(item, dict):
                items.append(item)
    return items


def load_coles_match_rows(
    bronze_dir,
    category_sets: Dict[int, List[str]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in _coles_items_from_bronze(bronze_dir):
        sku = item.get("id")
        try:
            rpid = int(sku)
        except (TypeError, ValueError):
            continue
        name = item.get("name") or ""
        brand = normalize_brand(item.get("brand"))
        native = category_sets.get(rpid) or []
        if not native and item.get("category"):
            native = [str(item.get("category"))]
        native = [c.strip().lower().replace(" ", "-") for c in native if c]
        rows.append(
            {
                "retailer_product_id": rpid,
                "name": name,
                "brand": brand,
                "tokens": name_tokens(f"{item.get('brand') or ''} {name}"),
                "categories": native,
            }
        )
    logger.info("sku_matcher coles rows=%d", len(rows))
    return rows


def load_ww_iris_match_rows(bronze_dir) -> List[Dict[str, Any]]:
    from pathlib import Path

    from hybrid_scraper.woolworths_aisle_enrichment import product_summary

    rows: List[Dict[str, Any]] = []
    path = Path(bronze_dir) / "product_details.jsonl"
    for rec in iter_jsonl(path):
        card = rec.get("card")
        if not isinstance(card, dict) or not card.get("productId"):
            continue
        summary = product_summary(card)
        pid = summary.get("product_id")
        try:
            rpid = int(pid)
        except (TypeError, ValueError):
            continue
        name = summary.get("name") or card.get("name") or ""
        brand_raw = card.get("brandName") or card.get("brand") or ""
        brand = normalize_brand(brand_raw)
        breadcrumb = summary.get("categories") or []
        # Skip Specials as L0 — use next level when present
        if breadcrumb and str(breadcrumb[0]).lower() == "specials":
            breadcrumb = breadcrumb[1:]
        ww_l0 = breadcrumb[0] if breadcrumb else None
        ww_l1 = breadcrumb[1] if len(breadcrumb) > 1 else None
        if not ww_l0:
            continue
        rows.append(
            {
                "retailer_product_id": rpid,
                "name": name,
                "brand": brand,
                "tokens": name_tokens(f"{brand_raw} {name}"),
                "ww_l0": ww_l0,
                "ww_l1": ww_l1,
            }
        )
    logger.info("sku_matcher ww iris rows=%d path=%s", len(rows), path)
    return rows


def match_skus(
    coles_rows: Sequence[Dict[str, Any]],
    ww_rows: Sequence[Dict[str, Any]],
    *,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> List[Dict[str, Any]]:
    brand_vocab = sorted(
        {r["brand"] for r in coles_rows if r.get("brand")},
        key=len,
        reverse=True,
    )
    by_brand: Dict[str, List[Dict[str, Any]]] = {}
    for row in coles_rows:
        brand = row.get("brand") or ""
        if brand:
            by_brand.setdefault(brand, []).append(row)

    matches: List[Dict[str, Any]] = []
    for ww in ww_rows:
        brand = ww.get("brand") or ""
        if not brand:
            brand = infer_brand_from_name(ww.get("name"), brand_vocab)
        if not brand:
            continue
        candidates = by_brand.get(brand) or []
        if not candidates:
            continue
        wt = ww.get("tokens") or set()
        best: Optional[Dict[str, Any]] = None
        best_score = 0.0
        for coles in candidates:
            score = jaccard(wt, coles.get("tokens") or set())
            if score > best_score:
                best_score = score
                best = coles
        if best is None or best_score < threshold:
            continue
        matches.append(
            {
                "coles_id": best["retailer_product_id"],
                "ww_id": ww["retailer_product_id"],
                "coles_name": best["name"],
                "ww_name": ww["name"],
                "brand": brand,
                "score": round(best_score, 4),
                "coles_categories": list(best.get("categories") or []),
                "ww_l0": ww["ww_l0"],
                "ww_l1": ww.get("ww_l1"),
            }
        )

    matches.sort(key=lambda m: (-m["score"], m["coles_id"], m["ww_id"]))
    # One Coles SKU → best WW only; one WW → best Coles only (greedy by score)
    used_coles: Set[int] = set()
    used_ww: Set[int] = set()
    deduped: List[Dict[str, Any]] = []
    for m in matches:
        if m["coles_id"] in used_coles or m["ww_id"] in used_ww:
            continue
        used_coles.add(m["coles_id"])
        used_ww.add(m["ww_id"])
        deduped.append(m)

    sample = deduped[:8]
    logger.info(
        "sku_matcher done candidates=%d accepted=%d threshold=%.2f sample=%s",
        len(matches),
        len(deduped),
        threshold,
        [
            {
                "score": s["score"],
                "coles": (s["coles_name"] or "")[:40],
                "ww": (s["ww_name"] or "")[:40],
                "map": f"{(s['coles_categories'] or [''])[0]}→{s['ww_l0']}",
            }
            for s in sample
        ],
    )
    return deduped


def build_coles_subcategory_lookup(
    coles_rows: Sequence[Dict[str, Any]],
    ww_rows: Sequence[Dict[str, Any]],
    matches: Sequence[Dict[str, Any]],
    slug_to_l0: Dict[str, str],
) -> Dict[int, Dict[str, Any]]:
    """Map Coles SKUs onto WW-style subcategories.

    Strategy:
    1. Direct fuzzy matches inherit the matched WW L0/L1.
    2. Unmatched Coles SKUs infer L1 from matched Coles exemplars sharing the
       same slug+L0.
    3. If that is too sparse, fall back to WW exemplars in the same L0.

    We stay conservative: no L1 if scores are weak or ambiguous.
    """
    by_coles_id = {int(r["retailer_product_id"]): r for r in coles_rows if r.get("retailer_product_id") is not None}
    direct: Dict[int, Dict[str, Any]] = {}
    slug_examples: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    ww_examples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for ww in ww_rows:
        l0 = (ww.get("ww_l0") or "").strip()
        l1 = (ww.get("ww_l1") or "").strip()
        if l0 and l1:
            ww_examples[l0].append({"brand": ww.get("brand") or "", "tokens": ww.get("tokens") or set(), "l1": l1})

    for match in matches:
        coles_id = match.get("coles_id")
        ww_l0 = (match.get("ww_l0") or "").strip()
        ww_l1 = (match.get("ww_l1") or "").strip()
        if coles_id is None or not ww_l0 or not ww_l1:
            continue
        try:
            coles_id_i = int(coles_id)
        except (TypeError, ValueError):
            continue
        coles = by_coles_id.get(coles_id_i)
        if not coles:
            continue
        direct[coles_id_i] = {
            "unified_category": ww_l0,
            "unified_subcategory": ww_l1,
            "subcategory_source": "direct_match",
            "subcategory_confidence": float(match.get("score") or 1.0),
        }
        for slug in coles.get("categories") or []:
            slug_text = str(slug).strip().lower().replace(" ", "-")
            if slug_to_l0.get(slug_text) != ww_l0:
                continue
            slug_examples[(slug_text, ww_l0)].append(
                {
                    "brand": coles.get("brand") or "",
                    "tokens": coles.get("tokens") or set(),
                    "l1": ww_l1,
                    "weight": float(match.get("score") or 1.0),
                }
            )

    def _infer_from_examples(
        tokens: Set[str],
        brand: str,
        examples: Sequence[Dict[str, Any]],
        *,
        min_score: float,
        min_confidence: float,
    ) -> Optional[Tuple[str, float]]:
        if not examples:
            return None
        brand_examples = [e for e in examples if (e.get("brand") or "") == brand]
        pool = brand_examples or list(examples)
        weighted: Dict[str, float] = defaultdict(float)
        top_by_label: Dict[str, float] = defaultdict(float)
        for ex in pool:
            score = jaccard(tokens, ex.get("tokens") or set())
            if score < min_score:
                continue
            label = ex["l1"]
            weight = float(ex.get("weight") or 1.0)
            weighted[label] += score * weight
            top_by_label[label] = max(top_by_label[label], score)
        if not weighted:
            return None
        ranked = sorted(weighted.items(), key=lambda item: (item[1], top_by_label[item[0]]), reverse=True)
        best_label, best_weight = ranked[0]
        second_weight = ranked[1][1] if len(ranked) > 1 else 0.0
        total_weight = sum(weighted.values())
        confidence = best_weight / total_weight if total_weight else 0.0
        if confidence < min_confidence:
            return None
        if second_weight and best_weight - second_weight < 0.08:
            return None
        return best_label, round(confidence, 3)

    inferred: Dict[int, Dict[str, Any]] = dict(direct)
    for coles_id, row in by_coles_id.items():
        if coles_id in inferred:
            continue
        native = [str(c).strip().lower().replace(" ", "-") for c in (row.get("categories") or []) if c]
        l0 = None
        for slug in native:
            l0 = slug_to_l0.get(slug)
            if l0:
                break
        if not l0:
            continue
        best = None
        for slug in native:
            result = _infer_from_examples(
                row.get("tokens") or set(),
                row.get("brand") or "",
                slug_examples.get((slug, l0), []),
                min_score=0.34,
                min_confidence=0.58,
            )
            if result:
                best = result
                break
        if best is None:
            best = _infer_from_examples(
                row.get("tokens") or set(),
                row.get("brand") or "",
                ww_examples.get(l0, []),
                min_score=0.45,
                min_confidence=0.62,
            )
        if best is None:
            continue
        inferred[coles_id] = {
            "unified_category": l0,
            "unified_subcategory": best[0],
            "subcategory_source": "inferred_match",
            "subcategory_confidence": best[1],
        }

    logger.info(
        "coles subcategory lookup direct=%d inferred=%d total=%d",
        len(direct),
        max(0, len(inferred) - len(direct)),
        len(inferred),
    )
    return inferred
