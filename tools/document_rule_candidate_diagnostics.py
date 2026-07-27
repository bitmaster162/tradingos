#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.document_rule_card_batch_tester import side_filter  # noqa: E402
from tools.event_feature_factory import (  # noqa: E402
    FeatureConfig,
    build_features,
    generate_signals,
    load_csv_by_time,
)
from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_hardening import simulate_trade  # noqa: E402


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


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def safe_float(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if not math.isnan(out) else default


def bin_value(value: Any, cuts: list[tuple[str, float, str]], default: str = "missing") -> str:
    numeric = safe_float(value)
    if math.isnan(numeric):
        return default
    for op, threshold, label in cuts:
        if op == "<" and numeric < threshold:
            return label
        if op == "<=" and numeric <= threshold:
            return label
        if op == ">=" and numeric >= threshold:
            return label
        if op == ">" and numeric > threshold:
            return label
    return "mid"


def summarize_r(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [safe_float(row.get("r_net")) for row in rows]
    values = [value for value in values if not math.isnan(value)]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    losing_streak = 0
    max_losing = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        if value <= 0:
            losing_streak += 1
            max_losing = max(max_losing, losing_streak)
        else:
            losing_streak = 0
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(values) * 100.0, 3) if values else None,
        "expectancy_r": round(sum(values) / len(values), 6) if values else None,
        "net_r_total": round(sum(values), 6) if values else 0.0,
        "avg_win_r": round(sum(wins) / len(wins), 6) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 6) if losses else None,
        "max_drawdown_r": round(max_dd, 6),
        "max_losing_streak": max_losing,
    }


def group_summary(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get(field) or "missing")
        groups.setdefault(key, []).append(row)
    out = []
    for key, subset in groups.items():
        summary = summarize_r(subset)
        out.append({"bucket": key, **summary})
    return sorted(out, key=lambda item: (item["trades"], item.get("expectancy_r") or -999.0), reverse=True)


def fold_assignments(rows: list[dict[str, Any]], folds: int) -> None:
    ordered = sorted(rows, key=lambda row: str(row["entry_ts"]))
    total = len(ordered)
    for index, row in enumerate(ordered):
        row["chronological_fold"] = int(index * folds / max(1, total)) + 1


def load_batch_candidate(path: Path, strategy_id: str | None) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    results = payload.get("all_results") or payload.get("top_results") or []
    if not results:
        raise ValueError(f"no results in {path}")
    if strategy_id:
        for result in results:
            if result.get("strategy_id") == strategy_id:
                return result
        raise ValueError(f"strategy_id not found: {strategy_id}")
    return results[0]


def build_trade_rows(candidate: dict[str, Any], args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if candidate.get("engine") != "event_feature":
        raise ValueError("diagnostics currently supports event_feature candidates")
    config = FeatureConfig(
        strategy_id=str(candidate["strategy_id"]),
        family=str(candidate["strategy_family"]),
        interval=str(candidate["interval"]),
        params=dict(candidate.get("params") or {}),
    )
    cache_dir = resolve_path(args.cache_dir)
    futures_path = cache_dir / "futures" / "BTCUSDT" / f"{config.interval}_klines.csv"
    spot_path = cache_dir / "spot" / "BTCUSDT" / f"{config.interval}_klines.csv"
    derivatives_path = cache_dir / "futures" / "BTCUSDT" / f"{config.interval}_oi_aligned.csv"
    bars = load_ohlcv(futures_path)
    spot_bars = load_ohlcv(spot_path) if spot_path.exists() else []
    features = build_features(
        bars=bars,
        spot_by_time={bar.ts: bar for bar in spot_bars},
        derivatives_by_time=load_csv_by_time(derivatives_path),
        oi_lag=args.oi_lag,
        spot_perp_lookback=args.spot_perp_lookback,
        volume_window=20,
        atr_window=20,
    )
    signals = generate_signals(config, bars, features)
    side = side_filter(str(candidate.get("trade_side") or ""))
    rows: list[dict[str, Any]] = []
    last_exit_bar = -1
    skipped = {"side_filter": 0, "overlap": 0, "bad_trade": 0}
    for signal in sorted(signals, key=lambda item: item["bar_index"]):
        signal_index = int(signal["bar_index"])
        if side is not None and str(signal.get("side_hint") or "").upper() != side:
            skipped["side_filter"] += 1
            continue
        if not args.allow_overlap and signal_index <= last_exit_bar:
            skipped["overlap"] += 1
            continue
        trade = simulate_trade(
            dataset_id=f"doc_rule_diagnostic_BTCUSDT_{config.interval}",
            strategy_id=config.strategy_id,
            bars=bars,
            signal=signal,
            stop_atr=args.stop_atr,
            take_atr=args.take_atr,
            max_hold_bars=args.max_hold_bars,
            cost_bps_per_side=args.fee_bps + args.slippage_bps,
        )
        if trade is None:
            skipped["bad_trade"] += 1
            continue
        feature = features[signal_index]
        entry_dt = parse_ts(trade.entry_ts)
        row = {
            **trade.__dict__,
            "rule_id": candidate.get("rule_id"),
            "source_rel": candidate.get("source_rel"),
            "signal_ts": bars[signal_index].ts,
            "signal_index": signal_index,
            "signal_reason": signal.get("reason"),
            "spot_perp_divergence_pct": feature.get("spot_perp_divergence_pct"),
            "oi_delta_pct": feature.get("oi_delta_pct"),
            "funding": feature.get("funding"),
            "volume_z": feature.get("volume_z"),
            "atr_ratio": feature.get("atr_ratio"),
            "body_pct": feature.get("body_pct"),
            "close_location": feature.get("close_location"),
            "range_atr": feature.get("range_atr"),
            "year": entry_dt.year,
            "month": entry_dt.strftime("%Y-%m"),
            "quarter": f"{entry_dt.year}-Q{((entry_dt.month - 1) // 3) + 1}",
            "atr_regime": bin_value(feature.get("atr_ratio"), [("<", 0.8, "atr_low"), (">", 1.2, "atr_high")], "atr_missing"),
            "volume_regime": bin_value(
                feature.get("volume_z"),
                [("<", -0.5, "volume_quiet"), ("<=", 0.5, "volume_normal"), ("<=", 1.5, "volume_active")],
                "volume_missing",
            ),
            "spot_perp_regime": bin_value(
                feature.get("spot_perp_divergence_pct"),
                [("<", -0.05, "spot_lagging"), ("<=", 0.05, "spot_neutral"), (">", 0.05, "spot_leading")],
                "spot_perp_missing",
            ),
            "oi_regime": bin_value(
                feature.get("oi_delta_pct"),
                [("<", -0.10, "oi_down"), ("<=", 0.10, "oi_flat"), (">", 0.10, "oi_up")],
                "oi_missing",
            ),
            "funding_regime": bin_value(
                feature.get("funding"),
                [("<", -0.0002, "funding_negative"), ("<=", 0.0002, "funding_flat"), (">", 0.0002, "funding_positive")],
                "funding_missing",
            ),
        }
        rows.append(row)
        if not args.allow_overlap:
            for index in range(signal_index + 1, min(len(bars), signal_index + args.max_hold_bars + 2)):
                if bars[index].ts == trade.exit_ts:
                    last_exit_bar = index
                    break
    fold_assignments(rows, args.folds)
    metadata = {
        "bars": len(bars),
        "spot_bars": len(spot_bars),
        "features": len(features),
        "signals": len(signals),
        "skipped": skipped,
        "futures_path": portable(futures_path),
        "spot_path": portable(spot_path),
        "derivatives_path": portable(derivatives_path),
    }
    return rows, metadata


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Document Rule Candidate Diagnostics",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research diagnostics only.",
        "- No orders, no credentials, no paper/live permission.",
        "- A positive slice is not promotion; it is only a candidate for a preregistered follow-up test.",
        "",
        "## Candidate",
        "",
        f"- Strategy: `{report['candidate']['strategy_id']}`",
        f"- Rule: `{report['candidate']['rule_id']}`",
        f"- Family: `{report['candidate']['strategy_family']}`",
        f"- Side filter: `{report['candidate']['trade_side']}`",
        f"- Params: `{report['candidate']['params']}`",
        "",
        "## Overall",
        "",
    ]
    overall = report["overall"]
    for key in ("trades", "winrate_pct", "expectancy_r", "net_r_total", "max_drawdown_r", "max_losing_streak"):
        lines.append(f"- {key}: `{overall.get(key)}`")
    lines.extend(["", "## Key Findings", ""])
    for finding in report["findings"]:
        lines.append(f"- {finding}")
    for section, title in (
        ("by_chronological_fold", "By Chronological Fold"),
        ("by_spot_perp_regime", "By Spot/Perp Regime"),
        ("by_oi_regime", "By OI Regime"),
        ("by_volume_regime", "By Volume Regime"),
        ("by_atr_regime", "By ATR Regime"),
        ("by_exit_reason", "By Exit Reason"),
        ("by_month_top", "Best Months"),
        ("by_month_bottom", "Worst Months"),
    ):
        lines.extend(["", f"## {title}", "", "| Bucket | Trades | Winrate | Exp R | Net R | Max DD |", "|---|---:|---:|---:|---:|---:|"])
        for item in report[section]:
            lines.append(
                "| `{}` | `{}` | `{}` | `{}` | `{}` | `{}` |".format(
                    item.get("bucket"),
                    item.get("trades"),
                    item.get("winrate_pct"),
                    item.get("expectancy_r"),
                    item.get("net_r_total"),
                    item.get("max_drawdown_r"),
                )
            )
    lines.extend(["", "## Verdict", "", f"- `{report['verdict']}`", "", f"- Next action: {report['next_action']}", ""])
    return "\n".join(lines)


def build_findings(report: dict[str, Any]) -> list[str]:
    findings = []
    fold_summaries = report["by_chronological_fold"]
    positive_folds = [item for item in fold_summaries if (item.get("expectancy_r") or 0) > 0]
    if len(positive_folds) < 3:
        findings.append(f"Fold instability: only {len(positive_folds)}/{len(fold_summaries)} chronological folds are positive.")
    worst_fold = min(fold_summaries, key=lambda item: item.get("expectancy_r") or 999)
    findings.append(f"Worst fold is {worst_fold['bucket']} with expectancy {worst_fold.get('expectancy_r')}R and net {worst_fold.get('net_r_total')}R.")
    best_spot = report["by_spot_perp_regime"][0] if report["by_spot_perp_regime"] else None
    if best_spot:
        findings.append(f"Best spot/perp bucket is {best_spot['bucket']} with {best_spot['trades']} trades and {best_spot.get('expectancy_r')}R expectancy.")
    worst_month = report["by_month_bottom"][0] if report["by_month_bottom"] else None
    if worst_month:
        findings.append(f"Worst month is {worst_month['bucket']} with net {worst_month.get('net_r_total')}R.")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostics for the best document rule-card candidate")
    parser.add_argument("--batch-report", default="docs/DOCUMENT_RULE_CARD_BATCH_TEST_RR1X3_2026-06-30.json")
    parser.add_argument("--strategy-id", default="")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", type=float, default=3.0)
    parser.add_argument("--max-hold-bars", type=int, default=24)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--oi-lag", type=int, default=4)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--out-prefix", default="docs/DOCUMENT_RULE_CANDIDATE_DIAGNOSTICS_RR1X3_2026-06-30")
    args = parser.parse_args()

    candidate = load_batch_candidate(resolve_path(args.batch_report), args.strategy_id or None)
    rows, metadata = build_trade_rows(candidate, args)
    by_month = group_summary(rows, "month")
    report = {
        "generated_at": now_iso(),
        "tool": "tools/document_rule_candidate_diagnostics.py",
        "runtime_boundary": {
            "classification": "research_diagnostics_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "candidate": {
            key: candidate.get(key)
            for key in ("rule_id", "source_rel", "card_family", "trade_side", "engine", "strategy_id", "strategy_family", "interval", "params")
        },
        "metadata": metadata,
        "overall": summarize_r(rows),
        "by_chronological_fold": group_summary(rows, "chronological_fold"),
        "by_side": group_summary(rows, "side"),
        "by_exit_reason": group_summary(rows, "exit_reason"),
        "by_spot_perp_regime": group_summary(rows, "spot_perp_regime"),
        "by_oi_regime": group_summary(rows, "oi_regime"),
        "by_funding_regime": group_summary(rows, "funding_regime"),
        "by_volume_regime": group_summary(rows, "volume_regime"),
        "by_atr_regime": group_summary(rows, "atr_regime"),
        "by_quarter": group_summary(rows, "quarter"),
        "by_month_top": sorted(by_month, key=lambda item: item.get("net_r_total") or -999, reverse=True)[:12],
        "by_month_bottom": sorted(by_month, key=lambda item: item.get("net_r_total") or 999)[:12],
        "all_trades_csv": "",
        "can_trade": False,
    }
    report["findings"] = build_findings(report)
    positive_folds = [item for item in report["by_chronological_fold"] if (item.get("expectancy_r") or 0) > 0]
    verdict = "blocked_fold_instability"
    next_action = "do not promote; test a preregistered filter only if it removes the losing fold without relying on future information"
    if len(positive_folds) >= 3 and (report["overall"].get("expectancy_r") or 0) > 0.10:
        verdict = "diagnostic_watchlist_needs_preregistered_filter"
        next_action = "define one fixed regime filter from diagnostics and run sealed validation; no trade permission"
    report["verdict"] = verdict
    report["next_action"] = next_action

    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    csv_path = out_prefix.with_name(out_prefix.name + "_trades").with_suffix(".csv")
    write_csv(rows, csv_path)
    report["all_trades_csv"] = portable(csv_path)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "strategy_id": report["candidate"]["strategy_id"],
                "trades": report["overall"]["trades"],
                "expectancy_r": report["overall"]["expectancy_r"],
                "positive_folds": len(positive_folds),
                "verdict": verdict,
                "json": portable(json_path),
                "md": portable(md_path),
                "trades_csv": portable(csv_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
