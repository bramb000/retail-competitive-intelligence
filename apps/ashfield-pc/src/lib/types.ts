export type Retailer = "Coles" | "Woolworths";

export type VennSide = "coles_only" | "matched" | "ww_only";

export interface DashboardFilters {
  side: VennSide | null;
  bayKey: string | null;
  subcategory: string | null;
  retailer: Retailer | null;
  matchPairId: string | null;
  search: string;
}

export const emptyFilters = (): DashboardFilters => ({
  side: null,
  bayKey: null,
  subcategory: null,
  retailer: null,
  matchPairId: null,
  search: "",
});

export interface SkuRow {
  retailer: Retailer;
  id: number;
  name: string;
  brand: string | null;
  subcategory: string | null;
  price_now: number | null;
  price_was: number | null;
  is_promo: boolean;
  bay_key: string | null;
  indoor_x: number | null;
  indoor_y: number | null;
  location_class: string | null;
  side: VennSide;
  match_partner_id: number | null;
  match_partner_name: string | null;
  match_score: number | null;
}

export interface PcDashboardData {
  meta: {
    category: string;
    stores: Record<string, string>;
    suburb: string;
    generated_at: string;
    caveats: string[];
  };
  kpis: {
    coles_skus: number;
    ww_skus: number;
    sku_ratio_coles_per_ww: number | null;
    matched_pairs: number;
    coles_only: number;
    ww_only: number;
    coles_pct_store_bays: number;
    ww_pct_store_bays: number;
    coles_pct_promo: number;
    ww_pct_promo: number;
    coles_median_price: number | null;
    ww_median_price: number | null;
    insights: {
      assortment: string;
      overlap: string;
      space: string;
      promo: string;
    };
  };
  space: Array<{
    retailer: Retailer;
    bay_count: number;
    store_bay_count: number;
    pct_store_bays: number;
    placed_skus: number;
    facing_sum: number | null;
  }>;
  venn: {
    coles_only: number;
    matched: number;
    ww_only: number;
    examples: Record<VennSide, string[]>;
  };
  matches: Array<{
    coles_id: number;
    ww_id: number;
    coles_name: string;
    ww_name: string;
    brand: string | null;
    score: number;
    ww_l1: string | null;
  }>;
  skus: SkuRow[];
  price_distribution: Array<{
    retailer: Retailer;
    n: number;
    median: number | null;
    p25: number | null;
    p75: number | null;
    min: number | null;
    max: number | null;
  }>;
  price_histogram: Array<{
    retailer: Retailer;
    bin_start: number;
    bin_end: number;
    label: string;
    count: number;
  }>;
  price_by_subcategory: Array<{
    retailer: Retailer;
    subcategory: string;
    skus: number;
    median_price: number | null;
    pct_promo: number;
  }>;
  promo_ladder: Array<{
    retailer: Retailer;
    promo_skus: number;
    median_was: number | null;
    median_now: number | null;
    median_regular: number | null;
  }>;
}

export const COLES = "#E87722";
export const WW = "#17823C";
export const PURPLE = "#7B4FE8";

export function money(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `$${n.toFixed(2)}`;
}

export function intFmt(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-AU");
}

export function pctFmt(n: number | null | undefined, digits = 0): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

export function filterSkus(skus: SkuRow[], filters: DashboardFilters): SkuRow[] {
  return skus.filter((s) => {
    if (filters.side && s.side !== filters.side) return false;
    if (filters.bayKey && s.bay_key !== filters.bayKey) return false;
    if (filters.subcategory && (s.subcategory || "(none)") !== filters.subcategory) return false;
    if (filters.retailer && s.retailer !== filters.retailer) return false;
    if (filters.matchPairId) {
      const [a, b] = filters.matchPairId.split(":");
      const cid = Number(a);
      const wid = Number(b);
      if (!(s.id === cid || s.id === wid)) return false;
    }
    if (filters.search) {
      const q = filters.search.toLowerCase();
      const hay = `${s.name} ${s.brand ?? ""} ${s.id}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

export function activeFilterChips(filters: DashboardFilters): Array<{ key: keyof DashboardFilters; label: string }> {
  const chips: Array<{ key: keyof DashboardFilters; label: string }> = [];
  if (filters.side === "coles_only") chips.push({ key: "side", label: "Coles only" });
  if (filters.side === "matched") chips.push({ key: "side", label: "Matched" });
  if (filters.side === "ww_only") chips.push({ key: "side", label: "WW only" });
  if (filters.bayKey) chips.push({ key: "bayKey", label: `Bay ${filters.bayKey}` });
  if (filters.subcategory) chips.push({ key: "subcategory", label: filters.subcategory });
  if (filters.retailer) chips.push({ key: "retailer", label: filters.retailer });
  if (filters.matchPairId) chips.push({ key: "matchPairId", label: "Matched pair" });
  if (filters.search.trim()) chips.push({ key: "search", label: `Search: ${filters.search}` });
  return chips;
}
