import type { DashboardFilters, PcDashboardData, VennSide } from "../lib/types";
import { COLES, WW, PURPLE, intFmt } from "../lib/types";
import { InfoTip } from "./InfoTip";

interface Props {
  venn: PcDashboardData["venn"];
  matches: PcDashboardData["matches"];
  filters: DashboardFilters;
  onSetSide: (side: VennSide | null) => void;
  onSelectPair: (colesId: number, wwId: number) => void;
  onOpenMethods?: (sectionId?: string) => void;
}

const REGIONS: Array<{ side: VennSide; label: string; fill: string }> = [
  { side: "coles_only", label: "Coles only", fill: COLES },
  { side: "matched", label: "Matched", fill: PURPLE },
  { side: "ww_only", label: "WW only", fill: WW },
];

export function AssortmentVenn({
  venn,
  matches,
  filters,
  onSetSide,
  onSelectPair,
  onOpenMethods,
}: Props) {
  const counts: Record<VennSide, number> = {
    coles_only: venn.coles_only,
    matched: venn.matched,
    ww_only: venn.ww_only,
  };

  return (
    <section className="panel panel--lilac">
      <h2>
        Who overlaps in Personal Care assortment?
        <InfoTip
          plain="Same or very similar product name and brand at both retailers — not a perfect barcode match."
          methodsId="overlap"
          onOpenMethods={onOpenMethods}
        />
      </h2>
      <p className="support">
        Common = fuzzy brand+name matches. Click a region to filter the SKU explorer. Hover for
        example products.
      </p>
      <div className="legend">
        <span className="coles">
          <i /> Coles
        </span>
        <span className="ww">
          <i /> Woolworths
        </span>
      </div>
      <div className="venn-wrap">
        <svg className="venn-svg" viewBox="0 0 420 230" role="img" aria-label="Assortment venn diagram">
          <title>Assortment overlap</title>
          <circle
            className="venn-region"
            cx="155"
            cy="115"
            r="95"
            fill={COLES}
            fillOpacity={0.35}
            stroke={COLES}
            tabIndex={0}
            role="button"
            aria-pressed={filters.side === "coles_only"}
            data-dimmed={filters.side != null && filters.side !== "coles_only" ? "true" : "false"}
            onClick={() => onSetSide(filters.side === "coles_only" ? null : "coles_only")}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSetSide(filters.side === "coles_only" ? null : "coles_only");
              }
            }}
          >
            <title>{`Coles only: ${intFmt(counts.coles_only)}\n${venn.examples.coles_only.join("\n")}`}</title>
          </circle>
          <circle
            className="venn-region"
            cx="265"
            cy="115"
            r="95"
            fill={WW}
            fillOpacity={0.35}
            stroke={WW}
            tabIndex={0}
            role="button"
            aria-pressed={filters.side === "ww_only"}
            data-dimmed={filters.side != null && filters.side !== "ww_only" ? "true" : "false"}
            onClick={() => onSetSide(filters.side === "ww_only" ? null : "ww_only")}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSetSide(filters.side === "ww_only" ? null : "ww_only");
              }
            }}
          >
            <title>{`WW only: ${intFmt(counts.ww_only)}\n${venn.examples.ww_only.join("\n")}`}</title>
          </circle>
          {/* Overlap hit target */}
          <ellipse
            className="venn-region"
            cx="210"
            cy="115"
            rx="42"
            ry="78"
            fill={PURPLE}
            fillOpacity={filters.side === "matched" ? 0.45 : 0.2}
            stroke={PURPLE}
            tabIndex={0}
            role="button"
            aria-pressed={filters.side === "matched"}
            data-dimmed={filters.side != null && filters.side !== "matched" ? "true" : "false"}
            onClick={() => onSetSide(filters.side === "matched" ? null : "matched")}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSetSide(filters.side === "matched" ? null : "matched");
              }
            }}
          >
            <title>{`Matched pairs: ${intFmt(counts.matched)}\n${venn.examples.matched.join("\n")}`}</title>
          </ellipse>
          <text x="105" y="108" textAnchor="middle" fontSize="13" fill="#090909">
            Coles only
          </text>
          <text x="105" y="132" textAnchor="middle" fontSize="18" fontWeight="600" fill="#090909">
            {intFmt(counts.coles_only)}
          </text>
          <text x="210" y="100" textAnchor="middle" fontSize="13" fill="#090909">
            Matched
          </text>
          <text x="210" y="124" textAnchor="middle" fontSize="18" fontWeight="600" fill="#090909">
            {intFmt(counts.matched)}
          </text>
          <text x="315" y="108" textAnchor="middle" fontSize="13" fill="#090909">
            WW only
          </text>
          <text x="315" y="132" textAnchor="middle" fontSize="18" fontWeight="600" fill="#090909">
            {intFmt(counts.ww_only)}
          </text>
        </svg>

        <div>
          <h3 style={{ margin: "0 0 0.5rem", fontSize: "1rem" }}>Matched pairs (click to focus)</h3>
          <div className="match-list">
            {matches.length === 0 ? (
              <div className="empty">No fuzzy matches in Personal Care yet.</div>
            ) : (
              matches.map((m) => {
                const id = `${m.coles_id}:${m.ww_id}`;
                return (
                  <button
                    key={id}
                    type="button"
                    className={`match-row${filters.matchPairId === id ? " active" : ""}`}
                    onClick={() => onSelectPair(m.coles_id, m.ww_id)}
                  >
                    <div>
                      <strong>{m.brand || "—"}</strong> · score {m.score.toFixed(2)}
                    </div>
                    <div className="meta">C: {m.coles_name}</div>
                    <div className="meta">W: {m.ww_name}</div>
                  </button>
                );
              })
            )}
          </div>
        </div>
      </div>
      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginTop: "0.85rem" }}>
        {REGIONS.map((r) => (
          <button
            key={r.side}
            type="button"
            className="chip"
            style={{ background: r.fill, color: r.side === "matched" ? "#fff" : "#090909" }}
            onClick={() => onSetSide(filters.side === r.side ? null : r.side)}
            title={(venn.examples[r.side] || []).join(" · ")}
          >
            {r.label}: {intFmt(counts[r.side])}
          </button>
        ))}
      </div>
      <p className="source">Source: gold.sku_matches + gold.sku_facts · Personal Care</p>
    </section>
  );
}
