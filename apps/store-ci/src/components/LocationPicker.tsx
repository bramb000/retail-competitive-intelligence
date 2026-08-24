import type { LocationMeta } from "../lib/types";

interface Props {
  locations: LocationMeta[];
  activeId: string;
  onSelect: (id: string) => void;
}

/** Location dimension of category × location. Only Ashfield is live today. */
export function LocationPicker({ locations, activeId, onSelect }: Props) {
  return (
    <div className="location-picker" role="group" aria-label="Location">
      <span className="location-picker-label">Location</span>
      {locations.map((loc) => {
        const active = loc.id === activeId;
        const enabled = loc.active !== false;
        return (
          <button
            key={loc.id}
            type="button"
            className={active ? "location-chip active" : "location-chip"}
            disabled={!enabled}
            aria-label={enabled ? loc.name : `${loc.name} — coming soon`}
            onClick={() => enabled && onSelect(loc.id)}
          >
            {loc.name}
            {loc.state ? <span className="loc-state">{loc.state}</span> : null}
          </button>
        );
      })}
      {locations.length <= 1 ? (
        <span className="location-hint muted">More suburbs when scrape expands</span>
      ) : null}
    </div>
  );
}
