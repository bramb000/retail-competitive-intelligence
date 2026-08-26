import type { Grain, Retailer, SkuRow } from "./types";

export interface BayMixRow {
  label: string;
  count: number;
  pct: number;
}

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
  /** Map colour label (shared comparison taxonomy). */
  label: string;
  count: number;
  color: string;
  /** Woolworths nomenclature for products in this bay. */
  wwLabel: string;
  wwMix: BayMixRow[];
  /** Coles nomenclature for products in this bay. */
  colesLabel: string;
  colesMix: BayMixRow[];
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

/** Shared label used to colour the map (Woolworths-style comparison taxonomy). */
export function groupLabel(sku: SkuRow, grain: Grain): string | null {
  if (grain === "subcategory") {
    return sku.subcategory || sku.shared_label || sku.gold_category || null;
  }
  return sku.shared_label || sku.gold_category || sku.category || null;
}

/** Woolworths Iris label — native for WW SKUs, mapped for Coles SKUs. */
export function wwNomenclatureLabel(sku: SkuRow, grain: Grain): string | null {
  if (sku.retailer === "Woolworths") {
    if (grain === "subcategory") {
      return sku.native_subcategory || sku.native_category || sku.gold_category || null;
    }
    return sku.native_category || sku.gold_category || null;
  }
  if (grain === "subcategory") {
    return (
      sku.ww_mapped_category ||
      sku.coles_mapped_subcategory ||
      sku.coles_mapped_category ||
      null
    );
  }
  return sku.ww_mapped_category || sku.coles_mapped_category || null;
}

/** Coles catalogue label — native for Coles SKUs, mapped for WW SKUs. */
export function colesNomenclatureLabel(sku: SkuRow, grain: Grain): string | null {
  if (sku.retailer === "Coles") {
    if (grain === "subcategory") {
      return sku.native_subcategory || sku.native_category || null;
    }
    return sku.native_category || null;
  }
  if (grain === "subcategory") {
    return sku.coles_mapped_subcategory || sku.coles_mapped_category || null;
  }
  return sku.coles_mapped_category || null;
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

function mixFromCounts(counts: Map<string, number>, total: number): BayMixRow[] {
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
  mapCounts: Map<string, number>;
  wwCounts: Map<string, number>;
  colesCounts: Map<string, number>;
  count: number;
}

/** Expand point-like bays into shelf rectangles along the aisle axis.
 *  Length fills most of the gap to neighbours (small gutter) so runs look continuous.
 */
function expandAisleGroup(bays: RawBay[], depth: number, minLen: number, fill = 0.92): void {
  if (bays.length === 0) return;

  const xs = bays.map((b) => b.cx);
  const ys = bays.map((b) => b.cy);
  const xSpread = Math.max(...xs) - Math.min(...xs);
  const ySpread = Math.max(...ys) - Math.min(...ys);
  const axis: "x" | "y" = xSpread >= ySpread ? "x" : "y";

  // Snap the perpendicular axis to a shared centreline so the run reads as one gondola.
  if (axis === "x") {
    const midY = median(ys);
    for (const b of bays) b.cy = midY;
  } else {
    const midX = median(xs);
    for (const b of bays) b.cx = midX;
  }

  bays.sort((a, b) => (axis === "x" ? a.cx - b.cx : a.cy - b.cy));

  const positions = bays.map((b) => (axis === "x" ? b.cx : b.cy));
  const gaps: number[] = [];
  for (let i = 1; i < positions.length; i++) {
    const g = positions[i] - positions[i - 1];
    if (g > 20) gaps.push(g);
  }
  gaps.sort((a, b) => a - b);
  const typicalGap = gaps[Math.floor(gaps.length / 2)] ?? Math.max(minLen, 120);
  const gutter = Math.max(8, Math.min(18, typicalGap * 0.04));

  for (let i = 0; i < bays.length; i++) {
    const b = bays[i];
    const toPrev = i > 0 ? positions[i] - positions[i - 1] : typicalGap;
    const toNext = i < bays.length - 1 ? positions[i + 1] - positions[i] : typicalGap;
    // Prefer almost-touching shelves; only shrink when neighbours are very close.
    const maxLen = Math.max(24, Math.min(toPrev, toNext) - gutter);
    const preferred = Math.min(typicalGap * 0.92, (toPrev + toNext) * 0.48);
    const len = Math.min(maxLen, Math.max(Math.min(minLen, maxLen), preferred * fill));

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

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
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

/** Pull apart overlapping bboxes along the dominant aisle axis. */
function separateOverlapping(bays: RawBay[]): void {
  if (bays.length < 2) return;
  const xs = bays.map((b) => b.cx);
  const ys = bays.map((b) => b.cy);
  const axis: "x" | "y" =
    Math.max(...xs) - Math.min(...xs) >= Math.max(...ys) - Math.min(...ys) ? "x" : "y";
  bays.sort((a, b) => (axis === "x" ? a.cx - b.cx : a.cy - b.cy));

  for (let i = 1; i < bays.length; i++) {
    const prev = bays[i - 1];
    const cur = bays[i];
    if (axis === "x") {
      if (cur.minX < prev.maxX) {
        const mid = (prev.maxX + cur.minX) / 2;
        const gutter = Math.max(4, (cur.cx - prev.cx) * 0.06);
        prev.maxX = mid - gutter / 2;
        cur.minX = mid + gutter / 2;
        prev.cx = (prev.minX + prev.maxX) / 2;
        cur.cx = (cur.minX + cur.maxX) / 2;
      }
    } else if (cur.minY < prev.maxY) {
      const mid = (prev.maxY + cur.minY) / 2;
      const gutter = Math.max(4, (cur.cy - prev.cy) * 0.06);
      prev.maxY = mid - gutter / 2;
      cur.minY = mid + gutter / 2;
      prev.cy = (prev.minY + prev.maxY) / 2;
      cur.cy = (cur.minY + cur.maxY) / 2;
    }
  }
}

export function buildBayBlocks(skus: SkuRow[], retailer: Retailer, grain: Grain): BayBlock[] {
  const grouped = new Map<string, RawBay>();

  for (const s of skus) {
    if (s.retailer !== retailer) continue;
    if (s.indoor_x == null || s.indoor_y == null) continue;
    const mapLabel = groupLabel(s, grain);
    if (!mapLabel) continue;

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
        mapCounts: new Map(),
        wwCounts: new Map(),
        colesCounts: new Map(),
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
    bay.mapCounts.set(mapLabel, (bay.mapCounts.get(mapLabel) ?? 0) + 1);

    const ww = wwNomenclatureLabel(s, grain);
    if (ww) bay.wwCounts.set(ww, (bay.wwCounts.get(ww) ?? 0) + 1);

    const coles = colesNomenclatureLabel(s, grain);
    if (coles) bay.colesCounts.set(coles, (bay.colesCounts.get(coles) ?? 0) + 1);
  }

  const raw = [...grouped.values()];
  const aisleGroups = new Map<string, RawBay[]>();
  for (const b of raw) {
    const key = `${b.aisle}|${b.side}`;
    const list = aisleGroups.get(key) ?? [];
    list.push(b);
    aisleGroups.set(key, list);
  }

  // Coles pins are sparse — slightly deeper shelves + near-continuous lengths.
  // Keep depth modest so Left/Right faces of an aisle do not visually collide.
  const depth = retailer === "Coles" ? 120 : 130;
  const minLen = retailer === "Coles" ? 180 : 180;
  const fill = retailer === "Coles" ? 0.9 : 0.9;

  for (const list of aisleGroups.values()) {
    const pointLike = list.every((b) => b.maxX - b.minX < 2 && b.maxY - b.minY < 2);
    if (pointLike) {
      expandAisleGroup(list, depth, minLen, fill);
    } else {
      for (const b of list) {
        padBBox(b, 90, minLen * 0.55);
      }
      separateOverlapping(list);
    }
  }

  const gap = 10;
  for (const b of raw) {
    const w = b.maxX - b.minX;
    const h = b.maxY - b.minY;
    const shrinkX = Math.min(gap * 0.35, Math.max(0, w * 0.06));
    const shrinkY = Math.min(gap * 0.35, Math.max(0, h * 0.06));
    b.minX += shrinkX;
    b.maxX -= shrinkX;
    b.minY += shrinkY;
    b.maxY -= shrinkY;
  }

  return raw
    .map((b) => {
      const label = dominantLabel(b.mapCounts);
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
        wwLabel: dominantLabel(b.wwCounts),
        wwMix: mixFromCounts(b.wwCounts, b.count),
        colesLabel: dominantLabel(b.colesCounts),
        colesMix: mixFromCounts(b.colesCounts, b.count),
      };
    })
    .sort((a, b) => b.count - a.count);
}

export function bayProducts(skus: SkuRow[], retailer: Retailer, bayId: string): SkuRow[] {
  return skus
    .filter((s) => s.retailer === retailer && s.bay_key === bayId)
    .sort((a, b) => (a.name ?? "").localeCompare(b.name ?? ""));
}

/** Products at a retailer that map to the shared category / subcategory colour label. */
export function categoryProducts(
  skus: SkuRow[],
  retailer: Retailer,
  categoryLabel: string,
  grain: Grain,
): SkuRow[] {
  return skus
    .filter(
      (s) =>
        s.retailer === retailer &&
        groupLabel(s, grain) === categoryLabel &&
        s.bay_key != null,
    )
    .sort((a, b) => (a.name ?? "").localeCompare(b.name ?? ""));
}

export interface BayInsight {
  id: string;
  title: string;
  summary: string;
  detail: string;
  count: number;
  pct: number;
}

function normLabel(s: string): string {
  return s.trim().toLowerCase().replace(/\s+/g, " ");
}

function labelsOverlap(a: string, b: string): boolean {
  const na = normLabel(a);
  const nb = normLabel(b);
  if (na === nb) return true;
  if (na.includes(nb) || nb.includes(na)) return true;
  const wa = new Set(na.split(" ").filter((w) => w.length > 2));
  const wb = nb.split(" ").filter((w) => w.length > 2);
  let hit = 0;
  for (const w of wb) if (wa.has(w)) hit += 1;
  return hit >= 2;
}

/** Second-layer bay findings — empty when the bay is a clean single-group fixture. */
export function deriveBayInsights(
  products: SkuRow[],
  bay: BayBlock,
  grain: Grain,
): BayInsight[] {
  if (products.length < 4) return [];
  const total = products.length;
  const insights: BayInsight[] = [];
  const minShare = 0.1;
  const minCount = 3;

  // 1) Assortment mix — map colour is dominant, but other groups share the bay.
  const mapCounts = new Map<string, number>();
  for (const p of products) {
    const lab = groupLabel(p, grain);
    if (!lab) continue;
    mapCounts.set(lab, (mapCounts.get(lab) ?? 0) + 1);
  }
  const secondary = [...mapCounts.entries()]
    .filter(([lab]) => lab !== bay.label)
    .sort((a, b) => b[1] - a[1]);
  const secondaryN = secondary.reduce((s, [, n]) => s + n, 0);
  if (secondaryN >= minCount && secondaryN / total >= minShare) {
    const top = secondary.slice(0, 3).map(([lab, n]) => `${lab} (${n})`).join(", ");
    const pct = Math.round((1000 * secondaryN) / total) / 10;
    insights.push({
      id: "assortment-mix",
      title: "Mixed assortment on this bay",
      summary: `${pct}% of products sit outside the map colour (${bay.label}).`,
      detail: `Placement puts them on this bay, but the comparison taxonomy labels them as ${top}. This is often deliberate cross-merchandising (for example cheese crackers in deli), not a map error.`,
      count: secondaryN,
      pct,
    });
  }

  // 2) Catalogue vs shared taxonomy — native retailer label ≠ comparison label.
  const mismatchPairs = new Map<string, { native: string; mapped: string; n: number }>();
  for (const p of products) {
    const native = p.native_category?.trim();
    if (!native) continue;
    const mapped =
      p.retailer === "Coles"
        ? wwNomenclatureLabel(p, grain)
        : colesNomenclatureLabel(p, grain);
    if (!mapped || labelsOverlap(native, mapped)) continue;
    const key = `${native}→${mapped}`;
    const cur = mismatchPairs.get(key) ?? { native, mapped, n: 0 };
    cur.n += 1;
    mismatchPairs.set(key, cur);
  }
  const topMismatch = [...mismatchPairs.values()].sort((a, b) => b.n - a.n)[0];
  if (topMismatch && topMismatch.n >= minCount && topMismatch.n / total >= minShare) {
    const pct = Math.round((1000 * topMismatch.n) / total) / 10;
    const catalogueBanner = products[0]?.retailer ?? "Retailer";
    const otherBanner = catalogueBanner === "Coles" ? "Woolworths-style" : "Coles-style";
    insights.push({
      id: "taxonomy-split",
      title: "Catalogue and comparison labels differ",
      summary: `${topMismatch.n} products: ${catalogueBanner} catalogue “${topMismatch.native}” vs ${otherBanner} “${topMismatch.mapped}”.`,
      detail: `The store map uses physical bay placement. The ${otherBanner} name comes from the shared comparison taxonomy, which can put the same SKU in a different aisle family (biscuits vs deli, snacks vs bakery). Both can be correct for their system.`,
      count: topMismatch.n,
      pct,
    });
  }

  return insights;
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

export interface AdjacentCategoryRow {
  label: string;
  count: number;
  color: string;
  pct: number;
}

export interface CategoryStoreContext {
  totalBays: number;
  categoryBays: number;
  categoryPct: number;
  adjacent: AdjacentCategoryRow[];
}

function sameShelfRun(a: BayBlock, b: BayBlock): boolean {
  if (a.aisle !== b.aisle) return false;
  const sa = a.side.trim().toLowerCase();
  const sb = b.side.trim().toLowerCase();
  return sa === sb;
}

function shelfRunAxis(blocks: BayBlock[]): "x" | "y" {
  if (blocks.length < 2) return "x";
  const xs = blocks.map((b) => b.cx);
  const ys = blocks.map((b) => b.cy);
  return Math.max(...xs) - Math.min(...xs) >= Math.max(...ys) - Math.min(...ys) ? "x" : "y";
}

/**
 * Immediate neighbours on the same shelf run (same aisle + side), ordered along the run.
 * Opposite faces across the walkway are excluded. Uses bay order, not a pixel gap —
 * Coles pins are often >80 units apart after expansion.
 */
export function findAdjacentBays(blocks: BayBlock[], bay: BayBlock): BayBlock[] {
  const run = blocks.filter((b) => sameShelfRun(bay, b));
  if (run.length < 2) return [];
  const axis = shelfRunAxis(run);
  const ordered = [...run].sort((a, b) => (axis === "x" ? a.cx - b.cx : a.cy - b.cy));
  const idx = ordered.findIndex((b) => b.id === bay.id);
  if (idx < 0) return [];
  const neighbours: BayBlock[] = [];
  if (idx > 0) neighbours.push(ordered[idx - 1]);
  if (idx < ordered.length - 1) neighbours.push(ordered[idx + 1]);
  return neighbours;
}

/** Bay share and neighbouring categories for one store. */
export function categoryStoreContext(blocks: BayBlock[], categoryLabel: string): CategoryStoreContext {
  const totalBays = blocks.length;
  const categoryBlocks = blocks.filter((b) => b.label === categoryLabel);
  const categoryBays = categoryBlocks.length;

  const adjCounts = new Map<string, number>();
  for (const b of categoryBlocks) {
    for (const n of findAdjacentBays(blocks, b)) {
      if (n.label === categoryLabel) continue;
      adjCounts.set(n.label, (adjCounts.get(n.label) ?? 0) + 1);
    }
  }

  const adjTotal = [...adjCounts.values()].reduce((s, n) => s + n, 0);
  const adjacent = [...adjCounts.entries()]
    .map(([label, count]) => ({
      label,
      count,
      color: colorFor(label),
      pct: adjTotal > 0 ? Math.round((1000 * count) / adjTotal) / 10 : 0,
    }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 6);

  return {
    totalBays,
    categoryBays,
    categoryPct: totalBays > 0 ? Math.round((1000 * categoryBays) / totalBays) / 10 : 0,
    adjacent,
  };
}

/** Bays whose map colour matches the category / subcategory label. */
export function categoryBayBlocks(blocks: BayBlock[], categoryLabel: string): BayBlock[] {
  return blocks
    .filter((b) => b.label === categoryLabel)
    .sort((a, b) => {
      const aisleCmp = a.aisle.localeCompare(b.aisle, undefined, { numeric: true });
      if (aisleCmp !== 0) return aisleCmp;
      const sideCmp = a.side.localeCompare(b.side);
      if (sideCmp !== 0) return sideCmp;
      return a.bayNum.localeCompare(b.bayNum, undefined, { numeric: true });
    });
}

export function formatBayLocation(bay: Pick<BayBlock, "aisle" | "side" | "bayNum">): string {
  const side =
    bay.side === "_" || bay.side === "?"
      ? "centre"
      : /side$/i.test(bay.side)
        ? bay.side
        : `${bay.side} side`;
  return `Aisle ${bay.aisle} · ${side} · bay ${bay.bayNum}`;
}

export function retailerBayMix(
  products: SkuRow[],
  retailer: Retailer,
  grain: Grain,
): { label: string; mix: BayMixRow[] } {
  const counts = new Map<string, number>();
  for (const p of products) {
    const lab =
      retailer === "Woolworths"
        ? wwNomenclatureLabel(p, grain)
        : colesNomenclatureLabel(p, grain);
    if (!lab) continue;
    counts.set(lab, (counts.get(lab) ?? 0) + 1);
  }
  const mix = mixFromCounts(counts, products.length);
  return { label: mix[0]?.label ?? "—", mix };
}

/** Resolve a map colour label to a department row for CategoryDrill. */
export function findDepartmentForLabel(
  departments: { id: string; shared_label: string; coles_skus: number; ww_skus: number }[],
  label: string,
  hintProducts: SkuRow[] = [],
  grain: Grain = "category",
): { id: string; shared_label: string } | null {
  const matches = departments.filter((d) => d.shared_label === label);
  if (matches.length === 0) return null;
  if (matches.length === 1) return matches[0];

  if (grain === "subcategory" && hintProducts.length > 0) {
    const counts = new Map<string, number>();
    for (const p of hintProducts) {
      const id = p.subcategory_id;
      if (!id || !matches.some((m) => m.id === id)) continue;
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
    const best = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
    if (best) {
      const row = matches.find((m) => m.id === best[0]);
      if (row) return row;
    }
  }

  return [...matches].sort(
    (a, b) => b.coles_skus + b.ww_skus - (a.coles_skus + a.ww_skus),
  )[0];
}
