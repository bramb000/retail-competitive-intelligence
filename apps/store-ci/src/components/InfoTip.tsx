import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface InfoTipProps {
  /** Short plain-language explanation for non-technical users */
  plain: string;
  /** Optional anchor in Methods wiki (without #) */
  methodsId?: string;
  onOpenMethods?: (sectionId?: string) => void;
}

type Place = { top: number; left: number; placement: "above" | "below" };

function prefersHoverTooltips(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  // Mouse/trackpad desktops only — iPad (even with trackpad) reports coarse/no-hover
  // for the primary pointing device in most Safari builds when touch is present.
  return window.matchMedia("(hover: hover) and (pointer: fine)").matches;
}

/**
 * Viewport-aware tip via portal. Tap-first for iPad/touch; hover only when the
 * device truly supports fine-pointer hover (avoids sticky/ghost hover on iOS).
 */
export function InfoTip({ plain, methodsId, onOpenMethods }: InfoTipProps) {
  const tipId = useId();
  const btnRef = useRef<HTMLButtonElement>(null);
  const bubbleRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<Place | null>(null);
  const [hoverMode, setHoverMode] = useState(false);
  const closeTimer = useRef<number | null>(null);
  const ignoreOutsideUntil = useRef(0);

  useEffect(() => {
    const sync = () => setHoverMode(prefersHoverTooltips());
    sync();
    const mq = window.matchMedia("(hover: hover) and (pointer: fine)");
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const clearClose = () => {
    if (closeTimer.current != null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };

  const scheduleClose = () => {
    if (!hoverMode) return;
    clearClose();
    closeTimer.current = window.setTimeout(() => setOpen(false), 160);
  };

  const show = () => {
    if (!hoverMode) return;
    clearClose();
    setOpen(true);
  };

  const close = () => {
    clearClose();
    setOpen(false);
  };

  const toggle = () => {
    clearClose();
    setOpen((v) => {
      const next = !v;
      if (next) {
        // Opening tap must not also count as an outside dismiss.
        ignoreOutsideUntil.current = Date.now() + 400;
      }
      return next;
    });
  };

  const reposition = useCallback(() => {
    const btn = btnRef.current;
    const bubble = bubbleRef.current;
    if (!btn || !bubble) return;

    const br = btn.getBoundingClientRect();
    const tip = bubble.getBoundingClientRect();
    const gap = 10;
    const margin = 16;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const isCompact = vw < 900;

    // On tablet widths, prefer below the control (thumb-friendly) and allow a wider card.
    let placement: "above" | "below" = isCompact ? "below" : "above";
    let top =
      placement === "below" ? br.bottom + gap : br.top - tip.height - gap;

    if (placement === "above" && top < margin) {
      placement = "below";
      top = br.bottom + gap;
    }
    if (placement === "below" && top + tip.height > vh - margin) {
      placement = "above";
      top = br.top - tip.height - gap;
    }
    if (top < margin) top = margin;
    if (top + tip.height > vh - margin) {
      top = Math.max(margin, vh - tip.height - margin);
    }

    let left = br.left + br.width / 2 - tip.width / 2;
    left = Math.min(Math.max(margin, left), vw - tip.width - margin);

    setPlace({ top, left, placement });
  }, []);

  useLayoutEffect(() => {
    if (!open) {
      setPlace(null);
      return;
    }
    reposition();
  }, [open, plain, hoverMode, reposition]);

  useEffect(() => {
    if (!open) return;
    const onScroll = () => reposition();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    const onPointerDown = (e: PointerEvent) => {
      if (Date.now() < ignoreOutsideUntil.current) return;
      const t = e.target as Node;
      if (btnRef.current?.contains(t) || bubbleRef.current?.contains(t)) return;
      close();
    };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    window.addEventListener("keydown", onKey);
    // pointerdown covers mouse + touch + pen without iOS hover ghosts.
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, [open, reposition]);

  useEffect(() => () => clearClose(), []);

  return (
    <span
      className="info-tip"
      onMouseEnter={hoverMode ? show : undefined}
      onMouseLeave={hoverMode ? scheduleClose : undefined}
    >
      <button
        ref={btnRef}
        type="button"
        className={open ? "info-tip-btn open" : "info-tip-btn"}
        aria-label={`About: ${plain}`}
        aria-describedby={open ? tipId : undefined}
        aria-expanded={open}
        onFocus={hoverMode ? show : undefined}
        onBlur={hoverMode ? scheduleClose : undefined}
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
          toggle();
        }}
      >
        <span className="info-tip-glyph" aria-hidden>
          ?
        </span>
      </button>
      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={bubbleRef}
              id={tipId}
              role="dialog"
              aria-label="Help"
              className={`info-tip-bubble portal ${place?.placement ?? "below"} ${
                hoverMode ? "hover-mode" : "touch-mode"
              }`}
              style={
                place
                  ? { top: place.top, left: place.left, visibility: "visible" }
                  : { top: 0, left: 0, visibility: "hidden" }
              }
              onMouseEnter={hoverMode ? show : undefined}
              onMouseLeave={hoverMode ? scheduleClose : undefined}
            >
              <span className="info-tip-plain">{plain}</span>
              <div className="info-tip-actions">
                {onOpenMethods ? (
                  <button
                    type="button"
                    className="info-tip-link"
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.stopPropagation();
                      close();
                      onOpenMethods(methodsId);
                    }}
                  >
                    Full guide
                  </button>
                ) : (
                  <span />
                )}
                {!hoverMode ? (
                  <button
                    type="button"
                    className="info-tip-dismiss"
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.stopPropagation();
                      close();
                    }}
                  >
                    Got it
                  </button>
                ) : null}
              </div>
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
