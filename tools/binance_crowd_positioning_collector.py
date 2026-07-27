#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


BASE_URL = "https://fapi.binance.com"
DEFAULT_CACHE_DIR = ROOT / "data" / "cache" / "binance_spot_perp_extended"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "BINANCE_CROWD_POSITIONING_COLLECTOR_2026-06-19"

ENDPOINTS = {
    "global": "/futures/data/globalLongShortAccountRatio",
    "top_account": "/futures/data/topLongShortAccountRatio",
    "top_position": "/futures/data/topLongShortPositionRatio",
}

FIELDNAMES = [
    "time",
    "timestamp",
    "global_long_account",
    "global_short_account",
    "global_long_short_ratio",
    "top_account_long_account",
    "top_account_short_account",
    "top_account_long_short_ratio",
    "top_position_long_account",
    "top_position_short_account",
    "top_position_long_short_ratio",
    "source",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ms_to_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()


def parse_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def format_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.12g}"


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDNAMES})
    temp_path.replace(path)


def row_range(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "first": None, "last": None}
    ordered = sorted(rows, key=lambda row: int(row.get("timestamp") or 0))
    return {
        "rows": len(ordered),
        "first": ordered[0].get("time"),
        "last": ordered[-1].get("time"),
    }


def fetch_ratio_page(endpoint: str, symbol: str, period: str, limit: int, end_time: int | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"symbol": symbol.upper(), "period": period, "limit": min(max(limit, 1), 500)}
    if end_time is not None:
        params["endTime"] = end_time
    url = f"{BASE_URL}{endpoint}?{urlencode(params)}"
    with urlopen(url, timeout=30) as response:  # noqa: S310 - fixed public Binance endpoint.
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"unexpected Binance payload for {endpoint}: {payload!r}")
    return payload


def fetch_ratio_history(endpoint: str, symbol: str, period: str, limit: int, pages: int) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    end_time: int | None = None
    for _ in range(max(1, pages)):
        page = fetch_ratio_page(endpoint, symbol, period, limit, end_time)
        if not page:
            break
        payload = page + payload
        first_ts = int(page[0]["timestamp"])
        next_end = first_ts - 1
        if end_time == next_end:
            break
        end_time = next_end

    seen: set[int] = set()
    records: list[dict[str, Any]] = []
    for item in payload:
        ts = int(item["timestamp"])
        if ts in seen:
            continue
        seen.add(ts)
        records.append(
            {
                "timestamp": ts,
                "time": ms_to_iso(ts),
                "long_account": parse_float(item.get("longAccount")),
                "short_account": parse_float(item.get("shortAccount")),
                "long_short_ratio": parse_float(item.get("longShortRatio")),
            }
        )
    return sorted(records, key=lambda row: int(row["timestamp"]))


def merge_existing(existing: list[dict[str, str]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for row in existing:
        try:
            ts = int(row.get("timestamp") or 0)
        except ValueError:
            continue
        if ts <= 0:
            continue
        merged[ts] = {field: row.get(field, "") for field in FIELDNAMES}

    for row in incoming:
        ts = int(row["timestamp"])
        merged[ts] = {field: row.get(field, "") for field in FIELDNAMES}

    return [merged[ts] for ts in sorted(merged)]


def build_rows_by_timestamp(histories: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    by_ts: dict[int, dict[str, Any]] = {}
    for prefix, rows in histories.items():
        for source in rows:
            ts = int(source["timestamp"])
            target = by_ts.setdefault(
                ts,
                {"time": source["time"], "timestamp": str(ts), "source": "binance_futures_data_api"},
            )
            target[f"{prefix}_long_account"] = format_float(source.get("long_account"))
            target[f"{prefix}_short_account"] = format_float(source.get("short_account"))
            target[f"{prefix}_long_short_ratio"] = format_float(source.get("long_short_ratio"))
    return [by_ts[ts] for ts in sorted(by_ts)]


def collect_interval(args: argparse.Namespace, interval: str) -> dict[str, Any]:
    symbol = args.symbol.upper()
    output_path = resolve_path(args.cache_dir) / "futures" / symbol / f"{interval}_crowd_positioning.csv"

    histories = {
        name: fetch_ratio_history(endpoint, symbol, interval, args.limit, args.pages)
        for name, endpoint in ENDPOINTS.items()
    }
    incoming_rows = build_rows_by_timestamp(histories)
    existing_rows = read_csv_rows(output_path)
    merged_rows = merge_existing(existing_rows, incoming_rows)

    if not args.dry_run:
        write_csv_rows(output_path, merged_rows)

    per_endpoint = {
        name: {
            "rows": len(rows),
            "range": row_range(
                [
                    {"timestamp": str(row["timestamp"]), "time": row["time"]}
                    for row in rows
                ]
            ),
        }
        for name, rows in histories.items()
    }
    return {
        "kind": "crowd_positioning",
        "symbol": symbol,
        "interval": interval,
        "path": str(output_path),
        "dry_run": bool(args.dry_run),
        "existing_rows": len(existing_rows),
        "incoming_rows": len(incoming_rows),
        "merged_rows": len(merged_rows),
        "range": row_range(merged_rows),
        "sources": per_endpoint,
        "coverage_note": "Binance public long/short ratio history is shorter than full OHLCV history; treat as external filter/observer until enough samples exist.",
    }


def write_report(report: dict[str, Any], out_prefix: Path) -> None:
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Binance Crowd Positioning Collector",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Symbol: `{report['symbol']}`",
        f"- Intervals: `{', '.join(report['intervals'])}`",
        f"- Pages x limit: `{report['pages']} x {report['limit']}`",
        f"- Dry run: `{report['dry_run']}`",
        f"- Decision: `{report['decision']}`",
        "",
        "## Coverage",
        "",
    ]
    for artifact in report["artifacts"]:
        rng = artifact["range"]
        lines.extend(
            [
                f"### {artifact['interval']}",
                "",
                f"- Path: `{Path(artifact['path']).relative_to(ROOT).as_posix()}`",
                f"- Existing rows: `{artifact['existing_rows']}`",
                f"- Incoming rows: `{artifact['incoming_rows']}`",
                f"- Merged rows: `{artifact['merged_rows']}`",
                f"- First: `{rng['first']}`",
                f"- Last: `{rng['last']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Runtime Safety",
            "",
            "- Public Binance futures-data endpoints only.",
            "- No API keys, no account access, no order placement.",
            "- Output is research/cache data; it does not change trading permissions.",
            "",
        ]
    )
    out_prefix.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")


def decide(artifacts: list[dict[str, Any]]) -> str:
    min_rows = min((int(item.get("merged_rows") or 0) for item in artifacts), default=0)
    if min_rows >= 1000:
        return "crowd_positioning_history_ready_for_research_tests"
    if min_rows >= 200:
        return "crowd_positioning_limited_history_ready_for_smoke_tests"
    return "crowd_positioning_forward_only_until_more_history"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Collect Binance public long/short ratio history for crowd-fade research.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--intervals", default="15m,1h,4h", help="Comma-separated Binance futures-data periods.")
    parser.add_argument("--pages", type=int, default=6)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    artifacts = [collect_interval(args, interval) for interval in intervals]
    report = {
        "generated_at": now_iso(),
        "engine": "BINANCE_CROWD_POSITIONING_COLLECTOR",
        "engine_version": "1.0.0",
        "symbol": args.symbol.upper(),
        "intervals": intervals,
        "pages": args.pages,
        "limit": args.limit,
        "dry_run": bool(args.dry_run),
        "cache_dir": str(resolve_path(args.cache_dir)),
        "decision": decide(artifacts),
        "artifacts": artifacts,
        "can_trade": False,
    }
    write_report(report, resolve_path(args.out_prefix))
    print(json.dumps({"decision": report["decision"], "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
