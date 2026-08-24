interface NavPillProps {
  locationName: string;
  stores: Record<string, string>;
  view: "scoreboard" | "methods";
  onView: (view: "scoreboard" | "methods") => void;
  categoryLabel?: string | null;
}

export function NavPill({ locationName, stores, view, onView, categoryLabel }: NavPillProps) {
  return (
    <div className="nav-pill" role="banner">
      <strong>Retail CI</strong>
      <span>
        Category × location · {locationName}
        {categoryLabel ? ` · ${categoryLabel}` : ""} · Coles {stores.Coles} / WW {stores.Woolworths}
      </span>
      <nav className="nav-pill-links" aria-label="Views">
        <button
          type="button"
          className={view === "scoreboard" ? "nav-link active" : "nav-link"}
          onClick={() => onView("scoreboard")}
          aria-current={view === "scoreboard" ? "page" : undefined}
        >
          Scoreboard
        </button>
        <button
          type="button"
          className={view === "methods" ? "nav-link active" : "nav-link"}
          onClick={() => onView("methods")}
          aria-current={view === "methods" ? "page" : undefined}
        >
          How it works
        </button>
      </nav>
    </div>
  );
}
