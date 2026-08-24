import { useEffect, useMemo, useState } from "react";
import { BannerLegend } from "./components/BannerMark";
import { BoardTabs } from "./components/BoardTabs";
import { CategoryDrill } from "./components/CategoryDrill";
import { DominanceBoard } from "./components/DominanceBoard";
import { GrainToggle } from "./components/GrainToggle";
import { KnownValueBoard } from "./components/KnownValueBoard";
import { LocationPicker } from "./components/LocationPicker";
import { MethodsWiki } from "./components/MethodsWiki";
import { NavPill } from "./components/NavPill";
import { PriceCompetitionBoard } from "./components/PriceCompetitionBoard";
import { StoreScoreboard } from "./components/StoreScoreboard";
import type { BoardTab, Grain, LocationMeta, StoreCiData } from "./lib/types";
import "./styles/app.css";

type View = "scoreboard" | "methods";

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
  const [view, setView] = useState<View>("scoreboard");
  const [board, setBoard] = useState<BoardTab>("departments");
  const [methodsSection, setMethodsSection] = useState<string | undefined>();
  const [deptId, setDeptId] = useState<string | null>(null);
  const [locationId, setLocationId] = useState<string>("ashfield");
  const [grain, setGrain] = useState<Grain>("category");

  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}data/store_ci.json`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`Failed to load data (${res.status})`);
        return res.json() as Promise<StoreCiData>;
      })
      .then((payload) => {
        setData(payload);
        setLocationId(payload.meta.location_id || payload.location?.id || "ashfield");
        setGrain(payload.meta.default_grain === "subcategory" ? "subcategory" : "category");
      })
      .catch((err: Error) => setError(err.message));
  }, []);

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

  function openMethods(sectionId?: string) {
    setMethodsSection(sectionId);
    setView("methods");
  }

  if (error) {
    return (
      <div className="error">
        <p>{error}</p>
        <p>
          Run: <code>.venv/bin/python scripts/export_store_ci_data.py</code>
        </p>
      </div>
    );
  }

  if (!data || !viewData) {
    return <div className="loading">Loading store intelligence…</div>;
  }

  return (
    <>
      <NavPill
        locationName={locationName}
        stores={data.meta.stores}
        view={view === "methods" ? "methods" : "scoreboard"}
        categoryLabel={dept?.shared_label}
        onView={(v) => {
          setView(v);
          if (v === "scoreboard") setMethodsSection(undefined);
        }}
      />
      {view === "methods" ? (
        <MethodsWiki
          onBack={() => {
            setView("scoreboard");
            setMethodsSection(undefined);
          }}
        />
      ) : (
        <main className="app">
          {dept ? (
            <CategoryDrill
              data={viewData}
              dept={dept}
              grain={grain}
              locationName={locationName}
              onBack={() => setDeptId(null)}
              onOpenMethods={openMethods}
            />
          ) : (
            <>
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
              <BannerLegend />
              <BoardTabs
                tab={board}
                onTab={(t) => {
                  setBoard(t);
                  setDeptId(null);
                }}
              />
              {board === "departments" ? (
                <StoreScoreboard
                  data={viewData}
                  grain={grain}
                  locationName={locationName}
                  onSelect={setDeptId}
                  onOpenMethods={openMethods}
                />
              ) : null}
              {board === "dominance" ? (
                <DominanceBoard
                  data={viewData}
                  grain={grain}
                  locationName={locationName}
                  onSelect={setDeptId}
                  onOpenMethods={openMethods}
                />
              ) : null}
              {board === "price" ? (
                <PriceCompetitionBoard
                  data={viewData}
                  grain={grain}
                  locationName={locationName}
                  onSelect={setDeptId}
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
            </>
          )}
        </main>
      )}
    </>
  );
}
