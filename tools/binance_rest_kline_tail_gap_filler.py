#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
FIELDNAMES = ["time", "time_ms", "open", "high", "low", "close", "volume"]
ENDPOINTS = {
    "spot": "https://api.binance.com/api/v3/klines",
    "futures": "https://fapi.binance.com/fapi/v1/klines",
}
INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def ms_to_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()


def parse_ts_ms(value: str) -> int:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return int(datetime.fromisoformat(text).timestamp() * 1000)


def read_existing(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def normalize_ms(value: Any) -> int | None:
    try:
        parsed = int(float(str(value)))
    except (TypeError, ValueError):
        return None
    if parsed > 10_000_000_000_000:
        parsed //= 1000
    return parsed if 0 < parsed < 10_000_000_000_000 else None


def fetch_klines(
    *,
    market: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int,
    timeout: int,
) -> list[list[Any]]:
    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": limit,
    }
    url = f"{ENDPOINTS[market]}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "TradingOS-public-tail-gap-filler/1.0"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed official Binance public endpoint.
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected Binance response for {symbol}: {payload!r}")
    return payload


def kline_to_row(item: list[Any]) -> dict[str, str] | None:
    if len(item) < 6:
        return None
    open_ms = normalize_ms(item[0])
    if open_ms is None:
        return None
    try:
        values = [float(item[index]) for index in range(1, 6)]
    except (TypeError, ValueError):
        return None
    return {
        "time": ms_to_iso(open_ms),
        "time_ms": str(open_ms),
        "open": str(values[0]),
        "high": str(values[1]),
        "low": str(values[2]),
        "close": str(values[3]),
        "volume": str(values[4]),
    }


def merge_rows(existing: list[dict[str, str]], fetched: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    merged: dict[int, dict[str, str]] = {}
    for row in existing:
        ts = normalize_ms(row.get("time_ms") or "")
        if ts is not None:
            merged[ts] = {field: str(row.get(field, "")) for field in FIELDNAMES}
    overlap = 0
    for row in fetched:
        ts = normalize_ms(row.get("time_ms") or "")
        if ts is None:
            continue
        overlap += int(ts in merged)
        merged[ts] = {field: str(row.get(field, "")) for field in FIELDNAMES}
    return [merged[key] for key in sorted(merged)], overlap


def write_rows(path: Path, rows: list[dict[str, str]], *, create_backup: bool = True) -> Path | None:
    backup_path: Path | None = None
    if path.exists() and create_backup:
        backup_root = path.parent / "_rest_tail_backup" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_path = backup_root / path.name
        shutil.copy2(path, backup_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)
    return backup_path


def fill_symbol(args: argparse.Namespace, symbol: str) -> dict[str, Any]:
    interval_ms = INTERVAL_MS[args.interval]
    output = resolve_path(args.cache_dir) / args.market / symbol / f"{args.interval}_klines.csv"
    existing = read_existing(output)
    last_existing_ms = max((normalize_ms(row.get("time_ms") or "") or 0 for row in existing), default=0)
    end_ms = parse_ts_ms(args.end) if args.end else int(time.time() * 1000)
    if args.start:
        start_ms = parse_ts_ms(args.start)
    elif last_existing_ms:
        # Re-fetch the current tail candle because a previous run may have stored it before close.
        start_ms = last_existing_ms
    else:
        start_ms = max(0, end_ms - interval_ms * args.limit * args.max_pages)
    fetched_rows: list[dict[str, str]] = []
    pages = 0
    current = start_ms
    errors: list[str] = []
    while current <= end_ms and pages < args.max_pages:
        try:
            raw = fetch_klines(
                market=args.market,
                symbol=symbol,
                interval=args.interval,
                start_ms=current,
                end_ms=end_ms,
                limit=args.limit,
                timeout=args.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))
            break
        pages += 1
        if not raw:
            break
        converted = [row for item in raw if (row := kline_to_row(item)) is not None]
        fetched_rows.extend(converted)
        last_ms = normalize_ms(converted[-1]["time_ms"]) if converted else None
        if last_ms is None or last_ms < current:
            break
        current = last_ms + interval_ms
        if len(raw) < args.limit:
            break
        time.sleep(max(0.0, args.sleep_sec))
    merged, overlap = merge_rows(existing, fetched_rows)
    backup = None
    written = False
    if not args.dry_run and fetched_rows and not errors:
        backup = write_rows(output, merged, create_backup=not args.no_backup)
        written = True
    return {
        "symbol": symbol,
        "market": args.market,
        "interval": args.interval,
        "path": portable(output),
        "existing_rows": len(existing),
        "fetched_rows": len(fetched_rows),
        "overlap_rows_existing_replaced": overlap,
        "merged_rows": len(merged),
        "first": merged[0]["time"] if merged else None,
        "last_before": ms_to_iso(last_existing_ms) if last_existing_ms else None,
        "last_after": merged[-1]["time"] if merged else None,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "pages": pages,
        "written": written,
        "backup_path": portable(backup) if backup else None,
        "errors": errors,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Binance REST Kline Tail Gap Filler",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        f"- Market: `{report['inputs']['market']}`",
        f"- Interval: `{report['inputs']['interval']}`",
        "",
        "## Boundary",
        "",
        "- Public REST klines only.",
        "- Intended for recent tail gaps before monthly Binance Vision archives are available.",
        "- No private credentials, no account endpoints, no alerts, no paper intents, no orders.",
        "",
        "## Results",
        "",
        "| Symbol | Existing | Fetched | Merged | Last before | Last after | Written | Errors |",
        "|---|---:|---:|---:|---|---|---:|---|",
    ]
    for item in report["results"]:
        lines.append(
            f"| {item['symbol']} | `{item['existing_rows']}` | `{item['fetched_rows']}` | `{item['merged_rows']}` | "
            f"`{item['last_before']}` | `{item['last_after']}` | `{item['written']}` | `{'; '.join(item['errors'])}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill recent Binance kline tail gaps from public REST klines.")
    parser.add_argument("--market", choices=sorted(ENDPOINTS), default="spot")
    parser.add_argument("--symbols", default="ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--interval", choices=sorted(INTERVAL_MS), default="1h")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--sleep-sec", type=float, default=0.2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--out-prefix", default="docs/BINANCE_REST_KLINE_TAIL_GAP_FILLER_2026-07-02")
    args = parser.parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    results = [fill_symbol(args, symbol) for symbol in symbols]
    any_errors = any(item["errors"] for item in results)
    any_written = any(item["written"] for item in results)
    any_fetched = any(item["fetched_rows"] for item in results)
    if any_errors:
        decision = "rest_kline_tail_gap_fill_partial_or_blocked"
    elif args.dry_run and any_fetched:
        decision = "rest_kline_tail_gap_dry_run_ready"
    elif any_written:
        decision = "rest_kline_tail_gap_fill_completed"
    elif any_fetched:
        decision = "rest_kline_tail_gap_fetched_not_written"
    else:
        decision = "rest_kline_tail_gap_no_new_rows"
    report = {
        "generated_at": now_iso(),
        "tool": "tools/binance_rest_kline_tail_gap_filler.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "inputs": {
            "market": args.market,
            "symbols": symbols,
            "interval": args.interval,
            "cache_dir": args.cache_dir,
            "start": args.start or None,
            "end": args.end or None,
            "dry_run": args.dry_run,
            "backup_enabled": not args.no_backup,
        },
        "boundary": {
            "public_market_data_only": True,
            "uses_private_credentials": False,
            "account_endpoints": False,
            "sends_orders": False,
            "can_trade": False,
        },
        "results": results,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "market": args.market,
                "symbols": symbols,
                "fetched_rows": sum(item["fetched_rows"] for item in results),
                "written": any_written,
                "out": portable(out.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if any_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
