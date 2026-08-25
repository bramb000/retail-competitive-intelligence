import { useMemo, useState, type KeyboardEvent } from "react";
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

  const toggleFocus = (region: Region) => setFocus((f) => (f === region ? null : region));

  const onRegionKey = (region: Region) => (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggleFocus(region);
    }
  };

  return (
    <section className="panel dominance-venn-panel">
      <h2>Dominance at a glance</h2>
      <p className="support">
        Left = Coles stronger · middle = too close to call · right = Woolworths stronger. Select a
        region to filter the lists; open a category chip to drill into that aisle.
      </p>

      <div className="dominance-venn">
        <svg
          className="dominance-venn-svg"
          viewBox="0 0 440 240"
          role="group"
          aria-label="Category dominance regions"
        >
          <circle
            className="venn-circle venn-circle--coles"
            cx="155"
            cy="120"
            r="100"
            data-dimmed={focus != null && focus !== "coles" ? "true" : "false"}
            data-active={focus === "coles" ? "true" : "false"}
            tabIndex={0}
            role="button"
            aria-pressed={focus === "coles"}
            aria-label={`Coles stronger: ${buckets.coles.length} aisles. Toggle to filter.`}
            onClick={() => toggleFocus("coles")}
            onKeyDown={onRegionKey("coles")}
          />
          <circle
            className="venn-circle venn-circle--ww"
            cx="285"
            cy="120"
            r="100"
            data-dimmed={focus != null && focus !== "ww" ? "true" : "false"}
            data-active={focus === "ww" ? "true" : "false"}
            tabIndex={0}
            role="button"
            aria-pressed={focus === "ww"}
            aria-label={`Woolworths stronger: ${buckets.ww.length} aisles. Toggle to filter.`}
            onClick={() => toggleFocus("ww")}
            onKeyDown={onRegionKey("ww")}
          />
          {/* Contested hit target in the overlap — neutral, not brand primary */}
          <ellipse
            className="venn-overlap-hit"
            cx="220"
            cy="120"
            rx="48"
            ry="78"
            data-dimmed={focus != null && focus !== "contested" ? "true" : "false"}
            data-active={focus === "contested" ? "true" : "false"}
            tabIndex={0}
            role="button"
            aria-pressed={focus === "contested"}
            aria-label={`Too close to call: ${buckets.contested.length} aisles. Toggle to filter.`}
            onClick={() => toggleFocus("contested")}
            onKeyDown={onRegionKey("contested")}
          />

          <text className="venn-count" x="115" y="108" textAnchor="middle" aria-hidden="true">
            {intFmt(buckets.coles.length)}
          </text>
          <text className="venn-label" x="115" y="132" textAnchor="middle" aria-hidden="true">
            Coles
          </text>
          <text className="venn-sub" x="115" y="150" textAnchor="middle" aria-hidden="true">
            stronger
          </text>

          <text className="venn-count" x="220" y="108" textAnchor="middle" aria-hidden="true">
            {intFmt(buckets.contested.length)}
          </text>
          <text className="venn-label" x="220" y="132" textAnchor="middle" aria-hidden="true">
            Contested
          </text>

          <text className="venn-count" x="325" y="108" textAnchor="middle" aria-hidden="true">
            {intFmt(buckets.ww.length)}
          </text>
          <text className="venn-label" x="325" y="132" textAnchor="middle" aria-hidden="true">
            Woolworths
          </text>
          <text className="venn-sub" x="325" y="150" textAnchor="middle" aria-hidden="true">
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
        <p className="venn-focus-hint muted tiny" aria-live="polite">
          Showing {active.length}{" "}
          {focus === "contested"
            ? "contested"
            : `${focus === "coles" ? "Coles" : "Woolworths"}-led`}{" "}
          aisle{active.length === 1 ? "" : "s"}. Select the region again to clear.
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
    <div
      className={`venn-col${active ? "" : " venn-col--dim"}`}
      aria-hidden={active ? undefined : true}
    >
      <div className="venn-col-title">
        {mark ? <BannerMark banner={mark} size="sm" /> : null}
        {title}
        <span className="muted"> · {intFmt(rows.length)}</span>
      </div>
      <ul className="venn-chips">
        {rows.length === 0 ? <li className="muted tiny">None yet</li> : null}
        {rows.map((r) => (
          <li key={r.id}>
            <button
              type="button"
              className="venn-chip"
              tabIndex={active ? 0 : -1}
              disabled={!active}
              onClick={() => onSelect(r.id)}
            >
              {r.shared_label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
