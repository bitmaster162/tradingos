#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
HOUR_MS = 3_600_000


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


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
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
        return


def parse_ts(value: Any) -> datetime | None:
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
    return parsed.astimezone(timezone.utc)


def parse_ts_ms(value: Any) -> int | None:
    parsed = parse_ts(value)
    return int(parsed.timestamp() * 1000) if parsed is not None else None


def iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
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


def nested(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def validate_prereg(prereg: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    boundary = prereg.get("runtime_boundary") if isinstance(prereg.get("runtime_boundary"), dict) else {}
    alignment = prereg.get("causal_alignment") if isinstance(prereg.get("causal_alignment"), dict) else {}
    rules = prereg.get("fixed_rules") if isinstance(prereg.get("fixed_rules"), dict) else {}
    gate = prereg.get("forward_gate") if isinstance(prereg.get("forward_gate"), dict) else {}
    if prereg.get("status") != "prospective_forward_preregistration_before_outcomes":
        failures.append("status")
    if parse_ts(prereg.get("forward_floor_at")) is None:
        failures.append("forward_floor_at")
    if prereg.get("can_trade") is not False:
        failures.append("can_trade")
    if alignment.get("historical_rows_for_strategy_selection_allowed") is not False:
        failures.append("historical_strategy_selection_boundary")
    if alignment.get("pre_floor_records_allowed") is not False or alignment.get("lookahead_allowed") is not False:
        failures.append("causal_boundary")
    if as_int(rules.get("registered_configurations")) != 1:
        failures.append("registered_configurations")
    if as_int(rules.get("primary_horizon_hours")) <= 0:
        failures.append("primary_horizon_hours")
    if gate.get("retuning_allowed") is not False:
        failures.append("retuning_allowed")
    for key in ("paper_entries_allowed", "live_entries_allowed", "orders_allowed", "uses_private_credentials", "can_trade"):
        if boundary.get(key) is not False:
            failures.append(f"runtime_boundary_{key}")
    return sorted(set(failures))


def report_age_hours(report: dict[str, Any], observed_at: datetime) -> float | None:
    generated = parse_ts(report.get("generated_at"))
    if generated is None:
        return None
    return max(0.0, (observed_at - generated).total_seconds() / 3600.0)


def source_integrity(readiness: dict[str, Any], max_age_hours: float, observed_at: datetime) -> dict[str, Any]:
    age = report_age_hours(readiness, observed_at)
    integrity = readiness.get("collector_integrity") if isinstance(readiness.get("collector_integrity"), dict) else {}
    checks = {
        "report_present": bool(readiness),
        "can_trade_false": readiness.get("can_trade") is False,
        "lock_verified": readiness.get("lock_verified") is True,
        "collector_integrity_passed": integrity.get("passed") is True,
        "report_fresh": age is not None and age <= max_age_hours,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "report_age_hours": round(age, 6) if age is not None else None,
        "decision": readiness.get("decision"),
        "research_gate_ready": readiness.get("research_gate_ready") is True,
        "metrics": readiness.get("metrics") if isinstance(readiness.get("metrics"), dict) else {},
    }


def first_new_macro_records(
    rows: Iterable[dict[str, Any]],
    *,
    floor: datetime,
    baseline_proxy_date: str,
) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        collected = parse_ts(row.get("collected_at"))
        proxy_date = str(row.get("latest_proxy_date") or "")
        if collected is None or collected < floor or not proxy_date or proxy_date <= baseline_proxy_date:
            continue
        if row.get("quality_pass") is not True or row.get("can_trade") is not False:
            continue
        if row.get("proxy_semantics") != "heuristic_fed_assets_minus_tga_minus_on_rrp":
            continue
        current = selected.get(proxy_date)
        current_collected = parse_ts((current or {}).get("collected_at"))
        if current is None or (current_collected is not None and collected < current_collected):
            selected[proxy_date] = row
    return sorted(selected.values(), key=lambda row: str(row.get("collected_at")))


def eligible_stable_records(rows: Iterable[dict[str, Any]], floor: datetime) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        collected = parse_ts(row.get("collected_at"))
        if collected is None or collected < floor:
            continue
        if row.get("quality_pass") is not True or row.get("can_trade") is not False:
            continue
        if row.get("metric_semantics") != "global_supply_not_exchange_netflow":
            continue
        result.append(row)
    return sorted(result, key=lambda row: str(row.get("collected_at")))


def latest_stable_before(rows: list[dict[str, Any]], timestamp: datetime) -> dict[str, Any] | None:
    eligible = [row for row in rows if (parse_ts(row.get("collected_at")) or timestamp) <= timestamp]
    return eligible[-1] if eligible else None


def next_hour_ms(timestamp: datetime) -> int:
    epoch_ms = int(timestamp.timestamp() * 1000)
    return epoch_ms - (epoch_ms % HOUR_MS) + HOUR_MS


def classify_side(stable: dict[str, Any], macro: dict[str, Any], rules: dict[str, Any]) -> tuple[str, str]:
    depeg = stable.get("depeg_guard") if isinstance(stable.get("depeg_guard"), dict) else {}
    if as_float(depeg.get("weighted_absolute_deviation_bps"), math.inf) > as_float(
        rules.get("maximum_weighted_depeg_deviation_bps"), 20.0
    ) or as_int(depeg.get("assets_over_50bps"), 999) > as_int(rules.get("maximum_assets_over_50bps"), 0):
        return "ABSTAIN", "depeg_guard"
    stable_bps = as_float(nested(stable, "aggregate_change_7d", "change_bps"))
    macro_bps = as_float(nested(macro, "changes", "4w", "change_bps"))
    positive = as_float(rules.get("positive_threshold_bps"), 0.0)
    negative = as_float(rules.get("negative_threshold_bps"), 0.0)
    if stable_bps > positive and macro_bps > positive:
        return str(rules.get("risk_on_side") or "LONG"), "aligned_positive"
    if stable_bps < negative and macro_bps < negative:
        return str(rules.get("risk_off_side") or "SHORT_RESEARCH_ONLY"), "aligned_negative"
    return "ABSTAIN", "direction_disagreement_or_zero"


def build_events(
    macro_rows: list[dict[str, Any]],
    stable_rows: list[dict[str, Any]],
    prereg: dict[str, Any],
) -> list[dict[str, Any]]:
    rules = prereg["fixed_rules"]
    events: list[dict[str, Any]] = []
    for macro in macro_rows:
        macro_collected = parse_ts(macro.get("collected_at"))
        if macro_collected is None:
            continue
        stable = latest_stable_before(stable_rows, macro_collected)
        if stable is None:
            continue
        side, reason = classify_side(stable, macro, rules)
        entry_ms = next_hour_ms(macro_collected)
        proxy_date = str(macro.get("latest_proxy_date"))
        events.append(
            {
                "hypothesis_id": prereg.get("hypothesis_id"),
                "event_id": f"{prereg.get('hypothesis_id')}:{proxy_date}",
                "macro_proxy_date": proxy_date,
                "macro_collected_at": macro.get("collected_at"),
                "stablecoin_collected_at": stable.get("collected_at"),
                "stablecoin_source_date": nested(stable, "historical_chart", "latest_date"),
                "stablecoin_change_7d_bps": as_float(nested(stable, "aggregate_change_7d", "change_bps")),
                "macro_change_4w_bps": as_float(nested(macro, "changes", "4w", "change_bps")),
                "weighted_depeg_deviation_bps": as_float(
                    nested(stable, "depeg_guard", "weighted_absolute_deviation_bps")
                ),
                "assets_over_50bps": as_int(nested(stable, "depeg_guard", "assets_over_50bps")),
                "side": side,
                "classification_reason": reason,
                "entry_time_ms": entry_ms,
                "entry_time": iso_from_ms(entry_ms),
                "can_trade": False,
            }
        )
    return events


def append_unique(path: Path, rows: list[dict[str, Any]], key: str) -> int:
    existing = {str(row.get(key)) for row in iter_jsonl(path)}
    new_rows = [row for row in rows if str(row.get(key)) not in existing]
    if not new_rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in new_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(new_rows)


def load_btc_opens(path: Path) -> dict[int, float]:
    result: dict[int, float] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            timestamp = as_int(row.get("time_ms"), -1)
            price = as_float(row.get("open"), math.nan)
            if timestamp >= 0 and math.isfinite(price) and price > 0:
                result[timestamp] = price
    return result


def build_outcomes(
    events: list[dict[str, Any]],
    btc_opens: dict[int, float],
    prereg: dict[str, Any],
) -> list[dict[str, Any]]:
    rules = prereg["fixed_rules"]
    horizon_hours = as_int(rules.get("primary_horizon_hours"), 168)
    cost = 2.0 * as_float(rules.get("fee_and_slippage_bps_per_side"), 10.0)
    outcomes: list[dict[str, Any]] = []
    for event in events:
        side = str(event.get("side"))
        if side not in {"LONG", "SHORT_RESEARCH_ONLY"}:
            continue
        entry_ms = as_int(event.get("entry_time_ms"), -1)
        exit_ms = entry_ms + horizon_hours * HOUR_MS
        entry = btc_opens.get(entry_ms)
        exit_price = btc_opens.get(exit_ms)
        if entry is None or exit_price is None:
            continue
        direction = 1.0 if side == "LONG" else -1.0
        gross_bps = direction * (exit_price / entry - 1.0) * 10_000.0
        outcomes.append(
            {
                "outcome_id": f"{event['event_id']}:{horizon_hours}h",
                "event_id": event["event_id"],
                "macro_proxy_date": event["macro_proxy_date"],
                "side": side,
                "entry_time": event["entry_time"],
                "exit_time": iso_from_ms(exit_ms),
                "horizon_hours": horizon_hours,
                "entry": entry,
                "exit": exit_price,
                "gross_bps": round(gross_bps, 8),
                "cost_bps": round(cost, 8),
                "net_bps": round(gross_bps - cost, 8),
                "can_trade": False,
            }
        )
    return outcomes


def metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [as_float(row.get("net_bps")) for row in rows]
    positives = [value for value in values if value > 0]
    negatives = [value for value in values if value < 0]
    profit_factor = sum(positives) / abs(sum(negatives)) if negatives else (math.inf if positives else 0.0)
    return {
        "n": len(values),
        "mean_net_bps": round(statistics.fmean(values), 8) if values else None,
        "median_net_bps": round(statistics.median(values), 8) if values else None,
        "winrate_pct": round(100.0 * len(positives) / len(values), 6) if values else None,
        "profit_factor": None if math.isinf(profit_factor) else round(profit_factor, 8),
        "profit_factor_infinite": math.isinf(profit_factor),
    }


def classify_report(
    events: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    prereg: dict[str, Any],
    source_passed: bool,
) -> tuple[str, list[str], str]:
    if not source_passed:
        return (
            "exogenous_liquidity_regime_blocked_source_integrity_or_freshness",
            ["source_integrity_or_freshness"],
            "restore the existing collectors/readiness guards; do not backfill or relax the source contract",
        )
    if not events:
        return (
            "exogenous_liquidity_regime_waiting_first_new_macro_date",
            ["no_post_floor_macro_proxy_date"],
            "keep forward collectors running until the first new macro proxy date is observed",
        )
    aligned_events = [row for row in events if row.get("side") in {"LONG", "SHORT_RESEARCH_ONLY"}]
    if not aligned_events:
        return (
            "exogenous_liquidity_regime_collecting_alignment_events",
            ["no_aligned_liquidity_direction"],
            "keep collecting; disagreement is an intentional abstention, not a threshold to retune",
        )
    gate = prereg["forward_gate"]
    long_rows = [row for row in outcomes if row.get("side") == "LONG"]
    short_rows = [row for row in outcomes if row.get("side") == "SHORT_RESEARCH_ONLY"]
    unique_dates = {str(row.get("macro_proxy_date")) for row in events}
    dates = sorted(unique_dates)
    span_days = 0
    if len(dates) >= 2:
        span_days = (datetime.fromisoformat(dates[-1]) - datetime.fromisoformat(dates[0])).days
    blockers: list[str] = []
    if len(outcomes) < as_int(gate.get("minimum_resolved_aligned_events")):
        blockers.append("minimum_resolved_aligned_events")
    if len(long_rows) < as_int(gate.get("minimum_long_events")):
        blockers.append("minimum_long_events")
    if len(short_rows) < as_int(gate.get("minimum_short_events")):
        blockers.append("minimum_short_events")
    if len(unique_dates) < as_int(gate.get("minimum_unique_macro_dates")):
        blockers.append("minimum_unique_macro_dates")
    if span_days < as_int(gate.get("minimum_span_days")):
        blockers.append("minimum_span_days")
    if blockers:
        return (
            "exogenous_liquidity_regime_collecting_forward_sample",
            blockers,
            "keep collecting untouched weekly decisions and seven-day outcomes",
        )
    overall = metric_summary(outcomes)
    long_summary = metric_summary(long_rows)
    short_summary = metric_summary(short_rows)
    pf = math.inf if overall.get("profit_factor_infinite") else as_float(overall.get("profit_factor"))
    performance_checks = {
        "mean_net_bps": as_float(overall.get("mean_net_bps"), -math.inf) >= as_float(gate.get("minimum_mean_net_bps")),
        "winrate_pct": as_float(overall.get("winrate_pct")) >= as_float(gate.get("minimum_winrate_pct")),
        "profit_factor": pf >= as_float(gate.get("minimum_profit_factor")),
        "long_mean": as_float(long_summary.get("mean_net_bps"), -math.inf)
        >= as_float(gate.get("minimum_side_mean_net_bps")),
        "short_mean": as_float(short_summary.get("mean_net_bps"), -math.inf)
        >= as_float(gate.get("minimum_side_mean_net_bps")),
    }
    if all(performance_checks.values()):
        return (
            "exogenous_liquidity_regime_passed_for_manual_review_only",
            [],
            "manual research review only; no paper or live permission",
        )
    return (
        "exogenous_liquidity_regime_failed_gate_for_tombstone_review",
        [key for key, passed in performance_checks.items() if not passed],
        "tombstone the fixed hypothesis; do not retune the opened forward sample",
    )


def render_markdown(report: dict[str, Any]) -> str:
    sample = report["sample"]
    lines = [
        "# Exogenous Liquidity Regime Forward Observer",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        f"- Forward floor: `{report['prereg']['forward_floor_at']}`",
        "",
        "## Source Readiness",
        "",
        f"- Stablecoin source decision: `{report['source_readiness']['stablecoin']['decision']}`",
        f"- Stablecoin new dates: `{report['source_readiness']['stablecoin']['metrics'].get('new_unique_source_dates')}`",
        f"- Macro source decision: `{report['source_readiness']['macro']['decision']}`",
        f"- Macro new dates: `{report['source_readiness']['macro']['metrics'].get('new_unique_weekly_dates')}`",
        "",
        "## Forward Sample",
        "",
        f"- Macro decision dates: `{sample['events_total']}`",
        f"- Aligned events: `{sample['aligned_events']}`",
        f"- Abstentions: `{sample['abstentions']}`",
        f"- Resolved outcomes: `{sample['resolved_outcomes']}`",
        f"- LONG outcomes: `{sample['long_outcomes']}`",
        f"- SHORT outcomes: `{sample['short_outcomes']}`",
        f"- Span days: `{sample['span_days']}`",
        "",
        "## Performance",
        "",
        "| Bucket | N | Mean net bps | Winrate | Profit factor |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in ("overall", "long", "short"):
        summary = report["performance"][name]
        pf = "inf" if summary.get("profit_factor_infinite") else summary.get("profit_factor")
        lines.append(
            f"| `{name}` | `{summary['n']}` | `{summary['mean_net_bps']}` | `{summary['winrate_pct']}` | `{pf}` |"
        )
    lines.extend(["", "## Blockers", ""])
    for blocker in report.get("blockers") or ["none"]:
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Only post-registration collector records are admitted.",
            "- Historical source rows are not used for strategy selection.",
            "- Disagreement and depeg states abstain.",
            "- No paper entries, live entries or orders are permitted.",
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
    prereg_path: Path,
    stable_metrics_path: Path,
    macro_metrics_path: Path,
    stable_readiness_path: Path,
    macro_readiness_path: Path,
    btc_path: Path,
    event_ledger_path: Path,
    outcome_ledger_path: Path,
    out_prefix: Path,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    current_time = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    prereg = read_json(prereg_path)
    failures = validate_prereg(prereg)
    if failures:
        raise ValueError("invalid preregistration: " + ",".join(failures))
    floor = parse_ts(prereg["forward_floor_at"])
    assert floor is not None
    maximum_age = as_float(nested(prereg, "source_freshness", "maximum_readiness_report_age_hours"), 30.0)
    stable_readiness = source_integrity(read_json(stable_readiness_path), maximum_age, current_time)
    macro_readiness = source_integrity(read_json(macro_readiness_path), maximum_age, current_time)
    sources_passed = stable_readiness["passed"] and macro_readiness["passed"]

    macro_rows = first_new_macro_records(
        iter_jsonl(macro_metrics_path),
        floor=floor,
        baseline_proxy_date=str(prereg.get("baseline_macro_proxy_date")),
    )
    stable_rows = eligible_stable_records(iter_jsonl(stable_metrics_path), floor)
    current_events = build_events(macro_rows, stable_rows, prereg) if sources_passed else []
    events_added = append_unique(event_ledger_path, current_events, "event_id")
    all_events = list(iter_jsonl(event_ledger_path))
    btc_opens = load_btc_opens(btc_path)
    current_outcomes = build_outcomes(all_events, btc_opens, prereg)
    outcomes_added = append_unique(outcome_ledger_path, current_outcomes, "outcome_id")
    all_outcomes = list(iter_jsonl(outcome_ledger_path))
    decision, blockers, next_action = classify_report(all_events, all_outcomes, prereg, sources_passed)

    unique_dates = sorted({str(row.get("macro_proxy_date")) for row in all_events})
    span_days = (
        (datetime.fromisoformat(unique_dates[-1]) - datetime.fromisoformat(unique_dates[0])).days
        if len(unique_dates) >= 2
        else 0
    )
    long_rows = [row for row in all_outcomes if row.get("side") == "LONG"]
    short_rows = [row for row in all_outcomes if row.get("side") == "SHORT_RESEARCH_ONLY"]
    aligned_events = [row for row in all_events if row.get("side") in {"LONG", "SHORT_RESEARCH_ONLY"}]
    report = {
        "generated_at": now_iso(),
        "tool": "tools/exogenous_liquidity_regime_forward_observer.py",
        "decision": decision,
        "can_trade": False,
        "automatic_promotion_allowed": False,
        "prereg": {
            "path": portable(prereg_path),
            "sha256": sha256_file(prereg_path),
            "hypothesis_id": prereg.get("hypothesis_id"),
            "registered_at": prereg.get("registered_at"),
            "forward_floor_at": prereg.get("forward_floor_at"),
        },
        "sources": {
            "stable_metrics": portable(stable_metrics_path),
            "macro_metrics": portable(macro_metrics_path),
            "btc_hourly": portable(btc_path),
            "event_ledger": portable(event_ledger_path),
            "outcome_ledger": portable(outcome_ledger_path),
        },
        "source_readiness": {"stablecoin": stable_readiness, "macro": macro_readiness},
        "sample": {
            "events_total": len(all_events),
            "events_added": events_added,
            "aligned_events": len(aligned_events),
            "abstentions": len(all_events) - len(aligned_events),
            "resolved_outcomes": len(all_outcomes),
            "outcomes_added": outcomes_added,
            "long_outcomes": len(long_rows),
            "short_outcomes": len(short_rows),
            "unique_macro_dates": len(unique_dates),
            "span_days": span_days,
        },
        "performance": {
            "overall": metric_summary(all_outcomes),
            "long": metric_summary(long_rows),
            "short": metric_summary(short_rows),
        },
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
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward-only stablecoin plus macro liquidity regime observer")
    parser.add_argument("--prereg", default="configs/EXOGENOUS_LIQUIDITY_REGIME_FORWARD_PREREG_2026-07-12.json")
    parser.add_argument(
        "--stable-metrics",
        default="HANDOFF/INCOMING/codex/20260711_stablecoin_supply_pulse_collector/runtime_v3/supply_pulse_metrics.jsonl",
    )
    parser.add_argument(
        "--macro-metrics",
        default="HANDOFF/INCOMING/codex/20260712_macro_usd_liquidity_collector/runtime/macro_liquidity_metrics.jsonl",
    )
    parser.add_argument(
        "--stable-readiness",
        default="HANDOFF/INCOMING/codex/20260712_stablecoin_supply_readiness_guard/runtime/LATEST.json",
    )
    parser.add_argument(
        "--macro-readiness",
        default="HANDOFF/INCOMING/codex/20260712_macro_usd_liquidity_readiness_guard/runtime/LATEST.json",
    )
    parser.add_argument("--btc", default="data/cache/binance_spot_perp_extended/futures/BTCUSDT/1h_klines.csv")
    parser.add_argument("--events", default="logs/exogenous_liquidity_regime/events.jsonl")
    parser.add_argument("--outcomes", default="logs/exogenous_liquidity_regime/outcomes.jsonl")
    parser.add_argument("--out-prefix", default="docs/EXOGENOUS_LIQUIDITY_REGIME_FORWARD_OBSERVER_2026-07-12")
    args = parser.parse_args()
    try:
        report = run_observer(
            prereg_path=resolve_path(args.prereg),
            stable_metrics_path=resolve_path(args.stable_metrics),
            macro_metrics_path=resolve_path(args.macro_metrics),
            stable_readiness_path=resolve_path(args.stable_readiness),
            macro_readiness_path=resolve_path(args.macro_readiness),
            btc_path=resolve_path(args.btc),
            event_ledger_path=resolve_path(args.events),
            outcome_ledger_path=resolve_path(args.outcomes),
            out_prefix=resolve_path(args.out_prefix),
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"decision": "exogenous_liquidity_regime_observer_error", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "events": report["sample"]["events_total"],
                "outcomes": report["sample"]["resolved_outcomes"],
                "stablecoin_forward_dates": report["source_readiness"]["stablecoin"]["metrics"].get(
                    "new_unique_source_dates"
                ),
                "macro_forward_dates": report["source_readiness"]["macro"]["metrics"].get(
                    "new_unique_weekly_dates"
                ),
                "can_trade": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
