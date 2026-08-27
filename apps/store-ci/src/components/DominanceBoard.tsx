import { ScoreboardRowMeta } from "./ScoreboardRowMeta";
import { InfoTip } from "./InfoTip";
import { BannerMark, DualBannerMarks } from "./BannerMark";
import { DominanceVenn } from "./DominanceVenn";
import { intFmt, pctFmt, type DominanceRow, type Grain, type StoreCiData, grainNoun, grainTitle } from "../lib/types";

interface Props {
  data: StoreCiData;
  grain: Grain;
  locationName: string;
  onSelect: (deptId: string) => void;
  onOpenMethods: (sectionId?: string) => void;
}

export function DominanceBoard({ data, grain, locationName, onSelect, onOpenMethods }: Props) {
  const rows = data.scoreboards?.dominance ?? [];
  const colesWins = rows.filter((r) => r.dominant === "Coles").length;
  const wwWins = rows.filter((r) => r.dominant === "Woolworths").length;
  const contested = rows.filter((r) => r.verdict === "contested").length;
  const nounMany = grainNoun(grain);
  const nounOne = grainNoun(grain, "one");

  return (
    <>
      <header className="hero">
        <p className="eyebrow">
          {grainTitle(grain)} · {locationName}
        </p>
        <h1>Who owns the aisle</h1>
        <p>
          Shelf space first, then assortment share at {locationName}. Thin coverage on one side
          often shows as one-sided until both banners are complete.
        </p>
        <p className="hero-methods-link">
          <button type="button" className="text-link" onClick={() => onOpenMethods("dominance")}>
            How we decide who is winning a {nounOne}
          </button>
        </p>
      </header>

      <section className="kpi-grid" aria-label="Dominance summary">
        <div className="kpi-card">
          <div className="kpi-label">
            <BannerMark banner="Coles" size="sm" />
            Stronger
          </div>
          <div className="kpi-value coles-text">{intFmt(colesWins)}</div>
          <div className="kpi-insight">{nounMany.charAt(0).toUpperCase() + nounMany.slice(1)}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">
            <BannerMark banner="Woolworths" size="sm" />
            Stronger
          </div>
          <div className="kpi-value ww-text">{intFmt(wwWins)}</div>
          <div className="kpi-insight">{nounMany.charAt(0).toUpperCase() + nounMany.slice(1)}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Too close to call</div>
          <div className="kpi-value">{intFmt(contested)}</div>
          <div className="kpi-insight">Similar space and range</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">
            How we decide
            <InfoTip
              plain="We look at shelf space first. If that is close, we look at how big the range is as a share of the whole store."
              methodsId="dominance"
              onOpenMethods={onOpenMethods}
            />
          </div>
          <div className="kpi-value">Space, then range</div>
          <div className="kpi-insight">Shelf before product count</div>
        </div>
      </section>

      <DominanceVenn rows={rows} onSelect={onSelect} />

      <section className="panel">
        <h2>Dominance table</h2>
        <p className="support">Click a row to open the {nounOne} drill-in.</p>
        <div className="scoreboard-table-wrap">
          <table className="scoreboard-table">
            <thead>
              <tr>
                <th>{grainTitle(grain)}</th>
                <th>Dominant</th>
                <th>Why</th>
                <th className="num">
                  <span className="th-with-mark">
                    Bay share
                    <DualBannerMarks />
                  </span>
                </th>
                <th className="num">Bay gap</th>
                <th className="num">
                  <span className="th-with-mark">
                    Product % of store
                    <DualBannerMarks />
                  </span>
                </th>
                <th className="num">Product gap</th>
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
                  <td className="scoreboard-label-cell">
                    <strong>{r.shared_label}</strong>
                    <ScoreboardRowMeta blurb={r.blurb} grain={grain} />
                  </td>
                  <td>
                    <WinnerBadge row={r} />
                  </td>
                  <td className="muted">{verdictLabel(r.verdict)}</td>
                  <td className="num">
                    <span className="coles-text">{pctFmt(r.coles_pct_store_bays)}</span>
                    {" / "}
                    <span className="ww-text">{pctFmt(r.ww_pct_store_bays)}</span>
                  </td>
                  <td className="num">{signedPp(r.bay_gap_pp)}</td>
                  <td className="num">
                    <span className="coles-text">{pctFmt(r.coles_pct_store_skus)}</span>
                    {" / "}
                    <span className="ww-text">{pctFmt(r.ww_pct_store_skus)}</span>
                  </td>
                  <td className="num">{signedPp(r.sku_gap_pp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function WinnerBadge({ row }: { row: DominanceRow }) {
  if (row.verdict === "contested") {
    return <span className="badge badge-contested">Contested</span>;
  }
  if (!row.dominant) return <span className="muted">—</span>;
  return (
    <span className={row.dominant === "Coles" ? "badge badge-coles" : "badge badge-ww"}>
      <BannerMark banner={row.dominant} size="sm" />
      {row.dominant}
    </span>
  );
}

function verdictLabel(v: DominanceRow["verdict"]): string {
  switch (v) {
    case "bay_dominant":
      return "More shelf space";
    case "assortment_dominant":
      return "Bigger range share";
    case "one_sided":
      return "Only one side listed";
    case "contested":
      return "Too close to call";
    default:
      return v;
  }
}

function signedPp(n: number | null): string {
  if (n == null) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)} pp`;
}
