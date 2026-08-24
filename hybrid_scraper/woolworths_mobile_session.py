"""Woolworths mobile session: guest commerce token + optional emulator capture.

Unlike Coles (opaque `x-d-token` that only the real app can mint), Woolworths'
mobile GraphQL stack accepts a guest Bearer token from:

  POST https://prod.mobile-api.woolworths.com.au/wow/v2/commerce/guest
  headers: x-api-key (static app key from BuildConfig.SHOP_IRIS_API_KEY)
  body: {"device_auth_token": "<uuid>", "postcode": "2131"}

Confirmed live via mitmproxy against the patched app (v26.16.0). An optional
emulator+mitm capture path mirrors Coles for cases where a logged-in token is
preferred; the default proof/enrichment path mints a fresh guest session.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

from curl_cffi.requests import Session

from hybrid_scraper.config import IMPERSONATE_TARGET
from hybrid_scraper.emulator_utils import (
    DEFAULT_AVD,
    DEVICE_SERIAL,
    clear_proxy as _emu_clear_proxy,
    ensure_device_ready,
    install_apk_bundle,
    relaunch_app as _emu_relaunch_app,
    set_ashfield_geo,
    set_proxy as _emu_set_proxy,
    start_emulator,
)
from hybrid_scraper.exceptions import MobileTokenCaptureError, NetworkError
from hybrid_scraper.models import MobileSessionContext

logger = logging.getLogger(__name__)

ADB_PATH = os.environ.get("EMULATOR_ADB_PATH", os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"))
DEVICE_SERIAL = os.environ.get("EMULATOR_DEVICE_SERIAL", "emulator-5554")
APP_PACKAGE = os.environ.get("WOOLWORTHS_APP_PACKAGE", "com.woolworths")
PROXY_HOST = os.environ.get("EMULATOR_PROXY_HOST", "10.0.2.2")


def _proxy_port() -> int:
    return int(os.environ.get("EMULATOR_PROXY_PORT", "8080"))


def _mitm_proxy_url() -> str:
    return os.environ.get("WOOLWORTHS_MITM_PROXY", f"http://127.0.0.1:{_proxy_port()}")

# Static app client key embedded in AU Shop BuildConfig (not a user secret).
SHOP_IRIS_API_KEY = os.environ.get(
    "WOOLWORTHS_APP_API_KEY",
    "ziFxVAagz3kO2H2s3uhLGhhCfPeg0mwQ",
)

GUEST_URL = "https://prod.mobile-api.woolworths.com.au/wow/v2/commerce/guest"
GRAPHQL_URL = "https://prod.mobile-api.woolworths.com.au/hermes/iris/v1/graphql"

_TOOLS_DIR = Path(__file__).resolve().parent.parent / ".tools"
SESSION_CACHE_PATH = _TOOLS_DIR / "woolworths_mobile_session.json"
_CAPTURE_OUT_PATH = _TOOLS_DIR / "woolworths_mobile_capture_result.json"
_HOSTS_LOG_PATH = _TOOLS_DIR / "woolworths_mobile_capture_hosts.log"
_ADDON_PATH = Path(__file__).resolve().parent / "woolworths_mobile_capture_addon.py"
_DEFAULT_APK_DIR = _TOOLS_DIR / "apk" / "woolworths" / "install_ready"

# Apollo persisted-query sha256 from ProductsByProductIdsQuery.OPERATION_ID (app 26.16.0).
PRODUCTS_BY_PRODUCT_IDS_OPERATION_ID = (
    "b2e074307dee63473ca3f9d514aea3769c3476652b542c76fd0c1f0791dc4001"
)


def _base_app_headers() -> Dict[str, str]:
    return {
        "x-api-key": SHOP_IRIS_API_KEY,
        "x-woolies-region": "AU",
        "x-apigee-location": "apigeeEdge",
        "wx-user-timezone": "Australia/Sydney",
        "x-shop-supported-capabilities": (
            "boostsCopyUpdate,directToBootNowShoppingMode,pushNotificationsNZ,tigerNew"
        ),
        "user-agent": "Supers/26.16.0 (AndroidPhone; 37)",
        "content-type": "application/json; charset=UTF-8",
        "accept": "application/json",
    }


def mitm_proxies() -> Optional[Dict[str, str]]:
    """Return curl_cffi proxies dict when local mitmdump is listening, else None."""
    if os.environ.get("WOOLWORTHS_DISABLE_MITM_PROXY", "").strip() in {"1", "true", "yes"}:
        return None
    try:
        import socket

        host_port = _mitm_proxy_url().replace("http://", "").replace("https://", "")
        host, _, port_s = host_port.partition(":")
        port = int(port_s or "8080")
        with socket.create_connection((host or "127.0.0.1", port), timeout=0.4):
            return {"http": _mitm_proxy_url(), "https": _mitm_proxy_url()}
    except OSError:
        return None


def _load_capture_headers() -> Dict[str, str]:
    """Headers from the latest emulator mitm capture (sensor + auth), if present."""
    if not _CAPTURE_OUT_PATH.exists():
        return {}
    try:
        payload = json.loads(_CAPTURE_OUT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    headers = payload.get("headers") or {}
    if not isinstance(headers, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items() if v}


def _merge_sensor_headers(session_headers: Dict[str, str]) -> Dict[str, str]:
    """Attach x-acf-sensor-data (and related) from capture when guest mint lacks them.

    Never replace a freshly minted Authorization — captured Bearers expire quickly
    and caused HTTP 401 when reused with a new guest token's other headers.
    """
    captured = _load_capture_headers()
    if not captured:
        return session_headers
    merged = dict(session_headers)
    for key in (
        "x-acf-sensor-data",
        "x-adobe-ecid",
        "x-tealium-visitor-id",
        "x-dynatrace",
        "accept-language",
    ):
        if key in captured and key not in merged:
            merged[key] = captured[key]
    return merged


def mint_guest_session(postcode: str = "2131") -> MobileSessionContext:
    """Mint a fresh guest Bearer token via commerce/guest (no emulator required)."""
    device_auth_token = str(uuid.uuid4())
    headers = _base_app_headers()
    headers["x-correlation-id"] = str(uuid.uuid4())
    last_error: Optional[Exception] = None
    body: Dict = {}
    proxies = mitm_proxies()
    for attempt in range(1, 4):
        with Session(impersonate=IMPERSONATE_TARGET) as session:
            response = session.post(
                GUEST_URL,
                headers=headers,
                json={"device_auth_token": device_auth_token, "postcode": postcode},
                timeout=30,
                proxies=proxies,
                verify=False if proxies else True,
            )
        if response.status_code < 400:
            body = response.json()
            if body.get("access_token"):
                break
            last_error = NetworkError(f"Woolworths guest mint missing access_token: {list(body)}")
        else:
            last_error = NetworkError(
                f"Woolworths guest mint failed HTTP {response.status_code}: {response.text[:300]}"
            )
            logger.warning("guest mint attempt %d failed: %s", attempt, last_error)
            time.sleep(1.5 * attempt)
            device_auth_token = str(uuid.uuid4())
            headers["x-correlation-id"] = str(uuid.uuid4())
    else:
        raise last_error or NetworkError("Woolworths guest mint failed")

    access_token = body["access_token"]
    session_headers = _merge_sensor_headers(
        {
            **_base_app_headers(),
            "authorization": f"Bearer {access_token}",
            "x-correlation-id": str(uuid.uuid4()),
        }
    )
    created = MobileSessionContext(
        headers=session_headers,
        created_at=time.time(),
        ttl_seconds=min(float(body.get("expires_in") or 3000), 3000.0),
    )
    _TOOLS_DIR.mkdir(exist_ok=True)
    SESSION_CACHE_PATH.write_text(created.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "woolworths guest session minted shopper=%s fulfil_store=%s ttl=%.0fs sensor=%s",
        body.get("shopperid"),
        body.get("fulfilmentstoreid"),
        created.ttl_seconds,
        "x-acf-sensor-data" in session_headers,
    )
    return created


class AndroidEmulatorDevice:
    """Thin adb wrapper (Woolworths package / APK dir)."""

    def __init__(self, adb_path: str = ADB_PATH, serial: str = DEVICE_SERIAL) -> None:
        self._adb_path = adb_path
        self._serial = serial

    def start_emulator(self, avd_name: str = DEFAULT_AVD) -> None:
        start_emulator(avd_name, self._serial)

    def connect(self) -> None:
        ensure_device_ready(self._serial)

    def install_patched_apk(self, apk_dir: Path = _DEFAULT_APK_DIR) -> None:
        install_apk_bundle(self._serial, apk_dir, APP_PACKAGE)

    def set_proxy(self, host: str, port: int) -> None:
        _emu_set_proxy(self._serial, host, port)

    def clear_proxy(self) -> None:
        _emu_clear_proxy(self._serial)

    def relaunch_app(self, package: str = APP_PACKAGE) -> None:
        _emu_relaunch_app(self._serial, package)


class WoolworthsMobileSessionRefresher:
    """Optional mitm capture off the real app; falls back to guest mint."""

    def __init__(self, device: Optional[AndroidEmulatorDevice] = None) -> None:
        self._device = device or AndroidEmulatorDevice()
        _TOOLS_DIR.mkdir(exist_ok=True)

    def _start_mitmdump(self) -> subprocess.Popen:
        for stale in (_CAPTURE_OUT_PATH, _HOSTS_LOG_PATH):
            stale.unlink(missing_ok=True)
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
        with open(_TOOLS_DIR / "woolworths_mitmdump.log", "w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
        time.sleep(2)
        if proc.poll() is not None:
            raise MobileTokenCaptureError(
                f"mitmdump exited immediately (code {proc.returncode}) — see {_TOOLS_DIR / 'woolworths_mitmdump.log'}"
            )
        return proc

    def _stop_mitmdump(self, proc: subprocess.Popen) -> None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    def refresh_from_emulator(self, capture_timeout: float = 60.0) -> MobileSessionContext:
        logger.info("woolworths emulator capture start timeout=%.0fs", capture_timeout)
        from hybrid_scraper.process_lock import emulator_capture_lock

        with emulator_capture_lock(holder="woolworths", timeout=max(capture_timeout + 120.0, 300.0)):
            self._device.start_emulator()
            self._device.connect()
            self._device.install_patched_apk()
            set_ashfield_geo(self._device._serial)
            mitm_proc = self._start_mitmdump()
            try:
                self._device.set_proxy(PROXY_HOST, _proxy_port())
                self._device.relaunch_app()
                deadline = time.monotonic() + capture_timeout
                while time.monotonic() < deadline:
                    if _CAPTURE_OUT_PATH.exists():
                        try:
                            captured = json.loads(_CAPTURE_OUT_PATH.read_text(encoding="utf-8"))
                        except (json.JSONDecodeError, OSError):
                            time.sleep(0.5)
                            continue
                        session = MobileSessionContext(headers=captured["headers"], created_at=time.time())
                        SESSION_CACHE_PATH.write_text(session.model_dump_json(indent=2), encoding="utf-8")
                        logger.info("woolworths emulator capture success headers=%d", len(session.headers))
                        return session
                    time.sleep(0.5)
                raise MobileTokenCaptureError(
                    f"No Woolworths Bearer token captured within {capture_timeout:.0f}s",
                    hosts_seen_log=str(_HOSTS_LOG_PATH),
                )
            finally:
                self._device.clear_proxy()
                self._stop_mitmdump(mitm_proc)

    def get_session(
        self,
        force_refresh: bool = False,
        prefer_emulator: bool = False,
        postcode: str = "2131",
        capture_timeout: float = 60.0,
    ) -> MobileSessionContext:
        if not force_refresh and SESSION_CACHE_PATH.exists():
            try:
                cached = MobileSessionContext.model_validate_json(SESSION_CACHE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                cached = None
            if cached is not None and not cached.is_expired():
                # Refresh sensor from a newer capture without discarding guest auth.
                merged = _merge_sensor_headers(dict(cached.headers))
                if merged != cached.headers:
                    cached = MobileSessionContext(
                        headers=merged,
                        created_at=cached.created_at,
                        ttl_seconds=cached.ttl_seconds,
                    )
                    SESSION_CACHE_PATH.write_text(cached.model_dump_json(indent=2), encoding="utf-8")
                return cached
        # Prefer a fresh capture that already has Akamai sensor + Bearer.
        if not force_refresh:
            captured = _load_capture_headers()
            if captured.get("authorization") and captured.get("x-acf-sensor-data"):
                session = MobileSessionContext(
                    headers={**_base_app_headers(), **captured},
                    created_at=time.time(),
                    ttl_seconds=1800.0,
                )
                SESSION_CACHE_PATH.write_text(session.model_dump_json(indent=2), encoding="utf-8")
                logger.info("woolworths session loaded from mitm capture (sensor present)")
                return session
        if prefer_emulator:
            try:
                self.refresh_from_emulator(capture_timeout=capture_timeout)
                merged = mint_guest_session(postcode=postcode)
                logger.info("woolworths session after emulator capture sensor=%s", "x-acf-sensor-data" in merged.headers)
                return merged
            except MobileTokenCaptureError as exc:
                logger.warning("emulator capture failed (%s); falling back to guest mint", exc)
        return mint_guest_session(postcode=postcode)


def get_woolworths_mobile_session(
    force_refresh: bool = False,
    prefer_emulator: bool = False,
    postcode: str = "2131",
    capture_timeout: float = 90.0,
) -> MobileSessionContext:
    return WoolworthsMobileSessionRefresher().get_session(
        force_refresh=force_refresh,
        prefer_emulator=prefer_emulator,
        postcode=postcode,
        capture_timeout=capture_timeout,
    )


if __name__ == "__main__":
    import argparse

    from hybrid_scraper.logging_config import configure_logging

    parser = argparse.ArgumentParser(description="Mint/capture a Woolworths mobile session")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--emulator", action="store_true", help="Prefer mitm capture from emulator")
    parser.add_argument("--postcode", default="2131")
    args = parser.parse_args()
    configure_logging()
    result = get_woolworths_mobile_session(
        force_refresh=args.force, prefer_emulator=args.emulator, postcode=args.postcode
    )
    print(f"Session ready: {len(result.headers)} headers, expires in {result.ttl_seconds:.0f}s")
    print(f"Header names: {sorted(result.headers)}")
