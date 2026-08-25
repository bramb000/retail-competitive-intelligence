import type { Grain, Retailer, SkuRow } from "./types";

export interface BayBlock {
  id: string;
  aisle: string;
  side: string;
  bayNum: string;
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  cx: number;
  cy: number;
  label: string;
  count: number;
  color: string;
  mix: Array<{ label: string; count: number; pct: number }>;
}

export interface WorldBounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  width: number;
  height: number;
}

export interface CategoryGroup {
  label: string;
  count: number;
  bays: number;
  color: string;
}

/** Rich, readable palette — stable hash by label. */
export const FLOOR_PALETTE = [
  "#FF6B8A",
  "#5B8DEF",
  "#45C4A0",
  "#FFB347",
  "#B388FF",
  "#4DD0E1",
  "#F06292",
  "#81C784",
  "#FFD54F",
  "#7986CB",
  "#4DB6AC",
  "#FF8A65",
  "#BA68C8",
  "#64B5F6",
  "#AED581",
  "#FFB74D",
  "#9575CD",
  "#4FC3F7",
  "#E57373",
  "#DCE775",
];

export function colorFor(label: string): string {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) >>> 0;
  return FLOOR_PALETTE[h % FLOOR_PALETTE.length];
}

export function groupLabel(sku: SkuRow, grain: Grain): string | null {
  if (grain === "subcategory") {
    return sku.subcategory || sku.shared_label || sku.gold_category || null;
  }
  return sku.shared_label || sku.gold_category || sku.category || null;
}

function parseBayKey(bayKey: string | null): { aisle: string; side: string; bayNum: string } {
  if (!bayKey) return { aisle: "?", side: "?", bayNum: "?" };
  const [aisle = "?", side = "?", bayNum = "?"] = bayKey.split("|");
  return { aisle, side, bayNum };
}

function dominantLabel(counts: Map<string, number>): string {
  let best = "Unknown";
  let bestN = 0;
  for (const [label, n] of counts) {
    if (n > bestN) {
      best = label;
      bestN = n;
    }
  }
  return best;
}

function mixFromCounts(counts: Map<string, number>, total: number): BayBlock["mix"] {
  return [...counts.entries()]
    .map(([label, count]) => ({
      label,
      count,
      pct: total > 0 ? Math.round((1000 * count) / total) / 10 : 0,
    }))
    .sort((a, b) => b.count - a.count);
}

interface RawBay {
  id: string;
  aisle: string;
  side: string;
  bayNum: string;
  cx: number;
  cy: number;
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  counts: Map<string, number>;
  count: number;
}

/** Expand point-like bays into shelf rectangles along the aisle axis. */
function expandAisleGroup(bays: RawBay[], depth: number, minLen: number): void {
  if (bays.length === 0) return;

  const xs = bays.map((b) => b.cx);
  const ys = bays.map((b) => b.cy);
  const xSpread = Math.max(...xs) - Math.min(...xs);
  const ySpread = Math.max(...ys) - Math.min(...ys);
  const axis: "x" | "y" = xSpread >= ySpread ? "x" : "y";

  bays.sort((a, b) => (axis === "x" ? a.cx - b.cx : a.cy - b.cy));

  const positions = bays.map((b) => (axis === "x" ? b.cx : b.cy));
  const gaps: number[] = [];
  for (let i = 1; i < positions.length; i++) {
    const g = positions[i] - positions[i - 1];
    if (g > 20) gaps.push(g);
  }
  gaps.sort((a, b) => a - b);
  const typicalGap = gaps[Math.floor(gaps.length / 2)] ?? minLen * 1.15;

  for (let i = 0; i < bays.length; i++) {
    const b = bays[i];
    const prev = i > 0 ? positions[i] - positions[i - 1] : typicalGap;
    const next = i < bays.length - 1 ? positions[i + 1] - positions[i] : typicalGap;
    const len = Math.max(minLen, Math.min(typicalGap * 1.05, (prev + next) * 0.48));

    const halfLen = len / 2;
    const halfDepth = depth / 2;

    if (axis === "x") {
      b.minX = b.cx - halfLen;
      b.maxX = b.cx + halfLen;
      b.minY = b.cy - halfDepth;
      b.maxY = b.cy + halfDepth;
    } else {
      b.minX = b.cx - halfDepth;
      b.maxX = b.cx + halfDepth;
      b.minY = b.cy - halfLen;
      b.maxY = b.cy + halfLen;
    }
  }
}

function padBBox(b: RawBay, minW: number, minH: number): void {
  const w = b.maxX - b.minX;
  const h = b.maxY - b.minY;
  if (w < minW) {
    const pad = (minW - w) / 2;
    b.minX -= pad;
    b.maxX += pad;
  }
  if (h < minH) {
    const pad = (minH - h) / 2;
    b.minY -= pad;
    b.maxY += pad;
  }
  b.cx = (b.minX + b.maxX) / 2;
  b.cy = (b.minY + b.maxY) / 2;
}

export function buildBayBlocks(skus: SkuRow[], retailer: Retailer, grain: Grain): BayBlock[] {
  const grouped = new Map<string, RawBay>();

  for (const s of skus) {
    if (s.retailer !== retailer) continue;
    if (s.indoor_x == null || s.indoor_y == null) continue;
    const label = groupLabel(s, grain);
    if (!label) continue;

    const id = s.bay_key ?? `${Math.round(s.indoor_x)}|${Math.round(s.indoor_y)}`;
    const { aisle, side, bayNum } = parseBayKey(s.bay_key);
    let bay = grouped.get(id);
    if (!bay) {
      bay = {
        id,
        aisle,
        side,
        bayNum,
        cx: s.indoor_x,
        cy: s.indoor_y,
        minX: s.indoor_x,
        maxX: s.indoor_x,
        minY: s.indoor_y,
        maxY: s.indoor_y,
        counts: new Map(),
        count: 0,
      };
      grouped.set(id, bay);
    }
    bay.minX = Math.min(bay.minX, s.indoor_x);
    bay.maxX = Math.max(bay.maxX, s.indoor_x);
    bay.minY = Math.min(bay.minY, s.indoor_y);
    bay.maxY = Math.max(bay.maxY, s.indoor_y);
    bay.cx = (bay.minX + bay.maxX) / 2;
    bay.cy = (bay.minY + bay.maxY) / 2;
    bay.count += 1;
    bay.counts.set(label, (bay.counts.get(label) ?? 0) + 1);
  }

  const raw = [...grouped.values()];
  const aisleGroups = new Map<string, RawBay[]>();
  for (const b of raw) {
    const key = `${b.aisle}|${b.side}`;
    const list = aisleGroups.get(key) ?? [];
    list.push(b);
    aisleGroups.set(key, list);
  }

  const depth = retailer === "Coles" ? 150 : 130;
  const minLen = retailer === "Coles" ? 260 : 180;

  for (const list of aisleGroups.values()) {
    const pointLike = list.every((b) => b.maxX - b.minX < 2 && b.maxY - b.minY < 2);
    if (pointLike) {
      expandAisleGroup(list, depth, minLen);
    } else {
      for (const b of list) {
        padBBox(b, 90, minLen * 0.55);
      }
    }
  }

  // Shrink slightly for visual aisle gaps between bays
  const gap = retailer === "Coles" ? 14 : 10;
  for (const b of raw) {
    b.minX += gap * 0.35;
    b.maxX -= gap * 0.35;
    b.minY += gap * 0.35;
    b.maxY -= gap * 0.35;
  }

  return raw
    .map((b) => {
      const label = dominantLabel(b.counts);
      return {
        id: b.id,
        aisle: b.aisle,
        side: b.side,
        bayNum: b.bayNum,
        minX: b.minX,
        maxX: b.maxX,
        minY: b.minY,
        maxY: b.maxY,
        cx: (b.minX + b.maxX) / 2,
        cy: (b.minY + b.maxY) / 2,
        label,
        count: b.count,
        color: colorFor(label),
        mix: mixFromCounts(b.counts, b.count),
      };
    })
    .sort((a, b) => b.count - a.count);
}

export function worldBounds(blocks: BayBlock[], padRatio = 0.06): WorldBounds {
  if (blocks.length === 0) {
    return { minX: 0, maxX: 1, minY: 0, maxY: 1, width: 1, height: 1 };
  }
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const b of blocks) {
    minX = Math.min(minX, b.minX);
    maxX = Math.max(maxX, b.maxX);
    minY = Math.min(minY, b.minY);
    maxY = Math.max(maxY, b.maxY);
  }
  const padX = (maxX - minX) * padRatio || 80;
  const padY = (maxY - minY) * padRatio || 80;
  minX -= padX;
  maxX += padX;
  minY -= padY;
  maxY += padY;
  return { minX, maxX, minY, maxY, width: maxX - minX, height: maxY - minY };
}

export function categoryGroups(blocks: BayBlock[]): CategoryGroup[] {
  const acc = new Map<string, { count: number; bays: number; color: string }>();
  for (const b of blocks) {
    const cur = acc.get(b.label) ?? { count: 0, bays: 0, color: b.color };
    cur.count += b.count;
    cur.bays += 1;
    acc.set(b.label, cur);
  }
  return [...acc.entries()]
    .map(([label, v]) => ({ label, ...v }))
    .sort((a, b) => b.count - a.count);
}

export function hitTestBay(blocks: BayBlock[], wx: number, wy: number): BayBlock | null {
  // Prefer smaller bays when overlapping (drawn on top)
  const hits = blocks.filter((b) => wx >= b.minX && wx <= b.maxX && wy >= b.minY && wy <= b.maxY);
  if (hits.length === 0) return null;
  hits.sort((a, b) => {
    const areaA = (a.maxX - a.minX) * (a.maxY - a.minY);
    const areaB = (b.maxX - b.minX) * (b.maxY - b.minY);
    return areaA - areaB;
  });
  return hits[0];
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}
