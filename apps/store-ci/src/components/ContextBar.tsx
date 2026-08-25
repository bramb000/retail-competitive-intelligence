import type { ReactNode } from "react";

interface Props {
  onMenu: () => void;
  crumb?: ReactNode;
  meta?: ReactNode;
  children?: ReactNode;
}

export function ContextBar({ onMenu, crumb, meta, children }: Props) {
  return (
    <header className="context-bar">
      <button type="button" className="context-menu-btn" aria-label="Open navigation" onClick={onMenu}>
        <span aria-hidden="true">☰</span>
      </button>
      <div className="context-bar-main">
        {crumb ? <div className="context-crumb">{crumb}</div> : null}
        <div className="context-bar-controls">{children}</div>
      </div>
      {meta ? <div className="context-meta muted tiny">{meta}</div> : null}
    </header>
  );
}
