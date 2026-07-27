#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    if not fields:
        fields = ["signal_key", "status", "r_net", "reason"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_bars(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    bars: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                bar = {
                    "time": str(row.get("time") or row.get("ts") or row.get("datetime") or "").strip(),
                    "open": float(row.get("open", "nan")),
                    "high": float(row.get("high", "nan")),
                    "low": float(row.get("low", "nan")),
                    "close": float(row.get("close", "nan")),
                }
            except (TypeError, ValueError):
                continue
            if bar["time"] and all(math.isfinite(float(bar[key])) for key in ("open", "high", "low", "close")):
                bars.append(bar)
    return bars


def safe_float(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return round(worst, 6)


def max_losing_streak(values: list[float]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def resolve_outcome(card: dict[str, Any], bars: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    signal_ts = str(card.get("signal_bar_ts") or "")
    side = str(card.get("side") or "").upper()
    observation = card.get("observation") if isinstance(card.get("observation"), dict) else {}
    atr = safe_float(observation.get("atr"))
    stop_atr = safe_float(card.get("stop_atr"), args.stop_atr)
    take_atr = safe_float(card.get("take_atr"), args.take_atr)
    max_hold_bars = int(card.get("max_hold_bars") or args.max_hold_bars)
    base = {
        "signal_key": card.get("signal_key"),
        "hypothesis_id": card.get("hypothesis_id"),
        "strategy_id": card.get("strategy_id"),
        "symbol": card.get("symbol"),
        "interval": card.get("interval"),
        "side": side,
        "signal_bar_ts": signal_ts,
        "atr": atr if math.isfinite(atr) else None,
        "stop_atr": stop_atr if math.isfinite(stop_atr) else None,
        "take_atr": take_atr if math.isfinite(take_atr) else None,
        "max_hold_bars": max_hold_bars,
        "volume_regime": observation.get("volume_regime"),
        "volume_z": observation.get("volume_z"),
        "spot_perp_divergence_pct": observation.get("spot_perp_divergence_pct"),
        "oi_delta_pct": observation.get("oi_delta_pct"),
        "funding": observation.get("funding"),
    }
    if side not in {"LONG", "SHORT"}:
        return {**base, "status": "unresolved", "r_net": None, "reason": "unsupported_side"}
    if not signal_ts:
        return {**base, "status": "unresolved", "r_net": None, "reason": "missing_signal_ts"}
    if not math.isfinite(atr) or atr <= 0 or not math.isfinite(stop_atr) or stop_atr <= 0 or not math.isfinite(take_atr) or take_atr <= 0:
        return {**base, "status": "unresolved", "r_net": None, "reason": "invalid_risk_inputs"}
    index_by_ts = {str(bar["time"]): index for index, bar in enumerate(bars)}
    signal_index = index_by_ts.get(signal_ts)
    if signal_index is None:
        return {**base, "status": "unresolved", "r_net": None, "reason": "signal_bar_not_in_cache"}
    entry_index = signal_index + 1
    if entry_index >= len(bars):
        return {**base, "status": "unresolved", "r_net": None, "reason": "entry_bar_not_closed_yet"}

    entry_bar = bars[entry_index]
    entry = float(entry_bar["open"])
    risk = atr * stop_atr
    reward = atr * take_atr
    if side == "LONG":
        stop = entry - risk
        take = entry + reward
    else:
        stop = entry + risk
        take = entry - reward

    end_index = min(len(bars) - 1, entry_index + max_hold_bars)
    for index in range(entry_index, end_index + 1):
        bar = bars[index]
        high = float(bar["high"])
        low = float(bar["low"])
        if side == "LONG":
            stop_hit = low <= stop
            take_hit = high >= take
        else:
            stop_hit = high >= stop
            take_hit = low <= take
        if stop_hit and take_hit:
            exit_price = stop if args.same_bar_policy == "conservative_stop" else None
            if exit_price is None:
                return {
                    **base,
                    "status": "ambiguous",
                    "r_net": None,
                    "entry_ts": entry_bar["time"],
                    "entry": round(entry, 8),
                    "stop": round(stop, 8),
                    "take": round(take, 8),
                    "exit_ts": bar["time"],
                    "exit": None,
                    "bars_held": index - entry_index + 1,
                    "reason": "same_bar_stop_and_take",
                }
            return score_exit(base, entry_bar["time"], entry, stop, take, bar["time"], exit_price, index - entry_index + 1, "same_bar_conservative_stop", side, risk, args)
        if stop_hit:
            return score_exit(base, entry_bar["time"], entry, stop, take, bar["time"], stop, index - entry_index + 1, "stop_loss", side, risk, args)
        if take_hit:
            return score_exit(base, entry_bar["time"], entry, stop, take, bar["time"], take, index - entry_index + 1, "take_profit", side, risk, args)

    if len(bars) - entry_index <= max_hold_bars:
        return {
            **base,
            "status": "unresolved",
            "r_net": None,
            "entry_ts": entry_bar["time"],
            "entry": round(entry, 8),
            "stop": round(stop, 8),
            "take": round(take, 8),
            "reason": "max_hold_not_reached_yet",
        }
    exit_bar = bars[end_index]
    return score_exit(base, entry_bar["time"], entry, stop, take, exit_bar["time"], float(exit_bar["close"]), end_index - entry_index + 1, "time_exit", side, risk, args)


def score_exit(
    base: dict[str, Any],
    entry_ts: str,
    entry: float,
    stop: float,
    take: float,
    exit_ts: str,
    exit_price: float,
    bars_held: int,
    reason: str,
    side: str,
    risk: float,
    args: argparse.Namespace,
) -> dict[str, Any]:
    gross_r = (exit_price - entry) / risk if side == "LONG" else (entry - exit_price) / risk
    cost_bps_per_side = args.fee_bps + args.slippage_bps
    cost_quote = (entry + exit_price) * cost_bps_per_side / 10_000.0
    cost_r = cost_quote / risk
    r_net = gross_r - cost_r
    return {
        **base,
        "status": "win" if r_net > 0 else "loss",
        "r_net": round(r_net, 6),
        "gross_r": round(gross_r, 6),
        "cost_r": round(cost_r, 6),
        "entry_ts": entry_ts,
        "entry": round(entry, 8),
        "stop": round(stop, 8),
        "take": round(take, 8),
        "exit_ts": exit_ts,
        "exit": round(exit_price, 8),
        "bars_held": bars_held,
        "reason": reason,
    }


def summarize(outcomes: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    resolved = [row for row in outcomes if isinstance(row.get("r_net"), (int, float))]
    values = [float(row["r_net"]) for row in resolved]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    unresolved = [row for row in outcomes if row.get("status") == "unresolved"]
    ambiguous = [row for row in outcomes if row.get("status") == "ambiguous"]
    winrate = round(len(wins) / len(values) * 100.0, 3) if values else None
    expectancy = round(sum(values) / len(values), 6) if values else None
    breakeven = round(100.0 / (1.0 + (args.take_atr / args.stop_atr)), 3)
    if not outcomes:
        classification = "no_forward_signals_yet"
    elif not values:
        classification = "pending_only"
    elif len(values) < args.min_resolved:
        classification = "positive_insufficient_forward_sample" if expectancy is not None and expectancy >= args.min_expectancy_r else "insufficient_forward_sample"
    elif expectancy is not None and expectancy >= args.min_expectancy_r and (winrate or 0.0) >= breakeven:
        classification = "forward_candidate_for_design_review"
    else:
        classification = "forward_negative_or_mixed"
    return {
        "classification": classification,
        "signals": len(outcomes),
        "resolved": len(values),
        "unresolved": len(unresolved),
        "ambiguous": len(ambiguous),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": winrate,
        "expectancy_r": expectancy,
        "net_r_total": round(sum(values), 6) if values else None,
        "avg_win_r": round(sum(wins) / len(wins), 6) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 6) if losses else None,
        "max_drawdown_r": max_drawdown(values) if values else None,
        "max_losing_streak": max_losing_streak(values) if values else None,
        "breakeven_winrate_pct": breakeven,
        "min_resolved_required": args.min_resolved,
        "min_expectancy_r_required": args.min_expectancy_r,
    }


def group_summary(outcomes: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in outcomes:
        groups.setdefault(str(row.get(field) or "missing"), []).append(row)
    result = []
    for key, rows in groups.items():
        values = [float(row["r_net"]) for row in rows if isinstance(row.get("r_net"), (int, float))]
        wins = [value for value in values if value > 0]
        result.append(
            {
                "bucket": key,
                "signals": len(rows),
                "resolved": len(values),
                "winrate_pct": round(len(wins) / len(values) * 100.0, 3) if values else None,
                "expectancy_r": round(sum(values) / len(values), 6) if values else None,
                "net_r_total": round(sum(values), 6) if values else None,
            }
        )
    return sorted(result, key=lambda item: (item["resolved"], item.get("expectancy_r") or -999.0), reverse=True)


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Document Rule Forward Scoreboard",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Scores watch-only forward observer signals.",
        "- No private keys, no orders, no paper/live permission.",
        "- Uses next 1h open after signal bar and the frozen RR 1:3 policy.",
        "",
        "## Summary",
        "",
        f"- Classification: `{summary['classification']}`",
        f"- Signals: `{summary['signals']}`",
        f"- Resolved / unresolved / ambiguous: `{summary['resolved']}` / `{summary['unresolved']}` / `{summary['ambiguous']}`",
        f"- Winrate: `{summary['winrate_pct']}`",
        f"- Expectancy: `{summary['expectancy_r']}` R",
        f"- Net R: `{summary['net_r_total']}`",
        f"- Max DD: `{summary['max_drawdown_r']}` R",
        f"- Breakeven WR: `{summary['breakeven_winrate_pct']}`%",
        "",
        "## By Exit Reason",
        "",
        "| Bucket | Signals | Resolved | Winrate | Exp R | Net R |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in report["by_exit_reason"]:
        lines.append(f"| `{item['bucket']}` | `{item['signals']}` | `{item['resolved']}` | `{item['winrate_pct']}` | `{item['expectancy_r']}` | `{item['net_r_total']}` |")
    lines.extend(["", "## Recent Outcomes", "", "| Signal Bar | Status | R | Reason | Exit |", "|---|---|---:|---|---|"])
    for item in report["recent_outcomes"]:
        lines.append(f"| `{item.get('signal_bar_ts')}` | `{item.get('status')}` | `{item.get('r_net')}` | `{item.get('reason')}` | `{item.get('exit_ts')}` |")
    lines.extend(["", "## Decision", "", f"- `{report['decision']}`", "", f"- Next: {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward scoreboard for document-rule watch-only observer")
    parser.add_argument("--journal-path", default="logs/document_rule_forward_observer/signals.jsonl")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", type=float, default=3.0)
    parser.add_argument("--max-hold-bars", type=int, default=24)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--same-bar-policy", choices=["conservative_stop", "ignore_ambiguous"], default="conservative_stop")
    parser.add_argument("--min-resolved", type=int, default=30)
    parser.add_argument("--min-expectancy-r", type=float, default=0.10)
    parser.add_argument("--out-prefix", default="docs/DOCUMENT_RULE_FORWARD_SCOREBOARD_2026-06-30")
    args = parser.parse_args()

    journal_path = resolve_path(args.journal_path)
    cache_dir = resolve_path(args.cache_dir)
    bars_path = cache_dir / "futures" / args.symbol / f"{args.interval}_klines.csv"
    signals = read_jsonl(journal_path)
    bars = load_bars(bars_path)
    outcomes = [resolve_outcome(card, bars, args) for card in signals]
    summary = summarize(outcomes, args)
    decision = summary["classification"]
    next_action = (
        "keep collecting forward signals until min_resolved is reached"
        if summary["resolved"] < args.min_resolved
        else "review forward evidence; no trade permission from this scorer alone"
    )
    report = {
        "generated_at": now_iso(),
        "tool": "tools/document_rule_forward_scoreboard.py",
        "runtime_boundary": {
            "classification": "watch_only_forward_scoreboard",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "journal_path": portable(journal_path),
        "bars_path": portable(bars_path),
        "bars": len(bars),
        "summary": summary,
        "outcomes": outcomes,
        "recent_outcomes": outcomes[-20:],
        "by_exit_reason": group_summary(outcomes, "reason"),
        "by_volume_regime": group_summary(outcomes, "volume_regime"),
        "by_signal_month": group_summary([{**row, "signal_month": str(row.get("signal_bar_ts") or "")[:7]} for row in outcomes], "signal_month"),
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    csv_path = out_prefix.with_name(out_prefix.name + "_outcomes").with_suffix(".csv")
    write_json(json_path, report)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    write_csv(csv_path, outcomes)
    print(
        json.dumps(
            {
                "decision": decision,
                "signals": summary["signals"],
                "resolved": summary["resolved"],
                "expectancy_r": summary["expectancy_r"],
                "json": portable(json_path),
                "md": portable(md_path),
                "outcomes_csv": portable(csv_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
