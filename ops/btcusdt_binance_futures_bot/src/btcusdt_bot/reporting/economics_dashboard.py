from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

_ZERO = Decimal("0")


def _to_decimal(value: object) -> Decimal | None:
    if value in {None, "", "None"}:
        return None
    return Decimal(str(value))


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, start=_ZERO) / Decimal(len(values))


def _weighted_bps(*, pnl: Decimal, quote_qty: Decimal) -> Decimal:
    if quote_qty <= _ZERO:
        return _ZERO
    return (pnl / quote_qty) * Decimal("10000")


@dataclass(slots=True)
class EconomicsDaySnapshot:
    date: str
    compared_at_ms: int = 0
    active_bucket_count: int = 0
    negative_bucket_ratio: Decimal = _ZERO
    trailing_negative_bucket_streak: int = 0
    exchange_trade_count: int = 0
    exchange_order_count: int = 0
    exchange_quote_qty_usdt: Decimal = _ZERO
    net_realized_pnl_usdt: Decimal = _ZERO
    net_realized_bps: Decimal = _ZERO
    maker_ratio: Decimal = _ZERO
    commission_bps: Decimal = _ZERO
    funding_bps: Decimal = _ZERO
    recent_bucket_net_realized_bps: Decimal = _ZERO
    recent_two_bucket_net_realized_bps: Decimal = _ZERO
    cumulative_drawdown_usdt: Decimal = _ZERO

    @property
    def active(self) -> bool:
        return self.active_bucket_count > 0 or self.exchange_trade_count > 0 or self.exchange_quote_qty_usdt > _ZERO

    @property
    def negative_day(self) -> bool:
        return self.net_realized_pnl_usdt < _ZERO or self.net_realized_bps < _ZERO

    @classmethod
    def from_report_payload(cls, *, date: str, payload: dict[str, object]) -> "EconomicsDaySnapshot":
        return cls(
            date=date,
            compared_at_ms=int(payload.get("compared_at_ms", 0) or 0),
            active_bucket_count=int(payload.get("active_bucket_count", 0) or 0),
            negative_bucket_ratio=_to_decimal(payload.get("negative_bucket_ratio")) or _ZERO,
            trailing_negative_bucket_streak=int(payload.get("trailing_negative_bucket_streak", 0) or 0),
            exchange_trade_count=int(payload.get("exchange_trade_count", 0) or 0),
            exchange_order_count=int(payload.get("exchange_order_count", 0) or 0),
            exchange_quote_qty_usdt=_to_decimal(payload.get("exchange_quote_qty_usdt")) or _ZERO,
            net_realized_pnl_usdt=_to_decimal(payload.get("net_realized_pnl_usdt")) or _ZERO,
            net_realized_bps=_to_decimal(payload.get("net_realized_bps")) or _ZERO,
            maker_ratio=_to_decimal(payload.get("maker_ratio")) or _ZERO,
            commission_bps=_to_decimal(payload.get("commission_bps")) or _ZERO,
            funding_bps=_to_decimal(payload.get("funding_bps")) or _ZERO,
            recent_bucket_net_realized_bps=_to_decimal(payload.get("recent_bucket_net_realized_bps")) or _ZERO,
            recent_two_bucket_net_realized_bps=_to_decimal(payload.get("recent_two_bucket_net_realized_bps")) or _ZERO,
            cumulative_drawdown_usdt=_to_decimal(payload.get("cumulative_drawdown_usdt")) or _ZERO,
        )


@dataclass(slots=True)
class EconomicsDashboard:
    symbol: str
    end_date: str
    start_date: str
    lookback_days: int
    available_day_count: int = 0
    missing_day_count: int = 0
    active_day_count: int = 0
    negative_day_count: int = 0
    negative_day_ratio: Decimal = _ZERO
    trailing_negative_day_streak: int = 0
    total_exchange_trade_count: int = 0
    total_exchange_order_count: int = 0
    total_exchange_quote_qty_usdt: Decimal = _ZERO
    total_net_realized_pnl_usdt: Decimal = _ZERO
    aggregate_net_realized_bps: Decimal = _ZERO
    average_daily_net_realized_bps: Decimal = _ZERO
    recent_day_net_realized_bps: Decimal = _ZERO
    recent_two_day_net_realized_bps: Decimal = _ZERO
    average_maker_ratio: Decimal = _ZERO
    average_commission_bps: Decimal = _ZERO
    average_funding_bps: Decimal = _ZERO
    average_negative_bucket_ratio: Decimal = _ZERO
    cumulative_drawdown_usdt: Decimal = _ZERO
    worst_day_net_realized_bps: Decimal = _ZERO
    best_day_net_realized_bps: Decimal = _ZERO
    best_day_date: str = ""
    worst_day_date: str = ""
    days: list[EconomicsDaySnapshot] = field(default_factory=list)

    @property
    def sample_ready(self) -> bool:
        return self.active_day_count > 0




    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "EconomicsDashboard":
        raw_days = payload.get("days")
        days: list[EconomicsDaySnapshot] = []
        if isinstance(raw_days, list):
            for item in raw_days:
                if not isinstance(item, dict):
                    continue
                date = str(item.get("date", "") or "")
                if not date:
                    continue
                days.append(EconomicsDaySnapshot.from_report_payload(date=date, payload=item))
        return cls(
            symbol=str(payload.get("symbol", "") or ""),
            end_date=str(payload.get("end_date", "") or ""),
            start_date=str(payload.get("start_date", "") or ""),
            lookback_days=int(payload.get("lookback_days", 0) or 0),
            available_day_count=int(payload.get("available_day_count", 0) or 0),
            missing_day_count=int(payload.get("missing_day_count", 0) or 0),
            active_day_count=int(payload.get("active_day_count", 0) or 0),
            negative_day_count=int(payload.get("negative_day_count", 0) or 0),
            negative_day_ratio=_to_decimal(payload.get("negative_day_ratio")) or _ZERO,
            trailing_negative_day_streak=int(payload.get("trailing_negative_day_streak", 0) or 0),
            total_exchange_trade_count=int(payload.get("total_exchange_trade_count", 0) or 0),
            total_exchange_order_count=int(payload.get("total_exchange_order_count", 0) or 0),
            total_exchange_quote_qty_usdt=_to_decimal(payload.get("total_exchange_quote_qty_usdt")) or _ZERO,
            total_net_realized_pnl_usdt=_to_decimal(payload.get("total_net_realized_pnl_usdt")) or _ZERO,
            aggregate_net_realized_bps=_to_decimal(payload.get("aggregate_net_realized_bps")) or _ZERO,
            average_daily_net_realized_bps=_to_decimal(payload.get("average_daily_net_realized_bps")) or _ZERO,
            recent_day_net_realized_bps=_to_decimal(payload.get("recent_day_net_realized_bps")) or _ZERO,
            recent_two_day_net_realized_bps=_to_decimal(payload.get("recent_two_day_net_realized_bps")) or _ZERO,
            average_maker_ratio=_to_decimal(payload.get("average_maker_ratio")) or _ZERO,
            average_commission_bps=_to_decimal(payload.get("average_commission_bps")) or _ZERO,
            average_funding_bps=_to_decimal(payload.get("average_funding_bps")) or _ZERO,
            average_negative_bucket_ratio=_to_decimal(payload.get("average_negative_bucket_ratio")) or _ZERO,
            cumulative_drawdown_usdt=_to_decimal(payload.get("cumulative_drawdown_usdt")) or _ZERO,
            worst_day_net_realized_bps=_to_decimal(payload.get("worst_day_net_realized_bps")) or _ZERO,
            best_day_net_realized_bps=_to_decimal(payload.get("best_day_net_realized_bps")) or _ZERO,
            best_day_date=str(payload.get("best_day_date", "") or ""),
            worst_day_date=str(payload.get("worst_day_date", "") or ""),
            days=days,
        )


@dataclass(slots=True)
class EconomicsDashboardStatus:
    reports_loaded: int = 0
    missing_days: int = 0
    last_dashboard_path: str = ""
    last_error: str = ""


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _iter_dates(*, end_date: str, lookback_days: int) -> list[str]:
    end_day = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=UTC)
    dates: list[str] = []
    for offset in range(max(1, int(lookback_days))):
        dates.append((end_day - timedelta(days=offset)).strftime("%Y-%m-%d"))
    return list(reversed(dates))


def load_daily_session_truth_report(*, data_dir: Path, symbol: str, date: str) -> EconomicsDaySnapshot | None:
    symbol_key = symbol.lower()
    path = Path(data_dir) / "reports" / date / f"{symbol_key}_session_truth_report.jsonl"
    records = _load_jsonl(path)
    latest_payload: dict[str, object] | None = None
    for record in records:
        report = record.get("report") if isinstance(record.get("report"), dict) else record
        if isinstance(report, dict):
            latest_payload = report
    if latest_payload is None:
        return None
    return EconomicsDaySnapshot.from_report_payload(date=date, payload=latest_payload)


def build_economics_dashboard(*, data_dir: Path, symbol: str, end_date: str, lookback_days: int) -> EconomicsDashboard:
    dates = _iter_dates(end_date=end_date, lookback_days=lookback_days)
    day_snapshots: list[EconomicsDaySnapshot] = []
    missing_days = 0
    for date in dates:
        snapshot = load_daily_session_truth_report(data_dir=data_dir, symbol=symbol, date=date)
        if snapshot is None:
            missing_days += 1
            continue
        day_snapshots.append(snapshot)

    active_days = [day for day in day_snapshots if day.active]
    negative_days = [day for day in active_days if day.negative_day]
    negative_day_ratio = (
        Decimal(len(negative_days)) / Decimal(len(active_days)) if active_days else _ZERO
    )
    trailing_negative_day_streak = 0
    for day in reversed(active_days):
        if day.negative_day:
            trailing_negative_day_streak += 1
        else:
            break

    total_exchange_trade_count = sum(day.exchange_trade_count for day in active_days)
    total_exchange_order_count = sum(day.exchange_order_count for day in active_days)
    total_exchange_quote_qty_usdt = sum((day.exchange_quote_qty_usdt for day in active_days), start=_ZERO)
    total_net_realized_pnl_usdt = sum((day.net_realized_pnl_usdt for day in active_days), start=_ZERO)
    aggregate_net_realized_bps = _weighted_bps(
        pnl=total_net_realized_pnl_usdt,
        quote_qty=total_exchange_quote_qty_usdt,
    )
    average_daily_net_realized_bps = _mean([day.net_realized_bps for day in active_days]) or _ZERO
    average_maker_ratio = _mean([day.maker_ratio for day in active_days]) or _ZERO
    average_commission_bps = _mean([day.commission_bps for day in active_days]) or _ZERO
    average_funding_bps = _mean([day.funding_bps for day in active_days]) or _ZERO
    average_negative_bucket_ratio = _mean([day.negative_bucket_ratio for day in active_days]) or _ZERO

    recent_day_net_realized_bps = active_days[-1].net_realized_bps if active_days else _ZERO
    if len(active_days) >= 2:
        recent_two_days = active_days[-2:]
        recent_two_day_net_realized_bps = _weighted_bps(
            pnl=sum((day.net_realized_pnl_usdt for day in recent_two_days), start=_ZERO),
            quote_qty=sum((day.exchange_quote_qty_usdt for day in recent_two_days), start=_ZERO),
        )
    else:
        recent_two_day_net_realized_bps = recent_day_net_realized_bps

    cumulative_drawdown_usdt = _ZERO
    running_equity = _ZERO
    peak_equity = _ZERO
    for day in active_days:
        running_equity += day.net_realized_pnl_usdt
        if running_equity > peak_equity:
            peak_equity = running_equity
        drawdown = peak_equity - running_equity
        if drawdown > cumulative_drawdown_usdt:
            cumulative_drawdown_usdt = drawdown

    best_day = max(active_days, key=lambda day: day.net_realized_bps, default=None)
    worst_day = min(active_days, key=lambda day: day.net_realized_bps, default=None)

    return EconomicsDashboard(
        symbol=symbol,
        end_date=end_date,
        start_date=dates[0],
        lookback_days=max(1, int(lookback_days)),
        available_day_count=len(day_snapshots),
        missing_day_count=missing_days,
        active_day_count=len(active_days),
        negative_day_count=len(negative_days),
        negative_day_ratio=negative_day_ratio,
        trailing_negative_day_streak=trailing_negative_day_streak,
        total_exchange_trade_count=total_exchange_trade_count,
        total_exchange_order_count=total_exchange_order_count,
        total_exchange_quote_qty_usdt=total_exchange_quote_qty_usdt,
        total_net_realized_pnl_usdt=total_net_realized_pnl_usdt,
        aggregate_net_realized_bps=aggregate_net_realized_bps,
        average_daily_net_realized_bps=average_daily_net_realized_bps,
        recent_day_net_realized_bps=recent_day_net_realized_bps,
        recent_two_day_net_realized_bps=recent_two_day_net_realized_bps,
        average_maker_ratio=average_maker_ratio,
        average_commission_bps=average_commission_bps,
        average_funding_bps=average_funding_bps,
        average_negative_bucket_ratio=average_negative_bucket_ratio,
        cumulative_drawdown_usdt=cumulative_drawdown_usdt,
        worst_day_net_realized_bps=worst_day.net_realized_bps if worst_day is not None else _ZERO,
        best_day_net_realized_bps=best_day.net_realized_bps if best_day is not None else _ZERO,
        best_day_date=best_day.date if best_day is not None else "",
        worst_day_date=worst_day.date if worst_day is not None else "",
        days=day_snapshots,
    )
