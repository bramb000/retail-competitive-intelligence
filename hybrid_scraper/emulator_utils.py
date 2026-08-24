"""Shared Android emulator helpers for Coles / Woolworths mobile capture."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

ADB_PATH = os.environ.get("EMULATOR_ADB_PATH", os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"))
EMULATOR_PATH = os.path.expanduser("~/Library/Android/sdk/emulator/emulator")
DEVICE_SERIAL = os.environ.get("EMULATOR_DEVICE_SERIAL", "emulator-5554")
DEFAULT_AVD = os.environ.get("EMULATOR_AVD", os.environ.get("COLES_AVD", "WW_Rootable"))

# Ashfield store geo (WW Product Finder / store locator).
ASHFIELD_LAT = -33.8895
ASHFIELD_LON = 151.1250


def is_local_emulator_serial(serial: str) -> bool:
    return serial.startswith("emulator-")


def adb_cmd(serial: str, *args: str, timeout: float = 30.0, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [ADB_PATH, "-s", serial, *args]
    logger.debug("adb %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)


def ensure_device_ready(serial: str = DEVICE_SERIAL) -> None:
    """Wait for a local emulator serial — never `adb connect emulator-5554`."""
    if is_local_emulator_serial(serial):
        subprocess.run([ADB_PATH, "-s", serial, "wait-for-device"], capture_output=True, text=True, timeout=120, check=False)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            res = adb_cmd(serial, "shell", "getprop", "sys.boot_completed", timeout=10, check=False)
            if res.stdout.strip() == "1":
                logger.info("emulator ready serial=%s", serial)
                return
            time.sleep(2)
        raise RuntimeError(f"Emulator {serial} did not finish booting")
    result = subprocess.run([ADB_PATH, "connect", serial], capture_output=True, text=True, timeout=15)
    logger.info("adb connect serial=%s stdout=%r stderr=%r", serial, result.stdout.strip(), result.stderr.strip())
    adb_cmd(serial, "wait-for-device", timeout=120, check=False)


def list_running_emulator_serials() -> List[str]:
    result = subprocess.run([ADB_PATH, "devices"], capture_output=True, text=True, timeout=15)
    serials: List[str] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device" and parts[0].startswith("emulator-"):
            serials.append(parts[0])
    return serials


def start_emulator(avd_name: str = DEFAULT_AVD, serial: str = DEVICE_SERIAL) -> None:
    running = list_running_emulator_serials()
    if serial in running or running:
        active = serial if serial in running else running[0]
        logger.info("emulator already running serial=%s (requested=%s)", active, serial)
        ensure_device_ready(active)
        return

    logger.info("starting emulator avd=%s serial=%s", avd_name, serial)
    subprocess.Popen(
        [EMULATOR_PATH, "-avd", avd_name, "-no-snapshot-load"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ensure_device_ready(serial)


def device_abi(serial: str = DEVICE_SERIAL) -> str:
    res = adb_cmd(serial, "shell", "getprop", "ro.product.cpu.abi", check=False)
    abi = (res.stdout or "").strip() or "arm64-v8a"
    logger.info("device abi serial=%s abi=%s", serial, abi)
    return abi


def app_installed(serial: str, package: str) -> bool:
    res = adb_cmd(serial, "shell", "pm", "path", package, check=False)
    return res.returncode == 0 and bool(res.stdout.strip())


def pick_split_apks(apk_dir: Path, abi: str) -> List[str]:
    """Pick one base + matching ABI (+ optional density) split — no duplicates."""
    if not apk_dir.exists():
        return []

    all_apks = sorted(apk_dir.glob("*.apk"))
    bases = [p for p in all_apks if p.name.startswith("base") and "patched" in p.name and "unsigned" not in p.name]
    if not bases:
        bases = [p for p in all_apks if p.name.startswith("base") and "unsigned" not in p.name]
    if not bases:
        return []
    base = sorted(bases, key=lambda p: p.name)[-1]

    abi_tokens = [abi, abi.replace("-", "_"), abi.replace("-", "")]
    if "arm64" in abi:
        abi_tokens.extend(["arm64-v8a", "arm64_v8a"])
    if "x86_64" in abi:
        abi_tokens.extend(["x86_64", "x86"])

    def pick_split(kind: str) -> Optional[Path]:
        candidates = [
            p
            for p in all_apks
            if p != base and "unsigned" not in p.name and "stripped" not in p.name and ".idsig" not in p.name
        ]
        if kind == "abi":
            matched = [p for p in candidates if "split_config" in p.name and any(tok in p.name for tok in abi_tokens)]
            candidates = matched or [p for p in candidates if "split_config" in p.name and "arm" in p.name or "x86" in p.name]
        elif kind in ("hdpi", "xhdpi", "xxhdpi", "mdpi"):
            matched = [p for p in candidates if kind in p.name and "split_config" in p.name]
            candidates = matched
        # Prefer unsigned/original splits — .signed.apk from bundletool often has stripped certs.
        unsigned = [p for p in candidates if ".signed." not in p.name and not p.name.endswith(".signed.apk")]
        pool = unsigned or candidates
        return sorted(pool, key=lambda p: p.name)[-1] if pool else None

    chosen: List[Path] = [base]
    abi_split = pick_split("abi")
    if abi_split:
        chosen.append(abi_split)
    for density in ("xxhdpi", "xhdpi", "hdpi", "mdpi"):
        dpi_split = pick_split(density)
        if dpi_split and dpi_split not in chosen:
            chosen.append(dpi_split)
            break

    names = [p.name for p in chosen]
    logger.info("picked apks dir=%s abi=%s files=%s", apk_dir, abi, names)
    return [str(p) for p in chosen]


def install_apk_bundle(serial: str, apk_dir: Path, package: str, *, force: bool = False) -> None:
    if not force and app_installed(serial, package):
        logger.info("skip apk install package=%s already on serial=%s", package, serial)
        return
    apks = pick_split_apks(apk_dir, device_abi(serial))
    if not apks:
        logger.warning("no apks to install dir=%s package=%s", apk_dir, package)
        return
    adb_cmd(serial, "uninstall", package, check=False)
    result = adb_cmd(serial, "install-multiple", "-r", *apks, timeout=180, check=False)
    if result.returncode != 0:
        logger.error(
            "install-multiple failed package=%s rc=%d stderr=%s stdout=%s apks=%s",
            package,
            result.returncode,
            (result.stderr or "")[:500],
            (result.stdout or "")[:500],
            [Path(p).name for p in apks],
        )
        raise RuntimeError(f"APK install failed for {package}: {(result.stderr or result.stdout or '')[:300]}")
    logger.info("installed package=%s apks=%d serial=%s", package, len(apks), serial)


def set_ashfield_geo(serial: str = DEVICE_SERIAL) -> None:
    # adb emu geo fix <longitude> <latitude>
    adb_cmd(serial, "emu", "geo", "fix", str(ASHFIELD_LON), str(ASHFIELD_LAT), check=False)
    logger.info("geo fix ashfield serial=%s lat=%s lon=%s", serial, ASHFIELD_LAT, ASHFIELD_LON)


def clear_proxy(serial: str = DEVICE_SERIAL) -> None:
    adb_cmd(serial, "shell", "settings", "put", "global", "http_proxy", ":0", check=False)


def set_proxy(serial: str, host: str, port: int) -> None:
    adb_cmd(serial, "shell", "settings", "put", "global", "http_proxy", f"{host}:{port}")


def relaunch_app(serial: str, package: str) -> None:
    adb_cmd(serial, "shell", "am", "force-stop", package, check=False)
    time.sleep(1)
    # Prefer explicit SplashActivity for Coles — monkey LAUNCHER can no-op when
    # another market app is foregrounded.
    if package == "com.coles.android.shopmate":
        start = adb_cmd(
            serial,
            "shell",
            "am",
            "start",
            "-n",
            "com.coles.android.shopmate/.ui.splash.SplashActivity",
            check=False,
        )
        if start.returncode != 0:
            adb_cmd(
                serial,
                "shell",
                "monkey",
                "-p",
                package,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
                check=False,
            )
    else:
        adb_cmd(
            serial,
            "shell",
            "monkey",
            "-p",
            package,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
            check=False,
        )
    logger.info("relaunched package=%s serial=%s", package, serial)
