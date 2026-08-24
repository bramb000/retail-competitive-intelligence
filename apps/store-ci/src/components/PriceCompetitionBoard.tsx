import { InfoTip } from "./InfoTip";
import { BannerMark, DualBannerMarks } from "./BannerMark";
import {
  gapLabel,
  intFmt,
  money,
  pctFmt,
  signedPct,
  type Grain,
  type PriceCompetitionRow,
  type StoreCiData,
  grainNoun,
  grainTitle,
} from "../lib/types";

interface Props {
  data: StoreCiData;
  grain: Grain;
  locationName: string;
  onSelect: (deptId: string) => void;
  onOpenMethods: (sectionId?: string) => void;
}

export function PriceCompetitionBoard({ data, grain, locationName, onSelect, onOpenMethods }: Props) {
  const rows = data.scoreboards?.price_competition ?? [];
  const hot = rows.filter((r) => r.status === "hot_gap").length;
  const competing = rows.filter((r) => r.status === "competing").length;
  const aligned = rows.filter((r) => r.status === "aligned").length;
  const colesCheaper = rows.filter((r) => r.cheaper_on_median === "Coles").length;
  const wwCheaper = rows.filter((r) => r.cheaper_on_median === "Woolworths").length;
  const nounMany = grainNoun(grain);
  const nounOne = grainNoun(grain, "one");

  return (
    <>
      <header className="hero">
        <p className="eyebrow">
          {grainTitle(grain)} × {locationName}
        </p>
        <h1>Where {nounMany} compete on price</h1>
        <p>
          Median shelf price by {nounOne} at {locationName}. Hot gaps (&gt;15%) often mean mix
          differences while one banner’s scrape is thin — treat as directional until both sides are
          deep.
        </p>
        <p className="hero-methods-link">
          <button type="button" className="text-link" onClick={() => onOpenMethods("price-race")}>
            How {nounOne} price gaps are judged
          </button>
        </p>
      </header>

      <section className="kpi-grid" aria-label="Price race summary">
        <div className="kpi-card">
          <div className="kpi-label">Large gaps</div>
          <div className="kpi-value">{intFmt(hot)}</div>
          <div className="kpi-insight">Middle prices more than ~15% apart</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Competing</div>
          <div className="kpi-value">{intFmt(competing)}</div>
          <div className="kpi-insight">About 5% to 15% apart</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Similar</div>
          <div className="kpi-value">{intFmt(aligned)}</div>
          <div className="kpi-insight">Within about 5%</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">
            Cheaper middle price
            <InfoTip
              plain="How many aisles have a lower middle (median) shelf price at Coles versus Woolworths. Only aisles we can compare on both sides."
              methodsId="price-race"
              onOpenMethods={onOpenMethods}
            />
          </div>
          <div className="kpi-value">
            <span className="coles-text">{intFmt(colesCheaper)}</span>
            <span className="vs"> / </span>
            <span className="ww-text">{intFmt(wwCheaper)}</span>
          </div>
          <div className="kpi-insight">
            <DualBannerMarks />
          </div>
        </div>
      </section>

      <section className="panel">
        <h2>Price race table</h2>
        <p className="support">
          Gap % compares Coles middle price to Woolworths middle price. Negative means Coles is
          cheaper on that middle price.
        </p>
        <div className="scoreboard-table-wrap">
          <table className="scoreboard-table">
            <thead>
              <tr>
                <th>{grainTitle(grain)}</th>
                <th>Status</th>
                <th>Cheaper</th>
                <th className="num">
                  <span className="th-with-mark">
                    Middle price
                    <DualBannerMarks />
                  </span>
                </th>
                <th className="num">Gap</th>
                <th className="num">
                  <span className="th-with-mark">
                    On special
                    <DualBannerMarks />
                  </span>
                </th>
                <th className="num">Products</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.id}
                  className="scoreboard-row"
                  tabIndex={0}
                  onClick={() => onSelect(r.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(r.id);
                    }
                  }}
                >
                  <td>
                    <strong>{r.shared_label}</strong>
                    <div className="muted tiny">{r.blurb}</div>
                  </td>
                  <td>
                    <StatusBadge status={r.status} />
                  </td>
                  <td>
                    <CheaperCell cheaper={r.cheaper_on_median} />
                  </td>
                  <td className="num">
                    <span className="coles-text">{money(r.coles_median_price)}</span>
                    {" / "}
                    <span className="ww-text">{money(r.ww_median_price)}</span>
                  </td>
                  <td className="num">
                    <div>{signedPct(r.median_gap_pct_coles_vs_ww)}</div>
                    <div className="muted tiny">{gapLabel(r.median_gap_pct_coles_vs_ww)}</div>
                  </td>
                  <td className="num">
                    <span className="coles-text">{pctFmt(r.coles_pct_promo)}</span>
                    {" / "}
                    <span className="ww-text">{pctFmt(r.ww_pct_promo)}</span>
                  </td>
                  <td className="num">
                    {intFmt(r.coles_skus)} / {intFmt(r.ww_skus)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function StatusBadge({ status }: { status: PriceCompetitionRow["status"] }) {
  const map: Record<PriceCompetitionRow["status"], { cls: string; label: string }> = {
    hot_gap: { cls: "badge badge-hot", label: "Large gap" },
    competing: { cls: "badge badge-warm", label: "Competing" },
    aligned: { cls: "badge badge-cool", label: "Similar" },
    not_comparable: { cls: "badge badge-muted", label: "Can’t compare yet" },
  };
  const m = map[status];
  return <span className={m.cls}>{m.label}</span>;
}

function CheaperCell({
  cheaper,
}: {
  cheaper: PriceCompetitionRow["cheaper_on_median"];
}) {
  if (!cheaper) return <span className="muted">—</span>;
  if (cheaper === "tie") return <span className="badge badge-contested">Tie</span>;
  return (
    <span className={cheaper === "Coles" ? "badge badge-coles" : "badge badge-ww"}>
      <BannerMark banner={cheaper} size="sm" />
      {cheaper}
    </span>
  );
}
