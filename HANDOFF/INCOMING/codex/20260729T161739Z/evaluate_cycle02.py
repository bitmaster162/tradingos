#!/usr/bin/env python3
"""Evaluate the three frozen R57 hypotheses without parameter search."""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import json
import math
import random
import statistics
import zipfile
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


BOOTSTRAP_SEED = 5702
BOOTSTRAP_N = 10_000


@dataclass(frozen=True)
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Trade:
    signal_ts: int
    entry_ts: int
    exit_ts: int
    direction: int
    gross_return: float
    cost_return: float
    net_return: float


def normalize_ms(raw: str | int) -> int:
    value = int(raw)
    while value > 99_999_999_999_999:
        value //= 1000
    return value


def read_zip_lines(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise ValueError(f"{path}: expected one CSV member")
        return archive.read(members[0]).decode("utf-8").splitlines()


def load_bars(paths: Iterable[Path]) -> list[Bar]:
    bars: dict[int, Bar] = {}
    for path in sorted(paths):
        rows = csv.reader(read_zip_lines(path))
        for row in rows:
            if not row or not row[0].lstrip("-").isdigit():
                continue
            ts = normalize_ms(row[0])
            bars[ts] = Bar(ts, float(row[1]), float(row[2]), float(row[3]), float(row[4]))
    return [bars[key] for key in sorted(bars)]


def load_funding(paths: Iterable[Path]) -> list[tuple[int, float]]:
    values: dict[int, float] = {}
    for path in sorted(paths):
        reader = csv.DictReader(read_zip_lines(path))
        for row in reader:
            values[normalize_ms(row["calc_time"])] = float(row["last_funding_rate"])
    return sorted(values.items())


def trade(signal_ts: int, entry: Bar, exit_bar: Bar, direction: int, cost: float) -> Trade:
    gross = direction * (exit_bar.open / entry.open - 1.0)
    return Trade(signal_ts, entry.ts, exit_bar.ts, direction, gross, cost, gross - cost)


def funding_reversal(events: list[tuple[int, float]], bars: list[Bar]) -> list[Trade]:
    times = [bar.ts for bar in bars]
    result: list[Trade] = []
    last_exit = -1
    for event_ts, rate in events:
        if abs(rate) < 0.0003:
            continue
        entry_index = bisect.bisect_right(times, event_ts)
        exit_index = entry_index + 8
        if exit_index >= len(bars):
            continue
        entry, exit_bar = bars[entry_index], bars[exit_index]
        if entry.ts <= last_exit or exit_bar.ts - entry.ts != 8 * 3_600_000:
            continue
        direction = -1 if rate > 0 else 1
        result.append(trade(event_ts, entry, exit_bar, direction, 0.0012))
        last_exit = exit_bar.ts
    return result


def btc_eth_lead_lag(btc: list[Bar], eth: list[Bar]) -> list[Trade]:
    eth_by_ts = {bar.ts: bar for bar in eth}
    result: list[Trade] = []
    last_exit = -1
    for index in range(1, len(btc) - 5):
        current, previous = btc[index], btc[index - 1]
        eth_current = eth_by_ts.get(current.ts)
        eth_previous = eth_by_ts.get(previous.ts)
        entry = eth_by_ts.get(btc[index + 1].ts)
        exit_bar = eth_by_ts.get(btc[index + 5].ts)
        if not all((eth_current, eth_previous, entry, exit_bar)):
            continue
        if current.ts - previous.ts != 900_000 or exit_bar.ts - entry.ts != 3_600_000:
            continue
        btc_return = current.close / previous.close - 1.0
        eth_return = eth_current.close / eth_previous.close - 1.0
        if abs(btc_return) < 0.008:
            continue
        if eth_return != 0 and math.copysign(1, eth_return) != math.copysign(1, btc_return):
            continue
        if abs(eth_return) > 0.40 * abs(btc_return):
            continue
        if entry.ts <= last_exit:
            continue
        direction = 1 if btc_return > 0 else -1
        result.append(trade(current.ts, entry, exit_bar, direction, 0.0024))
        last_exit = exit_bar.ts
    return result


def compression_breakout(bars: list[Bar]) -> list[Trade]:
    result: list[Trade] = []
    last_exit = -1
    for index in range(24, len(bars) - 13):
        prior = bars[index - 24 : index]
        current = bars[index]
        if any(prior[i].ts - prior[i - 1].ts != 3_600_000 for i in range(1, len(prior))):
            continue
        prior_high = max(bar.high for bar in prior)
        prior_low = min(bar.low for bar in prior)
        if (prior_high - prior_low) / prior[-1].close > 0.03:
            continue
        direction = 1 if current.close > prior_high else -1 if current.close < prior_low else 0
        if not direction:
            continue
        entry, exit_bar = bars[index + 1], bars[index + 13]
        if entry.ts <= last_exit or exit_bar.ts - entry.ts != 12 * 3_600_000:
            continue
        result.append(trade(current.ts, entry, exit_bar, direction, 0.0024))
        last_exit = exit_bar.ts
    return result


def year_of(ts: int) -> int:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).year


def bootstrap_lower(values: list[float]) -> float | None:
    if not values:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    size = len(values)
    means = [sum(rng.choice(values) for _ in range(size)) / size for _ in range(BOOTSTRAP_N)]
    means.sort()
    return means[int(0.025 * BOOTSTRAP_N)]


def quarter_concentration(trades: list[Trade]) -> float | None:
    by_quarter: dict[str, float] = defaultdict(float)
    for item in trades:
        if item.net_return > 0:
            date = datetime.fromtimestamp(item.entry_ts / 1000, tz=timezone.utc)
            by_quarter[f"{date.year}-Q{(date.month - 1) // 3 + 1}"] += item.net_return
    total = sum(by_quarter.values())
    return max(by_quarter.values(), default=0.0) / total if total else None


def metrics(trades: list[Trade]) -> dict[str, object]:
    values = [item.net_return for item in trades]
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return {
        "n": len(values),
        "net_mean": statistics.fmean(values) if values else None,
        "net_median": statistics.median(values) if values else None,
        "net_sum": sum(values),
        "win_rate": sum(value > 0 for value in values) / len(values) if values else None,
        "bootstrap_95_lower_mean": bootstrap_lower(values),
        "profit_factor": gains / losses if losses else None,
        "max_positive_quarter_concentration": quarter_concentration(trades),
    }


def disposition(hypothesis: str, result: dict[str, object]) -> tuple[str, str]:
    n = int(result["n"])
    mean = result["net_mean"]
    lower = result["bootstrap_95_lower_mean"]
    concentration = result["max_positive_quarter_concentration"]
    if hypothesis == "H01":
        if n < 20:
            return "INSUFFICIENT_DATA", "fewer than 20 OOS trades"
        keep = n >= 30 and mean > 0 and lower > 0 and concentration <= 0.70
    elif hypothesis == "H02":
        if n < 40:
            return "INSUFFICIENT_DATA", "fewer than 40 OOS trades"
        keep = (
            n >= 100
            and mean > 0
            and lower > 0
            and result["net_median"] > 0
            and concentration <= 0.70
        )
    else:
        if n < 25:
            return "INSUFFICIENT_DATA", "fewer than 25 OOS trades"
        keep = (
            n >= 50
            and mean > 0
            and lower > 0
            and result["profit_factor"] is not None
            and result["profit_factor"] > 1.10
            and concentration <= 0.70
        )
    if keep:
        return "KEEP_FOR_FORWARD_WATCH", "all frozen OOS gates passed"
    return "KILL", "one or more frozen OOS gates failed"


def next_requirement(hypothesis: str, decision: str, n: int) -> str:
    if decision == "KILL":
        return "none; hypothesis is closed and may not be retuned in this cycle"
    if hypothesis == "H01":
        return (
            f"at least {max(0, 20 - n)} additional frozen OOS trades to classify; "
            f"{max(0, 30 - n)} additional trades to reach the keep sample floor"
        )
    if hypothesis == "H02":
        return (
            f"at least {max(0, 40 - n)} additional frozen OOS trades to classify; "
            f"{max(0, 100 - n)} additional trades to reach the keep sample floor"
        )
    return (
        f"at least {max(0, 25 - n)} additional frozen OOS trades to classify; "
        f"{max(0, 50 - n)} additional trades to reach the keep sample floor"
    )


def write_hypothesis(
    root: Path, name: str, trades: list[Trade], source_records: list[dict[str, object]]
) -> dict[str, object]:
    is_trades = [item for item in trades if year_of(item.entry_ts) == 2024]
    oos_trades = [item for item in trades if year_of(item.entry_ts) == 2025]
    is_metrics, oos_metrics = metrics(is_trades), metrics(oos_trades)
    short = name.split("_", 1)[0]
    decision, reason = disposition(short, oos_metrics)
    requirement = next_requirement(short, decision, int(oos_metrics["n"]))
    result = {
        "hypothesis": name,
        "decision": decision,
        "reason": reason,
        "next_minimum_observation_requirement": requirement,
        "in_sample": is_metrics,
        "out_of_sample": oos_metrics,
        "cost_deducted_once": True,
        "same_snapshot_entry_exit": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }
    target = root / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (target / "source_manifest.json").write_text(
        json.dumps(
            {
                "schema": "TRADINGOS_R57_HYPOTHESIS_SOURCE_MANIFEST_V1",
                "hypothesis": name,
                "files": sorted(source_records, key=lambda item: str(item["source_id"])),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with (target / "oos_trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(oos_trades[0]).keys()) if oos_trades else [
            "signal_ts", "entry_ts", "exit_ts", "direction", "gross_return", "cost_return", "net_return"
        ])
        writer.writeheader()
        writer.writerows(asdict(item) for item in oos_trades)
    report = [
        f"# {name}: falsification result",
        "",
        f"**Disposition:** `{decision}`",
        "",
        f"Reason: {reason}.",
        f"Next minimum observation requirement: {requirement}.",
        "",
        "## Frozen split",
        "",
        f"- IS 2024: `{json.dumps(is_metrics, sort_keys=True)}`",
        f"- OOS 2025: `{json.dumps(oos_metrics, sort_keys=True)}`",
        "",
        "Costs were deducted once. Entry and exit are distinct bar opens. "
        "No overlapping positions were permitted.",
        "",
        "This result is research-only and cannot authorize execution.",
        "",
    ]
    (target / "FALSIFICATION.md").write_text("\n".join(report), encoding="utf-8")
    return result


def files(root: Path, fragment: str) -> list[Path]:
    return sorted(path for path in root.rglob("*.zip") if fragment in path.as_posix())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    args = parser.parse_args()
    source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    source_files = source_manifest["files"]

    futures_1h = load_bars(files(args.data, "futures/um/monthly/klines/BTCUSDT/1h"))
    funding = load_funding(files(args.data, "futures/um/monthly/fundingRate/BTCUSDT"))
    btc_15m = load_bars(files(args.data, "spot/monthly/klines/BTCUSDT/15m"))
    eth_15m = load_bars(files(args.data, "spot/monthly/klines/ETHUSDT/15m"))
    btc_1h = load_bars(files(args.data, "spot/monthly/klines/BTCUSDT/1h"))

    args.out.mkdir(parents=True, exist_ok=True)
    outcomes = [
        write_hypothesis(
            args.out,
            "H01_FUNDING_EXTREME_REVERSAL",
            funding_reversal(funding, futures_1h),
            [
                item
                for item in source_files
                if "futures/um/monthly/fundingRate/BTCUSDT" in item["source_id"]
                or "futures/um/monthly/klines/BTCUSDT/1h" in item["source_id"]
            ],
        ),
        write_hypothesis(
            args.out,
            "H02_BTC_ETH_LEAD_LAG",
            btc_eth_lead_lag(btc_15m, eth_15m),
            [
                item
                for item in source_files
                if "spot/monthly/klines/BTCUSDT/15m" in item["source_id"]
                or "spot/monthly/klines/ETHUSDT/15m" in item["source_id"]
            ],
        ),
        write_hypothesis(
            args.out,
            "H03_BTC_COMPRESSION_BREAKOUT",
            compression_breakout(btc_1h),
            [
                item
                for item in source_files
                if "spot/monthly/klines/BTCUSDT/1h" in item["source_id"]
            ],
        ),
    ]
    summary = {
        "schema": "TRADINGOS_R57_BOUNDED_EDGE_CYCLE_02_V1",
        "hypotheses": outcomes,
        "hypothesis_count": 3,
        "no_parameter_search": True,
        "data_quality": {
            "funding_events": len(funding),
            "futures_btc_1h_bars": len(futures_1h),
            "spot_btc_15m_bars": len(btc_15m),
            "spot_eth_15m_bars": len(eth_15m),
            "spot_btc_1h_bars": len(btc_1h),
        },
        "can_trade": False,
        "capital_permission": "DENY",
    }
    (args.out / "CYCLE_RESULT.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({item["hypothesis"]: item["decision"] for item in outcomes}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
