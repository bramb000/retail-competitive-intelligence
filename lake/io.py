"""Append-only JSONL + JSON checkpoint helpers for the Ashfield lake.

Secrets policy: request metadata may list header *names*, never token values.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("hybrid_scraper.lake.io")

REPO_ROOT = Path(__file__).resolve().parent.parent
LAKE_ROOT = REPO_ROOT / "lake"
BRONZE_ROOT = LAKE_ROOT / "bronze"
SILVER_ROOT = LAKE_ROOT / "silver"
GOLD_ROOT = LAKE_ROOT / "gold"
REF_ROOT = LAKE_ROOT / "ref"

_SECRET_HEADER_MARKERS = (
    "token",
    "authorization",
    "cookie",
    "sensor",
    "subscription",
    "api-key",
    "apikey",
    "bearer",
    "x-d-token",
    "x-acf",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def bronze_dir(retailer: str, store_id: str, run_id: str) -> Path:
    path = BRONZE_ROOT / retailer.lower() / str(store_id) / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_bronze_dir(retailer: str, store_id: str) -> Optional[Path]:
    parent = BRONZE_ROOT / retailer.lower() / str(store_id)
    if not parent.exists():
        return None
    runs = sorted((p for p in parent.iterdir() if p.is_dir()), reverse=True)
    return runs[0] if runs else None


def checkpoint_path(retailer: str, store_id: str) -> Path:
    parent = BRONZE_ROOT / retailer.lower() / str(store_id)
    parent.mkdir(parents=True, exist_ok=True)
    return parent / "checkpoint.json"


def load_checkpoint(path: Path) -> Dict[str, Any]:
    if not path.exists():
        logger.info("checkpoint miss path=%s", path)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("checkpoint unreadable path=%s error=%s — treating as empty", path, exc)
        return {}
    logger.info("checkpoint loaded path=%s keys=%s", path, sorted(data.keys()))
    return data


def save_checkpoint(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    logger.debug("checkpoint saved path=%s", path)


_LIST_MERGE_KEYS = frozenset({"discovered_ids", "iris_completed_ids"})


def merge_checkpoint(path: Path, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Load, merge, and save — safe for parallel WW search + iris workers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".json.lock")
    import fcntl

    with lock_path.open("w", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        current = load_checkpoint(path)
        merged = dict(current)
        for key, value in patch.items():
            if key in _LIST_MERGE_KEYS and isinstance(value, list):
                existing = {str(x) for x in (merged.get(key) or [])}
                for item in value:
                    existing.add(str(item))
                merged[key] = sorted(existing)
            else:
                merged[key] = value
        if "discovered_ids" in merged:
            merged["discovered_n"] = len(merged["discovered_ids"])
        merged["updated_at"] = utc_now_iso()
        save_checkpoint(path, merged)
        return merged


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")


def iter_jsonl(path: Path):
    if not path.exists():
        logger.info("jsonl missing path=%s", path)
        return
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("jsonl skip path=%s line=%d error=%s", path, line_no, exc)


def header_names_only(headers: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Return header names with values redacted (counts only for secrets)."""
    if not headers:
        return {}
    out: Dict[str, str] = {}
    for name in headers:
        lowered = str(name).lower()
        if any(marker in lowered for marker in _SECRET_HEADER_MARKERS):
            out[str(name)] = "<redacted>"
        else:
            out[str(name)] = "<present>"
    return out
