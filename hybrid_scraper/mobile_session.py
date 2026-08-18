"""Module: BlueStacks-driven capture of a fresh Coles mobile-app session.

Mirrors `hybrid_scraper.bootstrapper`'s pattern exactly, one layer down the
stack: that module lets a real (headless) browser solve Akamai/Imperva's
challenge and harvests the resulting cookies/headers; this module lets the
real, already-logged-in Coles Android app (running in BlueStacks) solve its
own device-attestation handshake and harvests the resulting `x-d-token` +
sibling headers via a local mitmproxy instance. Neither module tries to
compute the anti-bot secret itself — both just observe a real client
producing one.

Prerequisites this module assumes are already in place (one-time, manual
setup — see the module-level comment on `Product.aisle_number` in
`models.py` for how this was originally done):
  1. BlueStacks running with the Coles app installed, already logged in.
  2. That APK has its certificate-pinning check patched out (confirmed via
     its `DEBUGGABLE` manifest flag, which is what lets step 3 below work
     without root: Android's default network-security-config trusts
     user-installed CA certs for debuggable apps).
  3. mitmproxy's CA certificate installed and trusted as a user cert on the
     device (Settings -> Security -> Encryption & credentials -> Install a
     certificate -> CA certificate, using mitm.it from the device browser
     while a mitmproxy instance is reachable).
  4. The device's global HTTP proxy pointed at this host's mitmproxy
     instance (`adb shell settings put global http_proxy <PROXY_HOST>:<PROXY_PORT>`)
     — this module (re)sets it on every refresh, so step 4 is otherwise
     automatic; it's listed here only because BlueStacks' NAT gateway
     address (`PROXY_HOST`, default "10.0.2.2") is unverified for setups
     other than this one and may need overriding via env var.

Capture flow per refresh:
  1. Launch `mitmdump` (headless mitmproxy) locally with
     `mobile_capture_addon.py`, listening on `PROXY_PORT`.
  2. Point the BlueStacks device's global HTTP proxy at this host.
  3. Force-stop + relaunch the Coles app, so it makes fresh requests rather
     than reusing whatever it already had in memory.
  4. Poll the addon's output file until it captures a request carrying
     `x-d-token`, or `capture_timeout` elapses.
  5. Clear the device's proxy and stop mitmdump, regardless of outcome.

If step 4 times out, `hosts_seen_log` (see `MobileTokenCaptureError`) lists
every host the app actually contacted during the window — the likely fix is
that the app needs navigating to a specific screen (e.g. the in-store
"Wayfinding"/aisle-finder feature) before it calls apigw.coles.com.au at all;
this module only automates *launching* the app, not deep in-app navigation,
since that's specific to whatever screen triggers the call for a given app
version.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from hybrid_scraper.exceptions import MobileTokenCaptureError
from hybrid_scraper.models import MobileSessionContext

logger = logging.getLogger(__name__)

# --- Configuration (env-var overridable; defaults match this project's
# confirmed-working BlueStacks instance — see the live probe that found
# these: HD-Adb.exe's install path via the registry, "127.0.0.1:5555" via
# `adb devices`, the package name via `pm list packages`, and "10.0.2.2" via
# BlueStacks' own bluestacks.conf `ip_gateway_addr`).
ADB_PATH = os.environ.get("BLUESTACKS_ADB_PATH", r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe")
DEVICE_SERIAL = os.environ.get("BLUESTACKS_DEVICE_SERIAL", "127.0.0.1:5555")
APP_PACKAGE = os.environ.get("COLES_APP_PACKAGE", "com.coles.android.shopmate")
PROXY_HOST = os.environ.get("BLUESTACKS_PROXY_HOST", "10.0.2.2")
PROXY_PORT = int(os.environ.get("BLUESTACKS_PROXY_PORT", "8080"))

_TOOLS_DIR = Path(__file__).resolve().parent.parent / ".tools"
SESSION_CACHE_PATH = _TOOLS_DIR / "coles_mobile_session.json"
_CAPTURE_OUT_PATH = _TOOLS_DIR / "mobile_capture_result.json"
_HOSTS_LOG_PATH = _TOOLS_DIR / "mobile_capture_hosts.log"
_ADDON_PATH = Path(__file__).resolve().parent / "mobile_capture_addon.py"


class BlueStacksDevice:
    """Thin wrapper around BlueStacks' bundled adb binary for one instance."""

    def __init__(self, adb_path: str = ADB_PATH, serial: str = DEVICE_SERIAL) -> None:
        self._adb_path = adb_path
        self._serial = serial

    def _adb(self, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
        cmd = [self._adb_path, "-s", self._serial, *args]
        logger.debug("adb command: %s", " ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def connect(self) -> None:
        result = subprocess.run([self._adb_path, "connect", self._serial], capture_output=True, text=True, timeout=15)
        logger.info("adb connect %s -> %s", self._serial, result.stdout.strip() or result.stderr.strip())

    def set_proxy(self, host: str, port: int) -> None:
        self._adb("shell", "settings", "put", "global", "http_proxy", f"{host}:{port}")
        logger.debug("Set device global http_proxy=%s:%d", host, port)

    def clear_proxy(self) -> None:
        # ":0" is the standard Android convention for "no proxy" via `settings put`.
        self._adb("shell", "settings", "put", "global", "http_proxy", ":0")
        logger.debug("Cleared device global http_proxy")

    def relaunch_app(self, package: str = APP_PACKAGE) -> None:
        self._adb("shell", "am", "force-stop", package)
        time.sleep(1)
        self._adb("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")
        logger.info("Relaunched %s on %s", package, self._serial)


class MobileSessionRefresher:
    """Drives BlueStacksDevice + a local mitmdump instance to capture a fresh MobileSessionContext."""

    def __init__(self, device: Optional[BlueStacksDevice] = None) -> None:
        self._device = device or BlueStacksDevice()
        _TOOLS_DIR.mkdir(exist_ok=True)

    def _start_mitmdump(self) -> subprocess.Popen:
        for stale in (_CAPTURE_OUT_PATH, _HOSTS_LOG_PATH):
            stale.unlink(missing_ok=True)
        # `mitmproxy.tools.main` has no `-m`-runnable dispatcher of its own —
        # `mitmdump`/`mitmweb`/etc. are plain functions taking an argv list,
        # normally invoked via the separately-installed `mitmdump` console
        # script. Calling the function directly via `-c` avoids depending on
        # that script being on PATH.
        mitmdump_args = [
            "--listen-host",
            "0.0.0.0",
            "--listen-port",
            str(PROXY_PORT),
            "-s",
            str(_ADDON_PATH),
            "--set",
            f"capture_out={_CAPTURE_OUT_PATH}",
            "--set",
            f"capture_hosts_log={_HOSTS_LOG_PATH}",
            "-q",
        ]
        cmd = [
            sys.executable,
            "-c",
            "import sys; from mitmproxy.tools.main import mitmdump; sys.exit(mitmdump(sys.argv[1:]) or 0)",
            *mitmdump_args,
        ]
        logger.info("Starting mitmdump on 0.0.0.0:%d", PROXY_PORT)
        # mitmdump's own stdout (-q keeps it to just this addon's ctx.log.info
        # lines, which never include secret values) goes to a file rather
        # than DEVNULL, so a startup failure (bad flags, port already bound)
        # is diagnosable instead of silently swallowed.
        with open(_TOOLS_DIR / "mitmdump.log", "w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
        time.sleep(2)  # let mitmdump finish binding before the device starts talking to it
        if proc.poll() is not None:
            raise MobileTokenCaptureError(
                f"mitmdump exited immediately (code {proc.returncode}) — see {_TOOLS_DIR / 'mitmdump.log'}"
            )
        return proc

    def _stop_mitmdump(self, proc: subprocess.Popen) -> None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        logger.info("Stopped mitmdump")

    def refresh(self, capture_timeout: float = 60.0) -> MobileSessionContext:
        """Capture a fresh MobileSessionContext from the real app, and cache it to disk."""
        logger.info("mobile session refresh start")
        self._device.connect()
        mitm_proc = self._start_mitmdump()
        try:
            self._device.set_proxy(PROXY_HOST, PROXY_PORT)
            self._device.relaunch_app()

            deadline = time.monotonic() + capture_timeout
            while time.monotonic() < deadline:
                if _CAPTURE_OUT_PATH.exists():
                    try:
                        captured = json.loads(_CAPTURE_OUT_PATH.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        time.sleep(0.5)
                        continue
                    headers = captured["headers"]
                    logger.info(
                        "mobile session refresh success host=%s path=%s headers=%d",
                        captured.get("host"),
                        captured.get("path"),
                        len(headers),
                    )
                    session = MobileSessionContext(headers=headers, created_at=time.time())
                    SESSION_CACHE_PATH.write_text(session.model_dump_json(indent=2), encoding="utf-8")
                    return session
                time.sleep(0.5)

            hosts_seen = _HOSTS_LOG_PATH.read_text(encoding="utf-8") if _HOSTS_LOG_PATH.exists() else "(none)"
            logger.error(
                "mobile session refresh timed out after %.0fs — hosts contacted:\n%s", capture_timeout, hosts_seen
            )
            raise MobileTokenCaptureError(
                f"No x-d-token captured within {capture_timeout:.0f}s — the app never hit an "
                "apigw.coles.com.au endpoint carrying one during this window. It may need "
                "navigating to a specific screen (e.g. the in-store aisle-finder feature) "
                "before it makes that call; see the hosts-seen log for what it did contact.",
                hosts_seen_log=str(_HOSTS_LOG_PATH),
            )
        finally:
            self._device.clear_proxy()
            self._stop_mitmdump(mitm_proc)

    def get_session(self, force_refresh: bool = False, capture_timeout: float = 60.0) -> MobileSessionContext:
        """Return a cached MobileSessionContext if still fresh, else capture a new one."""
        if not force_refresh and SESSION_CACHE_PATH.exists():
            try:
                cached = MobileSessionContext.model_validate_json(SESSION_CACHE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                cached = None
            if cached is not None and not cached.is_expired():
                logger.debug("mobile session cache hit")
                return cached
        return self.refresh(capture_timeout=capture_timeout)


def get_mobile_session(force_refresh: bool = False, capture_timeout: float = 60.0) -> MobileSessionContext:
    """Convenience entry point for scraping scripts: cache-first, refresh on demand.

    Usage in a scraping script, replacing a manually-exported env var:
        from hybrid_scraper.mobile_session import get_mobile_session
        session = get_mobile_session()
        headers = session.headers
    Pass `force_refresh=True` after a 401/403 from apigw.coles.com.au to
    force a brand-new capture rather than reusing the (evidently expired)
    cached one.
    """
    return MobileSessionRefresher().get_session(force_refresh=force_refresh, capture_timeout=capture_timeout)


if __name__ == "__main__":
    import argparse

    from hybrid_scraper.logging_config import configure_logging

    parser = argparse.ArgumentParser(description="Capture a fresh Coles mobile-app session token via BlueStacks")
    parser.add_argument(
        "--force", action="store_true", help="Force a fresh capture even if a cached session is still valid"
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="Seconds to wait for a capture before giving up")
    args = parser.parse_args()

    configure_logging()
    result = get_mobile_session(force_refresh=args.force, capture_timeout=args.timeout)
    print(f"Session ready: {len(result.headers)} headers captured, expires in {result.ttl_seconds:.0f}s")
    print(f"Header names: {sorted(result.headers)}")
