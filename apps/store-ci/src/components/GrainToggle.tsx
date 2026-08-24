import type { Grain } from "../lib/types";

interface Props {
  grain: Grain;
  onGrain: (grain: Grain) => void;
}

const OPTIONS: Array<{ id: Grain; label: string; hint: string }> = [
  { id: "category", label: "Category", hint: "Aisle family (Dairy, Pantry, …)" },
  { id: "subcategory", label: "Subcategory", hint: "Woolworths-style shelf groups" },
];

export function GrainToggle({ grain, onGrain }: Props) {
  const active = OPTIONS.find((o) => o.id === grain) ?? OPTIONS[0];
  return (
    <div className="grain-toggle-wrap">
      <div className="location-picker grain-toggle" role="group" aria-label="Scoreboard grain">
        <span className="location-picker-label">View by</span>
        {OPTIONS.map((opt) => (
          <button
            key={opt.id}
            type="button"
            className={grain === opt.id ? "location-chip active" : "location-chip"}
            aria-pressed={grain === opt.id}
            aria-describedby="grain-hint"
            onClick={() => onGrain(opt.id)}
          >
            {opt.label}
          </button>
        ))}
      </div>
      {/* Native title= tooltips do not work reliably on iPad — keep hint visible */}
      <p id="grain-hint" className="grain-hint">
        {active.hint}
      </p>
    </div>
  );
}
