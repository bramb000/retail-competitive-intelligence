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
availability in the same response.

SAFE / MULTI-NIGHT MODE (2026-08-19): a fast, concurrent first attempt at
the full 42-store run tripped a soft-block on apigw.coles.com.au (requests
silently hung — see aisle_enrichment.py's hard-timeout/consecutive-timeout
handling, added in response). Since this pilot has several nights to finish
in rather than minutes, it now trades speed for safety:
  - Fully sequential — one request in flight, ever (no store or batch
    concurrency) — paced by a randomized --min-delay/--max-delay between
    requests, rather than a fast burst throttled only by a concurrency cap.
  - Checkpoints every completed store to --checkpoint-file immediately, so
    a session that's stopped (Ctrl+C, --max-hours elapsing, a sustained
    anti-bot block detected) can be resumed later with `python
    demo_scrape_mobile.py` again — already-completed stores are skipped.
  - Stops cleanly after --max-hours (default 8) *between* stores, never
    mid-store, so a session boundary never discards partially-finished work.
  - Stops the whole session immediately (not just the current store) if
    MobileBatchFetcher reports a sustained run of consecutive timeouts —
    that's the signal of a real block, and pushing straight into the next
    store would likely just hit the same wall.

Default pacing (1.0-2.0s between requests, ~1.5s average) x 2,962 batches
per store (29,616 SKUs / 10 per batch) x 42 stores ≈ 124,000 requests
total, averaging out to roughly 50 hours of actual request time — so
across 4-5 nights of an ~8-10 hour window each, this should comfortably
finish. Tune --min-delay/--max-delay/--max-hours based on how the first
night or two goes; there's no rush.

Run:
    python demo_scrape_mobile.py                        # tonight's session, up to --max-hours (default 8)
    python demo_scrape_mobile.py --max-hours 10          # a longer night
    python demo_scrape_mobile.py --limit-stores 2 --limit-skus 200 --max-hours 1   # smoke test
    python demo_scrape_mobile.py --reset-checkpoint      # start the whole 42-store pilot over from scratch

Exit codes match demo_scrape.py's convention: 0 if at least one store
succeeded (this session, or already done from a prior one), 1 if every
store failed or the script crashed outright. Stopping early — Ctrl+C,
--max-hours elapsing, or a detected block — is NOT a crash: whatever
stores finished are already in DuckDB and checkpointed, so re-running the
same command continues where this session left off.

Known gotcha: scraper_data.duckdb can only be opened for writing while no
other connection (e.g. `streamlit run dashboard.py`) has it open.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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
from hybrid_scraper.exceptions import NetworkError
from hybrid_scraper.logging_config import configure_logging
from hybrid_scraper.mobile_products import fetch_coles_products_via_mobile, load_catalogue_categories
from hybrid_scraper.models import StoreLocation
from hybrid_scraper.storage import ProductStore, store_id_for

logger = logging.getLogger("hybrid_scraper.demo_scrape_mobile")

DEFAULT_CHECKPOINT_FILE = Path(__file__).resolve().parent / "pilot_mobile_checkpoint.json"

# Fully sequential by design (see module docstring) — pacing comes from
# --min-delay/--max-delay, not from a concurrency cap.
BATCH_CONCURRENCY = 1
DEFAULT_MIN_DELAY_SECONDS = 1.0
DEFAULT_MAX_DELAY_SECONDS = 2.0
DEFAULT_MAX_HOURS = 8.0


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit-stores", type=int, default=None, help="Only consider the first N demo stores")
    parser.add_argument(
        "--limit-skus", type=int, default=None, help="Only check the first N catalogue SKUs per store (smoke test)"
    )
    parser.add_argument(
        "--max-hours",
        type=float,
        default=DEFAULT_MAX_HOURS,
        help=f"Stop cleanly after this many hours this session (default {DEFAULT_MAX_HOURS}) — finishes the "
        "in-progress store, then exits without starting another. Re-run to continue.",
    )
    parser.add_argument(
        "--min-delay", type=float, default=DEFAULT_MIN_DELAY_SECONDS, help="Minimum seconds between requests"
    )
    parser.add_argument(
        "--max-delay", type=float, default=DEFAULT_MAX_DELAY_SECONDS, help="Maximum seconds between requests"
    )
    parser.add_argument(
        "--checkpoint-file",
        type=Path,
        default=DEFAULT_CHECKPOINT_FILE,
        help="Where completed-store progress is tracked across sessions",
    )
    parser.add_argument(
        "--reset-checkpoint", action="store_true", help="Ignore/clear any existing checkpoint and start over"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress per-store console log lines; keep only the progress display"
    )
    return parser.parse_args(argv)


def _load_checkpoint(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")).get("completed_store_keys", []))
    except (json.JSONDecodeError, OSError):
        logger.warning("Checkpoint file %s unreadable — treating as empty", path)
        return set()


def _save_checkpoint(path: Path, completed: Set[str]) -> None:
    # Immediate write-through after every store, not batched — a session
    # can end at any moment (Ctrl+C, time budget, detected block), and the
    # whole point of checkpointing is that nothing already-completed is
    # ever re-fetched.
    path.write_text(json.dumps({"completed_store_keys": sorted(completed)}, indent=2), encoding="utf-8")


class _PilotProgress:
    """Live Rich display: one overall bar plus one line for the store currently in progress.

    Simpler than demo_scrape.py's version since this pilot is now strictly
    sequential — never more than one store's line at a time.
    """

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
        self._overall_task = self._progress.add_task("Pilot scrape (mobile API, safe mode)", total=total_stores)

    def __enter__(self) -> "_PilotProgress":
        self._progress.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._progress.stop()

    def _refresh_overall_description(self) -> None:
        self._progress.update(
            self._overall_task,
            description=f"Pilot scrape (mobile API, safe mode) — {self.ok_count} ok, {self.fail_count} failed",
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

    if args.reset_checkpoint and args.checkpoint_file.exists():
        args.checkpoint_file.unlink()
        logger.info("Checkpoint reset: %s removed", args.checkpoint_file)

    completed = _load_checkpoint(args.checkpoint_file)

    targets = load_demo_store_locations()
    if args.limit_stores:
        targets = targets[: args.limit_stores]

    category_by_sku = load_catalogue_categories()
    skus = [str(sku) for sku in category_by_sku]
    if args.limit_skus:
        skus = skus[: args.limit_skus]

    remaining = [t for t in targets if store_id_for(t.retailer, t.store_id) not in completed]
    already_done = len(targets) - len(remaining)

    if not remaining:
        console.print(
            f"[green]All {len(targets)} store(s) already completed[/green] per {args.checkpoint_file} — "
            "nothing left to do. Pass --reset-checkpoint to redo the pilot from scratch."
        )
        logger.info("Nothing to do: all %d store(s) already checkpointed complete", len(targets))
        return 0

    console.print(
        f"[bold]Pilot scrape (mobile API, safe/multi-night mode) starting:[/bold] "
        f"{len(remaining)} store(s) remaining ({already_done} already done from a prior session), "
        f"{len(skus)} SKU(s) per store, sequential pacing {args.min_delay:.1f}-{args.max_delay:.1f}s/request, "
        f"stopping after {args.max_hours:.1f}h this session"
    )
    logger.info(
        "Pilot scrape (mobile API, safe mode) starting: %d/%d store(s) remaining, %d SKU(s) per store, "
        "pacing %.1f-%.1fs, max_hours=%.1f",
        len(remaining),
        len(targets),
        len(skus),
        args.min_delay,
        args.max_delay,
        args.max_hours,
    )

    scrape_date = date.today().isoformat()
    fetcher = MobileBatchFetcher(
        max_concurrent_batches=BATCH_CONCURRENCY,
        pace_min_seconds=args.min_delay,
        pace_max_seconds=args.max_delay,
    )
    summary_rows: List[Dict[str, Any]] = []
    total_batches = (len(skus) + 9) // 10 or 1
    deadline = time.monotonic() + args.max_hours * 3600
    stop_reason: Optional[str] = None

    with ProductStore() as product_store, _PilotProgress(console, len(remaining)) as pilot_progress:
        async with AsyncSession() as session:
            for store_location in remaining:
                if time.monotonic() >= deadline:
                    stop_reason = f"time budget ({args.max_hours:.1f}h) reached"
                    break

                key = store_id_for(store_location.retailer, store_location.store_id)
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
                except NetworkError as exc:
                    # A sustained run of consecutive timeouts — the signal
                    # that something (most likely the WAF) is blocking this
                    # session outright. Stop everything for tonight rather
                    # than ploughing into the next store and hitting the
                    # same wall — the checkpoint means nothing is lost.
                    duration = pilot_progress.store_finished(key)
                    logger.error("%s: FAILED after %.1fs — %s", key, duration, exc)
                    pilot_progress.record_outcome(ok=False)
                    summary_rows.append(
                        {
                            "key": key,
                            "status": "BLOCKED",
                            "skus": 0,
                            "new": 0,
                            "changed": 0,
                            "unchanged": 0,
                            "duration": duration,
                        }
                    )
                    stop_reason = "a sustained block was detected (see the BLOCKED row below)"
                    break
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
                    continue

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
                completed.add(key)
                _save_checkpoint(args.checkpoint_file, completed)

    if fetcher.timed_out_batches:
        console.print(
            f"[yellow]{fetcher.timed_out_batches} batch(es) hard-timed-out during this session "
            "(no response within 25s) — see scraper.log for which stores/SKUs were affected.[/yellow]"
        )
        logger.warning("Session had %d hard-timed-out batch(es) in total", fetcher.timed_out_batches)

    ok_count = sum(1 for row in summary_rows if row["status"] == "OK")
    table = Table(
        title=f"Pilot scrape (mobile API) session summary — {ok_count}/{len(remaining)} attempted store(s) succeeded"
    )
    table.add_column("Store")
    table.add_column("Status")
    table.add_column("SKUs stocked", justify="right")
    table.add_column("New", justify="right")
    table.add_column("Changed", justify="right")
    table.add_column("Unchanged", justify="right")
    table.add_column("Duration", justify="right")
    for row in sorted(summary_rows, key=lambda r: r["key"]):
        status_style = {"OK": "green", "FAILED": "red", "BLOCKED": "red"}[row["status"]]
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

    still_remaining = sum(1 for t in targets if store_id_for(t.retailer, t.store_id) not in completed)
    if stop_reason is not None:
        console.print(
            f"[yellow]Stopped early — {stop_reason}.[/yellow] "
            f"{still_remaining} of {len(targets)} store(s) still remaining. "
            "Re-run this command (same checkpoint file) to continue."
        )
        logger.info("Session stopped early: %s. %d/%d store(s) remaining", stop_reason, still_remaining, len(targets))
    elif still_remaining:
        console.print(
            f"[green]Session complete.[/green] {still_remaining} store(s) still remaining for a future session."
        )
    else:
        console.print(f"[bold green]All {len(targets)} store(s) complete![/bold green]")

    logger.info(
        "Session done: %d/%d store(s) succeeded this session, %d/%d total complete",
        ok_count,
        len(remaining),
        len(targets) - still_remaining,
        len(targets),
    )
    return 0 if (ok_count > 0 or already_done > 0) else 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
    except KeyboardInterrupt:
        # Expected way to end a session early in multi-night mode — not a
        # crash. Whatever completed before this point is already recorded
        # in DuckDB and checkpointed, so this is a clean, resumable stop.
        logging.getLogger("hybrid_scraper.demo_scrape_mobile").info(
            "Stopped by user (Ctrl+C) — progress so far is saved; re-run to continue."
        )
        sys.exit(0)
    except Exception:
        logging.getLogger("hybrid_scraper.demo_scrape_mobile").exception(
            "demo_scrape_mobile.py crashed with an unhandled exception"
        )
        sys.exit(1)
    sys.exit(exit_code)
