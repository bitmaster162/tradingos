#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.historical_oi_importer import (  # noqa: E402
    merge_records,
    normalize_funding_rows,
    normalize_oi_rows,
    write_oi_csv as write_raw_oi_csv,
)
from tools.max_backtest import align_derivatives, read_ohlcv_csv, write_oi_csv as write_aligned_oi_csv  # noqa: E402


INTERVAL_MS = {
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def sample_oi_at_bar_close(
    klines: list[dict[str, str]],
    source: list[dict[str, Any]],
    *,
    interval: str,
    max_staleness_bars: float,
) -> list[dict[str, Any]]:
    interval_ms = INTERVAL_MS[interval]
    max_staleness_ms = int(interval_ms * max_staleness_bars)
    ordered = sorted(source, key=lambda row: int(row["timestamp"]))
    sampled: list[dict[str, Any]] = []
    source_index = -1
    for bar in klines:
        open_ms = int(float(bar.get("time_ms") or 0))
        if open_ms <= 0:
            continue
        close_ms = open_ms + interval_ms - 1
        while source_index + 1 < len(ordered) and int(ordered[source_index + 1]["timestamp"]) <= close_ms:
            source_index += 1
        if source_index < 0:
            continue
        selected = ordered[source_index]
        if close_ms - int(selected["timestamp"]) > max_staleness_ms:
            continue
        # Canonical interval caches are keyed by the candle open. The value is
        # the last public OI observation available by that candle's close.
        sampled.append({"timestamp": open_ms, "open_interest": float(selected["open_interest"])})
    return sampled


def backup(path: Path, backup_dir: Path) -> str | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / path.name
    shutil.copy2(path, destination)
    return rel(destination)


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    return "\n".join(
        [
            "# OI Cache Reindex",
            "",
            f"Generated: `{report['generated_at']}`",
            "",
            f"- Decision: `{report['decision']}`.",
            f"- Source/sampled/merged OI rows: `{summary['source_rows']}` / `{summary['sampled_rows']}` / `{summary['merged_rows']}`.",
            f"- Kline/aligned rows: `{summary['kline_rows']}` / `{summary['aligned_rows']}`.",
            f"- Aligned OI coverage: `{summary['aligned_oi_coverage_pct']}%`.",
            f"- Range: `{summary['first']}` to `{summary['last']}`.",
            "- Linear causal reindex; no private credentials or orders.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Reindex dense public OI history into an interval cache in linear time")
    parser.add_argument("--source-raw", required=True)
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", choices=sorted(INTERVAL_MS), default="1h")
    parser.add_argument("--max-staleness-bars", type=float, default=1.0)
    parser.add_argument("--out-prefix", default="docs/OI_CACHE_REINDEX_2026-06-23")
    args = parser.parse_args()

    cache_dir = resolve_path(args.cache_dir)
    symbol_dir = cache_dir / "futures" / args.symbol.upper()
    kline_path = symbol_dir / f"{args.interval}_klines.csv"
    raw_path = symbol_dir / f"{args.interval}_open_interest_raw.csv"
    aligned_path = symbol_dir / f"{args.interval}_oi_aligned.csv"
    funding_path = symbol_dir / "funding_raw.csv"
    source_path = resolve_path(args.source_raw)

    klines = read_ohlcv_csv(kline_path)
    source, source_stats = normalize_oi_rows(source_path, "timestamp", "open_interest")
    existing, _ = normalize_oi_rows(raw_path, "timestamp", "open_interest") if raw_path.exists() else ([], {})
    sampled = sample_oi_at_bar_close(
        klines,
        source,
        interval=args.interval,
        max_staleness_bars=args.max_staleness_bars,
    )
    # Existing API observations win exact timestamp overlaps.
    merged = merge_records(sampled, existing)
    funding = normalize_funding_rows(funding_path)
    aligned = align_derivatives(klines, interval=args.interval, oi_records=merged, funding_records=funding)

    backup_root = symbol_dir / "_reindex_backup" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backups = {"raw": backup(raw_path, backup_root), "aligned": backup(aligned_path, backup_root)}
    write_raw_oi_csv(raw_path, merged)
    write_aligned_oi_csv(aligned_path, aligned)

    covered = sum(1 for row in aligned if str(row.get("open_interest") or "").strip())
    summary = {
        "source_rows": len(source),
        "source_first": source_stats.get("first"),
        "source_last": source_stats.get("last"),
        "existing_rows": len(existing),
        "sampled_rows": len(sampled),
        "merged_rows": len(merged),
        "kline_rows": len(klines),
        "aligned_rows": len(aligned),
        "aligned_oi_rows": covered,
        "aligned_oi_coverage_pct": round(covered / len(aligned) * 100.0, 3) if aligned else 0.0,
        "first": klines[0].get("time") if klines else None,
        "last": klines[-1].get("time") if klines else None,
        "raw_path": rel(raw_path),
        "aligned_path": rel(aligned_path),
        "backups": backups,
    }
    report = {
        "generated_at": now_iso(),
        "method": "linear_latest_public_oi_at_or_before_bar_close",
        "inputs": {
            "source_raw": rel(source_path),
            "interval": args.interval,
            "max_staleness_bars": args.max_staleness_bars,
        },
        "summary": summary,
        "runtime_boundary": {
            "public_local_data_only": True,
            "changes_strategy_parameters": False,
            "sends_orders": False,
            "can_trade": False,
        },
        "decision": "oi_interval_cache_reindexed_no_orders",
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "summary": summary, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
