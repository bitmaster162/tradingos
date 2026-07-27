from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

_ZERO = Decimal("0")


_ACTION_RANK = {"": 0, "trade": 1, "reduce_size": 2, "observe_only": 3}


def _prefer_stronger_action(current: str, candidate: str) -> str:
    return candidate if _ACTION_RANK.get(candidate, 0) > _ACTION_RANK.get(current, 0) else current


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, start=_ZERO) / Decimal(len(values))


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


@dataclass(slots=True)
class DailyReportAggregate:
    date: str
    symbol: str
    live_session_count: int
    backtest_run_count: int
    drift_check_count: int
    walkforward_report_count: int = 0
    intraday_protection_check_count: int = 0
    pnl_protection_check_count: int = 0
    trade_reconciliation_check_count: int = 0
    session_truth_check_count: int = 0
    session_truth_report_count: int = 0
    session_truth_trend_check_count: int = 0
    economics_dashboard_count: int = 0
    economics_regime_check_count: int = 0
    combined_protection_check_count: int = 0
    authoritative_backfill_count: int = 0
    latest_live_action: str = ""
    latest_live_expected_fill_ratio: Decimal | None = None
    latest_live_entry_timeout_rate: Decimal | None = None
    latest_live_economics_feedback_multiplier: Decimal | None = None
    average_live_expected_fill_ratio: Decimal | None = None
    average_live_entry_fill_latency_seconds: Decimal | None = None
    average_live_entry_timeout_rate: Decimal | None = None
    average_live_economics_feedback_multiplier: Decimal | None = None
    latest_live_economics_feedback_multiplier: Decimal | None = None
    average_live_exit_depth_sweep_bps: Decimal | None = None
    average_live_queue_clear_seconds: Decimal | None = None
    latest_backtest_mode: str = ""
    latest_backtest_net_pnl: Decimal | None = None
    latest_backtest_trade_count: int = 0
    latest_backtest_entry_timeout_rate: Decimal | None = None
    latest_walkforward_total_test_net_pnl: Decimal | None = None
    latest_walkforward_fold_count: int = 0
    latest_walkforward_selection_turnover_ratio: Decimal | None = None
    average_backtest_net_pnl: Decimal | None = None
    average_backtest_trade_count: Decimal | None = None
    average_backtest_entry_fill_latency_seconds: Decimal | None = None
    average_backtest_entry_timeout_rate: Decimal | None = None
    latest_drift_action: str = ""
    reduce_size_checks: int = 0
    observe_only_checks: int = 0
    latest_drift_score: Decimal | None = None
    latest_intraday_protection_action: str = ""
    intraday_reduce_size_checks: int = 0
    intraday_observe_only_checks: int = 0
    latest_pnl_protection_action: str = ""
    pnl_reduce_size_checks: int = 0
    pnl_observe_only_checks: int = 0
    latest_trade_reconciliation_action: str = ""
    latest_trade_reconciliation_window_mode: str = ""
    latest_trade_reconciliation_income_trade_link_gap_ratio: Decimal | None = None
    latest_trade_reconciliation_quote_qty_abs_diff_usdt: Decimal | None = None
    average_trade_reconciliation_income_trade_link_gap_ratio: Decimal | None = None
    trade_reconciliation_reduce_size_checks: int = 0
    trade_reconciliation_observe_only_checks: int = 0
    latest_session_truth_action: str = ""
    latest_session_truth_net_realized_pnl_usdt: Decimal | None = None
    latest_session_truth_net_realized_bps: Decimal | None = None
    latest_session_truth_maker_ratio: Decimal | None = None
    average_session_truth_net_realized_pnl_usdt: Decimal | None = None
    average_session_truth_net_realized_bps: Decimal | None = None
    average_session_truth_maker_ratio: Decimal | None = None
    session_truth_reduce_size_checks: int = 0
    session_truth_observe_only_checks: int = 0
    latest_session_truth_report_negative_bucket_ratio: Decimal | None = None
    latest_session_truth_report_cumulative_drawdown_usdt: Decimal | None = None
    average_session_truth_report_negative_bucket_ratio: Decimal | None = None
    average_session_truth_report_cumulative_drawdown_usdt: Decimal | None = None
    latest_session_truth_trend_action: str = ""
    latest_session_truth_trend_negative_bucket_ratio: Decimal | None = None
    latest_session_truth_trend_recent_bucket_net_realized_bps: Decimal | None = None
    average_session_truth_trend_negative_bucket_ratio: Decimal | None = None
    session_truth_trend_reduce_size_checks: int = 0
    session_truth_trend_observe_only_checks: int = 0
    latest_economics_dashboard_negative_day_ratio: Decimal | None = None
    latest_economics_dashboard_average_maker_ratio: Decimal | None = None
    average_economics_dashboard_negative_day_ratio: Decimal | None = None
    average_economics_dashboard_average_maker_ratio: Decimal | None = None
    latest_economics_regime_action: str = ""
    latest_economics_regime_negative_day_ratio: Decimal | None = None
    latest_economics_regime_recent_day_net_realized_bps: Decimal | None = None
    economics_regime_reduce_size_checks: int = 0
    economics_regime_observe_only_checks: int = 0
    latest_combined_protection_action: str = ""
    combined_reduce_size_checks: int = 0
    combined_observe_only_checks: int = 0
    latest_authoritative_backfill_trade_rows: int = 0
    latest_authoritative_backfill_income_rows: int = 0


def aggregate_daily_reports(*, data_dir: Path, symbol: str, date: str) -> DailyReportAggregate:
    symbol_key = symbol.lower()
    reports_dir = Path(data_dir) / "reports" / date
    live_records = _load_jsonl(reports_dir / f"{symbol_key}_live_execution_quality.jsonl")
    backtest_records = _load_jsonl(reports_dir / f"{symbol_key}_backtest_reports.jsonl")
    walkforward_records = _load_jsonl(reports_dir / f"{symbol_key}_walkforward_reports.jsonl")
    drift_records = _load_jsonl(reports_dir / f"{symbol_key}_execution_drift.jsonl")
    intraday_records = _load_jsonl(reports_dir / f"{symbol_key}_intraday_protection.jsonl")
    pnl_records = _load_jsonl(reports_dir / f"{symbol_key}_pnl_protection.jsonl")
    trade_reconciliation_records = _load_jsonl(reports_dir / f"{symbol_key}_trade_reconciliation.jsonl")
    session_truth_records = _load_jsonl(reports_dir / f"{symbol_key}_session_truth.jsonl")
    session_truth_report_records = _load_jsonl(reports_dir / f"{symbol_key}_session_truth_report.jsonl")
    session_truth_trend_records = _load_jsonl(reports_dir / f"{symbol_key}_session_truth_trend.jsonl")
    economics_dashboard_records = _load_jsonl(reports_dir / f"{symbol_key}_economics_dashboard.jsonl")
    economics_regime_records = _load_jsonl(reports_dir / f"{symbol_key}_economics_regime.jsonl")
    combined_records = _load_jsonl(reports_dir / f"{symbol_key}_combined_protection.jsonl")
    authoritative_backfill_records = _load_jsonl(reports_dir / f"{symbol_key}_authoritative_backfill.jsonl")

    live_expected_fill_ratios: list[Decimal] = []
    live_entry_fill_latencies: list[Decimal] = []
    live_entry_timeout_rates: list[Decimal] = []
    live_economics_feedback_multipliers: list[Decimal] = []
    live_exit_sweeps: list[Decimal] = []
    live_queue_clear: list[Decimal] = []
    latest_live_action = ""
    latest_live_expected_fill_ratio: Decimal | None = None
    latest_live_entry_timeout_rate: Decimal | None = None
    latest_live_economics_feedback_multiplier: Decimal | None = None
    for record in live_records:
        report = record.get("report") if isinstance(record.get("report"), dict) else record
        if not isinstance(report, dict):
            continue
        fill_ratio = _to_decimal(report.get("average_expected_fill_ratio"))
        if fill_ratio is not None:
            live_expected_fill_ratios.append(fill_ratio)
            latest_live_expected_fill_ratio = fill_ratio
        entry_fill_latency = _to_decimal(report.get("average_entry_fill_latency_seconds"))
        if entry_fill_latency is not None:
            live_entry_fill_latencies.append(entry_fill_latency)
        entry_timeout_rate = _to_decimal(report.get("entry_timeout_rate"))
        if entry_timeout_rate is not None:
            live_entry_timeout_rates.append(entry_timeout_rate)
            latest_live_entry_timeout_rate = entry_timeout_rate
        economics_feedback_multiplier = _to_decimal(report.get("average_economics_feedback_multiplier"))
        if economics_feedback_multiplier is not None:
            live_economics_feedback_multipliers.append(economics_feedback_multiplier)
            latest_live_economics_feedback_multiplier = economics_feedback_multiplier
        exit_sweep = _to_decimal(report.get("average_exit_depth_sweep_bps"))
        if exit_sweep is not None:
            live_exit_sweeps.append(exit_sweep)
        queue_clear = _to_decimal(report.get("average_queue_clear_seconds"))
        if queue_clear is not None:
            live_queue_clear.append(queue_clear)
        latest_live_action = _prefer_stronger_action(
            latest_live_action,
            str(report.get("last_execution_drift_action", "") or ""),
        )
        latest_live_action = _prefer_stronger_action(
            latest_live_action,
            str(report.get("last_intraday_protection_action", "") or ""),
        )
        latest_live_action = _prefer_stronger_action(
            latest_live_action,
            str(report.get("last_pnl_protection_action", "") or ""),
        )
        latest_live_action = _prefer_stronger_action(
            latest_live_action,
            str(report.get("last_trade_reconciliation_action", "") or ""),
        )
        latest_live_action = _prefer_stronger_action(
            latest_live_action,
            str(report.get("last_session_truth_action", "") or ""),
        )
        latest_live_action = _prefer_stronger_action(
            latest_live_action,
            str(report.get("last_session_truth_trend_action", "") or ""),
        )
        latest_live_action = _prefer_stronger_action(
            latest_live_action,
            str(report.get("last_economics_regime_action", "") or ""),
        )
        latest_live_action = _prefer_stronger_action(
            latest_live_action,
            str(report.get("last_combined_protection_action", "") or ""),
        )

    backtest_net_pnls: list[Decimal] = []
    backtest_trade_counts: list[Decimal] = []
    backtest_entry_fill_latencies: list[Decimal] = []
    backtest_entry_timeout_rates: list[Decimal] = []
    latest_backtest_mode = ""
    latest_backtest_net_pnl: Decimal | None = None
    latest_backtest_trade_count = 0
    latest_backtest_entry_timeout_rate: Decimal | None = None
    latest_walkforward_total_test_net_pnl: Decimal | None = None
    latest_walkforward_fold_count = 0
    latest_walkforward_selection_turnover_ratio: Decimal | None = None
    for record in backtest_records:
        summary = record.get("summary", {}) if isinstance(record.get("summary"), dict) else {}
        mode = str(record.get("mode", latest_backtest_mode) or latest_backtest_mode)
        latest_backtest_mode = mode
        net_pnl = _to_decimal(summary.get("net_pnl"))
        if net_pnl is not None:
            backtest_net_pnls.append(net_pnl)
            latest_backtest_net_pnl = net_pnl
        entry_fill_latency = _to_decimal(summary.get("average_entry_fill_latency_seconds"))
        if entry_fill_latency is not None:
            backtest_entry_fill_latencies.append(entry_fill_latency)
        entry_timeout_rate = _to_decimal(summary.get("entry_timeout_rate"))
        if entry_timeout_rate is not None:
            backtest_entry_timeout_rates.append(entry_timeout_rate)
            latest_backtest_entry_timeout_rate = entry_timeout_rate
        trade_count = int(summary.get("trade_count", 0) or 0)
        if trade_count:
            backtest_trade_counts.append(Decimal(trade_count))
            latest_backtest_trade_count = trade_count

    for record in walkforward_records:
        summary = record.get("summary", {}) if isinstance(record.get("summary"), dict) else {}
        total_test_net_pnl = _to_decimal(summary.get("total_test_net_pnl"))
        if total_test_net_pnl is not None:
            latest_walkforward_total_test_net_pnl = total_test_net_pnl
        latest_walkforward_fold_count = int(summary.get("fold_count", latest_walkforward_fold_count) or latest_walkforward_fold_count)
        latest_walkforward_selection_turnover_ratio = (
            _to_decimal(summary.get("selection_turnover_ratio")) or latest_walkforward_selection_turnover_ratio
        )

    latest_drift_action = ""
    latest_drift_score: Decimal | None = None
    reduce_size_checks = 0
    observe_only_checks = 0
    for record in drift_records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else record
        if not isinstance(decision, dict):
            continue
        latest_drift_action = str(decision.get("action", latest_drift_action) or latest_drift_action)
        latest_drift_score = _to_decimal(decision.get("score")) or latest_drift_score
        action = str(decision.get("action", "") or "")
        if action == "reduce_size":
            reduce_size_checks += 1
        elif action == "observe_only":
            observe_only_checks += 1

    latest_intraday_protection_action = ""
    intraday_reduce_size_checks = 0
    intraday_observe_only_checks = 0
    for record in intraday_records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else record
        if not isinstance(decision, dict):
            continue
        latest_intraday_protection_action = str(
            decision.get("action", latest_intraday_protection_action) or latest_intraday_protection_action
        )
        action = str(decision.get("action", "") or "")
        if action == "reduce_size":
            intraday_reduce_size_checks += 1
        elif action == "observe_only":
            intraday_observe_only_checks += 1

    latest_trade_reconciliation_action = ""
    latest_trade_reconciliation_window_mode = ""
    latest_trade_reconciliation_income_trade_link_gap_ratio: Decimal | None = None
    latest_trade_reconciliation_quote_qty_abs_diff_usdt: Decimal | None = None
    trade_reconciliation_income_trade_link_gap_ratios: list[Decimal] = []
    trade_reconciliation_reduce_size_checks = 0
    trade_reconciliation_observe_only_checks = 0
    for record in trade_reconciliation_records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else record
        if not isinstance(decision, dict):
            continue
        latest_trade_reconciliation_action = str(
            decision.get("action", latest_trade_reconciliation_action) or latest_trade_reconciliation_action
        )
        latest_trade_reconciliation_window_mode = str(
            decision.get("window_mode", latest_trade_reconciliation_window_mode) or latest_trade_reconciliation_window_mode
        )
        income_gap_ratio = _to_decimal(decision.get("income_trade_link_gap_ratio"))
        if income_gap_ratio is not None:
            trade_reconciliation_income_trade_link_gap_ratios.append(income_gap_ratio)
            latest_trade_reconciliation_income_trade_link_gap_ratio = income_gap_ratio
        quote_qty_abs_diff = _to_decimal(decision.get("quote_qty_abs_diff_usdt"))
        if quote_qty_abs_diff is not None:
            latest_trade_reconciliation_quote_qty_abs_diff_usdt = quote_qty_abs_diff
        action = str(decision.get("action", "") or "")
        if action == "reduce_size":
            trade_reconciliation_reduce_size_checks += 1
        elif action == "observe_only":
            trade_reconciliation_observe_only_checks += 1

    latest_session_truth_action = ""
    latest_session_truth_net_realized_pnl_usdt: Decimal | None = None
    latest_session_truth_net_realized_bps: Decimal | None = None
    latest_session_truth_maker_ratio: Decimal | None = None
    session_truth_net_realized_pnls: list[Decimal] = []
    session_truth_net_realized_bps_values: list[Decimal] = []
    session_truth_maker_ratios: list[Decimal] = []
    session_truth_reduce_size_checks = 0
    session_truth_observe_only_checks = 0
    for record in session_truth_records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else record
        if not isinstance(decision, dict):
            continue
        latest_session_truth_action = str(
            decision.get("action", latest_session_truth_action) or latest_session_truth_action
        )
        net_realized_pnl = _to_decimal(decision.get("net_realized_pnl_usdt"))
        if net_realized_pnl is not None:
            session_truth_net_realized_pnls.append(net_realized_pnl)
            latest_session_truth_net_realized_pnl_usdt = net_realized_pnl
        net_realized_bps = _to_decimal(decision.get("net_realized_bps"))
        if net_realized_bps is not None:
            session_truth_net_realized_bps_values.append(net_realized_bps)
            latest_session_truth_net_realized_bps = net_realized_bps
        maker_ratio = _to_decimal(decision.get("maker_ratio"))
        if maker_ratio is not None:
            session_truth_maker_ratios.append(maker_ratio)
            latest_session_truth_maker_ratio = maker_ratio
        action = str(decision.get("action", "") or "")
        if action == "reduce_size":
            session_truth_reduce_size_checks += 1
        elif action == "observe_only":
            session_truth_observe_only_checks += 1

    latest_pnl_protection_action = ""
    pnl_reduce_size_checks = 0
    pnl_observe_only_checks = 0
    for record in pnl_records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else record
        if not isinstance(decision, dict):
            continue
        latest_pnl_protection_action = str(
            decision.get("action", latest_pnl_protection_action) or latest_pnl_protection_action
        )
        action = str(decision.get("action", "") or "")
        if action == "reduce_size":
            pnl_reduce_size_checks += 1
        elif action == "observe_only":
            pnl_observe_only_checks += 1


    session_truth_report_negative_bucket_ratios: list[Decimal] = []
    session_truth_report_cumulative_drawdowns: list[Decimal] = []
    latest_session_truth_report_negative_bucket_ratio: Decimal | None = None
    latest_session_truth_report_cumulative_drawdown_usdt: Decimal | None = None
    for record in session_truth_report_records:
        report = record.get("report") if isinstance(record.get("report"), dict) else record
        if not isinstance(report, dict):
            continue
        negative_bucket_ratio = _to_decimal(report.get("negative_bucket_ratio"))
        if negative_bucket_ratio is not None:
            session_truth_report_negative_bucket_ratios.append(negative_bucket_ratio)
            latest_session_truth_report_negative_bucket_ratio = negative_bucket_ratio
        cumulative_drawdown = _to_decimal(report.get("cumulative_drawdown_usdt"))
        if cumulative_drawdown is not None:
            session_truth_report_cumulative_drawdowns.append(cumulative_drawdown)
            latest_session_truth_report_cumulative_drawdown_usdt = cumulative_drawdown

    latest_session_truth_trend_action = ""
    latest_session_truth_trend_negative_bucket_ratio: Decimal | None = None
    latest_session_truth_trend_recent_bucket_net_realized_bps: Decimal | None = None
    session_truth_trend_negative_bucket_ratios: list[Decimal] = []
    session_truth_trend_reduce_size_checks = 0
    session_truth_trend_observe_only_checks = 0
    for record in session_truth_trend_records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else record
        if not isinstance(decision, dict):
            continue
        latest_session_truth_trend_action = str(
            decision.get("action", latest_session_truth_trend_action) or latest_session_truth_trend_action
        )
        negative_bucket_ratio = _to_decimal(decision.get("negative_bucket_ratio"))
        if negative_bucket_ratio is not None:
            session_truth_trend_negative_bucket_ratios.append(negative_bucket_ratio)
            latest_session_truth_trend_negative_bucket_ratio = negative_bucket_ratio
        recent_bucket_net_realized_bps = _to_decimal(decision.get("recent_bucket_net_realized_bps"))
        if recent_bucket_net_realized_bps is not None:
            latest_session_truth_trend_recent_bucket_net_realized_bps = recent_bucket_net_realized_bps
        action = str(decision.get("action", "") or "")
        if action == "reduce_size":
            session_truth_trend_reduce_size_checks += 1
        elif action == "observe_only":
            session_truth_trend_observe_only_checks += 1

    latest_economics_dashboard_negative_day_ratio: Decimal | None = None
    latest_economics_dashboard_average_maker_ratio: Decimal | None = None
    economics_dashboard_negative_day_ratios: list[Decimal] = []
    economics_dashboard_average_maker_ratios: list[Decimal] = []
    for record in economics_dashboard_records:
        dashboard = record.get("dashboard") if isinstance(record.get("dashboard"), dict) else record
        if not isinstance(dashboard, dict):
            continue
        negative_day_ratio = _to_decimal(dashboard.get("negative_day_ratio"))
        if negative_day_ratio is not None:
            economics_dashboard_negative_day_ratios.append(negative_day_ratio)
            latest_economics_dashboard_negative_day_ratio = negative_day_ratio
        average_maker_ratio = _to_decimal(dashboard.get("average_maker_ratio"))
        if average_maker_ratio is not None:
            economics_dashboard_average_maker_ratios.append(average_maker_ratio)
            latest_economics_dashboard_average_maker_ratio = average_maker_ratio

    latest_economics_regime_action = ""
    latest_economics_regime_negative_day_ratio: Decimal | None = None
    latest_economics_regime_recent_day_net_realized_bps: Decimal | None = None
    economics_regime_reduce_size_checks = 0
    economics_regime_observe_only_checks = 0
    for record in economics_regime_records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else record
        if not isinstance(decision, dict):
            continue
        latest_economics_regime_action = str(decision.get("action", latest_economics_regime_action) or latest_economics_regime_action)
        negative_day_ratio = _to_decimal(decision.get("negative_day_ratio"))
        if negative_day_ratio is not None:
            latest_economics_regime_negative_day_ratio = negative_day_ratio
        recent_day_bps = _to_decimal(decision.get("recent_day_net_realized_bps"))
        if recent_day_bps is not None:
            latest_economics_regime_recent_day_net_realized_bps = recent_day_bps
        action = str(decision.get("action", "") or "")
        if action == "reduce_size":
            economics_regime_reduce_size_checks += 1
        elif action == "observe_only":
            economics_regime_observe_only_checks += 1


    latest_combined_protection_action = ""
    combined_reduce_size_checks = 0
    combined_observe_only_checks = 0
    latest_authoritative_backfill_trade_rows = 0
    latest_authoritative_backfill_income_rows = 0
    for record in combined_records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else record
        if not isinstance(decision, dict):
            continue
        latest_combined_protection_action = str(
            decision.get("action", latest_combined_protection_action) or latest_combined_protection_action
        )
        action = str(decision.get("action", "") or "")
        if action == "reduce_size":
            combined_reduce_size_checks += 1
        elif action == "observe_only":
            combined_observe_only_checks += 1

    for record in authoritative_backfill_records:
        result = record.get("result") if isinstance(record.get("result"), dict) else record
        if not isinstance(result, dict):
            continue
        latest_authoritative_backfill_trade_rows = int(result.get("user_trade_row_count", latest_authoritative_backfill_trade_rows) or latest_authoritative_backfill_trade_rows)
        latest_authoritative_backfill_income_rows = int(result.get("income_row_count", latest_authoritative_backfill_income_rows) or latest_authoritative_backfill_income_rows)

    return DailyReportAggregate(
        date=date,
        symbol=symbol,
        live_session_count=len(live_records),
        backtest_run_count=len(backtest_records),
        drift_check_count=len(drift_records),
        walkforward_report_count=len(walkforward_records),
        intraday_protection_check_count=len(intraday_records),
        pnl_protection_check_count=len(pnl_records),
        trade_reconciliation_check_count=len(trade_reconciliation_records),
        session_truth_check_count=len(session_truth_records),
        session_truth_report_count=len(session_truth_report_records),
        session_truth_trend_check_count=len(session_truth_trend_records),
        economics_dashboard_count=len(economics_dashboard_records),
        economics_regime_check_count=len(economics_regime_records),
        combined_protection_check_count=len(combined_records),
        authoritative_backfill_count=len(authoritative_backfill_records),
        latest_live_action=latest_live_action,
        latest_live_expected_fill_ratio=latest_live_expected_fill_ratio,
        latest_live_entry_timeout_rate=latest_live_entry_timeout_rate,
        average_live_expected_fill_ratio=_mean(live_expected_fill_ratios),
        average_live_entry_fill_latency_seconds=_mean(live_entry_fill_latencies),
        average_live_entry_timeout_rate=_mean(live_entry_timeout_rates),
        average_live_economics_feedback_multiplier=_mean(live_economics_feedback_multipliers),
        latest_live_economics_feedback_multiplier=latest_live_economics_feedback_multiplier,
        average_live_exit_depth_sweep_bps=_mean(live_exit_sweeps),
        average_live_queue_clear_seconds=_mean(live_queue_clear),
        latest_backtest_mode=latest_backtest_mode,
        latest_backtest_net_pnl=latest_backtest_net_pnl,
        latest_backtest_trade_count=latest_backtest_trade_count,
        latest_backtest_entry_timeout_rate=latest_backtest_entry_timeout_rate,
        latest_walkforward_total_test_net_pnl=latest_walkforward_total_test_net_pnl,
        latest_walkforward_fold_count=latest_walkforward_fold_count,
        latest_walkforward_selection_turnover_ratio=latest_walkforward_selection_turnover_ratio,
        average_backtest_net_pnl=_mean(backtest_net_pnls),
        average_backtest_trade_count=_mean(backtest_trade_counts),
        average_backtest_entry_fill_latency_seconds=_mean(backtest_entry_fill_latencies),
        average_backtest_entry_timeout_rate=_mean(backtest_entry_timeout_rates),
        latest_drift_action=latest_drift_action,
        reduce_size_checks=reduce_size_checks,
        observe_only_checks=observe_only_checks,
        latest_drift_score=latest_drift_score,
        latest_intraday_protection_action=latest_intraday_protection_action,
        intraday_reduce_size_checks=intraday_reduce_size_checks,
        intraday_observe_only_checks=intraday_observe_only_checks,
        latest_pnl_protection_action=latest_pnl_protection_action,
        pnl_reduce_size_checks=pnl_reduce_size_checks,
        pnl_observe_only_checks=pnl_observe_only_checks,
        latest_trade_reconciliation_action=latest_trade_reconciliation_action,
        latest_trade_reconciliation_window_mode=latest_trade_reconciliation_window_mode,
        latest_trade_reconciliation_income_trade_link_gap_ratio=latest_trade_reconciliation_income_trade_link_gap_ratio,
        latest_trade_reconciliation_quote_qty_abs_diff_usdt=latest_trade_reconciliation_quote_qty_abs_diff_usdt,
        average_trade_reconciliation_income_trade_link_gap_ratio=_mean(trade_reconciliation_income_trade_link_gap_ratios),
        trade_reconciliation_reduce_size_checks=trade_reconciliation_reduce_size_checks,
        trade_reconciliation_observe_only_checks=trade_reconciliation_observe_only_checks,
        latest_session_truth_action=latest_session_truth_action,
        latest_session_truth_net_realized_pnl_usdt=latest_session_truth_net_realized_pnl_usdt,
        latest_session_truth_net_realized_bps=latest_session_truth_net_realized_bps,
        latest_session_truth_maker_ratio=latest_session_truth_maker_ratio,
        average_session_truth_net_realized_pnl_usdt=_mean(session_truth_net_realized_pnls),
        average_session_truth_net_realized_bps=_mean(session_truth_net_realized_bps_values),
        average_session_truth_maker_ratio=_mean(session_truth_maker_ratios),
        session_truth_reduce_size_checks=session_truth_reduce_size_checks,
        session_truth_observe_only_checks=session_truth_observe_only_checks,
        latest_session_truth_report_negative_bucket_ratio=latest_session_truth_report_negative_bucket_ratio,
        latest_session_truth_report_cumulative_drawdown_usdt=latest_session_truth_report_cumulative_drawdown_usdt,
        average_session_truth_report_negative_bucket_ratio=_mean(session_truth_report_negative_bucket_ratios),
        average_session_truth_report_cumulative_drawdown_usdt=_mean(session_truth_report_cumulative_drawdowns),
        latest_session_truth_trend_action=latest_session_truth_trend_action,
        latest_session_truth_trend_negative_bucket_ratio=latest_session_truth_trend_negative_bucket_ratio,
        latest_session_truth_trend_recent_bucket_net_realized_bps=latest_session_truth_trend_recent_bucket_net_realized_bps,
        average_session_truth_trend_negative_bucket_ratio=_mean(session_truth_trend_negative_bucket_ratios),
        session_truth_trend_reduce_size_checks=session_truth_trend_reduce_size_checks,
        session_truth_trend_observe_only_checks=session_truth_trend_observe_only_checks,
        latest_economics_dashboard_negative_day_ratio=latest_economics_dashboard_negative_day_ratio,
        latest_economics_dashboard_average_maker_ratio=latest_economics_dashboard_average_maker_ratio,
        average_economics_dashboard_negative_day_ratio=_mean(economics_dashboard_negative_day_ratios),
        average_economics_dashboard_average_maker_ratio=_mean(economics_dashboard_average_maker_ratios),
        latest_economics_regime_action=latest_economics_regime_action,
        latest_economics_regime_negative_day_ratio=latest_economics_regime_negative_day_ratio,
        latest_economics_regime_recent_day_net_realized_bps=latest_economics_regime_recent_day_net_realized_bps,
        economics_regime_reduce_size_checks=economics_regime_reduce_size_checks,
        economics_regime_observe_only_checks=economics_regime_observe_only_checks,
        latest_combined_protection_action=latest_combined_protection_action,
        combined_reduce_size_checks=combined_reduce_size_checks,
        combined_observe_only_checks=combined_observe_only_checks,
        latest_authoritative_backfill_trade_rows=latest_authoritative_backfill_trade_rows,
        latest_authoritative_backfill_income_rows=latest_authoritative_backfill_income_rows,
    )
