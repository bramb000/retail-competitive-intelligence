import { useEffect, useState } from "react";
import { AssortmentVenn } from "./components/AssortmentVenn";
import { FilterBar } from "./components/FilterBar";
import { InsightStrip } from "./components/InsightStrip";
import { MethodsWiki } from "./components/MethodsWiki";
import { NavPill } from "./components/NavPill";
import { PricePromoPanel } from "./components/PricePromoPanel";
import { SkuExplorer } from "./components/SkuExplorer";
import { SpacePanel } from "./components/SpacePanel";
import {
  emptyFilters,
  type DashboardFilters,
  type PcDashboardData,
  type VennSide,
} from "./lib/types";
import "./styles/app.css";

export default function App() {
  const [data, setData] = useState<PcDashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<DashboardFilters>(emptyFilters);
  const [view, setView] = useState<"dashboard" | "methods">("dashboard");
  const [methodsSection, setMethodsSection] = useState<string | undefined>();

  useEffect(() => {
    fetch("/data/personal_care.json")
      .then(async (res) => {
        if (!res.ok) throw new Error(`Failed to load data (${res.status})`);
        return res.json() as Promise<PcDashboardData>;
      })
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (view !== "methods" || !methodsSection) return;
    requestAnimationFrame(() => {
      document.getElementById(methodsSection)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [view, methodsSection]);

  function openMethods(sectionId?: string) {
    setMethodsSection(sectionId);
    setView("methods");
  }

  if (error) {
    return (
      <div className="error">
        <p>{error}</p>
        <p>
          Run: <code>.venv/bin/python scripts/export_pc_dashboard_data.py</code>
        </p>
      </div>
    );
  }

  if (!data) {
    return <div className="loading">Loading Personal Care dashboard…</div>;
  }

  function setSide(side: VennSide | null) {
    setFilters((f) => ({ ...f, side, matchPairId: null }));
    scrollToTable();
  }

  function selectPair(colesId: number, wwId: number) {
    setFilters((f) => ({
      ...f,
      side: "matched",
      matchPairId: `${colesId}:${wwId}`,
    }));
    scrollToTable();
  }

  function setBay(bayKey: string | null) {
    setFilters((f) => ({ ...f, bayKey }));
    scrollToTable();
  }

  function setRetailer(retailer: "Coles" | "Woolworths" | null) {
    setFilters((f) => ({ ...f, retailer }));
    scrollToTable();
  }

  function setSubcategory(subcategory: string | null) {
    setFilters((f) => ({ ...f, subcategory }));
    scrollToTable();
  }

  return (
    <>
      <NavPill
        category={data.meta.category}
        suburb={data.meta.suburb}
        stores={data.meta.stores}
        view={view}
        onView={(v) => {
          setView(v);
          if (v === "dashboard") setMethodsSection(undefined);
        }}
      />
      {view === "methods" ? (
        <MethodsWiki onBack={() => setView("dashboard")} />
      ) : (
        <main className="app">
          <header className="hero">
            <h1>Personal Care at Ashfield</h1>
            <p>
              Compare Coles and Woolworths on assortment overlap, bay share, and promo pressure —
              then drill into SKUs. Built for a two-minute category read.
            </p>
            <p className="hero-methods-link">
              <button type="button" className="text-link" onClick={() => openMethods()}>
                Methods wiki — formulas &amp; Coles bay inference
              </button>
            </p>
          </header>

          <aside className="caveats" aria-label="Data caveats">
            <strong>Read this first</strong>
            {data.meta.caveats.map((c) => (
              <p key={c}>{c}</p>
            ))}
          </aside>

          <InsightStrip kpis={data.kpis} onOpenMethods={openMethods} />
          <FilterBar filters={filters} onChange={setFilters} />

          <AssortmentVenn
            venn={data.venn}
            matches={data.matches}
            filters={filters}
            onSetSide={setSide}
            onSelectPair={selectPair}
            onOpenMethods={openMethods}
          />

          <SpacePanel
            space={data.space}
            skus={data.skus}
            filters={filters}
            onBay={setBay}
            onRetailer={setRetailer}
            onOpenMethods={openMethods}
          />

          <PricePromoPanel data={data} filters={filters} onSubcategory={setSubcategory} />

          <SkuExplorer
            skus={data.skus}
            filters={filters}
            onSearch={(search) => setFilters((f) => ({ ...f, search }))}
          />
        </main>
      )}
    </>
  );
}

function scrollToTable() {
  requestAnimationFrame(() => {
    document.getElementById("sku-explorer")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}
