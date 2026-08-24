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
  return (
    <div className="location-picker grain-toggle" role="group" aria-label="Scoreboard grain">
      <span className="location-picker-label">View by</span>
      {OPTIONS.map((opt) => (
        <button
          key={opt.id}
          type="button"
          className={grain === opt.id ? "location-chip active" : "location-chip"}
          title={opt.hint}
          onClick={() => onGrain(opt.id)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
