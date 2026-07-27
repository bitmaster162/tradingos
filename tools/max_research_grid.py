from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.max_backtest import (  # noqa: E402
    align_derivatives,
    fetch_binance_klines,
    fetch_funding_history,
    fetch_open_interest_history,
    run_event_export,
)
from tools.max_event_miner import mine_events, read_events  # noqa: E402


DEFAULT_GRID = "15m:2:16:4h,1h:4:12:4h,4h:3:8:1d"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_grid(value: str) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for raw in [item.strip() for item in value.split(",") if item.strip()]:
        parts = raw.split(":")
        if len(parts) != 4:
            raise ValueError(f"bad_grid_spec:{raw}; expected interval:pages:forward_bars:htf_interval")
        interval, pages, forward, htf = parts
        specs.append(
            {
                "interval": interval,
                "tf": interval,
                "pages": int(pages),
                "forward_bars": int(forward),
                "htf_interval": htf,
            }
        )
    if not specs:
        raise ValueError("empty_grid")
    return specs


def write_candidates_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "grid_id",
        "interval",
        "side",
        "rows",
        "hit_pct",
        "baseline_hit_pct",
        "edge_pct",
        "stable_folds",
        "fold_count",
        "score",
        "conditions",
        "labels",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MAX Core Lite v0.9 Research Grid",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Symbol: `{report['symbol']}`",
        f"- Grid: `{report['grid_raw']}`",
        f"- Candidate CSV: `{report['files']['candidates_csv']}`",
        "",
        "## Grid Results",
        "",
        "| Grid | Events | Baseline L/S | Top side | Top rows | Top hit % | Top edge % | Conditions |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for item in report["grid_results"]:
        top = item.get("top_candidate") or {}
        labels = " + ".join(f"`{label}`" for label in top.get("labels", [])) if top else "none"
        baseline = item.get("baseline", {})
        lines.append(
            f"| `{item['grid_id']}` | {item['events']} | {baseline.get('long_1r_hit_pct')}/{baseline.get('short_1r_hit_pct')} | "
            f"`{top.get('side')}` | {top.get('rows')} | {top.get('hit_pct')} | {top.get('edge_pct')} | {labels} |"
        )

    lines.extend(
        [
            "",
            "## Repeated Conditions",
            "",
            "| Condition | Count | Intervals |",
            "| --- | ---: | --- |",
        ]
    )
    for item in report["condition_repeats"]:
        lines.append(f"| `{item['condition']}` | {item['count']} | `{', '.join(item['intervals'])}` |")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            report["interpretation"],
            "",
            "## Runtime Boundary",
            "",
            report["runtime_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def run_grid(
    *,
    symbol: str,
    market: str,
    limit: int,
    grid_raw: str,
    out_prefix: Path,
    warmup_bars: int,
    max_conditions: int,
    folds: int,
    min_events: int,
    min_fold_events: int,
    min_hit_pct: float,
    min_edge_pct: float,
    top: int,
) -> dict[str, Any]:
    specs = parse_grid(grid_raw)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    stem = out_prefix.name
    grid_results: list[dict[str, Any]] = []
    flat_candidates: list[dict[str, Any]] = []
    condition_counts: dict[str, set[str]] = defaultdict(set)

    for spec in specs:
        interval = spec["interval"]
        pages = int(spec["pages"])
        forward_bars = int(spec["forward_bars"])
        htf_interval = spec["htf_interval"]
        grid_id = f"{symbol.upper()}_{interval}_p{pages}_f{forward_bars}"

        rows = fetch_binance_klines(symbol, interval, limit, market, pages=pages)
        htf_rows = fetch_binance_klines(symbol, htf_interval, limit, market, pages=pages)
        spot_rows = fetch_binance_klines(symbol, interval, limit, "spot", pages=pages)
        oi_records = fetch_open_interest_history(symbol, interval, limit, pages=pages)
        funding_records = fetch_funding_history(symbol, pages=pages)
        oi_rows = align_derivatives(rows, interval=interval, oi_records=oi_records, funding_records=funding_records)

        event_prefix = out_prefix.parent / f"{stem}_{interval}_events"
        event_report = run_event_export(
            rows=rows,
            oi_rows=oi_rows,
            htf_rows=htf_rows,
            spot_rows=spot_rows,
            symbol=symbol,
            tf=interval,
            htf_interval=htf_interval,
            warmup_bars=warmup_bars,
            event_forward_bars=forward_bars,
            event_stride=1,
            out_prefix=event_prefix,
        )
        event_rows = read_events(Path(event_report["files"]["csv"]))
        mined = mine_events(
            rows=event_rows,
            max_conditions=max_conditions,
            folds=folds,
            min_events=min_events,
            min_fold_events=min_fold_events,
            min_hit_pct=min_hit_pct,
            min_edge_pct=min_edge_pct,
            top=top,
        )
        top_candidate = mined["top_candidates"][0] if mined["top_candidates"] else None
        if top_candidate:
            for condition in top_candidate.get("conditions", []):
                condition_counts[str(condition)].add(interval)

        miner_json = out_prefix.parent / f"{stem}_{interval}_miner.json"
        miner_json.write_text(
            json.dumps(
                {
                    "generated_at": now_iso(),
                    "grid_id": grid_id,
                    "event_report": event_report,
                    "miner": mined,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        for candidate in mined["top_candidates"]:
            flat = {
                "grid_id": grid_id,
                "interval": interval,
                "side": candidate["side"],
                "rows": candidate["rows"],
                "hit_pct": candidate["hit_pct"],
                "baseline_hit_pct": candidate["baseline_hit_pct"],
                "edge_pct": candidate["edge_pct"],
                "stable_folds": candidate["stable_folds"],
                "fold_count": candidate["fold_count"],
                "score": candidate["score"],
                "conditions": " + ".join(candidate["conditions"]),
                "labels": " + ".join(candidate["labels"]),
            }
            flat_candidates.append(flat)

        grid_results.append(
            {
                "grid_id": grid_id,
                "interval": interval,
                "pages": pages,
                "forward_bars": forward_bars,
                "events": event_report["summary"]["events"],
                "baseline": mined["baseline"],
                "top_candidate": top_candidate,
                "event_files": event_report["files"],
                "miner_json": str(miner_json),
            }
        )

    condition_repeats = [
        {"condition": condition, "count": len(intervals), "intervals": sorted(intervals)}
        for condition, intervals in condition_counts.items()
    ]
    condition_repeats.sort(key=lambda item: (item["count"], item["condition"]), reverse=True)

    candidates_csv = out_prefix.with_suffix(".candidates.csv")
    write_candidates_csv(candidates_csv, flat_candidates)

    promoted = [
        item
        for item in grid_results
        if item.get("top_candidate")
        and item["top_candidate"].get("stable_folds") == item["top_candidate"].get("fold_count")
        and item["top_candidate"].get("rows", 0) >= min_events
    ]

    interpretation = (
        "v0.9 is a research grid, not a trading system. A repeated condition across intervals is a lead for v1.0 strategy design, "
        "but any candidate must still pass strict backtest gates with fees, slippage and enough trades."
    )
    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_RESEARCH_GRID",
        "engine_version": "0.9.0",
        "symbol": symbol.upper(),
        "market": market,
        "limit": limit,
        "grid_raw": grid_raw,
        "config": {
            "warmup_bars": warmup_bars,
            "max_conditions": max_conditions,
            "folds": folds,
            "min_events": min_events,
            "min_fold_events": min_fold_events,
            "min_hit_pct": min_hit_pct,
            "min_edge_pct": min_edge_pct,
            "top": top,
        },
        "grid_results": grid_results,
        "condition_repeats": condition_repeats,
        "promoted_for_strategy_design": promoted,
        "files": {
            "json": str(out_prefix.with_suffix(".json")),
            "md": str(out_prefix.with_suffix(".md")),
            "candidates_csv": str(candidates_csv),
        },
        "interpretation": interpretation,
        "runtime_boundary": (
            "Research-only multi-timeframe grid. It fetches public market data and writes diagnostics; "
            "it does not use private keys, does not place orders, and does not approve live trading."
        ),
    }
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX Core Lite v0.9 multi-timeframe research grid")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--market", choices=("futures", "spot"), default="futures")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--grid", default=DEFAULT_GRID, help="Comma list: interval:pages:forward_bars:htf_interval")
    parser.add_argument("--out-prefix", default="_dl/research_grid/BTCUSDT_v09_grid")
    parser.add_argument("--warmup-bars", type=int, default=220)
    parser.add_argument("--max-conditions", type=int, default=3)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--min-events", type=int, default=20)
    parser.add_argument("--min-fold-events", type=int, default=4)
    parser.add_argument("--min-hit-pct", type=float, default=55.0)
    parser.add_argument("--min-edge-pct", type=float, default=7.0)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    report = run_grid(
        symbol=args.symbol,
        market=args.market,
        limit=args.limit,
        grid_raw=args.grid,
        out_prefix=out_prefix,
        warmup_bars=args.warmup_bars,
        max_conditions=args.max_conditions,
        folds=args.folds,
        min_events=args.min_events,
        min_fold_events=args.min_fold_events,
        min_hit_pct=args.min_hit_pct,
        min_edge_pct=args.min_edge_pct,
        top=args.top,
    )
    print(
        json.dumps(
            {
                "json": report["files"]["json"],
                "md": report["files"]["md"],
                "candidates_csv": report["files"]["candidates_csv"],
                "grid_results": [
                    {
                        "grid_id": item["grid_id"],
                        "events": item["events"],
                        "top_candidate": item.get("top_candidate"),
                    }
                    for item in report["grid_results"]
                ],
                "condition_repeats": report["condition_repeats"][:10],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
