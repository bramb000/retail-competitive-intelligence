import { ScoreboardRowMeta } from "./ScoreboardRowMeta";
import { InfoTip } from "./InfoTip";
import { BannerMark, DualBannerMarks } from "./BannerMark";
import { BayShareButterfly } from "./BayShareButterfly";
import { DataNotes } from "./DataNotes";
import {
  grainNoun,
  grainTitle,
  intFmt,
  pctFmt,
  skuRatio,
  bayFmt,
  type DepartmentRow,
  type Grain,
  type StoreCiData,
} from "../lib/types";

interface Props {
  data: StoreCiData;
  grain: Grain;
  locationName: string;
  onSelect: (deptId: string) => void;
  onOpenMethods: (sectionId?: string) => void;
}

export function StoreScoreboard({ data, grain, locationName, onSelect, onOpenMethods }: Props) {
  const t = data.store_totals;
  const waiting = data.meta.status !== "ready" || data.departments.length === 0;
  const awaiting =
    grain === "category"
      ? (t.departments_awaiting ?? data.departments.filter((d) => d.data_status === "awaiting_scrape").length)
      : data.departments.filter((d) => d.data_status === "awaiting_scrape").length;
  const ready = data.departments.length - awaiting;
  const rowCount = data.departments.length;
  const nounMany = grainNoun(grain);
  const nounOne = grainNoun(grain, "one");
  const title = grainTitle(grain);

  return (
    <>
      <header className="hero">
        <p className="eyebrow">
          {title} · {locationName}
        </p>
        <h1>Aisle space and range</h1>
        <p>
          {grain === "category"
            ? "Compare how much shelf each banner gives major aisle families — lined up even when Coles and Woolworths use different names."
            : "Finer shelf groups using shared labels. Woolworths is native; Coles is mapped where we can."}
        </p>
        <p className="hero-methods-link">
          <button type="button" className="text-link" onClick={() => onOpenMethods("grain")}>
            How this view is built
          </button>
          {" · "}
          <button type="button" className="text-link" onClick={() => onOpenMethods("overlap")}>
            How we match products
          </button>
        </p>
      </header>

      <DataNotes notes={data.meta.caveats} />

      <section className="kpi-grid store-kpi" aria-label="Location totals">
        <div className="kpi-card">
          <div className="kpi-label">
            <BannerMark banner="Coles" size="sm" />
            Products
            <InfoTip
              plain="How many Coles products we currently have for this suburb."
              onOpenMethods={onOpenMethods}
            />
          </div>
          <div className="kpi-value coles-text">{intFmt(t.coles_skus)}</div>
          <div className="kpi-insight">Assigned to an aisle {intFmt(t.coles_mapped_skus)}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">
            <BannerMark banner="Woolworths" size="sm" />
            Products
            <InfoTip
              plain="How many Woolworths products we currently have for this suburb."
              onOpenMethods={onOpenMethods}
            />
          </div>
          <div className="kpi-value ww-text">{intFmt(t.ww_skus)}</div>
          <div className="kpi-insight">Assigned to an aisle {intFmt(t.ww_mapped_skus)}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">
            {title}
            <InfoTip
              plain={
                grain === "category"
                  ? "Every major aisle family we track in this suburb, even if collection is still running."
                  : "Every Woolworths-style subcategory we can map in this suburb. Coles is mapped onto the same labels; many Coles products still have no subcategory."
              }
              methodsId="grain"
              onOpenMethods={onOpenMethods}
            />
          </div>
          <div className="kpi-value">{intFmt(rowCount)}</div>
          <div className="kpi-insight">
            Ready {intFmt(ready)} · still filling in {intFmt(awaiting)}
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">
            Likely same products
            <InfoTip
              plain="Products that look like the same item at both retailers (same brand, very similar name). Real overlap is often higher."
              methodsId="overlap"
              onOpenMethods={onOpenMethods}
            />
          </div>
          <div className="kpi-value">{intFmt(t.matched_pairs)}</div>
          <div className="kpi-insight">A cautious count — not the full overlap</div>
        </div>
      </section>

      {waiting ? (
        <section className="panel panel--cream waiting-panel">
          <h2>Data still filling in</h2>
          <p className="support">
            Product and shelf coverage for {locationName} is not ready yet. Check back after the next
            data refresh.
          </p>
        </section>
      ) : (
        <>
          <BayShareButterfly
            departments={data.departments}
            locationName={locationName}
            grain={grain}
            onSelect={onSelect}
            onOpenMethods={onOpenMethods}
          />
          <section className="panel scoreboard-panel">
            <h2>
              {nounMany.charAt(0).toUpperCase() + nounMany.slice(1)} at {locationName}
            </h2>
            <p className="support">Open a row to drill into that {nounOne}.</p>
            <div className="scoreboard-table-wrap">
              <table className="scoreboard-table">
                <thead>
                  <tr>
                    <th>{title}</th>
                    <th className="num">
                      <span className="th-with-mark">
                        Share of store bays
                        <DualBannerMarks />
                        <InfoTip
                          plain="Of all shelf bays we can identify in this store, what share belongs to this aisle? Mixed bays are split by product mix (fractional), shown as a percent and as X of Y bay-equivalents."
                          methodsId="bay-share"
                          onOpenMethods={onOpenMethods}
                        />
                      </span>
                    </th>
                    <th className="num">
                      <span className="th-with-mark">
                        On special
                        <DualBannerMarks />
                      </span>
                    </th>
                    <th>
                      <span className="th-with-mark">
                        <BannerMark banner="Coles" size="sm" />
                        Says
                      </span>
                    </th>
                    <th>
                      <span className="th-with-mark">
                        <BannerMark banner="Woolworths" size="sm" />
                        Says
                      </span>
                    </th>
                    <th className="num coles-text">Products</th>
                    <th className="num ww-text">Products</th>
                    <th className="num">Product ratio</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.departments.map((d) => (
                    <DeptRow key={d.id} dept={d} grain={grain} onSelect={onSelect} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}

function DeptRow({
  dept,
  grain,
  onSelect,
}: {
  dept: DepartmentRow;
  grain: Grain;
  onSelect: (id: string) => void;
}) {
  const awaiting = dept.data_status === "awaiting_scrape";
  return (
    <tr
      className={awaiting ? "scoreboard-row row-awaiting" : "scoreboard-row"}
      tabIndex={0}
      onClick={() => onSelect(dept.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(dept.id);
        }
      }}
    >
      <td className="scoreboard-label-cell">
        <strong>{dept.shared_label}</strong>
        <ScoreboardRowMeta
          blurb={dept.blurb}
          grain={grain}
          parentCategory={dept.parent_category}
        />
        {awaiting ? <div className="muted tiny">Data still filling in</div> : null}
      </td>
      <td className="num">
        <div>
          <span className="coles-text">{pctFmt(dept.coles_pct_store_bays)}</span>
          {" / "}
          <span className="ww-text">{pctFmt(dept.ww_pct_store_bays)}</span>
        </div>
        <div className="muted tiny">
          {bayOfStore(dept.coles_bay_count, dept.coles_store_bay_count)}
          {" · "}
          {bayOfStore(dept.ww_bay_count, dept.ww_store_bay_count)}
        </div>
      </td>
      <td className="num">
        <span className="coles-text">{pctFmt(dept.coles_pct_promo)}</span>
        {" / "}
        <span className="ww-text">{pctFmt(dept.ww_pct_promo)}</span>
      </td>
      <td className="coles-text">{dept.coles_label}</td>
      <td className="ww-text">{dept.ww_label}</td>
      <td className="num">{intFmt(dept.coles_skus)}</td>
      <td className="num">{intFmt(dept.ww_skus)}</td>
      <td className="num">{skuRatio(dept.coles_skus, dept.ww_skus)}</td>
      <td className="num">
        <button type="button" className="chip chip-clear" onClick={() => onSelect(dept.id)}>
          Open →
        </button>
      </td>
    </tr>
  );
}

function bayOfStore(owned: number | null | undefined, store: number | null | undefined): string {
  if (owned == null || store == null) return "—";
  return `${bayFmt(owned)} of ${bayFmt(store)}`;
}
