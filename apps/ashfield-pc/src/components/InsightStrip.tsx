import type { PcDashboardData } from "../lib/types";
import { intFmt, money, pctFmt } from "../lib/types";
import { InfoTip } from "./InfoTip";

interface Props {
  kpis: PcDashboardData["kpis"];
  onOpenMethods?: (sectionId?: string) => void;
}

export function InsightStrip({ kpis, onOpenMethods }: Props) {
  const cards = [
    {
      label: "Assortment depth",
      value: `${intFmt(kpis.coles_skus)} / ${intFmt(kpis.ww_skus)}`,
      insight: kpis.insights.assortment,
      tip: "How many Personal Care products each retailer lists in our data.",
    },
    {
      label: "Fuzzy-matched pairs",
      value: intFmt(kpis.matched_pairs),
      insight: kpis.insights.overlap,
      tip: "Same or very similar product name and brand at both retailers — not a barcode match.",
      methodsId: "overlap",
    },
    {
      label: "Bay share of store",
      value: `${pctFmt(kpis.coles_pct_store_bays)} vs ${pctFmt(kpis.ww_pct_store_bays)}`,
      insight: kpis.insights.space,
      tip: "Share of this store’s shelf bays used for Personal Care. Higher = more of the store given to this category.",
      methodsId: "bay-share",
    },
    {
      label: "Promo & median price",
      value: `${pctFmt(kpis.coles_pct_promo)} · ${money(kpis.coles_median_price)}`,
      insight: kpis.insights.promo,
      tip: "Share of products on special, and the middle shelf price (Coles shown; Woolworths in the note).",
      methodsId: "promo",
    },
  ];

  return (
    <section className="kpi-grid" aria-label="Personal Care insights">
      {cards.map((c) => (
        <article key={c.label} className="kpi-card">
          <div className="kpi-label">
            {c.label}
            <InfoTip plain={c.tip} methodsId={c.methodsId} onOpenMethods={onOpenMethods} />
          </div>
          <div className="kpi-value">{c.value}</div>
          <div className="kpi-insight">{c.insight}</div>
        </article>
      ))}
    </section>
  );
}
