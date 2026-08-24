import { useEffect, useMemo, useRef, useState } from "react";
import { BannerMark } from "./BannerMark";
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
}

interface MapPoint {
  x: number;
  y: number;
  label: string;
}

interface GroupStats {
  label: string;
  count: number;
  cx: number;
  cy: number;
  color: string;
}

/** Distinct, readable palette — stable by label hash, not order. */
const PALETTE = [
  "#E87722",
  "#17823C",
  "#5B4FE8",
  "#C62828",
  "#0277BD",
  "#6A1B9A",
  "#00838F",
  "#AD1457",
  "#558B2F",
  "#EF6C00",
  "#3949AB",
  "#00897B",
  "#D84315",
  "#5D4037",
  "#455A64",
  "#7B1FA2",
  "#2E7D32",
  "#1565C0",
  "#C2185B",
  "#F9A825",
];

function colorFor(label: string): string {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

function groupLabel(sku: SkuRow, grain: Grain): string | null {
  if (grain === "subcategory") {
    return sku.subcategory || sku.shared_label || sku.gold_category || null;
  }
  return sku.shared_label || sku.gold_category || sku.category || null;
}

function buildPoints(skus: SkuRow[], retailer: Retailer, grain: Grain): MapPoint[] {
  const out: MapPoint[] = [];
  for (const s of skus) {
    if (s.retailer !== retailer) continue;
    if (s.indoor_x == null || s.indoor_y == null) continue;
    const label = groupLabel(s, grain);
    if (!label) continue;
    out.push({ x: s.indoor_x, y: s.indoor_y, label });
  }
  return out;
}

function groupStats(points: MapPoint[]): GroupStats[] {
  const acc = new Map<string, { n: number; sx: number; sy: number }>();
  for (const p of points) {
    const cur = acc.get(p.label) ?? { n: 0, sx: 0, sy: 0 };
    cur.n += 1;
    cur.sx += p.x;
    cur.sy += p.y;
    acc.set(p.label, cur);
  }
  return [...acc.entries()]
    .map(([label, v]) => ({
      label,
      count: v.n,
      cx: v.sx / v.n,
      cy: v.sy / v.n,
      color: colorFor(label),
    }))
    .sort((a, b) => b.count - a.count);
}

function extents(points: MapPoint[]): { minX: number; maxX: number; minY: number; maxY: number } {
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const p of points) {
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  }
  if (!Number.isFinite(minX)) return { minX: 0, maxX: 1, minY: 0, maxY: 1 };
  const padX = (maxX - minX) * 0.04 || 1;
  const padY = (maxY - minY) * 0.04 || 1;
  return { minX: minX - padX, maxX: maxX + padX, minY: minY - padY, maxY: maxY + padY };
}

interface StorePlotProps {
  retailer: Retailer;
  points: MapPoint[];
  groups: GroupStats[];
  highlight: string | null;
  onHighlight: (label: string | null) => void;
  grain: Grain;
}

function StorePlot({ retailer, points, groups, highlight, onHighlight, grain }: StorePlotProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 640, h: 480 });

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (!cr) return;
      const w = Math.max(280, Math.floor(cr.width));
      const h = Math.max(320, Math.min(560, Math.floor(w * 0.72)));
      setSize({ w, h });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || points.length === 0) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(size.w * dpr);
    canvas.height = Math.floor(size.h * dpr);
    canvas.style.width = `${size.w}px`;
    canvas.style.height = `${size.h}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const { minX, maxX, minY, maxY } = extents(points);
    const ml = 36;
    const mr = 12;
    const mt = 12;
    const mb = 28;
    const pw = size.w - ml - mr;
    const ph = size.h - mt - mb;

    const toScreen = (x: number, y: number) => ({
      sx: ml + ((x - minX) / (maxX - minX || 1)) * pw,
      // Invert Y so larger indoor_y sits toward the top of the plot
      sy: mt + ((maxY - y) / (maxY - minY || 1)) * ph,
    });

    ctx.clearRect(0, 0, size.w, size.h);
    ctx.fillStyle = "#fbfaf7";
    ctx.fillRect(0, 0, size.w, size.h);

    // Light grid
    ctx.strokeStyle = "rgba(9,9,9,0.06)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const gx = ml + (pw * i) / 4;
      const gy = mt + (ph * i) / 4;
      ctx.beginPath();
      ctx.moveTo(gx, mt);
      ctx.lineTo(gx, mt + ph);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(ml, gy);
      ctx.lineTo(ml + pw, gy);
      ctx.stroke();
    }

    ctx.strokeStyle = "rgba(9,9,9,0.14)";
    ctx.strokeRect(ml, mt, pw, ph);

    const colorMap = new Map(groups.map((g) => [g.label, g.color]));
    const r = points.length > 8000 ? 1.4 : points.length > 3000 ? 1.8 : 2.4;

    // Dim non-highlighted first, then highlighted on top
    const drawPass = (onlyHighlight: boolean) => {
      for (const p of points) {
        const isHi = highlight != null && p.label === highlight;
        if (onlyHighlight !== isHi) continue;
        const { sx, sy } = toScreen(p.x, p.y);
        ctx.beginPath();
        ctx.arc(sx, sy, onlyHighlight ? r + 0.6 : r, 0, Math.PI * 2);
        ctx.fillStyle = colorMap.get(p.label) ?? "#888";
        ctx.globalAlpha = onlyHighlight ? 0.9 : 0.08;
        ctx.fill();
      }
    };
    if (highlight == null) {
      ctx.globalAlpha = 0.55;
      for (const p of points) {
        const { sx, sy } = toScreen(p.x, p.y);
        ctx.beginPath();
        ctx.arc(sx, sy, r, 0, Math.PI * 2);
        ctx.fillStyle = colorMap.get(p.label) ?? "#888";
        ctx.fill();
      }
    } else {
      drawPass(false);
      drawPass(true);
    }
    ctx.globalAlpha = 1;

    // Centroid labels for larger groups (or the highlighted one)
    const labelMin = grain === "subcategory" ? 40 : 20;
    const toLabel = groups.filter(
      (g) => g.count >= labelMin || (highlight != null && g.label === highlight),
    );
    ctx.font = "600 11px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const g of toLabel) {
      const { sx, sy } = toScreen(g.cx, g.cy);
      const text = g.label.length > 22 ? `${g.label.slice(0, 20)}…` : g.label;
      const tw = ctx.measureText(text).width;
      const pad = 5;
      const bx = sx - tw / 2 - pad;
      const by = sy - 9;
      const bw = tw + pad * 2;
      const bh = 18;
      ctx.fillStyle = "rgba(255,255,255,0.88)";
      ctx.strokeStyle = g.color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      if (typeof ctx.roundRect === "function") {
        ctx.roundRect(bx, by, bw, bh, 4);
      } else {
        ctx.rect(bx, by, bw, bh);
      }
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#111";
      ctx.fillText(text, sx, sy);
    }

    // Axis captions
    ctx.fillStyle = "rgba(89,89,89,0.9)";
    ctx.font = "500 10px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("indoor x →", ml + pw / 2, size.h - 8);
    ctx.save();
    ctx.translate(12, mt + ph / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText("indoor y →", 0, 0);
    ctx.restore();
  }, [points, groups, highlight, size, grain]);

  const placed = points.length;
  const noun = grainNoun(grain);

  return (
    <article className="floor-plot">
      <header className="floor-plot-head">
        <BannerMark banner={retailer} size="sm" label="full" />
        <span className="floor-plot-meta">
          {intFmt(placed)} placed SKUs · {intFmt(groups.length)} {noun}
        </span>
      </header>
      <div className="floor-canvas-wrap" ref={wrapRef}>
        {placed === 0 ? (
          <p className="floor-empty">No indoor coordinates for this store yet.</p>
        ) : (
          <canvas ref={canvasRef} role="img" aria-label={`${retailer} floor map`} />
        )}
      </div>
      <ul className="floor-legend" aria-label={`${retailer} ${noun}`}>
        {groups.slice(0, grain === "subcategory" ? 24 : 16).map((g) => {
          const active = highlight === g.label;
          return (
            <li key={g.label}>
              <button
                type="button"
                className={active ? "floor-legend-item active" : "floor-legend-item"}
                onClick={() => onHighlight(active ? null : g.label)}
                onMouseEnter={() => onHighlight(g.label)}
                onMouseLeave={() => onHighlight(null)}
              >
                <span className="floor-swatch" style={{ background: g.color }} />
                <span className="floor-legend-label">{g.label}</span>
                <span className="floor-legend-count">{intFmt(g.count)}</span>
              </button>
            </li>
          );
        })}
        {groups.length > (grain === "subcategory" ? 24 : 16) ? (
          <li className="floor-legend-more">
            +{groups.length - (grain === "subcategory" ? 24 : 16)} more on the map
          </li>
        ) : null}
      </ul>
    </article>
  );
}

export function StoreFloorMap({ data, grain, locationName, onOpenMethods }: Props) {
  const [highlight, setHighlight] = useState<string | null>(null);
  const title = grainTitle(grain);
  const nounMany = grainNoun(grain);

  const colesPoints = useMemo(() => buildPoints(data.skus, "Coles", grain), [data.skus, grain]);
  const wwPoints = useMemo(() => buildPoints(data.skus, "Woolworths", grain), [data.skus, grain]);
  const colesGroups = useMemo(() => groupStats(colesPoints), [colesPoints]);
  const wwGroups = useMemo(() => groupStats(wwPoints), [wwPoints]);

  // Clear highlight when grain changes (labels differ)
  useEffect(() => {
    setHighlight(null);
  }, [grain]);

  return (
    <>
      <header className="hero">
        <p className="eyebrow">Store floor · {title.toLowerCase()} adjacency</p>
        <h1>{locationName} floor maps</h1>
        <p>
          Each store’s own indoor map, drawn from product pin coordinates. Colour shows which{" "}
          {nounMany} sit next to each other — Coles and Woolworths use different map systems, so the
          two plots are separate (not overlaid).
        </p>
        <p className="hero-methods-link">
          <button type="button" className="text-link" onClick={() => onOpenMethods("floor-map")}>
            Methods — how the floor map is built
          </button>
        </p>
      </header>

      <aside className="caveats" aria-label="Floor map caveats">
        <strong>Read this first</strong>
        <p>
          Coordinates come from each retailer’s app. Absolute positions are not comparable across
          banners — only adjacency <em>within</em> a store. Hover a legend item to highlight that{" "}
          {grainNoun(grain, "one")} on the map. Use the Category / Subcategory toggle above to
          change the colour grain.
        </p>
      </aside>

      <div className="floor-grid">
        <StorePlot
          retailer="Coles"
          points={colesPoints}
          groups={colesGroups}
          highlight={highlight}
          onHighlight={setHighlight}
          grain={grain}
        />
        <StorePlot
          retailer="Woolworths"
          points={wwPoints}
          groups={wwGroups}
          highlight={highlight}
          onHighlight={setHighlight}
          grain={grain}
        />
      </div>
    </>
  );
}
