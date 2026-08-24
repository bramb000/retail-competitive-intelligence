"""Bilingual Coles ↔ Woolworths category glossary for store CI copy."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional

from lake.io import REF_ROOT

logger = logging.getLogger("hybrid_scraper.lake.category_glossary")

GLOSSARY_CSV = REF_ROOT / "category_glossary.csv"


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
