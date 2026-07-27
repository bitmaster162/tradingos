from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.max_backtest import INTERVAL_MS  # noqa: E402
from tools.max_v11_candidate_validator import load_or_fetch  # noqa: E402
from tools.max_v13_structural_candidate import (  # noqa: E402
    gate_candidate,
    parse_float,
    simulate_candidate,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def candidate_specs(args: argparse.Namespace) -> list[dict[str, Any]]:
    strict_width = (args.strict_width_lower, args.strict_width_upper)
    broad_width = (args.broad_width_lower, args.broad_width_upper)

    def width_between(features: dict[str, Any], band: tuple[float, float]) -> bool:
        width = parse_float(features.get("donchian_width_atr"))
        return band[0] <= width <= band[1]

    def div_ok(features: dict[str, Any]) -> bool:
        return parse_float(features.get("spot_perp_divergence_12")) >= args.divergence_min

    def spot_quiet(features: dict[str, Any]) -> bool:
        return parse_float(features.get("spot_volume_ratio")) <= args.spot_volume_max

    specs: list[dict[str, Any]] = []
    for side in ("LONG", "SHORT"):
        side_prefix = side.lower()
        specs.extend(
            [
                {
                    "id": f"v14_{side_prefix}_strict_near_low_quiet_spot",
                    "side": side,
                    "requires": [
                        "near_low",
                        f"spot_volume_ratio <= {args.spot_volume_max}",
                        f"{strict_width[0]} <= donchian_width_atr <= {strict_width[1]}",
                        f"spot_perp_divergence_12 >= {args.divergence_min}",
                    ],
                    "predicate": lambda f, band=strict_width: bool(
                        f.get("near_low") and spot_quiet(f) and width_between(f, band) and div_ok(f)
                    ),
                },
                {
                    "id": f"v14_{side_prefix}_strict_near_low",
                    "side": side,
                    "requires": [
                        "near_low",
                        f"{strict_width[0]} <= donchian_width_atr <= {strict_width[1]}",
                        f"spot_perp_divergence_12 >= {args.divergence_min}",
                    ],
                    "predicate": lambda f, band=strict_width: bool(f.get("near_low") and width_between(f, band) and div_ok(f)),
                },
                {
                    "id": f"v14_{side_prefix}_strict_structural_only",
                    "side": side,
                    "requires": [
                        f"{strict_width[0]} <= donchian_width_atr <= {strict_width[1]}",
                        f"spot_perp_divergence_12 >= {args.divergence_min}",
                    ],
                    "predicate": lambda f, band=strict_width: bool(width_between(f, band) and div_ok(f)),
                },
                {
                    "id": f"v14_{side_prefix}_broad_near_low_quiet_spot",
                    "side": side,
                    "requires": [
                        "near_low",
                        f"spot_volume_ratio <= {args.spot_volume_max}",
                        f"{broad_width[0]} <= donchian_width_atr <= {broad_width[1]}",
                        f"spot_perp_divergence_12 >= {args.divergence_min}",
                    ],
                    "predicate": lambda f, band=broad_width: bool(
                        f.get("near_low") and spot_quiet(f) and width_between(f, band) and div_ok(f)
                    ),
                },
                {
                    "id": f"v14_{side_prefix}_broad_near_low",
                    "side": side,
                    "requires": [
                        "near_low",
                        f"{broad_width[0]} <= donchian_width_atr <= {broad_width[1]}",
                        f"spot_perp_divergence_12 >= {args.divergence_min}",
                    ],
                    "predicate": lambda f, band=broad_width: bool(f.get("near_low") and width_between(f, band) and div_ok(f)),
                },
                {
                    "id": f"v14_{side_prefix}_broad_structural_only",
                    "side": side,
                    "requires": [
                        f"{broad_width[0]} <= donchian_width_atr <= {broad_width[1]}",
                        f"spot_perp_divergence_12 >= {args.divergence_min}",
                    ],
                    "predicate": lambda f, band=broad_width: bool(width_between(f, band) and div_ok(f)),
                },
                {
                    "id": f"v14_{side_prefix}_divergence_only",
                    "side": side,
                    "requires": [f"spot_perp_divergence_12 >= {args.divergence_min}"],
                    "predicate": lambda f: bool(div_ok(f)),
                },
            ]
        )
    return specs


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MAX Core Lite v1.4 Long Expansion",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Data: `{report['data']['first_time']}` -> `{report['data']['last_time']}`",
        f"- Rows: `{report['data']['rows']}` futures / `{report['data']['spot_rows']}` spot",
        "",
        "## Purpose",
        "",
        "Runs a larger-sample structural sweep and tests the reverse LONG hypothesis for the v1.2/v1.3 lead.",
        "",
        "## Results",
        "",
        "| Candidate | Side | Trades | Winrate | Expectancy | Net R | Bootstrap P>0 | Stable Folds | Verdict |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["candidates"]:
        summary = item["summary"]
        gate = item["research_gate"]
        prob = (item.get("bootstrap", {}).get("expectancy_r") or {}).get("prob_gt_0")
        lines.append(
            f"| `{item['id']}` | {item['side']} | {summary['trades']} | {summary['winrate_pct']} | "
            f"{summary['expectancy_r']} | {summary['net_r_total']} | {prob} | "
            f"{gate['stable_folds']}/{gate['fold_count']} | `{gate['verdict']}` |"
        )
    lines.extend(["", "## Best Candidate", ""])
    best = report.get("best_candidate")
    if best:
        lines.extend(
            [
                f"- ID: `{best['id']}`",
                f"- Side: `{best['side']}`",
                f"- Trades: `{best['summary']['trades']}`",
                f"- Winrate: `{best['summary']['winrate_pct']}`",
                f"- Expectancy: `{best['summary']['expectancy_r']}`",
                f"- Verdict: `{best['research_gate']['verdict']}`",
                "",
            ]
        )
    lines.extend(["## Decision", "", report["decision"], "", "## Boundary", "", report["runtime_boundary"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX Core Lite v1.4 larger-sample LONG/SHORT expansion")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--market", default="futures", choices=["futures"])
    parser.add_argument("--pages", type=int, default=24)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--warmup-bars", type=int, default=220)
    parser.add_argument("--strict-width-lower", type=float, default=6.0)
    parser.add_argument("--strict-width-upper", type=float, default=7.0)
    parser.add_argument("--broad-width-lower", type=float, default=4.0)
    parser.add_argument("--broad-width-upper", type=float, default=9.0)
    parser.add_argument("--divergence-min", type=float, default=0.0)
    parser.add_argument("--spot-volume-max", type=float, default=0.8)
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", type=float, default=1.5)
    parser.add_argument("--max-hold-bars", type=int, default=16)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=8)
    parser.add_argument("--bootstrap-iterations", type=int, default=5000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260602)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--min-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-winrate-pct", type=float, default=50.0)
    parser.add_argument("--min-bootstrap-prob-gt-0", type=float, default=0.8)
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--cache-dir", default="data/cache/binance")
    parser.add_argument("--out-prefix", default="_dl/v14/MAX_CORE_LITE_V14_LONG_EXPANSION")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = ROOT / cache_dir
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    rows, source = load_or_fetch(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        market=args.market,
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        pages=args.pages,
    )
    spot_rows, spot_source = load_or_fetch(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        market="spot",
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        pages=args.pages,
    )
    interval_ms = INTERVAL_MS.get(args.interval, 3_600_000)
    rng = random.Random(args.bootstrap_seed)
    results: list[dict[str, Any]] = []
    for spec in candidate_specs(args):
        trades, skipped = simulate_candidate(
            spec=spec,
            rows=rows,
            spot_rows=spot_rows,
            htf_rows=[],
            htf_interval="",
            interval_ms=interval_ms,
            warmup_bars=args.warmup_bars,
            stop_atr=args.stop_atr,
            take_atr=args.take_atr,
            max_hold_bars=args.max_hold_bars,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
        )
        gate = gate_candidate(
            trades=trades,
            rows_count=len(rows),
            warmup_bars=args.warmup_bars,
            folds=args.folds,
            bootstrap_iterations=args.bootstrap_iterations,
            bootstrap_seed=rng.randrange(1, 10_000_000),
            min_trades=args.min_trades,
            min_expectancy_r=args.min_expectancy_r,
            min_winrate_pct=args.min_winrate_pct,
            min_bootstrap_prob_gt_0=args.min_bootstrap_prob_gt_0,
        )
        results.append(
            {
                "id": spec["id"],
                "side": spec["side"],
                "requires": spec["requires"],
                "summary": gate["summary"],
                "folds": gate["folds"],
                "stable_folds": gate["stable_folds"],
                "bootstrap": gate["bootstrap"],
                "research_gate": gate["research_gate"],
                "skipped": skipped,
                "trades": trades,
            }
        )

    def rank_key(item: dict[str, Any]) -> tuple[int, float, float, int]:
        summary = item["summary"]
        return (
            1 if item["research_gate"].get("pass") else 0,
            float(summary.get("expectancy_r") or -999.0),
            float(summary.get("winrate_pct") or 0.0),
            int(summary.get("trades") or 0),
        )

    results.sort(key=rank_key, reverse=True)
    best = results[0] if results else None
    passed = [item for item in results if item["research_gate"].get("pass")]
    decision = (
        "At least one v1.4 candidate passed the research gate and can move to paper-trading design review."
        if passed
        else "No v1.4 LONG/SHORT expansion candidate passed the research gate. Keep this lead research-only."
    )
    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_V14_LONG_EXPANSION",
        "engine_version": "1.4.0",
        "data": {
            "rows": len(rows),
            "spot_rows": len(spot_rows),
            "first_time": rows[0].get("time") if rows else None,
            "last_time": rows[-1].get("time") if rows else None,
            "source": source,
            "spot_source": spot_source,
        },
        "params": vars(args),
        "source_lead": "spot_perp_divergence_12 >= 0 + Donchian width bands; reverse LONG hypothesis included",
        "candidates": results,
        "best_candidate": best,
        "passed": passed,
        "decision": decision,
        "runtime_boundary": (
            "Research-only larger-sample expansion. It uses public market data and deterministic simulation; "
            "it does not use private keys, does not place orders, and does not approve live trading."
        ),
    }
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(json_path),
                "md": str(md_path),
                "best_candidate": {
                    "id": best.get("id") if best else None,
                    "side": best.get("side") if best else None,
                    "summary": best.get("summary") if best else None,
                    "research_gate": best.get("research_gate") if best else None,
                },
                "passed": len(passed),
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
