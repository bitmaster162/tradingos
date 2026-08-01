#!/usr/bin/env python3
"""Download only the exact preregistered M1 public source archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def date_tokens(start: str, end: str) -> list[str]:
    current = date.fromisoformat(start)
    final = date.fromisoformat(end)
    values = []
    while current <= final:
        values.append(current.isoformat())
        current += timedelta(days=1)
    return values


def expand(plan: dict[str, object]) -> list[dict[str, str]]:
    base = str(plan["base_url"]).rstrip("/")
    jobs: list[dict[str, str]] = []
    for series in plan["series"]:
        tokens = series.get("tokens") or date_tokens(series["start"], series["end"])
        for token in tokens:
            name = str(series["filename_template"]).format(token=token)
            relative = f"{series['folder']}/{name}"
            jobs.append(
                {
                    "source_class": str(series["source_class"]),
                    "archive_granularity": str(series["archive_granularity"]),
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
        partial = target.with_suffix(target.suffix + ".part")
        request = urllib.request.Request(
            job["url"], headers={"User-Agent": "TradingOS-M1-public-research"}
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            partial.write_bytes(response.read())
        partial.replace(target)
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
    parser.add_argument("--workers", type=int, default=8)
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
        "schema": "TRADINGOS_M1_SOURCE_MANIFEST_V1",
        "plan_sha256": digest(args.plan),
        "public_read_only": True,
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
