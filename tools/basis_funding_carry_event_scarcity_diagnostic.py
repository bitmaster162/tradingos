#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.basis_funding_carry_nested_holdout import (  # noqa: E402
    CarryConfig,
    aligned_bars,
    funding_events,
    rolling_funding_means,
    simulate_window,
    split_index,
    summarize,
)


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


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def selected_config(report: dict[str, Any]) -> CarryConfig:
    selected = report.get("selected_on_train") if isinstance(report.get("selected_on_train"), dict) else {}
    config = selected.get("config") if isinstance(selected.get("config"), dict) else {}
    if not config:
        raise ValueError("selected_on_train.config is missing")
    return CarryConfig(**config)


def load_context(cache: Path, symbol: str) -> dict[str, Any]:
    symbol = symbol.upper()
    spot_path = cache / "spot" / symbol / "1h_klines.csv"
    futures_path = cache / "futures" / symbol / "1h_klines.csv"
    funding_path = cache / "futures" / symbol / "funding_raw.csv"
    rows = aligned_bars(spot_path, futures_path)
    events = funding_events(funding_path)
    funding_mean = rolling_funding_means(rows, events)
    return {
        "symbol": symbol,
        "rows": rows,
        "events": events,
        "funding_mean": funding_mean,
        "spot_path": portable(spot_path),
        "futures_path": portable(futures_path),
        "funding_path": portable(funding_path),
    }


def safe_split_index(rows: list[dict[str, Any]], timestamp: str) -> int:
    try:
        return split_index(rows, timestamp)
    except ValueError as exc:
        if str(exc).startswith("split after data"):
            return len(rows)
        raise


def window_indices(rows: list[dict[str, Any]], start: str, end: str) -> tuple[int, int]:
    return safe_split_index(rows, start), safe_split_index(rows, end)


def with_symbol(symbol: str, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**trade, "symbol": symbol} for trade in trades]


def simulate_bucket(
    config: CarryConfig,
    contexts: list[dict[str, Any]],
    *,
    start: str,
    end: str,
    fee_bps: float,
    slippage_bps: float,
) -> dict[str, Any]:
    all_trades: list[dict[str, Any]] = []
    by_symbol: dict[str, Any] = {}
    for context in contexts:
        rows = context["rows"]
        start_index, end_index = window_indices(rows, start, end)
        trades = with_symbol(
            context["symbol"],
            simulate_window(
                config,
                rows,
                context["funding_mean"],
                context["events"],
                start_index=start_index,
                end_index=end_index,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            ),
        )
        all_trades.extend(trades)
        by_symbol[context["symbol"]] = {
            "trades": len(trades),
            "summary": summarize(trades),
            "first_trade": trades[0]["entry_time"] if trades else None,
            "last_trade": trades[-1]["entry_time"] if trades else None,
        }
    ordered = sorted(all_trades, key=lambda item: (item["entry_time"], item.get("symbol", "")))
    return {
        "start": start,
        "end": end,
        "trades": len(ordered),
        "summary": summarize(ordered),
        "by_symbol": by_symbol,
        "first_trade": ordered[0]["entry_time"] if ordered else None,
        "last_trade": ordered[-1]["entry_time"] if ordered else None,
        "sample_trades": ordered[:5],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Basis/Funding Carry Event Scarcity Diagnostic",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        f"- Config: `{report['config']['strategy_id']}`",
        "",
        "## Bucket Counts",
        "",
        "| Bucket | Window | Trades | Positive % | Mean bps | First | Last |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for bucket in report.get("buckets", []):
        summary = bucket.get("summary") if isinstance(bucket.get("summary"), dict) else {}
        lines.append(
            f"| `{bucket['name']}` | `{bucket['start']}..{bucket['end']}` | `{bucket['trades']}` | "
            f"`{summary.get('positive_pct')}` | `{summary.get('mean_net_bps')}` | "
            f"`{bucket.get('first_trade')}` | `{bucket.get('last_trade')}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            f"- {report['next_action']}",
            "",
            "## Boundary",
            "",
            "- Diagnostic-only scarcity map.",
            "- Uses the frozen selected config; does not optimize or retune thresholds.",
            "- Does not open OOS, create signals, paper entries or orders.",
            "- `can_trade=false`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Diagnostic-only event scarcity map for frozen basis/funding carry config.")
    parser.add_argument("--source-report", default="docs/BASIS_FUNDING_CARRY_MULTI_SYMBOL_NESTED_HOLDOUT_2026-07-02_REFRESHED.json")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--out-prefix", default="docs/BASIS_FUNDING_CARRY_EVENT_SCARCITY_2026-07-02")
    args = parser.parse_args()

    source_path = resolve_path(args.source_report)
    source = read_json(source_path)
    config = selected_config(source)
    cache = resolve_path(args.cache_dir)
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    contexts = [load_context(cache, symbol) for symbol in symbols]
    buckets = [
        ("train_2021_2024", "2021-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
        ("validation_2025", "2025-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        ("forward_2026_ytd", "2026-01-01T00:00:00+00:00", now_iso()),
        ("full_available", "2021-01-01T00:00:00+00:00", now_iso()),
    ]
    bucket_rows = []
    for name, start, end in buckets:
        row = simulate_bucket(config, contexts, start=start, end=end, fee_bps=args.fee_bps, slippage_bps=args.slippage_bps)
        bucket_rows.append({"name": name, **row})

    validation = next(row for row in bucket_rows if row["name"] == "validation_2025")
    forward = next(row for row in bucket_rows if row["name"] == "forward_2026_ytd")
    if validation["trades"] == 0 and forward["trades"] == 0:
        decision = "basis_funding_carry_event_scarcity_no_recent_events"
        next_action = "do not promote; treat carry as rare-cycle regime and collect forward events or search another class"
    elif validation["trades"] == 0:
        decision = "basis_funding_carry_validation_starved_forward_events_exist"
        next_action = "do not use opened forward period for promotion; create a new preregistered forward observer for the frozen config"
    else:
        decision = "basis_funding_carry_validation_events_available_for_manual_review"
        next_action = "review validation quality without changing the frozen selected config"

    report = {
        "generated_at": now_iso(),
        "tool": "tools/basis_funding_carry_event_scarcity_diagnostic.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "source_report": portable(source_path),
        "config": asdict(config),
        "symbols": symbols,
        "data": [
            {
                "symbol": context["symbol"],
                "rows": len(context["rows"]),
                "funding_events": len(context["events"]),
                "first": context["rows"][0]["time"] if context["rows"] else None,
                "last": context["rows"][-1]["time"] if context["rows"] else None,
                "spot_path": context["spot_path"],
                "futures_path": context["futures_path"],
                "funding_path": context["funding_path"],
            }
            for context in contexts
        ],
        "buckets": bucket_rows,
        "next_action": next_action,
        "boundary": {
            "diagnostic_only": True,
            "uses_frozen_config": True,
            "optimizes_parameters": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "can_trade": False,
        },
    }
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "validation_trades": validation["trades"],
                "forward_2026_trades": forward["trades"],
                "out": portable(out_prefix.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
