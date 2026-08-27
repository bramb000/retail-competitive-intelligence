import type { Grain } from "../lib/types";

/** Parse L1 blurb: "{parent} subcategory · Coles parent: X · WW parent: Y" */
function parseSubcategoryBlurb(blurb: string): {
  parentAisle: string;
  colesParent: string;
  wwParent: string;
} | null {
  const match = blurb.match(/^(.+?) subcategory · Coles parent: (.+?) · WW parent: (.+)$/);
  if (!match) return null;
  return {
    parentAisle: match[1].trim(),
    colesParent: match[2].trim(),
    wwParent: match[3].trim(),
  };
}

interface Props {
  blurb: string;
  grain: Grain;
  parentCategory?: string | null;
}

/** Multi-line taxonomy context under a scoreboard row title — keeps tables within the panel. */
export function ScoreboardRowMeta({ blurb, grain, parentCategory }: Props) {
  if (!blurb) return null;

  if (grain === "subcategory") {
    const parsed = parseSubcategoryBlurb(blurb);
    const parent = parentCategory?.trim() || parsed?.parentAisle;
    if (parent && parsed) {
      return (
        <div className="scoreboard-row-meta muted tiny">
          <div className="scoreboard-row-meta-line">
            Parent aisle · <span className="scoreboard-row-meta-strong">{parent}</span>
          </div>
          <div className="scoreboard-row-meta-line coles-text">
            Coles parent · {parsed.colesParent}
          </div>
          <div className="scoreboard-row-meta-line ww-text">
            Woolworths parent · {parsed.wwParent}
          </div>
        </div>
      );
    }
  }

  return <div className="scoreboard-row-meta muted tiny blurb-line">{blurb}</div>;
}
