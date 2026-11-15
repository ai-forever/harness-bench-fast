#!/usr/bin/env python3
"""Sync Cloud.ru Foundation Models catalog prices into a local cache JSON.

Fetches ``GET https://foundation-models.api.cloud.ru/v1/models`` with
``Authorization: Bearer $CLOUD_RU_API_KEY`` and writes a cache shaped:

    {
      "fetched_at": "<ISO8601 UTC>",
      "models": {
        "<full model id>": {
          "input": <float>, "output": <float>,
          "cache_read": <float>, "is_billable": <bool>
        }
      }
    }

On network/HTTP failure (or an empty catalog) the previous cache file is left
untouched and the script exits non-zero -- it never clobbers a good cache.

Usage:
    python3 scripts/sync_cloudru_prices.py [--out PATH] [--ttl SECONDS]
                                           [--force] [--url URL]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_OUT = Path.home() / ".openclaw" / "cloudru-prices.json"
DEFAULT_TTL = 24 * 3600  # seconds
DEFAULT_URL = "https://foundation-models.api.cloud.ru/v1/models"


def api_key() -> str | None:
    """Return the Cloud.ru API key from the environment, if set."""
    return (
        os.environ.get("CLOUD_RU_API_KEY")
        or os.environ.get("CLOUD_RU_FOUNDATION_MODELS_API_KEY")
    )


def parse_catalog(payload: dict) -> dict:
    """Return ``{model_id: {input, output, cache_read, is_billable}}``.

    Reads ``data[].metadata.{prompt_tokens_cost, generated_tokens_cost,
    cache_read_tokens_cost, is_billable}``. Missing/None cost fields become 0.
    """
    models: dict = {}
    entries = payload.get("data") or payload.get("models") or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not model_id:
            continue
        meta = entry.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        models[str(model_id)] = {
            "input": float(meta.get("prompt_tokens_cost") or 0.0),
            "output": float(meta.get("generated_tokens_cost") or 0.0),
            "cache_read": float(meta.get("cache_read_tokens_cost") or 0.0),
            "is_billable": bool(meta.get("is_billable", False)),
        }
    return models


def fetch_catalog(api_key_value: str, url: str = DEFAULT_URL, timeout: float = 60.0) -> dict:
    """GET the catalog JSON; raises on HTTP/network failure."""
    import httpx

    headers = {
        "Authorization": f"Bearer {api_key_value}",
        "Accept": "application/json",
    }
    with httpx.Client(verify=True, timeout=timeout) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _atomic_write(path: Path, payload: dict) -> None:
    """Write *payload* atomically: temp file in same dir, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".cloudru-prices-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def cache_age_seconds(path: Path, now: datetime | None = None) -> float | None:
    """Return age of the cache in seconds, or None if unreadable/undated."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    fetched_at = data.get("fetched_at")
    if not fetched_at:
        return None
    try:
        ts = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return (now - ts).total_seconds()


def cache_is_fresh(path: Path, ttl: int, now: datetime | None = None) -> bool:
    age = cache_age_seconds(path, now=now)
    return age is not None and age < ttl


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync Cloud.ru Foundation Models prices to a local cache JSON."
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help=f"cache output path (default: {DEFAULT_OUT})")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL,
                        help="cache TTL in seconds; a fresher cache skips the fetch")
    parser.add_argument("--force", action="store_true",
                        help="ignore TTL and always refetch")
    parser.add_argument("--url", default=DEFAULT_URL, help="catalog endpoint")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout (s)")
    args = parser.parse_args(argv)

    out = Path(args.out).expanduser()

    if not args.force and cache_is_fresh(out, args.ttl):
        print(f"[sync_cloudru_prices] cache is fresh (<{args.ttl}s), skipped: {out}")
        return 0

    key = api_key()
    if not key:
        print("[sync_cloudru_prices] ERROR: CLOUD_RU_API_KEY (or "
              "CLOUD_RU_FOUNDATION_MODELS_API_KEY) not set; keeping previous cache.",
              file=sys.stderr)
        return 2

    try:
        payload = fetch_catalog(key, url=args.url, timeout=args.timeout)
    except Exception as exc:  # noqa: BLE001 - any failure must not clobber the cache
        print(f"[sync_cloudru_prices] ERROR: catalog fetch failed: {exc}. "
              f"Keeping previous cache at {out}.", file=sys.stderr)
        return 1

    models = parse_catalog(payload)
    if not models:
        print("[sync_cloudru_prices] ERROR: catalog returned no models. "
              f"Keeping previous cache at {out}.", file=sys.stderr)
        return 1

    cache = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "models": models,
    }
    try:
        _atomic_write(out, cache)
    except Exception as exc:  # noqa: BLE001
        print(f"[sync_cloudru_prices] ERROR: could not write cache {out}: {exc}",
              file=sys.stderr)
        return 1

    billable = sum(1 for m in models.values() if m["is_billable"])
    print(f"[sync_cloudru_prices] wrote {len(models)} models "
          f"({billable} billable) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
