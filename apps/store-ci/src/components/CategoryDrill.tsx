import { useMemo, useState } from "react";
import { InfoTip } from "./InfoTip";
import { BannerMark, BannerLegend } from "./BannerMark";
import {
  grainNoun,
  grainTitle,
  intFmt,
  bayFmt,
  money,
  pctFmt,
  skuRatio,
  type DepartmentRow,
  type Grain,
  type SkuRow,
  type StoreCiData,
} from "../lib/types";

interface Props {
  data: StoreCiData;
  dept: DepartmentRow;
  grain: Grain;
  locationName: string;
  onBack: () => void;
  onOpenMethods: (sectionId?: string) => void;
}

function skuInDept(s: SkuRow, dept: DepartmentRow, grain: Grain): boolean {
  if (grain === "subcategory") return s.subcategory_id === dept.id;
  if (s.category === dept.id) return true;
  const keys = dept.gold_keys ?? [];
  return keys.length > 0 && s.category != null && keys.includes(s.category);
}

function matchInDept(
  m: StoreCiData["matches"][number],
  dept: DepartmentRow,
  grain: Grain,
): boolean {
  if (grain === "subcategory") return m.subcategory_id === dept.id;
  if (m.category === dept.id) return true;
  const keys = dept.gold_keys ?? [];
  if (keys.length && m.category && keys.includes(m.category)) return true;
  return m.ww_l0 === dept.id || m.ww_l0 === dept.shared_label || m.ww_l0 === dept.ww_label;
}

export function CategoryDrill({ data, dept, grain, locationName, onBack, onOpenMethods }: Props) {
  const [retailer, setRetailer] = useState<"All" | "Coles" | "Woolworths">("All");
  const [search, setSearch] = useState("");
  const [promoOnly, setPromoOnly] = useState(false);
  const nounMany = grainNoun(grain);
  const nounOne = grainNoun(grain, "one");
  const title = grainTitle(grain);

  const skus = useMemo(() => {
    return data.skus.filter((s) => {
      if (!skuInDept(s, dept, grain)) return false;
      if (retailer !== "All" && s.retailer !== retailer) return false;
      if (promoOnly && !s.is_promo) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        const hay = `${s.name ?? ""} ${s.brand ?? ""} ${s.id}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [data.skus, dept, grain, retailer, promoOnly, search]);

  const matches = useMemo(
    () => data.matches.filter((m) => matchInDept(m, dept, grain)),
    [data.matches, dept, grain],
  );

  const maxSku = Math.max(dept.coles_skus, dept.ww_skus, 1);
  const maxBay = Math.max(dept.coles_pct_store_bays ?? 0, dept.ww_pct_store_bays ?? 0, 1);
  const maxPromo = Math.max(dept.coles_pct_promo ?? 0, dept.ww_pct_promo ?? 0, 1);
  const awaiting = dept.data_status === "awaiting_scrape";

  return (
    <>
      <header className="hero">
        <button type="button" className="chip" onClick={onBack}>
          ← Back to {nounMany}
        </button>
        <h1 style={{ marginTop: "1rem" }}>
          {dept.shared_label}
          {grain === "subcategory" && dept.parent_category ? (
            <span className="hero-loc"> · {dept.parent_category}</span>
          ) : null}
          <span className="hero-loc"> · {locationName}</span>
        </h1>
        <p className="blurb-line">{dept.blurb}</p>
        <p>
          <span className="banner-pill coles-pill">
            <BannerMark banner="Coles" size="sm" />
            {dept.coles_label}
          </span>{" "}
          <span className="banner-pill ww-pill">
            <BannerMark banner="Woolworths" size="sm" />
            {dept.ww_label}
          </span>
          {awaiting ? <span className="badge badge-muted">Data still filling in</span> : null}
        </p>
      </header>

      <BannerLegend />

      {awaiting ? (
        <section className="panel panel--cream waiting-panel">
          <h2>Data still filling in</h2>
          <p className="support">
            This {nounOne} is reserved in the taxonomy for {locationName}, but products are not
            available yet. Check back after the next data refresh.
          </p>
        </section>
      ) : null}

      <section className="kpi-grid" aria-label={`${title} KPIs`}>
        <div className="kpi-card">
          <div className="kpi-label">Assortment</div>
          <div className="kpi-value">
            {intFmt(dept.coles_skus)} <span className="vs">/</span> {intFmt(dept.ww_skus)}
          </div>
          <div className="kpi-insight">{skuRatio(dept.coles_skus, dept.ww_skus)}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">
            Bay share
            <InfoTip
              plain="Of all shelf bays in this store, what share belongs to this aisle? Mixed bays are split by product mix. Shown as a percent and as X of Y bay-equivalents."
              methodsId="bay-share"
              onOpenMethods={onOpenMethods}
            />
          </div>
          <div className="kpi-value">
            {pctFmt(dept.coles_pct_store_bays)} <span className="vs">/</span>{" "}
            {pctFmt(dept.ww_pct_store_bays)}
          </div>
          <div className="kpi-insight">
            {bayFmt(dept.coles_bay_count)} of {bayFmt(dept.coles_store_bay_count)} ·{" "}
            {bayFmt(dept.ww_bay_count)} of {bayFmt(dept.ww_store_bay_count)}
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">On promo</div>
          <div className="kpi-value">
            {pctFmt(dept.coles_pct_promo)} <span className="vs">/</span> {pctFmt(dept.ww_pct_promo)}
          </div>
          <div className="kpi-insight">Coles / WW</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Median price</div>
          <div className="kpi-value">
            {money(dept.coles_median_price)} <span className="vs">/</span>{" "}
            {money(dept.ww_median_price)}
          </div>
          <div className="kpi-insight">{intFmt(matches.length)} fuzzy matches</div>
        </div>
      </section>

      <section className="panel">
        <h2>Head-to-head</h2>
        <p className="support">Orange = Coles · Green = Woolworths. Bars scaled within this {nounOne}.</p>
        <CompareBar
          label="SKUs"
          coles={dept.coles_skus}
          ww={dept.ww_skus}
          max={maxSku}
          format={intFmt}
        />
        <CompareBar
          label="Bay share %"
          coles={dept.coles_pct_store_bays}
          ww={dept.ww_pct_store_bays}
          max={maxBay}
          format={(n) => pctFmt(n)}
        />
        <CompareBar
          label="Promo %"
          coles={dept.coles_pct_promo}
          ww={dept.ww_pct_promo}
          max={maxPromo}
          format={(n) => pctFmt(n)}
        />
      </section>

      {matches.length > 0 ? (
        <section className="panel panel--cream">
          <h2>Matched examples</h2>
          <p className="support">
            {grain === "subcategory"
              ? "Fuzzy brand+name pairs that helped map Coles SKUs into this Woolworths-style subcategory."
              : "Fuzzy brand+name pairs we believe are the same product in both stores within this aisle family."}
            {" "}
            <button type="button" className="text-link" onClick={() => onOpenMethods("overlap")}>
              How matching works
            </button>
          </p>
          <ul className="match-examples">
            {matches.slice(0, 12).map((m) => (
              <li key={`${m.coles_id}-${m.ww_id}`}>
                <strong>{m.brand ?? "—"}</strong>
                <span className="muted">
                  {" "}
                  · Coles {m.coles_name} ↔ WW {m.ww_name}
                  {m.score != null ? ` (${m.score.toFixed(2)})` : ""}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="panel" id="sku-explorer">
        <h2>SKUs</h2>
        <div className="filter-bar drill-filters">
          <span className="label">Retailer</span>
          {(["All", "Coles", "Woolworths"] as const).map((r) => (
            <button
              key={r}
              type="button"
              className={retailer === r ? "chip chip-clear" : "chip"}
              onClick={() => setRetailer(r)}
            >
              {r}
            </button>
          ))}
          <button
            type="button"
            className={promoOnly ? "chip chip-clear" : "chip"}
            onClick={() => setPromoOnly((v) => !v)}
          >
            Promo only
          </button>
          <input
            className="search-input"
            placeholder="Search name, brand, id…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search SKUs"
          />
          <span className="muted">{intFmt(skus.length)} shown</span>
        </div>
        <div className="sku-table-wrap">
          <table className="sku-table">
            <thead>
              <tr>
                <th>Retailer</th>
                <th>Brand</th>
                <th>Name</th>
                <th className="num">Price</th>
                <th>Promo</th>
                <th>Bay</th>
              </tr>
            </thead>
            <tbody>
              {skus.slice(0, 400).map((s) => (
                <SkuRowTr key={`${s.retailer}-${s.id}`} s={s} />
              ))}
            </tbody>
          </table>
          {skus.length > 400 ? (
            <p className="source">Showing first 400 of {intFmt(skus.length)}. Narrow search to see more.</p>
          ) : null}
        </div>
      </section>
    </>
  );
}

function CompareBar({
  label,
  coles,
  ww,
  max,
  format,
}: {
  label: string;
  coles: number | null;
  ww: number | null;
  max: number;
  format: (n: number | null | undefined) => string;
}) {
  const c = coles ?? 0;
  const w = ww ?? 0;
  return (
    <div className="compare-row">
      <div className="compare-label">{label}</div>
      <div className="compare-bars">
        <div className="bar-track">
          <div className="bar coles-bar" style={{ width: `${(c / max) * 100}%` }} />
          <span className="bar-val coles-text">{format(coles)}</span>
        </div>
        <div className="bar-track">
          <div className="bar ww-bar" style={{ width: `${(w / max) * 100}%` }} />
          <span className="bar-val ww-text">{format(ww)}</span>
        </div>
      </div>
    </div>
  );
}

function SkuRowTr({ s }: { s: SkuRow }) {
  return (
    <tr>
      <td className={s.retailer === "Coles" ? "coles-text" : "ww-text"}>{s.retailer}</td>
      <td>{s.brand ?? "—"}</td>
      <td>{s.name ?? "—"}</td>
      <td className="num">{money(s.price_now)}</td>
      <td>{s.is_promo ? "Yes" : "—"}</td>
      <td className="muted">{s.bay_key ?? "—"}</td>
    </tr>
  );
}
