import Plot from "react-plotly.js";
import type { DashboardFilters, PcDashboardData, SkuRow } from "../lib/types";
import { COLES, WW, intFmt, pctFmt } from "../lib/types";
import { bannerColor, baseLayout, plotConfig } from "../lib/plotlyTheme";
import { InfoTip } from "./InfoTip";

interface Props {
  space: PcDashboardData["space"];
  skus: SkuRow[];
  filters: DashboardFilters;
  onBay: (bayKey: string | null) => void;
  onRetailer: (retailer: "Coles" | "Woolworths" | null) => void;
  onOpenMethods?: (sectionId?: string) => void;
}

export function SpacePanel({ space, skus, filters, onBay, onRetailer, onOpenMethods }: Props) {
  const retailers: Array<"Coles" | "Woolworths"> = ["Coles", "Woolworths"];
  const bayShare = {
    x: retailers,
    y: retailers.map((r) => {
      const row = space.find((s) => s.retailer === r);
      return row ? row.pct_store_bays * 100 : 0;
    }),
    customdata: retailers.map((r) => {
      const row = space.find((s) => s.retailer === r);
      return row
        ? `${row.bay_count} of ${row.store_bay_count} store bays<br>Placed SKUs: ${intFmt(row.placed_skus)}${
            row.facing_sum != null ? `<br>Facing sum: ${intFmt(row.facing_sum)}` : ""
          }`
        : "";
    }),
    type: "bar" as const,
    marker: { color: [COLES, WW] },
    hovertemplate: "<b>%{x}</b><br>%{y:.1f}% of store bays<br>%{customdata}<extra></extra>",
    name: "% store bays",
  };

  const placed = {
    x: retailers,
    y: retailers.map((r) => space.find((s) => s.retailer === r)?.placed_skus ?? 0),
    type: "bar" as const,
    marker: { color: [COLES, WW], opacity: 0.55 },
    hovertemplate: "<b>%{x}</b><br>Placed SKUs: %{y:,}<extra></extra>",
    name: "Placed SKUs",
    yaxis: "y2" as const,
  };

  return (
    <section className="panel">
      <h2>
        Who has more Personal Care bay share?
        <InfoTip
          plain="Share of this store’s shelf bays used for Personal Care. Higher = more of the store given to this category."
          methodsId="bay-share"
          onOpenMethods={onOpenMethods}
        />
      </h2>
      <p className="support">
        Bay share is the cross-banner space signal. Floor scatters use each banner&apos;s own map —
        do not compare absolute coordinates across banners.{" "}
        <InfoTip
          plain="Coles doesn’t publish bay numbers. We estimate them from where products sit on the store map."
          methodsId="coles-inference"
          onOpenMethods={onOpenMethods}
        />{" "}
        <InfoTip
          plain="Woolworths bay numbers come from their own aisle and bay labels."
          methodsId="ww-bays"
          onOpenMethods={onOpenMethods}
        />
      </p>
      <div className="legend">
        <span className="coles">
          <i /> Coles
        </span>
        <span className="ww">
          <i /> Woolworths
        </span>
      </div>
      <Plot
        data={[bayShare, placed]}
        layout={baseLayout({
          barmode: "group",
          yaxis: { title: { text: "Share of store bays (%)" }, rangemode: "tozero" },
          yaxis2: {
            title: { text: "Placed SKUs" },
            overlaying: "y",
            side: "right",
            rangemode: "tozero",
            showgrid: false,
          },
          height: 320,
        })}
        config={plotConfig}
        style={{ width: "100%" }}
        onClick={(ev) => {
          const r = ev.points?.[0]?.x;
          if (r === "Coles" || r === "Woolworths") {
            onRetailer(filters.retailer === r ? null : r);
          }
        }}
      />
      <div className="scatter-grid" style={{ marginTop: "1rem" }}>
        {retailers.map((retailer) => {
          const pts = skus.filter(
            (s) =>
              s.retailer === retailer &&
              s.indoor_x != null &&
              s.indoor_y != null &&
              (!filters.side || s.side === filters.side) &&
              (!filters.subcategory || (s.subcategory || "(none)") === filters.subcategory),
          );
          const dimmed = filters.retailer != null && filters.retailer !== retailer;
          return (
            <div className="map-card" key={retailer} style={{ opacity: dimmed ? 0.4 : 1 }}>
              <h3>
                {retailer} map{" "}
                <span style={{ fontWeight: 400, color: "#595959" }}>
                  ({intFmt(pts.length)} placed points)
                </span>
                <InfoTip
                  plain={
                    retailer === "Coles"
                      ? "Each dot is a product on Coles’ map. Bays are estimated from map gaps — not official Coles bay IDs."
                      : "Each dot is a product on Woolworths’ map. Don’t compare positions to the Coles map — different maps."
                  }
                  methodsId={retailer === "Coles" ? "coles-inference" : "ww-bays"}
                  onOpenMethods={onOpenMethods}
                />
              </h3>
              <Plot
                data={[
                  {
                    x: pts.map((p) => p.indoor_x),
                    y: pts.map((p) => p.indoor_y),
                    text: pts.map(
                      (p) =>
                        `<b>${p.name}</b><br>${p.brand ?? "—"}<br>Bay ${p.bay_key ?? "—"}<br>$${
                          p.price_now?.toFixed(2) ?? "—"
                        } · ${p.is_promo ? "Promo" : "Regular"}`,
                    ),
                    customdata: pts.map((p) => p.bay_key),
                    type: "scatter",
                    mode: "markers",
                    marker: {
                      size: 7,
                      color: pts.map((p) =>
                        filters.bayKey && p.bay_key === filters.bayKey ? "#7B4FE8" : bannerColor(retailer),
                      ),
                      opacity: 0.7,
                    },
                    hovertemplate: "%{text}<extra></extra>",
                    name: retailer,
                  },
                ]}
                layout={baseLayout({
                  height: 280,
                  margin: { t: 10, r: 10, b: 40, l: 48 },
                  xaxis: { title: { text: "Indoor X (banner CRS)" }, zeroline: false },
                  yaxis: { title: { text: "Indoor Y (banner CRS)" }, zeroline: false, scaleanchor: "x" },
                  showlegend: false,
                })}
                config={plotConfig}
                style={{ width: "100%" }}
                onClick={(ev) => {
                  const bay = ev.points?.[0]?.customdata as string | null | undefined;
                  if (bay) onBay(filters.bayKey === bay ? null : bay);
                }}
              />
            </div>
          );
        })}
      </div>
      <p className="source">
        Source: gold.category_space · gold.sku_facts · Coles {pctFmt(space.find((s) => s.retailer === "Coles")?.pct_store_bays != null ? (space.find((s) => s.retailer === "Coles")!.pct_store_bays * 100) : null)} bay share · WW{" "}
        {pctFmt(space.find((s) => s.retailer === "Woolworths")?.pct_store_bays != null ? (space.find((s) => s.retailer === "Woolworths")!.pct_store_bays * 100) : null)}
      </p>
    </section>
  );
}
