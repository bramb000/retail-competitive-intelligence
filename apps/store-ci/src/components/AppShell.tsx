import type { ReactNode } from "react";
import { ContextBar } from "./ContextBar";
import { SidebarNav } from "./SidebarNav";
import type { AppView } from "../lib/hashRoute";
import type { BoardTab } from "../lib/types";

interface Props {
  view: AppView;
  board: BoardTab;
  onBoard: (board: BoardTab) => void;
  onMethods: () => void;
  mobileOpen: boolean;
  onMobileOpen: (open: boolean) => void;
  crumb?: ReactNode;
  meta?: ReactNode;
  toolbar?: ReactNode;
  children: ReactNode;
}

export function AppShell({
  view,
  board,
  onBoard,
  onMethods,
  mobileOpen,
  onMobileOpen,
  crumb,
  meta,
  toolbar,
  children,
}: Props) {
  return (
    <div className="app-shell">
      <SidebarNav
        view={view}
        board={board}
        onBoard={onBoard}
        onMethods={onMethods}
        mobileOpen={mobileOpen}
        onCloseMobile={() => onMobileOpen(false)}
      />
      <div className="app-main-column">
        <ContextBar
          onMenu={() => onMobileOpen(true)}
          crumb={crumb}
          meta={meta}
        >
          {toolbar}
        </ContextBar>
        <main className="app-main">{children}</main>
      </div>
    </div>
  );
}
