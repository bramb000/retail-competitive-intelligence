import { InfoTip } from "./InfoTip";
import { BannerMark } from "./BannerMark";
import {
  gapLabel,
  intFmt,
  money,
  signedPct,
  type KnownValueRow,
  type StoreCiData,
} from "../lib/types";

interface Props {
  data: StoreCiData;
  locationName: string;
  onOpenMethods: (sectionId?: string) => void;
}

export function KnownValueBoard({ data, locationName, onOpenMethods }: Props) {
  const rows = data.scoreboards?.known_value ?? [];
  const s = data.scoreboards?.known_value_summary ?? {
    defined: 0,
    both_priced: 0,
    comparable: 0,
    not_comparable: 0,
    coles_cheaper: 0,
    ww_cheaper: 0,
    ties: 0,
    coles_only: 0,
    ww_only: 0,
  };

  return (
    <>
      <header className="hero">
        <p className="eyebrow">Price perception · {locationName}</p>
        <h1>Known value products</h1>
        <p>
          Everyday staples shoppers use to judge whether a store feels expensive. We only call a
          winner when pack sizes are similar, or when both sides publish a matching unit price
          (for example $/L). Different bottle sizes are shown as <em>not comparable</em>.
        </p>
        <p className="hero-methods-link">
          <button type="button" className="text-link" onClick={() => onOpenMethods("known-value")}>
            How we keep pack sizes fair
          </button>
        </p>
      </header>

      <section className="kpi-grid" aria-label="KVI summary">
        <div className="kpi-card">
          <div className="kpi-label">
            Fair comparisons
            <InfoTip
              plain="Staples where both sides have a product and pack sizes (or unit prices) are close enough to compare."
              methodsId="known-value"
              onOpenMethods={onOpenMethods}
            />
          </div>
          <div className="kpi-value">
            {intFmt(s.comparable ?? 0)}
            <span className="vs"> / {intFmt(s.defined)}</span>
          </div>
          <div className="kpi-insight">
            {intFmt(s.not_comparable ?? 0)} skipped for size mismatch
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">
            <BannerMark banner="Coles" size="sm" />
            Cheaper
          </div>
          <div className="kpi-value coles-text">{intFmt(s.coles_cheaper)}</div>
          <div className="kpi-insight">Of fair comparisons only</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">
            <BannerMark banner="Woolworths" size="sm" />
            Cheaper
          </div>
          <div className="kpi-value ww-text">{intFmt(s.ww_cheaper)}</div>
          <div className="kpi-insight">Of fair comparisons only</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Still one-sided</div>
          <div className="kpi-value">{intFmt(s.coles_only + s.ww_only)}</div>
          <div className="kpi-insight">
            Coles {intFmt(s.coles_only)} · WW {intFmt(s.ww_only)}
          </div>
        </div>
      </section>

      <section className="panel">
        <h2>KVI ledger</h2>
        <p className="support">
          Target pack size is listed under each staple. Gap uses unit price when available, otherwise
          shelf price only if packs are within about 30% of each other.
        </p>
        <div className="scoreboard-table-wrap">
          <table className="scoreboard-table kvi-table">
            <thead>
              <tr>
                <th>Known value item</th>
                <th>Winner</th>
                <th>
                  <span className="th-with-mark">
                    <BannerMark banner="Coles" size="sm" />
                    Pick
                  </span>
                </th>
                <th className="num">Shelf</th>
                <th>
                  <span className="th-with-mark">
                    <BannerMark banner="Woolworths" size="sm" />
                    Pick
                  </span>
                </th>
                <th className="num">Shelf</th>
                <th className="num">Gap</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.kvi_id} className={r.cheaper === "not_comparable" ? "row-awaiting" : undefined}>
                  <td>
                    <strong>{r.label}</strong>
                    <div className="muted tiny">
                      {r.perception_role || "—"}
                      {r.target_pack ? ` · target ~${r.target_pack}` : ""}
                    </div>
                  </td>
                  <td>
                    <Winner row={r} />
                  </td>
                  <td>
                    <SkuCell side="coles" sku={r.coles} />
                  </td>
                  <td className="num coles-text">{money(r.coles?.price_now)}</td>
                  <td>
                    <SkuCell side="ww" sku={r.ww} />
                  </td>
                  <td className="num ww-text">{money(r.ww?.price_now)}</td>
                  <td className="num">
                    {r.comparable ? (
                      <>
                        <div>{signedPct(r.gap_pct_coles_vs_ww)}</div>
                        <div className="muted tiny">
                          {gapLabel(r.gap_pct_coles_vs_ww)}
                          {r.compare_basis === "unit_price" ? " · by unit price" : ""}
                          {r.compare_basis === "similar_pack" ? " · similar packs" : ""}
                        </div>
                      </>
                    ) : (
                      <span className="muted tiny">—</span>
                    )}
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

function Winner({ row }: { row: KnownValueRow }) {
  const { cheaper } = row;
  if (!cheaper) return <span className="muted">Missing</span>;
  if (cheaper === "not_comparable") {
    return (
      <div>
        <span className="badge badge-hot">Not comparable</span>
        {row.incomparable_reason ? (
          <div className="muted tiny" style={{ marginTop: "0.35rem", maxWidth: "12rem" }}>
            {row.incomparable_reason}
          </div>
        ) : null}
      </div>
    );
  }
  if (cheaper === "tie") return <span className="badge badge-contested">Tie</span>;
  if (cheaper === "coles_only") return <span className="badge badge-muted">Coles only</span>;
  if (cheaper === "ww_only") return <span className="badge badge-muted">WW only</span>;
  return (
    <span className={cheaper === "Coles" ? "badge badge-coles" : "badge badge-ww"}>
      <BannerMark banner={cheaper} size="sm" />
      {cheaper}
    </span>
  );
}

function SkuCell({
  side,
  sku,
}: {
  side: "coles" | "ww";
  sku: KnownValueRow["coles"];
}) {
  if (!sku) return <span className="muted">Not found yet</span>;
  return (
    <div>
      <div className={side === "coles" ? "coles-text" : "ww-text"}>{sku.name}</div>
      <div className="muted tiny">
        {sku.brand ?? "—"}
        {sku.pack_label ? ` · ${sku.pack_label}` : ""}
        {sku.own_brand ? " · own brand" : ""}
        {sku.is_promo ? " · promo" : ""}
      </div>
      {sku.unit_price ? <div className="muted tiny">{sku.unit_price}</div> : null}
    </div>
  );
}
