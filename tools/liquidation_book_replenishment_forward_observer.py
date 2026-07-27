#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
MINUTE_MS = 60_000


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


def read_json(path: str | Path) -> dict[str, Any]:
    resolved = resolve_path(path)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_ts_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("quantile requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, probability)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def validate_lock(lock: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    boundary = lock.get("runtime_boundary") if isinstance(lock.get("runtime_boundary"), dict) else {}
    if lock.get("status") != "prospective_forward_lock_before_outcome_review":
        failures.append("lock_status")
    if parse_ts_ms(lock.get("forward_start_at")) is None:
        failures.append("forward_start_at")
    if lock.get("can_trade") is not False or lock.get("orders_allowed") is not False:
        failures.append("top_level_runtime_boundary")
    required_false = (
        "paper_entries_allowed",
        "live_entries_allowed",
        "sends_orders",
        "uses_private_credentials",
        "can_trade",
    )
    if any(boundary.get(key) is not False for key in required_false):
        failures.append("runtime_boundary")
    rules = lock.get("fixed_rules") if isinstance(lock.get("fixed_rules"), dict) else {}
    horizons = rules.get("outcome_horizons_minutes")
    if not isinstance(horizons, list) or not horizons or any(as_int(item) <= 0 for item in horizons):
        failures.append("outcome_horizons")
    return failures


def iter_jsonl(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in sorted(paths):
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        yield payload
        except OSError:
            continue


def load_liquidation_minutes(
    liquidation_root: Path,
    *,
    symbol: str,
    forward_start_ms: int,
    minimum_side_dominance_ratio: float,
) -> list[dict[str, Any]]:
    symbol_root = liquidation_root / symbol
    grouped: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"BUY": 0.0, "SELL": 0.0, "BUY_count": 0, "SELL_count": 0}
    )
    seen: set[tuple[Any, ...]] = set()
    for row in iter_jsonl(symbol_root.glob("*.jsonl")):
        if row.get("symbol") != symbol or row.get("is_real_liquidation_feed") is not True:
            continue
        event_ms = as_int(row.get("event_time_ms"), -1)
        side = str(row.get("side") or "").upper()
        if event_ms < forward_start_ms or side not in {"BUY", "SELL"}:
            continue
        dedupe_key = (
            event_ms,
            row.get("trade_time_ms"),
            side,
            row.get("price"),
            row.get("quantity"),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        minute_ms = event_ms - (event_ms % MINUTE_MS)
        notional = max(0.0, as_float(row.get("notional_usd")))
        grouped[minute_ms][side] += notional
        grouped[minute_ms][f"{side}_count"] += 1

    result: list[dict[str, Any]] = []
    for minute_ms, values in sorted(grouped.items()):
        buy = as_float(values["BUY"])
        sell = as_float(values["SELL"])
        dominant_side = "BUY" if buy > sell else "SELL"
        dominant = max(buy, sell)
        opposite = min(buy, sell)
        ratio = dominant / opposite if opposite > 0 else math.inf
        if ratio < minimum_side_dominance_ratio:
            continue
        result.append(
            {
                "minute_ms": minute_ms,
                "event_time": iso_from_ms(minute_ms),
                "liquidation_side": dominant_side,
                "dominant_notional_usd": round(dominant, 8),
                "opposite_notional_usd": round(opposite, 8),
                "side_dominance_ratio": None if math.isinf(ratio) else round(ratio, 8),
                "event_count": as_int(values[f"{dominant_side}_count"]),
            }
        )
    return result


FEATURE_COLUMNS = (
    "minute_ms",
    "trades",
    "notional",
    "price_first",
    "price_last",
    "return_bps",
    "book_snapshots",
    "avg_spread_bps",
    "avg_top_imbalance",
)


def load_features(database: Path, start_ms: int) -> dict[str, dict[int, dict[str, Any]]]:
    result: dict[str, dict[int, dict[str, Any]]] = {"binance": {}, "coinbase": {}}
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=20)
    try:
        for venue, product in (("binance", "BTCUSDT"), ("coinbase", "BTC-USD")):
            sql = (
                "select "
                + ",".join(FEATURE_COLUMNS)
                + " from minute_features where venue=? and product=? and minute_ms>=? order by minute_ms"
            )
            for values in connection.execute(sql, (venue, product, start_ms)):
                row = dict(zip(FEATURE_COLUMNS, values))
                result[venue][as_int(row["minute_ms"])] = row
    finally:
        connection.close()
    return result


def mean_field(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [as_float(row.get(field), math.nan) for row in rows]
    values = [value for value in values if math.isfinite(value)]
    return statistics.fmean(values) if values else None


def select_rows(
    feature_map: dict[int, dict[str, Any]],
    minute_values: Iterable[int],
    *,
    require_book: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for minute_ms in minute_values:
        row = feature_map.get(minute_ms)
        if not row:
            continue
        if require_book and as_int(row.get("book_snapshots")) <= 0:
            continue
        rows.append(row)
    return rows


def build_signals(
    events: list[dict[str, Any]],
    features: dict[str, dict[int, dict[str, Any]]],
    rules: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    min_prior = as_int(rules.get("minimum_prior_event_minutes"), 20)
    rolling_minutes = as_int(rules.get("rolling_event_minutes"), 1440)
    probability = as_float(rules.get("burst_notional_quantile"), 0.75)
    absolute_floor = as_float(rules.get("minimum_burst_notional_usd"), 2500.0)
    pre_count = as_int(rules.get("pre_event_minutes"), 5)
    post_count = as_int(rules.get("confirmation_minutes"), 3)
    min_post_books = as_int(rules.get("minimum_post_book_minutes"), post_count)
    min_abs_imbalance = as_float(rules.get("minimum_abs_post_imbalance"), 0.05)
    min_recovery = as_float(rules.get("minimum_imbalance_recovery"), 0.10)
    max_spread_ratio = as_float(rules.get("maximum_spread_ratio_to_pre"), 1.25)
    nonconfirmation_bps = as_float(rules.get("coinbase_nonconfirmation_bps"), 5.0)
    side_mapping = rules.get("side_mapping") if isinstance(rules.get("side_mapping"), dict) else {}

    signals: list[dict[str, Any]] = []
    counters: dict[str, int] = defaultdict(int)
    prior_events: list[dict[str, Any]] = []
    for event in events:
        event_minute = as_int(event.get("minute_ms"))
        lower_bound = event_minute - rolling_minutes * MINUTE_MS
        prior_events = [row for row in prior_events if as_int(row.get("minute_ms")) >= lower_bound]
        if len(prior_events) < min_prior:
            counters["calibration_wait"] += 1
            prior_events.append(event)
            continue
        threshold = max(
            absolute_floor,
            quantile([as_float(row.get("dominant_notional_usd")) for row in prior_events], probability),
        )
        if as_float(event.get("dominant_notional_usd")) < threshold:
            counters["below_burst_threshold"] += 1
            prior_events.append(event)
            continue

        pre_minutes = [event_minute - offset * MINUTE_MS for offset in range(pre_count, 0, -1)]
        post_minutes = [event_minute + offset * MINUTE_MS for offset in range(1, post_count + 1)]
        pre_binance = select_rows(features["binance"], pre_minutes, require_book=True)
        post_binance = select_rows(features["binance"], post_minutes, require_book=True)
        post_coinbase = select_rows(features["coinbase"], post_minutes, require_book=False)
        if len(pre_binance) < min(3, pre_count) or len(post_binance) < min_post_books or len(post_coinbase) < post_count:
            counters["incomplete_confirmation_features"] += 1
            prior_events.append(event)
            continue

        pre_imbalance = mean_field(pre_binance, "avg_top_imbalance")
        post_imbalance = mean_field(post_binance, "avg_top_imbalance")
        pre_spread = mean_field(pre_binance, "avg_spread_bps")
        post_spread = mean_field(post_binance, "avg_spread_bps")
        coinbase_first = as_float(post_coinbase[0].get("price_first"), math.nan)
        coinbase_last = as_float(post_coinbase[-1].get("price_last"), math.nan)
        entry_price = as_float(post_binance[-1].get("price_last"), math.nan)
        if not all(
            value is not None and math.isfinite(float(value))
            for value in (pre_imbalance, post_imbalance, pre_spread, post_spread)
        ) or not all(math.isfinite(value) and value > 0 for value in (coinbase_first, coinbase_last, entry_price)):
            counters["invalid_confirmation_features"] += 1
            prior_events.append(event)
            continue

        assert pre_imbalance is not None and post_imbalance is not None
        assert pre_spread is not None and post_spread is not None
        coinbase_return_bps = (coinbase_last / coinbase_first - 1.0) * 10_000.0
        spread_ok = pre_spread > 0 and post_spread / pre_spread <= max_spread_ratio
        liquidation_side = str(event.get("liquidation_side"))
        if liquidation_side == "SELL":
            book_ok = post_imbalance >= min_abs_imbalance and post_imbalance - pre_imbalance >= min_recovery
            venue_ok = coinbase_return_bps >= -nonconfirmation_bps
        else:
            book_ok = post_imbalance <= -min_abs_imbalance and post_imbalance - pre_imbalance <= -min_recovery
            venue_ok = coinbase_return_bps <= nonconfirmation_bps
        if not spread_ok:
            counters["spread_not_recovered"] += 1
        if not book_ok:
            counters["book_not_replenished"] += 1
        if not venue_ok:
            counters["coinbase_confirmed_forced_move"] += 1
        if spread_ok and book_ok and venue_ok:
            confirmation_minute = event_minute + post_count * MINUTE_MS
            signals.append(
                {
                    "event_id": f"{event_minute}:{liquidation_side}",
                    "event_minute_ms": event_minute,
                    "event_time": iso_from_ms(event_minute),
                    "confirmation_minute_ms": confirmation_minute,
                    "confirmation_time": iso_from_ms(confirmation_minute),
                    "liquidation_side": liquidation_side,
                    "observer_side": side_mapping.get(liquidation_side),
                    "dominant_notional_usd": event.get("dominant_notional_usd"),
                    "event_count": event.get("event_count"),
                    "burst_threshold_usd": round(threshold, 8),
                    "pre_imbalance": round(pre_imbalance, 8),
                    "post_imbalance": round(post_imbalance, 8),
                    "pre_spread_bps": round(pre_spread, 8),
                    "post_spread_bps": round(post_spread, 8),
                    "coinbase_confirmation_return_bps": round(coinbase_return_bps, 8),
                    "entry_price": entry_price,
                    "entry_spread_bps": post_binance[-1].get("avg_spread_bps"),
                }
            )
            counters["signals"] += 1
        prior_events.append(event)
    return signals, dict(counters)


def build_outcomes(
    signals: list[dict[str, Any]],
    features: dict[str, dict[int, dict[str, Any]]],
    rules: dict[str, Any],
    lock_id: str,
) -> list[dict[str, Any]]:
    horizons = [as_int(value) for value in rules.get("outcome_horizons_minutes", [])]
    cost_per_side = as_float(rules.get("fee_and_slippage_bps_per_side"), 7.0)
    outcomes: list[dict[str, Any]] = []
    for signal in signals:
        entry = as_float(signal.get("entry_price"), math.nan)
        side = str(signal.get("observer_side"))
        direction = 1.0 if side == "LONG" else -1.0
        for horizon in horizons:
            exit_minute = as_int(signal.get("confirmation_minute_ms")) + horizon * MINUTE_MS
            exit_row = features["binance"].get(exit_minute)
            exit_price = as_float((exit_row or {}).get("price_last"), math.nan)
            if not math.isfinite(entry) or entry <= 0 or not math.isfinite(exit_price) or exit_price <= 0:
                continue
            gross_bps = direction * (exit_price / entry - 1.0) * 10_000.0
            entry_spread = max(0.0, as_float(signal.get("entry_spread_bps")))
            exit_spread = max(0.0, as_float((exit_row or {}).get("avg_spread_bps")))
            spread_cost = 0.5 * (entry_spread + exit_spread)
            net_bps = gross_bps - 2.0 * cost_per_side - spread_cost
            outcomes.append(
                {
                    "lock_id": lock_id,
                    "outcome_key": f"{signal['event_id']}:{horizon}",
                    "event_id": signal["event_id"],
                    "event_time": signal["event_time"],
                    "confirmation_time": signal["confirmation_time"],
                    "resolved_at": iso_from_ms(exit_minute),
                    "liquidation_side": signal["liquidation_side"],
                    "observer_side": side,
                    "horizon_minutes": horizon,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "gross_bps": round(gross_bps, 8),
                    "cost_bps": round(2.0 * cost_per_side + spread_cost, 8),
                    "net_bps": round(net_bps, 8),
                    "can_trade": False,
                }
            )
    return outcomes


def read_ledger(path: Path, lock_id: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [row for row in iter_jsonl([path]) if row.get("lock_id") == lock_id and row.get("can_trade") is False]


def append_new_outcomes(path: Path, existing: list[dict[str, Any]], current: list[dict[str, Any]]) -> int:
    existing_keys = {str(row.get("outcome_key")) for row in existing}
    new_rows = [row for row in current if str(row.get("outcome_key")) not in existing_keys]
    if not new_rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in sorted(new_rows, key=lambda item: (str(item.get("resolved_at")), str(item.get("outcome_key")))):
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(new_rows)


def summarize(rows: list[dict[str, Any]], horizons: list[int]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for horizon in horizons:
        values = [as_float(row.get("net_bps")) for row in rows if as_int(row.get("horizon_minutes")) == horizon]
        positives = [value for value in values if value > 0]
        negatives = [value for value in values if value < 0]
        profit_factor = sum(positives) / abs(sum(negatives)) if negatives else (math.inf if positives else 0.0)
        result.append(
            {
                "horizon_minutes": horizon,
                "n": len(values),
                "mean_net_bps": round(statistics.fmean(values), 8) if values else None,
                "median_net_bps": round(statistics.median(values), 8) if values else None,
                "winrate_pct": round(100.0 * len(positives) / len(values), 6) if values else None,
                "profit_factor": None if math.isinf(profit_factor) else round(profit_factor, 8),
                "profit_factor_infinite": math.isinf(profit_factor),
            }
        )
    return result


def classify(
    *,
    event_count: int,
    signal_count: int,
    summaries: list[dict[str, Any]],
    gate: dict[str, Any],
) -> tuple[str, list[str], str]:
    min_events = as_int(gate.get("minimum_resolved_events_per_horizon"), 30)
    min_positive_horizons = as_int(gate.get("minimum_positive_horizons"), 2)
    min_mean = as_float(gate.get("minimum_mean_net_bps"), 5.0)
    min_winrate = as_float(gate.get("minimum_winrate_pct"), 52.0)
    min_pf = as_float(gate.get("minimum_profit_factor"), 1.05)
    if event_count == 0:
        return (
            "liquidation_book_replenishment_waiting_first_post_lock_event",
            ["no_post_lock_liquidation_event_minutes"],
            "keep collectors running; do not change the locked thresholds",
        )
    if signal_count == 0:
        return (
            "liquidation_book_replenishment_collecting_calibration_or_signals",
            ["no_locked_signal_yet"],
            "keep collecting untouched force-order and book data",
        )
    minimum_n = min(as_int(row.get("n")) for row in summaries) if summaries else 0
    if minimum_n < min_events:
        return (
            "liquidation_book_replenishment_collecting_resolved_outcomes",
            ["minimum_resolved_events_per_horizon"],
            "keep collecting until every locked horizon reaches the sample gate",
        )
    positive = 0
    for row in summaries:
        profit_factor = math.inf if row.get("profit_factor_infinite") is True else as_float(row.get("profit_factor"))
        if (
            as_float(row.get("mean_net_bps"), -math.inf) >= min_mean
            and as_float(row.get("winrate_pct")) >= min_winrate
            and profit_factor >= min_pf
        ):
            positive += 1
    if positive >= min_positive_horizons:
        return (
            "liquidation_book_replenishment_passed_for_manual_review_only",
            [],
            "manual research review only; paper/live execution remains forbidden",
        )
    return (
        "liquidation_book_replenishment_failed_gate_for_tombstone_review",
        ["positive_horizon_gate"],
        "tombstone this fixed mechanism after manual review; do not retune the opened sample",
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidation Book Replenishment Forward Observer",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        f"- Lock: `{report['lock']['lock_id']}`",
        f"- Forward start: `{report['lock']['forward_start_at']}`",
        "",
        "## Sample",
        "",
        f"- Post-lock liquidation event minutes: `{report['sample']['event_minutes']}`",
        f"- Locked signals: `{report['sample']['signals']}`",
        f"- Resolved outcome rows: `{report['sample']['resolved_outcome_rows']}`",
        f"- Newly appended outcome rows: `{report['sample']['new_outcome_rows']}`",
        "",
        "## Horizons",
        "",
        "| Horizon | N | Mean net bps | Median net bps | Winrate | Profit factor |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["horizons"]:
        pf = "inf" if row.get("profit_factor_infinite") else row.get("profit_factor")
        lines.append(
            f"| `{row['horizon_minutes']}` | `{row['n']}` | `{row['mean_net_bps']}` | "
            f"`{row['median_net_bps']}` | `{row['winrate_pct']}` | `{pf}` |"
        )
    lines.extend(["", "## Blockers", ""])
    for blocker in report.get("blockers") or ["none"]:
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This tool observes public data and records research outcomes only.",
            "- It cannot place paper or live entries and cannot send orders.",
            "- Parameters are locked before forward outcome review; failed gates require tombstone review, not retuning.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def run_observer(
    *,
    lock_path: Path,
    liquidation_root: Path,
    database: Path,
    ledger_path: Path,
    out_prefix: Path,
) -> dict[str, Any]:
    lock = read_json(lock_path)
    lock_failures = validate_lock(lock)
    if lock_failures:
        raise ValueError("invalid forward lock: " + ",".join(lock_failures))
    if not database.exists():
        raise FileNotFoundError(database)
    rules = lock["fixed_rules"]
    forward_start_ms = parse_ts_ms(lock["forward_start_at"])
    assert forward_start_ms is not None
    events = load_liquidation_minutes(
        liquidation_root,
        symbol=str(rules.get("symbol") or "BTCUSDT"),
        forward_start_ms=forward_start_ms,
        minimum_side_dominance_ratio=as_float(rules.get("minimum_side_dominance_ratio"), 1.5),
    )
    feature_start = forward_start_ms - as_int(rules.get("pre_event_minutes"), 5) * MINUTE_MS
    features = load_features(database, feature_start)
    signals, signal_counters = build_signals(events, features, rules)
    current_outcomes = build_outcomes(signals, features, rules, str(lock["lock_id"]))
    existing = read_ledger(ledger_path, str(lock["lock_id"]))
    appended = append_new_outcomes(ledger_path, existing, current_outcomes)
    ledger_rows = read_ledger(ledger_path, str(lock["lock_id"]))
    horizons = [as_int(value) for value in rules.get("outcome_horizons_minutes", [])]
    summaries = summarize(ledger_rows, horizons)
    decision, blockers, next_action = classify(
        event_count=len(events),
        signal_count=len(signals),
        summaries=summaries,
        gate=lock.get("forward_gate") or {},
    )
    report = {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_book_replenishment_forward_observer.py",
        "decision": decision,
        "can_trade": False,
        "automatic_promotion_allowed": False,
        "lock": {
            "path": portable(lock_path),
            "sha256": sha256_file(lock_path),
            "lock_id": lock.get("lock_id"),
            "status": lock.get("status"),
            "forward_start_at": lock.get("forward_start_at"),
        },
        "sources": {
            "liquidation_root": portable(liquidation_root),
            "microstructure_database": portable(database),
            "ledger": portable(ledger_path),
        },
        "sample": {
            "event_minutes": len(events),
            "signals": len(signals),
            "resolved_outcome_rows": len(ledger_rows),
            "new_outcome_rows": appended,
            "latest_event_time": events[-1]["event_time"] if events else None,
        },
        "signal_counters": signal_counters,
        "horizons": summaries,
        "blockers": blockers,
        "next_action": next_action,
        "runtime_boundary": {
            "observer_only": True,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward-only BTC liquidation/book-replenishment observer")
    parser.add_argument("--lock", default="configs/LIQUIDATION_BOOK_REPLENISHMENT_FORWARD_LOCK_2026-07-12.json")
    parser.add_argument("--liquidation-root", default="data/live/liquidations/binance_force_order")
    parser.add_argument("--database", default="data/cross_venue_microstructure/microstructure.sqlite3")
    parser.add_argument("--ledger", default="logs/liquidation_book_replenishment/forward_outcomes.jsonl")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_BOOK_REPLENISHMENT_FORWARD_OBSERVER_2026-07-12")
    args = parser.parse_args()
    try:
        report = run_observer(
            lock_path=resolve_path(args.lock),
            liquidation_root=resolve_path(args.liquidation_root),
            database=resolve_path(args.database),
            ledger_path=resolve_path(args.ledger),
            out_prefix=resolve_path(args.out_prefix),
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"decision": "liquidation_book_replenishment_observer_error", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "event_minutes": report["sample"]["event_minutes"],
                "signals": report["sample"]["signals"],
                "resolved_outcome_rows": report["sample"]["resolved_outcome_rows"],
                "can_trade": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
