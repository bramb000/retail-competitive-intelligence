"""mitmproxy addon: capture Woolworths mobile-app auth headers for session cache.

Driven by `hybrid_scraper.woolworths_mobile_session` the same way Coles uses
`mobile_capture_addon.py`. Prefers a Bearer Authorization on
`prod.mobile-api.woolworths.com.au` (guest or logged-in commerce token).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Set

from mitmproxy import ctx

_CAPTURE_HEADER_NAMES = {
    "authorization",
    "x-api-key",
    "x-acf-sensor-data",  # Akamai Bot Manager — required for Iris GraphQL (else CDN returns a poisoned HIT)
    "x-woolies-region",
    "x-apigee-location",
    "wx-user-timezone",
    "x-shop-supported-capabilities",
    "x-adobe-ecid",
    "user-agent",
    "accept-language",
    "x-correlation-id",
    "x-tealium-visitor-id",
    "x-dynatrace",
}

_TARGET_HOST_MARKERS = (
    "mobile-api.woolworths.com.au",
    "prod-apix.woolworths.com.au",
)

_seen_hosts: Set[str] = set()


def load(loader) -> None:
    loader.add_option(
        name="capture_out", typespec=str, default="", help="Path to write captured headers as JSON once seen"
    )
    loader.add_option(
        name="capture_hosts_log",
        typespec=str,
        default="",
        help="Path to append every distinct host contacted",
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
    auth = captured.get("authorization") or ""
    # Prefer GraphQL-shaped requests that carry Akamai sensor data; fall back to
    # any Bearer+api-key pair (guest mint path) if sensor is absent.
    if not auth.lower().startswith("bearer ") or not captured.get("x-api-key"):
        return
    path = flow.request.path or ""
    if "graphql" in path and not captured.get("x-acf-sensor-data"):
        return

    out_path = ctx.options.capture_out
    if not out_path:
        return

    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "host": host,
                "path": flow.request.path,
                "headers": captured,
                "captured_at": time.time(),
            },
            f,
            indent=2,
        )
    Path(tmp_path).replace(out_path)
    ctx.log.info(
        f"hybrid_scraper: captured Woolworths headers {sorted(captured)} "
        f"off {host}{flow.request.path}"
    )
