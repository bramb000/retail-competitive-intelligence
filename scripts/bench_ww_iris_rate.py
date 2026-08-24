#!/usr/bin/env python3
"""Benchmark Woolworths Iris productDetailsPage inter-request delays.

Uses already-completed Ashfield Iris IDs (no checkpoint writes). Scores each
candidate delay by effective ok/hour, requiring a high success rate.

Examples:
  .venv/bin/python scripts/bench_ww_iris_rate.py
  .venv/bin/python scripts/bench_ww_iris_rate.py --per-delay 25 --delays 0.5,1,2,3,4,6
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from curl_cffi.requests import AsyncSession  # noqa: E402

from hybrid_scraper.exceptions import AuthExpiredError, NetworkError  # noqa: E402
from hybrid_scraper.woolworths_aisle_enrichment import fetch_product_details  # noqa: E402
from lake.io import load_checkpoint  # noqa: E402

STORE_ID = "1213"
DEFAULT_DELAYS = (0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0)
MIN_SUCCESS_RATE = 0.92


@dataclass
class DelayResult:
    delay_s: float
    attempts: int = 0
    ok: int = 0
    empty: int = 0
    auth: int = 0
    network: int = 0
    other: int = 0
    latencies_ms: List[float] = field(default_factory=list)
    wall_s: float = 0.0

    @property
    def success_rate(self) -> float:
        return (self.ok / self.attempts) if self.attempts else 0.0

    @property
    def effective_ok_per_hour(self) -> float:
        if self.wall_s <= 0:
            return 0.0
        return self.ok * 3600.0 / self.wall_s

    def latency_p50(self) -> Optional[float]:
        if not self.latencies_ms:
            return None
        return float(statistics.median(self.latencies_ms))

    def as_row(self) -> Dict[str, Any]:
        return {
            "delay_s": self.delay_s,
            "attempts": self.attempts,
            "ok": self.ok,
            "empty": self.empty,
            "auth": self.auth,
            "network": self.network,
            "other": self.other,
            "success_rate": round(self.success_rate, 4),
            "ok_per_hour": round(self.effective_ok_per_hour, 1),
            "latency_p50_ms": None if self.latency_p50() is None else round(self.latency_p50(), 1),
            "wall_s": round(self.wall_s, 1),
        }


def _parse_delays(raw: str) -> List[float]:
    out: List[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        val = float(part)
        if val < 0:
            raise ValueError(f"delay must be >= 0, got {val}")
        out.append(val)
    if not out:
        raise ValueError("need at least one delay")
    return out


def _sample_ids(n: int) -> List[str]:
    cp = load_checkpoint(ROOT / "lake" / "bronze" / "woolworths" / STORE_ID / "checkpoint.json")
    done = [str(x) for x in (cp.get("iris_completed_ids") or [])]
    if len(done) < n:
        # Fall back to discovered IDs if Iris history is short.
        done = [str(x) for x in (cp.get("discovered_ids") or [])]
    if not done:
        raise SystemExit("No Woolworths product IDs in checkpoint to probe")
    # Cycle if needed.
    ids: List[str] = []
    while len(ids) < n:
        ids.extend(done)
    return ids[:n]


async def _probe_one(session: AsyncSession, product_id: str) -> tuple[str, float]:
    t0 = time.perf_counter()
    try:
        card = await fetch_product_details(session, STORE_ID, product_id)
        ms = (time.perf_counter() - t0) * 1000.0
        return ("ok" if card else "empty", ms)
    except AuthExpiredError:
        return ("auth", (time.perf_counter() - t0) * 1000.0)
    except NetworkError:
        return ("network", (time.perf_counter() - t0) * 1000.0)
    except Exception:
        return ("other", (time.perf_counter() - t0) * 1000.0)


async def run_delay(
    session: AsyncSession,
    delay_s: float,
    product_ids: Sequence[str],
    cooldown_s: float,
) -> DelayResult:
    if cooldown_s > 0:
        await asyncio.sleep(cooldown_s)
    result = DelayResult(delay_s=delay_s)
    wall0 = time.perf_counter()
    for i, pid in enumerate(product_ids):
        status, ms = await _probe_one(session, pid)
        result.attempts += 1
        result.latencies_ms.append(ms)
        if status == "ok":
            result.ok += 1
        elif status == "empty":
            result.empty += 1
        elif status == "auth":
            result.auth += 1
        elif status == "network":
            result.network += 1
        else:
            result.other += 1
        # Stop early on auth storm — delay is too aggressive or session dead.
        if result.auth >= 3:
            break
        if i + 1 < len(product_ids) and delay_s > 0:
            await asyncio.sleep(delay_s)
    result.wall_s = time.perf_counter() - wall0
    return result


def pick_best(results: Sequence[DelayResult], min_success: float) -> Optional[DelayResult]:
    eligible = [
        r
        for r in results
        if r.attempts >= 5 and r.success_rate >= min_success and r.auth == 0
    ]
    if not eligible:
        # Relax: allow empty cards as non-fatal; still require no auth.
        eligible = [
            r
            for r in results
            if r.attempts >= 5 and r.auth == 0 and ((r.ok + r.empty) / r.attempts) >= min_success
        ]
    if not eligible:
        return None
    top = max(r.effective_ok_per_hour for r in eligible)
    # Prefer a perfect bucket if it stays within 20% of peak throughput.
    perfect = [
        r
        for r in eligible
        if r.success_rate >= 0.999 and r.effective_ok_per_hour >= 0.8 * top
    ]
    pool = perfect or eligible
    return max(pool, key=lambda r: r.effective_ok_per_hour)


def recommend_jitter(best: DelayResult) -> tuple[float, float]:
    """Map fixed delay → min/max jitter window used by the scraper."""
    base = best.delay_s
    # Keep a small jitter band (~±20%, min width 0.4s) so traffic isn't perfectly periodic.
    lo = max(0.1, round(base * 0.85, 2))
    hi = max(round(lo + 0.4, 2), round(base * 1.2, 2))
    return lo, hi


async def main_async(args: argparse.Namespace) -> int:
    delays = _parse_delays(args.delays) if args.delays else list(DEFAULT_DELAYS)
    total_calls = args.per_delay * len(delays)
    ids = _sample_ids(total_calls)
    print(
        f"Iris rate bench store={STORE_ID} per_delay={args.per_delay} "
        f"delays={delays} cooldown={args.cooldown}s ids={len(ids)}"
    )

    results: List[DelayResult] = []
    async with AsyncSession() as session:
        offset = 0
        for delay in delays:
            chunk = ids[offset : offset + args.per_delay]
            offset += args.per_delay
            print(f"\n--- delay={delay}s ({len(chunk)} calls) ---", flush=True)
            result = await run_delay(session, delay, chunk, cooldown_s=args.cooldown)
            results.append(result)
            row = result.as_row()
            print(
                f"ok={row['ok']}/{row['attempts']} success={row['success_rate']:.1%} "
                f"auth={row['auth']} net={row['network']} empty={row['empty']} other={row['other']} "
                f"p50={row['latency_p50_ms']}ms ok/h={row['ok_per_hour']} wall={row['wall_s']}s",
                flush=True,
            )
            if result.auth >= 3:
                print("auth storm — stopping further faster probes would be unsafe; continuing slower ones")

    print("\n=== summary ===")
    for r in results:
        row = r.as_row()
        flag = ""
        print(
            f"delay={row['delay_s']:>5}  ok/h={row['ok_per_hour']:>7}  "
            f"success={row['success_rate']:.1%}  auth={row['auth']}  "
            f"p50_ms={row['latency_p50_ms']}{flag}"
        )

    best = pick_best(results, args.min_success)
    out_path = Path(args.out) if args.out else ROOT / "lake" / "bronze" / "woolworths" / STORE_ID / "iris_rate_bench.json"
    payload: Dict[str, Any] = {
        "store_id": STORE_ID,
        "min_success_rate": args.min_success,
        "results": [r.as_row() for r in results],
        "best": None,
        "recommended_min_delay": None,
        "recommended_max_delay": None,
    }
    if best is None:
        print("\nNo delay met success criteria — keep current conservative defaults.")
    else:
        lo, hi = recommend_jitter(best)
        payload["best"] = best.as_row()
        payload["recommended_min_delay"] = lo
        payload["recommended_max_delay"] = hi
        print(
            f"\nBEST delay={best.delay_s}s → ~{best.effective_ok_per_hour:.0f} ok/h "
            f"(success={best.success_rate:.1%})"
        )
        print(f"Recommended scraper jitter: --min-delay {lo} --max-delay {hi}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-delay", type=int, default=20, help="Calls per candidate delay")
    parser.add_argument(
        "--delays",
        type=str,
        default=None,
        help="Comma-separated fixed delays in seconds (default: 0.25,0.5,1,1.5,2,3,4,6)",
    )
    parser.add_argument("--cooldown", type=float, default=5.0, help="Seconds between delay buckets")
    parser.add_argument("--min-success", type=float, default=MIN_SUCCESS_RATE)
    parser.add_argument("--out", type=str, default=None, help="JSON results path")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
