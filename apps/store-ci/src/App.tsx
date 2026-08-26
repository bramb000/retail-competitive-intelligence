import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "./components/AppShell";
import { BannerLegend } from "./components/BannerMark";
import { CategoryDrill } from "./components/CategoryDrill";
import { DominanceBoard } from "./components/DominanceBoard";
import { GrainToggle } from "./components/GrainToggle";
import { KnownValueBoard } from "./components/KnownValueBoard";
import { LocationPicker } from "./components/LocationPicker";
import { MethodsWiki } from "./components/MethodsWiki";
import { PriceCompetitionBoard } from "./components/PriceCompetitionBoard";
import { StoreMacrospaceMap } from "./components/StoreMacrospaceMap";
import { StoreScoreboard } from "./components/StoreScoreboard";
import {
  parseHash,
  replaceHash,
  type AppView,
} from "./lib/hashRoute";
import type { BoardTab, Grain, LocationMeta, StoreCiData } from "./lib/types";
import "./styles/app.css";

const BOARD_CRUMB: Record<BoardTab, string> = {
  departments: "Overview",
  dominance: "Dominance",
  price: "Price",
  kvi: "Staples",
  macrospace: "Macrospace",
};

function applyGrain(data: StoreCiData, grain: Grain): StoreCiData {
  const slice = data.grains?.[grain];
  if (!slice) return data;
  return {
    ...data,
    departments: slice.departments,
    scoreboards: {
      dominance: slice.scoreboards.dominance,
      price_competition: slice.scoreboards.price_competition,
      known_value: data.scoreboards?.known_value ?? [],
      known_value_summary: data.scoreboards?.known_value_summary ?? {
        defined: 0,
        both_priced: 0,
        coles_cheaper: 0,
        ww_cheaper: 0,
        ties: 0,
        coles_only: 0,
        ww_only: 0,
      },
    },
  };
}

export default function App() {
  const [data, setData] = useState<StoreCiData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [skusLoading, setSkusLoading] = useState(false);
  const [view, setView] = useState<AppView>("scoreboard");
  const [board, setBoard] = useState<BoardTab>("departments");
  const [methodsSection, setMethodsSection] = useState<string | undefined>();
  const [deptId, setDeptId] = useState<string | null>(null);
  const [deptMatchBy, setDeptMatchBy] = useState<"id" | "shared_label">("id");
  const [locationId, setLocationId] = useState<string>("ashfield");
  const [grain, setGrain] = useState<Grain>("category");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [hashReady, setHashReady] = useState(false);

  useEffect(() => {
    const base = import.meta.env.BASE_URL;
    fetch(`${base}data/store_ci.json`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`Failed to load data (${res.status})`);
        return res.json() as Promise<StoreCiData>;
      })
      .then((payload) => {
        setData({ ...payload, skus: payload.skus ?? [] });
        setLocationId(payload.meta.location_id || payload.location?.id || "ashfield");
        setGrain(payload.meta.default_grain === "subcategory" ? "subcategory" : "category");

        const skusPath = payload.meta.skus_url || "data/store_ci_skus.json";
        const skusUrl = skusPath.startsWith("http")
          ? skusPath
          : `${base}${skusPath.replace(/^\//, "")}`;
        setSkusLoading(true);
        return fetch(skusUrl)
          .then(async (res) => {
            if (!res.ok) throw new Error(`Failed to load products (${res.status})`);
            return res.json() as Promise<{ skus: StoreCiData["skus"] } | StoreCiData["skus"]>;
          })
          .then((body) => {
            const skus = Array.isArray(body) ? body : body.skus ?? [];
            setData((prev) => (prev ? { ...prev, skus } : prev));
          })
          .catch((err: Error) => {
            // Boards can still render; product views show empty until reload.
            console.error(err);
          })
          .finally(() => setSkusLoading(false));
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  // Apply hash on first load (and when user uses back/forward).
  useEffect(() => {
    function applyFromHash() {
      const state = parseHash(window.location.hash);
      setView(state.view);
      setBoard(state.board);
      setDeptId(state.deptId);
      setMethodsSection(state.methodsSection);
      setHashReady(true);
    }
    applyFromHash();
    window.addEventListener("hashchange", applyFromHash);
    return () => window.removeEventListener("hashchange", applyFromHash);
  }, []);

  // Keep hash in sync with navigation state.
  useEffect(() => {
    if (!hashReady) return;
    replaceHash({ view, board, deptId, methodsSection });
  }, [view, board, deptId, methodsSection, hashReady]);

  useEffect(() => {
    if (view !== "methods" || !methodsSection) return;
    requestAnimationFrame(() => {
      document.getElementById(methodsSection)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [view, methodsSection]);

  const locations: LocationMeta[] = useMemo(() => {
    if (!data) return [];
    if (data.meta.locations?.length) return data.meta.locations;
    return [
      {
        id: data.meta.location_id || "ashfield",
        name: data.meta.location_name || data.meta.suburb,
        active: true,
        stores: data.meta.stores,
      },
    ];
  }, [data]);

  const viewData = useMemo(() => (data ? applyGrain(data, grain) : null), [data, grain]);

  const locationName =
    locations.find((l) => l.id === locationId)?.name ||
    data?.meta.location_name ||
    data?.meta.suburb ||
    "Ashfield";

  const dept = useMemo(
    () => (viewData && deptId ? viewData.departments.find((d) => d.id === deptId) ?? null : null),
    [viewData, deptId],
  );

  const navigateBoard = useCallback((next: BoardTab) => {
    setView("scoreboard");
    setBoard(next);
    setDeptId(null);
    setDeptMatchBy("id");
    setMethodsSection(undefined);
  }, []);

  const openMethods = useCallback((sectionId?: string) => {
    setMethodsSection(sectionId);
    setView("methods");
    setDeptId(null);
  }, []);

  const selectDept = useCallback((id: string) => {
    setDeptId(id);
    setDeptMatchBy("id");
    setView("scoreboard");
  }, []);

  const selectDeptFromMacrospace = useCallback((id: string) => {
    setDeptId(id);
    setDeptMatchBy("shared_label");
    setView("scoreboard");
  }, []);

  if (error) {
    return (
      <div className="error">
        <p>{error}</p>
        <p>Ask your team to refresh the store data export, then reload this page.</p>
      </div>
    );
  }

  if (!data || !viewData) {
    return <div className="loading">Loading Retail CI…</div>;
  }

  const stores = data.meta.stores;
  const crumb =
    view === "methods" ? (
      "Methods"
    ) : dept ? (
      <>
        <button
          type="button"
          onClick={() => {
            setDeptId(null);
            setDeptMatchBy("id");
          }}
        >
          {BOARD_CRUMB[board]}
        </button>
        {" / "}
        {dept.shared_label}
      </>
    ) : (
      BOARD_CRUMB[board]
    );

  const toolbar =
    view === "scoreboard" && !dept ? (
      <div className="view-toolbar">
        <LocationPicker
          locations={locations}
          activeId={locationId}
          onSelect={(id) => {
            setLocationId(id);
            setDeptId(null);
          }}
        />
        <GrainToggle
          grain={grain}
          onGrain={(next) => {
            setGrain(next);
            setDeptId(null);
          }}
        />
      </div>
    ) : null;

  return (
    <AppShell
      view={view}
      board={board}
      onBoard={navigateBoard}
      onMethods={() => openMethods()}
      mobileOpen={mobileNavOpen}
      onMobileOpen={setMobileNavOpen}
      crumb={crumb}
      meta={
        <>
          {locationName}
          {" · "}
          Coles {stores.Coles} / WW {stores.Woolworths}
        </>
      }
      toolbar={toolbar}
    >
      {skusLoading && (board === "macrospace" || dept) ? (
        <div className="loading" style={{ padding: "0.75rem 0" }}>
          Loading product catalogue…
        </div>
      ) : null}
      {view === "methods" ? (
        <MethodsWiki onBack={() => navigateBoard(board)} />
      ) : dept ? (
        <CategoryDrill
          data={viewData}
          dept={dept}
          grain={grain}
          locationName={locationName}
          onBack={() => {
            setDeptId(null);
            setDeptMatchBy("id");
          }}
          onOpenMethods={openMethods}
          backLabel={board === "macrospace" ? "Macrospace" : undefined}
          matchBy={deptMatchBy}
        />
      ) : (
        <>
          <BannerLegend />
          {board === "departments" ? (
            <StoreScoreboard
              data={viewData}
              grain={grain}
              locationName={locationName}
              onSelect={selectDept}
              onOpenMethods={openMethods}
            />
          ) : null}
          {board === "dominance" ? (
            <DominanceBoard
              data={viewData}
              grain={grain}
              locationName={locationName}
              onSelect={selectDept}
              onOpenMethods={openMethods}
            />
          ) : null}
          {board === "price" ? (
            <PriceCompetitionBoard
              data={viewData}
              grain={grain}
              locationName={locationName}
              onSelect={selectDept}
              onOpenMethods={openMethods}
            />
          ) : null}
          {board === "kvi" ? (
            <KnownValueBoard
              data={data}
              locationName={locationName}
              onOpenMethods={openMethods}
            />
          ) : null}
          {board === "macrospace" ? (
            <StoreMacrospaceMap
              data={viewData}
              grain={grain}
              locationName={locationName}
              onOpenMethods={openMethods}
              onViewAllProducts={selectDeptFromMacrospace}
            />
          ) : null}
        </>
      )}
    </AppShell>
  );
}
