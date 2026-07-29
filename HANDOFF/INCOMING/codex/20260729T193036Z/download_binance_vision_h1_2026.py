#!/usr/bin/env python3
"""Download only the exact preregistered R59 public source archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def expand(plan: dict[str, object]) -> list[dict[str, str]]:
    base = str(plan["base_url"]).rstrip("/")
    jobs: list[dict[str, str]] = []
    for series in plan["series"]:
        for month in plan["months"]:
            name = str(series["filename_template"]).format(ym=month)
            folder = str(series["folder"])
            relative = f"{folder}/{name}"
            jobs.append(
                {
                    "hypothesis": str(series["hypothesis"]),
                    "source_id": f"binance-vision:{relative}",
                    "url": f"{base}/{relative}",
                    "path": relative,
                }
            )
    if len(jobs) != int(plan["expected_file_count"]):
        raise ValueError("expanded source count differs from frozen plan")
    if len({item["source_id"] for item in jobs}) != len(jobs):
        raise ValueError("frozen source plan contains duplicates")
    return sorted(jobs, key=lambda item: item["source_id"])


def download_one(out: Path, job: dict[str, str]) -> dict[str, object]:
    target = out / job["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        request = urllib.request.Request(
            job["url"], headers={"User-Agent": "TradingOS-R59-public-research"}
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = response.read()
        target.write_bytes(payload)
    record: dict[str, object] = dict(job)
    record.update(
        {
            "bytes": target.stat().st_size,
            "sha256": digest(target),
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    records: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(download_one, args.out, job): job for job in expand(plan)
        }
        for future in as_completed(futures):
            records.append(future.result())
    records.sort(key=lambda item: str(item["source_id"]))
    manifest = {
        "schema": "TRADINGOS_R59_BINANCE_VISION_SOURCE_MANIFEST_V1",
        "plan_sha256": digest(args.plan),
        "public_read_only": True,
        "period": "2026-H1",
        "files": records,
    }
    (args.out / "SOURCE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "files": len(records),
                "bytes": sum(int(item["bytes"]) for item in records),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
