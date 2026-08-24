"""Slow Ashfield-only deep scrape for Coles (791) and Woolworths (1213).

Anti-bot first: concurrency 1, jittered delays, checkpoint/resume, stop on
sustained 403/429/WAF. Writes immutable bronze JSONL, then optional silver/gold ETL.

Does not run unless you invoke it. Resume is the default.

  .venv/bin/python scrape_ashfield_deep.py --canary
  .venv/bin/python scrape_ashfield_deep.py --banner coles
  .venv/bin/python scrape_ashfield_deep.py --banner woolworths
  .venv/bin/python scrape_ashfield_deep.py --phase etl

Session warmup (emulator boot + mitm capture) runs automatically at start.
Use --skip-session-warmup only if sessions are already fresh.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import random
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

from hybrid_scraper.ashfield_session import warmup_ashfield_sessions
from hybrid_scraper.aisle_enrichment import BATCH_SIZE, MobileBatchFetcher
from hybrid_scraper.bootstrapper import PlaywrightBootstrapper
from hybrid_scraper.engine import CurlCffiEngine
from hybrid_scraper.exceptions import AuthExpiredError, NetworkError, RateLimitError
from hybrid_scraper.logging_config import configure_logging
from hybrid_scraper.mobile_products import _parse_mobile_product, load_catalogue_categories
from hybrid_scraper.models import Product, StoreLocation
from hybrid_scraper.storage import ProductStore
from hybrid_scraper.woolworths_aisle_enrichment import (
    fetch_product_details,
    fetch_product_list_page,
    is_ashfield_instore_product,
    product_id_from_card,
    product_summary,
)
from lake.etl.bronze_to_silver import run_bronze_to_silver
from lake.etl.silver_to_gold import run_silver_to_gold
from lake.io import (
    append_jsonl,
    bronze_dir,
    checkpoint_path,
    load_checkpoint,
    merge_checkpoint,
    new_run_id,
    save_checkpoint,
    utc_now_iso,
)

logger = logging.getLogger("hybrid_scraper.ashfield_deep")

REPO_ROOT = Path(__file__).resolve().parent
SAMPLE_CSV = REPO_ROOT / "ashfield_sample_skus.csv"
DEFAULT_LOG_FILE = REPO_ROOT / "ashfield_deep.log"

COLES_STORE = StoreLocation(
    retailer="Coles",
    store_id="791",
    store_name="Coles Ashfield",
    suburb_name="Ashfield",
    state="NSW",
    postcode="2131",
    latitude=-33.889879,
    longitude=151.124763,
)
WW_STORE = StoreLocation(
    retailer="Woolworths",
    store_id="1213",
    store_name="Woolworths Ashfield",
    suburb_name="Ashfield",
    state="NSW",
    postcode="2131",
    latitude=-33.8895,
    longitude=151.1250,
)

# Tuned for ~6–8h full-store Coles (~10s/batch incl. request). Raise if 403s spike.
DEFAULT_COLES_MIN_DELAY = 4.0
DEFAULT_COLES_MAX_DELAY = 10.0
# WW search (productList / website) — still conservative; Iris PDP is separate below.
DEFAULT_WW_MIN_DELAY = 4.0
DEFAULT_WW_MAX_DELAY = 9.0
# Iris productDetailsPage — from scripts/bench_ww_iris_rate.py soak (0.75s fixed =
# 40/40 ok, ~2500/h; 0.5s faster but intermittent INTERNAL_SERVER_ERROR empties).
DEFAULT_WW_IRIS_MIN_DELAY = 0.65
DEFAULT_WW_IRIS_MAX_DELAY = 0.9
DEFAULT_MAX_HOURS = 8.0
DEFAULT_PAUSE_EVERY = 0  # long cron-like pauses off; use CLI to re-enable
DEFAULT_PAUSE_SECONDS = 60.0
CANARY_SKU_LIMIT = 5
WW_IRIS_POLL_SECONDS = 30.0
WW_IRIS_LIST_PAGE_SIZE = 40
# Top-level WW website categories that are not stocked at a typical supermarket.
_WW_SKIP_ROOT_CATEGORIES = frozenset(
    {
        "Everyday Market",
        "Computers",
        "Home & Lifestyle",
        "Entertainment",
        "Baby & Child",
    }
)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--banner",
        choices=("coles", "woolworths", "both"),
        default="both",
        help="Which banner to scrape (ignored for --phase etl)",
    )
    parser.add_argument(
        "--phase",
        choices=("scrape", "etl", "all"),
        default="scrape",
        help="scrape writes bronze; etl runs silver+gold; all does scrape then etl",
    )
    parser.add_argument("--canary", action="store_true", help="5-SKU smoke test, still slow delays, no long pauses")
    parser.add_argument("--max-hours", type=float, default=DEFAULT_MAX_HOURS, help="Stop between batches after this many hours")
    parser.add_argument("--min-delay", type=float, default=None, help="Override min seconds between requests")
    parser.add_argument("--max-delay", type=float, default=None, help="Override max seconds between requests")
    parser.add_argument("--pause-every", type=int, default=DEFAULT_PAUSE_EVERY, help="Pause after this many batches/pages (0 to disable)")
    parser.add_argument("--pause-seconds", type=float, default=DEFAULT_PAUSE_SECONDS, help="Length of anti-bot pause")
    parser.add_argument("--max-pages", type=int, default=50, help="WW Iris search max pages per leaf term")
    parser.add_argument(
        "--ww-phase",
        choices=("search", "iris", "both"),
        default="both",
        help="WW: Iris store discovery (search), productDetailsPage (iris), or both sequentially",
    )
    parser.add_argument("--limit-skus", type=int, default=None, help="Cap Coles catalogue / WW Iris SKUs")
    parser.add_argument("--limit-terms", type=int, default=None, help="Cap WW search leaf terms")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Ignore existing checkpoint and start a new run_id")
    parser.add_argument("--record-duckdb", action="store_true", help="Also write parsed products into scraper_data.duckdb")
    parser.add_argument("--force-session", action="store_true", help="Force fresh mobile session capture")
    parser.add_argument(
        "--skip-session-warmup",
        action="store_true",
        help="Skip automatic emulator/session warmup (advanced; sessions must already be valid)",
    )
    parser.add_argument("--quiet", action="store_true", help="Console WARNING only; progress bar still shows")
    return parser.parse_args(argv)


def _delays(args: argparse.Namespace, banner: str) -> tuple:
    if banner == "coles":
        lo = args.min_delay if args.min_delay is not None else DEFAULT_COLES_MIN_DELAY
        hi = args.max_delay if args.max_delay is not None else DEFAULT_COLES_MAX_DELAY
    elif banner == "woolworths_iris":
        lo = args.min_delay if args.min_delay is not None else DEFAULT_WW_IRIS_MIN_DELAY
        hi = args.max_delay if args.max_delay is not None else DEFAULT_WW_IRIS_MAX_DELAY
    else:
        lo = args.min_delay if args.min_delay is not None else DEFAULT_WW_MIN_DELAY
        hi = args.max_delay if args.max_delay is not None else DEFAULT_WW_MAX_DELAY
    return lo, max(hi, lo)


def _sample_skus(retailer: str) -> List[str]:
    if SAMPLE_CSV.exists():
        ids: List[str] = []
        with SAMPLE_CSV.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("retailer") != retailer:
                    continue
                pid = (row.get("retailer_product_id") or "").strip()
                if pid:
                    ids.append(pid)
        if ids:
            return ids[:CANARY_SKU_LIMIT]
    if retailer == "Coles":
        return ["7667368", "329607", "3646151"]
    return ["36066", "277728"]


def _maybe_pause(args: argparse.Namespace, n_done: int, label: str) -> None:
    if args.canary or args.pause_every <= 0:
        return
    if n_done > 0 and n_done % args.pause_every == 0:
        # Randomize the long pause so it doesn't look like a fixed cron.
        pause = args.pause_seconds * random.uniform(0.7, 1.4)
        logger.warning(
            "anti-bot pause start label=%s n_done=%d pause_seconds=%.0f",
            label,
            n_done,
            pause,
        )
        time.sleep(pause)
        logger.info("anti-bot pause done label=%s n_done=%d", label, n_done)


class RunProgress:
    def __init__(self, console: Console, description: str, total: int) -> None:
        self.ok = 0
        self.empty = 0
        self.failed = 0
        self._label = description
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
        self._task = self._progress.add_task(description, total=max(total, 1))

    def __enter__(self) -> "RunProgress":
        self._progress.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._progress.stop()

    def advance(self, outcome: str = "ok") -> None:
        if outcome == "ok":
            self.ok += 1
        elif outcome == "empty":
            self.empty += 1
        else:
            self.failed += 1
        self._progress.update(
            self._task,
            advance=1,
            description=f"{self._label} — ok={self.ok} empty={self.empty} fail={self.failed}",
        )


def _ensure_run(checkpoint: Dict[str, Any], reset: bool) -> str:
    if reset or not checkpoint.get("run_id"):
        run_id = new_run_id()
        logger.info("new run_id=%s reset=%s", run_id, reset)
        return run_id
    logger.info("resume run_id=%s", checkpoint["run_id"])
    return str(checkpoint["run_id"])


async def scrape_coles(args: argparse.Namespace, console: Console) -> Dict[str, Any]:
    lo, hi = _delays(args, "coles")
    cp_path = checkpoint_path("coles", COLES_STORE.store_id)
    checkpoint = {} if args.reset_checkpoint else load_checkpoint(cp_path)
    run_id = _ensure_run(checkpoint, args.reset_checkpoint)
    out_dir = bronze_dir("coles", COLES_STORE.store_id, run_id)
    jsonl_path = out_dir / "products_list.jsonl"
    deadline = time.monotonic() + args.max_hours * 3600

    category_by_sku = load_catalogue_categories()
    if args.canary:
        skus = _sample_skus("Coles")
    else:
        skus = [str(sku) for sku in category_by_sku]
        if args.limit_skus:
            skus = skus[: args.limit_skus]
    start_batch = int(checkpoint.get("next_batch_index") or 0)
    n_batches = (len(skus) + BATCH_SIZE - 1) // BATCH_SIZE or 1
    scrape_date = date.today().isoformat()

    logger.info(
        "coles scrape start store_id=%s run_id=%s skus=%d batches=%d start_batch=%d pace=%.1f-%.1fs bronze=%s",
        COLES_STORE.store_id,
        run_id,
        len(skus),
        n_batches,
        start_batch,
        lo,
        hi,
        out_dir,
    )
    console.print(
        f"[bold]Coles Ashfield {COLES_STORE.store_id}[/bold] run_id={run_id} "
        f"skus={len(skus)} batches={n_batches} resume_at={start_batch} delay={lo:.0f}-{hi:.0f}s"
    )

    fetcher = MobileBatchFetcher(max_concurrent_batches=1, pace_min_seconds=lo, pace_max_seconds=hi)
    parsed: List[Product] = []
    stop_reason: Optional[str] = None
    last_batch = start_batch

    def should_stop() -> bool:
        return time.monotonic() >= deadline

    def on_raw(batch_index: int, batch: List[str], items: List[Dict]) -> None:
        append_jsonl(
            jsonl_path,
            {
                "captured_at": utc_now_iso(),
                "store_id": COLES_STORE.store_id,
                "batch_index": batch_index,
                "skus": batch,
                "n_results": len(items),
                "results": items,
            },
        )
        logger.info(
            "coles batch done store_id=%s batch_index=%d skus=%d results=%d with_locations=%d",
            COLES_STORE.store_id,
            batch_index,
            len(batch),
            len(items),
            sum(1 for it in items if (it.get("locations") or [{}])[0].get("aisle")),
        )

    def on_done(batch_index: int) -> None:
        nonlocal last_batch
        last_batch = batch_index + 1
        checkpoint.update({"run_id": run_id, "next_batch_index": last_batch, "updated_at": utc_now_iso()})
        save_checkpoint(cp_path, checkpoint)
        progress.advance("ok" if True else "empty")
        _maybe_pause(args, last_batch, "coles-batch")

    remaining_total = max(n_batches - start_batch, 1)
    with RunProgress(console, "Coles products/list", remaining_total) as progress:
        async with AsyncSession() as session:
            try:
                parsed = await fetcher.fetch_sequential(
                    session,
                    COLES_STORE.store_id,
                    skus,
                    parse_item=lambda item: _parse_mobile_product(item, category_by_sku, scrape_date),
                    start_batch_index=start_batch,
                    on_raw_batch=on_raw,
                    on_batch_done=on_done,
                    should_stop=should_stop,
                )
            except NetworkError as exc:
                stop_reason = f"blocked: {exc}"
                logger.error("coles scrape aborted store_id=%s reason=%s", COLES_STORE.store_id, exc, exc_info=True)
            except AuthExpiredError as exc:
                stop_reason = f"auth: {exc}"
                logger.error(
                    "coles scrape paused for auth store_id=%s batch=%s reason=%s",
                    COLES_STORE.store_id,
                    last_batch,
                    exc,
                )
                console.print(
                    "[yellow]Coles auth expired and recreate failed — checkpoint saved. "
                    "Mint a fresh pool then re-run: "
                    ".venv/bin/python -m hybrid_scraper.mobile_session --pool 3[/yellow]"
                )
            except Exception as exc:
                stop_reason = f"failed: {exc}"
                logger.error("coles scrape failed store_id=%s error=%s", COLES_STORE.store_id, exc, exc_info=True)
                raise

    if should_stop() and stop_reason is None and last_batch < n_batches:
        stop_reason = f"time budget ({args.max_hours:.1f}h) reached"

    if args.record_duckdb and parsed:
        with ProductStore() as store:
            store.record_scrape(COLES_STORE, parsed, scrape_date)
        logger.info("coles duckdb recorded products=%d store_id=%s", len(parsed), COLES_STORE.store_id)

    summary = {
        "banner": "Coles",
        "store_id": COLES_STORE.store_id,
        "run_id": run_id,
        "parsed": len(parsed),
        "with_price": sum(1 for p in parsed if p.price_display is not None),
        "with_placement": sum(1 for p in parsed if p.aisle_number),
        "next_batch_index": last_batch,
        "batches_total": n_batches,
        "bronze": str(jsonl_path),
        "stop_reason": stop_reason or ("complete" if last_batch >= n_batches else "paused"),
    }
    checkpoint.update(
        {
            "run_id": run_id,
            "next_batch_index": last_batch,
            "batches_total": n_batches,
            "stop_reason": summary["stop_reason"],
            "updated_at": utc_now_iso(),
        }
    )
    save_checkpoint(cp_path, checkpoint)
    logger.info("coles scrape end %s", " ".join(f"{k}={v}" for k, v in summary.items()))
    return summary


def _ww_paths(checkpoint: Dict[str, Any], reset: bool) -> tuple[Path, str, Path, Path]:
    cp_path = checkpoint_path("woolworths", WW_STORE.store_id)
    run_id = _ensure_run(checkpoint, reset)
    out_dir = bronze_dir("woolworths", WW_STORE.store_id, run_id)
    return cp_path, run_id, out_dir / "search_pages.jsonl", out_dir / "product_details.jsonl"


def _ww_migrate_to_iris_discovery(checkpoint: Dict[str, Any], cp_path: Path) -> None:
    if checkpoint.get("discovery_mode") == "iris":
        return
    old_n = len(checkpoint.get("discovered_ids") or [])
    logger.warning(
        "ww discovery migrate website -> iris store_id=%s clearing %d national website ids (iris_completed kept)",
        WW_STORE.store_id,
        old_n,
    )
    checkpoint.update(
        {
            "discovery_mode": "iris",
            "discovered_ids": [],
            "discovered_n": 0,
            "completed_terms": [],
            "in_progress_term": None,
            "in_progress_page": None,
            "in_progress_next_page": None,
            "search_complete": False,
            "iris_productlist_ok": True,
        }
    )
    save_checkpoint(cp_path, checkpoint)


def _ww_reset_for_ashfield_iris(checkpoint: Dict[str, Any], cp_path: Path) -> None:
    """Drop national website-fallback IDs and re-enable Iris productList discovery.

    Keeps iris_completed_ids so PDP work already done is not repeated when those
    SKUs reappear in the Ashfield-scoped list.
    """
    if checkpoint.get("discovery_mode") != "iris":
        return
    if checkpoint.get("iris_productlist_ok") is not False and not checkpoint.get("force_ashfield_iris_reset"):
        return
    old_n = len(checkpoint.get("discovered_ids") or [])
    logger.warning(
        "ww discovery reset for Ashfield Iris productList store_id=%s clearing %d ids "
        "(iris_completed kept=%d)",
        WW_STORE.store_id,
        old_n,
        len(checkpoint.get("iris_completed_ids") or []),
    )
    checkpoint.update(
        {
            "discovered_ids": [],
            "discovered_n": 0,
            "completed_terms": [],
            "in_progress_term": None,
            "in_progress_page": None,
            "in_progress_next_page": None,
            "search_complete": False,
            "iris_productlist_ok": True,
            "force_ashfield_iris_reset": False,
            "discovery_source": "iris_productList",
        }
    )
    save_checkpoint(cp_path, checkpoint)


def _ww_skip_category(cat: Any) -> bool:
    root = (getattr(cat, "category", None) or "").strip()
    return root in _WW_SKIP_ROOT_CATEGORIES


def _ww_website_product_id(fields: Dict[str, Any]) -> Optional[str]:
    pid = fields.get("retailer_product_id")
    if pid is None:
        return None
    return str(pid)


def _ww_website_instore(fields: Dict[str, Any]) -> bool:
    pid = _ww_website_product_id(fields)
    if not pid:
        return False
    stock = str(fields.get("stock_status") or "").lower()
    if stock and stock not in ("available", "in stock", "unknown", ""):
        return False
    return True


async def _ww_website_search_term(
    *,
    engine: CurlCffiEngine,
    bootstrapper: PlaywrightBootstrapper,
    session_ctx: Any,
    cat: Any,
    term_key: str,
    checkpoint: Dict[str, Any],
    cp_path: Path,
    run_id: str,
    search_path: Path,
    discovered: Set[str],
    args: argparse.Namespace,
    lo: float,
    hi: float,
    deadline: float,
) -> tuple[bool, bool, int, Any]:
    """Website fallback per category. Returns (term_finished, hit_deadline, new_ids, session_ctx)."""
    page = 1
    if checkpoint.get("in_progress_term") == term_key:
        try:
            page = max(1, int(checkpoint.get("in_progress_page") or 1))
        except (TypeError, ValueError):
            page = 1
    consecutive_soft_fails = 0
    term_new_ids = 0
    term_finished = False
    search_hit_deadline = False

    while page <= args.max_pages:
        if time.monotonic() >= deadline:
            search_hit_deadline = True
            break
        try:
            parsed, has_more = await engine.fetch_products_page(session_ctx, cat, page)
        except RateLimitError as exc:
            consecutive_soft_fails += 1
            wait = (exc.retry_after or 60) + random.uniform(30, 120)
            await asyncio.sleep(wait)
            if consecutive_soft_fails >= 5:
                break
            session_ctx = await bootstrapper.bootstrap("Woolworths", WW_STORE.store_id)
            continue
        except (AuthExpiredError, NetworkError) as exc:
            consecutive_soft_fails += 1
            wait = min(120.0, 10.0 * consecutive_soft_fails) + random.uniform(5.0, 20.0)
            logger.warning("ww website search retry term=%r page=%d error=%s", cat.search_term, page, exc)
            await asyncio.sleep(wait)
            if consecutive_soft_fails >= 5:
                break
            session_ctx = await bootstrapper.bootstrap("Woolworths", WW_STORE.store_id)
            continue

        consecutive_soft_fails = 0
        instore_rows = [row for row in parsed if _ww_website_instore(row)]
        new_ids: List[str] = []
        for row in instore_rows:
            pid = _ww_website_product_id(row)
            if pid and pid not in discovered:
                discovered.add(pid)
                new_ids.append(pid)
        term_new_ids += len(new_ids)
        append_jsonl(
            search_path,
            {
                "captured_at": utc_now_iso(),
                "store_id": WW_STORE.store_id,
                "source": "website_search",
                "search_term": cat.search_term,
                "category": cat.category,
                "sub_category_1": cat.sub_category_1,
                "page": page,
                "n_parsed": len(parsed),
                "n_instore": len(instore_rows),
                "n_new_ids": len(new_ids),
                "has_more": has_more,
                "new_ids": new_ids,
            },
        )
        merge_checkpoint(
            cp_path,
            {
                "run_id": run_id,
                "discovered_ids": sorted(discovered),
                "in_progress_term": term_key,
                "in_progress_page": page,
                "in_progress_next_page": None,
            },
        )
        await asyncio.sleep(random.uniform(lo, hi))
        if not has_more:
            term_finished = True
            break
        page += 1
    else:
        term_finished = True
    return term_finished, search_hit_deadline, term_new_ids, session_ctx


async def scrape_woolworths_search(args: argparse.Namespace, console: Console) -> Dict[str, Any]:
    lo, hi = _delays(args, "woolworths")
    cp_path = checkpoint_path("woolworths", WW_STORE.store_id)
    checkpoint = {} if args.reset_checkpoint else load_checkpoint(cp_path)
    cp_path, run_id, search_path, _pdp_path = _ww_paths(checkpoint, args.reset_checkpoint)
    deadline = time.monotonic() + args.max_hours * 3600

    if not args.canary:
        _ww_migrate_to_iris_discovery(checkpoint, cp_path)
        checkpoint = load_checkpoint(cp_path)
        _ww_reset_for_ashfield_iris(checkpoint, cp_path)
        checkpoint = load_checkpoint(cp_path)

    discovered: Set[str] = {str(pid) for pid in (checkpoint.get("discovered_ids") or [])}
    completed_terms: Set[str] = set(checkpoint.get("completed_terms") or [])
    search_terms_total = int(checkpoint.get("search_terms_total") or 0)
    search_hit_deadline = False

    if args.canary:
        for pid in _sample_skus("Woolworths"):
            discovered.add(str(pid))
        merge_checkpoint(
            cp_path,
            {
                "run_id": run_id,
                "discovery_mode": "iris",
                "discovered_ids": sorted(discovered),
                "search_complete": True,
                "search_terms_total": 0,
                "completed_terms": [],
            },
        )
        return {
            "banner": "Woolworths",
            "ww_phase": "search",
            "store_id": WW_STORE.store_id,
            "run_id": run_id,
            "search_discovered": len(discovered),
            "search_terms_done": 0,
            "search_terms_total": 0,
            "stop_reason": "canary",
        }

    console.print(
        f"[bold]Woolworths Ashfield {WW_STORE.store_id}[/bold] Iris discovery run_id={run_id} delay={lo:.0f}-{hi:.0f}s"
    )
    async with PlaywrightBootstrapper() as bootstrapper, CurlCffiEngine(concurrency=1, request_timeout=45.0) as engine:
        session_ctx = await bootstrapper.bootstrap("Woolworths", WW_STORE.store_id)
        categories = await engine.fetch_categories(session_ctx)
        if args.limit_terms:
            categories = categories[: args.limit_terms]
        remaining = [c for c in categories if f"{c.id}|{c.search_term}" not in completed_terms]
        search_terms_total = len(categories)
        logger.info(
            "ww iris search terms total=%d remaining=%d already_done=%d discovered=%d",
            len(categories),
            len(remaining),
            len(completed_terms),
            len(discovered),
        )
        merge_checkpoint(
            cp_path,
            {"run_id": run_id, "discovery_mode": "iris", "search_terms_total": search_terms_total},
        )

        iris_productlist_ok = bool(checkpoint.get("iris_productlist_ok", True))
        with RunProgress(console, "WW store discovery", max(len(remaining), 1)) as progress:
            async with AsyncSession() as iris_session:
                for cat in remaining:
                    if _ww_skip_category(cat):
                        term_key = f"{cat.id}|{cat.search_term}"
                        completed_terms.add(term_key)
                        merge_checkpoint(cp_path, {"completed_terms": sorted(completed_terms)})
                        progress.advance("ok")
                        logger.info("ww skip non-grocery term=%r category=%r", cat.search_term, cat.category)
                        continue

                    checkpoint = load_checkpoint(cp_path)
                    iris_productlist_ok = bool(checkpoint.get("iris_productlist_ok", iris_productlist_ok))
                    if time.monotonic() >= deadline:
                        logger.warning("ww search stop: time budget term=%r", cat.search_term)
                        search_hit_deadline = True
                        break
                    term_key = f"{cat.id}|{cat.search_term}"
                    term_new_ids = 0
                    term_finished = False

                    if iris_productlist_ok:
                        page_num = 1
                        next_page: Optional[Any] = None
                        if checkpoint.get("in_progress_term") == term_key:
                            page_num = max(1, int(checkpoint.get("in_progress_page") or 1))
                            next_page = checkpoint.get("in_progress_next_page")
                        consecutive_soft_fails = 0
                        iris_failed = False
                        try:
                            while page_num <= args.max_pages:
                                if time.monotonic() >= deadline:
                                    search_hit_deadline = True
                                    break
                                try:
                                    cards, next_page, total = await fetch_product_list_page(
                                        iris_session,
                                        WW_STORE.store_id,
                                        cat.search_term,
                                        page_size=WW_IRIS_LIST_PAGE_SIZE,
                                        next_page=next_page,
                                    )
                                except (AuthExpiredError, NetworkError) as exc:
                                    consecutive_soft_fails += 1
                                    if consecutive_soft_fails >= 3:
                                        iris_failed = True
                                        logger.warning(
                                            "ww iris productList soft-fail term=%r error=%s — "
                                            "skip term (keep Iris enabled; no website national fallback)",
                                            cat.search_term,
                                            exc,
                                        )
                                        break
                                    await asyncio.sleep(min(120.0, 10.0 * consecutive_soft_fails))
                                    continue

                                consecutive_soft_fails = 0
                                instore_cards = [c for c in cards if is_ashfield_instore_product(c)]
                                new_ids: List[str] = []
                                for card in instore_cards:
                                    pid = product_id_from_card(card)
                                    if pid and pid not in discovered:
                                        discovered.add(pid)
                                        new_ids.append(pid)
                                term_new_ids += len(new_ids)
                                append_jsonl(
                                    search_path,
                                    {
                                        "captured_at": utc_now_iso(),
                                        "store_id": WW_STORE.store_id,
                                        "source": "iris_productList",
                                        "search_term": cat.search_term,
                                        "category": cat.category,
                                        "sub_category_1": cat.sub_category_1,
                                        "page": page_num,
                                        "n_cards": len(cards),
                                        "n_instore": len(instore_cards),
                                        "n_new_ids": len(new_ids),
                                        "total_reported": total,
                                        "has_more": bool(next_page),
                                        "new_ids": new_ids,
                                    },
                                )
                                merge_checkpoint(
                                    cp_path,
                                    {
                                        "run_id": run_id,
                                        "discovered_ids": sorted(discovered),
                                        "discovery_source": "iris_productList",
                                        "iris_productlist_ok": True,
                                        "in_progress_term": term_key,
                                        "in_progress_page": page_num,
                                        "in_progress_next_page": next_page,
                                    },
                                )
                                await asyncio.sleep(random.uniform(lo, hi))
                                if not next_page:
                                    term_finished = True
                                    break
                                page_num += 1
                            else:
                                if not iris_failed:
                                    term_finished = True
                        except Exception as exc:
                            logger.error("ww iris search failed term=%r error=%s", cat.search_term, exc, exc_info=True)
                            iris_failed = True

                        if iris_failed and not term_finished:
                            # Prefer empty Ashfield miss over national website inflation.
                            logger.warning(
                                "ww iris term abandoned term=%r — marking complete with no website fallback",
                                cat.search_term,
                            )
                            term_finished = True
                    else:
                        # Legacy path only if explicitly forced off; default is Iris-only.
                        logger.warning(
                            "ww iris_productlist_ok=false — forcing Ashfield Iris reset instead of website crawl"
                        )
                        merge_checkpoint(
                            cp_path,
                            {"force_ashfield_iris_reset": True, "iris_productlist_ok": False},
                        )
                        _ww_reset_for_ashfield_iris(load_checkpoint(cp_path), cp_path)
                        checkpoint = load_checkpoint(cp_path)
                        discovered = {str(pid) for pid in (checkpoint.get("discovered_ids") or [])}
                        completed_terms = set(checkpoint.get("completed_terms") or [])
                        iris_productlist_ok = True
                        term_finished = False
                        continue

                    if term_finished:
                        completed_terms.add(term_key)
                        merge_checkpoint(
                            cp_path,
                            {
                                "run_id": run_id,
                                "completed_terms": sorted(completed_terms),
                                "discovered_ids": sorted(discovered),
                                "in_progress_term": None,
                                "in_progress_page": None,
                                "in_progress_next_page": None,
                            },
                        )
                        progress.advance("ok")
                        logger.info(
                            "ww search term done term=%r new_ids=%d discovered=%d iris=%s",
                            cat.search_term,
                            term_new_ids,
                            len(discovered),
                            iris_productlist_ok,
                        )
                    else:
                        progress.advance("fail")

                    if search_hit_deadline:
                        break

        search_complete = (
        (not search_hit_deadline)
        and search_terms_total > 0
        and len(completed_terms) >= search_terms_total
    )
    stop_reason = "complete" if search_complete else "paused"
    if search_hit_deadline or time.monotonic() >= deadline:
        stop_reason = f"time budget ({args.max_hours:.1f}h) reached"

    merge_checkpoint(
        cp_path,
        {
            "run_id": run_id,
            "discovered_ids": sorted(discovered),
            "completed_terms": sorted(completed_terms),
            "search_terms_total": search_terms_total,
            "search_complete": search_complete,
            "stop_reason": stop_reason if not search_complete else checkpoint.get("stop_reason"),
            "in_progress_term": None,
            "in_progress_page": None,
            "in_progress_next_page": None,
        },
    )
    summary = {
        "banner": "Woolworths",
        "ww_phase": "search",
        "store_id": WW_STORE.store_id,
        "run_id": run_id,
        "search_discovered": len(discovered),
        "search_terms_done": len(completed_terms),
        "search_terms_total": search_terms_total,
        "search_complete": search_complete,
        "bronze_search": str(search_path),
        "stop_reason": stop_reason,
    }
    logger.info("ww search end %s", " ".join(f"{k}={v}" for k, v in summary.items()))
    return summary


async def scrape_woolworths_iris(args: argparse.Namespace, console: Console) -> Dict[str, Any]:
    lo, hi = _delays(args, "woolworths_iris")
    cp_path = checkpoint_path("woolworths", WW_STORE.store_id)
    checkpoint = {} if args.reset_checkpoint else load_checkpoint(cp_path)
    cp_path, run_id, _search_path, pdp_path = _ww_paths(checkpoint, args.reset_checkpoint)
    deadline = time.monotonic() + args.max_hours * 3600

    console.print(f"[bold]Woolworths[/bold] Iris PDP worker run_id={run_id} delay={lo:.2f}-{hi:.2f}s")

    iris_ok = 0
    iris_fail = 0
    idle_polls = 0

    with RunProgress(console, "WW Iris productDetailsPage", total=1) as progress:
        async with AsyncSession() as session:
            while time.monotonic() < deadline:
                checkpoint = load_checkpoint(cp_path)
                product_ids = [str(pid) for pid in (checkpoint.get("discovered_ids") or [])]
                if args.limit_skus:
                    product_ids = product_ids[: args.limit_skus]
                iris_done = {str(x) for x in (checkpoint.get("iris_completed_ids") or [])}
                pending = [pid for pid in product_ids if pid not in iris_done]
                search_complete = bool(checkpoint.get("search_complete"))

                if not pending:
                    if search_complete:
                        logger.info("ww iris worker done store_id=%s processed=%d", WW_STORE.store_id, len(iris_done))
                        break
                    idle_polls += 1
                    logger.info(
                        "ww iris worker idle poll=%d discovered=%d iris_done=%d search_complete=%s sleeping=%.0fs",
                        idle_polls,
                        len(product_ids),
                        len(iris_done),
                        search_complete,
                        WW_IRIS_POLL_SECONDS,
                    )
                    await asyncio.sleep(WW_IRIS_POLL_SECONDS)
                    continue

                idle_polls = 0
                if progress._progress.tasks[progress._task].total == 1 and len(product_ids) > 1:
                    progress._progress.update(progress._task, total=len(product_ids))

                pid = pending[0]
                try:
                    card = None
                    iris_attempts = 0
                    while iris_attempts < 3:
                        iris_attempts += 1
                        try:
                            card = await fetch_product_details(session, WW_STORE.store_id, pid)
                            break
                        except (NetworkError, AuthExpiredError) as exc:
                            logger.warning(
                                "ww iris retry store_id=%s product_id=%s attempt=%d error=%s",
                                WW_STORE.store_id,
                                pid,
                                iris_attempts,
                                exc,
                            )
                            if iris_attempts >= 3:
                                raise
                            await asyncio.sleep(random.uniform(8.0, 25.0))
                    summary = product_summary(card) if card else {}
                    append_jsonl(
                        pdp_path,
                        {
                            "captured_at": utc_now_iso(),
                            "store_id": WW_STORE.store_id,
                            "product_id": pid,
                            "ok": card is not None,
                            "summary": summary,
                            "card": card,
                        },
                    )
                    if card:
                        iris_ok += 1
                        progress.advance("ok")
                        logger.info(
                            "ww iris ok store_id=%s product_id=%s price=%s aisle=%s bay=%s",
                            WW_STORE.store_id,
                            pid,
                            summary.get("price"),
                            summary.get("aisle_number"),
                            summary.get("bay_number"),
                        )
                    else:
                        iris_fail += 1
                        progress.advance("empty")
                except Exception as exc:
                    iris_fail += 1
                    progress.advance("fail")
                    logger.error(
                        "ww iris failed store_id=%s product_id=%s error=%s",
                        WW_STORE.store_id,
                        pid,
                        exc,
                        exc_info=True,
                    )
                    append_jsonl(
                        pdp_path,
                        {
                            "captured_at": utc_now_iso(),
                            "store_id": WW_STORE.store_id,
                            "product_id": pid,
                            "ok": False,
                            "error_type": type(exc).__name__,
                        },
                    )

                merge_checkpoint(cp_path, {"run_id": run_id, "iris_completed_ids": [pid]})
                await asyncio.sleep(random.uniform(lo, hi))
                _maybe_pause(args, iris_ok + iris_fail, "ww-iris")

    checkpoint = load_checkpoint(cp_path)
    product_ids = [str(pid) for pid in (checkpoint.get("discovered_ids") or [])]
    if args.limit_skus:
        product_ids = product_ids[: args.limit_skus]
    iris_done = {str(x) for x in (checkpoint.get("iris_completed_ids") or [])}
    search_complete = bool(checkpoint.get("search_complete"))
    iris_complete = len(product_ids) > 0 and all(pid in iris_done for pid in product_ids)
    stop_reason = "complete" if search_complete and iris_complete else "paused"
    if time.monotonic() >= deadline and stop_reason != "complete":
        stop_reason = f"time budget ({args.max_hours:.1f}h) reached"

    merge_checkpoint(
        cp_path,
        {
            "run_id": run_id,
            "stop_reason": stop_reason,
            "search_complete": search_complete,
        },
    )
    summary = {
        "banner": "Woolworths",
        "ww_phase": "iris",
        "store_id": WW_STORE.store_id,
        "run_id": run_id,
        "iris_ok": iris_ok,
        "iris_fail": iris_fail,
        "iris_completed": len(iris_done),
        "search_discovered": len(product_ids),
        "search_complete": search_complete,
        "bronze_pdp": str(pdp_path),
        "stop_reason": stop_reason,
    }
    logger.info("ww iris end %s", " ".join(f"{k}={v}" for k, v in summary.items()))
    return summary


async def scrape_woolworths(args: argparse.Namespace, console: Console) -> Dict[str, Any]:
    phase = args.ww_phase
    summaries: List[Dict[str, Any]] = []
    if phase in ("search", "both"):
        summaries.append(await scrape_woolworths_search(args, console))
    if phase in ("iris", "both"):
        summaries.append(await scrape_woolworths_iris(args, console))
    if len(summaries) == 1:
        return summaries[0]
    merged: Dict[str, Any] = {"banner": "Woolworths", "ww_phase": phase}
    for row in summaries:
        merged.update(row)
    return merged


def run_etl(console: Console) -> Dict[str, Any]:
    logger.info("etl start")
    silver = run_bronze_to_silver()
    gold = run_silver_to_gold(silver)
    logger.info("etl done silver=%s gold=%s", silver, gold)
    console.print(f"[green]ETL done[/green] silver={silver} gold={gold}")
    return {"silver": str(silver), "gold": str(gold)}


def _print_summary(console: Console, rows: List[Dict[str, Any]]) -> None:
    table = Table(title="Ashfield deep scrape")
    keys = []
    for row in rows:
        for k in row:
            if k not in keys:
                keys.append(k)
    for k in keys:
        table.add_column(k)
    for row in rows:
        table.add_row(*[str(row.get(k, "")) for k in keys])
    console.print(table)


async def async_main(args: argparse.Namespace, console: Console) -> int:
    summaries: List[Dict[str, Any]] = []
    if args.phase in ("scrape", "all"):
        banners = ["coles", "woolworths"] if args.banner == "both" else [args.banner]
        if not args.skip_session_warmup:
            console.print("[bold]Session warmup[/bold] (emulator + mobile capture)...")
            warmup_ashfield_sessions(banners, force=args.force_session)
            console.print("[green]Session warmup OK[/green]")
        for banner in banners:
            logger.info("phase=scrape banner=%s canary=%s", banner, args.canary)
            if banner == "coles":
                summaries.append(await scrape_coles(args, console))
            else:
                summaries.append(await scrape_woolworths(args, console))
    if args.phase in ("etl", "all"):
        summaries.append(run_etl(console))
    _print_summary(console, summaries)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    console = Console()
    configure_logging(
        console_level=logging.WARNING if args.quiet else logging.INFO,
        log_file=DEFAULT_LOG_FILE,
        rich_console=console,
    )
    logger.info(
        "ashfield_deep start banner=%s phase=%s canary=%s max_hours=%.1f reset=%s",
        args.banner,
        args.phase,
        args.canary,
        args.max_hours,
        args.reset_checkpoint,
    )
    try:
        return asyncio.run(async_main(args, console))
    except KeyboardInterrupt:
        logger.warning("interrupted — checkpoint saved; re-run the same command to resume")
        console.print("[yellow]Interrupted. Re-run the same command to resume.[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
