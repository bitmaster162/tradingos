#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.range_family_validator import RangeConfig, generate_signals, load_interval_payload, parse_rr_list  # noqa: E402
from tools.range_watchlist_refiner import apply_filter_mode, make_filters  # noqa: E402

DEFAULT_DIAGNOSTIC = ROOT / "docs" / "EDGE_CANDIDATE_HARDENING_DIAGNOSTIC_2026-06-19.json"
DEFAULT_RANGE_REPORT = ROOT / "docs" / "RANGE_SWEEP_RECLAIM_RR_STRESS_2026-06-18.json"
DEFAULT_CACHE_DIR = ROOT / "data" / "cache" / "binance_spot_perp_extended"
DEFAULT_JOURNAL = ROOT / "logs" / "forward_paper_feed" / "edge_same_shape_shadow_observer.jsonl"
DEFAULT_STATE = ROOT / "logs" / "forward_paper_feed" / "edge_same_shape_shadow_observer_state.json"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "EDGE_SAME_SHAPE_SHADOW_OBSERVER_2026-06-19"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def read_json(path: Path, default: Any = None) -> Any:
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def parse_base_id(strategy_id: str) -> dict[str, Any]:
    pattern = re.compile(
        r"^range_(?P<tf>[^_]+)_(?P<side>long|short)_(?P<trigger>.+)_lb(?P<lookback>\d+)_edge(?P<edge>[0-9.]+)_rr(?P<stop>[0-9.]+)x(?P<take>[0-9.]+)_h(?P<hold>\d+)"
    )
    match = pattern.match(strategy_id)
    if not match:
        raise ValueError(f"cannot_parse_base_strategy_id:{strategy_id}")
    data = match.groupdict()
    return {
        "tf": data["tf"],
        "side": data["side"].upper(),
        "trigger": data["trigger"],
        "lookback": int(data["lookback"]),
        "edge_pct": float(data["edge"]),
        "rr": f"{data['stop']}:{data['take']}",
        "max_hold_bars": int(data["hold"]),
    }


def rr_to_pair(value: str) -> tuple[float, float]:
    parsed = parse_rr_list(value)
    if not parsed:
        raise ValueError(f"invalid_rr:{value}")
    return parsed[0]


def config_from_candidate(row: dict[str, Any], settings: dict[str, Any]) -> RangeConfig:
    base_id = str(row.get("base_strategy_id") or row.get("strategy_id") or "")
    parsed = parse_base_id(base_id)
    stop, take = rr_to_pair(str(row.get("rr") or parsed["rr"]))
    side = str(row.get("side") or parsed["side"]).upper()
    trigger = str(row.get("trigger") or parsed["trigger"])
    if side == "LONG":
        rsi_filter = "lte"
        rsi_threshold = 45.0 if trigger == "near_low" else 50.0
    else:
        rsi_filter = "gte"
        rsi_threshold = 55.0 if trigger == "near_high" else 50.0
    return RangeConfig(
        strategy_id=str(row.get("strategy_id") or base_id),
        interval=str(row.get("interval") or parsed["tf"]),
        side=side,
        trigger=trigger,
        lookback=int(parsed["lookback"]),
        edge_pct=float(parsed["edge_pct"]),
        min_width_atr=float(settings.get("min_width_atr", 2.0)),
        max_width_atr=float(settings.get("max_width_atr", 12.0)),
        max_abs_trend_atr=float(settings.get("max_abs_trend_atr", 2.2)),
        max_atr_ratio=float(settings.get("max_atr_ratio", 1.15)),
        rsi_filter=rsi_filter,
        rsi_threshold=rsi_threshold,
        stop_atr=stop,
        take_atr=take,
        max_hold_bars=int(row.get("max_hold_bars") or parsed["max_hold_bars"]),
    )


def signal_payload(signal: dict[str, Any], bar: Any, index: int) -> dict[str, Any]:
    return {
        "bar_index": index,
        "bar_ts": str(bar.ts),
        "close": round(float(bar.close), 8),
        "atr": signal.get("atr"),
        "reason": signal.get("reason"),
        "feature_snapshot": signal.get("feature_snapshot") if isinstance(signal.get("feature_snapshot"), dict) else {},
    }


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest") if isinstance(report.get("latest"), dict) else {}
    lines = [
        "# Edge Same-Shape Shadow Observer",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Observer-only shadow comparison for same-shape variants.",
        "- Does not change the active edge observer candidate.",
        "- Does not create paper entry intents.",
        "- Does not send orders or grant live permission.",
        "",
        "## Latest",
        "",
        f"- Latest bar: `{latest.get('latest_closed_bar_ts')}` close `{latest.get('latest_closed_close')}`.",
        f"- Variants checked: `{latest.get('variants_checked')}`.",
        f"- Base signals: `{latest.get('base_signals')}`.",
        f"- Variant signals: `{latest.get('variant_signals')}`.",
        f"- Signalling variants: `{', '.join(latest.get('signalling_variants') or []) or 'none'}`.",
        f"- Journal: `{report.get('journal_path')}`.",
        "",
        "## Variants",
        "",
        "| Variant | Status | RR | Hold | Filter | Hist Score | Full Exp | Holdout Exp | Cost+10 |",
        "|---|---|---|---:|---|---:|---:|---:|---:|",
    ]
    for row in latest.get("variants", []):
        lines.append(
            f"| `{row.get('strategy_id')}` | `{row.get('status')}` | `{row.get('rr')}` | `{row.get('max_hold_bars')}` | "
            f"`{row.get('filter_mode')}` | `{row.get('hard_score')}` | `{row.get('full_expectancy_r')}` | "
            f"`{row.get('holdout_expectancy_r')}` | `{row.get('cost10_expectancy_r')}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A shadow signal is not a trade signal.",
            "- If a shadow variant signals more often, it still needs its own resolved forward outcomes before promotion.",
            "- Variants with negative cost-stress remain blocked even when holdout looks attractive.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    diagnostic_path = resolve_path(args.diagnostic)
    range_report_path = resolve_path(args.range_report)
    cache_dir = resolve_path(args.cache_dir)
    journal_path = resolve_path(args.journal_path)
    state_path = resolve_path(args.state_path)
    out_prefix = resolve_path(args.out_prefix)

    diagnostic = read_json(diagnostic_path)
    range_report = read_json(range_report_path)
    if not isinstance(diagnostic, dict):
        raise ValueError(f"diagnostic_not_found:{diagnostic_path}")
    if not isinstance(range_report, dict):
        raise ValueError(f"range_report_not_found:{range_report_path}")

    settings = range_report.get("settings") if isinstance(range_report.get("settings"), dict) else {}
    alternatives = [
        row for row in diagnostic.get("same_shape_alternatives", []) if isinstance(row, dict)
    ][: max(1, args.top_n)]
    if not alternatives:
        raise ValueError("no_same_shape_alternatives")

    payloads: dict[str, tuple[list[Any], list[dict[str, Any]], list[float | None]]] = {}
    filter_modes = make_filters()
    latest_rows: list[dict[str, Any]] = []
    journal_rows: list[dict[str, Any]] = []
    variant_signal_count = 0
    base_signal_count = 0
    signalling_variants: list[str] = []
    latest_closed_bar_ts = None
    latest_closed_close = None

    for row in alternatives:
        config = config_from_candidate(row, settings)
        if config.interval not in payloads:
            payloads[config.interval] = load_interval_payload(cache_dir, config.interval, args.oi_lag, args.spot_perp_lookback)
        bars, features, rsi14 = payloads[config.interval]
        if not bars:
            status = "shadow_no_data"
            raw_signals: list[dict[str, Any]] = []
            filtered: list[dict[str, Any]] = []
            latest_index = 0
            latest_bar = None
        else:
            latest_index = len(bars) - 1
            latest_bar = bars[latest_index]
            latest_closed_bar_ts = str(latest_bar.ts)
            latest_closed_close = round(float(latest_bar.close), 8)
            raw_signals = generate_signals(config, bars, features, rsi14, latest_index, latest_index + 1)
            filter_names = tuple(filter_modes.get(str(row.get("filter_mode") or ""), tuple(row.get("filters") or ())))
            filtered = apply_filter_mode(config, raw_signals, filter_names)
            if not raw_signals:
                status = "shadow_no_base_signal"
            elif filtered:
                status = "shadow_variant_signal_observed"
                variant_signal_count += 1
                signalling_variants.append(str(row.get("strategy_id")))
            else:
                status = "shadow_variant_filtered_out"
            if raw_signals:
                base_signal_count += 1

        latest_signal = signal_payload(filtered[0], latest_bar, latest_index) if filtered and latest_bar is not None else None
        latest_rows.append(
            {
                "strategy_id": row.get("strategy_id"),
                "base_strategy_id": row.get("base_strategy_id"),
                "status": status,
                "interval": config.interval,
                "side": config.side,
                "trigger": config.trigger,
                "rr": row.get("rr"),
                "max_hold_bars": row.get("max_hold_bars"),
                "filter_mode": row.get("filter_mode"),
                "filters": row.get("filters"),
                "hard_score": row.get("hard_score"),
                "full_expectancy_r": row.get("full_expectancy_r"),
                "holdout_expectancy_r": row.get("holdout_expectancy_r"),
                "cost10_expectancy_r": row.get("cost10_expectancy_r"),
                "latest_signal": latest_signal,
            }
        )
        journal_rows.append(
            {
                "event_type": "edge_same_shape_shadow_variant_state",
                "ts_emitted": now_iso(),
                "strategy_id": row.get("strategy_id"),
                "base_strategy_id": row.get("base_strategy_id"),
                "status": status,
                "bar_ts": latest_closed_bar_ts,
                "symbol": args.symbol.upper(),
                "interval": config.interval,
                "side": config.side,
                "trigger": config.trigger,
                "rr": row.get("rr"),
                "max_hold_bars": row.get("max_hold_bars"),
                "filter_mode": row.get("filter_mode"),
                "filters": row.get("filters"),
                "latest_signal": latest_signal,
                "historical_evidence": {
                    "hard_score": row.get("hard_score"),
                    "full_expectancy_r": row.get("full_expectancy_r"),
                    "holdout_expectancy_r": row.get("holdout_expectancy_r"),
                    "cost10_expectancy_r": row.get("cost10_expectancy_r"),
                    "full_trades": row.get("full_trades"),
                    "holdout_trades": row.get("holdout_trades"),
                    "stable_folds": row.get("stable_folds"),
                    "segment_positive_ratio": row.get("segment_positive_ratio"),
                    "worst_segment_expectancy_r": row.get("worst_segment_expectancy_r"),
                },
                "can_trade": False,
                "creates_paper_entry_intents": False,
                "sends_orders": False,
                "uses_private_credentials": False,
            }
        )

    latest = {
        "latest_closed_bar_ts": latest_closed_bar_ts,
        "latest_closed_close": latest_closed_close,
        "variants_checked": len(latest_rows),
        "base_signals": base_signal_count,
        "variant_signals": variant_signal_count,
        "signalling_variants": signalling_variants,
        "variants": latest_rows,
    }
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "mode": "observer_only",
            "can_trade": False,
            "creates_paper_entry_intents": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "inputs": {
            "diagnostic": rel_path(diagnostic_path),
            "range_report": rel_path(range_report_path),
            "cache_dir": rel_path(cache_dir),
            "top_n": args.top_n,
        },
        "latest": latest,
        "journal_path": rel_path(journal_path),
        "state_path": rel_path(state_path),
        "decision": "same_shape_shadow_no_trade_permission",
        "next_action": "score_shadow_variants_after_resolved_forward_events",
        "can_trade": False,
    }
    append_jsonl(journal_path, journal_rows)
    write_json(state_path, {"updated_at": now_iso(), "latest": latest, "can_trade": False})
    write_json(out_prefix.with_suffix(".json"), report)
    write_text(out_prefix.with_suffix(".md"), render_markdown(report))
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Observer-only same-shape shadow comparison for current edge candidate.")
    parser.add_argument("--diagnostic", default=str(DEFAULT_DIAGNOSTIC))
    parser.add_argument("--range-report", default=str(DEFAULT_RANGE_REPORT))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--journal-path", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--top-n", type=int, default=12)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    args = parser.parse_args()
    report = run(args)
    latest = report["latest"]
    print(
        json.dumps(
            {
                "status": "ok",
                "decision": report["decision"],
                "variants_checked": latest["variants_checked"],
                "base_signals": latest["base_signals"],
                "variant_signals": latest["variant_signals"],
                "json": rel_path(Path(args.out_prefix).with_suffix(".json")),
                "md": rel_path(Path(args.out_prefix).with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
