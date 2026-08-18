"""mitmproxy addon: captures the Coles mobile app's device-attestation headers.

Not meant to be run standalone — `hybrid_scraper.mobile_session` drives this
as a `mitmdump -s mobile_capture_addon.py` subprocess, passing its two
options (`capture_out`, `capture_hosts_log`) via `--set`.

Why this *captures* rather than computes `x-d-token`: it's an opaque,
encrypted device-attestation blob (see `models.py`'s `Product.aisle_number`
comment) that only the real app's own native code can produce — the same
reason `hybrid_scraper.bootstrapper` listens on outgoing browser requests
for the website's anti-bot cookies instead of trying to reconstruct them.
This addon does the mobile-app equivalent: let the real, already-logged-in
app do the device-attestation handshake with apigw.coles.com.au, and lift
the resulting headers off the (locally decrypted, thanks to the app's
already-patched cert pinning) HTTPS request.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Set

from mitmproxy import ctx

# Case-insensitive header names worth pulling off a real request — enough to
# reconstruct the exact request shape scrape_burwood.py already replays.
_CAPTURE_HEADER_NAMES = {
    "x-d-token",
    "ocp-apim-subscription-key",
    "client",
    "x-app-version",
    "x-device-model",
    "x-device-id",
    "x-client-os",
    "authorization",
    "accept-language",
    "user-agent",
}

# Matches the app backend host confirmed live (apigw.coles.com.au) plus the
# main site domain, in case a future app version moves the endpoint.
_TARGET_HOST_MARKERS = ("coles.com.au", "coles.opapi.au")

_seen_hosts: Set[str] = set()


def load(loader) -> None:
    loader.add_option(
        name="capture_out", typespec=str, default="", help="Path to write captured headers as JSON once seen"
    )
    loader.add_option(
        name="capture_hosts_log",
        typespec=str,
        default="",
        help="Path to append every distinct host contacted (diagnostics for when capture_out never appears)",
    )


def request(flow) -> None:
    host = flow.request.pretty_host
    if host not in _seen_hosts:
        _seen_hosts.add(host)
        hosts_log = ctx.options.capture_hosts_log
        if hosts_log:
            with open(hosts_log, "a", encoding="utf-8") as f:
                f.write(f"{time.time()} {host} {flow.request.path}\n")

    if not any(marker in host for marker in _TARGET_HOST_MARKERS):
        return

    headers = {k.lower(): v for k, v in flow.request.headers.items()}
    captured = {name: headers[name] for name in _CAPTURE_HEADER_NAMES if name in headers}
    if not captured.get("x-d-token"):
        return  # only the device-attestation call is useful; every other Coles request is ignored

    out_path = ctx.options.capture_out
    if not out_path:
        return

    # Write-then-rename so hybrid_scraper.mobile_session's poller (running in
    # a separate process) never reads a half-written file.
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(
            {"host": host, "path": flow.request.path, "headers": captured, "captured_at": time.time()},
            f,
            indent=2,
        )
    Path(tmp_path).replace(out_path)
    # Log which header NAMES were captured, never values — same secrets
    # policy as bootstrapper.py (this log file is the first thing handed
    # over when asking for debugging help).
    ctx.log.info(f"hybrid_scraper: captured {sorted(captured)} off a live request to {host}{flow.request.path}")
