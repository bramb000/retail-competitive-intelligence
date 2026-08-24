import { useMemo, useState } from "react";
import { BannerMark } from "./BannerMark";
import { intFmt, type DominanceRow } from "../lib/types";

type Region = "coles" | "contested" | "ww";

interface Props {
  rows: DominanceRow[];
  onSelect: (deptId: string) => void;
}

export function DominanceVenn({ rows, onSelect }: Props) {
  const [focus, setFocus] = useState<Region | null>(null);

  const buckets = useMemo(() => {
    const coles: DominanceRow[] = [];
    const ww: DominanceRow[] = [];
    const contested: DominanceRow[] = [];
    for (const r of rows) {
      if (r.verdict === "contested") contested.push(r);
      else if (r.dominant === "Coles") coles.push(r);
      else if (r.dominant === "Woolworths") ww.push(r);
    }
    const byStrength = (a: DominanceRow, b: DominanceRow) => (b.strength ?? 0) - (a.strength ?? 0);
    coles.sort(byStrength);
    ww.sort(byStrength);
    contested.sort((a, b) => a.shared_label.localeCompare(b.shared_label));
    return { coles, ww, contested };
  }, [rows]);

  const active =
    focus === "coles" ? buckets.coles : focus === "ww" ? buckets.ww : focus === "contested" ? buckets.contested : null;

  return (
    <section className="panel panel--lilac dominance-venn-panel">
      <h2>Dominance at a glance</h2>
      <p className="support">
        Left = Coles stronger · middle = too close to call · right = Woolworths stronger. Click a
        circle or a category chip to open the aisle.
      </p>

      <div className="dominance-venn">
        <svg
          className="dominance-venn-svg"
          viewBox="0 0 440 240"
          role="img"
          aria-label="Dominance Venn diagram"
        >
          <title>Category dominance Venn</title>
          <circle
            className="venn-circle venn-circle--coles"
            cx="155"
            cy="120"
            r="100"
            data-dimmed={focus != null && focus !== "coles" ? "true" : "false"}
            tabIndex={0}
            role="button"
            aria-label={`Coles stronger: ${buckets.coles.length} aisles`}
            onClick={() => setFocus((f) => (f === "coles" ? null : "coles"))}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setFocus((f) => (f === "coles" ? null : "coles"));
              }
            }}
          />
          <circle
            className="venn-circle venn-circle--ww"
            cx="285"
            cy="120"
            r="100"
            data-dimmed={focus != null && focus !== "ww" ? "true" : "false"}
            tabIndex={0}
            role="button"
            aria-label={`Woolworths stronger: ${buckets.ww.length} aisles`}
            onClick={() => setFocus((f) => (f === "ww" ? null : "ww"))}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setFocus((f) => (f === "ww" ? null : "ww"));
              }
            }}
          />
          {/* Contested hit target in the overlap */}
          <ellipse
            className="venn-overlap-hit"
            cx="220"
            cy="120"
            rx="48"
            ry="78"
            data-dimmed={focus != null && focus !== "contested" ? "true" : "false"}
            tabIndex={0}
            role="button"
            aria-label={`Contested: ${buckets.contested.length} aisles`}
            onClick={() => setFocus((f) => (f === "contested" ? null : "contested"))}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setFocus((f) => (f === "contested" ? null : "contested"));
              }
            }}
          />

          <text className="venn-count venn-count--coles" x="115" y="108" textAnchor="middle">
            {intFmt(buckets.coles.length)}
          </text>
          <text className="venn-label" x="115" y="132" textAnchor="middle">
            Coles
          </text>
          <text className="venn-sub" x="115" y="150" textAnchor="middle">
            stronger
          </text>

          <text className="venn-count venn-count--mid" x="220" y="108" textAnchor="middle">
            {intFmt(buckets.contested.length)}
          </text>
          <text className="venn-label" x="220" y="132" textAnchor="middle">
            Contested
          </text>

          <text className="venn-count venn-count--ww" x="325" y="108" textAnchor="middle">
            {intFmt(buckets.ww.length)}
          </text>
          <text className="venn-label" x="325" y="132" textAnchor="middle">
            Woolworths
          </text>
          <text className="venn-sub" x="325" y="150" textAnchor="middle">
            stronger
          </text>
        </svg>

        <div className="venn-chip-cols" aria-label="Categories by dominance region">
          <VennColumn
            title="Coles"
            mark="Coles"
            rows={buckets.coles}
            active={focus === "coles" || focus == null}
            onSelect={onSelect}
          />
          <VennColumn
            title="Contested"
            rows={buckets.contested}
            active={focus === "contested" || focus == null}
            onSelect={onSelect}
          />
          <VennColumn
            title="Woolworths"
            mark="Woolworths"
            rows={buckets.ww}
            active={focus === "ww" || focus == null}
            onSelect={onSelect}
          />
        </div>
      </div>

      {active && focus ? (
        <p className="venn-focus-hint muted tiny">
          Showing {active.length} {focus === "contested" ? "contested" : `${focus === "coles" ? "Coles" : "Woolworths"}-led`}{" "}
          aisle{active.length === 1 ? "" : "s"}. Click the circle again to clear.
        </p>
      ) : null}
    </section>
  );
}

function VennColumn({
  title,
  mark,
  rows,
  active,
  onSelect,
}: {
  title: string;
  mark?: "Coles" | "Woolworths";
  rows: DominanceRow[];
  active: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <div className={`venn-col${active ? "" : " venn-col--dim"}`}>
      <div className="venn-col-title">
        {mark ? <BannerMark banner={mark} size="sm" /> : null}
        {title}
        <span className="muted"> · {intFmt(rows.length)}</span>
      </div>
      <ul className="venn-chips">
        {rows.length === 0 ? <li className="muted tiny">None yet</li> : null}
        {rows.map((r) => (
          <li key={r.id}>
            <button type="button" className="venn-chip" onClick={() => onSelect(r.id)}>
              {r.shared_label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
