import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BannerMark } from "./BannerMark";
import {
  buildBayBlocks,
  categoryGroups,
  hitTestBay,
  truncate,
  worldBounds,
  type BayBlock,
  type CategoryGroup,
  type WorldBounds,
} from "../lib/floorMapUtils";
import {
  grainNoun,
  grainTitle,
  intFmt,
  type Grain,
  type Retailer,
  type StoreCiData,
} from "../lib/types";

interface Props {
  data: StoreCiData;
  grain: Grain;
  locationName: string;
  onOpenMethods: (sectionId?: string) => void;
}

interface Viewport {
  scale: number;
  tx: number;
  ty: number;
}

interface FloorPlanProps {
  retailer: Retailer;
  blocks: BayBlock[];
  groups: CategoryGroup[];
  grain: Grain;
  highlight: string | null;
  selectedBay: BayBlock | null;
  onHighlight: (label: string | null) => void;
  onSelectBay: (bay: BayBlock | null) => void;
}

function fitViewport(
  bounds: ReturnType<typeof worldBounds>,
  width: number,
  height: number,
): Viewport {
  const pad = 28;
  const innerW = Math.max(width - pad * 2, 1);
  const innerH = Math.max(height - pad * 2, 1);
  const scale = Math.min(innerW / bounds.width, innerH / bounds.height, 2.4);
  const tx = pad + (innerW - bounds.width * scale) / 2 - bounds.minX * scale;
  const ty = pad + (innerH - bounds.height * scale) / 2 - bounds.minY * scale;
  return { scale, tx, ty };
}

function worldToScreen(v: Viewport, x: number, y: number, bounds: WorldBounds) {
  const fy = bounds.maxY + bounds.minY - y;
  return { sx: v.tx + x * v.scale, sy: v.ty + fy * v.scale };
}

function screenToWorld(v: Viewport, sx: number, sy: number, bounds: WorldBounds) {
  const wx = (sx - v.tx) / v.scale;
  const fy = (sy - v.ty) / v.scale;
  const wy = bounds.maxY + bounds.minY - fy;
  return { wx, wy };
}

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  if (typeof ctx.roundRect === "function") {
    ctx.roundRect(x, y, w, h, radius);
  } else {
    ctx.rect(x, y, w, h);
  }
}

function FloorPlanCanvas({
  retailer,
  blocks,
  groups,
  grain,
  highlight,
  selectedBay,
  onHighlight,
  onSelectBay,
}: FloorPlanProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewportRef = useRef<Viewport>({ scale: 1, tx: 0, ty: 0 });
  const bounds = useMemo(() => worldBounds(blocks), [blocks]);
  const [size, setSize] = useState({ w: 800, h: 560 });
  const [viewport, setViewport] = useState<Viewport>(() => fitViewport(bounds, 800, 560));
  const [ready, setReady] = useState(false);

  const dragRef = useRef<{
    active: boolean;
    startX: number;
    startY: number;
    lastX: number;
    lastY: number;
    pointerId: number;
  } | null>(null);
  const pinchRef = useRef<{ dist: number; scale: number; midX: number; midY: number } | null>(null);
  const lastTapRef = useRef(0);

  viewportRef.current = viewport;

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (!cr) return;
      const w = Math.max(320, Math.floor(cr.width));
      const h = Math.max(420, Math.floor(cr.height));
      setSize({ w, h });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    setViewport(fitViewport(bounds, size.w, size.h));
    setReady(true);
  }, [bounds, size.w, size.h, blocks.length]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || blocks.length === 0) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(size.w * dpr);
    canvas.height = Math.floor(size.h * dpr);
    canvas.style.width = `${size.w}px`;
    canvas.style.height = `${size.h}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const v = viewportRef.current;
    ctx.clearRect(0, 0, size.w, size.h);

    // Floor slab
    const tl = worldToScreen(v, bounds.minX, bounds.maxY, bounds);
    const br = worldToScreen(v, bounds.maxX, bounds.minY, bounds);
    ctx.fillStyle = "#f4f1ea";
    ctx.strokeStyle = "rgba(9,9,9,0.08)";
    ctx.lineWidth = 1;
    drawRoundedRect(ctx, tl.sx - 8, tl.sy - 8, br.sx - tl.sx + 16, br.sy - tl.sy + 16, 14);
    ctx.fill();
    ctx.stroke();

    // Subtle grid
    ctx.strokeStyle = "rgba(9,9,9,0.035)";
    ctx.lineWidth = 1;
    const gridStep = 400 * v.scale;
    if (gridStep > 18) {
      for (let gx = tl.sx; gx < br.sx; gx += gridStep) {
        ctx.beginPath();
        ctx.moveTo(gx, tl.sy);
        ctx.lineTo(gx, br.sy);
        ctx.stroke();
      }
      for (let gy = tl.sy; gy < br.sy; gy += gridStep) {
        ctx.beginPath();
        ctx.moveTo(tl.sx, gy);
        ctx.lineTo(br.sx, gy);
        ctx.stroke();
      }
    }

    const sorted = [...blocks].sort((a, b) => {
      const areaA = (a.maxX - a.minX) * (a.maxY - a.minY);
      const areaB = (b.maxX - b.minX) * (b.maxY - b.minY);
      return areaB - areaA;
    });

    for (const b of sorted) {
      const isHi = highlight != null && b.label === highlight;
      const isSel = selectedBay?.id === b.id;
      const dimmed = highlight != null && !isHi;

      const p1 = worldToScreen(v, b.minX, b.maxY, bounds);
      const p2 = worldToScreen(v, b.maxX, b.minY, bounds);
      const x = p1.sx;
      const y = p1.sy;
      const w = p2.sx - p1.sx;
      const h = p2.sy - p1.sy;
      if (w < 1 || h < 1) continue;

      ctx.globalAlpha = dimmed ? 0.22 : isSel ? 1 : 0.92;
      const grad = ctx.createLinearGradient(x, y, x + w, y + h);
      grad.addColorStop(0, b.color);
      grad.addColorStop(1, shadeColor(b.color, -18));
      ctx.fillStyle = grad;
      ctx.strokeStyle = isSel ? "#111" : "rgba(255,255,255,0.85)";
      ctx.lineWidth = isSel ? 2.5 : 1.2;
      drawRoundedRect(ctx, x, y, w, h, Math.min(8, w * 0.18, h * 0.18));
      ctx.fill();
      ctx.stroke();

      if (isSel || isHi) {
        ctx.shadowColor = b.color;
        ctx.shadowBlur = isSel ? 14 : 8;
        ctx.strokeStyle = isSel ? "#111" : b.color;
        ctx.lineWidth = isSel ? 2.5 : 1.5;
        drawRoundedRect(ctx, x, y, w, h, Math.min(8, w * 0.18, h * 0.18));
        ctx.stroke();
        ctx.shadowBlur = 0;
      }

      // Label when block is large enough on screen
      const minDim = Math.min(w, h);
      if (minDim >= 34 && !dimmed) {
        const label =
          minDim >= 52 ? truncate(b.label, Math.floor(w / 7)) : truncate(b.label, 8);
        ctx.globalAlpha = 1;
        ctx.fillStyle = textColorFor(b.color);
        ctx.font = `600 ${Math.min(13, Math.max(9, minDim * 0.22))}px Inter, system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(label, x + w / 2, y + h / 2, w - 6);
      }
    }
    ctx.globalAlpha = 1;
  }, [blocks, bounds, highlight, selectedBay, size]);

  useEffect(() => {
    draw();
  }, [draw, viewport]);

  const clampScale = (s: number) => Math.min(5, Math.max(0.35, s));

  const zoomAt = useCallback(
    (factor: number, cx: number, cy: number) => {
      setViewport((prev) => {
        const nextScale = clampScale(prev.scale * factor);
        const { wx, wy } = screenToWorld(prev, cx, cy, bounds);
        const tx = cx - wx * nextScale;
        const ty = cy - wy * nextScale;
        return { scale: nextScale, tx, ty };
      });
    },
    [],
  );

  const resetView = useCallback(() => {
    setViewport(fitViewport(bounds, size.w, size.h));
    onSelectBay(null);
  }, [bounds, size.w, size.h, onSelectBay]);

  const handlePointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.setPointerCapture(e.pointerId);

    if (e.pointerType === "touch") {
      const now = Date.now();
      if (now - lastTapRef.current < 320) {
        resetView();
        lastTapRef.current = 0;
        return;
      }
      lastTapRef.current = now;
    }

    dragRef.current = {
      active: true,
      startX: e.clientX,
      startY: e.clientY,
      lastX: e.clientX,
      lastY: e.clientY,
      pointerId: e.pointerId,
    };
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!dragRef.current?.active || dragRef.current.pointerId !== e.pointerId) return;
    const dx = e.clientX - dragRef.current.lastX;
    const dy = e.clientY - dragRef.current.lastY;
    dragRef.current.lastX = e.clientX;
    dragRef.current.lastY = e.clientY;
    setViewport((prev) => ({ ...prev, tx: prev.tx + dx, ty: prev.ty + dy }));
  };

  const finishPointer = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!dragRef.current || dragRef.current.pointerId !== e.pointerId) return;
    const moved =
      Math.hypot(e.clientX - dragRef.current.startX, e.clientY - dragRef.current.startY) > 10;
    dragRef.current = null;

    if (moved || pinchRef.current) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const { wx, wy } = screenToWorld(viewportRef.current, sx, sy, bounds);
    const hit = hitTestBay(blocks, wx, wy);
    onSelectBay(hit?.id === selectedBay?.id ? null : hit);
  };

  const handleTouchStart = (e: React.TouchEvent<HTMLCanvasElement>) => {
    if (e.touches.length === 2) {
      e.preventDefault();
      const [a, b] = [e.touches[0], e.touches[1]];
      const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      pinchRef.current = {
        dist,
        scale: viewportRef.current.scale,
        midX: (a.clientX + b.clientX) / 2,
        midY: (a.clientY + b.clientY) / 2,
      };
      dragRef.current = null;
    }
  };

  const handleTouchMove = (e: React.TouchEvent<HTMLCanvasElement>) => {
    if (e.touches.length !== 2 || !pinchRef.current) return;
    e.preventDefault();
    const [a, b] = [e.touches[0], e.touches[1]];
    const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const midX = (a.clientX + b.clientX) / 2 - rect.left;
    const midY = (a.clientY + b.clientY) / 2 - rect.top;
    const factor = dist / pinchRef.current.dist;
    setViewport((prev) => {
      const nextScale = clampScale(prev.scale * factor);
      const { wx, wy } = screenToWorld(prev, midX, midY, bounds);
      return { scale: nextScale, tx: midX - wx * nextScale, ty: midY - wy * nextScale };
    });
    pinchRef.current.dist = dist;
  };

  const handleTouchEnd = () => {
    pinchRef.current = null;
  };

  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const factor = e.deltaY > 0 ? 0.92 : 1.08;
    zoomAt(factor, cx, cy);
  };

  const noun = grainNoun(grain);

  return (
    <article className="floor-plan-card">
      <header className="floor-plan-head">
        <div className="floor-plan-title">
          <BannerMark banner={retailer} size="sm" label="full" />
          <span className="floor-plan-meta">
            {intFmt(blocks.length)} bays · {intFmt(groups.length)} {noun}
          </span>
        </div>
        <div className="floor-zoom-controls" aria-label="Map zoom">
          <button type="button" className="floor-zoom-btn" onClick={() => zoomAt(1.25, size.w / 2, size.h / 2)} aria-label="Zoom in">
            +
          </button>
          <button type="button" className="floor-zoom-btn" onClick={() => zoomAt(0.8, size.w / 2, size.h / 2)} aria-label="Zoom out">
            −
          </button>
          <button type="button" className="floor-zoom-btn floor-zoom-reset" onClick={resetView}>
            Fit
          </button>
        </div>
      </header>

      <div className="floor-plan-stage" ref={wrapRef}>
        {blocks.length === 0 ? (
          <p className="floor-empty">No shelf placement data for this store yet.</p>
        ) : (
          <>
            <canvas
              ref={canvasRef}
              className={`floor-plan-canvas${ready ? " ready" : ""}`}
              role="img"
              aria-label={`${retailer} floor plan`}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={finishPointer}
              onPointerCancel={() => {
                dragRef.current = null;
              }}
              onTouchStart={handleTouchStart}
              onTouchMove={handleTouchMove}
              onTouchEnd={handleTouchEnd}
              onWheel={handleWheel}
            />
            <div className="floor-plan-hint">
              Drag to pan · Pinch or scroll to zoom · Tap a bay · Double-tap to reset
            </div>
          </>
        )}
      </div>

      {selectedBay ? (
        <aside className="floor-bay-detail" aria-live="polite">
          <div className="floor-bay-detail-head">
            <span className="floor-swatch lg" style={{ background: selectedBay.color }} />
            <div>
              <strong>{selectedBay.label}</strong>
              <span className="floor-bay-aisle">
                {selectedBay.aisle} · {selectedBay.side} · bay {selectedBay.bayNum}
              </span>
            </div>
            <button type="button" className="floor-detail-close" onClick={() => onSelectBay(null)} aria-label="Close">
              ×
            </button>
          </div>
          <p className="floor-bay-skus">{intFmt(selectedBay.count)} products in this bay</p>
          {selectedBay.mix.length > 1 ? (
            <ul className="floor-bay-mix">
              {selectedBay.mix.slice(0, 5).map((m) => (
                <li key={m.label}>
                  <span>{m.label}</span>
                  <span>{m.pct}%</span>
                </li>
              ))}
            </ul>
          ) : null}
        </aside>
      ) : null}

      <div className="floor-legend-scroll" aria-label={`${retailer} ${noun}`}>
        {groups.map((g) => {
          const active = highlight === g.label;
          return (
            <button
              key={g.label}
              type="button"
              className={active ? "floor-legend-chip active" : "floor-legend-chip"}
              onClick={() => onHighlight(active ? null : g.label)}
            >
              <span className="floor-swatch" style={{ background: g.color }} />
              <span className="floor-legend-label">{g.label}</span>
              <span className="floor-legend-count">{intFmt(g.bays)} bays</span>
            </button>
          );
        })}
      </div>
    </article>
  );
}

function shadeColor(hex: string, amount: number): string {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.max(0, Math.min(255, ((n >> 16) & 255) + amount));
  const g = Math.max(0, Math.min(255, ((n >> 8) & 255) + amount));
  const b = Math.max(0, Math.min(255, (n & 255) + amount));
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}

function textColorFor(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.62 ? "#1a1a1a" : "#fff";
}

type StoreView = "Coles" | "Woolworths" | "both";

export function StoreFloorMap({ data, grain, locationName, onOpenMethods }: Props) {
  const [highlight, setHighlight] = useState<string | null>(null);
  const [selectedBay, setSelectedBay] = useState<{ retailer: Retailer; bay: BayBlock } | null>(null);
  const [storeView, setStoreView] = useState<StoreView>("both");
  const title = grainTitle(grain);
  const nounMany = grainNoun(grain);

  const colesBlocks = useMemo(
    () => buildBayBlocks(data.skus, "Coles", grain),
    [data.skus, grain],
  );
  const wwBlocks = useMemo(
    () => buildBayBlocks(data.skus, "Woolworths", grain),
    [data.skus, grain],
  );
  const colesGroups = useMemo(() => categoryGroups(colesBlocks), [colesBlocks]);
  const wwGroups = useMemo(() => categoryGroups(wwBlocks), [wwBlocks]);

  useEffect(() => {
    setHighlight(null);
    setSelectedBay(null);
  }, [grain]);

  const handleSelectBay = (retailer: Retailer, bay: BayBlock | null) => {
    if (!bay) {
      setSelectedBay(null);
      return;
    }
    setSelectedBay({ retailer, bay });
    setHighlight(bay.label);
  };

  return (
    <>
      <header className="hero floor-hero">
        <p className="eyebrow">
          {locationName} · {title.toLowerCase()}
        </p>
        <h1>Store layout by aisle</h1>
        <p>
          Shelf bays coloured by {nounMany}. Drag, pinch, or scroll to explore; tap a bay for
          detail.
        </p>
        <p className="hero-methods-link">
          <button type="button" className="text-link" onClick={() => onOpenMethods("floor-map")}>
            How the floor map is built
          </button>
        </p>
      </header>

      <div className="floor-toolbar">
        <div className="floor-view-toggle" role="group" aria-label="Store view">
          {(["both", "Coles", "Woolworths"] as const).map((v) => (
            <button
              key={v}
              type="button"
              className={storeView === v ? "floor-view-btn active" : "floor-view-btn"}
              onClick={() => setStoreView(v)}
            >
              {v === "both" ? "Both stores" : v}
            </button>
          ))}
        </div>
        {highlight ? (
          <button type="button" className="chip chip-clear floor-clear-highlight" onClick={() => setHighlight(null)}>
            Clear highlight · {highlight}
          </button>
        ) : null}
      </div>

      <details className="data-notes floor-caveats">
        <summary>Data notes</summary>
        <div className="data-notes-body">
          <p>
            Coles and Woolworths use different indoor map systems — compare adjacency within each
            store only. Pinch to zoom, drag to pan, tap a coloured bay for its aisle and category mix.
          </p>
        </div>
      </details>

      <div className={storeView === "both" ? "floor-plan-grid" : "floor-plan-grid single"}>
        {(storeView === "both" || storeView === "Coles") && (
          <FloorPlanCanvas
            retailer="Coles"
            blocks={colesBlocks}
            groups={colesGroups}
            grain={grain}
            highlight={highlight}
            selectedBay={selectedBay?.retailer === "Coles" ? selectedBay.bay : null}
            onHighlight={setHighlight}
            onSelectBay={(bay) => handleSelectBay("Coles", bay)}
          />
        )}
        {(storeView === "both" || storeView === "Woolworths") && (
          <FloorPlanCanvas
            retailer="Woolworths"
            blocks={wwBlocks}
            groups={wwGroups}
            grain={grain}
            highlight={highlight}
            selectedBay={selectedBay?.retailer === "Woolworths" ? selectedBay.bay : null}
            onHighlight={setHighlight}
            onSelectBay={(bay) => handleSelectBay("Woolworths", bay)}
          />
        )}
      </div>
    </>
  );
}
