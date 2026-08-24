interface NavPillProps {
  category: string;
  suburb: string;
  stores: Record<string, string>;
  view: "dashboard" | "methods";
  onView: (view: "dashboard" | "methods") => void;
}

export function NavPill({ category, suburb, stores, view, onView }: NavPillProps) {
  return (
    <div className="nav-pill" role="banner">
      <strong>Retail CI</strong>
      <span>
        {category} · {suburb} · Coles {stores.Coles} / WW {stores.Woolworths}
      </span>
      <nav className="nav-pill-links" aria-label="Views">
        <button
          type="button"
          className={view === "dashboard" ? "nav-link active" : "nav-link"}
          onClick={() => onView("dashboard")}
          aria-current={view === "dashboard" ? "page" : undefined}
        >
          Dashboard
        </button>
        <button
          type="button"
          className={view === "methods" ? "nav-link active" : "nav-link"}
          onClick={() => onView("methods")}
          aria-current={view === "methods" ? "page" : undefined}
        >
          Methods
        </button>
      </nav>
    </div>
  );
}
