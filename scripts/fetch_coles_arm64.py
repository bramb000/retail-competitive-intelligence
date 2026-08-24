#!/usr/bin/env python3
"""Pull arm64 Coles APK splits from the emulator after Play/Aurora install.

Usage (after Coles is installed on emulator-5554):
  .venv/bin/python scripts/fetch_coles_arm64.py
  ./scripts/build_coles_install_ready.sh
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"
SRC = TOOLS / "apk" / "coles" / "source"
ADB = Path.home() / "Library/Android/sdk/platform-tools/adb"
SERIAL = "emulator-5554"
PACKAGE = "com.coles.android.shopmate"


def adb(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ADB), "-s", SERIAL, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    if not ADB.exists():
        print(f"adb not found: {ADB}", file=sys.stderr)
        return 1

    paths = adb("shell", "pm", "path", PACKAGE)
    if paths.returncode != 0 or not paths.stdout.strip():
        print(
            f"{PACKAGE} not installed on {SERIAL}. Install via Aurora Store first:\n"
            "  market://details?id=com.coles.android.shopmate",
            file=sys.stderr,
        )
        return 1

    SRC.mkdir(parents=True, exist_ok=True)
    pulled: list[Path] = []
    for line in paths.stdout.splitlines():
        m = re.match(r"package:(.+)", line.strip())
        if not m:
            continue
        remote = m.group(1)
        name = Path(remote).name
        if "split_" in name:
            name = name.split("==")[-1] if "==" in name else name
        local = SRC / name
        print(f"pull {remote} -> {local}")
        r = adb("pull", remote, str(local))
        if r.returncode != 0:
            print(r.stderr or r.stdout, file=sys.stderr)
            return 1
        pulled.append(local)

    # Also copy existing patched base if present
    patched = ROOT / ".tools/apk/base-patched-signed3.apk"
    if patched.exists():
        dest = SRC / "base-patched-signed3.apk"
        if not dest.exists() or dest.stat().st_mtime < patched.stat().st_mtime:
            dest.write_bytes(patched.read_bytes())
            print(f"copied patched base -> {dest}")

    print(f"pulled {len(pulled)} split(s) to {SRC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
