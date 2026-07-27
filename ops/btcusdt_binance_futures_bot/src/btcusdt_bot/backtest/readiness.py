from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StreamSpec:
    namespace: str
    filename: str
    label: str
    required: bool = True
    aliases: tuple[str, ...] = ()

    def path_for(self, data_dir: Path, day: str) -> Path:
        return Path(data_dir) / self.namespace / day / self.filename

    def candidate_paths_for(self, data_dir: Path, day: str) -> tuple[Path, ...]:
        day_dir = Path(data_dir) / self.namespace / day
        return tuple(day_dir / filename for filename in (self.filename, *self.aliases))


@dataclass(frozen=True, slots=True)
class StreamCoverage:
    label: str
    namespace: str
    filename: str
    required: bool
    present_days: tuple[str, ...]
    missing_days: tuple[str, ...]
    line_counts: dict[str, int]

    @property
    def coverage_ratio(self) -> float:
        total_days = len(self.present_days) + len(self.missing_days)
        if total_days <= 0:
            return 0.0
        return len(self.present_days) / total_days

    @property
    def min_line_count(self) -> int | None:
        if not self.line_counts:
            return None
        return min(self.line_counts.values())


@dataclass(frozen=True, slots=True)
class BacktestReadinessReport:
    symbol: str
    data_dir: Path
    start_date: str | None
    end_date: str | None
    days: tuple[str, ...]
    requested_mode: str
    recommendation: str
    requested_streams: tuple[StreamCoverage, ...]
    missing_required_streams: tuple[str, ...]
    low_density_warnings: tuple[str, ...]
    recommended_command: str


def build_backtest_readiness_report(
    data_dir: Path,
    *,
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    mark_only: bool = False,
    crowding_period: str = "5m",
    depth_levels: int = 20,
    use_rpi_depth_fills: bool = True,
    ignore_contract_status: bool = False,
) -> BacktestReadinessReport:
    days = discover_market_days(data_dir, start_date=start_date, end_date=end_date)
    specs = build_requested_stream_specs(
        symbol=symbol,
        mark_only=mark_only,
        crowding_period=crowding_period,
        depth_levels=depth_levels,
        use_rpi_depth_fills=use_rpi_depth_fills,
        ignore_contract_status=ignore_contract_status,
    )
    coverage = tuple(evaluate_stream_coverage(data_dir, days=days, spec=spec) for spec in specs)
    missing_required_streams = tuple(
        item.label for item in coverage if item.required and item.missing_days
    )
    low_density_warnings = tuple(
        f"{item.label}: min_line_count={item.min_line_count}"
        for item in coverage
        if item.min_line_count is not None and item.min_line_count < 60
    )
    requested_mode = "mark_only" if mark_only else "multistream_parity"
    recommendation = decide_recommendation(mark_only=mark_only, days=days, coverage=coverage)
    recommended_command = build_recommended_command(
        recommendation=recommendation,
        start_date=start_date,
        end_date=end_date,
    )
    return BacktestReadinessReport(
        symbol=symbol,
        data_dir=Path(data_dir),
        start_date=start_date,
        end_date=end_date,
        days=days,
        requested_mode=requested_mode,
        recommendation=recommendation,
        requested_streams=coverage,
        missing_required_streams=missing_required_streams,
        low_density_warnings=low_density_warnings,
        recommended_command=recommended_command,
    )


def discover_market_days(
    data_dir: Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[str, ...]:
    market_root = Path(data_dir) / "market"
    if not market_root.exists():
        return ()
    days: list[str] = []
    for day_dir in sorted(path for path in market_root.iterdir() if path.is_dir()):
        day = day_dir.name
        if start_date is not None and day < start_date:
            continue
        if end_date is not None and day > end_date:
            continue
        days.append(day)
    return tuple(days)


def build_requested_stream_specs(
    *,
    symbol: str,
    mark_only: bool,
    crowding_period: str,
    depth_levels: int,
    use_rpi_depth_fills: bool,
    ignore_contract_status: bool,
) -> tuple[StreamSpec, ...]:
    specs: list[StreamSpec] = [
        StreamSpec("market", f"{symbol.lower()}_markPrice_1s.jsonl", "mark_price_1s", True),
    ]
    if mark_only:
        return tuple(specs)
    specs.extend(
        [
            StreamSpec("market", f"{symbol.lower()}_aggTrade.jsonl", "agg_trade", True),
            StreamSpec(
                "public",
                f"{symbol.lower()}_bookTicker.jsonl",
                "book_ticker",
                True,
                aliases=(f"{symbol.lower()}@bookTicker.jsonl",),
            ),
            StreamSpec("public", f"{symbol.lower()}_localDepth{depth_levels}.jsonl", f"local_depth_{depth_levels}", True),
            StreamSpec("crowding", f"{symbol.lower()}_{crowding_period}.jsonl", f"crowding_{crowding_period}", True),
        ]
    )
    if not ignore_contract_status:
        specs.append(StreamSpec("market", "contractInfo.jsonl", "contract_info", True))
    if use_rpi_depth_fills:
        specs.append(
            StreamSpec(
                "public",
                f"{symbol.lower()}_localRpiDepth{depth_levels}.jsonl",
                f"local_rpi_depth_{depth_levels}",
                False,
            )
        )
    return tuple(specs)


def evaluate_stream_coverage(data_dir: Path, *, days: tuple[str, ...], spec: StreamSpec) -> StreamCoverage:
    present_days: list[str] = []
    missing_days: list[str] = []
    line_counts: dict[str, int] = {}
    for day in days:
        path = next((candidate for candidate in spec.candidate_paths_for(data_dir, day) if candidate.exists()), None)
        if path is not None:
            present_days.append(day)
            line_counts[day] = count_lines(path)
        else:
            missing_days.append(day)
    return StreamCoverage(
        label=spec.label,
        namespace=spec.namespace,
        filename=spec.filename,
        required=spec.required,
        present_days=tuple(present_days),
        missing_days=tuple(missing_days),
        line_counts=line_counts,
    )


def count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def decide_recommendation(
    *,
    mark_only: bool,
    days: tuple[str, ...],
    coverage: tuple[StreamCoverage, ...],
) -> str:
    if not days:
        return "no_market_days_found"
    required = tuple(item for item in coverage if item.required)
    missing_required = tuple(item for item in required if item.missing_days)
    if mark_only:
        return "mark_only_ready" if not missing_required else "mark_only_missing_required_streams"
    if missing_required:
        mark_only_ready = not coverage[0].missing_days if coverage else False
        return "mark_only_only" if mark_only_ready else "multistream_missing_required_streams"
    if any((item.min_line_count or 0) < 60 for item in required):
        return "multistream_ready_but_sample_sparse"
    return "multistream_ready"


def build_recommended_command(*, recommendation: str, start_date: str | None, end_date: str | None) -> str:
    args: list[str] = ["python -m btcusdt_bot backtest-breakout"]
    if start_date is not None:
        args.extend(["--start-date", start_date])
    if end_date is not None:
        args.extend(["--end-date", end_date])
    if recommendation in {"mark_only_ready", "mark_only_only", "mark_only_missing_required_streams", "multistream_missing_required_streams"}:
        args.append("--mark-only")
    return " ".join(args)
