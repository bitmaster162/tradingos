#!/usr/bin/env python3
"""Evaluate exactly two frozen R59 hypotheses without parameter search."""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import random
import statistics
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


BOOTSTRAP_SEED = 5702
BOOTSTRAP_N = 10_000
H01 = "H01_FUNDING_EXTREME_REVERSAL"
H02 = "H02_BTC_ETH_LEAD_LAG"
TRADE_FIELDS = (
    "signal_ts",
    "entry_ts",
    "exit_ts",
    "direction",
    "gross_return",
    "cost_return",
    "net_return",
)


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


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


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


def read_trade_csv(path: Path) -> list[Trade]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        if tuple(rows.fieldnames or ()) != TRADE_FIELDS:
            raise ValueError(f"{path}: unexpected trade schema")
        return [
            Trade(
                signal_ts=int(row["signal_ts"]),
                entry_ts=int(row["entry_ts"]),
                exit_ts=int(row["exit_ts"]),
                direction=int(row["direction"]),
                gross_return=float(row["gross_return"]),
                cost_return=float(row["cost_return"]),
                net_return=float(row["net_return"]),
            )
            for row in rows
        ]


def write_trade_csv(path: Path, trades: list[Trade]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_FIELDS)
        writer.writeheader()
        writer.writerows(asdict(item) for item in trades)


def validate_trades(trades: list[Trade], expected_cost: float) -> None:
    previous_exit = -1
    identities: set[tuple[int, int, int]] = set()
    for item in sorted(trades, key=lambda row: row.entry_ts):
        identity = (item.signal_ts, item.entry_ts, item.exit_ts)
        if identity in identities:
            raise ValueError("duplicate trade identity")
        identities.add(identity)
        if not item.signal_ts < item.entry_ts < item.exit_ts:
            raise ValueError("trade chronology is not strictly causal")
        if item.entry_ts <= previous_exit:
            raise ValueError("overlapping trades")
        if not math.isclose(item.cost_return, expected_cost, rel_tol=0, abs_tol=1e-12):
            raise ValueError("unexpected cost")
        if not math.isclose(
            item.net_return,
            item.gross_return - item.cost_return,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError("cost was not deducted exactly once")
        previous_exit = item.exit_ts


def merge_ledgers(prior: list[Trade], extension: list[Trade], expected_cost: float) -> list[Trade]:
    merged = sorted([*prior, *extension], key=lambda item: item.entry_ts)
    validate_trades(merged, expected_cost)
    return merged


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
    if hypothesis == "H01":
        if n < 20:
            return "INSUFFICIENT_DATA", "fewer than 20 cumulative OOS trades"
        keep = (
            n >= 30
            and result["net_mean"] is not None
            and result["net_mean"] > 0
            and result["bootstrap_95_lower_mean"] is not None
            and result["bootstrap_95_lower_mean"] > 0
            and result["max_positive_quarter_concentration"] is not None
            and result["max_positive_quarter_concentration"] <= 0.70
        )
    elif hypothesis == "H02":
        if n < 40:
            return "INSUFFICIENT_DATA", "fewer than 40 cumulative OOS trades"
        keep = (
            n >= 100
            and result["net_mean"] is not None
            and result["net_mean"] > 0
            and result["net_median"] is not None
            and result["net_median"] > 0
            and result["bootstrap_95_lower_mean"] is not None
            and result["bootstrap_95_lower_mean"] > 0
            and result["max_positive_quarter_concentration"] is not None
            and result["max_positive_quarter_concentration"] <= 0.70
        )
    else:
        raise ValueError("R59 permits exactly H01 and H02")
    if keep:
        return "KEEP_FOR_FORWARD_WATCH", "all frozen cumulative OOS gates passed"
    return "KILL", "one or more frozen cumulative OOS gates failed"


def next_requirement(hypothesis: str, decision: str, n: int) -> str:
    if decision == "KILL":
        return "none; hypothesis is closed and may not be retuned in this cycle"
    classify, keep = (20, 30) if hypothesis == "H01" else (40, 100)
    return (
        f"at least {max(0, classify - n)} additional frozen OOS trades to classify; "
        f"{max(0, keep - n)} additional trades to reach the keep sample floor"
    )


def expand_plan(plan: dict[str, object]) -> list[dict[str, str]]:
    base = str(plan["base_url"]).rstrip("/")
    records: list[dict[str, str]] = []
    for series in plan["series"]:
        for month in plan["months"]:
            name = str(series["filename_template"]).format(ym=month)
            relative = f"{series['folder']}/{name}"
            records.append(
                {
                    "hypothesis": str(series["hypothesis"]),
                    "source_id": f"binance-vision:{relative}",
                    "url": f"{base}/{relative}",
                    "path": relative,
                }
            )
    if len(records) != int(plan["expected_file_count"]):
        raise ValueError("frozen source plan count mismatch")
    return sorted(records, key=lambda item: item["source_id"])


def verify_sources(
    data: Path, plan: dict[str, object], manifest: dict[str, object]
) -> list[dict[str, object]]:
    expected = expand_plan(plan)
    actual = sorted(manifest["files"], key=lambda item: str(item["source_id"]))
    if len(actual) != len(expected):
        raise ValueError("source manifest count mismatch")
    for wanted, observed in zip(expected, actual):
        for field in ("hypothesis", "source_id", "url", "path"):
            if wanted[field] != observed[field]:
                raise ValueError(f"source manifest mismatch: {field}")
        path = data / str(observed["path"])
        if not path.is_file():
            raise ValueError(f"missing frozen source: {path}")
        if path.stat().st_size != int(observed["bytes"]) or digest(path) != observed["sha256"]:
            raise ValueError(f"frozen source hash mismatch: {path}")
    return actual


def files(root: Path, fragment: str) -> list[Path]:
    return sorted(path for path in root.rglob("*.zip") if fragment in path.as_posix())


def prior_record(path: Path, expected_sha: str) -> dict[str, object]:
    sha = digest(path)
    if sha != expected_sha:
        raise ValueError(f"accepted R57 prior hash mismatch: {path}")
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": sha}


def write_hypothesis(
    root: Path,
    name: str,
    prior: list[Trade],
    extension: list[Trade],
    expected_cost: float,
    source_records: list[dict[str, object]],
    prior_evidence: dict[str, object],
) -> dict[str, object]:
    if any(datetime.fromtimestamp(item.entry_ts / 1000, tz=timezone.utc).year != 2026 for item in extension):
        raise ValueError("extension contains a non-2026 trade")
    validate_trades(prior, expected_cost)
    validate_trades(extension, expected_cost)
    cumulative = merge_ledgers(prior, extension, expected_cost)
    short = name.split("_", 1)[0]
    prior_metrics = metrics(prior)
    extension_metrics = metrics(extension)
    cumulative_metrics = metrics(cumulative)
    decision, reason = disposition(short, cumulative_metrics)
    requirement = next_requirement(short, decision, int(cumulative_metrics["n"]))
    result = {
        "hypothesis": name,
        "decision": decision,
        "reason": reason,
        "next_minimum_observation_requirement": requirement,
        "prior_2025": prior_metrics,
        "extension_2026_h1": extension_metrics,
        "cumulative_oos": cumulative_metrics,
        "cost_deducted_once": True,
        "same_snapshot_entry_exit": False,
        "overlap_prohibited": True,
        "parameter_search": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }
    target = root / name
    target.mkdir(parents=True, exist_ok=True)
    write_trade_csv(target / "extension_trades.csv", extension)
    write_trade_csv(target / "cumulative_trades.csv", cumulative)
    (target / "RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (target / "source_manifest.json").write_text(
        json.dumps(
            {
                "schema": "TRADINGOS_R59_HYPOTHESIS_SOURCE_MANIFEST_V1",
                "hypothesis": name,
                "prior_r57": prior_evidence,
                "extension_files": sorted(
                    source_records, key=lambda item: str(item["source_id"])
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    report = [
        f"# {name}: R59 falsification result",
        "",
        f"**Disposition:** `{decision}`",
        "",
        f"Reason: {reason}.",
        f"Next minimum observation requirement: {requirement}.",
        "",
        f"- Prior 2025: `{json.dumps(prior_metrics, sort_keys=True)}`",
        f"- Extension 2026-H1: `{json.dumps(extension_metrics, sort_keys=True)}`",
        f"- Cumulative OOS: `{json.dumps(cumulative_metrics, sort_keys=True)}`",
        "",
        "Costs were deducted once. Entry and exit are distinct bar opens. "
        "No overlapping positions were permitted.",
        "",
        "This result is research-only and cannot authorize execution.",
        "",
    ]
    (target / "FALSIFICATION.md").write_text("\n".join(report), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--prior", required=True, type=Path)
    parser.add_argument("--prior-manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    source_records = verify_sources(args.data, plan, manifest)
    prior_manifest = json.loads(args.prior_manifest.read_text(encoding="utf-8"))
    prior_by_hypothesis = {
        item["hypothesis"]: item for item in prior_manifest["files"]
    }
    if set(prior_by_hypothesis) != {H01, H02}:
        raise ValueError("prior manifest must contain exactly H01 and H02")

    h01_prior_path = args.prior / H01 / "oos_trades.csv"
    h02_prior_path = args.prior / H02 / "oos_trades.csv"
    h01_evidence = prior_record(h01_prior_path, prior_by_hypothesis[H01]["sha256"])
    h02_evidence = prior_record(h02_prior_path, prior_by_hypothesis[H02]["sha256"])

    futures_1h = load_bars(files(args.data, "futures/um/monthly/klines/BTCUSDT/1h"))
    funding = load_funding(files(args.data, "futures/um/monthly/fundingRate/BTCUSDT"))
    btc_15m = load_bars(files(args.data, "spot/monthly/klines/BTCUSDT/15m"))
    eth_15m = load_bars(files(args.data, "spot/monthly/klines/ETHUSDT/15m"))

    args.out.mkdir(parents=True, exist_ok=True)
    outcomes = [
        write_hypothesis(
            args.out,
            H01,
            read_trade_csv(h01_prior_path),
            funding_reversal(funding, futures_1h),
            0.0012,
            [item for item in source_records if item["hypothesis"] == H01],
            h01_evidence,
        ),
        write_hypothesis(
            args.out,
            H02,
            read_trade_csv(h02_prior_path),
            btc_eth_lead_lag(btc_15m, eth_15m),
            0.0024,
            [item for item in source_records if item["hypothesis"] == H02],
            h02_evidence,
        ),
    ]
    if len(outcomes) != 2 or {item["hypothesis"] for item in outcomes} != {H01, H02}:
        raise ValueError("R59 must dispose exactly two frozen hypotheses")
    summary = {
        "schema": "TRADINGOS_R59_BOUNDED_EDGE_CYCLE_03_V1",
        "terminal": "TWO_HYPOTHESES_DISPOSED",
        "hypothesis_count": 2,
        "hypotheses": outcomes,
        "no_parameter_search": True,
        "source_file_count": len(source_records),
        "data_quality": {
            "funding_events": len(funding),
            "futures_btc_1h_bars": len(futures_1h),
            "spot_btc_15m_bars": len(btc_15m),
            "spot_eth_15m_bars": len(eth_15m),
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
