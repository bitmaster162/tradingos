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


HYPOTHESIS_ID = "HYP-MICROSTRUCTURE-IMBALANCE-001"
EXPERIMENT = "microstructure_imbalance_continuation"
FAMILY = "BINANCE_AGGRESSOR_DELTA_COINBASE_CONFIRMATION"


@dataclass(frozen=True)
class Config:
    lookback_minutes: int
    imbalance_z_window_minutes: int
    entry_z: float
    coinbase_confirm_bps: float
    hold_minutes: int

    @property
    def strategy_id(self) -> str:
        return (
            f"imbalance_lb{self.lookback_minutes}_zw{self.imbalance_z_window_minutes}"
            f"_z{self.entry_z:g}_cb{self.coinbase_confirm_bps:g}_h{self.hold_minutes}"
        )


@dataclass(frozen=True)
class MinuteRow:
    minute_ms: int
    binance_price_first: float
    binance_price_last: float
    binance_notional: float
    binance_delta_notional: float
    coinbase_return_bps: float
    binance_return_bps: float


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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
        if str(binance.get("aggressor_side_usable")).lower() not in {"1", "true"}:
            continue
        b_first = safe_float(binance.get("price_first"))
        b_last = safe_float(binance.get("price_last"))
        b_notional = safe_float(binance.get("notional"))
        b_delta = safe_float(binance.get("delta_notional"))
        b_ret = safe_float(binance.get("return_bps"))
        c_ret = safe_float(coinbase.get("return_bps"))
        if None in {b_first, b_last, b_notional, b_delta, b_ret, c_ret}:
            continue
        if b_first <= 0 or b_last <= 0 or b_notional <= 0:
            continue
        aligned.append(
            MinuteRow(
                minute_ms=minute_ms,
                binance_price_first=b_first,
                binance_price_last=b_last,
                binance_notional=b_notional,
                binance_delta_notional=b_delta,
                coinbase_return_bps=c_ret,
                binance_return_bps=b_ret,
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


def rolling_ratios(rows: list[MinuteRow], lookback: int) -> list[float | None]:
    delta_prefix = cumulative([row.binance_delta_notional for row in rows])
    notional_prefix = cumulative([row.binance_notional for row in rows])
    ratios: list[float | None] = []
    for index in range(len(rows)):
        start = index + 1 - lookback
        delta = window_sum(delta_prefix, start, index + 1)
        notional = window_sum(notional_prefix, start, index + 1)
        ratios.append(delta / notional if notional > 0 and index + 1 >= lookback else None)
    return ratios


def rolling_zscores(values: list[float | None], window: int) -> list[float | None]:
    clean = [0.0 if value is None else float(value) for value in values]
    valid = [0 if value is None else 1 for value in values]
    prefix = cumulative(clean)
    prefix_sq = cumulative([value * value for value in clean])
    valid_prefix = [0]
    for flag in valid:
        valid_prefix.append(valid_prefix[-1] + flag)
    zscores: list[float | None] = []
    for index, value in enumerate(values):
        start = index - window
        end = index
        count = int(window_sum(valid_prefix, start, end))
        if value is None or count < max(10, int(window * 0.8)):
            zscores.append(None)
            continue
        total = window_sum(prefix, start, end)
        total_sq = window_sum(prefix_sq, start, end)
        mean = total / count
        variance = max(0.0, total_sq / count - mean * mean)
        stdev = math.sqrt(variance)
        zscores.append((value - mean) / stdev if stdev > 0 else None)
    return zscores


def configs_from_protocol() -> list[Config]:
    lookbacks = [1, 3, 5, 10]
    windows = [360, 720, 1440]
    entry_z = [1.5, 2.0, 2.5]
    coinbase_confirm = [0.0, 2.5]
    holds = [1, 3, 5]
    return [
        Config(lookback, window, z, confirm, hold)
        for lookback in lookbacks
        for window in windows
        for z in entry_z
        for confirm in coinbase_confirm
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


def build_trades(rows: list[MinuteRow], cfg: Config, zscores: list[float | None]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    blocked_until = -1
    for index, zscore in enumerate(zscores):
        if index <= blocked_until or zscore is None:
            continue
        coinbase_ret = rows[index].coinbase_return_bps
        side = None
        if zscore >= cfg.entry_z and coinbase_ret >= cfg.coinbase_confirm_bps:
            side = "LONG"
        elif zscore <= -cfg.entry_z and coinbase_ret <= -cfg.coinbase_confirm_bps:
            side = "SHORT_RESEARCH_ONLY"
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
                "zscore": round(zscore, 6),
                "coinbase_return_bps": round(coinbase_ret, 6),
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
        if sum(values[start:end]) > 0:
            positives += 1
    return positives


def bootstrap_probability_mean_gt_zero(values: list[float], iterations: int = 500) -> float | None:
    if not values:
        return None
    rng = random.Random(1337)
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
        summary["trades"] >= 80
        and summary["mean_net_bps"] >= 1.5
        and summary["positive_folds"] >= 3
        and summary["max_drawdown_bps"] >= -500.0
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
    ratio_cache: dict[int, list[float | None]] = {}
    z_cache: dict[tuple[int, int], list[float | None]] = {}
    results: list[dict[str, Any]] = []
    qualified: list[dict[str, Any]] = []
    for cfg in configs:
        ratios = ratio_cache.setdefault(cfg.lookback_minutes, rolling_ratios(train_rows, cfg.lookback_minutes))
        zscores = z_cache.setdefault((cfg.lookback_minutes, cfg.imbalance_z_window_minutes), rolling_zscores(ratios, cfg.imbalance_z_window_minutes))
        trades = build_trades(train_rows, cfg, zscores)
        summary = summarize_trades(trades, per_side_cost_bps=per_side_cost, stress_extra_per_side_bps=stress_extra)
        row = {
            "strategy_id": cfg.strategy_id,
            "config": {
                "lookback_minutes": cfg.lookback_minutes,
                "imbalance_z_window_minutes": cfg.imbalance_z_window_minutes,
                "entry_z": cfg.entry_z,
                "coinbase_confirm_bps": cfg.coinbase_confirm_bps,
                "hold_minutes": cfg.hold_minutes,
            },
            "train": summary,
            "train_gate_pass": train_gate_pass(summary),
        }
        results.append(row)
        if row["train_gate_pass"]:
            qualified.append(row)
    qualified.sort(
        key=lambda row: (
            row["train"]["stress_mean_net_bps"],
            row["train"]["mean_net_bps"],
            row["train"]["trades"],
        ),
        reverse=True,
    )
    ranked = sorted(
        results,
        key=lambda row: (
            row["train"]["stress_mean_net_bps"],
            row["train"]["mean_net_bps"],
            row["train"]["trades"],
        ),
        reverse=True,
    )
    selected = qualified[0] if qualified else None
    return {
        "configs": configs,
        "results": results,
        "ranked": ranked,
        "qualified": qualified,
        "selected": selected,
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
    lines = [
        "# Microstructure Imbalance Continuation Research",
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
    return "\n".join(lines)


def write_lock(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Research-only Binance aggressor imbalance continuation test")
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
    decision = "candidate_requires_validation_review" if selected else "reject_no_train_qualified_microstructure_imbalance_candidate"
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
    write_lock(
        lock_path,
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
    )
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": decision, "tested": len(search["results"]), "train_qualified": len(search["qualified"]), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
