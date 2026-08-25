#!/usr/bin/env python3
"""Verify store-CI department SKU counts match gold after WW native L0 rollups.

Usage:
  .venv/bin/python scripts/eval_store_ci_category_rollup.py
  .venv/bin/python scripts/eval_store_ci_category_rollup.py --json lake/eval/store_ci_category_rollup.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import duckdb  # noqa: E402

from lake.etl.category_glossary import (  # noqa: E402
    expected_gold_keys_for_shared,
    load_glossary,
    rollup_gold_by_shared,
)

GOLD_DB = REPO / "lake" / "gold" / "ashfield_compare.duckdb"
STORE_CI = REPO / "apps" / "store-ci" / "public" / "data" / "store_ci.json"

# Shared departments where WW native keys must roll up (min WW SKUs in store-CI).
ROLLUP_MIN_WW: Dict[str, int] = {
    "Meat Seafood & Deli": 600,
    "Fruit & Vegetables": 300,
    "Liquor": 1,
    "Pantry": 4000,
    "Personal Care": 3500,
}


def _gold_counts() -> Tuple[Dict[str, int], Dict[str, int]]:
    conn = duckdb.connect(str(GOLD_DB), read_only=True)
    rows = conn.execute("""
        SELECT retailer, unified_category, count(*) AS n
        FROM gold.sku_facts
        WHERE unified_category IS NOT NULL AND unified_category <> 'Unmapped'
        GROUP BY 1, 2
        """).fetchall()
    conn.close()
    coles: Dict[str, int] = {}
    ww: Dict[str, int] = {}
    for retailer, cat, n in rows:
        if retailer == "Coles":
            coles[str(cat)] = int(n)
        else:
            ww[str(cat)] = int(n)
    return coles, ww


def _sum_keys(counts: Dict[str, int], keys: List[str]) -> int:
    return sum(counts.get(k, 0) for k in keys)


def run_eval() -> Dict[str, Any]:
    if not GOLD_DB.exists():
        raise SystemExit(f"missing gold DB: {GOLD_DB}")
    if not STORE_CI.exists():
        raise SystemExit(f"missing store-CI export: {STORE_CI}")

    glossary_rows = load_glossary()
    coles_gold, ww_gold = _gold_counts()
    gold_cats = set(coles_gold) | set(ww_gold)
    gold_by_shared = rollup_gold_by_shared(gold_cats, glossary_rows)

    store_ci = json.loads(STORE_CI.read_text(encoding="utf-8"))
    dept_by_shared = {d["shared_label"]: d for d in store_ci.get("departments", [])}

    checks: List[Dict[str, Any]] = []

    def chk(check_id: str, ok: bool, severity: str, detail: str, **extra: Any) -> None:
        checks.append({"id": check_id, "ok": ok, "severity": severity, "detail": detail, **extra})

    for shared, min_ww in ROLLUP_MIN_WW.items():
        keys = gold_by_shared.get(shared) or expected_gold_keys_for_shared(shared, glossary_rows)
        dept = dept_by_shared.get(shared)
        exp_ww = _sum_keys(ww_gold, keys)
        exp_coles = _sum_keys(coles_gold, keys)
        act_ww = int(dept.get("ww_skus") or 0) if dept else 0
        act_coles = int(dept.get("coles_skus") or 0) if dept else 0
        dept_keys = list(dept.get("gold_keys") or []) if dept else []

        chk(
            f"rollup_keys_{shared.replace(' ', '_').replace('&', 'and')}",
            dept is not None and set(keys).issubset(set(dept_keys)),
            "critical",
            f"{shared}: gold_keys {dept_keys} should include rollup keys {keys}",
            expected_keys=keys,
            actual_keys=dept_keys,
        )
        chk(
            f"ww_skus_{shared.replace(' ', '_').replace('&', 'and')}",
            act_ww >= min_ww and act_ww == exp_ww,
            "critical",
            f"{shared}: store-CI ww_skus={act_ww} (gold={exp_ww}, min={min_ww})",
            actual=act_ww,
            expected=exp_ww,
        )
        chk(
            f"coles_skus_{shared.replace(' ', '_').replace('&', 'and')}",
            act_coles == exp_coles,
            "high",
            f"{shared}: store-CI coles_skus={act_coles} (gold={exp_coles})",
            actual=act_coles,
            expected=exp_coles,
        )
        chk(
            f"in_venn_{shared.replace(' ', '_').replace('&', 'and')}",
            (dept or {}).get("in_venn") == "both" if exp_ww and exp_coles else True,
            "high",
            f"{shared}: in_venn={(dept or {}).get('in_venn')} (expect both when both banners have SKUs)",
        )

    # Orphan WW-only native keys that should have been rolled up must not appear as top-level glossary dupes.
    rolled_up = {k for shared, keys in gold_by_shared.items() for k in keys if k != shared and shared in ROLLUP_MIN_WW}
    for native in sorted(rolled_up):
        if native in dept_by_shared and native not in ROLLUP_MIN_WW:
            chk(
                f"no_orphan_{native.replace(' ', '_')}",
                False,
                "high",
                f"Native gold key {native!r} still exported as standalone department; should roll up",
            )

    passed = sum(1 for c in checks if c["ok"])
    failed = sum(1 for c in checks if not c["ok"])
    critical_failed = sum(1 for c in checks if not c["ok"] and c["severity"] == "critical")

    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "store_ci_generated_at": store_ci.get("meta", {}).get("generated_at"),
        "gold_by_shared": gold_by_shared,
        "checks": checks,
        "summary": {"passed": passed, "failed": failed, "critical_failed": critical_failed},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=str, default=None)
    args = parser.parse_args()

    ctx = run_eval()
    print(f"Store-CI category rollup eval ({ctx['evaluated_at']})")
    print(f"store_ci.json generated_at: {ctx.get('store_ci_generated_at')}")
    print()
    for c in ctx["checks"]:
        mark = "PASS" if c["ok"] else "FAIL"
        print(f"  [{mark}] ({c['severity']}) {c['id']}: {c['detail']}")
    print()
    s = ctx["summary"]
    print(f"Summary: {s['passed']} passed, {s['failed']} failed ({s['critical_failed']} critical)")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ctx, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out}")

    return 1 if s["critical_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
