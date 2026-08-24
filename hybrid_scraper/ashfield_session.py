"""One-shot Ashfield session warmup for scrape_ashfield_deep.py."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, List, Sequence

from hybrid_scraper.emulator_utils import (
    DEFAULT_AVD,
    DEVICE_SERIAL,
    app_installed,
    ensure_device_ready,
    install_apk_bundle,
    set_ashfield_geo,
    start_emulator,
)
from hybrid_scraper.exceptions import MobileTokenCaptureError
from hybrid_scraper.mobile_session import APP_PACKAGE, get_mobile_session
from hybrid_scraper.woolworths_mobile_session import get_woolworths_mobile_session, mitm_proxies

logger = logging.getLogger("hybrid_scraper.ashfield_session")

_COLES_INSTALL_DIR = Path(__file__).resolve().parent.parent / ".tools" / "apk" / "coles" / "install_ready"


def _needs_coles(banners: Sequence[str]) -> bool:
    return any(b in ("coles", "both") for b in banners)


def _needs_woolworths(banners: Sequence[str]) -> bool:
    return any(b in ("woolworths", "both") for b in banners)


def warmup_ashfield_sessions(
    banners: Iterable[str],
    *,
    force: bool = False,
    capture_timeout: float = 90.0,
) -> None:
    """Boot emulator (if needed), capture mobile sessions, set Ashfield geo.

    Called once at the start of scrape_ashfield_deep so the user runs a single
    command. Skips Coles emulator when COLES_APP_* env overrides are set.
    """
    banner_list = list(banners)
    if not banner_list or banner_list == ["etl"]:
        return

    needs_coles = _needs_coles(banner_list)
    needs_ww = _needs_woolworths(banner_list)
    if not needs_coles and not needs_ww:
        return

    logger.info(
        "warmup start banners=%s force=%s avd=%s serial=%s",
        banner_list,
        force,
        DEFAULT_AVD,
        DEVICE_SERIAL,
    )

    if needs_coles or needs_ww:
        start_emulator(DEFAULT_AVD, DEVICE_SERIAL)
        ensure_device_ready(DEVICE_SERIAL)
        if needs_ww:
            set_ashfield_geo(DEVICE_SERIAL)

    if needs_coles:
        if os.environ.get("COLES_APP_SUBSCRIPTION_KEY") and os.environ.get("COLES_APP_X_D_TOKEN"):
            logger.info("coles warmup using COLES_APP_* env overrides (no emulator capture)")
        elif not app_installed(DEVICE_SERIAL, APP_PACKAGE):
            if _COLES_INSTALL_DIR.exists():
                logger.info("coles app missing — installing from %s", _COLES_INSTALL_DIR)
                install_apk_bundle(DEVICE_SERIAL, _COLES_INSTALL_DIR, APP_PACKAGE)
            else:
                logger.warning(
                    "coles app missing on serial=%s — using cached mobile session (no install/capture)",
                    DEVICE_SERIAL,
                )
                session = get_mobile_session(
                    force_refresh=False, capture_timeout=capture_timeout, cache_only=True, allow_stale=True
                )
                logger.info("coles warmup cache-only headers=%d", len(session.headers))
                return
        if app_installed(DEVICE_SERIAL, APP_PACKAGE):
            logger.info("coles warmup capture start timeout=%.0fs", capture_timeout)
            try:
                session = get_mobile_session(force_refresh=True, capture_timeout=max(capture_timeout, 180.0), allow_stale=False)
            except MobileTokenCaptureError as exc:
                logger.warning(
                    "coles warmup capture failed (%s) — trying pool / cached session if present",
                    exc,
                )
                session = get_mobile_session(
                    force_refresh=False, capture_timeout=capture_timeout, cache_only=True, allow_stale=True
                )
            has_token = "x-d-token" in {k.lower() for k in session.headers}
            logger.info("coles warmup done headers=%d has_x_d_token=%s", len(session.headers), has_token)
            if not has_token:
                raise MobileTokenCaptureError(
                    "Coles warmup captured headers but no x-d-token — open in-store/wayfinding in the app "
                    "or check mitm CA / patched APK."
                )
        else:
            logger.warning("coles app still missing after install attempt on serial=%s", DEVICE_SERIAL)

    if needs_ww:
        logger.info("woolworths warmup start prefer_emulator=True")
        session = get_woolworths_mobile_session(
            force_refresh=force,
            prefer_emulator=True,
            postcode="2131",
        )
        has_sensor = "x-acf-sensor-data" in {k.lower() for k in session.headers}
        has_auth = any(k.lower() == "authorization" for k in session.headers)
        logger.info(
            "woolworths warmup done headers=%d has_sensor=%s has_auth=%s mitm_proxy=%s",
            len(session.headers),
            has_sensor,
            has_auth,
            mitm_proxies() is not None,
        )
        if not has_sensor:
            logger.warning(
                "woolworths warmup: no x-acf-sensor-data — Iris price/placement may fail; "
                "check Frida unpin + mitm on serial=%s",
                DEVICE_SERIAL,
            )

    logger.info("warmup complete banners=%s", banner_list)
