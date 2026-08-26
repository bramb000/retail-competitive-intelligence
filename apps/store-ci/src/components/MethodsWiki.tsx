/** Plain-language guide to how the boards are built. */

export function MethodsWiki({ onBack }: { onBack: () => void }) {
  return (
    <div className="methods-wiki">
      <header className="hero">
        <button type="button" className="chip" onClick={onBack}>
          ← Back to boards
        </button>
        <h1 style={{ marginTop: "1rem" }}>How we calculate this</h1>
        <p>
          Short explanations of what each number means. Written for store and category managers —
          not software engineers. If a word is unclear, that is our fault, not yours.
        </p>
      </header>

      <nav className="methods-toc panel panel--cream" aria-label="On this page">
        <strong>On this page</strong>
        <ol>
          <li>
            <a href="#/methods/grain">What you are looking at</a>
          </li>
          <li>
            <a href="#/methods/subcategory-mapping">How Coles subcategories are assigned</a>
          </li>
          <li>
            <a href="#/methods/glossary">Why names differ between Coles and Woolworths</a>
          </li>
          <li>
            <a href="#/methods/dominance">Who is “winning” a category</a>
          </li>
          <li>
            <a href="#/methods/price-race">How we compare prices by category</a>
          </li>
          <li>
            <a href="#/methods/known-value">Everyday staples shoppers use to judge price</a>
          </li>
          <li>
            <a href="#/methods/bay-share">How much shelf space a category gets</a>
          </li>
          <li>
            <a href="#/methods/macrospace">Macrospace (category adjacency)</a>
          </li>
          <li>
            <a href="#/methods/bay-key">How we count shelf bays at each retailer</a>
          </li>
          <li>
            <a href="#/methods/overlap">When we say the same product is in both stores</a>
          </li>
          <li>
            <a href="#/methods/promo">Specials and typical prices</a>
          </li>
          <li>
            <a href="#/methods/refresh">Keeping the numbers up to date</a>
          </li>
        </ol>
      </nav>

      <section className="panel panel--lilac" id="grain">
        <h2>What you are looking at</h2>
        <p className="support">
          Retail CI compares Coles and Woolworths <strong>one suburb at a time</strong>. Use the{" "}
          <strong>View by</strong> control in the top bar to switch between two levels of detail:
        </p>
        <ul>
          <li>
            <strong>Category</strong> — major aisle families (Dairy, Pantry, Personal Care, and so
            on). This is the default view and has the most complete Coles coverage.
          </li>
          <li>
            <strong>Subcategory</strong> — finer shelf groups using Woolworths’ labels (Snacks,
            Milk, Shampoo, and so on). Woolworths reports these natively; Coles products are mapped
            onto the same labels where we can.
          </li>
        </ul>
        <p>
          Both views come from the same collection run — switching does not re-collect. Category rows
          show bilingual names (what each retailer calls that part of the store). Subcategory rows
          show the Woolworths label plus the parent aisle family.
        </p>
        <ul>
          <li>
            Right now the live suburb is <strong>Ashfield</strong>. More locations can be added later
            using the same layout.
          </li>
          <li>
            We always show the full taxonomy list, even if we have not finished collecting products
            for one of them yet. Those rows say <em>data still filling in</em> until coverage arrives.
          </li>
          <li>
            Personal care is just one aisle family on the list — the same as bakery or drinks. There
            is no separate “personal care only” product.
          </li>
        </ul>
      </section>

      <section className="panel" id="subcategory-mapping">
        <h2>How Coles subcategories are assigned</h2>
        <p className="support">
          Coles does not publish subcategory labels on Ashfield SKUs the way Woolworths does. We map
          Coles onto Woolworths-style subcategories in three conservative steps:
        </p>
        <ol>
          <li>
            <strong>Direct match</strong> — if a Coles product fuzzy-matches a Woolworths product
            (see below), the Coles SKU inherits that Woolworths parent aisle and subcategory.
          </li>
          <li>
            <strong>Infer from matched neighbours</strong> — for unmatched Coles SKUs in the same
            catalogue department and parent aisle, look at already-matched Coles products with
            similar brand and name. Only assign when the signal is strong and not ambiguous.
          </li>
          <li>
            <strong>Infer from Woolworths examples</strong> — if step 2 is too sparse, look at
            Woolworths products in the same parent aisle with a similar brand and name. This uses a
            stricter bar than step 2.
          </li>
          <li>
            <strong>Leave blank</strong> — if none of the above is confident enough, the Coles SKU
            stays on the category view only. We never stamp an entire Coles department as one
            subcategory (for example, all Pantry ≠ all Baking).
          </li>
        </ol>
        <p>
          Coverage is uneven: Woolworths subcategories are native for most SKUs; Coles subcategory
          assignment is partial (roughly one third of Coles SKUs today). Subcategory boards
          therefore under-count Coles until more products are matched or inferred.
        </p>
      </section>

      <section className="panel" id="glossary">
        <h2>Why names differ between Coles and Woolworths</h2>
        <p className="support">
          The two retailers often use different words for the same part of the store. Coles might
          say “Health &amp; Beauty” while Woolworths says “Personal Care.”
        </p>
        <p>
          On the Overview board we show three names side by side:
        </p>
        <ul>
          <li>
            <strong>Shared</strong> — a plain name we use so both sides can be compared.
          </li>
          <li>
            <strong>Coles says</strong> — the name Coles uses in that store.
          </li>
          <li>
            <strong>Woolworths says</strong> — the name Woolworths uses in that store.
          </li>
        </ul>
        <p>
          That way a Coles manager and a Woolworths manager can both recognise the aisle, without
          forcing one retailer’s wording onto the other.
        </p>
      </section>

      <section className="panel panel--lilac" id="dominance">
        <h2>Who is “winning” a category</h2>
        <p className="support">
          Dominance answers a simple question:{" "}
          <strong>in this suburb, which banner looks stronger in this aisle?</strong>
        </p>
        <p>We decide in this order:</p>
        <ol>
          <li>
            If only one retailer has products listed in that aisle, they win by default (the other
            side is missing or still being collected).
          </li>
          <li>
            Otherwise we look at <strong>shelf space</strong> first — which store gives more of its
            known shelf bays to this aisle. If the gap is about 1.5 percentage points or more, that
            retailer is marked dominant on space.
          </li>
          <li>
            If space is too close to call, we look at <strong>how big the range is</strong> as a
            share of that retailer’s whole store. A gap of about 2 percentage points or more decides
            it.
          </li>
          <li>
            If both are still close, we call it <strong>contested</strong> — neither side clearly
            owns the aisle.
          </li>
        </ol>
      </section>

      <section className="panel" id="price-race">
        <h2>How we compare prices by category</h2>
        <p className="support">
          For each aisle we take the <strong>middle price</strong> (the median) of products on the
          shelf at Coles, and the middle price at Woolworths, then compare those two numbers.
        </p>
        <ul>
          <li>
            <strong>Aligned</strong> — the two middle prices are within about 5% of each other.
          </li>
          <li>
            <strong>Competing</strong> — the gap is roughly 5% to 15%.
          </li>
          <li>
            <strong>Hot gap</strong> — the gap is bigger than about 15%.
          </li>
        </ul>
        <p>
          Important caveat: we are not matching every pack size one-for-one here. If one store’s
          range is still thin, or the mix of products is very different, the gap can look larger
          than shoppers would feel in the aisle. Treat big gaps as a signal to investigate, not as
          a final verdict.
        </p>
      </section>

      <section className="panel panel--cream" id="known-value">
        <h2>Everyday staples shoppers use to judge price</h2>
        <p className="support">
          Shoppers often decide whether a store “feels expensive” from a small set of everyday
          items — milk, eggs, bread, mince, toilet paper, and similar staples.
        </p>
        <p>
          For each staple we pick one representative product at Coles and one at Woolworths, aiming
          for a <strong>similar pack size</strong> (for example both near 2 litres of milk, or both
          near 1 litre of laundry liquid).
        </p>
        <p>
          We only name a cheaper store when the comparison is fair:
        </p>
        <ul>
          <li>
            Prefer <strong>unit price</strong> when both sides publish the same kind of rate (such
            as dollars per litre) — that stays fair even if bottle sizes differ a little.
          </li>
          <li>
            Otherwise compare shelf prices only if pack sizes are within about 30% of each other,
            and we normalise by size (so 1.8L vs 2L is not treated as a raw dollar duel).
          </li>
          <li>
            If packs are clearly different and there is no matching unit price, we mark the line{" "}
            <strong>not comparable</strong> and do <em>not</em> declare a winner.
          </li>
        </ul>
      </section>

      <section className="panel" id="bay-share">
        <h2>How much of the store’s shelf space this aisle gets</h2>
        <p className="support">
          Bay share answers: <strong>of all shelf bays we can identify in this store, what share
          belongs to this aisle?</strong>
        </p>
        <p>
          Example: if the store has 200 identified bays and dairy’s products add up to 12.4 bay
          equivalents, dairy’s bay share is 12.4 of 200 (about 6%).
        </p>
        <p>
          When a bay has more than one category on it, we split that bay{" "}
          <strong>fractionally</strong> by how many products of each category sit there. A bay that
          is 70% dairy and 30% drinks contributes 0.7 to dairy and 0.3 to drinks — not a hard
          winner-take-all assignment.
        </p>
        <p>
          Pure single-category bays (common on Woolworths, and on Coles once map pins are counted
          correctly) still contribute a full 1.0 to that category.
        </p>
        <p>
          This is a count of shelf sections, <strong>not</strong> floor area in square metres, and
          not a raw map overlay between Coles and Woolworths.
        </p>
        <p>
          Assortment counts use the <strong>same rule</strong> for both banners: only products that
          sit on an identified shelf bay. Department areas without bay numbers (for example Woolworths{" "}
          <em>Produce Department</em> or <em>Deli Department</em>, and Coles fixtures that never got
          map pins) are left out so range and bay share stay comparable.
        </p>
        <p>
          If either banner has most of a category on <strong>department fixtures without bay
          numbers</strong> (for example Woolworths <em>Produce Department</em>), we hide that whole
          aisle for <strong>both</strong> Coles and Woolworths — category and subcategories. Products
          that are merely missing map pins (<em>unplaced</em>) do not trigger this; that is a data
          gap, not a fixture.
        </p>
      </section>

      <section className="panel panel--cream" id="macrospace">
        <h2>Macrospace — which aisles sit next to each other</h2>
        <p className="support">
          The <strong>Macrospace</strong> tab draws each store as a coloured shelf plan — one block
          per bay, using in-store map coordinates.
        </p>
        <ul>
          <li>
            Colour follows the <strong>Category / Subcategory</strong> toggle. Each bay is shaded by
            its dominant group; tap a bay to see aisle, side, and mix.
          </li>
          <li>
            The bay panel opens on <strong>Overview</strong> (taxonomy cards and products). When
            placement and labels diverge — for example specialty crackers ranged in deli but named
            Biscuits &amp; Crackers in the comparison taxonomy — an <strong>Insights</strong> tab
            appears with that second-layer explanation.
          </li>
          <li>
            <strong>Drag</strong> to pan, <strong>pinch</strong> or scroll to zoom,{" "}
            <strong>double-tap</strong> to reset the view. Use <strong>Both stores</strong> or pick
            one banner for a larger iPad-friendly canvas.
          </li>
          <li>
            Legend chips highlight every bay belonging to that category across the plan.
          </li>
        </ul>
        <p>
          Coles and Woolworths use <strong>different indoor map systems</strong>. Do not overlay the
          two plots or compare absolute coordinates across banners. Adjacency is only meaningful
          inside one store.
        </p>
      </section>

      <section className="panel panel--lilac" id="bay-key">
        <h2>How we count shelf bays at each retailer</h2>
        <p className="support">
          A <strong>bay</strong> is one section of shelf along an aisle — the chunk of fixtures
          shoppers walk past — not a single product facing. We label each one as “aisle number + bay
          number” (for example, aisle 6, bay 2).
        </p>

        <h3>Woolworths</h3>
        <p>
          Woolworths usually tells us both pieces directly in their store data: which aisle the
          product sits in, and which bay number along that aisle.
        </p>
        <ul>
          <li>
            If we have an aisle and a bay number, we count that as one shelf section for Woolworths.
          </li>
          <li>
            If either piece is missing, that product does not add a bay to the Woolworths space
            figures.
          </li>
          <li>
            We do <strong>not</strong> invent Woolworths bay numbers. What their app reports is what
            we use.
          </li>
        </ul>

        <h3>Coles</h3>
        <p>
          Coles usually tells us the aisle and a pin on the in-store map, but{" "}
          <strong>not</strong> a bay number. Their map already places many products onto a small set
          of pins along each aisle side (often about 6–14 pins per side). We treat each distinct pin
          as one bay.
        </p>
        <p>In plain steps:</p>
        <ol>
          <li>
            Keep only Coles products that have both an aisle and map coordinates. Products without
            those cannot be placed on a bay for space counting.
          </li>
          <li>
            Group products on the same aisle (and the same side of the aisle, when Coles provides a
            side).
          </li>
          <li>
            Merge pins that land on essentially the same spot (tiny map jitter), then number the
            remaining pins in order along the aisle.
          </li>
          <li>
            Every product on a pin shares that bay label. Those labels are what we count for Coles
            bay share.
          </li>
        </ol>
        <p>
          Getting this pin→bay step right matters far more than how we later split mixed bays.
          Earlier gap-merging glued whole aisles into one or two bays and made mixes look worse
          than the map really showed.
        </p>

        <h3>What we never do</h3>
        <ul>
          <li>
            We never mix Coles and Woolworths map coordinates into one drawing. The maps are
            different scales and layouts.
          </li>
          <li>
            We never treat “more products on a bay” as “more floor area.” Bay share is about how
            much of the store’s bay inventory an aisle family accounts for, including fractional
            slices of shared bays.
          </li>
        </ul>
      </section>

      <section className="panel panel--lilac" id="overlap">
        <h2>When we say the same product is in both stores</h2>
        <p className="support">
          We do not have barcodes in the collected product data, so “matched pairs” are found by{" "}
          <strong>same brand + similar product name</strong> — not by scanning identical GTINs.
        </p>
        <p>In plain steps:</p>
        <ol>
          <li>
            Strip pack-size tokens from names (litres, grams, “6 pack”, and similar) so “Milk 2L”
            and “Milk 1L” can still compare.
          </li>
          <li>
            Compare the remaining name tokens using a similarity score (Jaccard overlap). Pairs need
            a score of at least <strong>0.72</strong> to count as a match.
          </li>
          <li>
            Only match within the same brand. If the brand field is missing, we infer it from the
            start of the product name using known Coles brand names.
          </li>
          <li>
            Assign matches greedily best-first so each Coles SKU maps to at most one Woolworths SKU
            and vice versa.
          </li>
        </ol>
        <p>
          These matches serve two jobs: they are evidence of range overlap in drill-ins, and they
          are the first step when mapping Coles onto Woolworths subcategories.
        </p>
        <p>
          Matching is imperfect. Pack sizes can differ, naming conventions differ, and some true
          matches are missed. The match count is usually a <strong>lower bound</strong> — real
          overlap in the aisle is often higher. Use matches as examples and evidence, not as a
          complete catalogue of everything shared.
        </p>
      </section>

      <section className="panel" id="promo">
        <h2>Specials and typical prices</h2>
        <p className="support">
          <strong>On promo</strong> is the share of products in that aisle that are currently on
          special in our latest snapshot.
        </p>
        <p>
          <strong>Median price</strong> is the middle shelf price in that aisle for that retailer —
          half the products cost less, half cost more. It is a quick sense of the price level of
          the range, not the price of any single hero item.
        </p>
        <p>
          Prices and specials are from one collection pass. They are not a week-long trend until we
          collect again.
        </p>
      </section>

      <section className="panel panel--cream" id="refresh">
        <h2>Keeping the numbers up to date</h2>
        <p className="support">
          Product lists for Ashfield are still being collected in the background. When a new batch
          is ready, the comparison is refreshed so boards pick up the latest figures.
        </p>
        <p>
          Until then, some Woolworths (or Coles) cells may look thin or empty. That usually means
          data is still arriving — not that the aisle is truly empty in the real store.
        </p>
      </section>
    </div>
  );
}
