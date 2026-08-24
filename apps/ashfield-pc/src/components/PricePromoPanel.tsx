import Plot from "react-plotly.js";
import type { DashboardFilters, PcDashboardData } from "../lib/types";
import { COLES, WW, money, pctFmt } from "../lib/types";
import { baseLayout, plotConfig } from "../lib/plotlyTheme";

interface Props {
  data: PcDashboardData;
  filters: DashboardFilters;
  onSubcategory: (sub: string | null) => void;
}

export function PricePromoPanel({ data, filters, onSubcategory }: Props) {
  const retailers: Array<"Coles" | "Woolworths"> = ["Coles", "Woolworths"];

  const promoBars = {
    x: retailers,
    y: [
      data.kpis.coles_pct_promo,
      data.kpis.ww_pct_promo,
    ],
    type: "bar" as const,
    marker: { color: [COLES, WW] },
    name: "% on promo",
    hovertemplate: "<b>%{x}</b><br>On promo: %{y:.1f}%<extra></extra>",
  };

  const medPrice = {
    x: retailers,
    y: [data.kpis.coles_median_price ?? 0, data.kpis.ww_median_price ?? 0],
    type: "bar" as const,
    marker: { color: [COLES, WW], opacity: 0.5 },
    name: "Median price ($)",
    yaxis: "y2" as const,
    hovertemplate: "<b>%{x}</b><br>Median price: $%{y:.2f}<extra></extra>",
  };

  const labels = Array.from(new Set(data.price_histogram.map((h) => h.label)));
  const histColes = labels.map(
    (l) => data.price_histogram.find((h) => h.retailer === "Coles" && h.label === l)?.count ?? 0,
  );
  const histWw = labels.map(
    (l) => data.price_histogram.find((h) => h.retailer === "Woolworths" && h.label === l)?.count ?? 0,
  );

  const distNote = data.price_distribution
    .map(
      (d) =>
        `${d.retailer}: n=${d.n}, median ${money(d.median)}, IQR ${money(d.p25)}–${money(d.p75)}`,
    )
    .join(" · ");

  const subWw = data.price_by_subcategory
    .filter((s) => s.retailer === "Woolworths")
    .sort((a, b) => b.skus - a.skus)
    .slice(0, 10);

  const ladder = data.promo_ladder;

  return (
    <section className="panel panel--cream">
      <h2>Where is promo pressure — and at what price?</h2>
      <p className="support">
        Snapshot only: no multi-day Personal Care history for these Ashfield IDs yet. Click a
        subcategory to filter the SKU explorer.
      </p>
      <div className="legend">
        <span className="coles">
          <i /> Coles
        </span>
        <span className="ww">
          <i /> Woolworths
        </span>
      </div>

      <div className="grid-2">
        <Plot
          data={[promoBars, medPrice]}
          layout={baseLayout({
            barmode: "group",
            height: 300,
            yaxis: { title: { text: "On promo (%)" }, rangemode: "tozero" },
            yaxis2: {
              title: { text: "Median price ($)" },
              overlaying: "y",
              side: "right",
              rangemode: "tozero",
              showgrid: false,
            },
          })}
          config={plotConfig}
          style={{ width: "100%" }}
        />
        <Plot
          data={[
            {
              x: labels,
              y: histColes,
              type: "bar",
              name: "Coles",
              marker: { color: COLES },
              hovertemplate: "Coles %{x}<br>SKUs: %{y:,}<extra></extra>",
            },
            {
              x: labels,
              y: histWw,
              type: "bar",
              name: "Woolworths",
              marker: { color: WW },
              hovertemplate: "Woolworths %{x}<br>SKUs: %{y:,}<extra></extra>",
            },
          ]}
          layout={baseLayout({
            barmode: "group",
            height: 300,
            xaxis: { title: { text: "Price bin (price_now)" }, tickangle: -35 },
            yaxis: { title: { text: "SKU count" }, rangemode: "tozero" },
          })}
          config={plotConfig}
          style={{ width: "100%" }}
        />
      </div>
      <p className="source">{distNote}</p>

      <div className="grid-2" style={{ marginTop: "1.25rem" }}>
        <div>
          <h3 style={{ margin: "0 0 0.5rem", fontSize: "1.05rem" }}>
            Median price by subcategory (WW L1)
          </h3>
          <Plot
            data={[
              {
                x: subWw.map((s) => s.median_price),
                y: subWw.map((s) => s.subcategory),
                orientation: "h",
                type: "bar",
                marker: {
                  color: subWw.map((s) =>
                    filters.subcategory === s.subcategory ? "#7B4FE8" : WW,
                  ),
                },
                customdata: subWw.map(
                  (s) =>
                    `${s.skus} SKUs<br>Promo: ${pctFmt(s.pct_promo, 1)}<br>Median: ${money(s.median_price)}`,
                ),
                hovertemplate: "<b>%{y}</b><br>%{customdata}<extra></extra>",
              },
            ]}
            layout={baseLayout({
              height: Math.max(280, subWw.length * 36),
              margin: { t: 16, r: 16, b: 40, l: 140 },
              xaxis: { title: { text: "Median price ($)" }, rangemode: "tozero" },
              yaxis: { automargin: true },
              showlegend: false,
            })}
            config={plotConfig}
            style={{ width: "100%" }}
            onClick={(ev) => {
              const sub = ev.points?.[0]?.y as string | undefined;
              if (sub) onSubcategory(filters.subcategory === sub ? null : sub);
            }}
          />
        </div>
        <div>
          <h3 style={{ margin: "0 0 0.5rem", fontSize: "1.05rem" }}>
            Promo SKUs: median was → now
          </h3>
          {ladder.length === 0 ? (
            <div className="empty">No promo SKUs with both was and now prices.</div>
          ) : (
            <Plot
              data={ladder.flatMap((row) => {
                const color = row.retailer === "Coles" ? COLES : WW;
                return [
                  {
                    x: ["Was (median)", "Now (median)", "Regular (median)"],
                    y: [row.median_was, row.median_now, row.median_regular],
                    type: "scatter" as const,
                    mode: "lines+markers" as const,
                    name: row.retailer,
                    line: { color, width: 2 },
                    marker: { size: 9, color },
                    hovertemplate: `<b>${row.retailer}</b><br>%{x}: $%{y:.2f}<br>Promo SKUs: ${row.promo_skus}<extra></extra>`,
                  },
                ];
              })}
              layout={baseLayout({
                height: 300,
                yaxis: { title: { text: "Price ($)" }, rangemode: "tozero" },
                xaxis: { title: { text: "Price point (snapshot)" } },
              })}
              config={plotConfig}
              style={{ width: "100%" }}
            />
          )}
        </div>
      </div>
      <p className="source">Source: gold.sku_facts · snapshot scrape · Ashfield Personal Care</p>
    </section>
  );
}
