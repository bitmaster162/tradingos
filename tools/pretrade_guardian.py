#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path("configs/PRETRADE_GUARDIAN_v1.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def as_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def normalize_trade(raw: dict[str, Any]) -> dict[str, Any]:
    tp = raw.get("tp") or raw.get("take_profit") or raw.get("targets") or []
    if not isinstance(tp, list):
        tp = [tp]
    entry = as_float(raw.get("entry"))
    return {
        "symbol": raw.get("symbol", "BTCUSDT"),
        "tf": raw.get("tf", "unknown"),
        "market": str(raw.get("market") or raw.get("instrument_type") or "spot").lower(),
        "side": str(raw.get("side", "")).upper(),
        "entry": entry,
        "stop": as_float(raw.get("stop") if raw.get("stop") is not None else raw.get("sl")),
        "tp": [as_float(item) for item in tp if as_float(item) is not None],
        "risk_pct": as_float(raw.get("risk_pct"), 1.0),
        "leverage": as_float(raw.get("leverage"), 1.0),
        "margin_mode": str(raw.get("margin_mode") or "").lower(),
        "mark_price": as_float(raw.get("mark_price"), entry),
        "liquidation_price": as_float(raw.get("liquidation_price") or raw.get("liq_price")),
        "fees_slippage_included": as_bool(raw.get("fees_slippage_included", False)),
        "confirmations": int(raw.get("confirmations") or len(raw.get("signals", []) or [])),
        "stop_method": str(raw.get("stop_method") or "").lower(),
        "restricted_window": as_bool(raw.get("restricted_window", False)),
        "funding": as_float(raw.get("funding")),
        "m15_bias": str(raw.get("m15_bias") or "").upper(),
        "bitmaster_bias": str(raw.get("bitmaster_bias") or raw.get("bitmasterai_bias") or "").upper(),
        "chasing": as_bool(raw.get("chasing", False)),
        "averaging_down": as_bool(raw.get("averaging_down", False)),
        "divergence_only": as_bool(raw.get("divergence_only", False)),
        "notes": raw.get("notes", ""),
    }


def rr_for(trade: dict[str, Any]) -> float | None:
    entry = trade["entry"]
    stop = trade["stop"]
    targets = trade["tp"]
    side = trade["side"]
    if entry is None or stop is None or not targets:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    target = targets[0]
    if side == "LONG":
        reward = target - entry
    elif side == "SHORT":
        reward = entry - target
    else:
        reward = abs(target - entry)
    return round(reward / risk, 6)


def is_futures_market(trade: dict[str, Any]) -> bool:
    return str(trade.get("market") or "").lower() in {"futures", "perp", "perpetual", "usdm", "coinm"}


def liquidation_buffer_pct(trade: dict[str, Any]) -> float | None:
    mark = trade.get("mark_price") or trade.get("entry")
    liquidation = trade.get("liquidation_price")
    side = trade.get("side")
    if not is_futures_market(trade) or mark is None or liquidation is None or mark <= 0:
        return None
    if side == "LONG":
        return round(((mark - liquidation) / mark) * 100.0, 6)
    if side == "SHORT":
        return round(((liquidation - mark) / mark) * 100.0, 6)
    return None


def funding_blocks(trade: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    funding = trade.get("funding")
    side = trade.get("side")
    if funding is None:
        return []
    hot = float(thresholds.get("hot_funding_abs", 0.08))
    extreme = float(thresholds.get("extreme_funding_abs", 0.10))
    blocks: list[str] = []
    if side == "LONG" and funding >= hot:
        blocks.append("hot_positive_funding_blocks_long_chase")
    if side == "SHORT" and funding <= -hot:
        blocks.append("hot_negative_funding_blocks_short_chase")
    if abs(funding) >= extreme:
        blocks.append("extreme_funding_requires_fakebreak_or_reduction")
    return blocks


def evaluate_trade(raw: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    trade = normalize_trade(raw)
    thresholds = config["thresholds"]
    allowed_stop_methods = set(config.get("allowed_stop_methods", []))
    rr = rr_for(trade)

    hard_blocks: list[str] = []
    warnings: list[str] = []
    adjustments: list[str] = []
    liq_buffer = liquidation_buffer_pct(trade)

    if trade["side"] not in {"LONG", "SHORT"}:
        hard_blocks.append("missing_or_invalid_side")
    if trade["entry"] is None:
        hard_blocks.append("missing_entry")
    if trade["stop"] is None:
        hard_blocks.append("missing_stop")
    if not trade["tp"]:
        hard_blocks.append("missing_take_profit")
    if trade["stop_method"] not in allowed_stop_methods:
        hard_blocks.append("stop_method_must_be_swing_structure_or_atr")
    if trade["confirmations"] < int(thresholds.get("min_confirmations", 2)):
        hard_blocks.append("not_enough_confirmations")
    if rr is None:
        hard_blocks.append("missing_or_invalid_rr")
    elif rr < float(thresholds.get("min_rr", 1.5)):
        hard_blocks.append("rr_below_minimum")
    elif rr < float(thresholds.get("strict_rr", 2.0)):
        warnings.append("rr_passes_min_but_below_strict_2r")
    if (trade["risk_pct"] or 0.0) > float(thresholds.get("max_risk_pct", 1.0)):
        hard_blocks.append("risk_pct_above_limit")
    if trade["restricted_window"]:
        hard_blocks.append("restricted_news_or_event_window")
    if is_futures_market(trade):
        if (trade["leverage"] or 0.0) > float(thresholds.get("max_leverage", 5.0)):
            hard_blocks.append("futures_leverage_above_limit")
        if liq_buffer is None:
            hard_blocks.append("missing_futures_liquidation_buffer")
        elif liq_buffer <= float(thresholds.get("min_liquidation_buffer_pct", 3.0)):
            hard_blocks.append("futures_liquidation_buffer_too_small")
        if not trade["margin_mode"]:
            warnings.append("missing_margin_mode")
        if not trade["fees_slippage_included"]:
            warnings.append("fees_slippage_not_declared")
    if trade["chasing"]:
        hard_blocks.append("no_chasing")
    if trade["averaging_down"]:
        hard_blocks.append("no_averaging_down")
    if trade["divergence_only"]:
        hard_blocks.append("divergence_without_structure_confirmation")
    hard_blocks.extend(funding_blocks(trade, thresholds))

    m15_bias = trade["m15_bias"]
    bitmaster_bias = trade["bitmaster_bias"]
    size_multiplier = 1.0
    if m15_bias in {"LONG", "SHORT"} and bitmaster_bias in {"LONG", "SHORT"} and m15_bias != bitmaster_bias:
        size_multiplier = float(thresholds.get("conflict_size_multiplier", 0.5))
        adjustments.append("m15_bitmaster_conflict_size_0.5x")

    decision = "pass_manual_review_only"
    if hard_blocks:
        decision = "blocked_do_not_trade"
        size_multiplier = 0.0
    elif warnings or adjustments:
        decision = "conditional_pass_reduced_or_review"

    return {
        "trade": trade,
        "computed": {
            "rr": rr,
            "size_multiplier": size_multiplier,
            "liquidation_buffer_pct": liq_buffer,
        },
        "decision": decision,
        "hard_blocks": sorted(set(hard_blocks)),
        "warnings": sorted(set(warnings)),
        "adjustments": sorted(set(adjustments)),
        "can_trade": False,
    }


def demo_trades() -> list[dict[str, Any]]:
    return [
        {
            "symbol": "BTCUSDT",
            "tf": "15m",
            "market": "futures",
            "side": "LONG",
            "entry": 100000,
            "stop": 99000,
            "tp": [102100, 103500],
            "risk_pct": 0.5,
            "leverage": 3,
            "margin_mode": "isolated",
            "mark_price": 100000,
            "liquidation_price": 85000,
            "fees_slippage_included": True,
            "confirmations": 3,
            "stop_method": "structure",
            "funding": 0.01,
            "m15_bias": "LONG",
            "bitmaster_bias": "LONG"
        },
        {
            "symbol": "BTCUSDT",
            "tf": "15m",
            "market": "futures",
            "side": "LONG",
            "entry": 100000,
            "stop": 99500,
            "tp": [100600],
            "risk_pct": 1.5,
            "leverage": 10,
            "mark_price": 100000,
            "liquidation_price": 99700,
            "confirmations": 1,
            "stop_method": "manual",
            "funding": 0.09,
            "chasing": True
        },
        {
            "symbol": "BTCUSDT",
            "tf": "15m",
            "market": "futures",
            "side": "SHORT",
            "entry": 100000,
            "stop": 101000,
            "tp": [98000],
            "risk_pct": 0.5,
            "leverage": 3,
            "margin_mode": "isolated",
            "mark_price": 100000,
            "liquidation_price": 115000,
            "fees_slippage_included": True,
            "confirmations": 2,
            "stop_method": "atr",
            "funding": 0.02,
            "m15_bias": "SHORT",
            "bitmaster_bias": "LONG"
        }
    ]


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Pretrade Guardian",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Local pre-trade policy gate.",
        "- It never sends orders and does not grant live permission.",
        "- A pass means manual-review eligible, not auto-execution.",
        "",
        "## Result",
        "",
        f"- Evaluated: `{report['evaluated_count']}`.",
        f"- Passed/manual-review: `{report['pass_count']}`.",
        f"- Conditional/reduced: `{report['conditional_count']}`.",
        f"- Blocked: `{report['blocked_count']}`.",
        "",
        "| Symbol | TF | Market | Side | Lev | Liq Buffer | RR | Size Mult | Decision | Hard Blocks | Warnings/Adjustments |",
        "|---|---|---|---|---:|---:|---:|---:|---|---|---|",
    ]
    for item in report["results"]:
        trade = item["trade"]
        computed = item["computed"]
        notes = ", ".join(item["warnings"] + item["adjustments"]) or "-"
        blocks = ", ".join(item["hard_blocks"]) or "-"
        lines.append(
            f"| `{trade['symbol']}` | `{trade['tf']}` | `{trade['market']}` | `{trade['side']}` | "
            f"`{trade['leverage']}` | `{computed['liquidation_buffer_pct']}` | `{computed['rr']}` | "
            f"`{computed['size_multiplier']}` | `{item['decision']}` | `{blocks}` | `{notes}` |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-trade guardian policy gate")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--input")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--out-prefix", default="docs/PRETRADE_GUARDIAN_SMOKE_2026-06-04")
    args = parser.parse_args()

    config = read_json(Path(args.config))
    if args.demo:
        trades = demo_trades()
    elif args.input:
        payload = read_json(Path(args.input))
        trades = payload if isinstance(payload, list) else [payload]
    else:
        raise SystemExit("provide --demo or --input")

    results = [evaluate_trade(trade, config) for trade in trades]
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "pretrade_policy_gate",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "config": str(Path(args.config)),
        "evaluated_count": len(results),
        "pass_count": sum(1 for item in results if item["decision"] == "pass_manual_review_only"),
        "conditional_count": sum(1 for item in results if item["decision"] == "conditional_pass_reduced_or_review"),
        "blocked_count": sum(1 for item in results if item["decision"] == "blocked_do_not_trade"),
        "results": results,
    }
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "evaluated_count": report["evaluated_count"],
                "pass_count": report["pass_count"],
                "conditional_count": report["conditional_count"],
                "blocked_count": report["blocked_count"],
                "json": str(json_path),
                "md": str(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
