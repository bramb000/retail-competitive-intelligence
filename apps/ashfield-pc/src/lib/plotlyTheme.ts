import type { Layout, Config } from "plotly.js";
import { COLES, WW } from "./types";

export const plotConfig: Partial<Config> = {
  displayModeBar: false,
  responsive: true,
};

export function baseLayout(overrides: Partial<Layout> = {}): Partial<Layout> {
  return {
    font: { family: "Inter, system-ui, sans-serif", color: "#090909", size: 12 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(255,255,255,0.55)",
    margin: { t: 28, r: 16, b: 48, l: 52 },
    legend: { orientation: "h", y: 1.12, x: 0 },
    hoverlabel: {
      bgcolor: "#111",
      font: { family: "Inter, system-ui, sans-serif", size: 12, color: "#fff" },
      bordercolor: "#7B4FE8",
    },
    ...overrides,
  };
}

export function bannerColor(retailer: string): string {
  return retailer === "Coles" ? COLES : WW;
}
