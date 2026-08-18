"""42-store curated Coles pilot scrape (22 NSW + 20 VIC).

A deliberately-scoped-down precursor to a possible future 811-store
nationwide run — see the plan doc for why this scope, why Wayfinding
enrichment is enabled for every store, and the concurrency defaults chosen
(anti-bot sensitivity was observed live in an earlier session at a much
larger request volume than this pilot reaches).

Coles only: store targets come from demo_stores.csv via
hybrid_scraper.demo_stores.load_demo_store_locations, bypassing
resolve_store_id's suburb-search flow entirely (store IDs/coordinates are
already confirmed). Woolworths is out of scope for this pilot — no
fixed-store-list equivalent exists for it yet, and it's separately already
blocked by an unrelated Akamai issue, so including it would only add
suburb-resolution traffic this design avoids.

Run:
    python demo_scrape.py                  # all 42 stores, with Wayfinding enrichment
    python demo_scrape.py --limit 3         # smoke test a handful first
    python demo_scrape.py --quiet           # progress bar only, no per-store log lines on console

Exit codes match daily_scrape.py's convention: 0 if at least one store
succeeded, 1 if every store failed or the script crashed outright. Unlike
daily_scrape.py, results are recorded to DuckDB store-by-store as each one
finishes (via ScraperOrchestrator's on_store_done callback), so a Ctrl+C
part-way through preserves every store that already completed.

Progress/logging: a live Rich progress display shows an overall bar
(store count, elapsed, ETA) plus one line per store currently in flight
(exactly MAX_CONCURRENT_STORES at a time, matching the real concurrency
cap below) — that per-store line disappears once the store finishes, and
its outcome is printed as a normal log line above the bar instead. Full
DEBUG-level detail always goes to scraper.log regardless of console
verbosity, same as every other entry point in this project.

Known gotcha: scraper_data.duckdb can only be opened for writing while no
other connection (e.g. `streamlit run dashboard.py`) has it open.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import date
from typing import Any, Dict, List, Optional

from curl_cffi.requests import AsyncSession
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from hybrid_scraper.aisle_enrichment import fetch_coles_instore_locations
from hybrid_scraper.bootstrapper import PlaywrightBootstrapper
from hybrid_scraper.demo_stores import load_demo_store_locations
from hybrid_scraper.engine import CurlCffiEngine
from hybrid_scraper.logging_config import configure_logging
from hybrid_scraper.models import StoreLocation
from hybrid_scraper.orchestrator import ScraperOrchestrator
from hybrid_scraper.storage import ProductStore, store_id_for

logger = logging.getLogger("hybrid_scraper.demo_scrape")

# Conservative defaults chosen live: each store's bootstrap is a full
# Playwright anti-bot challenge-solve hitting Coles' edge directly, and this
# session already observed the website's Incapsula/Imperva layer react
# after ~2,400-4,000 requests. 2 concurrent stores, staggered 5s apart,
# keeps this pilot's footprint well under that threshold.
MAX_CONCURRENT_STORES = 2
LAUNCH_STAGGER_SECONDS = 5.0


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None, help="Only scrape the first N demo stores (smoke test before the full 42)"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress per-store console log lines; keep only the progress display"
    )
    return parser.parse_args(argv)


class _PilotProgress:
    """Owns the live Rich display: one overall bar plus one line per in-flight store.

    Store lines are added on start and removed on completion (Rich's
    standard "worker pool" pattern) — with MAX_CONCURRENT_STORES=2 that
    means at most 2 store lines are ever visible alongside the overall bar,
    which is what's actually running concurrently.
    """

    def __init__(self, console: Console, total_stores: int) -> None:
        self.console = console
        self.ok_count = 0
        self.fail_count = 0
        self._start_times: Dict[str, float] = {}
        self._store_tasks: Dict[str, int] = {}
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn("ETA"),
            TimeRemainingColumn(),
            console=console,
        )
        self._overall_task = self._progress.add_task("Pilot scrape", total=total_stores)

    def __enter__(self) -> "_PilotProgress":
        self._progress.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._progress.stop()

    def _refresh_overall_description(self) -> None:
        self._progress.update(
            self._overall_task,
            description=f"Pilot scrape — {self.ok_count} ok, {self.fail_count} failed",
        )

    def store_started(self, key: str, store_location: StoreLocation) -> None:
        self._start_times[key] = time.monotonic()
        self._store_tasks[key] = self._progress.add_task(
            f"  {key} {store_location.store_name} ({store_location.suburb_name})", total=None
        )

    def store_finished(self, key: str) -> float:
        """Removes that store's line from the display and returns its duration in seconds."""
        duration = time.monotonic() - self._start_times.pop(key, time.monotonic())
        task_id = self._store_tasks.pop(key, None)
        if task_id is not None:
            self._progress.remove_task(task_id)
        return duration

    def record_outcome(self, ok: bool) -> None:
        if ok:
            self.ok_count += 1
        else:
            self.fail_count += 1
        self._refresh_overall_description()
        self._progress.advance(self._overall_task)


async def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    console = Console()
    configure_logging(
        console_level=logging.WARNING if args.quiet else logging.INFO,
        rich_console=console,
    )

    targets = load_demo_store_locations()
    if args.limit:
        targets = targets[: args.limit]
    console.print(
        f"[bold]Pilot scrape starting:[/bold] {len(targets)} Coles store(s), "
        f"max {MAX_CONCURRENT_STORES} concurrent, {LAUNCH_STAGGER_SECONDS:.0f}s launch stagger"
    )
    logger.info("Pilot scrape starting: %d Coles store(s)", len(targets))

    summary_rows: List[Dict[str, Any]] = []

    async with PlaywrightBootstrapper() as bootstrapper, CurlCffiEngine(concurrency=8) as engine:
        orchestrator = ScraperOrchestrator(bootstrapper, engine, max_retries=3)
        scrape_date = date.today().isoformat()

        with ProductStore() as product_store, _PilotProgress(console, len(targets)) as pilot_progress:
            async with AsyncSession() as app_session:

                async def _on_store_start(store_location: StoreLocation) -> None:
                    key = store_id_for(store_location.retailer, store_location.store_id)
                    pilot_progress.store_started(key, store_location)
                    logger.info(
                        "%s: starting (store=%r suburb=%s)",
                        key,
                        store_location.store_name,
                        store_location.suburb_name,
                    )

                async def _on_store_done(store_location: StoreLocation, outcome: Any) -> None:
                    key = store_id_for(store_location.retailer, store_location.store_id)
                    duration = pilot_progress.store_finished(key)

                    if isinstance(outcome, Exception):
                        logger.error("%s: FAILED after %.1fs — %s", key, duration, outcome)
                        pilot_progress.record_outcome(ok=False)
                        summary_rows.append(
                            {
                                "key": key,
                                "status": "FAILED",
                                "skus": 0,
                                "new": 0,
                                "changed": 0,
                                "unchanged": 0,
                                "aisle": "-",
                                "duration": duration,
                            }
                        )
                        return

                    stats = product_store.record_scrape(store_location, outcome, scrape_date)
                    logger.info(
                        "%s: scraped %d SKUs in %.1fs (new=%d changed=%d unchanged=%d)",
                        key,
                        len(outcome),
                        duration,
                        stats.new,
                        stats.changed,
                        stats.unchanged,
                    )

                    # Wayfinding enrichment runs for every store in this
                    # pilot (not capped/opt-in) — already-proven safe at
                    # this scale: a separate ~811-store live audit this
                    # session completed with zero token refreshes needed.
                    skus = [str(p.retailer_product_id) for p in outcome]
                    aisle_summary = "-"
                    try:
                        aisle_by_sku = await fetch_coles_instore_locations(app_session, store_location.store_id, skus)
                        updated = product_store.apply_aisle_enrichment(key, aisle_by_sku)
                        aisle_summary = f"{updated}/{len(skus)}"
                        logger.info("%s: %d/%d rows now have real aisle data", key, updated, len(skus))
                    except Exception as exc:
                        logger.warning("Aisle enrichment skipped for %s: %s", key, exc)

                    pilot_progress.record_outcome(ok=True)
                    summary_rows.append(
                        {
                            "key": key,
                            "status": "OK",
                            "skus": len(outcome),
                            "new": stats.new,
                            "changed": stats.changed,
                            "unchanged": stats.unchanged,
                            "aisle": aisle_summary,
                            "duration": duration,
                        }
                    )

                results, failures = await orchestrator.run_for_stores(
                    targets,
                    scrape_date,
                    max_pages_per_term=5,
                    max_search_terms=30,
                    max_concurrent_stores=MAX_CONCURRENT_STORES,
                    launch_stagger_seconds=LAUNCH_STAGGER_SECONDS,
                    on_store_start=_on_store_start,
                    on_store_done=_on_store_done,
                )

    table = Table(title=f"Pilot scrape summary — {len(results)}/{len(targets)} stores succeeded")
    table.add_column("Store")
    table.add_column("Status")
    table.add_column("SKUs", justify="right")
    table.add_column("New", justify="right")
    table.add_column("Changed", justify="right")
    table.add_column("Unchanged", justify="right")
    table.add_column("Aisle data")
    table.add_column("Duration", justify="right")
    for row in sorted(summary_rows, key=lambda r: r["key"]):
        status_style = "green" if row["status"] == "OK" else "red"
        table.add_row(
            row["key"],
            f"[{status_style}]{row['status']}[/{status_style}]",
            str(row["skus"]),
            str(row["new"]),
            str(row["changed"]),
            str(row["unchanged"]),
            row["aisle"],
            f"{row['duration']:.1f}s",
        )
    console.print(table)

    logger.info("Pilot scrape done: %d/%d stores succeeded", len(results), len(targets))
    for key in sorted(results):
        logger.info("  OK   %s", key)
    for key in sorted(failures):
        logger.info("  FAIL %s", key)

    return 0 if results else 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
    except Exception:
        # Logged with full traceback so a crashed run can be diagnosed from
        # scraper.log alone, without needing to reproduce it.
        logging.getLogger("hybrid_scraper.demo_scrape").exception("demo_scrape.py crashed with an unhandled exception")
        sys.exit(1)
    sys.exit(exit_code)
