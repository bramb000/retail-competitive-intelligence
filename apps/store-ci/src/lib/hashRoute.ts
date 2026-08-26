import type { BoardTab } from "./types";

export type AppView = "scoreboard" | "methods";

export interface HashState {
  view: AppView;
  board: BoardTab;
  deptId: string | null;
  methodsSection?: string;
}

const BOARD_TO_SLUG: Record<BoardTab, string> = {
  departments: "overview",
  dominance: "dominance",
  price: "price",
  kvi: "staples",
  macrospace: "macrospace",
};

const SLUG_TO_BOARD: Record<string, BoardTab> = {
  overview: "departments",
  categories: "departments",
  departments: "departments",
  dominance: "dominance",
  price: "price",
  staples: "kvi",
  kvi: "kvi",
  macrospace: "macrospace",
  floor: "macrospace",
};

export function boardSlug(board: BoardTab): string {
  return BOARD_TO_SLUG[board];
}

export function parseHash(hash: string): HashState {
  const raw = hash.replace(/^#\/?/, "").replace(/^#/, "");
  const parts = raw.split("/").filter(Boolean);

  if (parts[0] === "methods") {
    const section = parts[1] || undefined;
    return {
      view: "methods",
      board: "departments",
      deptId: null,
      methodsSection: section === "floor-map" ? "macrospace" : section,
    };
  }

  const board = SLUG_TO_BOARD[parts[0] ?? ""] ?? "departments";
  let deptId: string | null = null;
  if (parts[1] === "dept" && parts[2]) {
    deptId = decodeURIComponent(parts[2]);
  }

  return { view: "scoreboard", board, deptId };
}

export function buildHash(state: HashState): string {
  if (state.view === "methods") {
    return state.methodsSection ? `#/methods/${state.methodsSection}` : "#/methods";
  }
  const slug = boardSlug(state.board);
  if (state.deptId) {
    return `#/${slug}/dept/${encodeURIComponent(state.deptId)}`;
  }
  return `#/${slug}`;
}

export function replaceHash(state: HashState): void {
  const next = buildHash(state);
  if (window.location.hash !== next) {
    window.history.replaceState(null, "", next);
  }
}
