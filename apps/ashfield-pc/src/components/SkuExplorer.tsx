import { useMemo, useState } from "react";
import type { DashboardFilters, SkuRow } from "../lib/types";
import { filterSkus, intFmt, money } from "../lib/types";

interface Props {
  skus: SkuRow[];
  filters: DashboardFilters;
  onSearch: (q: string) => void;
}

type SortKey = "name" | "retailer" | "price_now" | "match_score" | "brand";

export function SkuExplorer({ skus, filters, onSearch }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const filtered = useMemo(() => filterSkus(skus, filters), [skus, filters]);

  const sorted = useMemo(() => {
    const rows = [...filtered];
    const dir = sortDir === "asc" ? 1 : -1;
    rows.sort((a, b) => {
      if (filters.side === "matched" && sortKey === "name") {
        return dir * ((b.match_score ?? 0) - (a.match_score ?? 0));
      }
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "number" && typeof bv === "number") return dir * (av - bv);
      return dir * String(av).localeCompare(String(bv));
    });
    return rows;
  }, [filtered, sortKey, sortDir, filters.side]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir(key === "price_now" || key === "match_score" ? "desc" : "asc");
    }
  }

  function exportCsv() {
    const header = [
      "retailer",
      "id",
      "name",
      "brand",
      "subcategory",
      "price_now",
      "price_was",
      "is_promo",
      "bay_key",
      "side",
      "match_partner_name",
      "match_score",
    ];
    const lines = [header.join(",")];
    for (const s of sorted) {
      lines.push(
        [
          s.retailer,
          s.id,
          csvEscape(s.name),
          csvEscape(s.brand),
          csvEscape(s.subcategory),
          s.price_now ?? "",
          s.price_was ?? "",
          s.is_promo,
          s.bay_key ?? "",
          s.side,
          csvEscape(s.match_partner_name),
          s.match_score ?? "",
        ].join(","),
      );
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "personal_care_skus.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section className="panel" id="sku-explorer">
      <h2>SKU explorer</h2>
      <p className="support">
        Showing {intFmt(sorted.length)} of {intFmt(skus.length)} Personal Care SKUs
        {filters.side || filters.bayKey || filters.subcategory || filters.retailer || filters.matchPairId
          ? " (filtered from charts above)"
          : ""}
        .
      </p>
      <div className="table-toolbar">
        <input
          type="search"
          placeholder="Search name, brand, or ID"
          value={filters.search}
          onChange={(e) => onSearch(e.target.value)}
          aria-label="Search SKUs"
        />
        <button type="button" onClick={exportCsv}>
          Export CSV
        </button>
      </div>
      {sorted.length === 0 ? (
        <div className="empty">
          No SKUs match these filters. Clear filters in the chip bar to reset.
        </div>
      ) : (
        <div className="sku-table-wrap">
          <table className="sku-table">
            <thead>
              <tr>
                <Th label="Retailer" onClick={() => toggleSort("retailer")} />
                <Th label="Name" onClick={() => toggleSort("name")} />
                <Th label="Brand" onClick={() => toggleSort("brand")} />
                <th>Subcategory</th>
                <Th label="Price now" onClick={() => toggleSort("price_now")} />
                <th>Promo</th>
                <th>Bay</th>
                <th>Match</th>
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, 500).map((s) => (
                <tr key={`${s.retailer}-${s.id}`} tabIndex={0}>
                  <td>{s.retailer}</td>
                  <td>
                    <div>{s.name}</div>
                    <div style={{ fontSize: "0.78rem", color: "#595959" }}>ID {s.id}</div>
                  </td>
                  <td>{s.brand ?? "—"}</td>
                  <td>{s.subcategory ?? "—"}</td>
                  <td>
                    {money(s.price_now)}
                    {s.price_was != null ? (
                      <div style={{ fontSize: "0.78rem", color: "#595959" }}>was {money(s.price_was)}</div>
                    ) : null}
                  </td>
                  <td>
                    <span className={`pill ${s.is_promo ? "pill-promo" : "pill-regular"}`}>
                      {s.is_promo ? "Promo" : "Regular"}
                    </span>
                  </td>
                  <td>{s.bay_key ?? "—"}</td>
                  <td>
                    {s.match_partner_name ? (
                      <>
                        <div>{s.match_partner_name}</div>
                        <div style={{ fontSize: "0.78rem", color: "#595959" }}>
                          score {s.match_score?.toFixed(2)}
                        </div>
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {sorted.length > 500 ? (
            <div className="empty">Showing first 500 rows — export CSV for the full filtered set.</div>
          ) : null}
        </div>
      )}
      <p className="source">Source: gold.sku_facts · Personal Care</p>
    </section>
  );
}

function Th({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <th>
      <button
        type="button"
        onClick={onClick}
        style={{
          border: "none",
          background: "transparent",
          font: "inherit",
          fontWeight: 600,
          cursor: "pointer",
          padding: 0,
          color: "inherit",
        }}
      >
        {label}
      </button>
    </th>
  );
}

function csvEscape(v: string | null | undefined): string {
  const s = v ?? "";
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}
