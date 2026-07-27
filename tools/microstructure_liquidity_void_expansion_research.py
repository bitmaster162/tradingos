#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


HYPOTHESIS_ID = "HYP-MICROSTRUCTURE-LIQUIDITY-VOID-003"
EXPERIMENT = "microstructure_liquidity_void_expansion"
FAMILY = "TOP_OF_BOOK_THINNING_TRADE_BURST_EXPANSION"


@dataclass(frozen=True)
class Config:
    book_z_window_minutes: int
    spread_z: float
    trade_burst_z: float
    delta_agreement: str
    hold_minutes: int

    @property
    def strategy_id(self) -> str:
        return (
            f"liquidity_void_zw{self.book_z_window_minutes}_sz{self.spread_z:g}"
            f"_tb{self.trade_burst_z:g}_da{self.delta_agreement}_h{self.hold_minutes}"
        )


@dataclass(frozen=True)
class MinuteRow:
    minute_ms: int
    price_first: float
    price_last: float
    trades: float
    notional: float
    delta_notional: float
    avg_spread_bps: float | None
    avg_top_imbalance: float | None

    @property
    def delta_ratio(self) -> float:
        return self.delta_notional / self.notional if self.notional > 0 else 0.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def minute_features_from_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def minute_features_from_sqlite(path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = []
        for row in conn.execute("SELECT * FROM minute_features ORDER BY minute_ms,venue"):
            rows.append({key: row[key] for key in row.keys()})
        return rows
    finally:
        conn.close()


def load_feature_rows(cache_dir: Path) -> tuple[list[dict[str, Any]], str]:
    csv_path = cache_dir / "minute_features.csv"
    if csv_path.is_file():
        return minute_features_from_csv(csv_path), "minute_features.csv"
    csv_path = cache_dir / "minute_features_v2.csv"
    if csv_path.is_file():
        return minute_features_from_csv(csv_path), "minute_features_v2.csv"
    db_path = cache_dir / "microstructure.sqlite3"
    if db_path.is_file():
        return minute_features_from_sqlite(db_path), "microstructure.sqlite3"
    raise FileNotFoundError(f"microstructure_features_not_found: {cache_dir}")


def binance_minutes(rows: Iterable[dict[str, Any]]) -> list[MinuteRow]:
    out: list[MinuteRow] = []
    for row in rows:
        if str(row.get("venue")) != "binance":
            continue
        minute_ms = safe_float(row.get("minute_ms"))
        price_first = safe_float(row.get("price_first"))
        price_last = safe_float(row.get("price_last"))
        trades = safe_float(row.get("trades"))
        notional = safe_float(row.get("notional"))
        delta = safe_float(row.get("delta_notional"))
        side_usable = str(row.get("aggressor_side_usable")).lower() in {"1", "true"}
        if None in {minute_ms, price_first, price_last, trades, notional, delta}:
            continue
        if not side_usable or price_first <= 0 or price_last <= 0 or trades <= 0 or notional <= 0:
            continue
        out.append(
            MinuteRow(
                minute_ms=int(minute_ms),
                price_first=price_first,
                price_last=price_last,
                trades=trades,
                notional=notional,
                delta_notional=delta,
                avg_spread_bps=safe_float(row.get("avg_spread_bps")),
                avg_top_imbalance=safe_float(row.get("avg_top_imbalance")),
            )
        )
    out.sort(key=lambda row: row.minute_ms)
    return out


def cumulative(values: list[float]) -> list[float]:
    out = [0.0]
    for value in values:
        out.append(out[-1] + value)
    return out


def window_sum(prefix: list[float], start: int, end: int) -> float:
    start = max(0, start)
    end = min(len(prefix) - 1, end)
    if end <= start:
        return 0.0
    return prefix[end] - prefix[start]


def rolling_zscores(values: list[float | None], window: int) -> list[float | None]:
    clean = [0.0 if value is None else float(value) for value in values]
    valid = [0 if value is None else 1 for value in values]
    prefix = cumulative(clean)
    prefix_sq = cumulative([value * value for value in clean])
    valid_prefix = [0]
    for flag in valid:
        valid_prefix.append(valid_prefix[-1] + flag)
    out: list[float | None] = []
    for index, value in enumerate(values):
        start = index - window
        end = index
        count = int(window_sum(valid_prefix, start, end))
        if value is None or count < max(10, int(window * 0.8)):
            out.append(None)
            continue
        total = window_sum(prefix, start, end)
        total_sq = window_sum(prefix_sq, start, end)
        mean = total / count
        variance = max(0.0, total_sq / count - mean * mean)
        stdev = math.sqrt(variance)
        out.append((value - mean) / stdev if stdev > 0 else None)
    return out


def configs_from_protocol() -> list[Config]:
    windows = [360, 720]
    spread_z = [1.5, 2.0, 2.5]
    burst_z = [1.5, 2.0]
    delta_agreement = ["required", "strong_required"]
    holds = [1, 3, 5]
    return [
        Config(window, spread, burst, agreement, hold)
        for window in windows
        for spread in spread_z
        for burst in burst_z
        for agreement in delta_agreement
        for hold in holds
    ]


def delta_side(row: MinuteRow, agreement: str) -> str | None:
    ratio = row.delta_ratio
    threshold = 0.005 if agreement == "required" else 0.02
    if abs(ratio) < threshold:
        return None
    if agreement == "strong_required" and row.avg_top_imbalance is not None:
        if ratio > 0 and row.avg_top_imbalance < -0.05:
            return None
        if ratio < 0 and row.avg_top_imbalance > 0.05:
            return None
    return "LONG" if ratio > 0 else "SHORT_RESEARCH_ONLY"


def trade_return_bps(rows: list[MinuteRow], signal_index: int, hold: int, side: str) -> float | None:
    entry_index = signal_index + 1
    exit_index = entry_index + hold - 1
    if entry_index >= len(rows) or exit_index >= len(rows):
        return None
    entry = rows[entry_index].price_first
    exit_price = rows[exit_index].price_last
    gross = (exit_price / entry - 1.0) * 10_000
    return gross if side == "LONG" else -gross


def build_trades(
    rows: list[MinuteRow], cfg: Config, spread_zscores: list[float | None], burst_zscores: list[float | None]
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    blocked_until = -1
    for index, spread_z in enumerate(spread_zscores):
        burst_z = burst_zscores[index] if index < len(burst_zscores) else None
        if index <= blocked_until or spread_z is None or burst_z is None:
            continue
        if spread_z < cfg.spread_z or burst_z < cfg.trade_burst_z:
            continue
        side = delta_side(rows[index], cfg.delta_agreement)
        if side is None:
            continue
        gross = trade_return_bps(rows, index, cfg.hold_minutes, "LONG" if side == "LONG" else "SHORT")
        if gross is None:
            continue
        entry_index = index + 1
        exit_index = entry_index + cfg.hold_minutes - 1
        trades.append(
            {
                "signal_minute_ms": rows[index].minute_ms,
                "entry_minute_ms": rows[entry_index].minute_ms,
                "exit_minute_ms": rows[exit_index].minute_ms,
                "side": side,
                "spread_z": round(spread_z, 6),
                "trade_burst_z": round(burst_z, 6),
                "delta_ratio": round(rows[index].delta_ratio, 8),
                "gross_bps": gross,
            }
        )
        blocked_until = exit_index
    return trades


def max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def positive_folds(values: list[float], folds: int = 4) -> int:
    if not values:
        return 0
    positives = 0
    for fold in range(folds):
        start = len(values) * fold // folds
        end = len(values) * (fold + 1) // folds
        positives += int(sum(values[start:end]) > 0)
    return positives


def bootstrap_probability_mean_gt_zero(values: list[float], iterations: int = 500) -> float | None:
    if not values:
        return None
    rng = random.Random(3003)
    wins = 0
    for _ in range(iterations):
        sample_mean = sum(rng.choice(values) for _ in values) / len(values)
        wins += int(sample_mean > 0)
    return wins / iterations


def summarize_trades(trades: list[dict[str, Any]], *, per_side_cost_bps: float, stress_extra_per_side_bps: float) -> dict[str, Any]:
    round_trip = per_side_cost_bps * 2.0
    stress_round_trip = (per_side_cost_bps + stress_extra_per_side_bps) * 2.0
    gross = [float(row["gross_bps"]) for row in trades]
    net = [value - round_trip for value in gross]
    stress = [value - stress_round_trip for value in gross]
    trades_count = len(net)
    return {
        "trades": trades_count,
        "winrate_pct": round(sum(value > 0 for value in net) / trades_count * 100, 6) if trades_count else 0.0,
        "mean_gross_bps": round(sum(gross) / trades_count, 6) if trades_count else 0.0,
        "mean_net_bps": round(sum(net) / trades_count, 6) if trades_count else 0.0,
        "stress_mean_net_bps": round(sum(stress) / trades_count, 6) if trades_count else 0.0,
        "positive_folds": positive_folds(net),
        "max_drawdown_bps": round(max_drawdown(net), 6),
        "bootstrap_probability_mean_gt_0": bootstrap_probability_mean_gt_zero(net),
    }


def train_gate_pass(summary: dict[str, Any]) -> bool:
    probability = summary.get("bootstrap_probability_mean_gt_0")
    return bool(
        summary["trades"] >= 40
        and summary["mean_net_bps"] >= 2.0
        and summary["positive_folds"] >= 3
        and summary["max_drawdown_bps"] >= -400.0
        and isinstance(probability, float)
        and probability >= 0.95
        and summary["stress_mean_net_bps"] > 0.0
    )


def run_search(rows: list[MinuteRow]) -> dict[str, Any]:
    configs = configs_from_protocol()
    split_index = int(len(rows) * 0.70)
    train_rows = rows[:split_index]
    per_side_cost = 10.0
    stress_extra = 5.0
    spread_cache: dict[int, list[float | None]] = {}
    burst_cache: dict[int, list[float | None]] = {}
    results: list[dict[str, Any]] = []
    qualified: list[dict[str, Any]] = []
    spreads = [row.avg_spread_bps for row in train_rows]
    trades_count = [row.trades for row in train_rows]
    for cfg in configs:
        spread_zscores = spread_cache.setdefault(cfg.book_z_window_minutes, rolling_zscores(spreads, cfg.book_z_window_minutes))
        burst_zscores = burst_cache.setdefault(cfg.book_z_window_minutes, rolling_zscores(trades_count, cfg.book_z_window_minutes))
        trades = build_trades(train_rows, cfg, spread_zscores, burst_zscores)
        summary = summarize_trades(trades, per_side_cost_bps=per_side_cost, stress_extra_per_side_bps=stress_extra)
        row = {
            "strategy_id": cfg.strategy_id,
            "config": {
                "book_z_window_minutes": cfg.book_z_window_minutes,
                "spread_z": cfg.spread_z,
                "trade_burst_z": cfg.trade_burst_z,
                "delta_agreement": cfg.delta_agreement,
                "hold_minutes": cfg.hold_minutes,
            },
            "train": summary,
            "train_gate_pass": train_gate_pass(summary),
        }
        results.append(row)
        if row["train_gate_pass"]:
            qualified.append(row)
    ranked = sorted(
        results,
        key=lambda row: (
            row["train"]["stress_mean_net_bps"],
            row["train"]["mean_net_bps"],
            row["train"]["trades"],
        ),
        reverse=True,
    )
    qualified.sort(
        key=lambda row: (
            row["train"]["stress_mean_net_bps"],
            row["train"]["mean_net_bps"],
            row["train"]["trades"],
        ),
        reverse=True,
    )
    return {
        "results": results,
        "ranked": ranked,
        "qualified": qualified,
        "selected": qualified[0] if qualified else None,
        "split_index": split_index,
        "costs": {
            "fee_and_slippage_bps_per_side": per_side_cost,
            "stress_extra_bps_per_side": stress_extra,
            "round_trip_bps": per_side_cost * 2,
            "stress_round_trip_bps": (per_side_cost + stress_extra) * 2,
            "short_side": "research_symmetric_only_not_spot_execution_permission",
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    selected = report.get("selected_on_train") or {}
    return "\n".join(
        [
            "# Microstructure Liquidity Void Expansion Research",
            "",
            f"Generated: `{report['generated_at']}`",
            "",
            f"- Decision: `{report['decision']}`.",
            f"- Tested configs: `{report['search']['tested']}`.",
            f"- Train qualified: `{report['search']['train_qualified']}`.",
            f"- Selected: `{selected.get('strategy_id')}`.",
            "- Research only. No signals, no observer registration and no orders.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Research-only liquidity void expansion test")
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--out-prefix", required=True)
    parser.add_argument("--lock-path", required=True)
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir).resolve()
    out_prefix = Path(args.out_prefix).resolve()
    lock_path = Path(args.lock_path).resolve()
    rows_raw, source = load_feature_rows(cache_dir)
    rows = binance_minutes(rows_raw)
    search = run_search(rows)
    selected = search["selected"]
    decision = "candidate_requires_validation_review" if selected else "reject_no_train_qualified_microstructure_liquidity_void_candidate"
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "hypothesis_id": HYPOTHESIS_ID,
        "experiment": EXPERIMENT,
        "family": FAMILY,
        "decision": decision,
        "data": {
            "cache_dir": str(cache_dir),
            "source": source,
            "binance_minutes": len(rows),
            "first_minute_ms": rows[0].minute_ms if rows else None,
            "last_minute_ms": rows[-1].minute_ms if rows else None,
        },
        "search": {
            "tested": len(search["results"]),
            "configs_tested": len(search["results"]),
            "train_qualified": len(search["qualified"]),
            "top_train_results_regardless_of_gate": search["ranked"][:10],
        },
        "selected_on_train": selected,
        "splits": {
            "method": "chronological_train_only_selection",
            "train_fraction": 0.70,
            "train_minutes": search["split_index"],
            "validation_opened": False,
            "oos_opened": False,
            "validation_policy": "closed_until_train_gate_and_governance_review",
            "oos_policy": "closed_until_validation_gate_passes",
        },
        "costs": search["costs"],
        "runtime_boundary": {
            "research_only": True,
            "network_required": False,
            "credentials_allowed": False,
            "signals_allowed": False,
            "observer_registration_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "locked_at": report["generated_at"],
                "hypothesis_id": HYPOTHESIS_ID,
                "experiment": EXPERIMENT,
                "family": FAMILY,
                "grid_configurations": len(search["results"]),
                "feature_source": source,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": decision, "tested": len(search["results"]), "train_qualified": len(search["qualified"]), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
