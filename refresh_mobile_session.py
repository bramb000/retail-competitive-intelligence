"""Standalone entry point: force a fresh Coles mobile-app session capture.

    python refresh_mobile_session.py            # refresh only if the cached one has expired
    python refresh_mobile_session.py --force    # always capture a brand-new one

Scraping scripts don't need to run this explicitly — `scrape_burwood.py`
(and anything else importing `hybrid_scraper.mobile_session.get_mobile_session`)
already captures/refreshes on demand. This is here for manually pre-warming
the cache, or for debugging a capture failure in isolation (see
`hybrid_scraper/mobile_session.py`'s module docstring for the prerequisites
and what a `MobileTokenCaptureError` timeout means).
"""

from __future__ import annotations

if __name__ == "__main__":
    import runpy

    runpy.run_module("hybrid_scraper.mobile_session", run_name="__main__")
