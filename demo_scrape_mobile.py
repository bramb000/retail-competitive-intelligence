"""42-store Coles pilot scrape via the mobile app API ONLY — no website, no Playwright.

Supersedes `demo_scrape.py` for this pilot: that script discovers each
store's SKUs by driving a real Playwright browser through ~30 website
search terms, which depends on Coles' anti-bot layer letting the search box
render — increasingly unreliable under load (15-30% of stores failed with
"search box never became visible" in the 2026-08-18 run).

This script skips discovery entirely. It already knows the SKUs worth
checking — `data/coles_catalogue_categories.csv.csv`, a 29,616-SKU
nationwide catalogue built from earlier scrapes — and looks each one up
directly by ID via the same private app endpoint
`hybrid_scraper.aisle_enrichment`/`hybrid_scraper.mobile_products` already
use for aisle data, which happens to return full pricing/name/brand/
availability in the same response. A store simply omits SKUs it doesn't
stock from the results (confirmed live), so no discovery step is needed —
only a lookup.

Trade-off worth knowing: 42 stores x 29,616 SKUs / 10 per batch is ~124,000
requests to apigw.coles.com.au. This project's mobile-endpoint traffic has
so far only been validated at ~16,000 requests (an 811-store x ~20-SKU
aisle audit, zero token refreshes needed) — this pilot is ~8x that volume
against the SAME host, so a fresh anti-bot reaction at this endpoint,
though never observed here, isn't ruled out. Use --limit-stores/--limit-skus
to scope a smoke test down first.

Run:
    python demo_scrape_mobile.py                              # all 42 stores, full catalogue
    python demo_scrape_mobile.py --limit-stores 3 --limit-skus 200   # smoke test

Exit codes match demo_scrape.py's convention: 0 if at least one store
succeeded, 1 if every store failed or the script crashed outright. Results
are recorded to DuckDB store-by-store as each one finishes, so a Ctrl+C
part-way through preserves every store that already completed.

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

from hybrid_scraper.aisle_enrichment import MobileBatchFetcher
from hybrid_scraper.demo_stores import load_demo_store_locations
from hybrid_scraper.logging_config import configure_logging
from hybrid_scraper.mobile_products import fetch_coles_products_via_mobile, load_catalogue_categories
from hybrid_scraper.models import StoreLocation
from hybrid_scraper.storage import ProductStore, store_id_for

logger = logging.getLogger("hybrid_scraper.demo_scrape_mobile")

# Store-level concurrency here bounds how many stores' worth of batches are
# queued at once, not anti-bot browser challenges (there's no browser at
# all) — kept modest so the shared MobileBatchFetcher's own
# max_concurrent_batches is what actually caps total in-flight requests
# against apigw.coles.com.au, rather than every store piling on at once.
#
# Scaled DOWN from the 2026-08-18 pilot's original (4 stores / 5 batches):
# that run stalled at 0/42 stores after ~9 minutes with all 5 concurrent
# request slots stuck in CLOSE_WAIT against apigw.coles.com.au — consistent
# with Imperva soft-blocking this session under that volume. Re-validate at
# this lower concurrency (and ideally a smaller --limit-stores/--limit-skus
# scope first) before raising these back up.
MAX_CONCURRENT_STORES = 2
MAX_CONCURRENT_BATCHES = 3


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit-stores", type=int, default=None, help="Only scrape the first N demo stores")
    parser.add_argument(
        "--limit-skus", type=int, default=None, help="Only check the first N catalogue SKUs per store (smoke test)"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress per-store console log lines; keep only the progress display"
    )
    return parser.parse_args(argv)


class _PilotProgress:
    """Live Rich display: one overall bar plus one line per in-flight store — see demo_scrape.py's identical helper."""

    def __init__(self, console: Console, total_stores: int) -> None:
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
        self._overall_task = self._progress.add_task("Pilot scrape (mobile API)", total=total_stores)

    def __enter__(self) -> "_PilotProgress":
        self._progress.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._progress.stop()

    def _refresh_overall_description(self) -> None:
        self._progress.update(
            self._overall_task,
            description=f"Pilot scrape (mobile API) — {self.ok_count} ok, {self.fail_count} failed",
        )

    def store_started(self, key: str, store_location: StoreLocation, total_batches: int) -> None:
        self._start_times[key] = time.monotonic()
        self._store_tasks[key] = self._progress.add_task(
            f"  {key} {store_location.store_name} ({store_location.suburb_name})",
            total=total_batches,
        )

    def store_batch_done(self, key: str) -> None:
        task_id = self._store_tasks.get(key)
        if task_id is not None:
            self._progress.advance(task_id)

    def store_finished(self, key: str) -> float:
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
    if args.limit_stores:
        targets = targets[: args.limit_stores]

    category_by_sku = load_catalogue_categories()
    skus = [str(sku) for sku in category_by_sku]
    if args.limit_skus:
        skus = skus[: args.limit_skus]

    console.print(
        f"[bold]Pilot scrape (mobile API) starting:[/bold] {len(targets)} Coles store(s), "
        f"{len(skus)} SKU(s) checked per store, max {MAX_CONCURRENT_STORES} concurrent stores, "
        f"{MAX_CONCURRENT_BATCHES} concurrent batches"
    )
    logger.info("Pilot scrape (mobile API) starting: %d store(s), %d SKU(s) per store", len(targets), len(skus))

    scrape_date = date.today().isoformat()
    fetcher = MobileBatchFetcher(max_concurrent_batches=MAX_CONCURRENT_BATCHES)
    store_semaphore = asyncio.Semaphore(MAX_CONCURRENT_STORES)
    summary_rows: List[Dict[str, Any]] = []
    total_batches = (len(skus) + 9) // 10 or 1

    with ProductStore() as product_store, _PilotProgress(console, len(targets)) as pilot_progress:
        async with AsyncSession() as session:

            async def _run_store(store_location: StoreLocation) -> None:
                key = store_id_for(store_location.retailer, store_location.store_id)
                async with store_semaphore:
                    pilot_progress.store_started(key, store_location, total_batches)
                    logger.info(
                        "%s: starting mobile lookup (store=%r suburb=%s, %d SKUs)",
                        key,
                        store_location.store_name,
                        store_location.suburb_name,
                        len(skus),
                    )
                    try:
                        products = await fetch_coles_products_via_mobile(
                            fetcher,
                            session,
                            store_location.store_id,
                            skus,
                            category_by_sku,
                            scrape_date,
                            on_batch_done=lambda: pilot_progress.store_batch_done(key),
                        )
                    except Exception as exc:
                        duration = pilot_progress.store_finished(key)
                        logger.error("%s: FAILED after %.1fs — %s", key, duration, exc)
                        pilot_progress.record_outcome(ok=False)
                        summary_rows.append(
                            {
                                "key": key,
                                "status": "FAILED",
                                "skus": 0,
                                "new": 0,
                                "changed": 0,
                                "unchanged": 0,
                                "duration": duration,
                            }
                        )
                        return

                    duration = pilot_progress.store_finished(key)
                    stats = product_store.record_scrape(store_location, products, scrape_date)
                    logger.info(
                        "%s: %d/%d SKUs stocked, scraped in %.1fs (new=%d changed=%d unchanged=%d)",
                        key,
                        len(products),
                        len(skus),
                        duration,
                        stats.new,
                        stats.changed,
                        stats.unchanged,
                    )
                    pilot_progress.record_outcome(ok=True)
                    summary_rows.append(
                        {
                            "key": key,
                            "status": "OK",
                            "skus": len(products),
                            "new": stats.new,
                            "changed": stats.changed,
                            "unchanged": stats.unchanged,
                            "duration": duration,
                        }
                    )

            await asyncio.gather(*(_run_store(target) for target in targets))

    if fetcher.timed_out_batches:
        console.print(
            f"[yellow]{fetcher.timed_out_batches} batch(es) hard-timed-out during this run "
            "(no response within 25s) — see scraper.log for which stores/SKUs were affected.[/yellow]"
        )
        logger.warning("Run had %d hard-timed-out batch(es) in total", fetcher.timed_out_batches)

    ok_count = sum(1 for row in summary_rows if row["status"] == "OK")
    table = Table(title=f"Pilot scrape (mobile API) summary — {ok_count}/{len(targets)} stores succeeded")
    table.add_column("Store")
    table.add_column("Status")
    table.add_column("SKUs stocked", justify="right")
    table.add_column("New", justify="right")
    table.add_column("Changed", justify="right")
    table.add_column("Unchanged", justify="right")
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
            f"{row['duration']:.1f}s",
        )
    console.print(table)

    logger.info("Pilot scrape (mobile API) done: %d/%d stores succeeded", ok_count, len(targets))
    return 0 if ok_count else 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
    except Exception:
        logging.getLogger("hybrid_scraper.demo_scrape_mobile").exception(
            "demo_scrape_mobile.py crashed with an unhandled exception"
        )
        sys.exit(1)
    sys.exit(exit_code)
