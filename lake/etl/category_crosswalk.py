"""Coles slug → Woolworths L0 recommendations from matched SKU votes.

Manual overrides in lake/ref/category_crosswalk_overrides.csv always win.
Recommended mappings use only literal Woolworths category strings observed
on matched Iris products — never invented labels.
"""

from __future__ import annotations

import csv
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from lake.io import REF_ROOT

logger = logging.getLogger("hybrid_scraper.lake.category_crosswalk")

RECOMMENDED_CSV = REF_ROOT / "category_crosswalk_recommended.csv"
OVERRIDES_CSV = REF_ROOT / "category_crosswalk_overrides.csv"

# Accept a slug→WW mapping when support is strong enough.
MIN_SUPPORT_ABSOLUTE = 3
MIN_SUPPORT_WEAK = 2
MIN_VOTE_SHARE = 0.60

_FIELDNAMES = [
    "coles_category_slug",
    "woolworths_category",
    "woolworths_subcategory",
    "match_support",
    "vote_share",
    "source",
    "notes",
]

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_AFFINITY_STOP = frozenset({"and", "the", "or", "a", "an", "of", "with", "for"})


def _tokens(text: str) -> set[str]:
    return {t for t in _NON_ALNUM.sub(" ", (text or "").lower()).split() if t and t not in _AFFINITY_STOP}


def _slug_affinity(slug: str, ww_category: str) -> float:
    """How well a Coles slug aligns with a WW category name (Jaccard on tokens)."""
    a = _tokens(slug.replace("-", " "))
    b = _tokens(ww_category)
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def _slugs_to_vote(coles_categories: Sequence[str], ww_l0: str) -> List[str]:
    """Pick Coles slug(s) that best match WW L0 — avoids multi-tag vote pollution.

    Example: milk tagged bakery+dairy+drinks+pantry should vote dairy-eggs-fridge
    toward Dairy, Eggs & Fridge, not bakery.
    """
    slugs = [str(c).strip().lower().replace(" ", "-") for c in coles_categories if c]
    slugs = [s for s in slugs if s]
    if not slugs:
        return []
    if len(slugs) == 1:
        return slugs
    scored = [(s, _slug_affinity(s, ww_l0)) for s in slugs]
    best = max(score for _, score in scored)
    if best <= 0:
        # No string affinity — keep all votes (thin evidence better than none)
        return slugs
    return [s for s, score in scored if score == best]


def recommend_from_matches(matches: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Plurality vote: best-aligned Coles slug on each matched SKU → WW L0."""
    votes: Dict[str, Counter] = defaultdict(Counter)
    sub_votes: Dict[str, Dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))

    for match in matches:
        ww_l0 = (match.get("ww_l0") or "").strip()
        if not ww_l0:
            continue
        ww_l1 = (match.get("ww_l1") or "").strip() or None
        for slug in _slugs_to_vote(match.get("coles_categories") or [], ww_l0):
            votes[slug][ww_l0] += 1
            if ww_l1:
                sub_votes[slug][ww_l0][ww_l1] += 1

    rows: List[Dict[str, str]] = []
    for slug, counter in sorted(votes.items()):
        total = sum(counter.values())
        ww_l0, support = counter.most_common(1)[0]
        share = support / total if total else 0.0
        ok = support >= MIN_SUPPORT_ABSOLUTE or (
            support >= MIN_SUPPORT_WEAK and share >= MIN_VOTE_SHARE
        )
        if not ok:
            logger.info(
                "crosswalk skip slug=%s best=%s support=%d share=%.2f total=%d",
                slug,
                ww_l0,
                support,
                share,
                total,
            )
            continue
        sub = ""
        if ww_l0 in sub_votes.get(slug, {}):
            sub_counter = sub_votes[slug][ww_l0]
            if sub_counter:
                sub = sub_counter.most_common(1)[0][0]
        rows.append(
            {
                "coles_category_slug": slug,
                "woolworths_category": ww_l0,
                "woolworths_subcategory": sub,
                "match_support": str(support),
                "vote_share": f"{share:.4f}",
                "source": "recommended",
                "notes": f"votes={total};candidates={dict(counter)}",
            }
        )
    logger.info("crosswalk recommended rows=%d slugs_seen=%d", len(rows), len(votes))
    return rows


def write_recommended_csv(rows: Sequence[Dict[str, str]], path: Path = RECOMMENDED_CSV) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in _FIELDNAMES})
    logger.info("crosswalk recommended wrote path=%s rows=%d", path, len(rows))
    return path


def ensure_overrides_stub(path: Path = OVERRIDES_CSV) -> Path:
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDNAMES)
        writer.writeheader()
    logger.info("crosswalk overrides stub created path=%s", path)
    return path


def _read_crosswalk_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    out: List[Dict[str, str]] = []
    for row in rows:
        slug = (row.get("coles_category_slug") or "").strip().lower()
        ww = (row.get("woolworths_category") or "").strip()
        if not slug or not ww:
            continue
        out.append(
            {
                "coles_category_slug": slug,
                "woolworths_category": ww,
                "woolworths_subcategory": (row.get("woolworths_subcategory") or "").strip(),
                "match_support": (row.get("match_support") or "").strip(),
                "vote_share": (row.get("vote_share") or "").strip(),
                "source": (row.get("source") or "").strip() or path.stem,
                "notes": (row.get("notes") or "").strip(),
                "priority": str(10 + len(out)),  # stable order for first-match
            }
        )
    return out


def load_effective_crosswalk(
    recommended_path: Path = RECOMMENDED_CSV,
    overrides_path: Path = OVERRIDES_CSV,
) -> List[Dict[str, str]]:
    """Overrides replace recommendations by coles_category_slug."""
    ensure_overrides_stub(overrides_path)
    recommended = _read_crosswalk_csv(recommended_path)
    overrides = _read_crosswalk_csv(overrides_path)
    by_slug: Dict[str, Dict[str, str]] = {}
    for row in recommended:
        by_slug[row["coles_category_slug"]] = {**row, "source": row.get("source") or "recommended"}
    for row in overrides:
        by_slug[row["coles_category_slug"]] = {
            **row,
            "source": "override",
        }
    # Priority: lower number = tried first in unify (same as old crosswalk)
    effective = list(by_slug.values())
    effective.sort(key=lambda r: int(r.get("priority") or 999))
    logger.info(
        "crosswalk effective rows=%d recommended=%d overrides=%d path_rec=%s path_ovr=%s",
        len(effective),
        len(recommended),
        len(overrides),
        recommended_path,
        overrides_path,
    )
    return effective


def write_crosswalk_used(rows: Sequence[Dict[str, str]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["priority", *_FIELDNAMES]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for i, row in enumerate(rows):
            writer.writerow(
                {
                    "priority": row.get("priority") or str(10 + i),
                    **{k: row.get(k, "") for k in _FIELDNAMES},
                }
            )
    logger.info("crosswalk used wrote path=%s rows=%d", path, len(rows))
    return path


def unify_coles_category(
    native_cats: Iterable[str],
    crosswalk: Sequence[Dict[str, str]],
) -> tuple[str, Optional[str], str]:
    native = [c.strip().lower().replace(" ", "-") for c in native_cats if c]
    for rule in crosswalk:
        slug = (rule.get("coles_category_slug") or "").strip().lower()
        if slug and slug in native:
            return (
                rule["woolworths_category"],
                rule.get("woolworths_subcategory") or None,
                slug,
            )
    return "Unmapped", None, native[0] if native else ""
