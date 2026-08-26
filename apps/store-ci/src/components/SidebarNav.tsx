import type { BoardTab } from "../lib/types";
import type { AppView } from "../lib/hashRoute";

const BOARDS: Array<{ id: BoardTab; label: string; hint: string }> = [
  { id: "departments", label: "Overview", hint: "Aisle space and range" },
  { id: "dominance", label: "Dominance", hint: "Who owns each aisle" },
  { id: "price", label: "Price", hint: "Where prices diverge" },
  { id: "kvi", label: "Staples", hint: "Everyday price perception" },
  { id: "macrospace", label: "Macrospace", hint: "Bay layout map" },
];

interface Props {
  view: AppView;
  board: BoardTab;
  onBoard: (board: BoardTab) => void;
  onMethods: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
}

export function SidebarNav({
  view,
  board,
  onBoard,
  onMethods,
  mobileOpen,
  onCloseMobile,
}: Props) {
  return (
    <>
      {mobileOpen ? (
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label="Close navigation"
          onClick={onCloseMobile}
        />
      ) : null}
      <aside
        className={`app-sidebar${mobileOpen ? " app-sidebar--open" : ""}`}
        aria-label="Main navigation"
      >
        <div className="sidebar-brand">
          <strong className="sidebar-wordmark">Retail CI</strong>
          <span className="sidebar-tagline muted tiny">Competitive intelligence</span>
        </div>

        <nav className="sidebar-nav" aria-label="Boards">
          <p className="sidebar-section-label">Boards</p>
          {BOARDS.map((item) => {
            const active = view === "scoreboard" && board === item.id;
            return (
              <button
                key={item.id}
                type="button"
                className={active ? "sidebar-link active" : "sidebar-link"}
                aria-current={active ? "page" : undefined}
                onClick={() => {
                  onBoard(item.id);
                  onCloseMobile();
                }}
              >
                <span className="sidebar-link-label">{item.label}</span>
                <span className="sidebar-link-hint">{item.hint}</span>
              </button>
            );
          })}
        </nav>

        <nav className="sidebar-nav sidebar-nav--footer" aria-label="Documentation">
          <p className="sidebar-section-label">Docs</p>
          <button
            type="button"
            className={view === "methods" ? "sidebar-link active" : "sidebar-link"}
            aria-current={view === "methods" ? "page" : undefined}
            onClick={() => {
              onMethods();
              onCloseMobile();
            }}
          >
            <span className="sidebar-link-label">Methods</span>
            <span className="sidebar-link-hint">How numbers are built</span>
          </button>
        </nav>
      </aside>
    </>
  );
}
