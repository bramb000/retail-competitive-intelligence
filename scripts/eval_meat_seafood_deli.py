#!/usr/bin/env python3
"""Evaluate Woolworths meat / seafood / deli coverage across bronze → store-CI.

The common dashboard bug is *not* missing scrape data: WW Iris assigns L0
``Deli`` and ``Poultry, Meat & Seafood``, while the glossary/export rolls Coles
into shared ``Meat Seafood & Deli`` with the same string as ``ww_label`` — a key
that never appears in WW gold. Result: store-CI shows ww_skus=0 for that aisle.

Usage:
  .venv/bin/python scripts/eval_meat_seafood_deli.py
  .venv/bin/python scripts/eval_meat_seafood_deli.py --json lake/eval/meat_seafood_deli.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from lake.etl.category_glossary import (  # noqa: E402
    WW_GOLD_TO_SHARED,
    expected_gold_keys_for_shared,
    glossary_by_ww_label,
    load_glossary,
)
from lake.io import iter_jsonl, latest_bronze_dir  # noqa: E402

WW_STORE = "1213"
COLES_STORE = "791"
SHARED_DEPT = "Meat Seafood & Deli"
WW_NATIVE_L0 = ("Deli", "Poultry, Meat & Seafood")

GOLD_DB = REPO / "lake" / "gold" / "ashfield_compare.duckdb"
STORE_CI = REPO / "apps" / "store-ci" / "public" / "data" / "store_ci.json"
Venn_CSV = REPO / "lake" / "gold" / "exports" / "category_venn.csv"


def _latest_silver_dir() -> Optional[Path]:
    roots = sorted((REPO / "lake" / "silver").glob("*"), reverse=True)
    return roots[0] if roots else None


def _count_silver_ww(silver_dir: Path) -> Dict[str, Any]:
    path = silver_dir / "woolworths" / "products.jsonl"
    if not path.exists():
        return {"error": f"missing {path}"}
    by_cat: Counter[str] = Counter()
    by_loc: Counter[str] = Counter()
    counter_aisle = 0
    for row in iter_jsonl(path):
        cat = str(row.get("unified_category") or "Unmapped")
        by_cat[cat] += 1
        lc = str(row.get("location_class") or "")
        by_loc[lc] += 1
        if cat in WW_NATIVE_L0 and str(row.get("aisle_number") or "").lower() == "deli department":
            counter_aisle += 1
    meat = by_cat.get("Poultry, Meat & Seafood", 0)
    deli = by_cat.get("Deli", 0)
    return {
        "stamp": silver_dir.name,
        "meat_l0": meat,
        "deli_l0": deli,
        "combined": meat + deli,
        "location_class": dict(by_loc),
        "counter_deli_department_skus": counter_aisle,
        "top_categories": by_cat.most_common(8),
    }


def _sample_bronze_categories() -> Dict[str, Any]:
    bronze = latest_bronze_dir("woolworths", WW_STORE)
    if not bronze:
        return {"error": "no bronze dir"}
    pdp = bronze / "product_details.jsonl"
    if not pdp.exists():
        return {"error": f"missing {pdp}", "run_id": bronze.name}
    meat_hits = 0
    deli_hits = 0
    samples: List[Dict[str, Any]] = []
    for row in iter_jsonl(pdp):
        card = row.get("card") or {}
        summary = row.get("summary") or {}
        cats = summary.get("categories") or []
        if not cats and isinstance(card.get("categories"), list):
            cats = [c.get("name") for c in card["categories"] if isinstance(c, dict) and c.get("name")]
        l0 = cats[0] if cats else summary.get("category")
        if l0 == "Poultry, Meat & Seafood":
            meat_hits += 1
            if len(samples) < 3:
                samples.append(
                    {"product_id": row.get("product_id"), "l0": l0, "l1": cats[1] if len(cats) > 1 else None}
                )
        elif l0 == "Deli":
            deli_hits += 1
            if len(samples) < 6:
                samples.append(
                    {"product_id": row.get("product_id"), "l0": l0, "l1": cats[1] if len(cats) > 1 else None}
                )
    return {
        "run_id": bronze.name,
        "pdp_rows": sum(1 for _ in iter_jsonl(pdp)),
        "meat_l0_rows": meat_hits,
        "deli_l0_rows": deli_hits,
        "samples": samples,
    }


def _gold_counts() -> Dict[str, Any]:
    if not GOLD_DB.exists():
        return {"error": "gold DB missing — run scrape_ashfield_deep.py --phase etl"}
    import duckdb

    conn = duckdb.connect(str(GOLD_DB), read_only=True)
    rows = conn.execute("""
        SELECT retailer, unified_category, count(*) AS n
        FROM gold.sku_facts
        WHERE unified_category IN ('Deli', 'Poultry, Meat & Seafood', 'Meat Seafood & Deli')
        GROUP BY 1, 2
        ORDER BY 1, 2
        """).fetchall()
    conn.close()
    out: Dict[str, Dict[str, int]] = {"Coles": {}, "Woolworths": {}}
    for retailer, cat, n in rows:
        out.setdefault(retailer, {})[cat] = int(n)
    return {
        "by_retailer_category": out,
        "ww_combined": sum(out.get("Woolworths", {}).values()),
        "coles_meat_seafood_deli": out.get("Coles", {}).get("Meat Seafood & Deli", 0),
    }


def _venn() -> Dict[str, Any]:
    if not Venn_CSV.exists():
        return {"error": f"missing {Venn_CSV}"}
    meat_rows: List[Dict[str, str]] = []
    with Venn_CSV.open(encoding="utf-8") as handle:
        import csv

        for row in csv.DictReader(handle):
            cat = row.get("unified_category") or ""
            if any(t in cat.lower() for t in ("meat", "deli", "seafood", "poultry")):
                meat_rows.append({"side": row.get("side"), "unified_category": cat})
    return {"rows": meat_rows}


def _glossary_mapping() -> Dict[str, Any]:
    gloss_rows = load_glossary()
    gloss = glossary_by_ww_label(gloss_rows)
    shared = gloss.get(SHARED_DEPT) or {}
    return {
        "shared_label": SHARED_DEPT,
        "ww_label_in_glossary": shared.get("ww_label"),
        "coles_label": shared.get("coles_label"),
        "coles_slug": shared.get("coles_slug"),
        "ww_native_l0s": list(WW_NATIVE_L0),
        "rollup_aliases": {k: v for k, v in WW_GOLD_TO_SHARED.items() if v == SHARED_DEPT},
        "expected_gold_keys": expected_gold_keys_for_shared(SHARED_DEPT, gloss_rows),
    }


def _store_ci_dept() -> Dict[str, Any]:
    if not STORE_CI.exists():
        return {"error": f"missing {STORE_CI}"}
    data = json.loads(STORE_CI.read_text(encoding="utf-8"))
    dept = next((d for d in data.get("departments", []) if d.get("shared_label") == SHARED_DEPT), None)
    ww_only = [
        d
        for d in data.get("departments", [])
        if d.get("in_venn") == "ww_only" and d.get("shared_label") in WW_NATIVE_L0
    ]
    return {
        "generated_at": data.get("meta", {}).get("generated_at"),
        "unified_dept": dept,
        "ww_only_native_depts": ww_only,
    }


def _checks(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    silver = ctx.get("silver") or {}
    gold = ctx.get("gold") or {}
    glossary = ctx.get("glossary") or {}
    store_ci = ctx.get("store_ci") or {}
    venn = ctx.get("venn") or {}

    unified = (store_ci.get("unified_dept") or {}) if isinstance(store_ci, dict) else {}
    ww_skus_unified = int(unified.get("ww_skus") or 0)
    ww_combined_silver = int(silver.get("combined") or 0)
    ww_combined_gold = int(gold.get("ww_combined") or 0)

    def chk(check_id: str, ok: bool, severity: str, detail: str, **extra: Any) -> Dict[str, Any]:
        return {"id": check_id, "ok": ok, "severity": severity, "detail": detail, **extra}

    checks = [
        chk(
            "silver_ww_meat_present",
            int(silver.get("meat_l0") or 0) >= 400,
            "critical",
            f"WW silver Poultry, Meat & Seafood: {silver.get('meat_l0', 0)} (expect ≥400)",
            value=silver.get("meat_l0"),
        ),
        chk(
            "silver_ww_deli_present",
            int(silver.get("deli_l0") or 0) >= 100,
            "critical",
            f"WW silver Deli: {silver.get('deli_l0', 0)} (expect ≥100)",
            value=silver.get("deli_l0"),
        ),
        chk(
            "bronze_categories_populated",
            int((ctx.get("bronze") or {}).get("meat_l0_rows") or 0) > 0
            and int((ctx.get("bronze") or {}).get("deli_l0_rows") or 0) > 0,
            "critical",
            "Bronze PDP must carry Iris L0 categories for meat and deli",
            bronze=ctx.get("bronze"),
        ),
        chk(
            "gold_ww_native_l0s",
            ww_combined_gold >= 600,
            "critical",
            f"Gold WW meat+deli native L0s: {ww_combined_gold} (expect ≥600)",
            value=ww_combined_gold,
        ),
        chk(
            "rollup_aliases_cover_native_l0s",
            all(native in WW_GOLD_TO_SHARED for native in WW_NATIVE_L0),
            "critical",
            f"WW_GOLD_TO_SHARED must map native L0s {WW_NATIVE_L0} into {SHARED_DEPT}",
            rollup=glossary.get("rollup_aliases"),
        ),
        chk(
            "store_ci_unified_dept_has_ww_skus",
            ww_skus_unified >= 600,
            "critical",
            f"Store-CI '{SHARED_DEPT}' ww_skus={ww_skus_unified} (expect ≥600 when silver has {ww_combined_silver})",
            ww_skus=ww_skus_unified,
            silver_combined=ww_combined_silver,
        ),
        chk(
            "venn_not_coles_only_for_shared_name",
            not any(
                r.get("unified_category") == SHARED_DEPT and r.get("side") == "coles_only"
                for r in (venn.get("rows") or [])
            ),
            "info",
            f"Venn should not list '{SHARED_DEPT}' as coles_only once taxonomy is aligned",
            venn_rows=venn.get("rows"),
        ),
        chk(
            "counter_skus_documented",
            int(silver.get("counter_deli_department_skus") or 0) > 0,
            "info",
            (
                f"{silver.get('counter_deli_department_skus', 0)} meat/deli SKUs at "
                "'Deli Department' (location_class=other, excluded from bay share)"
            ),
            value=silver.get("counter_deli_department_skus"),
        ),
    ]
    return checks


def run_eval() -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    silver_dir = _latest_silver_dir()
    ctx: Dict[str, Any] = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "stores": {"Coles": COLES_STORE, "Woolworths": WW_STORE},
        "bronze": _sample_bronze_categories(),
        "silver": _count_silver_ww(silver_dir) if silver_dir else {"error": "no silver dir"},
        "gold": _gold_counts(),
        "venn": _venn(),
        "glossary": _glossary_mapping(),
        "store_ci": _store_ci_dept(),
    }
    checks = _checks(ctx)
    ctx["checks"] = checks
    ctx["summary"] = {
        "passed": sum(1 for c in checks if c["ok"]),
        "failed": sum(1 for c in checks if not c["ok"]),
        "critical_failed": sum(1 for c in checks if not c["ok"] and c["severity"] == "critical"),
    }
    return ctx, checks


def _print_report(ctx: Dict[str, Any], checks: List[Dict[str, Any]]) -> None:
    s = ctx.get("silver") or {}
    g = ctx.get("gold") or {}
    print(f"Meat / seafood / deli eval  ({ctx.get('evaluated_at')})")
    print()
    print("Scrape / lake (WW native L0):")
    print(f"  silver meat L0: {s.get('meat_l0')}  deli L0: {s.get('deli_l0')}  combined: {s.get('combined')}")
    print(f"  gold WW combined: {g.get('ww_combined')}  Coles unified: {g.get('coles_meat_seafood_deli')}")
    print()
    print("Dashboard / taxonomy:")
    gloss = ctx.get("glossary") or {}
    print(f"  glossary ww_label: {gloss.get('ww_label_in_glossary')!r}  (WW gold keys: {WW_NATIVE_L0})")
    dept = (ctx.get("store_ci") or {}).get("unified_dept") or {}
    print(f"  store-CI '{SHARED_DEPT}' ww_skus: {dept.get('ww_skus')}  in_venn: {dept.get('in_venn')}")
    print()
    print("Checks:")
    for c in checks:
        mark = "PASS" if c["ok"] else "FAIL"
        print(f"  [{mark}] ({c['severity']}) {c['id']}: {c['detail']}")
    print()
    summ = ctx.get("summary") or {}
    print(f"Summary: {summ.get('passed')} passed, {summ.get('failed')} failed ({summ.get('critical_failed')} critical)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=str, default=None, help="Write full report JSON to path")
    args = parser.parse_args()
    ctx, checks = run_eval()
    _print_report(ctx, checks)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ctx, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out}")
    critical_fail = any(not c["ok"] and c["severity"] == "critical" for c in checks)
    return 1 if critical_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
