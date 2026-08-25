"""Bilingual Coles ↔ Woolworths category glossary for store CI copy."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from lake.io import REF_ROOT

logger = logging.getLogger("hybrid_scraper.lake.category_glossary")

GLOSSARY_CSV = REF_ROOT / "category_glossary.csv"
SUBCATEGORY_GLOSSARY_CSV = REF_ROOT / "subcategory_glossary.csv"

# Woolworths Iris L0 names that roll into a shared glossary department (many-to-one).
WW_GOLD_TO_SHARED: Dict[str, str] = {
    "Deli": "Meat Seafood & Deli",
    "Poultry, Meat & Seafood": "Meat Seafood & Deli",
    "Fruit & Veg": "Fruit & Vegetables",
    "Beer, Wine & Spirits": "Liquor",
    "Snacks & Confectionery": "Pantry",
    "Health & Wellness": "Personal Care",
    "Beauty": "Personal Care",
}


def load_glossary(path: Path = GLOSSARY_CSV) -> List[Dict[str, str]]:
    if not path.exists():
        logger.warning("glossary missing path=%s", path)
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows.sort(key=lambda r: int(r.get("priority") or 999))
    logger.info("glossary loaded path=%s rows=%d", path, len(rows))
    return rows


def glossary_by_ww_label(rows: Optional[List[Dict[str, str]]] = None) -> Dict[str, Dict[str, str]]:
    """One entry per shared/WW comparison label (first slug wins for coles alias list)."""
    rows = rows if rows is not None else load_glossary()
    by_label: Dict[str, Dict[str, str]] = {}
    coles_aliases: Dict[str, List[str]] = {}
    for row in rows:
        shared = (row.get("shared_label") or row.get("ww_label") or "").strip()
        ww = (row.get("ww_label") or shared).strip()
        coles_label = (row.get("coles_label") or row.get("coles_slug") or "").strip()
        slug = (row.get("coles_slug") or "").strip()
        if not shared:
            continue
        coles_aliases.setdefault(shared, [])
        if coles_label and coles_label not in coles_aliases[shared]:
            coles_aliases[shared].append(coles_label)
        if shared not in by_label:
            by_label[shared] = {
                "shared_label": shared,
                "ww_label": ww,
                "coles_label": coles_label,
                "coles_slug": slug,
                "notes": (row.get("notes") or "").strip(),
            }
    for shared, aliases in coles_aliases.items():
        if shared in by_label:
            by_label[shared]["coles_aliases"] = ", ".join(aliases)
    return by_label


def bilingual_blurb(shared_label: str, glossary: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    g = (glossary or glossary_by_ww_label()).get(shared_label) or {}
    coles = g.get("coles_aliases") or g.get("coles_label") or "—"
    ww = g.get("ww_label") or shared_label
    return f"Coles: {coles} · Woolworths: {ww}"


def build_gloss_index(rows: Optional[List[Dict[str, str]]] = None) -> Dict[str, Dict[str, str]]:
    """Lookup shared glossary row by shared label, WW label, or Coles alias."""
    rows = rows if rows is not None else load_glossary()
    gloss = glossary_by_ww_label(rows)
    for g in list(gloss.values()):
        gloss[g["shared_label"]] = g
        ww = (g.get("ww_label") or "").strip()
        if ww:
            gloss[ww] = g
        for alias in (g.get("coles_aliases") or "").split(", "):
            alias = alias.strip()
            if alias:
                gloss[alias] = g
    return gloss


def shared_label_for_gold(
    cat: str,
    gloss: Optional[Dict[str, Dict[str, str]]] = None,
) -> str:
    """Map a gold ``unified_category`` to the shared store-CI department label."""
    mapped = WW_GOLD_TO_SHARED.get(cat)
    if mapped:
        return mapped
    g = (gloss or build_gloss_index()).get(cat) or {}
    return (g.get("shared_label") or cat).strip() or cat


def rollup_gold_by_shared(
    gold_categories: Iterable[str],
    rows: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, List[str]]:
    """Invert gold categories into shared_label → [gold keys] for export rollups."""
    gloss = build_gloss_index(rows)
    by_shared: Dict[str, List[str]] = {}
    for gc in gold_categories:
        cat = (gc or "").strip()
        if not cat:
            continue
        shared = shared_label_for_gold(cat, gloss)
        keys = by_shared.setdefault(shared, [])
        if cat not in keys:
            keys.append(cat)
    for shared in sorted(by_shared):
        by_shared[shared].sort()
    return by_shared


def expected_gold_keys_for_shared(shared_label: str, rows: Optional[List[Dict[str, str]]] = None) -> List[str]:
    """Gold keys that should attach to a shared department (for eval / QA)."""
    gloss = build_gloss_index(rows)
    keys = [shared_label]
    g = gloss.get(shared_label) or {}
    ww = (g.get("ww_label") or "").strip()
    if ww and ww not in keys:
        keys.append(ww)
    for gold_key, mapped in WW_GOLD_TO_SHARED.items():
        if mapped == shared_label and gold_key not in keys:
            keys.append(gold_key)
    return keys


def load_subcategory_glossary(path: Path = SUBCATEGORY_GLOSSARY_CSV) -> List[Dict[str, str]]:
    if not path.exists():
        logger.warning("subcategory glossary missing path=%s", path)
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows.sort(key=lambda r: int(r.get("priority") or 999))
    logger.info("subcategory glossary loaded path=%s rows=%d", path, len(rows))
    return rows


def subcategory_alias_map(
    rows: Optional[List[Dict[str, str]]] = None,
) -> Dict[tuple[str, str], str]:
    """(shared_parent, alias_label) → shared_subcategory."""
    rows = rows if rows is not None else load_subcategory_glossary()
    out: Dict[tuple[str, str], str] = {}
    for row in rows:
        parent = (row.get("shared_parent") or "").strip()
        alias = (row.get("alias_label") or "").strip()
        shared_sub = (row.get("shared_subcategory") or alias).strip()
        if parent and alias and shared_sub:
            out[(parent, alias)] = shared_sub
    return out


def subcategory_cross_parent_map(
    rows: Optional[List[Dict[str, str]]] = None,
) -> Dict[tuple[str, str], tuple[str, str]]:
    """(source_parent, alias_label) → (target_parent, shared_subcategory) when target_parent set."""
    rows = rows if rows is not None else load_subcategory_glossary()
    out: Dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        parent = (row.get("shared_parent") or "").strip()
        alias = (row.get("alias_label") or "").strip()
        shared_sub = (row.get("shared_subcategory") or alias).strip()
        target = (row.get("target_parent") or "").strip()
        if parent and alias and shared_sub and target:
            out[(parent, alias)] = (target, shared_sub)
    return out


def shared_subcategory_for(
    shared_parent: str,
    subcategory: Optional[str],
    alias_map: Optional[Dict[tuple[str, str], str]] = None,
) -> Optional[str]:
    """Canonical subcategory label within a shared department."""
    sub = (subcategory or "").strip()
    if not sub:
        return None
    parent = (shared_parent or "").strip()
    amap = alias_map if alias_map is not None else subcategory_alias_map()
    if parent:
        mapped = amap.get((parent, sub))
        if mapped:
            return mapped
    return sub


def resolve_shared_l1(
    shared_parent: str,
    subcategory: Optional[str],
    *,
    alias_map: Optional[Dict[tuple[str, str], str]] = None,
    cross_parent: Optional[Dict[tuple[str, str], tuple[str, str]]] = None,
) -> Optional[tuple[str, str]]:
    """Resolve (shared_parent, subcategory) → canonical (parent, sub), including cross-parent rollups."""
    parent = (shared_parent or "").strip()
    sub = (subcategory or "").strip()
    if not parent or not sub:
        return None
    amap = alias_map if alias_map is not None else subcategory_alias_map()
    cmap = cross_parent if cross_parent is not None else subcategory_cross_parent_map()
    # Cross-parent first (uses source parent + raw or aliased label).
    if (parent, sub) in cmap:
        return cmap[(parent, sub)]
    canon_sub = shared_subcategory_for(parent, sub, amap) or sub
    if (parent, canon_sub) in cmap:
        return cmap[(parent, canon_sub)]
    return parent, canon_sub
