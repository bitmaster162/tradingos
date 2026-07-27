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


HYPOTHESIS_ID = "HYP-MICROSTRUCTURE-BOOK-PRESSURE-004"
EXPERIMENT = "microstructure_book_pressure_alignment"
FAMILY = "CROSS_VENUE_TOP_OF_BOOK_PRESSURE_ALIGNMENT"


@dataclass(frozen=True)
class Config:
    lookback_minutes: int
    pressure_z_window_minutes: int
    entry_z: float
    alignment_mode: str
    spread_filter: str
    hold_minutes: int

    @property
    def strategy_id(self) -> str:
        return (
            f"book_pressure_lb{self.lookback_minutes}_zw{self.pressure_z_window_minutes}"
            f"_z{self.entry_z:g}_{self.alignment_mode}_sf{self.spread_filter}_h{self.hold_minutes}"
        )


@dataclass(frozen=True)
class MinuteRow:
    minute_ms: int
    binance_price_first: float
    binance_price_last: float
    binance_pressure: float
    coinbase_pressure: float
    binance_spread_bps: float | None
    coinbase_spread_bps: float | None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def minute_features_from_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def minute_features_from_sqlite(path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return [{key: row[key] for key in row.keys()} for row in conn.execute("SELECT * FROM minute_features ORDER BY minute_ms,venue")]
    finally:
        conn.close()


def load_feature_rows(cache_dir: Path) -> tuple[list[dict[str, Any]], str]:
    for name in ("minute_features.csv", "minute_features_v2.csv"):
        csv_path = cache_dir / name
        if csv_path.is_file():
            return minute_features_from_csv(csv_path), name
    db_path = cache_dir / "microstructure.sqlite3"
    if db_path.is_file():
        return minute_features_from_sqlite(db_path), "microstructure.sqlite3"
    raise FileNotFoundError(f"microstructure_features_not_found: {cache_dir}")


def aligned_minutes(rows: Iterable[dict[str, Any]]) -> list[MinuteRow]:
    by_minute: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        try:
            minute_ms = int(row["minute_ms"])
            venue = str(row["venue"])
        except (KeyError, TypeError, ValueError):
            continue
        by_minute.setdefault(minute_ms, {})[venue] = row

    aligned: list[MinuteRow] = []
    for minute_ms in sorted(by_minute):
        binance = by_minute[minute_ms].get("binance")
        coinbase = by_minute[minute_ms].get("coinbase")
        if not binance or not coinbase:
            continue
        b_first = safe_float(binance.get("price_first"))
        b_last = safe_float(binance.get("price_last"))
        b_pressure = safe_float(binance.get("avg_top_imbalance"))
        c_pressure = safe_float(coinbase.get("avg_top_imbalance"))
        if None in {b_first, b_last, b_pressure, c_pressure}:
            continue
        if b_first <= 0 or b_last <= 0:
            continue
        aligned.append(
            MinuteRow(
                minute_ms=minute_ms,
                binance_price_first=b_first,
                binance_price_last=b_last,
                binance_pressure=b_pressure,
                coinbase_pressure=c_pressure,
                binance_spread_bps=safe_float(binance.get("avg_spread_bps")),
                coinbase_spread_bps=safe_float(coinbase.get("avg_spread_bps")),
            )
        )
    return aligned


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


def rolling_mean(values: list[float], lookback: int) -> list[float | None]:
    prefix = cumulative(values)
    out: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < lookback:
            out.append(None)
            continue
        out.append(window_sum(prefix, index + 1 - lookback, index + 1) / lookback)
    return out


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


def rolling_spread_ok(rows: list[MinuteRow], window: int = 60) -> list[bool]:
    b_spreads = [row.binance_spread_bps for row in rows]
    c_spreads = [row.coinbase_spread_bps for row in rows]

    def one_side(values: list[float | None]) -> list[bool]:
        clean = [0.0 if value is None else float(value) for value in values]
        valid = [0 if value is None else 1 for value in values]
        prefix = cumulative(clean)
        valid_prefix = [0]
        for flag in valid:
            valid_prefix.append(valid_prefix[-1] + flag)
        out: list[bool] = []
        for index, value in enumerate(values):
            count = int(window_sum(valid_prefix, index - window, index))
            if value is None or count < max(10, int(window * 0.5)):
                out.append(False)
                continue
            mean = window_sum(prefix, index - window, index) / count
            out.append(value <= mean * 1.25)
        return out

    b_ok = one_side(b_spreads)
    c_ok = one_side(c_spreads)
    return [left and right for left, right in zip(b_ok, c_ok)]


def configs_from_protocol() -> list[Config]:
    lookbacks = [1, 3, 5]
    windows = [360, 720, 1440]
    entry_z = [1.5, 2.0, 2.5]
    alignment_modes = ["agreement_continuation", "divergence_reversion"]
    spread_filters = ["none", "both_not_wide"]
    holds = [1, 3, 5]
    return [
        Config(lookback, window, z, mode, spread_filter, hold)
        for lookback in lookbacks
        for window in windows
        for z in entry_z
        for mode in alignment_modes
        for spread_filter in spread_filters
        for hold in holds
    ]


def trade_return_bps(rows: list[MinuteRow], signal_index: int, hold: int, side: str) -> float | None:
    entry_index = signal_index + 1
    exit_index = entry_index + hold - 1
    if entry_index >= len(rows) or exit_index >= len(rows):
        return None
    entry = rows[entry_index].binance_price_first
    exit_price = rows[exit_index].binance_price_last
    gross = (exit_price / entry - 1.0) * 10_000
    return gross if side == "LONG" else -gross


def signal_side(cfg: Config, zscore: float, coinbase_pressure: float) -> str | None:
    confirm = 0.01
    if cfg.alignment_mode == "agreement_continuation":
        if zscore >= cfg.entry_z and coinbase_pressure >= confirm:
            return "LONG"
        if zscore <= -cfg.entry_z and coinbase_pressure <= -confirm:
            return "SHORT_RESEARCH_ONLY"
    elif cfg.alignment_mode == "divergence_reversion":
        if zscore >= cfg.entry_z and coinbase_pressure <= -confirm:
            return "SHORT_RESEARCH_ONLY"
        if zscore <= -cfg.entry_z and coinbase_pressure >= confirm:
            return "LONG"
    return None


def build_trades(
    rows: list[MinuteRow],
    cfg: Config,
    zscores: list[float | None],
    coinbase_pressure: list[float | None],
    spread_ok: list[bool],
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    blocked_until = -1
    for index, zscore in enumerate(zscores):
        c_pressure = coinbase_pressure[index] if index < len(coinbase_pressure) else None
        if index <= blocked_until or zscore is None or c_pressure is None:
            continue
        if cfg.spread_filter == "both_not_wide" and not spread_ok[index]:
            continue
        side = signal_side(cfg, zscore, c_pressure)
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
                "alignment_mode": cfg.alignment_mode,
                "binance_pressure_z": round(zscore, 6),
                "coinbase_pressure": round(c_pressure, 8),
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
    rng = random.Random(4004)
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
    spread_ok = rolling_spread_ok(train_rows)
    b_pressure_cache: dict[int, list[float | None]] = {}
    c_pressure_cache: dict[int, list[float | None]] = {}
    z_cache: dict[tuple[int, int], list[float | None]] = {}
    results: list[dict[str, Any]] = []
    qualified: list[dict[str, Any]] = []
    b_values = [row.binance_pressure for row in train_rows]
    c_values = [row.coinbase_pressure for row in train_rows]
    for cfg in configs:
        b_pressure = b_pressure_cache.setdefault(cfg.lookback_minutes, rolling_mean(b_values, cfg.lookback_minutes))
        c_pressure = c_pressure_cache.setdefault(cfg.lookback_minutes, rolling_mean(c_values, cfg.lookback_minutes))
        zscores = z_cache.setdefault((cfg.lookback_minutes, cfg.pressure_z_window_minutes), rolling_zscores(b_pressure, cfg.pressure_z_window_minutes))
        trades = build_trades(train_rows, cfg, zscores, c_pressure, spread_ok)
        summary = summarize_trades(trades, per_side_cost_bps=per_side_cost, stress_extra_per_side_bps=stress_extra)
        row = {
            "strategy_id": cfg.strategy_id,
            "config": {
                "lookback_minutes": cfg.lookback_minutes,
                "pressure_z_window_minutes": cfg.pressure_z_window_minutes,
                "entry_z": cfg.entry_z,
                "alignment_mode": cfg.alignment_mode,
                "spread_filter": cfg.spread_filter,
                "hold_minutes": cfg.hold_minutes,
            },
            "train": summary,
            "train_gate_pass": train_gate_pass(summary),
        }
        results.append(row)
        if row["train_gate_pass"]:
            qualified.append(row)
    ranked = sorted(results, key=lambda row: (row["train"]["stress_mean_net_bps"], row["train"]["mean_net_bps"], row["train"]["trades"]), reverse=True)
    qualified.sort(key=lambda row: (row["train"]["stress_mean_net_bps"], row["train"]["mean_net_bps"], row["train"]["trades"]), reverse=True)
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
            "# Microstructure Book Pressure Alignment Research",
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
    parser = argparse.ArgumentParser(description="Research-only cross-venue top-of-book pressure alignment test")
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--out-prefix", required=True)
    parser.add_argument("--lock-path", required=True)
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir).resolve()
    out_prefix = Path(args.out_prefix).resolve()
    lock_path = Path(args.lock_path).resolve()
    rows_raw, source = load_feature_rows(cache_dir)
    rows = aligned_minutes(rows_raw)
    search = run_search(rows)
    selected = search["selected"]
    decision = "candidate_requires_validation_review" if selected else "reject_no_train_qualified_microstructure_book_pressure_candidate"
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
            "aligned_minutes": len(rows),
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
