#!/usr/bin/env python3
"""Keep Woolworths Iris GraphQL mitmdump listening on :8083 (pass-through proxy).

Port is hardcoded to 8083. Never inherit EMULATOR_PROXY_PORT — that env is also
used by Coles captures on :8082; inheriting it caused WW mitm to steal Coles' port
and every Coles remint to fail with 'address already in use'.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8083
PIDFILE = ROOT / ".tools" / "ww_mitm.pid"
LOG = ROOT / ".tools" / "woolworths_mitmdump.log"
ADDON = ROOT / "hybrid_scraper" / "woolworths_mobile_capture_addon.py"


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _start() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-c",
        "import sys; from mitmproxy.tools.main import mitmdump; sys.exit(mitmdump(sys.argv[1:]) or 0)",
        "--listen-host",
        "0.0.0.0",
        "--listen-port",
        str(PORT),
        "-s",
        str(ADDON),
        "-q",
    ]
    with LOG.open("w", encoding="utf-8") as handle:
        proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError(f"mitmdump on :{PORT} exited (code {proc.returncode}) — see {LOG}")
    PIDFILE.write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def main() -> int:
    if _listening(PORT):
        print(f"ww mitm already listening on :{PORT}")
        return 0
    pid = _start()
    print(f"ww mitm started pid={pid} port={PORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
