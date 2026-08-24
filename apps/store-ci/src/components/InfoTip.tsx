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

/**
 * Viewport-aware tip via portal. Avoids clipping inside overflow:auto /
 * sticky table headers (where absolute bubbles get cut off or misaligned).
 */
export function InfoTip({ plain, methodsId, onOpenMethods }: InfoTipProps) {
  const tipId = useId();
  const btnRef = useRef<HTMLButtonElement>(null);
  const bubbleRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<Place | null>(null);
  const closeTimer = useRef<number | null>(null);

  const clearClose = () => {
    if (closeTimer.current != null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };

  const scheduleClose = () => {
    clearClose();
    closeTimer.current = window.setTimeout(() => setOpen(false), 120);
  };

  const show = () => {
    clearClose();
    setOpen(true);
  };

  const reposition = useCallback(() => {
    const btn = btnRef.current;
    const bubble = bubbleRef.current;
    if (!btn || !bubble) return;

    const br = btn.getBoundingClientRect();
    const tip = bubble.getBoundingClientRect();
    const gap = 8;
    const margin = 12;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    let placement: "above" | "below" = "above";
    let top = br.top - tip.height - gap;
    if (top < margin) {
      placement = "below";
      top = br.bottom + gap;
    }
    // If still overflowing bottom, clamp.
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
  }, [open, plain, reposition]);

  useEffect(() => {
    if (!open) return;
    const onScroll = () => reposition();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onPointer = (e: MouseEvent | TouchEvent) => {
      const t = e.target as Node;
      if (btnRef.current?.contains(t) || bubbleRef.current?.contains(t)) return;
      setOpen(false);
    };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    window.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("touchstart", onPointer);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("touchstart", onPointer);
    };
  }, [open, reposition]);

  useEffect(() => () => clearClose(), []);

  return (
    <span
      className="info-tip"
      onMouseEnter={show}
      onMouseLeave={scheduleClose}
    >
      <button
        ref={btnRef}
        type="button"
        className={open ? "info-tip-btn open" : "info-tip-btn"}
        aria-label={plain}
        aria-describedby={open ? tipId : undefined}
        aria-expanded={open}
        onFocus={show}
        onBlur={scheduleClose}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        ?
      </button>
      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={bubbleRef}
              id={tipId}
              role="tooltip"
              className={`info-tip-bubble portal ${place?.placement ?? "above"}`}
              style={
                place
                  ? { top: place.top, left: place.left, visibility: "visible" }
                  : { top: 0, left: 0, visibility: "hidden" }
              }
              onMouseEnter={show}
              onMouseLeave={scheduleClose}
            >
              <span className="info-tip-plain">{plain}</span>
              {onOpenMethods ? (
                <button
                  type="button"
                  className="info-tip-link"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={(e) => {
                    e.stopPropagation();
                    setOpen(false);
                    onOpenMethods(methodsId);
                  }}
                >
                  Full guide →
                </button>
              ) : null}
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
