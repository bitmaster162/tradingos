#!/usr/bin/env python3
"""Download the preregistered public Binance Vision archives and hash them."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


BASE = "https://data.binance.vision/data"
SERIES = (
    ("futures/um/monthly/fundingRate/BTCUSDT", "BTCUSDT-fundingRate-{ym}.zip"),
    ("futures/um/monthly/klines/BTCUSDT/1h", "BTCUSDT-1h-{ym}.zip"),
    ("spot/monthly/klines/BTCUSDT/15m", "BTCUSDT-15m-{ym}.zip"),
    ("spot/monthly/klines/ETHUSDT/15m", "ETHUSDT-15m-{ym}.zip"),
    ("spot/monthly/klines/BTCUSDT/1h", "BTCUSDT-1h-{ym}.zip"),
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def months() -> list[str]:
    return [f"{year}-{month:02d}" for year in (2024, 2025) for month in range(1, 13)]


def download_one(out: Path, folder: str, template: str, ym: str) -> dict[str, object]:
    name = template.format(ym=ym)
    url = f"{BASE}/{folder}/{name}"
    target = out / folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        request = urllib.request.Request(url, headers={"User-Agent": "TradingOS-R57-research"})
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
        target.write_bytes(payload)
    return {
        "source_id": f"binance-vision:{folder}/{name}",
        "url": url,
        "path": target.relative_to(out).as_posix(),
        "bytes": target.stat().st_size,
        "sha256": digest(target),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    jobs = [(folder, template, ym) for folder, template in SERIES for ym in months()]
    records = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(download_one, args.out, folder, template, ym): (folder, ym)
            for folder, template, ym in jobs
        }
        for future in as_completed(futures):
            records.append(future.result())
    records.sort(key=lambda item: str(item["source_id"]))
    manifest = {
        "schema": "TRADINGOS_R57_BINANCE_VISION_SOURCE_MANIFEST_V1",
        "public_read_only": True,
        "period": {"is": "2024", "oos": "2025"},
        "files": records,
    }
    (args.out / "SOURCE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"files": len(records), "bytes": sum(int(x["bytes"]) for x in records)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
