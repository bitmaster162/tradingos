from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable

from btcusdt_bot.backtest.engine import BacktestReport, BreakoutBacktestConfig

_ZERO = Decimal("0")
_NEG_INF = Decimal("-1E18")


@dataclass(frozen=True, slots=True)
class BreakoutParameterCandidate:
    breakout_lookback_ticks: int
    max_hold_seconds: int
    strategy_kind: str = "breakout"
    min_flow_imbalance: Decimal = Decimal("0")
    min_crowding_score: Decimal | None = None
    min_depth_imbalance: Decimal | None = None
    max_book_spread_bps: Decimal | None = None
    min_expected_fill_ratio: Decimal | None = None
    reversion_entry_atr_multiple: Decimal | None = None
    reversion_max_atr_fraction: Decimal | None = None
    reversion_min_flow_flip: Decimal | None = None

    @property
    def label(self) -> str:
        parts = [
            f"strategy={self.strategy_kind}",
            f"lb={self.breakout_lookback_ticks}",
            f"hold={self.max_hold_seconds}",
            f"flow={self.min_flow_imbalance}",
            f"crowd={self.min_crowding_score if self.min_crowding_score is not None else 'none'}",
            f"depth={self.min_depth_imbalance if self.min_depth_imbalance is not None else 'none'}",
            f"spread={self.max_book_spread_bps if self.max_book_spread_bps is not None else 'none'}",
            f"fill={self.min_expected_fill_ratio if self.min_expected_fill_ratio is not None else 'none'}",
        ]
        if self.strategy_kind in {"reversion", "ensemble"}:
            parts.extend(
                [
                    f"rev_atr={self.reversion_entry_atr_multiple if self.reversion_entry_atr_multiple is not None else 'none'}",
                    f"rev_max_atr_frac={self.reversion_max_atr_fraction if self.reversion_max_atr_fraction is not None else 'none'}",
                    f"rev_flow={self.reversion_min_flow_flip if self.reversion_min_flow_flip is not None else 'none'}",
                ]
            )
        return "|".join(parts)

    def apply(self, base_config: BreakoutBacktestConfig) -> BreakoutBacktestConfig:
        return replace(
            base_config,
            strategy_kind=self.strategy_kind,
            breakout_lookback_ticks=self.breakout_lookback_ticks,
            max_hold_seconds=self.max_hold_seconds,
            min_flow_imbalance=self.min_flow_imbalance,
            min_crowding_score=self.min_crowding_score,
            min_depth_imbalance=self.min_depth_imbalance,
            max_book_spread_bps=self.max_book_spread_bps,
            min_expected_fill_ratio=self.min_expected_fill_ratio,
            reversion_lookback_ticks=self.breakout_lookback_ticks if self.strategy_kind in {"reversion", "ensemble"} else base_config.reversion_lookback_ticks,
            reversion_entry_atr_multiple=(
                self.reversion_entry_atr_multiple
                if self.reversion_entry_atr_multiple is not None
                else base_config.reversion_entry_atr_multiple
            ),
            reversion_max_atr_fraction=(
                self.reversion_max_atr_fraction
                if self.strategy_kind in {"reversion", "ensemble"}
                else base_config.reversion_max_atr_fraction
            ),
            reversion_min_flow_flip=(
                self.reversion_min_flow_flip
                if self.reversion_min_flow_flip is not None
                else base_config.reversion_min_flow_flip
            ),
        )


@dataclass(slots=True)
class WalkForwardFold:
    fold_index: int
    train_dates: list[str]
    test_dates: list[str]

    @property
    def train_start_date(self) -> str:
        return self.train_dates[0]

    @property
    def train_end_date(self) -> str:
        return self.train_dates[-1]

    @property
    def test_start_date(self) -> str:
        return self.test_dates[0]

    @property
    def test_end_date(self) -> str:
        return self.test_dates[-1]


@dataclass(frozen=True, slots=True)
class WalkForwardScoreConfig:
    max_drawdown_penalty: Decimal = Decimal("0.50")
    entry_timeout_rate_penalty: Decimal = Decimal("25")
    exit_depth_sweep_bps_penalty: Decimal = Decimal("2")
    min_trade_count: int = 1


@dataclass(slots=True)
class BacktestReportSummary:
    trade_count: int
    net_pnl: Decimal
    gross_pnl: Decimal
    fee_pnl: Decimal
    funding_pnl: Decimal
    max_drawdown: Decimal
    win_rate: Decimal
    entry_timeout_rate: Decimal | None
    average_entry_fill_latency_seconds: Decimal | None
    average_expected_fill_ratio: Decimal | None
    average_exit_depth_sweep_bps: Decimal | None
    average_economics_feedback_multiplier: Decimal | None
    last_economics_regime_action: str
    modeled_partial_entry_count: int
    modeled_partial_entry_qty: Decimal
    entry_remainder_cancel_count: int
    unmodeled_partial_entry_count: int
    unmodeled_partial_entry_qty: Decimal
    promotion_blocked_by_partial_fills: bool
    execution_fidelity_status: str


@dataclass(slots=True)
class WalkForwardCandidateScore:
    candidate: BreakoutParameterCandidate
    candidate_label: str
    score: Decimal
    eligible: bool
    reason: str
    trade_count: int
    net_pnl: Decimal
    max_drawdown: Decimal
    entry_timeout_rate: Decimal | None
    average_exit_depth_sweep_bps: Decimal | None
    score_components: dict[str, Decimal] = field(default_factory=dict)


@dataclass(slots=True)
class WalkForwardFoldResult:
    window: WalkForwardFold
    selected_candidate: BreakoutParameterCandidate | None
    selected_candidate_label: str = ""
    train_candidate_scores: list[WalkForwardCandidateScore] = field(default_factory=list)
    train_summary: BacktestReportSummary | None = None
    test_summary: BacktestReportSummary | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass(slots=True)
class WalkForwardReport:
    symbol: str
    mode: str
    available_dates: list[str]
    fold_count: int
    candidate_count: int
    score_config: WalkForwardScoreConfig
    folds: list[WalkForwardFoldResult] = field(default_factory=list)
    total_test_net_pnl: Decimal = _ZERO
    total_test_gross_pnl: Decimal = _ZERO
    total_test_fee_pnl: Decimal = _ZERO
    total_test_funding_pnl: Decimal = _ZERO
    total_test_trade_count: int = 0
    average_test_win_rate: Decimal | None = None
    average_test_entry_timeout_rate: Decimal | None = None
    average_test_exit_depth_sweep_bps: Decimal | None = None
    average_test_entry_fill_latency_seconds: Decimal | None = None
    selection_turnover_ratio: Decimal | None = None
    selected_candidate_counts: dict[str, int] = field(default_factory=dict)
    selected_parameter_value_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    skipped_fold_count: int = 0
    modeled_partial_entry_count: int = 0
    modeled_partial_entry_qty: Decimal = _ZERO
    entry_remainder_cancel_count: int = 0
    unmodeled_partial_entry_count: int = 0
    unmodeled_partial_entry_qty: Decimal = _ZERO
    promotion_blocked_by_partial_fills: bool = False


BacktestEvaluator = Callable[[BreakoutParameterCandidate, str, str], BacktestReport]


def discover_available_market_dates(
    *,
    data_dir: Path,
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    root = Path(data_dir) / "market"
    if not root.exists():
        return []
    filename = f"{symbol.lower()}_markPrice_1s.jsonl"
    dates: list[str] = []
    for day_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        day = day_dir.name
        if start_date is not None and day < start_date:
            continue
        if end_date is not None and day > end_date:
            continue
        if (day_dir / filename).exists():
            dates.append(day)
    return dates


def build_walkforward_folds(
    *,
    available_dates: list[str],
    train_days: int,
    test_days: int,
    step_days: int | None = None,
    anchored_train: bool = False,
    max_folds: int | None = None,
) -> list[WalkForwardFold]:
    if train_days <= 0:
        raise ValueError("train_days must be positive")
    if test_days <= 0:
        raise ValueError("test_days must be positive")
    if step_days is None:
        step_days = test_days
    if step_days <= 0:
        raise ValueError("step_days must be positive")

    folds: list[WalkForwardFold] = []
    cursor = 0
    while True:
        train_start_idx = 0 if anchored_train else cursor
        train_end_exclusive = cursor + train_days
        test_start_idx = train_end_exclusive
        test_end_exclusive = test_start_idx + test_days
        if test_end_exclusive > len(available_dates):
            break
        train_dates = available_dates[train_start_idx:train_end_exclusive]
        test_dates = available_dates[test_start_idx:test_end_exclusive]
        if len(train_dates) < train_days or len(test_dates) < test_days:
            break
        folds.append(
            WalkForwardFold(
                fold_index=len(folds) + 1,
                train_dates=train_dates,
                test_dates=test_dates,
            )
        )
        if max_folds is not None and len(folds) >= max_folds:
            break
        cursor += step_days
    return folds


def build_breakout_parameter_grid(
    *,
    lookbacks: Iterable[int],
    hold_seconds: Iterable[int],
    min_flow_imbalances: Iterable[Decimal],
    min_crowding_scores: Iterable[Decimal | None],
    min_depth_imbalances: Iterable[Decimal | None],
    max_book_spread_bps_values: Iterable[Decimal | None],
    min_expected_fill_ratios: Iterable[Decimal | None],
    strategy_kinds: Iterable[str] | None = None,
    reversion_entry_atr_multiples: Iterable[Decimal | None] | None = None,
    reversion_max_atr_fractions: Iterable[Decimal | None] | None = None,
    reversion_min_flow_flips: Iterable[Decimal | None] | None = None,
) -> list[BreakoutParameterCandidate]:
    candidates: list[BreakoutParameterCandidate] = []
    seen: set[str] = set()
    resolved_strategy_kinds = [
        (kind or "breakout").strip().lower()
        for kind in (strategy_kinds or ["breakout"])
    ]
    reversion_entry_atr_multiples = list(reversion_entry_atr_multiples or [Decimal("1.25")])
    reversion_max_atr_fractions = list(reversion_max_atr_fractions or [Decimal("0.0040")])
    reversion_min_flow_flips = list(reversion_min_flow_flips or [Decimal("0")])

    for strategy_kind in resolved_strategy_kinds:
        for lookback in lookbacks:
            for hold in hold_seconds:
                for min_flow in min_flow_imbalances:
                    for min_crowding in min_crowding_scores:
                        for min_depth in min_depth_imbalances:
                            for max_spread in max_book_spread_bps_values:
                                for min_fill in min_expected_fill_ratios:
                                    if strategy_kind in {"reversion", "ensemble"}:
                                        for entry_atr_multiple in reversion_entry_atr_multiples:
                                            for max_atr_fraction in reversion_max_atr_fractions:
                                                for min_flow_flip in reversion_min_flow_flips:
                                                    candidate = BreakoutParameterCandidate(
                                                        breakout_lookback_ticks=max(1, int(lookback)),
                                                        max_hold_seconds=max(1, int(hold)),
                                                        strategy_kind=strategy_kind,
                                                        min_flow_imbalance=min_flow,
                                                        min_crowding_score=min_crowding,
                                                        min_depth_imbalance=min_depth,
                                                        max_book_spread_bps=max_spread,
                                                        min_expected_fill_ratio=min_fill,
                                                        reversion_entry_atr_multiple=entry_atr_multiple,
                                                        reversion_max_atr_fraction=max_atr_fraction,
                                                        reversion_min_flow_flip=min_flow_flip,
                                                    )
                                                    if candidate.label in seen:
                                                        continue
                                                    seen.add(candidate.label)
                                                    candidates.append(candidate)
                                        continue
                                    candidate = BreakoutParameterCandidate(
                                        breakout_lookback_ticks=max(1, int(lookback)),
                                        max_hold_seconds=max(1, int(hold)),
                                        strategy_kind="router" if strategy_kind == "router" else "breakout",
                                        min_flow_imbalance=min_flow,
                                        min_crowding_score=min_crowding,
                                        min_depth_imbalance=min_depth,
                                        max_book_spread_bps=max_spread,
                                        min_expected_fill_ratio=min_fill,
                                    )
                                    if candidate.label in seen:
                                        continue
                                    seen.add(candidate.label)
                                    candidates.append(candidate)
    return candidates


def summarize_backtest_report(report: BacktestReport) -> BacktestReportSummary:
    return BacktestReportSummary(
        trade_count=report.trade_count,
        net_pnl=report.net_pnl,
        gross_pnl=report.gross_pnl,
        fee_pnl=report.fee_pnl,
        funding_pnl=report.funding_pnl,
        max_drawdown=report.max_drawdown,
        win_rate=report.win_rate,
        entry_timeout_rate=report.entry_timeout_rate,
        average_entry_fill_latency_seconds=report.average_entry_fill_latency_seconds,
        average_expected_fill_ratio=report.average_expected_fill_ratio,
        average_exit_depth_sweep_bps=report.average_exit_depth_sweep_bps,
        average_economics_feedback_multiplier=report.average_economics_feedback_multiplier,
        last_economics_regime_action=report.last_economics_regime_action,
        modeled_partial_entry_count=report.modeled_partial_entry_count,
        modeled_partial_entry_qty=report.modeled_partial_entry_qty,
        entry_remainder_cancel_count=report.entry_remainder_cancel_count,
        unmodeled_partial_entry_count=report.unmodeled_partial_entry_count,
        unmodeled_partial_entry_qty=report.unmodeled_partial_entry_qty,
        promotion_blocked_by_partial_fills=report.promotion_blocked_by_partial_fills,
        execution_fidelity_status=report.execution_fidelity_status,
    )


def score_backtest_report(
    report: BacktestReport,
    *,
    candidate: BreakoutParameterCandidate,
    score_config: WalkForwardScoreConfig,
) -> WalkForwardCandidateScore:
    if report.promotion_blocked_by_partial_fills:
        return WalkForwardCandidateScore(
            candidate=candidate,
            candidate_label=candidate.label,
            score=_NEG_INF,
            eligible=False,
            reason="unmodeled_partial_entry_exposure",
            trade_count=report.trade_count,
            net_pnl=report.net_pnl,
            max_drawdown=report.max_drawdown,
            entry_timeout_rate=report.entry_timeout_rate,
            average_exit_depth_sweep_bps=report.average_exit_depth_sweep_bps,
            score_components={
                "unmodeled_partial_entry_count": Decimal(report.unmodeled_partial_entry_count),
                "unmodeled_partial_entry_qty": report.unmodeled_partial_entry_qty,
            },
        )

    if report.trade_count < score_config.min_trade_count:
        return WalkForwardCandidateScore(
            candidate=candidate,
            candidate_label=candidate.label,
            score=_NEG_INF,
            eligible=False,
            reason="insufficient_trade_count",
            trade_count=report.trade_count,
            net_pnl=report.net_pnl,
            max_drawdown=report.max_drawdown,
            entry_timeout_rate=report.entry_timeout_rate,
            average_exit_depth_sweep_bps=report.average_exit_depth_sweep_bps,
            score_components={"trade_count": Decimal(report.trade_count)},
        )

    drawdown_penalty = score_config.max_drawdown_penalty * report.max_drawdown
    timeout_penalty = score_config.entry_timeout_rate_penalty * (report.entry_timeout_rate or _ZERO)
    exit_sweep_penalty = score_config.exit_depth_sweep_bps_penalty * (report.average_exit_depth_sweep_bps or _ZERO)
    score = report.net_pnl - drawdown_penalty - timeout_penalty - exit_sweep_penalty
    return WalkForwardCandidateScore(
        candidate=candidate,
        candidate_label=candidate.label,
        score=score,
        eligible=True,
        reason="ok",
        trade_count=report.trade_count,
        net_pnl=report.net_pnl,
        max_drawdown=report.max_drawdown,
        entry_timeout_rate=report.entry_timeout_rate,
        average_exit_depth_sweep_bps=report.average_exit_depth_sweep_bps,
        score_components={
            "net_pnl": report.net_pnl,
            "drawdown_penalty": drawdown_penalty,
            "timeout_penalty": timeout_penalty,
            "exit_sweep_penalty": exit_sweep_penalty,
        },
    )


def run_walkforward(
    *,
    symbol: str,
    mode: str,
    available_dates: list[str],
    folds: list[WalkForwardFold],
    candidates: list[BreakoutParameterCandidate],
    evaluator: BacktestEvaluator,
    score_config: WalkForwardScoreConfig,
) -> WalkForwardReport:
    report = WalkForwardReport(
        symbol=symbol,
        mode=mode,
        available_dates=list(available_dates),
        fold_count=len(folds),
        candidate_count=len(candidates),
        score_config=score_config,
    )
    selected_labels: list[str] = []
    test_win_rates: list[Decimal] = []
    test_timeout_rates: list[Decimal] = []
    test_exit_sweeps: list[Decimal] = []
    test_entry_fill_latencies: list[Decimal] = []

    for fold in folds:
        candidate_scores: list[WalkForwardCandidateScore] = []
        train_reports: dict[str, BacktestReport] = {}
        for candidate in candidates:
            train_report = evaluator(candidate, fold.train_start_date, fold.train_end_date)
            train_reports[candidate.label] = train_report
            candidate_scores.append(
                score_backtest_report(train_report, candidate=candidate, score_config=score_config)
            )

        candidate_scores.sort(key=lambda item: (item.score, item.net_pnl), reverse=True)
        eligible_scores = [score for score in candidate_scores if score.eligible]
        if not eligible_scores:
            report.skipped_fold_count += 1
            report.folds.append(
                WalkForwardFoldResult(
                    window=fold,
                    selected_candidate=None,
                    train_candidate_scores=candidate_scores,
                    skipped=True,
                    skip_reason="no_eligible_candidates",
                )
            )
            continue

        selected_score = eligible_scores[0]
        selected_candidate = selected_score.candidate
        selected_labels.append(selected_candidate.label)
        report.selected_candidate_counts[selected_candidate.label] = report.selected_candidate_counts.get(selected_candidate.label, 0) + 1
        _accumulate_selected_parameter_counts(report.selected_parameter_value_counts, selected_candidate)

        test_report = evaluator(selected_candidate, fold.test_start_date, fold.test_end_date)
        train_summary = summarize_backtest_report(train_reports[selected_candidate.label])
        test_summary = summarize_backtest_report(test_report)

        report.modeled_partial_entry_count += test_summary.modeled_partial_entry_count
        report.modeled_partial_entry_qty += test_summary.modeled_partial_entry_qty
        report.entry_remainder_cancel_count += test_summary.entry_remainder_cancel_count
        report.unmodeled_partial_entry_count += test_summary.unmodeled_partial_entry_count
        report.unmodeled_partial_entry_qty += test_summary.unmodeled_partial_entry_qty
        if test_summary.promotion_blocked_by_partial_fills:
            report.promotion_blocked_by_partial_fills = True

        report.total_test_net_pnl += test_summary.net_pnl
        report.total_test_gross_pnl += test_summary.gross_pnl
        report.total_test_fee_pnl += test_summary.fee_pnl
        report.total_test_funding_pnl += test_summary.funding_pnl
        report.total_test_trade_count += test_summary.trade_count
        test_win_rates.append(test_summary.win_rate)
        if test_summary.entry_timeout_rate is not None:
            test_timeout_rates.append(test_summary.entry_timeout_rate)
        if test_summary.average_exit_depth_sweep_bps is not None:
            test_exit_sweeps.append(test_summary.average_exit_depth_sweep_bps)
        if test_summary.average_entry_fill_latency_seconds is not None:
            test_entry_fill_latencies.append(test_summary.average_entry_fill_latency_seconds)

        report.folds.append(
            WalkForwardFoldResult(
                window=fold,
                selected_candidate=selected_candidate,
                selected_candidate_label=selected_candidate.label,
                train_candidate_scores=candidate_scores,
                train_summary=train_summary,
                test_summary=test_summary,
            )
        )

    report.average_test_win_rate = _mean(test_win_rates)
    report.average_test_entry_timeout_rate = _mean(test_timeout_rates)
    report.average_test_exit_depth_sweep_bps = _mean(test_exit_sweeps)
    report.average_test_entry_fill_latency_seconds = _mean(test_entry_fill_latencies)
    report.selection_turnover_ratio = _selection_turnover_ratio(selected_labels)
    return report


def _accumulate_selected_parameter_counts(
    accumulator: dict[str, dict[str, int]],
    candidate: BreakoutParameterCandidate,
) -> None:
    _increment_nested_count(accumulator, "strategy_kind", candidate.strategy_kind)
    _increment_nested_count(accumulator, "breakout_lookback_ticks", str(candidate.breakout_lookback_ticks))
    _increment_nested_count(accumulator, "max_hold_seconds", str(candidate.max_hold_seconds))
    _increment_nested_count(accumulator, "min_flow_imbalance", str(candidate.min_flow_imbalance))
    _increment_nested_count(
        accumulator,
        "min_crowding_score",
        str(candidate.min_crowding_score) if candidate.min_crowding_score is not None else "none",
    )
    _increment_nested_count(
        accumulator,
        "min_depth_imbalance",
        str(candidate.min_depth_imbalance) if candidate.min_depth_imbalance is not None else "none",
    )
    _increment_nested_count(
        accumulator,
        "max_book_spread_bps",
        str(candidate.max_book_spread_bps) if candidate.max_book_spread_bps is not None else "none",
    )
    _increment_nested_count(
        accumulator,
        "min_expected_fill_ratio",
        str(candidate.min_expected_fill_ratio) if candidate.min_expected_fill_ratio is not None else "none",
    )
    if candidate.strategy_kind in {"reversion", "ensemble"}:
        _increment_nested_count(
            accumulator,
            "reversion_entry_atr_multiple",
            str(candidate.reversion_entry_atr_multiple) if candidate.reversion_entry_atr_multiple is not None else "none",
        )
        _increment_nested_count(
            accumulator,
            "reversion_max_atr_fraction",
            str(candidate.reversion_max_atr_fraction) if candidate.reversion_max_atr_fraction is not None else "none",
        )
        _increment_nested_count(
            accumulator,
            "reversion_min_flow_flip",
            str(candidate.reversion_min_flow_flip) if candidate.reversion_min_flow_flip is not None else "none",
        )


def _increment_nested_count(accumulator: dict[str, dict[str, int]], key: str, value: str) -> None:
    bucket = accumulator.setdefault(key, {})
    bucket[value] = bucket.get(value, 0) + 1


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, start=_ZERO) / Decimal(len(values))


def _selection_turnover_ratio(selected_labels: list[str]) -> Decimal | None:
    if len(selected_labels) <= 1:
        return None
    changes = 0
    for previous, current in zip(selected_labels, selected_labels[1:], strict=False):
        if current != previous:
            changes += 1
    return Decimal(changes) / Decimal(len(selected_labels) - 1)
