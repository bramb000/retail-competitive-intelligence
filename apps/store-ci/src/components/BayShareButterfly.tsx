import { useMemo } from "react";
import { BannerMark } from "./BannerMark";
import { InfoTip } from "./InfoTip";
import { pctFmt, type DepartmentRow, type Grain, grainNoun } from "../lib/types";

interface Props {
  departments: DepartmentRow[];
  locationName: string;
  grain: Grain;
  onSelect: (deptId: string) => void;
  onOpenMethods: (sectionId?: string) => void;
}

export function BayShareButterfly({ departments, locationName, grain, onSelect, onOpenMethods }: Props) {
  const rows = useMemo(() => {
    return departments
      .map((d) => {
        const c = d.coles_pct_store_bays;
        const w = d.ww_pct_store_bays;
        const max = Math.max(c ?? 0, w ?? 0);
        return { d, c, w, max };
      })
      .filter((r) => r.c != null || r.w != null)
      .sort((a, b) => b.max - a.max || a.d.shared_label.localeCompare(b.d.shared_label));
  }, [departments]);

  const nounOne = grainNoun(grain, "one");
  const axisMax = useMemo(() => {
    const peak = Math.max(1, ...rows.map((r) => r.max));
    const step = peak <= 10 ? 2 : peak <= 20 ? 5 : 10;
    return Math.ceil(peak / step) * step;
  }, [rows]);

  if (rows.length === 0) return null;

  return (
    <section className="panel bay-butterfly-panel">
      <h2>
        Share of store bays
        <InfoTip
          plain={`Of each store’s identified shelf bays, what share (in bay-equivalents) belongs to this ${nounOne}? Mixed bays are split by product mix — not the same as ‘dedicated’ exclusive bays. Coles left, Woolworths right.`}
          methodsId="bay-share"
          onOpenMethods={onOpenMethods}
        />
      </h2>
      <p className="support">
        At {locationName}, each bar is that {nounOne}’s share of the banner’s bay inventory (fractional).
        Example: Woolworths Pantry near 40% means about two-fifths of WW’s counted bay-equivalents,
        not that 40% of bays are pantry-only. Click a row to drill in.
      </p>

      <div className="butterfly" role="img" aria-label="Bay share butterfly chart">
        <div className="butterfly-head-row" aria-hidden="true">
          <div className="butterfly-side butterfly-side--coles">
            <span className="butterfly-val-slot" />
            <span className="butterfly-brand coles-text">
              <BannerMark banner="Coles" size="sm" /> Coles
            </span>
          </div>
          <div className="butterfly-label-col">Category</div>
          <div className="butterfly-side butterfly-side--ww">
            <span className="butterfly-brand ww-text">
              <BannerMark banner="Woolworths" size="sm" /> Woolworths
            </span>
            <span className="butterfly-val-slot" />
          </div>
        </div>

        <div className="butterfly-axis-row" aria-hidden="true">
          <div className="butterfly-side butterfly-side--coles">
            <span className="butterfly-val-slot" />
            <div className="butterfly-track-axis">
              <span>{pctFmt(axisMax, 0)}</span>
              <span>{pctFmt(axisMax / 2, 0)}</span>
              <span>0%</span>
            </div>
          </div>
          <div className="butterfly-label-col" />
          <div className="butterfly-side butterfly-side--ww">
            <div className="butterfly-track-axis">
              <span>0%</span>
              <span>{pctFmt(axisMax / 2, 0)}</span>
              <span>{pctFmt(axisMax, 0)}</span>
            </div>
            <span className="butterfly-val-slot" />
          </div>
        </div>

        <ul className="butterfly-list">
          {rows.map((r) => {
            const cPct = r.c == null ? 0 : (r.c / axisMax) * 100;
            const wPct = r.w == null ? 0 : (r.w / axisMax) * 100;
            return (
              <li key={r.d.id}>
                <button
                  type="button"
                  className="butterfly-row"
                  onClick={() => onSelect(r.d.id)}
                  aria-label={`${r.d.shared_label}: Coles ${pctFmt(r.c)}, Woolworths ${pctFmt(r.w)}`}
                >
                  <div className="butterfly-side butterfly-side--coles">
                    <span className="butterfly-val butterfly-val--coles">
                      {r.c == null ? "—" : pctFmt(r.c)}
                    </span>
                    <div className="butterfly-track">
                      <div
                        className="butterfly-bar butterfly-bar--coles"
                        style={{ width: `${cPct}%` }}
                      />
                    </div>
                  </div>

                  <div className="butterfly-label-col">
                    <span className="butterfly-cat">{r.d.shared_label}</span>
                  </div>

                  <div className="butterfly-side butterfly-side--ww">
                    <div className="butterfly-track">
                      <div
                        className="butterfly-bar butterfly-bar--ww"
                        style={{ width: `${wPct}%` }}
                      />
                    </div>
                    <span className="butterfly-val butterfly-val--ww">
                      {r.w == null ? "—" : pctFmt(r.w)}
                    </span>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
