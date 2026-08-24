"""Cross-process lock so Coles/Woolworths emulator+mitm captures never overlap.

Steady-state scrapes are pure HTTP and can run in parallel. Only token mint /
mitm capture needs the emulator + global http_proxy + a listen port — those
must be single-flight across processes.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_TOOLS_DIR = Path(__file__).resolve().parent.parent / ".tools"
_DEFAULT_LOCK = _TOOLS_DIR / "emulator_capture.lock"


@contextmanager
def emulator_capture_lock(
    lock_path: Optional[Path] = None,
    *,
    timeout: float = 600.0,
    poll: float = 1.0,
    holder: str = "capture",
) -> Iterator[None]:
    """Exclusive flock around emulator/mitm capture. Raises TimeoutError if waited too long."""
    path = lock_path or _DEFAULT_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        while True:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out after {timeout:.0f}s waiting for emulator capture lock "
                        f"({path}) — another Coles/WW capture is still running"
                    )
                logger.info("waiting for emulator capture lock holder=%s path=%s", holder, path)
                time.sleep(poll)
        os.ftruncate(fd, 0)
        os.write(fd, f"{holder} pid={os.getpid()} at={time.time():.0f}\n".encode())
        logger.debug("acquired emulator capture lock holder=%s", holder)
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)
