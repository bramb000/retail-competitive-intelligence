"""Drive the Coles Android app past welcome/login into store browsing.

Needed so mitm can observe an `x-d-token`-bearing request to apigw.coles.com.au.
The app often sits on "Welcome / Browse for now" and never hits that endpoint
unless we tap through store selection.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Tuple

from hybrid_scraper.emulator_utils import ADB_PATH, DEVICE_SERIAL, adb_cmd

logger = logging.getLogger(__name__)

_UI_DUMP = Path("/sdcard/ui_coles_nav.xml")
_LOCAL_DUMP = Path("/tmp/coles_ui_nav.xml")


def _dump_ui(serial: str = DEVICE_SERIAL) -> str:
    adb_cmd(serial, "shell", "uiautomator", "dump", str(_UI_DUMP), check=False)
    subprocess.run(
        [ADB_PATH, "-s", serial, "pull", str(_UI_DUMP), str(_LOCAL_DUMP)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if not _LOCAL_DUMP.exists():
        return ""
    return _LOCAL_DUMP.read_text(encoding="utf-8", errors="replace")


def _texts(xml: str) -> List[str]:
    return [t for t in re.findall(r'text="([^"]*)"', xml) if t.strip()]


def _find_bounds(xml: str, label: str, *, exact: bool = False) -> Optional[Tuple[int, int]]:
    if not xml:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    needle = label.lower()
    for node in root.iter("node"):
        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        for candidate in (text, desc):
            if not candidate:
                continue
            hit = candidate.lower() == needle if exact else needle in candidate.lower()
            if hit:
                bounds = node.attrib.get("bounds") or ""
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
                if m:
                    x1, y1, x2, y2 = map(int, m.groups())
                    return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def _tap(serial: str, x: int, y: int) -> None:
    adb_cmd(serial, "shell", "input", "tap", str(x), str(y), check=False)


def _tap_label(serial: str, label: str, *, exact: bool = False) -> bool:
    xml = _dump_ui(serial)
    point = _find_bounds(xml, label, exact=exact)
    if not point:
        return False
    _tap(serial, *point)
    logger.info("coles ui tap label=%r at=%s", label, point)
    time.sleep(2.0)
    return True


def _type_text(serial: str, text: str) -> None:
    # Spaces must be %s for `adb shell input text`
    escaped = text.replace(" ", "%s").replace("'", "\\'")
    adb_cmd(serial, "shell", "input", "text", escaped, check=False)
    time.sleep(1.0)


def _clear_focused_field(serial: str, presses: int = 48) -> None:
    """Delete junk left in a focused text field (MOVE_END then DEL)."""
    adb_cmd(serial, "shell", "input", "keyevent", "123", check=False)  # MOVE_END
    for _ in range(presses):
        adb_cmd(serial, "shell", "input", "keyevent", "67", check=False)  # DEL


def _nudge_search(serial: str) -> None:
    """Open search and query milk so apigw traffic is more likely."""
    for label in ("Search for products", "Search"):
        if _tap_label(serial, label):
            time.sleep(1.0)
            _type_text(serial, "milk")
            adb_cmd(serial, "shell", "input", "keyevent", "66", check=False)
            time.sleep(4.0)
            return
    # Fallback: tap common search bar coordinates on phone-sized emulators.
    _tap(serial, 540, 280)
    time.sleep(1.0)
    _type_text(serial, "milk")
    adb_cmd(serial, "shell", "input", "keyevent", "66", check=False)
    time.sleep(4.0)


def _looks_like_main_shop(blob: str) -> bool:
    """True when bottom nav / home chrome is visible (not welcome/store picker)."""
    bottom_nav = all(k in blob for k in ("home", "lists", "products", "trolley"))
    if bottom_nav:
        return True
    return any(
        k in blob
        for k in (
            "search for products",
            "product locator",
            "my shopping list",
            "my list",
            "in store",
            "in-store",
            "flybuys",
        )
    )


def navigate_for_token_capture(
    serial: str = DEVICE_SERIAL,
    *,
    postcode: str = "2131",
    suburb_hint: str = "Ashfield",
    deadline: Optional[float] = None,
) -> None:
    """Best-effort taps to reach a screen that triggers apigw traffic.

    Safe to call repeatedly; no-ops when already past welcome.
    """
    logger.info("coles ui navigate start serial=%s postcode=%s", serial, postcode)
    steps = 0
    while deadline is None or time.monotonic() < deadline:
        steps += 1
        if steps > 25:
            logger.warning("coles ui navigate giving up after %d steps", steps)
            return
        xml = _dump_ui(serial)
        texts = _texts(xml)
        blob = " | ".join(texts).lower()
        logger.info("coles ui texts=%s", texts[:20])

        on_welcome = any(
            k in blob
            for k in (
                "welcome to coles",
                "sign up or log in",
                "browse for now",
                "find your nearest coles",
                "enter postcode",
            )
        )
        on_main = (not on_welcome) and _looks_like_main_shop(blob)
        if on_main:
            # Dismiss one-shot list/onboarding sheets before searching.
            for dismiss in ("Got it", "Not now", "Skip", "Close", "Continue"):
                if _tap_label(serial, dismiss, exact=True):
                    time.sleep(1.5)
                    break
            else:
                # Promo sheets sometimes only expose a content-desc "OK" that is
                # not a real dismiss — prefer BACK once, then search.
                if "looking lonely" in blob or "lists just got better" in blob:
                    adb_cmd(serial, "shell", "input", "keyevent", "4", check=False)
                    time.sleep(1.5)
            logger.info("coles ui looks like main shop — nudging search")
            _nudge_search(serial)
            return

        dismissed = False
        for label in (
            "Browse for now",
            "While using the app",
            "Only this time",
            "Allow",
            "Got it",
            "Not now",
            "Skip",
            "Continue",
            "Close",
        ):
            if _tap_label(serial, label):
                dismissed = True
                break
        if dismissed:
            continue

        # Postcode / suburb picker
        if "postcode" in blob or "suburb" in blob or "nearest coles" in blob:
            if _tap_label(serial, "Your current location"):
                time.sleep(5.0)
                continue
            field = _find_bounds(xml, "Enter postcode") or _find_bounds(xml, "postcode/suburb")
            if field:
                _tap(serial, *field)
                time.sleep(0.4)
                _clear_focused_field(serial)
                # Prefer suburb name — postcode alone often appends/fails in this app.
                _type_text(serial, suburb_hint)
                time.sleep(2.5)
                if _tap_label(serial, suburb_hint) or _tap_label(serial, "NSW"):
                    time.sleep(4.0)
                    continue
                # Fallback: clear again and try postcode once.
                _tap(serial, *field)
                time.sleep(0.3)
                _clear_focused_field(serial)
                _type_text(serial, postcode)
                time.sleep(2.5)
                if _tap_label(serial, postcode) or _tap_label(serial, "NSW"):
                    time.sleep(4.0)
                    continue
                _tap(serial, 540, 700)
                time.sleep(4.0)
                continue
            time.sleep(1.5)
            continue

        # Already shopping — try opening search to force API traffic
        if not on_welcome:
            logger.info("coles ui unknown non-welcome screen — nudging search")
            _nudge_search(serial)
            return

        time.sleep(1.5)

    logger.warning("coles ui navigate hit deadline without confirming main shop")
