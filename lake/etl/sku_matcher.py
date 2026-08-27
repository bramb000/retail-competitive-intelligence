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

from lake.etl.category_glossary import shared_label_for_gold
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

# Shared-parent keyword → WW-style L1 when fuzzy match is too sparse.
_KEYWORD_L1: Dict[str, List[Tuple[Tuple[str, ...], str]]] = {
    "Meat Seafood & Deli": [
        (("chicken", "turkey", "duck", "poultry", "drumstick", "tenderloin", "maryland"), "Poultry"),
        (
            (
                "prawn",
                "shrimp",
                "salmon",
                "barramundi",
                "seafood",
                "crab",
                "oyster",
                "mussel",
                "fish fillet",
                "tuna steak",
            ),
            "Seafood",
        ),
        (("ham", "bacon", "salami", "prosciutto", "pastrami", "frankfurt", "kransky"), "Ham, Bacon & Smallgoods"),
        (("beef", "lamb", "pork", "veal", "mince", "steak", "sausage", "schnitzel", "rump", "scotch"), "Meat"),
        (("cheese", "camembert", "brie", "cheddar", "bocconcini", "havarti"), "Cheese"),
    ],
    "Fruit & Vegetables": [
        (("apple", "banana", "orange", "berry", "grape", "mango", "fruit"), "Fruit"),
        (("lettuce", "salad", "spinach", "rocket"), "Salad"),
        (("potato", "carrot", "onion", "broccoli", "vegetable", "tomato", "cucumber", "capsicum"), "Vegetables"),
    ],
    "Pantry": [
        (("chip", "crisp", "corn chip", "doritos", "waves"), "Chips"),
        (
            (
                "chocolate",
                "lolly",
                "candy",
                "confection",
                "mint",
                "peppermint",
                "gum",
                "lozenge",
                "smarties",
                "kitkat",
                "kit kat",
                "wafer",
                "marshmallow",
                "liquorice",
                "licorice",
            ),
            "Confectionery",
        ),
        (("biscuit", "cookie", "cracker"), "Biscuits & Crackers"),
        (("snack", "popcorn", "pretzel", "nut mix", "harvest snap"), "Snacks"),
        (("pasta", "rice", "noodle"), "Pasta, Rice & Grains"),
        (("sauce", "mayo", "ketchup", "mustard"), "Condiments"),
        (("cereal", "muesli", "oat"), "Breakfast & Spreads"),
        (("tea", "coffee", "espresso", "latte"), "Tea & Coffee"),
        (("bake", "flour", "sugar", "cake mix"), "Baking"),
    ],
    "Liquor": [
        (("wine", "champagne", "prosecco"), "Wine"),
        (("beer", "lager", "ale", "cider"), "Beer"),
        (("vodka", "whisky", "whiskey", "gin", "rum", "spirit"), "Spirits"),
    ],
    "International Foods": [
        (("asian", "japanese", "chinese", "korean", "thai", "vietnamese"), "Asian"),
        (("mexican", "latin"), "Mexican & Latin American"),
        (("middle eastern", "hummus", "falafel"), "Middle Eastern"),
        (("indian", "curry paste", "tikka"), "Indian & South Asian"),
    ],
    "Dairy Eggs & Fridge": [
        (("yoghurt", "yogurt"), "Yoghurt"),
        (("milk", "cream"), "Milk"),
        (("butter", "margarine", "egg"), "Eggs, Butter & Margarine"),
        (("cheese",), "Cheese"),
    ],
    "Frozen": [
        (("ice cream", "gelato", "icy pole"), "Ice Cream"),
        (("frozen vegetable",), "Frozen Vegetables"),
        (("frozen fruit",), "Frozen Fruit"),
        (("frozen meal", "ready meal"), "Frozen Meals"),
        (("frozen meat", "frozen chicken"), "Frozen Meat"),
        (("pizza",), "Frozen Pizzas"),
    ],
    "Bakery": [
        (("bread", "loaf", "sourdough", "cake", "muffin", "cupcake", "brownie"), "Packaged Bread & Bakery"),
        (("wrap", "tortilla", "pita"), "Sandwich Ingredients"),
    ],
    "Personal Care": [
        (("shampoo", "conditioner", "hair"), "Hair Care"),
        (("vitamin", "supplement"), "Vitamins"),
        (("toothpaste", "mouthwash", "floss"), "Oral Care"),
        (("deodorant", "body wash", "soap"), "Shower, Bath & Body"),
        (("moisturiser", "moisturizer", "sunscreen", "skincare"), "Skincare & Body"),
    ],
    "Cleaning & Maintenance": [
        (("laundry", "detergent", "fabric softener"), "Laundry"),
        (("dishwasher", "dishwashing", "sponge"), "Kitchen"),
        (("bleach", "disinfectant", "cleaner", "wipes"), "Cleaning Goods"),
        (("battery", "batteries", "energizer", "eveready", "duracell"), "Batteries & Power"),
    ],
    "Pet": [
        (("dog", "puppy"), "Dog & Puppy"),
        (("cat", "kitten"), "Cat & Kitten"),
        (("bird", "fish food"), "Pet"),
    ],
    "Baby": [
        (("formula", "toddler milk"), "Baby Formula & Toddler Milk"),
        (("wipe", "nappy", "diaper"), "Wipes & Changing"),
        (("baby bath", "baby lotion"), "Bath & Skincare"),
    ],
    "Drinks": [
        (("coffee", "tea"), "Tea & Coffee"),
        (("soft drink", "cola", "lemonade", "soda"), "Soft Drinks"),
        (("juice", "cordial"), "Cordials, Juices & Iced Teas"),
        (("water", "sparkling water"), "Water"),
        (("energy drink", "sports drink"), "Sports & Energy Drinks"),
    ],
    "Home & Lifestyle": [
        (("candle", "decor"), "Home Decor & Furniture"),
        (("kitchen", "cookware"), "Kitchen"),
        (("toy", "game"), "Toys & Games"),
        (("battery", "batteries", "energizer", "eveready", "duracell"), "Batteries & Power"),
        (("sport", "fitness", "outdoor"), "Sport, Fitness & Outdoor Activities"),
        (("towel", "bath sheet"), "Bathroom Towels & Accessories"),
        (("phone", "earphone", "headphone", "charger"), "Phones & Accessories"),
        (("sheet", "duvet", "pillowcase", "doona", "manchester", "quilt"), "Manchester & Bedding"),
        (("kettle", "toaster", "air fryer", "appliance"), "Appliances"),
        (("hardware", "screw", "nail pack", "duct tape"), "Hardware"),
    ],
}


def _keyword_subcategory(shared_l0: str, name: str) -> Optional[str]:
    text = (name or "").lower()
    if not text:
        return None
    for needles, label in _KEYWORD_L1.get(shared_l0) or []:
        if any(n in text for n in needles):
            return label
    return None


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
            shared = shared_label_for_gold(l0)
            example = {"brand": ww.get("brand") or "", "tokens": ww.get("tokens") or set(), "l1": l1}
            ww_examples[l0].append(example)
            if shared != l0:
                ww_examples[shared].append(example)

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
        shared_l0 = shared_label_for_gold(ww_l0)
        direct[coles_id_i] = {
            "unified_category": ww_l0,
            "unified_subcategory": ww_l1,
            "subcategory_source": "direct_match",
            "subcategory_confidence": float(match.get("score") or 1.0),
        }
        for slug in coles.get("categories") or []:
            slug_text = str(slug).strip().lower().replace(" ", "-")
            crosswalk_l0 = slug_to_l0.get(slug_text) or ""
            if not crosswalk_l0:
                continue
            if shared_label_for_gold(crosswalk_l0) != shared_l0:
                continue
            example = {
                "brand": coles.get("brand") or "",
                "tokens": coles.get("tokens") or set(),
                "l1": ww_l1,
                "weight": float(match.get("score") or 1.0),
            }
            slug_examples[(slug_text, shared_l0)].append(example)
            # Also key by crosswalk L0 string for callers that look up that way.
            if crosswalk_l0 != shared_l0:
                slug_examples[(slug_text, crosswalk_l0)].append(example)

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
        shared_l0 = shared_label_for_gold(l0)
        best = None
        for slug in native:
            pool = slug_examples.get((slug, shared_l0)) or slug_examples.get((slug, l0)) or []
            result = _infer_from_examples(
                row.get("tokens") or set(),
                row.get("brand") or "",
                pool,
                min_score=0.34,
                min_confidence=0.58,
            )
            if result:
                best = result
                break
        if best is None:
            pool = ww_examples.get(shared_l0) or ww_examples.get(l0) or []
            best = _infer_from_examples(
                row.get("tokens") or set(),
                row.get("brand") or "",
                pool,
                min_score=0.32,
                min_confidence=0.48,
            )
        if best is None:
            # Last resort: name-match against all WW L1 exemplars (cross-department).
            # Catches Coles Pantry tea/coffee that WW files under Drinks, etc.
            all_ww: List[Dict[str, Any]] = []
            for examples in ww_examples.values():
                all_ww.extend(examples)
            best = _infer_from_examples(
                row.get("tokens") or set(),
                row.get("brand") or "",
                all_ww,
                min_score=0.45,
                min_confidence=0.62,
            )
            if best:
                inferred[coles_id] = {
                    "unified_category": l0,
                    "unified_subcategory": best[0],
                    "subcategory_source": "inferred_match_cross_dept",
                    "subcategory_confidence": best[1],
                }
                continue
        if best is None:
            kw = _keyword_subcategory(shared_l0, row.get("name") or "")
            if kw:
                inferred[coles_id] = {
                    "unified_category": l0,
                    "unified_subcategory": kw,
                    "subcategory_source": "keyword_fallback",
                    "subcategory_confidence": 0.5,
                }
            continue
        inferred[coles_id] = {
            "unified_category": l0,
            "unified_subcategory": best[0],
            "subcategory_source": "inferred_match",
            "subcategory_confidence": best[1],
        }

    keyword_n = sum(1 for v in inferred.values() if v.get("subcategory_source") == "keyword_fallback")
    logger.info(
        "coles subcategory lookup direct=%d inferred=%d keyword=%d total=%d",
        len(direct),
        max(0, len(inferred) - len(direct) - keyword_n),
        keyword_n,
        len(inferred),
    )
    return inferred
