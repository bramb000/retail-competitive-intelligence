export type Retailer = "Coles" | "Woolworths";

export type BoardTab = "departments" | "dominance" | "price" | "kvi" | "macrospace";

export type Grain = "category" | "subcategory";

export function grainNoun(grain: Grain, count: "one" | "many" = "many"): string {
  if (grain === "category") return count === "one" ? "category" : "categories";
  return count === "one" ? "subcategory" : "subcategories";
}

export function grainTitle(grain: Grain): string {
  return grain === "category" ? "Category" : "Subcategory";
}

export interface LocationMeta {
  id: string;
  name: string;
  state?: string;
  stores?: Record<string, string>;
  active?: boolean;
}

export interface DepartmentRow {
  id: string;
  location_id?: string;
  parent_category?: string;
  shared_label: string;
  coles_label: string;
  ww_label: string;
  blurb: string;
  taxonomy?: "glossary" | "observed" | string;
  data_status?: "ready" | "awaiting_scrape" | string;
  gold_keys?: string[];
  /** False when either banner has too little of this category on numbered bays. */
  bay_comparable?: boolean;
  coles_bay_coverage_pct?: number | null;
  ww_bay_coverage_pct?: number | null;
  coles_dept_fixture_pct?: number | null;
  ww_dept_fixture_pct?: number | null;
  coles_skus: number;
  ww_skus: number;
  coles_pct_store_bays: number | null;
  ww_pct_store_bays: number | null;
  coles_bay_count: number | null;
  ww_bay_count: number | null;
  coles_store_bay_count?: number | null;
  ww_store_bay_count?: number | null;
  coles_pct_promo: number | null;
  ww_pct_promo: number | null;
  coles_median_price: number | null;
  ww_median_price: number | null;
  coles_pct_store_skus?: number | null;
  ww_pct_store_skus?: number | null;
  median_gap_pct_coles_vs_ww?: number | null;
  in_venn: "both" | "coles_only" | "ww_only" | "unknown";
}

export interface DominanceRow {
  id: string;
  shared_label: string;
  coles_label: string;
  ww_label: string;
  blurb: string;
  dominant: "Coles" | "Woolworths" | null;
  verdict: "bay_dominant" | "assortment_dominant" | "contested" | "one_sided";
  strength: number;
  coles_skus: number;
  ww_skus: number;
  coles_pct_store_skus: number | null;
  ww_pct_store_skus: number | null;
  sku_gap_pp: number | null;
  coles_pct_store_bays: number | null;
  ww_pct_store_bays: number | null;
  bay_gap_pp: number | null;
}

export interface PriceCompetitionRow {
  id: string;
  shared_label: string;
  coles_label: string;
  ww_label: string;
  blurb: string;
  status: "hot_gap" | "competing" | "aligned" | "not_comparable";
  heat: "hot" | "warm" | "cool" | null;
  cheaper_on_median: "Coles" | "Woolworths" | "tie" | null;
  median_gap_pct_coles_vs_ww: number | null;
  coles_median_price: number | null;
  ww_median_price: number | null;
  coles_pct_promo: number | null;
  ww_pct_promo: number | null;
  promo_gap_pp: number | null;
  coles_skus: number;
  ww_skus: number;
}

export interface GrainSlice {
  departments: DepartmentRow[];
  scoreboards: {
    dominance: DominanceRow[];
    price_competition: PriceCompetitionRow[];
  };
}

export interface KviSkuBrief {
  id: number | null;
  name: string | null;
  brand: string | null;
  price_now: number | null;
  is_promo: boolean;
  category: string | null;
  own_brand: boolean;
  pack_label?: string | null;
  unit_price?: string | null;
  unit_rate?: number | null;
  unit_family?: string | null;
}

export interface KnownValueRow {
  kvi_id: string;
  label: string;
  perception_role: string;
  notes: string;
  target_pack?: string | null;
  cheaper:
    | "Coles"
    | "Woolworths"
    | "tie"
    | "coles_only"
    | "ww_only"
    | "not_comparable"
    | null;
  gap_pct_coles_vs_ww: number | null;
  comparable?: boolean;
  compare_basis?: "unit_price" | "similar_pack" | null;
  incomparable_reason?: string | null;
  coles: KviSkuBrief | null;
  ww: KviSkuBrief | null;
}

export interface SkuRow {
  retailer: Retailer;
  id: number;
  name: string | null;
  brand: string | null;
  category: string | null;
  subcategory_id?: string | null;
  gold_category?: string | null;
  shared_label: string | null;
  subcategory: string | null;
  /** Coles catalogue department label, or WW Iris L0 breadcrumb. */
  native_category?: string | null;
  /** WW Iris L1 breadcrumb (Woolworths only). */
  native_subcategory?: string | null;
  /** How the other retailer would label this aisle family (for macrospace comparison). */
  coles_mapped_category?: string | null;
  coles_mapped_subcategory?: string | null;
  ww_mapped_category?: string | null;
  price_now: number | null;
  price_was: number | null;
  is_promo: boolean;
  bay_key: string | null;
  indoor_x: number | null;
  indoor_y: number | null;
  location_class: string | null;
}

export interface StoreCiData {
  meta: {
    product: string;
    grain?: string;
    default_grain?: Grain;
    assumes_full_store?: boolean;
    location_id?: string;
    location_name?: string;
    suburb: string;
    stores: Record<string, string>;
    locations?: LocationMeta[];
    generated_at: string;
    gold_db?: string;
    status: "ready" | "waiting_for_data" | string;
    caveats: string[];
  };
  location?: {
    id: string;
    name: string;
    state?: string;
    stores: Record<string, string>;
    store_totals: StoreCiData["store_totals"];
    departments: DepartmentRow[];
    scoreboards: StoreCiData["scoreboards"];
    skus: SkuRow[];
    matches: StoreCiData["matches"];
  };
  store_totals: {
    location_id?: string;
    coles_skus: number;
    ww_skus: number;
    coles_mapped_skus: number;
    ww_mapped_skus: number;
    unmapped_coles: number;
    unmapped_ww: number;
    excluded_non_bay_skus?: number;
    departments: number;
    departments_ready?: number;
    departments_awaiting?: number;
    subcategories?: number;
    matched_pairs: number;
  };
  grains?: {
    category: GrainSlice;
    subcategory: GrainSlice;
  };
  glossary: Array<{
    shared_label: string | null;
    coles_slug: string | null;
    coles_label: string | null;
    ww_label: string | null;
    notes: string | null;
  }>;
  departments: DepartmentRow[];
  scoreboards?: {
    dominance: DominanceRow[];
    price_competition: PriceCompetitionRow[];
    known_value: KnownValueRow[];
    known_value_summary: {
      defined: number;
      both_priced: number;
      comparable?: number;
      not_comparable?: number;
      coles_cheaper: number;
      ww_cheaper: number;
      ties: number;
      coles_only: number;
      ww_only: number;
    };
  };
  matches: Array<{
    coles_id: number;
    ww_id: number;
    coles_name: string | null;
    ww_name: string | null;
    brand: string | null;
    score: number | null;
    ww_l0: string | null;
    ww_l1: string | null;
    category?: string | null;
    subcategory_id?: string | null;
    location_id?: string;
  }>;
  skus: SkuRow[];
  space: Array<{
    retailer: Retailer;
    category: string;
    bay_count: number;
    store_bay_count: number;
    pct_store_bays: number;
    placed_skus: number;
  }>;
  pricing: Array<{
    retailer: Retailer;
    category: string;
    sku_count: number;
    median_price: number | null;
    pct_on_promo: number | null;
  }>;
}

export const COLES = "#E87722";
export const WW = "#17823C";

export function money(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `$${n.toFixed(2)}`;
}

export function intFmt(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-AU");
}

/** Bay counts may be fractional (SKU-weighted share of a mixed bay). */
export function bayFmt(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const rounded = Math.abs(n - Math.round(n)) < 0.05 ? Math.round(n) : Math.round(n * 10) / 10;
  return rounded.toLocaleString("en-AU", {
    minimumFractionDigits: Number.isInteger(rounded) ? 0 : 1,
    maximumFractionDigits: 1,
  });
}

export function pctFmt(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

export function signedPct(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}%`;
}

export function skuRatio(coles: number, ww: number): string {
  if (!ww) return coles ? "Coles only" : "—";
  if (!coles) return "WW only";
  return `${(coles / ww).toFixed(1)}× Coles`;
}

export function gapLabel(gap: number | null | undefined): string {
  if (gap == null || Number.isNaN(gap)) return "—";
  if (Math.abs(gap) < 0.05) return "Tied";
  if (gap < 0) return `Coles ${Math.abs(gap).toFixed(1)}% cheaper`;
  return `WW ${gap.toFixed(1)}% cheaper`;
}
