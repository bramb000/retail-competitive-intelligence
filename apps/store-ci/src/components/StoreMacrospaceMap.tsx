import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BannerMark } from "./BannerMark";
import {
  bayMatchesFocus,
  bayProducts,
  buildBayBlocks,
  categoryBayBlocks,
  categoryGroups,
  categoryProducts,
  categoryStoreContext,
  colesNomenclatureLabel,
  colorFor,
  deriveBayInsights,
  findDepartmentForLabel,
  formatBayLocation,
  hitTestBay,
  retailerBayMix,
  truncate,
  wwNomenclatureLabel,
  worldBounds,
  type AdjacentCategoryRow,
  type BayBlock,
  type BayInsight,
  type BayMixRow,
  type CategoryGroup,
  type CategoryStoreContext,
  type WorldBounds,
} from "../lib/macrospaceMapUtils";
import {
  grainNoun,
  grainTitle,
  intFmt,
  type Grain,
  type Retailer,
  type SkuRow,
  type StoreCiData,
} from "../lib/types";

interface Props {
  data: StoreCiData;
  grain: Grain;
  locationName: string;
  onOpenMethods: (sectionId?: string) => void;
  onViewAllProducts: (deptId: string) => void;
}

interface Viewport {
  scale: number;
  tx: number;
  ty: number;
}

interface MacrospacePlanProps {
  retailer: Retailer;
  blocks: BayBlock[];
  groups: CategoryGroup[];
  grain: Grain;
  /** Legend chip selection (controls chip active state). */
  highlight: string | null;
  /** Legend filter — equal highlight on every matching bay. */
  focusLabel: string | null;
  /** Category label for a soft peer sweep when one bay is selected. */
  sweepLabel: string | null;
  selectedBay: BayBlock | null;
  onHighlight: (label: string | null) => void;
  onSelectBay: (bay: BayBlock | null) => void;
}

function fitViewport(
  bounds: ReturnType<typeof worldBounds>,
  width: number,
  height: number,
  maxScale = 2.4,
): Viewport {
  const pad = 28;
  const innerW = Math.max(width - pad * 2, 1);
  const innerH = Math.max(height - pad * 2, 1);
  const scale = Math.min(innerW / bounds.width, innerH / bounds.height, maxScale);
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

function MacrospacePlanCanvas({
  retailer,
  blocks,
  groups,
  grain,
  highlight,
  focusLabel,
  sweepLabel,
  selectedBay,
  onHighlight,
  onSelectBay,
}: MacrospacePlanProps) {
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

    // Full-canvas floor — avoids floating slabs / letterbox overlays that look broken.
    ctx.fillStyle = "#f3f1ec";
    ctx.fillRect(0, 0, size.w, size.h);

    const sorted = [...blocks].sort((a, b) => {
      const areaA = (a.maxX - a.minX) * (a.maxY - a.minY);
      const areaB = (b.maxX - b.minX) * (b.maxY - b.minY);
      return areaB - areaA;
    });

    for (const b of sorted) {
      const isSel = selectedBay?.id === b.id;
      const isLegendHi = focusLabel != null && bayMatchesFocus(b, focusLabel);
      const isPeer =
        sweepLabel != null && !isSel && bayMatchesFocus(b, sweepLabel);

      type BayTier = "selected" | "legend" | "peer" | "dimmed" | "normal";
      const tier: BayTier = isSel
        ? "selected"
        : isLegendHi
          ? "legend"
          : isPeer
            ? "peer"
            : // Dim the rest whenever legend OR a bay selection (sweep) is active —
              // including the other store's map, which has no selectedBay of its own.
              focusLabel != null || sweepLabel != null || selectedBay != null
              ? "dimmed"
              : "normal";

      const alpha =
        tier === "selected" || tier === "legend"
          ? 1
          : tier === "peer"
            ? 0.72
            : tier === "dimmed"
              ? 0.16
              : 0.94;
      const strokeW = tier === "selected" ? 2.4 : tier === "legend" ? 2 : tier === "peer" ? 1.1 : 1;
      const strongRing = tier === "selected" || tier === "legend";

      const p1 = worldToScreen(v, b.minX, b.maxY, bounds);
      const p2 = worldToScreen(v, b.maxX, b.minY, bounds);
      let x = p1.sx;
      let y = p1.sy;
      let w = p2.sx - p1.sx;
      let h = p2.sy - p1.sy;
      if (w < 1 || h < 1) continue;

      // Specialty fixtures are physically small — boost on-screen size when focused.
      if ((tier === "selected" || tier === "legend") && (w < 14 || h < 14)) {
        const cx = x + w / 2;
        const cy = y + h / 2;
        w = Math.max(w, 16);
        h = Math.max(h, 16);
        x = cx - w / 2;
        y = cy - h / 2;
      }

      const radius = Math.min(4, w * 0.14, h * 0.14);
      ctx.globalAlpha = alpha;
      ctx.fillStyle = b.color;
      ctx.strokeStyle =
        tier === "peer" ? "rgba(255,255,255,0.85)" : strongRing ? "#111" : "rgba(255,255,255,0.75)";
      ctx.lineWidth = strokeW;
      drawRoundedRect(ctx, x, y, w, h, radius);
      ctx.fill();
      ctx.stroke();

      if (strongRing) {
        ctx.shadowColor = "rgba(0,0,0,0.18)";
        ctx.shadowBlur = tier === "selected" ? 8 : 6;
        ctx.strokeStyle = "#111";
        ctx.lineWidth = tier === "selected" ? 2.4 : 2;
        drawRoundedRect(ctx, x, y, w, h, radius);
        ctx.stroke();
        ctx.shadowBlur = 0;
      }

      const minDim = Math.min(w, h);
      if (tier === "selected") {
        const catLabel = truncate(b.label, minDim >= 36 ? 14 : 9);
        ctx.globalAlpha = 1;
        ctx.fillStyle = textColorFor(b.color);
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        if (minDim >= 28) {
          ctx.font = `700 ${Math.min(11, Math.max(8, minDim * 0.22))}px Inter, system-ui, sans-serif`;
          ctx.fillText(catLabel, x + w / 2, y + h / 2 - (minDim >= 36 ? 5 : 0), Math.max(w, h) - 4);
          if (minDim >= 36) {
            ctx.font = `600 ${Math.min(9, Math.max(7, minDim * 0.16))}px Inter, system-ui, sans-serif`;
            ctx.globalAlpha = 0.92;
            ctx.fillText(`A${b.aisle}`, x + w / 2, y + h / 2 + 7, Math.max(w, h) - 4);
          }
        } else {
          ctx.font = `700 8px Inter, system-ui, sans-serif`;
          ctx.fillText(truncate(b.label, 6), x + w / 2, y + h / 2, Math.max(w, h) - 2);
        }
      } else if (tier === "legend") {
        ctx.globalAlpha = 1;
        ctx.fillStyle = textColorFor(b.color);
        ctx.font = `700 ${Math.min(13, Math.max(9, minDim * 0.28))}px Inter, system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(truncate(b.label, 12), x + w / 2, y + h / 2, Math.max(w, h) - 4);
      } else if (tier === "peer" && minDim >= 20) {
        ctx.globalAlpha = 0.88;
        ctx.fillStyle = textColorFor(b.color);
        ctx.font = `600 ${Math.min(9, Math.max(7, minDim * 0.2))}px Inter, system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(`A${b.aisle}`, x + w / 2, y + h / 2, Math.max(w, h) - 4);
      } else if (minDim >= 30 && tier === "normal") {
        const label =
          minDim >= 48 ? truncate(b.label, Math.floor(Math.max(w, h) / 7)) : truncate(b.label, 7);
        ctx.globalAlpha = 1;
        ctx.fillStyle = textColorFor(b.color);
        ctx.font = `600 ${Math.min(12, Math.max(8, minDim * 0.2))}px Inter, system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(label, x + w / 2, y + h / 2, Math.max(w, h) - 6);
      }
    }
    ctx.globalAlpha = 1;
  }, [blocks, bounds, focusLabel, sweepLabel, selectedBay, size]);

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
    <article className="macrospace-plan-card">
      <header className="macrospace-plan-head">
        <div className="macrospace-plan-title">
          <BannerMark banner={retailer} size="sm" label="full" />
          <span className="macrospace-plan-meta">
            {intFmt(blocks.length)} bays · {intFmt(groups.length)} {noun}
          </span>
        </div>
        <div className="macrospace-zoom-controls" aria-label="Map zoom">
          <button type="button" className="macrospace-zoom-btn" onClick={() => zoomAt(1.25, size.w / 2, size.h / 2)} aria-label="Zoom in">
            +
          </button>
          <button type="button" className="macrospace-zoom-btn" onClick={() => zoomAt(0.8, size.w / 2, size.h / 2)} aria-label="Zoom out">
            −
          </button>
          <button type="button" className="macrospace-zoom-btn macrospace-zoom-reset" onClick={resetView}>
            Fit
          </button>
        </div>
      </header>

      <div className="macrospace-plan-stage" ref={wrapRef}>
        {blocks.length === 0 ? (
          <p className="macrospace-empty">No shelf placement data for this store yet.</p>
        ) : (
          <>
            <canvas
              ref={canvasRef}
              className={`macrospace-plan-canvas${ready ? " ready" : ""}`}
              role="img"
              aria-label={`${retailer} macrospace plan`}
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
            <div className="macrospace-plan-hint">
              Drag to pan · Pinch or scroll to zoom · Tap a bay · Double-tap to reset
            </div>
          </>
        )}
      </div>

      <div className="macrospace-legend-scroll" aria-label={`${retailer} ${noun}`}>
        {groups.map((g) => {
          const active = highlight === g.label;
          const sweep = !highlight && sweepLabel === g.label;
          return (
            <button
              key={g.label}
              type="button"
              className={
                active
                  ? "macrospace-legend-chip active"
                  : sweep
                    ? "macrospace-legend-chip sweep"
                    : "macrospace-legend-chip"
              }
              onClick={() => onHighlight(active ? null : g.label)}
            >
              <span className="macrospace-swatch" style={{ background: g.color }} />
              <span className="macrospace-legend-label">{g.label}</span>
              <span className="macrospace-legend-count">{intFmt(g.bays)} bays</span>
            </button>
          );
        })}
      </div>
    </article>
  );
}

function BayComparisonPanel({
  retailer,
  bay,
  grain,
  skus,
  departments,
  colesBlocks,
  wwBlocks,
  onClose,
  onViewAllProducts,
}: {
  retailer: Retailer;
  bay: BayBlock;
  grain: Grain;
  skus: SkuRow[];
  departments: StoreCiData["departments"];
  colesBlocks: BayBlock[];
  wwBlocks: BayBlock[];
  onClose: () => void;
  onViewAllProducts: (deptId: string) => void;
}) {
  const [panelTab, setPanelTab] = useState<"overview" | "insights">("overview");
  const products = useMemo(() => bayProducts(skus, retailer, bay.id), [skus, retailer, bay.id]);
  const insights = useMemo(
    () => deriveBayInsights(products, bay, grain),
    [products, bay, grain],
  );
  const colesContext = useMemo(
    () => categoryStoreContext(colesBlocks, bay.label),
    [colesBlocks, bay.label],
  );
  const wwContext = useMemo(
    () => categoryStoreContext(wwBlocks, bay.label),
    [wwBlocks, bay.label],
  );
  const colesCategoryProducts = useMemo(
    () => categoryProducts(skus, "Coles", bay.label, grain),
    [skus, bay.label, grain],
  );
  const wwCategoryProducts = useMemo(
    () => categoryProducts(skus, "Woolworths", bay.label, grain),
    [skus, bay.label, grain],
  );
  const colesColumnProducts = retailer === "Coles" ? products : colesCategoryProducts;
  const wwColumnProducts = retailer === "Woolworths" ? products : wwCategoryProducts;
  const colesMix = useMemo(
    () => retailerBayMix(colesColumnProducts, "Coles", grain),
    [colesColumnProducts, grain],
  );
  const wwMix = useMemo(
    () => retailerBayMix(wwColumnProducts, "Woolworths", grain),
    [wwColumnProducts, grain],
  );
  const drillDept = useMemo(
    () =>
      findDepartmentForLabel(
        departments,
        bay.label,
        [...colesCategoryProducts, ...wwCategoryProducts],
        grain,
      ),
    [departments, bay.label, colesCategoryProducts, wwCategoryProducts, grain],
  );
  const sideLabel = bay.side === "_" ? "centre" : `${bay.side} side`;
  const bothProductCount = colesCategoryProducts.length + wwCategoryProducts.length;
  const grainNounLabel = grain === "subcategory" ? "Subcategory" : "Category";

  useEffect(() => {
    setPanelTab("overview");
  }, [bay.id, retailer]);

  return (
    <aside className="macrospace-bay-comparison" aria-live="polite">
      <header className="macrospace-bay-comparison-top">
        <div className="macrospace-bay-comparison-intro">
          <p className="macrospace-bay-detail-eyebrow">Bay comparison · {grainNounLabel}</p>
          <div className="macrospace-category-hero">
            <span
              className="macrospace-category-hero-swatch"
              style={{ background: bay.color }}
              aria-hidden="true"
            />
            <div className="macrospace-category-hero-text">
              <h3 className="macrospace-category-hero-title">{bay.label}</h3>
              <p className="macrospace-category-hero-native">
                <span>
                  Coles <strong>{bay.colesLabel || colesMix.label || "—"}</strong>
                </span>
                <span className="macrospace-category-hero-sep" aria-hidden="true">
                  ·
                </span>
                <span>
                  Woolworths <strong>{bay.wwLabel || wwMix.label || "—"}</strong>
                </span>
              </p>
            </div>
          </div>
          <p className="macrospace-bay-location">
            <BannerMark banner={retailer} size="sm" label="full" />
            <span>
              Aisle {bay.aisle} · {sideLabel} · bay {bay.bayNum}
            </span>
            <span className="macrospace-bay-location-count">
              · {intFmt(bay.count)} products in this bay
            </span>
          </p>
        </div>
        <div className="macrospace-bay-comparison-actions">
          <button type="button" className="macrospace-detail-close" onClick={onClose}>
            Close <span aria-hidden="true">×</span>
          </button>
        </div>
      </header>

      {insights.length > 0 ? (
        <div className="panel-subtabs" role="tablist" aria-label="Bay detail sections">
          <button
            type="button"
            role="tab"
            aria-selected={panelTab === "overview"}
            className={panelTab === "overview" ? "panel-subtab active" : "panel-subtab"}
            onClick={() => setPanelTab("overview")}
          >
            Overview
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={panelTab === "insights"}
            className={panelTab === "insights" ? "panel-subtab active" : "panel-subtab"}
            onClick={() => setPanelTab("insights")}
          >
            Insights
            <span className="panel-subtab-count">{insights.length}</span>
          </button>
        </div>
      ) : null}

      {panelTab === "insights" && insights.length > 0 ? (
        <BayInsightsList insights={insights} />
      ) : (
        <div className="macrospace-store-columns">
          <StoreBayColumn
            banner="Coles"
            categoryLabel={bay.label}
            context={colesContext}
            grain={grain}
            isSelected={retailer === "Coles"}
            taxonomy={colesColumnProducts.length > 0 ? colesMix : null}
            products={colesColumnProducts}
            productScope={retailer === "Coles" ? "bay" : "category"}
            locations={categoryBayBlocks(colesBlocks, bay.label)}
            selectedBayId={retailer === "Coles" ? bay.id : null}
            onViewAllProducts={
              drillDept ? () => onViewAllProducts(drillDept.id) : undefined
            }
            viewAllCount={bothProductCount}
          />
          <StoreBayColumn
            banner="Woolworths"
            categoryLabel={bay.label}
            context={wwContext}
            grain={grain}
            isSelected={retailer === "Woolworths"}
            taxonomy={wwColumnProducts.length > 0 ? wwMix : null}
            products={wwColumnProducts}
            productScope={retailer === "Woolworths" ? "bay" : "category"}
            locations={categoryBayBlocks(wwBlocks, bay.label)}
            selectedBayId={retailer === "Woolworths" ? bay.id : null}
            onViewAllProducts={
              drillDept ? () => onViewAllProducts(drillDept.id) : undefined
            }
            viewAllCount={bothProductCount}
          />
        </div>
      )}
    </aside>
  );
}

function StoreBayColumn({
  banner,
  categoryLabel,
  context,
  grain,
  isSelected,
  taxonomy,
  products,
  productScope,
  locations,
  selectedBayId,
  onViewAllProducts,
  viewAllCount,
}: {
  banner: Retailer;
  categoryLabel: string;
  context: CategoryStoreContext;
  grain: Grain;
  isSelected: boolean;
  taxonomy: { label: string; mix: BayMixRow[] } | null;
  products: SkuRow[];
  productScope: "bay" | "category";
  locations: BayBlock[];
  selectedBayId?: string | null;
  onViewAllProducts?: () => void;
  viewAllCount?: number;
}) {
  const labelFn = banner === "Woolworths" ? wwNomenclatureLabel : colesNomenclatureLabel;
  const productTitle =
    productScope === "bay"
      ? "Products in selected bay"
      : `Products in ${categoryLabel}`;

  return (
    <section
      className={`macrospace-store-column macrospace-store-column--${banner === "Coles" ? "coles" : "ww"}${isSelected ? " is-selected" : ""}`}
    >
      <header className="macrospace-store-slot macrospace-store-column-head">
        <BannerMark banner={banner} size="sm" label="full" />
        {isSelected ? (
          <span className="macrospace-store-column-badge">Selected bay</span>
        ) : (
          <span className="macrospace-store-column-badge">
            Peer · <strong>{categoryLabel}</strong>
          </span>
        )}
      </header>

      <div className="macrospace-store-slot">
        <BayLocationsViz
          locations={locations}
          categoryLabel={categoryLabel}
          selectedBayId={selectedBayId ?? null}
        />
      </div>

      <div className="macrospace-store-slot">
        <CategoryFootprintViz label={categoryLabel} context={context} />
      </div>

      <div className="macrospace-store-slot">
        <AdjacentCategoriesViz rows={context.adjacent} />
      </div>

      <div className="macrospace-store-slot">
        {taxonomy && taxonomy.mix.length > 0 ? (
          <RetailerTaxonomyCard
            banner={banner}
            dominant={taxonomy.label}
            mix={taxonomy.mix}
            grain={grain}
          />
        ) : (
          <p className="muted tiny macrospace-store-column-hint">No taxonomy data for this group.</p>
        )}
      </div>

      <section className="macrospace-store-slot macrospace-bay-products macrospace-store-products">
        <h4 className="macrospace-bay-products-title">
          {productTitle}{" "}
          <span className="muted">({intFmt(products.length)})</span>
        </h4>
        {products.length > 0 ? (
          <>
            <ul className="macrospace-bay-product-list">
              {products.slice(0, 10).map((p) => (
                <li key={p.id}>
                  <span className="macrospace-product-name">{p.name ?? `SKU ${p.id}`}</span>
                  <span className="macrospace-store-product-cat">{labelFn(p, grain) ?? "—"}</span>
                </li>
              ))}
            </ul>
            {products.length > 10 ? (
              <p className="muted tiny macrospace-bay-products-more">
                + {products.length - 10} more products
              </p>
            ) : null}
          </>
        ) : (
          <p className="muted tiny macrospace-store-column-hint">No bay-placed products in this group.</p>
        )}
        {onViewAllProducts ? (
          <button type="button" className="macrospace-view-all-btn" onClick={onViewAllProducts}>
            View all products
            {viewAllCount != null ? (
              <span className="muted"> · {intFmt(viewAllCount)}</span>
            ) : null}
          </button>
        ) : null}
      </section>
    </section>
  );
}

function BayLocationsViz({
  locations,
  categoryLabel,
  selectedBayId,
}: {
  locations: BayBlock[];
  categoryLabel: string;
  selectedBayId: string | null;
}) {
  if (locations.length === 0) {
    return (
      <div className="macrospace-viz-block">
        <h4 className="macrospace-viz-title">Locations</h4>
        <p className="muted tiny">No matching bays on this store map.</p>
      </div>
    );
  }

  const ordered = [...locations].sort((a, b) => {
    const aSel = selectedBayId != null && a.id === selectedBayId ? 0 : 1;
    const bSel = selectedBayId != null && b.id === selectedBayId ? 0 : 1;
    if (aSel !== bSel) return aSel - bSel;
    const aisleCmp = a.aisle.localeCompare(b.aisle, undefined, { numeric: true });
    if (aisleCmp !== 0) return aisleCmp;
    const sideCmp = a.side.localeCompare(b.side);
    if (sideCmp !== 0) return sideCmp;
    return a.bayNum.localeCompare(b.bayNum, undefined, { numeric: true });
  });

  const shown = ordered.slice(0, 8);
  const peerCount = selectedBayId
    ? Math.max(0, locations.length - 1)
    : locations.length;

  return (
    <div className="macrospace-viz-block">
      <div className="macrospace-viz-head">
        <h4 className="macrospace-viz-title">Matching locations</h4>
        <span className="macrospace-viz-stat">
          {intFmt(locations.length)} {categoryLabel} bay{locations.length === 1 ? "" : "s"}
        </span>
      </div>
      <ul className="macrospace-location-list">
        {shown.map((loc) => {
          const isPrimary = selectedBayId != null && loc.id === selectedBayId;
          return (
            <li
              key={loc.id}
              className={
                isPrimary
                  ? "macrospace-location-item is-primary"
                  : selectedBayId
                    ? "macrospace-location-item is-secondary"
                    : "macrospace-location-item"
              }
            >
              <span className="macrospace-location-text">
                {isPrimary ? (
                  <span className="macrospace-location-badge">Selected</span>
                ) : null}
                {formatBayLocation(loc)}
              </span>
              <span className="macrospace-location-count">{intFmt(loc.count)} SKUs</span>
            </li>
          );
        })}
      </ul>
      {ordered.length > 8 ? (
        <p className="muted tiny macrospace-bay-products-more">
          + {ordered.length - 8} more {categoryLabel} bays shown softly on the map
        </p>
      ) : peerCount > 0 && selectedBayId ? (
        <p className="muted tiny macrospace-location-hint">
          Selected bay is primary · other {categoryLabel} bays secondary on the map
        </p>
      ) : peerCount > 0 ? (
        <p className="muted tiny macrospace-location-hint">
          Other {categoryLabel} bays shown softly on the map
        </p>
      ) : null}
    </div>
  );
}

function CategoryFootprintViz({
  label,
  context,
}: {
  label: string;
  context: CategoryStoreContext;
}) {
  const otherPct = Math.max(0, 100 - context.categoryPct);

  return (
    <div className="macrospace-viz-block">
      <div className="macrospace-viz-head">
        <h4 className="macrospace-viz-title">Bay footprint</h4>
        <span className="macrospace-viz-stat">
          {intFmt(context.categoryBays)} / {intFmt(context.totalBays)} bays
        </span>
      </div>
      <p className="macrospace-viz-caption">
        <strong>{label}</strong> · {context.categoryPct}% of store
      </p>
      <div
        className="macrospace-footprint-bar"
        role="img"
        aria-label={`${label}: ${context.categoryBays} of ${context.totalBays} bays (${context.categoryPct}%)`}
      >
        <span
          className="macrospace-footprint-fill"
          style={{ width: `${context.categoryPct}%`, background: colorFor(label) }}
        />
        <span className="macrospace-footprint-rest" style={{ width: `${otherPct}%` }} />
      </div>
    </div>
  );
}

function AdjacentCategoriesViz({ rows }: { rows: AdjacentCategoryRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="macrospace-viz-block">
        <h4 className="macrospace-viz-title">Adjacent categories</h4>
        <p className="muted tiny">No neighbouring categories detected for this group.</p>
      </div>
    );
  }

  const max = rows[0]?.count ?? 1;

  return (
    <div className="macrospace-viz-block">
      <div className="macrospace-viz-head">
        <h4 className="macrospace-viz-title">Adjacent categories</h4>
        <span className="macrospace-viz-stat">Touch count</span>
      </div>
      <ul className="macrospace-adjacency-list">
        {rows.map((r) => (
          <li key={r.label}>
            <span className="macrospace-adjacency-label">
              <span className="macrospace-swatch" style={{ background: r.color }} />
              {truncate(r.label, 28)}
            </span>
            <span className="macrospace-adjacency-bar-wrap">
              <span
                className="macrospace-adjacency-bar"
                style={{ width: `${(r.count / max) * 100}%`, background: r.color }}
              />
            </span>
            <span className="macrospace-adjacency-count">{r.count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function BayInsightsList({ insights }: { insights: BayInsight[] }) {
  return (
    <div className="bay-insights" role="tabpanel">
      <p className="bay-insights-lede">
        Placement and taxonomy can disagree. These notes explain mixes that look odd at first glance.
      </p>
      <ul className="bay-insight-list">
        {insights.map((insight) => (
          <li key={insight.id} className="bay-insight-item">
            <div className="bay-insight-item-head">
              <h4 className="bay-insight-title">{insight.title}</h4>
              <span className="bay-insight-meta">
                {insight.count} · {insight.pct}%
              </span>
            </div>
            <p className="bay-insight-summary">{insight.summary}</p>
            <p className="bay-insight-detail">{insight.detail}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RetailerTaxonomyCard({
  banner,
  dominant,
  mix,
  grain,
}: {
  banner: Retailer;
  dominant: string;
  mix: BayMixRow[];
  grain: Grain;
}) {
  const level = grain === "subcategory" ? "subcategory" : "department";
  const hasMix = mix.length > 1 || (mix[0] && mix[0].pct < 99.9);

  return (
    <section className={`macrospace-taxonomy-card macrospace-taxonomy-card--${banner === "Coles" ? "coles" : "ww"}`}>
      <div className="macrospace-taxonomy-card-head">
        <BannerMark banner={banner} size="sm" label="full" />
        <span className="macrospace-taxonomy-level">{level}</span>
      </div>
      <p className="macrospace-taxonomy-dominant">{dominant || "—"}</p>
      {hasMix ? (
        <ul className="macrospace-taxonomy-mix">
          {mix.slice(0, 5).map((m) => (
            <li key={m.label}>
              <span>{m.label}</span>
              <span>{m.pct}%</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted tiny macrospace-taxonomy-single">All products in this group</p>
      )}
    </section>
  );
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

export function StoreMacrospaceMap({
  data,
  grain,
  locationName,
  onOpenMethods,
  onViewAllProducts,
}: Props) {
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
  const mapFocusLabel = highlight;
  const mapSweepLabel = selectedBay?.bay.label ?? null;
  const peerBayCount = useMemo(() => {
    if (!mapSweepLabel) return 0;
    const colesPeers = categoryBayBlocks(colesBlocks, mapSweepLabel).length;
    const wwPeers = categoryBayBlocks(wwBlocks, mapSweepLabel).length;
    return Math.max(colesPeers, wwPeers);
  }, [mapSweepLabel, colesBlocks, wwBlocks]);

  useEffect(() => {
    setHighlight(null);
    setSelectedBay(null);
  }, [grain]);

  const handleSelectBay = (retailer: Retailer, bay: BayBlock | null) => {
    if (!bay) {
      setSelectedBay(null);
      return;
    }
    // Bay tap: zoom to one bay; peer bays in the same category stay visible but subdued.
    setHighlight(null);
    setSelectedBay({ retailer, bay });
  };

  const handleHighlight = (label: string | null) => {
    // Legend filter clears any selected bay so only one active mode exists.
    setSelectedBay(null);
    setHighlight(label);
  };

  return (
    <>
      <header className="hero macrospace-hero">
        <p className="eyebrow">
          {locationName} · {title.toLowerCase()}
        </p>
        <h1>Macrospace</h1>
        <p>
          Shelf bays coloured by {nounMany}. Tap a bay for a side-by-side panel: each retailer’s
          native taxonomy, bay footprint, and adjacent categories.
        </p>
        <p className="hero-methods-link">
          <button type="button" className="text-link" onClick={() => onOpenMethods("macrospace")}>
            How macrospace is built
          </button>
        </p>
      </header>

      <div className="macrospace-toolbar">
        <div className="macrospace-view-toggle" role="group" aria-label="Store view">
          {(["both", "Coles", "Woolworths"] as const).map((v) => (
            <button
              key={v}
              type="button"
              className={storeView === v ? "macrospace-view-btn active" : "macrospace-view-btn"}
              onClick={() => setStoreView(v)}
            >
              {v === "both" ? "Both stores" : v}
            </button>
          ))}
        </div>
        {highlight ? (
          <button type="button" className="chip chip-clear macrospace-clear-highlight" onClick={() => setHighlight(null)}>
            Clear highlight · {highlight}
          </button>
        ) : selectedBay && peerBayCount > 1 ? (
          <span className="macrospace-sweep-hint muted tiny">
            Selected bay is bold · other {selectedBay.bay.label} bays shown softly ({intFmt(peerBayCount - 1)} more)
          </span>
        ) : null}
      </div>

      <details className="data-notes macrospace-caveats">
        <summary>Data notes</summary>
        <div className="data-notes-body">
          <p>
            Coles and Woolworths use different indoor map systems — compare adjacency within each
            store only. Pinch to zoom, drag to pan, tap a coloured bay for its aisle and category mix.
          </p>
        </div>
      </details>

      <div className={storeView === "both" ? "macrospace-plan-grid" : "macrospace-plan-grid single"}>
        {(storeView === "both" || storeView === "Coles") && (
          <MacrospacePlanCanvas
            retailer="Coles"
            blocks={colesBlocks}
            groups={colesGroups}
            grain={grain}
            highlight={highlight}
            focusLabel={mapFocusLabel}
            sweepLabel={mapSweepLabel}
            selectedBay={selectedBay?.retailer === "Coles" ? selectedBay.bay : null}
            onHighlight={handleHighlight}
            onSelectBay={(bay) => handleSelectBay("Coles", bay)}
          />
        )}
        {(storeView === "both" || storeView === "Woolworths") && (
          <MacrospacePlanCanvas
            retailer="Woolworths"
            blocks={wwBlocks}
            groups={wwGroups}
            grain={grain}
            highlight={highlight}
            focusLabel={mapFocusLabel}
            sweepLabel={mapSweepLabel}
            selectedBay={selectedBay?.retailer === "Woolworths" ? selectedBay.bay : null}
            onHighlight={handleHighlight}
            onSelectBay={(bay) => handleSelectBay("Woolworths", bay)}
          />
        )}
      </div>

      {selectedBay ? (
        <BayComparisonPanel
          retailer={selectedBay.retailer}
          bay={selectedBay.bay}
          grain={grain}
          skus={data.skus}
          departments={data.departments}
          colesBlocks={colesBlocks}
          wwBlocks={wwBlocks}
          onClose={() => setSelectedBay(null)}
          onViewAllProducts={onViewAllProducts}
        />
      ) : null}
    </>
  );
}
