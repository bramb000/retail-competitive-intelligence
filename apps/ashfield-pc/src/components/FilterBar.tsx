import type { DashboardFilters } from "../lib/types";
import { activeFilterChips, emptyFilters } from "../lib/types";

interface Props {
  filters: DashboardFilters;
  onChange: (next: DashboardFilters) => void;
}

export function FilterBar({ filters, onChange }: Props) {
  const chips = activeFilterChips(filters);
  if (chips.length === 0) {
    return (
      <div className="filter-bar" aria-live="polite">
        <span className="label">Filters</span>
        <span className="chip-idle">None — click a chart region, bay, or subcategory to drill in</span>
      </div>
    );
  }

  return (
    <div className="filter-bar" aria-live="polite">
      <span className="label">Active filters</span>
      {chips.map((c) => (
        <button
          key={c.key + c.label}
          type="button"
          className="chip"
          onClick={() => {
            if (c.key === "search") onChange({ ...filters, search: "" });
            else if (c.key === "side") onChange({ ...filters, side: null });
            else if (c.key === "bayKey") onChange({ ...filters, bayKey: null });
            else if (c.key === "subcategory") onChange({ ...filters, subcategory: null });
            else if (c.key === "retailer") onChange({ ...filters, retailer: null });
            else if (c.key === "matchPairId") onChange({ ...filters, matchPairId: null });
          }}
          title="Remove filter"
        >
          {c.label} ×
        </button>
      ))}
      <button type="button" className="chip chip-clear" onClick={() => onChange(emptyFilters())}>
        Clear all
      </button>
    </div>
  );
}
