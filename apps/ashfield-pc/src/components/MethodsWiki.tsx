/** In-app Methods wiki (mirrors apps/ashfield-pc/METHODS.md). Not shown on the home dashboard. */

export function MethodsWiki({ onBack }: { onBack: () => void }) {
  return (
    <main className="app methods-wiki">
      <header className="hero">
        <button type="button" className="chip chip-clear" onClick={onBack}>
          ← Back to dashboard
        </button>
        <h1 style={{ marginTop: "1rem" }}>Methods wiki</h1>
        <p>
          Technical formulas and edge cases for bay share, Coles bay inference, overlap, and prices.
          The dashboard home stays short; this page is for savvy users.
        </p>
      </header>

      <nav className="methods-toc panel panel--cream" aria-label="On this page">
        <strong>On this page</strong>
        <ol>
          <li>
            <a href="#bay-share">Bay share</a>
          </li>
          <li>
            <a href="#bay-key">What is a bay_key?</a>
          </li>
          <li>
            <a href="#coles-inference">Coles bay inference (detailed)</a>
          </li>
          <li>
            <a href="#ww-bays">Woolworths bays</a>
          </li>
          <li>
            <a href="#overlap">Assortment overlap</a>
          </li>
          <li>
            <a href="#promo">Promo &amp; price</a>
          </li>
        </ol>
      </nav>

      <section className="panel" id="bay-share">
        <h2>1. Bay share</h2>
        <p className="support">
          Of all shelf bays we can identify in the store, what fraction have at least one product in
          this category?
        </p>
        <p>
          Only <strong>placed</strong> SKUs count (<code>location_class = aisle</code> and{" "}
          <code>bay_key</code> set).
        </p>
        <pre className="formula">{`bay_count(c)     = |{ bay_key | SKU in category c }|
store_bay_count  = |{ bay_key | any placed SKU in store }|
pct_store_bays(c)= bay_count(c) / store_bay_count`}</pre>
        <p>
          <strong>Not:</strong> floor area in m²; not raw map coordinates across banners.
        </p>
        <p className="source">Source: gold.category_space · lake/etl/silver_to_gold.py</p>
      </section>

      <section className="panel" id="bay-key">
        <h2>2. What is a bay_key?</h2>
        <pre className="formula">{`bay_key = "{aisle}|{bay}"   e.g. "6|2"`}</pre>
        <ul>
          <li>
            <strong>Woolworths:</strong> aisle + bay from the app (native).
          </li>
          <li>
            <strong>Coles:</strong> aisle from the app; bay is <em>inferred</em> (§3).
          </li>
        </ul>
      </section>

      <section className="panel panel--lilac" id="coles-inference">
        <h2>3. Coles bay inference (detailed)</h2>
        <p className="support">
          Coles gives aisle and indoor (x, y) but no bay number. We invent bay ids so Coles bay counts
          can be compared to Woolworths.
        </p>

        <h3>A. Eligibility</h3>
        <p>
          Need aisle text and numeric <code>indoor_x</code>, <code>indoor_y</code>. Otherwise{" "}
          <code>bay_key = null</code> (unplaced / other).
        </p>

        <h3>B. Grouping</h3>
        <p>
          Group by <code>(aisle, side)</code> — one side of one aisle. Side comes from{" "}
          <code>aisle_side</code> when present.
        </p>

        <h3>C. Dominant axis</h3>
        <pre className="formula">{`axis = x  if range(x) ≥ range(y)
     = y  otherwise`}</pre>
        <p>Sort products along that axis (walk down the aisle).</p>

        <h3>D. Gaps and threshold</h3>
        <p>Consecutive gaps along the sorted positions:</p>
        <pre className="formula">{`d_i = position[i+1] − position[i]`}</pre>
        <p>
          Take positive gaps, sort them ascending. <strong>median_gap</strong> = median of the{" "}
          <em>smaller half</em> of those gaps (so one long jump between clusters does not inflate
          “typical” spacing).
        </p>
        <pre className="formula">{`τ = 4 × median_gap     if median_gap > 0
  = ∞                  otherwise (whole group = one bay)`}</pre>

        <h3>E. Assign bay numbers</h3>
        <p>
          Start at bay 1. Walk in axis order; when the gap to the previous product is greater than{" "}
          <code>τ</code>, increment bay. Write <code>bay_key = &quot;{"{aisle}|{bay}"}&quot;</code>.
        </p>

        <h3>Intuition</h3>
        <p>
          Products close together on the map share a bay. A gap much larger than usual within-bay
          spacing (4× the typical small gap) starts the next bay.
        </p>

        <h3>Important limitations</h3>
        <ul>
          <li>Heuristic — not a planogram bay from Coles.</li>
          <li>
            Woolworths “bay pitch” is calibrated for QA logs only; it is <strong>not</strong> used
            in the Coles threshold today.
          </li>
          <li>Compare bay <em>counts / share</em>, not metres between maps.</li>
        </ul>
        <p className="source">Source: lake/etl/bay_inference.py → infer_coles_bays</p>
      </section>

      <section className="panel" id="ww-bays">
        <h2>4. Woolworths bays</h2>
        <p>
          If aisle and bay exist: <code>bay_key = &quot;{"{aisle}|{bay}"}&quot;</code>, location =
          aisle. No inference.
        </p>
        <p className="source">Source: lake/etl/bay_inference.py → attach_ww_bay_keys</p>
      </section>

      <section className="panel" id="overlap">
        <h2>5. Assortment overlap</h2>
        <ul>
          <li>
            <strong>Matched:</strong> same normalised brand; name token Jaccard ≥ 0.72.
          </li>
          <li>
            <strong>Exclusive:</strong> Personal Care SKUs not in those pairs (understates true
            overlap).
          </li>
        </ul>
        <p className="source">Source: lake/etl/sku_matcher.py</p>
      </section>

      <section className="panel" id="promo">
        <h2>6. Promo &amp; price</h2>
        <p>
          Snapshot fields <code>price_now</code>, <code>price_was</code>, <code>is_promo</code>. The
          was→now chart is median promo prices on one scrape — not a multi-day trend unless history
          is joined later.
        </p>
      </section>

      <p className="source" style={{ marginTop: "1.5rem" }}>
        Full markdown copy also lives at <code>apps/ashfield-pc/METHODS.md</code> in the repo.
      </p>
      <button type="button" className="chip chip-clear" onClick={onBack}>
        ← Back to dashboard
      </button>
    </main>
  );
}
