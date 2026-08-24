"""Module: Android Emulator-driven capture of a fresh Coles mobile-app session.

Mirrors `hybrid_scraper.bootstrapper`'s pattern exactly, one layer down the
stack: that module lets a real (headless) browser solve Akamai/Imperva's
challenge and harvests the resulting cookies/headers; this module lets the
real, already-logged-in Coles Android app (running in Android Emulator) solve its
own device-attestation handshake and harvests the resulting `x-d-token` +
sibling headers via a local mitmproxy instance. Neither module tries to
compute the anti-bot secret itself — both just observe a real client
producing one.

Prerequisites this module assumes are already in place (one-time, manual
setup — see the module-level comment on `Product.aisle_number` in
`models.py` for how this was originally done):
  1. Android Emulator running with the Coles app installed, already logged in.
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
     automatic; it's listed here only because the Android Emulator's NAT gateway
     address (`PROXY_HOST`, default "10.0.2.2") is unverified for setups
     other than this one and may need overriding via env var.

Capture flow per refresh:
  1. Launch `mitmdump` (headless mitmproxy) locally with
     `mobile_capture_addon.py`, listening on `PROXY_PORT`.
  2. Point the Android Emulator device's global HTTP proxy at this host.
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
import threading
import time
from pathlib import Path
from typing import List, Optional

from hybrid_scraper.coles_ui_nav import navigate_for_token_capture
from hybrid_scraper.emulator_utils import (
    DEFAULT_AVD,
    DEVICE_SERIAL,
    adb_cmd,
    app_installed,
    clear_proxy as _emu_clear_proxy,
    ensure_device_ready,
    install_apk_bundle,
    relaunch_app as _emu_relaunch_app,
    set_proxy as _emu_set_proxy,
    start_emulator,
)
from hybrid_scraper.exceptions import MobileTokenCaptureError
from hybrid_scraper.models import MobileSessionContext

logger = logging.getLogger(__name__)

# --- Configuration (env-var overridable; defaults match this project's
# Android Emulator instance on macOS).
ADB_PATH = os.environ.get("EMULATOR_ADB_PATH", os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"))
DEVICE_SERIAL = os.environ.get("EMULATOR_DEVICE_SERIAL", "emulator-5554")
APP_PACKAGE = os.environ.get("COLES_APP_PACKAGE", "com.coles.android.shopmate")
PROXY_HOST = os.environ.get("EMULATOR_PROXY_HOST", "10.0.2.2")


def _proxy_port() -> int:
    return int(os.environ.get("EMULATOR_PROXY_PORT", "8080"))

_TOOLS_DIR = Path(__file__).resolve().parent.parent / ".tools"
_DEFAULT_COLES_APK_DIR = _TOOLS_DIR / "apk" / "coles" / "install_ready"
COLES_APK_DIR = (
    Path(os.environ.get("COLES_APK_DIR", "")).expanduser()
    if os.environ.get("COLES_APK_DIR")
    else (_DEFAULT_COLES_APK_DIR if _DEFAULT_COLES_APK_DIR.exists() else None)
)

SESSION_CACHE_PATH = _TOOLS_DIR / "coles_mobile_session.json"
SESSION_POOL_PATH = _TOOLS_DIR / "coles_mobile_session_pool.json"
_CAPTURE_OUT_PATH = _TOOLS_DIR / "mobile_capture_result.json"
_HOSTS_LOG_PATH = _TOOLS_DIR / "mobile_capture_hosts.log"
_ADDON_PATH = Path(__file__).resolve().parent / "mobile_capture_addon.py"

_POOL_LOCK = threading.Lock()
DEFAULT_POOL_SIZE = 3
# Pause between minting pool tokens so each capture gets a fresh app handshake.
_POOL_MINT_GAP_SECONDS = 8.0


def _load_pool() -> List[MobileSessionContext]:
    if not SESSION_POOL_PATH.exists():
        return []
    try:
        raw = json.loads(SESSION_POOL_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    sessions: List[MobileSessionContext] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            sessions.append(MobileSessionContext.model_validate(item))
        except (ValueError, TypeError):
            continue
    return sessions


def _save_pool(sessions: List[MobileSessionContext]) -> None:
    SESSION_POOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = [s.model_dump() for s in sessions]
    SESSION_POOL_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _cache_session(session: MobileSessionContext) -> None:
    SESSION_CACHE_PATH.write_text(session.model_dump_json(indent=2), encoding="utf-8")


def deposit_session(session: MobileSessionContext, *, max_pool: int = DEFAULT_POOL_SIZE) -> None:
    """Store a freshly minted session as current cache + optional pool entry."""
    _cache_session(session)
    with _POOL_LOCK:
        pool = [s for s in _load_pool() if not s.is_expired()]
        token = session.headers.get("x-d-token") or session.headers.get("X-D-Token")
        pool = [s for s in pool if (s.headers.get("x-d-token") or s.headers.get("X-D-Token")) != token]
        pool.insert(0, session)
        _save_pool(pool[:max_pool])
    logger.info("coles session deposited pool_size=%d ttl=%.0fs", min(len(_load_pool()), max_pool), session.ttl_seconds)


def take_fresh_pooled_session() -> Optional[MobileSessionContext]:
    """Pop the freshest non-expired pooled token (also writes it as active cache)."""
    with _POOL_LOCK:
        pool = _load_pool()
        fresh = [s for s in pool if not s.is_expired() and (s.headers.get("x-d-token") or s.headers.get("X-D-Token"))]
        if not fresh:
            _save_pool([])
            return None
        fresh.sort(key=lambda s: s.created_at, reverse=True)
        chosen = fresh[0]
        rest = [s for s in fresh[1:]]
        _save_pool(rest)
        _cache_session(chosen)
        logger.info(
            "coles session taken from pool remaining=%d age=%.0fs",
            len(rest),
            time.time() - chosen.created_at,
        )
        return chosen


def pool_fresh_count() -> int:
    return sum(1 for s in _load_pool() if not s.is_expired())


class AndroidEmulatorDevice:
    """Thin wrapper around adb for one Android Emulator instance."""

    def __init__(self, adb_path: str = ADB_PATH, serial: str = DEVICE_SERIAL) -> None:
        self._adb_path = adb_path
        self._serial = serial

    def _adb(self, *args: str, timeout: float = 30.0, check: bool = True) -> subprocess.CompletedProcess:
        cmd = [self._adb_path, "-s", self._serial, *args]
        logger.debug("adb command: %s", " ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)

    def start_emulator(self, avd_name: str = DEFAULT_AVD) -> None:
        start_emulator(avd_name, self._serial)

    def install_patched_apk(self, apk_dir: Path) -> None:
        install_apk_bundle(self._serial, apk_dir, APP_PACKAGE)

    def connect(self) -> None:
        ensure_device_ready(self._serial)

    def set_proxy(self, host: str, port: int) -> None:
        _emu_set_proxy(self._serial, host, port)
        logger.debug("Set device global http_proxy=%s:%d", host, port)

    def clear_proxy(self) -> None:
        _emu_clear_proxy(self._serial)
        logger.debug("Cleared device global http_proxy")

    def relaunch_app(self, package: str = APP_PACKAGE) -> None:
        _emu_relaunch_app(self._serial, package)


class MobileSessionRefresher:
    """Drives AndroidEmulatorDevice + a local mitmdump instance to capture a fresh MobileSessionContext."""

    def __init__(self, device: Optional[AndroidEmulatorDevice] = None) -> None:
        self._device = device or AndroidEmulatorDevice()
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
            str(_proxy_port()),
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
        logger.info("Starting mitmdump on 0.0.0.0:%d", _proxy_port())
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

    def refresh(self, capture_timeout: float = 120.0, *, relaunch: bool = True) -> MobileSessionContext:
        """Capture a fresh MobileSessionContext from the real app, and cache it to disk.

        Set relaunch=False when the app is already on a screen that talks to
        apigw (e.g. after manual store setup) so we don't wipe that state.
        """
        logger.info("mobile session refresh start timeout=%.0fs relaunch=%s", capture_timeout, relaunch)

        from hybrid_scraper.process_lock import emulator_capture_lock

        with emulator_capture_lock(holder="coles", timeout=max(capture_timeout + 120.0, 300.0)):
            # Ensure emulator is running and patched app is installed
            self._device.start_emulator()
            self._device.connect()
            apk_dir = COLES_APK_DIR or (_TOOLS_DIR / "apk")
            if not app_installed(self._device._serial, APP_PACKAGE):
                self._device.install_patched_apk(apk_dir)

            mitm_proc = self._start_mitmdump()
            try:
                self._device.set_proxy(PROXY_HOST, _proxy_port())
                if relaunch:
                    self._device.relaunch_app()
                    time.sleep(3.0)
                else:
                    # App already open — settle briefly; navigate_for_token_capture
                    # below will nudge search without requiring a relaunch.
                    time.sleep(1.0)

                deadline = time.monotonic() + capture_timeout
                navigated = False
                while time.monotonic() < deadline:
                    if _CAPTURE_OUT_PATH.exists():
                        try:
                            captured = json.loads(_CAPTURE_OUT_PATH.read_text(encoding="utf-8"))
                        except (json.JSONDecodeError, OSError):
                            time.sleep(0.5)
                            continue
                        headers = captured["headers"]
                        if not any(k.lower() == "x-d-token" for k in headers):
                            time.sleep(0.5)
                            continue
                        logger.info(
                            "mobile session refresh success host=%s path=%s headers=%d",
                            captured.get("host"),
                            captured.get("path"),
                            len(headers),
                        )
                        session = MobileSessionContext(headers=headers, created_at=time.time())
                        deposit_session(session)
                        return session

                    # After a short settle, drive past welcome / set Ashfield store.
                    elapsed = capture_timeout - (deadline - time.monotonic())
                    if not navigated and elapsed >= 5.0:
                        navigated = True
                        try:
                            navigate_for_token_capture(
                                self._device._serial,
                                postcode="2131",
                                suburb_hint="Ashfield",
                                deadline=deadline,
                            )
                        except Exception as exc:  # noqa: BLE001 — navigation is best-effort
                            logger.warning("coles ui navigate failed: %s", exc)

                    time.sleep(0.5)

                hosts_seen = _HOSTS_LOG_PATH.read_text(encoding="utf-8") if _HOSTS_LOG_PATH.exists() else "(none)"
                logger.error(
                    "mobile session refresh timed out after %.0fs — hosts contacted:\n%s",
                    capture_timeout,
                    hosts_seen,
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

    def get_session(
        self,
        force_refresh: bool = False,
        capture_timeout: float = 120.0,
        *,
        cache_only: bool = False,
        allow_stale: bool = False,
    ) -> MobileSessionContext:
        """Return a cached MobileSessionContext if still fresh, else capture a new one.

        When `force_refresh=True` (e.g. after a 403), never silently reuse a
        stale cache — prefer a pooled fresh token, else mint, else raise.
        """
        cached: Optional[MobileSessionContext] = None
        if SESSION_CACHE_PATH.exists():
            try:
                cached = MobileSessionContext.model_validate_json(SESSION_CACHE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                cached = None

        if cached is not None and not cached.is_expired() and not force_refresh:
            logger.debug("mobile session cache hit")
            return cached

        if not force_refresh:
            pooled = take_fresh_pooled_session()
            if pooled is not None:
                return pooled

        if cache_only:
            if cached is not None:
                logger.warning("mobile session cache_only stale=%s", cached.is_expired())
                return cached
            raise MobileTokenCaptureError(
                "No cached Coles mobile session and cache_only=True (install com.coles.android.shopmate or capture once)"
            )

        if force_refresh:
            pooled = take_fresh_pooled_session()
            if pooled is not None:
                logger.info("coles force_refresh satisfied from token pool")
                return pooled

        if not app_installed(self._device._serial, APP_PACKAGE):
            if allow_stale and cached is not None:
                logger.warning(
                    "Coles app not on serial=%s — skipping capture, using stale cache",
                    self._device._serial,
                )
                return cached
            raise MobileTokenCaptureError(
                f"Coles app {APP_PACKAGE} not installed on {self._device._serial} and no session cache"
            )
        try:
            return self.refresh(capture_timeout=capture_timeout)
        except MobileTokenCaptureError:
            if allow_stale and cached is not None and not force_refresh:
                logger.warning(
                    "Coles capture failed — reusing stale cached session (token may 401/403)",
                )
                return cached
            raise

    def mint_pool(self, count: int = DEFAULT_POOL_SIZE, capture_timeout: float = 120.0) -> int:
        """Capture `count` fresh tokens into the on-disk pool. Returns how many were minted."""
        minted = 0
        for i in range(count):
            logger.info("coles pool mint %d/%d", i + 1, count)
            try:
                self.refresh(capture_timeout=capture_timeout)
                minted += 1
            except MobileTokenCaptureError as exc:
                logger.error("coles pool mint failed at %d/%d: %s", i + 1, count, exc)
                break
            if i + 1 < count:
                time.sleep(_POOL_MINT_GAP_SECONDS)
        logger.info("coles pool mint done minted=%d fresh_in_pool=%d", minted, pool_fresh_count())
        return minted


def get_mobile_session(
    force_refresh: bool = False,
    capture_timeout: float = 120.0,
    *,
    cache_only: bool = False,
    allow_stale: bool = False,
) -> MobileSessionContext:
    """Convenience entry point for scraping scripts: cache-first, refresh on demand.

    Usage in a scraping script, replacing a manually-exported env var:
        from hybrid_scraper.mobile_session import get_mobile_session
        session = get_mobile_session()
        headers = session.headers
    Pass `force_refresh=True` after a 401/403 from apigw.coles.com.au to
    force a brand-new capture rather than reusing the (evidently expired)
    cached one.
    """
    return MobileSessionRefresher().get_session(
        force_refresh=force_refresh,
        capture_timeout=capture_timeout,
        cache_only=cache_only,
        allow_stale=allow_stale,
    )


def mint_token_pool(count: int = DEFAULT_POOL_SIZE, capture_timeout: float = 120.0) -> int:
    """Public helper: pre-mint several Coles tokens for later rotation."""
    return MobileSessionRefresher().mint_pool(count=count, capture_timeout=capture_timeout)


if __name__ == "__main__":
    import argparse

    from hybrid_scraper.logging_config import configure_logging

    parser = argparse.ArgumentParser(description="Capture a fresh Coles mobile-app session token via Android Emulator")
    parser.add_argument(
        "--force", action="store_true", help="Force a fresh capture even if a cached session is still valid"
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="Seconds to wait for a capture before giving up")
    parser.add_argument(
        "--pool",
        type=int,
        default=0,
        metavar="N",
        help="Mint N fresh tokens into the on-disk pool (also updates the active cache)",
    )
    parser.add_argument(
        "--no-relaunch",
        action="store_true",
        help="Do not force-stop the Coles app (use when already on a shop screen)",
    )
    args = parser.parse_args()

    configure_logging()
    if args.no_relaunch:
        session = MobileSessionRefresher().refresh(capture_timeout=args.timeout, relaunch=False)
        print(f"Session ready: {len(session.headers)} headers, expires in {session.ttl_seconds:.0f}s")
        print(f"Header names: {sorted(session.headers)}")
        print(f"Pool fresh tokens: {pool_fresh_count()}")
        raise SystemExit(0)
    if args.pool > 0:
        n = mint_token_pool(count=args.pool, capture_timeout=args.timeout)
        print(f"Pool mint complete: {n}/{args.pool} tokens; fresh_in_pool={pool_fresh_count()}")
        raise SystemExit(0 if n > 0 else 1)
    result = get_mobile_session(force_refresh=args.force, capture_timeout=args.timeout)
    print(f"Session ready: {len(result.headers)} headers captured, expires in {result.ttl_seconds:.0f}s")
    print(f"Header names: {sorted(result.headers)}")
    print(f"Pool fresh tokens: {pool_fresh_count()}")
