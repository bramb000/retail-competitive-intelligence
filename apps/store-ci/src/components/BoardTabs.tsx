import type { BoardTab } from "../lib/types";

const TABS: Array<{ id: BoardTab; label: string }> = [
  { id: "departments", label: "Categories" },
  { id: "dominance", label: "Dominance" },
  { id: "price", label: "Price race" },
  { id: "kvi", label: "Known value" },
];

interface Props {
  tab: BoardTab;
  onTab: (tab: BoardTab) => void;
}

export function BoardTabs({ tab, onTab }: Props) {
  return (
    <nav className="board-tabs" aria-label="Scoreboards">
      {TABS.map((t) => (
        <button
          key={t.id}
          type="button"
          className={tab === t.id ? "board-tab active" : "board-tab"}
          onClick={() => onTab(t.id)}
          aria-current={tab === t.id ? "page" : undefined}
        >
          {t.label}
        </button>
      ))}
    </nav>
  );
}
