/** Compact brand marks — used sparingly in legends/headers, not every cell. */

export type Banner = "Coles" | "Woolworths";

interface BannerMarkProps {
  banner: Banner;
  size?: "sm" | "md";
  /** Hide text label for icon-only use (headers that already say Coles/WW nearby). */
  label?: "none" | "short" | "full";
  className?: string;
}

export function BannerMark({
  banner,
  size = "sm",
  label = "none",
  className = "",
}: BannerMarkProps) {
  const title = banner === "Coles" ? "Coles" : "Woolworths";
  const short = banner === "Coles" ? "Coles" : "WW";
  const show = label === "none" ? null : label === "short" ? short : title;

  return (
    <span
      className={`banner-mark banner-mark--${banner === "Coles" ? "coles" : "ww"} banner-mark--${size} ${className}`.trim()}
      title={title}
      aria-label={title}
    >
      {banner === "Coles" ? <ColesGlyph /> : <WoolworthsGlyph />}
      {show ? <span className="banner-mark-text">{show}</span> : null}
    </span>
  );
}

/** Coles-style red wordmark glyph (simplified, for UI marking only). */
function ColesGlyph() {
  return (
    <svg className="banner-mark-svg" viewBox="0 0 28 28" aria-hidden="true" focusable="false">
      <rect width="28" height="28" rx="6" fill="#E87722" />
      <text
        x="14"
        y="19"
        textAnchor="middle"
        fill="#fff"
        fontFamily="Inter, system-ui, sans-serif"
        fontWeight="700"
        fontSize="14"
      >
        C
      </text>
    </svg>
  );
}

/** Woolworths-style green mark (simplified, for UI marking only). */
function WoolworthsGlyph() {
  return (
    <svg className="banner-mark-svg" viewBox="0 0 28 28" aria-hidden="true" focusable="false">
      <rect width="28" height="28" rx="6" fill="#17823C" />
      <text
        x="14"
        y="19"
        textAnchor="middle"
        fill="#fff"
        fontFamily="Inter, system-ui, sans-serif"
        fontWeight="700"
        fontSize="13"
      >
        W
      </text>
    </svg>
  );
}

/** One pair for dual columns — keeps logo count low. */
export function DualBannerMarks({ size = "sm" }: { size?: "sm" | "md" }) {
  return (
    <span className="dual-banner-marks" aria-label="Coles then Woolworths">
      <BannerMark banner="Coles" size={size} />
      <span className="dual-banner-sep" aria-hidden="true">
        /
      </span>
      <BannerMark banner="Woolworths" size={size} />
    </span>
  );
}

/** Persistent legend — place once per view, not per row. */
export function BannerLegend() {
  return (
    <div className="banner-legend" aria-label="Retailer colour key">
      <BannerMark banner="Coles" size="sm" label="full" />
      <BannerMark banner="Woolworths" size="sm" label="full" />
      <span className="banner-legend-hint muted">Colour in tables matches these marks</span>
    </div>
  );
}
