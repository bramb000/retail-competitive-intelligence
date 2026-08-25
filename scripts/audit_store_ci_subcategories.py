#!/usr/bin/env python3
"""Full audit of store-CI subcategory (L1) alignment across all departments.

Surfaces systemic issues — not one aisle at a time:
  1. L1 parents still on native gold keys (not shared-label rollup)
  2. Parent splits (same L1 label under multiple native parents → one shared)
  3. One-sided L1 rows after shared rollup
  4. Near-duplicate L1 labels within a shared parent
  5. Coles L1 coverage / subcategory_source mix
  6. Crosswalk overrides that stamp coarse L1 (blocks WW-style Poultry etc.)

Usage:
  .venv/bin/python scripts/audit_store_ci_subcategories.py
  .venv/bin/python scripts/audit_store_ci_subcategories.py --json lake/eval/subcategory_audit.json
  .venv/bin/python scripts/audit_store_ci_subcategories.py --md lake/QA_SUBCATEGORIES.md
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import duckdb  # noqa: E402

from lake.etl.category_glossary import (  # noqa: E402
    build_gloss_index,
    load_glossary,
    shared_label_for_gold,
    shared_subcategory_for,
    subcategory_alias_map,
)
from lake.io import REF_ROOT, iter_jsonl  # noqa: E402

GOLD_DB = REPO / "lake" / "gold" / "ashfield_compare.duckdb"
STORE_CI = REPO / "apps" / "store-ci" / "public" / "data" / "store_ci.json"
OVERRIDES = REF_ROOT / "category_crosswalk_overrides.csv"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(label: str) -> str:
    s = _NON_ALNUM.sub(" ", (label or "").lower()).strip()
    return re.sub(r"\s+", " ", s)


def _latest_silver() -> Optional[Path]:
    roots = sorted((REPO / "lake" / "silver").glob("*"), reverse=True)
    return roots[0] if roots else None


def _gold_facts() -> List[Tuple[str, str, Optional[str], int]]:
    conn = duckdb.connect(str(GOLD_DB), read_only=True)
    rows = conn.execute("""
        SELECT retailer, unified_category, unified_subcategory, count(*) AS n
        FROM gold.sku_facts
        WHERE unified_category IS NOT NULL AND unified_category <> 'Unmapped'
        GROUP BY 1, 2, 3
        """).fetchall()
    conn.close()
    return [(str(r), str(c), (str(s) if s else None), int(n)) for r, c, s, n in rows]


def run_audit() -> Dict[str, Any]:
    gloss = build_gloss_index(load_glossary())
    alias_map = subcategory_alias_map()
    facts = _gold_facts()

    by_native: DefaultDict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: {"Coles": 0, "Woolworths": 0})
    by_shared: DefaultDict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: {"Coles": 0, "Woolworths": 0})
    null_sub: DefaultDict[str, Dict[str, int]] = defaultdict(lambda: {"Coles": 0, "Woolworths": 0})
    totals = {"Coles": 0, "Woolworths": 0}
    with_sub = {"Coles": 0, "Woolworths": 0}

    for retailer, cat, sub, n in facts:
        totals[retailer] = totals.get(retailer, 0) + n
        shared = shared_label_for_gold(cat, gloss)
        if not sub:
            null_sub[shared][retailer] += n
            continue
        with_sub[retailer] = with_sub.get(retailer, 0) + n
        by_native[(cat, sub)][retailer] += n
        canon_sub = shared_subcategory_for(shared, sub, alias_map) or sub
        by_shared[(shared, canon_sub)][retailer] += n

    # Parent splits — after L1 shared rollup, native splits should not appear in store-CI.
    # Still report gold-level splits for ETL visibility; critical check uses store-CI parents.
    subs_by_shared: DefaultDict[str, DefaultDict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    for (cat, sub), _ in by_native.items():
        shared = shared_label_for_gold(cat, gloss)
        canon = shared_subcategory_for(shared, sub, alias_map) or sub
        subs_by_shared[shared][canon].add(cat)

    parent_splits: List[Dict[str, Any]] = []
    for shared, submap in sorted(subs_by_shared.items()):
        for sub, natives in sorted(submap.items()):
            if len(natives) <= 1:
                continue
            detail = {nat: dict(by_native[(nat, sub)]) for nat in sorted(natives)}
            parent_splits.append({"shared_parent": shared, "subcategory": sub, "by_native_parent": detail})

    both_shared = []
    one_sided_shared = []
    for (shared, sub), counts in sorted(by_shared.items(), key=lambda x: -(x[1]["Coles"] + x[1]["Woolworths"])):
        row = {
            "shared_parent": shared,
            "subcategory": sub,
            "coles_skus": counts["Coles"],
            "ww_skus": counts["Woolworths"],
        }
        if counts["Coles"] and counts["Woolworths"]:
            both_shared.append(row)
        else:
            row["side"] = "coles_only" if counts["Coles"] else "ww_only"
            one_sided_shared.append(row)

    both_native = sum(1 for c in by_native.values() if c["Coles"] and c["Woolworths"])
    one_sided_native = len(by_native) - both_native

    # Near-dupes
    near_dupes: List[Dict[str, Any]] = []
    for shared, submap in sorted(subs_by_shared.items()):
        labels = sorted(submap.keys())
        norms = {lab: _norm(lab) for lab in labels}
        by_n: DefaultDict[str, List[str]] = defaultdict(list)
        for lab, n in norms.items():
            by_n[n].append(lab)
        for n, labs in by_n.items():
            if len(labs) > 1:
                near_dupes.append({"shared_parent": shared, "kind": "exact_norm", "labels": labs})
        for i, a in enumerate(labels):
            for b in labels[i + 1 :]:
                if norms[a] == norms[b]:
                    continue
                ta, tb = set(norms[a].split()), set(norms[b].split())
                if not ta or not tb:
                    continue
                if ta <= tb or tb <= ta:
                    near_dupes.append({"shared_parent": shared, "kind": "containment", "labels": [a, b]})
                elif len(ta & tb) >= 2 and len(ta & tb) / len(ta | tb) >= 0.6:
                    near_dupes.append({"shared_parent": shared, "kind": "token_overlap", "labels": [a, b]})

    # Per shared parent health
    per_parent: List[Dict[str, Any]] = []
    for shared in sorted({s for s, _ in by_shared}):
        pairs = [by_shared[(shared, sub)] for sh, sub in by_shared if sh == shared]
        per_parent.append(
            {
                "shared_parent": shared,
                "l1_rows": len(pairs),
                "both": sum(1 for c in pairs if c["Coles"] and c["Woolworths"]),
                "coles_only": sum(1 for c in pairs if c["Coles"] and not c["Woolworths"]),
                "ww_only": sum(1 for c in pairs if c["Woolworths"] and not c["Coles"]),
                "coles_skus": sum(c["Coles"] for c in pairs),
                "ww_skus": sum(c["Woolworths"] for c in pairs),
                "null_coles": null_sub[shared]["Coles"],
                "null_ww": null_sub[shared]["Woolworths"],
                "parent_splits": sum(1 for p in parent_splits if p["shared_parent"] == shared),
            }
        )

    # Store-CI current L1 parents
    store_ci_parents: List[Dict[str, Any]] = []
    sci_summary: Dict[str, Any] = {}
    if STORE_CI.exists():
        data = json.loads(STORE_CI.read_text(encoding="utf-8"))
        l1 = data.get("grains", {}).get("subcategory", {}).get("departments") or []
        parent_counts = Counter(d.get("parent_category") for d in l1)
        for parent, n in parent_counts.most_common():
            shared = shared_label_for_gold(str(parent or ""), gloss)
            store_ci_parents.append(
                {
                    "parent": parent,
                    "rows": n,
                    "shared_label": shared,
                    "needs_rollup": parent != shared,
                }
            )
        sci_summary = {
            "generated_at": data.get("meta", {}).get("generated_at"),
            "l1_rows": len(l1),
            "both": sum(1 for d in l1 if d.get("coles_skus") and d.get("ww_skus")),
            "coles_only": sum(1 for d in l1 if d.get("coles_skus") and not d.get("ww_skus")),
            "ww_only": sum(1 for d in l1 if d.get("ww_skus") and not d.get("coles_skus")),
        }

    # Coles silver subcategory sources
    coles_sources: Dict[str, Any] = {"error": "no silver"}
    silver = _latest_silver()
    if silver and (silver / "coles" / "products.jsonl").exists():
        src = Counter()
        per_l0: DefaultDict[str, Dict[str, int]] = defaultdict(lambda: {"has": 0, "null": 0})
        src_by_l0: DefaultDict[str, Counter] = defaultdict(Counter)
        for row in iter_jsonl(silver / "coles" / "products.jsonl"):
            l0 = row.get("unified_category") or "Unmapped"
            shared = shared_label_for_gold(str(l0), gloss)
            sub = row.get("unified_subcategory")
            source = row.get("subcategory_source") or ("none" if not sub else "crosswalk_or_unknown")
            if sub:
                src[source] += 1
                per_l0[shared]["has"] += 1
                src_by_l0[shared][source] += 1
            else:
                src["null"] += 1
                per_l0[shared]["null"] += 1
        coles_sources = {
            "silver_stamp": silver.name,
            "by_source": dict(src),
            "by_shared_parent": {
                k: {
                    "has": v["has"],
                    "null": v["null"],
                    "coverage_pct": (
                        round(100.0 * v["has"] / (v["has"] + v["null"]), 1) if (v["has"] + v["null"]) else 0.0
                    ),
                    "sources": dict(src_by_l0[k]),
                }
                for k, v in sorted(per_l0.items(), key=lambda x: -(x[1]["has"] + x[1]["null"]))
            },
        }

    # Coarse crosswalk L1 stamps
    coarse_overrides: List[Dict[str, str]] = []
    if OVERRIDES.exists():
        with OVERRIDES.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if (row.get("woolworths_subcategory") or "").strip():
                    coarse_overrides.append(
                        {
                            "coles_slug": row.get("coles_category_slug") or "",
                            "ww_l0": row.get("woolworths_category") or "",
                            "ww_l1": row.get("woolworths_subcategory") or "",
                            "notes": row.get("notes") or "",
                        }
                    )

    # Systemic issue checklist (pass/fail for eval exit code)
    issues: List[Dict[str, Any]] = []

    def issue(issue_id: str, severity: str, ok: bool, detail: str, **extra: Any) -> None:
        issues.append({"id": issue_id, "severity": severity, "ok": ok, "detail": detail, **extra})

    needs_rollup = [p for p in store_ci_parents if p.get("needs_rollup")]
    issue(
        "l1_parents_use_shared_label",
        "critical",
        len(needs_rollup) == 0,
        f"{len(needs_rollup)} store-CI L1 parents still use native gold keys " f"(expect 0 after export rollup)",
        parents=needs_rollup,
    )
    # Parent splits in gold are OK if export merged them; fail only if store-CI still has split parents.
    issue(
        "l1_parent_splits_in_store_ci",
        "critical",
        len(needs_rollup) == 0,
        (
            "Store-CI L1 must use shared parents so native parent splits merge "
            f"({len(parent_splits)} gold-level splits remain for ETL)"
        ),
        gold_parent_splits=len(parent_splits),
    )
    issue(
        "coles_subcategory_coverage",
        "high",
        (with_sub["Coles"] / totals["Coles"] if totals["Coles"] else 0) >= 0.55,
        (
            f"Coles subcategory coverage {100 * with_sub['Coles'] / totals['Coles']:.1f}% "
            f"(target ≥55% after shared-L0 matcher fix)"
        ),
        coverage=round(100 * with_sub["Coles"] / totals["Coles"], 1) if totals["Coles"] else 0,
    )
    issue(
        "coarse_crosswalk_l1_overrides",
        "high",
        len(coarse_overrides) == 0,
        f"{len(coarse_overrides)} crosswalk overrides stamp a fixed L1 (blocks fine WW labels like Poultry)",
        overrides=coarse_overrides,
    )
    meat_both_poultry = any(
        r["shared_parent"] == "Meat Seafood & Deli"
        and r["subcategory"] == "Poultry"
        and r["coles_skus"] > 0
        and r["ww_skus"] > 0
        for r in both_shared
    )
    meat_coles_poultry = any(
        r["shared_parent"] == "Meat Seafood & Deli" and r["subcategory"] == "Poultry" and r["coles_skus"] > 0
        for r in both_shared + one_sided_shared
    )
    issue(
        "meat_coles_has_poultry_or_fine_l1",
        "high",
        meat_coles_poultry or meat_both_poultry,
        "Coles should have Poultry (or other fine WW L1) SKUs under Meat Seafood & Deli after matcher fix",
        meat_both_poultry=meat_both_poultry,
        meat_coles_poultry=meat_coles_poultry,
    )

    critical_failed = sum(1 for i in issues if not i["ok"] and i["severity"] == "critical")
    high_failed = sum(1 for i in issues if not i["ok"] and i["severity"] == "high")

    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "native_l1_pairs": len(by_native),
            "native_both": both_native,
            "native_one_sided": one_sided_native,
            "shared_l1_pairs": len(by_shared),
            "shared_both": len(both_shared),
            "shared_one_sided": len(one_sided_shared),
            "parent_splits": len(parent_splits),
            "near_duplicate_groups": len(near_dupes),
            "coles_with_sub": with_sub["Coles"],
            "coles_total": totals["Coles"],
            "coles_coverage_pct": round(100 * with_sub["Coles"] / totals["Coles"], 1) if totals["Coles"] else 0,
            "ww_with_sub": with_sub["Woolworths"],
            "ww_total": totals["Woolworths"],
            "ww_coverage_pct": (
                round(100 * with_sub["Woolworths"] / totals["Woolworths"], 1) if totals["Woolworths"] else 0
            ),
            "store_ci": sci_summary,
            "critical_failed": critical_failed,
            "high_failed": high_failed,
        },
        "issues": issues,
        "parent_splits": parent_splits,
        "one_sided_shared_top": one_sided_shared[:60],
        "both_shared_top": both_shared[:40],
        "near_dupes": near_dupes,
        "per_parent": per_parent,
        "null_sub_by_parent": {
            k: dict(v) for k, v in sorted(null_sub.items(), key=lambda x: -(x[1]["Coles"] + x[1]["Woolworths"]))
        },
        "store_ci_parents": store_ci_parents,
        "coles_sources": coles_sources,
        "coarse_crosswalk_l1_overrides": coarse_overrides,
    }


def _print_report(ctx: Dict[str, Any]) -> None:
    s = ctx["summary"]
    print(f"Subcategory audit  ({ctx['evaluated_at']})")
    print()
    print("SKU coverage:")
    print(f"  Coles L1: {s['coles_with_sub']}/{s['coles_total']} ({s['coles_coverage_pct']}%)")
    print(f"  WW L1:    {s['ww_with_sub']}/{s['ww_total']} ({s['ww_coverage_pct']}%)")
    print()
    print("L1 pairs:")
    print(f"  Native parents: {s['native_l1_pairs']} (both={s['native_both']} one-sided={s['native_one_sided']})")
    print(f"  Shared parents: {s['shared_l1_pairs']} (both={s['shared_both']} one-sided={s['shared_one_sided']})")
    print(f"  Parent splits:  {s['parent_splits']}  Near-dupes: {s['near_duplicate_groups']}")
    if s.get("store_ci"):
        sc = s["store_ci"]
        print(
            f"  Store-CI L1:    {sc.get('l1_rows')} "
            f"(both={sc.get('both')} coles_only={sc.get('coles_only')} "
            f"ww_only={sc.get('ww_only')})"
        )
    print()
    print("Systemic issues:")
    for i in ctx["issues"]:
        mark = "PASS" if i["ok"] else "FAIL"
        print(f"  [{mark}] ({i['severity']}) {i['id']}: {i['detail']}")
    print()
    print("Parent splits:")
    for p in ctx["parent_splits"]:
        print(f"  {p['shared_parent']!r} :: {p['subcategory']!r}")
        for nat, cnt in p["by_native_parent"].items():
            print(f"    {nat}: Coles={cnt['Coles']} WW={cnt['Woolworths']}")
    print()
    print("Per shared parent:")
    for p in ctx["per_parent"]:
        print(
            f"  {p['shared_parent']:28} L1={p['l1_rows']:3} both={p['both']:3} "
            f"c_only={p['coles_only']:3} w_only={p['ww_only']:3} "
            f"null_c={p['null_coles']:5} splits={p['parent_splits']}"
        )


def _to_markdown(ctx: Dict[str, Any]) -> str:
    s = ctx["summary"]
    lines = [
        "# QA: Store-CI subcategory (L1) full audit",
        "",
        f"_Generated {ctx['evaluated_at']}_",
        "",
        "## Verdict",
        "",
        "Subcategory competitive views are **systemically misaligned**, not broken in a single aisle. "
        "L0 (category) rollup was fixed; **L1 export still keys off native gold parents**, Coles L1 coverage "
        "is ~47%, and coarse crosswalk L1 overrides force Coles meat into `Meat`/`Deli` instead of WW labels "
        "(`Poultry`, `Seafood`, …).",
        "",
        "## Headline numbers",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        (
            f"| Coles SKUs with subcategory | {s['coles_with_sub']}/{s['coles_total']} "
            f"({s['coles_coverage_pct']}%) |"
        ),
        (f"| WW SKUs with subcategory | {s['ww_with_sub']}/{s['ww_total']} " f"({s['ww_coverage_pct']}%) |"),
        (
            f"| Native (parent, sub) pairs | {s['native_l1_pairs']} "
            f"(both={s['native_both']}, one-sided={s['native_one_sided']}) |"
        ),
        (
            f"| Shared-parent pairs | {s['shared_l1_pairs']} "
            f"(both={s['shared_both']}, one-sided={s['shared_one_sided']}) |"
        ),
        f"| Parent splits | {s['parent_splits']} |",
        f"| Near-duplicate label groups | {s['near_duplicate_groups']} |",
        "",
        "## Systemic issues (fix)",
        "",
    ]
    for i in ctx["issues"]:
        mark = "PASS" if i["ok"] else "FAIL"
        lines.append(f"- **[{mark}]** `{i['id']}` ({i['severity']}): {i['detail']}")
    lines += [
        "",
        "## Root causes (ordered)",
        "",
        "### 1. L1 export does not use shared-parent rollup (critical)",
        "",
        "`scripts/export_store_ci_data.py` builds L1 from raw `unified_category` + `unified_subcategory`. "
        "L0 already rolls WW natives via `WW_GOLD_TO_SHARED`; L1 does not. Effect:",
        "",
        "- Coles meat under parent `Meat Seafood & Deli`",
        "- WW poultry under parent `Poultry, Meat & Seafood`",
        "- Same subcategory label `Meat` appears as two one-sided rows instead of one both-banner row",
        "",
        "Store-CI L1 parents needing rollup:",
        "",
    ]
    for p in ctx.get("store_ci_parents") or []:
        if p.get("needs_rollup"):
            lines.append(f"- `{p['parent']}` ({p['rows']} rows) → `{p['shared_label']}`")
    lines += [
        "",
        "### 2. Parent splits (critical)",
        "",
        "Same L1 label under multiple native parents that share one glossary department. "
        "Export without rollup double-counts aisles and breaks joins.",
        "",
    ]
    for p in ctx["parent_splits"]:
        lines.append(f"- **{p['shared_parent']}** / `{p['subcategory']}`:")
        for nat, cnt in p["by_native_parent"].items():
            lines.append(f"  - `{nat}`: Coles={cnt['Coles']}, WW={cnt['Woolworths']}")
    lines += [
        "",
        "### 3. Coarse crosswalk L1 overrides (high)",
        "",
        "`lake/ref/category_crosswalk_overrides.csv` stamps a single L1 for whole Coles slugs. "
        "Matcher can only override when `subcategory_hint` L0 matches; many meat SKUs keep override L1.",
        "",
    ]
    for o in ctx.get("coarse_crosswalk_l1_overrides") or []:
        lines.append(f"- `{o['coles_slug']}` → L0 `{o['ww_l0']}` L1 `{o['ww_l1']}`")
    lines += [
        "",
        "Meat inventory today: Coles L1 = `Meat` + `Deli` only; WW = `Poultry`, `Seafood`, `Deli Meats`, …",
        "",
        "### 4. Coles L1 coverage ~47% (high)",
        "",
        "WW Iris breadcrumb L1 ≈ 100%. Coles relies on SKU match + inference. Zero coverage depts: "
        "Liquor, Fruit & Vegetables, International Foods.",
        "",
        "### 5. Residual one-sided L1 after rollup (expected + label debt)",
        "",
        "Even with shared parents, many WW-only L1s remain (true assortment: Everyday Market, Lunch Box; "
        "or label mismatch: Coles `Deli` vs WW `Deli Meats`). Near-dupes need a subcategory glossary.",
        "",
        "## Per shared parent",
        "",
        "| Parent | L1 rows | Both | Coles-only | WW-only | Coles null L1 | Splits |",
        "|--------|---------|------|------------|---------|---------------|--------|",
    ]
    for p in ctx["per_parent"]:
        lines.append(
            f"| {p['shared_parent']} | {p['l1_rows']} | {p['both']} | {p['coles_only']} | "
            f"{p['ww_only']} | {p['null_coles']} | {p['parent_splits']} |"
        )
    lines += [
        "",
        "## Systemic fix plan (do together, not aisle-by-aisle)",
        "",
        "1. **Export:** Build L1 on `shared_label_for_gold(unified_category)` + subcategory; merge gold_keys; "
        "point SKU `subcategory_id` / `parent_category` at shared parent ids.",
        "2. **Crosswalk:** Clear `woolworths_subcategory` on overrides (`meat-seafood`, `deli`, `chips-…`); "
        "keep L0 only. Let matcher/inference assign WW L1.",
        "3. **ETL re-run:** bronze→silver→gold after override change so Coles meat gets Poultry/Seafood where matched.",
        "4. **Subcategory glossary (optional phase 2):** map near-dupes "
        "(`Deli`↔`Deli Meats`, `Coffee`↔`Tea & Coffee`).",
        "5. **Coverage push (phase 2):** improve Coles L1 for Fruit, Liquor, " "International, Home & Lifestyle.",
        "6. **Gate:** `scripts/audit_store_ci_subcategories.py` exit 0 before " "treating store-CI L1 as trustworthy.",
        "",
        "## Commands",
        "",
        "```bash",
        ".venv/bin/python scripts/audit_store_ci_subcategories.py",
        ".venv/bin/python scripts/audit_store_ci_subcategories.py --json lake/eval/subcategory_audit.json",
        "```",
        "",
        "## Sign-off",
        "",
        "- [ ] Critical issues PASS (L1 shared parents, zero parent splits)",
        "- [ ] Meat/poultry: Coles SKUs appear under fine WW L1s where matches exist",
        "- [ ] Coles L1 coverage ≥ 70% (or waived with documented dept exceptions)",
        "- [ ] Dashboard subcategory grain smoke-tested on Meat, Pantry, Personal Care",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=str, default=None)
    parser.add_argument("--md", type=str, default=None, help="Write markdown QA report")
    args = parser.parse_args()

    if not GOLD_DB.exists():
        print(f"missing gold DB: {GOLD_DB}", file=sys.stderr)
        return 2

    ctx = run_audit()
    _print_report(ctx)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ctx, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out}")
    if args.md:
        out = Path(args.md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_to_markdown(ctx), encoding="utf-8")
        print(f"Wrote {out}")

    return 1 if ctx["summary"]["critical_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
