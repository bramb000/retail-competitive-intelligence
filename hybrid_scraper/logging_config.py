"""Centralized logging setup for guess-free QA.

Every module logs under the `hybrid_scraper.<module>` namespace (via
`logging.getLogger(__name__)`), so one `configure_logging()` call controls
verbosity and destinations everywhere. Messages use `key=value` pairs for
identifying context (retailer, store_id, run_number, url, status_code,
attempt) so a failure can be grepped straight out of the log file instead of
re-run to reproduce — e.g. `grep 'store_id=0584' scraper.log` reconstructs
that store's entire bootstrap/fetch/retry timeline after the fact.

Secrets policy: cookie/header VALUES (session tokens, subscription keys,
bearer tokens) are never logged — only their names/counts — since this log
file is the first thing to hand over when asking for debugging help.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional, Union

DEFAULT_LOG_FILE = "scraper.log"
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    log_file: Union[str, Path] = DEFAULT_LOG_FILE,
    rich_console: Optional["Console"] = None,  # noqa: F821 - forward ref, see below
) -> None:
    """Attach console + rotating-file handlers to the `hybrid_scraper` logger tree.

    Idempotent: calling this more than once (e.g. once from main.py, once
    from a test) is a no-op after the first call, so modules don't end up
    with duplicated handlers and doubled log lines.

    `rich_console`, if given, swaps the plain stdout console handler for a
    `rich.logging.RichHandler` bound to that `Console` — colorized levels,
    and (via Rich's own coordination) log lines that print cleanly above a
    `rich.progress.Progress` live display on the same console instead of
    corrupting it. File logging is unaffected either way. Only
    `demo_scrape.py` opts into this today; every other caller keeps the
    plain-text console format.
    """
    package_logger = logging.getLogger("hybrid_scraper")
    if package_logger.handlers:
        return
    package_logger.setLevel(min(console_level, file_level))

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    if rich_console is not None:
        from rich.logging import RichHandler

        console_handler = RichHandler(console=rich_console, show_path=False, log_time_format=DATE_FORMAT, markup=False)
        console_handler.setLevel(console_level)
    else:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(console_level)
        console_handler.setFormatter(formatter)
    package_logger.addHandler(console_handler)

    # Full detail always goes to disk, even when the console is kept quiet,
    # so a failure that scrolled past on screen is still fully reconstructable.
    file_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    package_logger.addHandler(file_handler)

    package_logger.propagate = False
