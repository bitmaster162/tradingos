from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_detector import (  # noqa: E402
    DEFAULT_CONFIG,
    DetectorParams,
    detect_events,
    load_config_params,
    load_ohlcv,
)
from tools.liquidity_sweep_forward_eval import (  # noqa: E402
    compute_atr,
    evaluate_event,
    infer_dataset_id,
    summarize_outcomes,
)


def build_datasets(cache_dir: str | Path = "data/cache/binance") -> list[dict[str, str]]:
    base = Path(cache_dir)
    return [
        {
            "id": "futures_BTCUSDT_15m",
            "klines": str(base / "futures" / "BTCUSDT" / "15m_klines.csv"),
            "derivatives": str(base / "futures" / "BTCUSDT" / "15m_oi_aligned.csv"),
            "htf": str(base / "futures" / "BTCUSDT" / "1h_klines.csv"),
        },
        {
            "id": "futures_BTCUSDT_1h",
            "klines": str(base / "futures" / "BTCUSDT" / "1h_klines.csv"),
            "derivatives": str(base / "futures" / "BTCUSDT" / "1h_oi_aligned.csv"),
            "htf": str(base / "futures" / "BTCUSDT" / "4h_klines.csv"),
        },
        {
            "id": "futures_BTCUSDT_4h",
            "klines": str(base / "futures" / "BTCUSDT" / "4h_klines.csv"),
            "derivatives": str(base / "futures" / "BTCUSDT" / "4h_oi_aligned.csv"),
            "htf": str(base / "futures" / "BTCUSDT" / "4h_klines.csv"),
        },
    ]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_dt(value: str) -> datetime:
    clean = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(clean)


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    if math.isnan(result):
        return None
    return result


def load_derivatives(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if not path.exists():
        return {}, {"exists": False, "rows": 0, "oi_coverage_pct": 0.0, "funding_coverage_pct": 0.0}
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(row)
    by_time = {str(row.get("time", "")).strip(): row for row in rows}
    total = len(rows)
    oi_nonblank = sum(1 for row in rows if safe_float(row.get("open_interest")) is not None)
    funding_nonblank = sum(1 for row in rows if safe_float(row.get("funding")) is not None)
    return by_time, {
        "exists": True,
        "rows": total,
        "oi_coverage_pct": round(oi_nonblank / total * 100.0, 3) if total else 0.0,
        "funding_coverage_pct": round(funding_nonblank / total * 100.0, 3) if total else 0.0,
    }


def ema(values: list[float], length: int) -> list[float | None]:
    if not values:
        return []
    alpha = 2.0 / (length + 1.0)
    result: list[float | None] = []
    current: float | None = None
    for index, value in enumerate(values):
        if index + 1 < length:
            result.append(None)
            continue
        if current is None:
            current = sum(values[index + 1 - length : index + 1]) / length
        else:
            current = value * alpha + current * (1.0 - alpha)
        result.append(current)
    return result


def build_htf_lookup(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "records": []}
    bars = load_ohlcv(path)
    closes = [bar.close for bar in bars]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    records: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        bias = "UNKNOWN"
        regime = "insufficient_htf_history"
        if ema20[index] is not None and ema50[index] is not None and ema200[index] is not None and index >= 5:
            slope20 = (ema20[index] or 0.0) - (ema20[index - 5] or ema20[index] or 0.0)
            if bar.close > (ema200[index] or 0.0) and (ema20[index] or 0.0) > (ema50[index] or 0.0) and slope20 > 0:
                bias = "LONG"
                regime = "htf_trend_up"
            elif bar.close < (ema200[index] or 0.0) and (ema20[index] or 0.0) < (ema50[index] or 0.0) and slope20 < 0:
                bias = "SHORT"
                regime = "htf_trend_down"
            else:
                bias = "NEUTRAL"
                regime = "htf_mixed_or_range"
        records.append(
            {
                "ts": bar.ts,
                "dt": parse_dt(bar.ts),
                "bias": bias,
                "regime": regime,
                "close": bar.close,
            }
        )
    return {"exists": True, "path": str(path), "records": records}


def htf_at_or_before(htf_lookup: dict[str, Any], ts: str) -> dict[str, Any]:
    records = htf_lookup.get("records") or []
    if not records:
        return {"bias": "UNKNOWN", "regime": "missing_htf"}
    target = parse_dt(ts)
    selected: dict[str, Any] | None = None
    for record in records:
        if record["dt"] <= target:
            selected = record
        else:
            break
    if selected is None:
        return {"bias": "UNKNOWN", "regime": "before_htf_history"}
    return {
        "bias": selected["bias"],
        "regime": selected["regime"],
        "htf_ts": selected["ts"],
        "htf_close": selected["close"],
    }


def build_params(args: argparse.Namespace) -> DetectorParams:
    config_params = load_config_params(Path(args.config)) if args.config else {}
    eq_detection = config_params.get("eq_detection", {}) if isinstance(config_params, dict) else {}
    return DetectorParams(
        lookback=int(args.lookback or config_params.get("swing_window", 50) or 50),
        eqh_tolerance_pct=float(args.eqh_tolerance_pct if args.eqh_tolerance_pct is not None else eq_detection.get("eqh_tolerance_pct", 0.15)),
        eql_tolerance_pct=float(args.eql_tolerance_pct if args.eql_tolerance_pct is not None else eq_detection.get("eql_tolerance_pct", 0.15)),
        sweep_displacement_ticks=float(
            args.sweep_displacement_ticks
            if args.sweep_displacement_ticks is not None
            else config_params.get("sweep_displacement_ticks", 2)
        ),
        tick_size=float(args.tick_size),
    )


def median_abs_delta(rows: list[dict[str, Any]], lag: int) -> float:
    values: list[float] = []
    oi_series = [safe_float(row.get("open_interest")) for row in rows]
    for index in range(lag, len(oi_series)):
        current = oi_series[index]
        previous = oi_series[index - lag]
        if current is None or previous is None or previous == 0:
            continue
        values.append(abs((current - previous) / previous * 100.0))
    if not values:
        return 0.0
    return float(statistics.median(values))


def derivative_context(
    event: dict[str, Any],
    derivative_rows: list[dict[str, Any]],
    derivative_by_time: dict[str, dict[str, Any]],
    *,
    oi_lag: int,
    funding_hot_abs: float,
    funding_neutral_abs: float,
    oi_spike_abs_pct: float,
) -> dict[str, Any]:
    row = derivative_by_time.get(str(event["ts"]))
    if row is None:
        return {
            "oi_present": False,
            "funding_present": False,
            "oi_delta_pct": None,
            "funding": None,
            "funding_state": "missing",
            "funding_reversal_aligned": False,
            "oi_expansion": False,
            "oi_spike_abs": False,
            "missing": ["derivatives_row"],
        }
    time_to_index = {str(item.get("time", "")).strip(): index for index, item in enumerate(derivative_rows)}
    index = time_to_index.get(str(event["ts"]), -1)
    current_oi = safe_float(row.get("open_interest"))
    previous_oi = None
    if index >= oi_lag:
        previous_oi = safe_float(derivative_rows[index - oi_lag].get("open_interest"))
    oi_delta_pct = None
    if current_oi is not None and previous_oi is not None and previous_oi != 0:
        oi_delta_pct = (current_oi - previous_oi) / previous_oi * 100.0
    funding = safe_float(row.get("funding"))
    if funding is None:
        funding_state = "missing"
    elif funding >= funding_hot_abs:
        funding_state = "positive_hot"
    elif funding <= -funding_hot_abs:
        funding_state = "negative_hot"
    elif abs(funding) <= funding_neutral_abs:
        funding_state = "neutral"
    elif funding > 0:
        funding_state = "positive_mild"
    else:
        funding_state = "negative_mild"

    side = str(event["side_hint"]).upper()
    funding_reversal_aligned = bool(
        (side == "SHORT" and funding is not None and funding >= funding_hot_abs)
        or (side == "LONG" and funding is not None and funding <= -funding_hot_abs)
    )
    missing: list[str] = []
    if oi_delta_pct is None:
        missing.append("oi_delta")
    if funding is None:
        missing.append("funding")
    return {
        "oi_present": oi_delta_pct is not None,
        "funding_present": funding is not None,
        "oi_delta_pct": None if oi_delta_pct is None else round(oi_delta_pct, 6),
        "funding": None if funding is None else round(funding, 8),
        "funding_state": funding_state,
        "funding_reversal_aligned": funding_reversal_aligned,
        "oi_expansion": bool(oi_delta_pct is not None and oi_delta_pct > 0),
        "oi_contraction": bool(oi_delta_pct is not None and oi_delta_pct < 0),
        "oi_spike_abs": bool(oi_delta_pct is not None and abs(oi_delta_pct) >= oi_spike_abs_pct),
        "missing": missing,
    }


def add_confluence(
    outcome: dict[str, Any],
    event: dict[str, Any],
    derivative: dict[str, Any],
    htf: dict[str, Any],
) -> dict[str, Any]:
    side = str(outcome["side_hint"]).upper()
    htf_bias = str(htf.get("bias", "UNKNOWN"))
    opposite = "SHORT" if side == "LONG" else "LONG"
    enriched = dict(outcome)
    enriched["confluence"] = {
        "oi_present": derivative["oi_present"],
        "oi_delta_pct": derivative["oi_delta_pct"],
        "oi_expansion": derivative["oi_expansion"],
        "oi_contraction": derivative.get("oi_contraction", False),
        "oi_spike_abs": derivative["oi_spike_abs"],
        "funding": derivative["funding"],
        "funding_state": derivative["funding_state"],
        "funding_reversal_aligned": derivative["funding_reversal_aligned"],
        "htf_bias": htf_bias,
        "htf_regime": htf.get("regime", "UNKNOWN"),
        "htf_aligned": htf_bias == side,
        "htf_not_against": htf_bias not in {opposite, "UNKNOWN"},
        "missing": list(sorted(set(derivative.get("missing", [])))),
    }
    enriched["event"] = {
        "level_type": event["level_type"],
        "liquidity_level": event["liquidity_level"],
        "sweep_extreme": event["sweep_extreme"],
        "cluster_count": event["cluster_count"],
    }
    return enriched


def classify_bucket(summary: dict[str, Any], min_events: int) -> str:
    events = int(summary["eligible_events"])
    positive = summary["positive_1atr_touch_pct"]
    avg_close = summary["avg_close_return_pct_directional"]
    if events == 0:
        return "empty"
    if events < min_events:
        if positive is not None and positive >= 60 and avg_close is not None and avg_close > 0:
            return "watchlist_positive_tiny_sample"
        return "insufficient_sample"
    if positive is not None and positive >= 55 and avg_close is not None and avg_close > 0:
        return "candidate_for_hardening"
    if positive is not None and (positive <= 45 or (avg_close is not None and avg_close < 0)):
        return "negative_or_mixed"
    return "neutral_inconclusive"


def summarize_bucket(name: str, outcomes: list[dict[str, Any]], min_events: int) -> dict[str, Any]:
    summary = summarize_outcomes(outcomes)
    summary["bucket"] = name
    summary["classification"] = classify_bucket(summary, min_events)
    return summary


def build_buckets(outcomes: list[dict[str, Any]], min_events: int) -> list[dict[str, Any]]:
    predicates: list[tuple[str, Callable[[dict[str, Any]], bool]]] = [
        ("all", lambda item: True),
        ("side_LONG", lambda item: item["side_hint"] == "LONG"),
        ("side_SHORT", lambda item: item["side_hint"] == "SHORT"),
        ("oi_present", lambda item: item["confluence"]["oi_present"]),
        ("oi_expansion", lambda item: item["confluence"]["oi_expansion"]),
        ("oi_contraction", lambda item: item["confluence"]["oi_contraction"]),
        ("oi_spike_abs", lambda item: item["confluence"]["oi_spike_abs"]),
        ("funding_reversal_aligned", lambda item: item["confluence"]["funding_reversal_aligned"]),
        ("funding_neutral", lambda item: item["confluence"]["funding_state"] == "neutral"),
        ("htf_aligned", lambda item: item["confluence"]["htf_aligned"]),
        ("htf_not_against", lambda item: item["confluence"]["htf_not_against"]),
        ("oi_expansion_and_htf_not_against", lambda item: item["confluence"]["oi_expansion"] and item["confluence"]["htf_not_against"]),
        ("funding_reversal_and_htf_not_against", lambda item: item["confluence"]["funding_reversal_aligned"] and item["confluence"]["htf_not_against"]),
        ("oi_expansion_and_funding_reversal", lambda item: item["confluence"]["oi_expansion"] and item["confluence"]["funding_reversal_aligned"]),
        (
            "oi_expansion_funding_reversal_htf_not_against",
            lambda item: item["confluence"]["oi_expansion"]
            and item["confluence"]["funding_reversal_aligned"]
            and item["confluence"]["htf_not_against"],
        ),
    ]
    buckets = []
    for name, predicate in predicates:
        selected = [item for item in outcomes if predicate(item)]
        buckets.append(summarize_bucket(name, selected, min_events))
    return buckets


def evaluate_dataset(dataset: dict[str, str], args: argparse.Namespace, params: DetectorParams) -> dict[str, Any]:
    kline_path = Path(dataset["klines"])
    bars = load_ohlcv(kline_path)
    events = detect_events(bars, params)
    atr_values = compute_atr(bars, args.atr_window)

    derivative_rows: list[dict[str, Any]] = []
    derivative_path = Path(dataset["derivatives"])
    if derivative_path.exists():
        with derivative_path.open("r", encoding="utf-8-sig", newline="") as handle:
            derivative_rows = list(csv.DictReader(handle))
    derivative_by_time, derivative_coverage = load_derivatives(derivative_path)
    oi_spike_abs_pct = args.oi_spike_abs_pct
    if oi_spike_abs_pct is None:
        oi_spike_abs_pct = max(0.05, median_abs_delta(derivative_rows, args.oi_lag))

    htf_lookup = build_htf_lookup(Path(dataset["htf"]))
    outcomes: list[dict[str, Any]] = []
    skipped = 0
    for event in events:
        outcome = evaluate_event(event, bars, atr_values, args.forward_bars)
        if outcome is None:
            skipped += 1
            continue
        derivative = derivative_context(
            event,
            derivative_rows,
            derivative_by_time,
            oi_lag=args.oi_lag,
            funding_hot_abs=args.funding_hot_abs,
            funding_neutral_abs=args.funding_neutral_abs,
            oi_spike_abs_pct=oi_spike_abs_pct,
        )
        htf = htf_at_or_before(htf_lookup, str(event["ts"]))
        outcomes.append(add_confluence(outcome, event, derivative, htf))

    buckets = build_buckets(outcomes, args.min_events)
    ranked = sorted(
        buckets,
        key=lambda item: (
            1 if item["classification"] == "candidate_for_hardening" else 0,
            item["eligible_events"],
            item["positive_1atr_touch_pct"] or 0,
            item["avg_close_return_pct_directional"] or -999,
        ),
        reverse=True,
    )
    return {
        "dataset_id": dataset.get("id") or infer_dataset_id(kline_path),
        "paths": dataset,
        "rows": len(bars),
        "events_detected": len(events),
        "events_skipped_no_forward_or_atr": skipped,
        "derivative_coverage": derivative_coverage,
        "oi_spike_abs_pct_used": round(float(oi_spike_abs_pct), 6),
        "buckets": buckets,
        "top_buckets": ranked[:8],
        "sample_outcomes": outcomes[:15],
        "_all_outcomes": outcomes,
    }


def render_markdown(report: dict[str, Any]) -> str:
    candidate_buckets = [
        bucket
        for bucket in report["aggregate"]["top_buckets"]
        if bucket["classification"] == "candidate_for_hardening"
    ]
    lines = [
        "# Liquidity Sweep Confluence Evaluation",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Forward bars: `{report['forward_bars']}`",
        f"Min events for hardening bucket: `{report['min_events']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Research/evidence-only evaluation.",
        "- Uses local BTCUSDT cache only.",
        "- Does not grant trade permission, does not size positions and does not send orders.",
        "- Configs/specs are not treated as live features unless a real consumer passes smoke proof.",
        "",
        "## Findings First",
        "",
    ]
    aggregate = report["aggregate"]
    lines.extend(
        [
            f"- Aggregate classification: `{aggregate['summary']['classification']}`.",
            f"- Aggregate eligible events: `{aggregate['summary']['eligible_events']}`.",
            f"- Aggregate positive +1ATR first-touch: `{aggregate['summary']['positive_1atr_touch_pct']}`%.",
            f"- Aggregate avg directional close return: `{aggregate['summary']['avg_close_return_pct_directional']}`%.",
            "- Keep `liquidity_sweep_eq` blocked from trading unless a confluence bucket later passes hardening and out-of-sample checks.",
            "",
            "Candidate buckets for hardening:",
            "",
        ]
    )
    if candidate_buckets:
        for bucket in candidate_buckets:
            lines.append(
                f"- `{bucket['bucket']}`: events=`{bucket['eligible_events']}`, "
                f"+1ATR=`{bucket['positive_1atr_touch_pct']}`%, "
                f"avg_close=`{bucket['avg_close_return_pct_directional']}`%."
            )
    else:
        lines.append("- No aggregate bucket reached `candidate_for_hardening`.")
    lines.extend(
        [
            "",
            "## Dataset Results",
            "",
        ]
    )
    for dataset in report["datasets"]:
        lines.extend(
            [
                f"### {dataset['dataset_id']}",
                "",
                f"- Rows: `{dataset['rows']}`",
                f"- Events detected: `{dataset['events_detected']}`",
                f"- OI coverage: `{dataset['derivative_coverage']['oi_coverage_pct']}`%",
                f"- Funding coverage: `{dataset['derivative_coverage']['funding_coverage_pct']}`%",
                f"- OI spike abs threshold used: `{dataset['oi_spike_abs_pct_used']}`%",
                "",
                "| Bucket | Events | +1ATR % | Avg close % | Class |",
                "|---|---:|---:|---:|---|",
            ]
        )
        for bucket in dataset["top_buckets"]:
            lines.append(
                f"| `{bucket['bucket']}` | `{bucket['eligible_events']}` | "
                f"`{bucket['positive_1atr_touch_pct']}` | "
                f"`{bucket['avg_close_return_pct_directional']}` | "
                f"`{bucket['classification']}` |"
            )
        lines.append("")

    lines.extend(
        [
            "## Aggregate Buckets",
            "",
            "| Bucket | Events | +1ATR % | Avg close % | Class |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for bucket in aggregate["top_buckets"]:
        lines.append(
            f"| `{bucket['bucket']}` | `{bucket['eligible_events']}` | "
            f"`{bucket['positive_1atr_touch_pct']}` | "
            f"`{bucket['avg_close_return_pct_directional']}` | "
            f"`{bucket['classification']}` |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "- Do not promote the detector to live trading from this report.",
            "- Hardening is justified only for the candidate buckets listed above, especially short-side and funding-aligned sweep reversals.",
            "- Long-side sweep reversal remains weak on this sample and should stay blocked.",
            "",
        ]
    )
    return "\n".join(lines)


def strip_private(dataset: dict[str, Any]) -> dict[str, Any]:
    clean = dict(dataset)
    clean.pop("_all_outcomes", None)
    return clean


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate liquidity_sweep_eq confluence with OI/funding/HTF filters")
    parser.add_argument("--out-prefix", default="docs/LIQUIDITY_SWEEP_CONFLUENCE_EVAL_2026-06-03")
    parser.add_argument("--cache-dir", default="data/cache/binance")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--lookback", type=int, default=None)
    parser.add_argument("--eqh-tolerance-pct", type=float, default=None)
    parser.add_argument("--eql-tolerance-pct", type=float, default=None)
    parser.add_argument("--sweep-displacement-ticks", type=float, default=None)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--forward-bars", type=int, default=12)
    parser.add_argument("--atr-window", type=int, default=14)
    parser.add_argument("--oi-lag", type=int, default=4)
    parser.add_argument("--oi-spike-abs-pct", type=float, default=None)
    parser.add_argument("--funding-hot-abs", type=float, default=0.00005)
    parser.add_argument("--funding-neutral-abs", type=float, default=0.00002)
    parser.add_argument("--min-events", type=int, default=15)
    args = parser.parse_args()

    params = build_params(args)
    datasets = [evaluate_dataset(dataset, args, params) for dataset in build_datasets(args.cache_dir)]
    aggregate_outcomes = [outcome for dataset in datasets for outcome in dataset["_all_outcomes"]]
    aggregate_buckets = build_buckets(aggregate_outcomes, args.min_events)
    aggregate_top = sorted(
        aggregate_buckets,
        key=lambda item: (
            1 if item["classification"] == "candidate_for_hardening" else 0,
            item["eligible_events"],
            item["positive_1atr_touch_pct"] or 0,
            item["avg_close_return_pct_directional"] or -999,
        ),
        reverse=True,
    )[:10]
    aggregate_summary = summarize_bucket("all", aggregate_outcomes, args.min_events)
    report = {
        "generated_at": now_iso(),
        "forward_bars": args.forward_bars,
        "atr_window": args.atr_window,
        "oi_lag": args.oi_lag,
        "funding_hot_abs": args.funding_hot_abs,
        "funding_neutral_abs": args.funding_neutral_abs,
        "min_events": args.min_events,
        "params": {
            "lookback": params.lookback,
            "eqh_tolerance_pct": params.eqh_tolerance_pct,
            "eql_tolerance_pct": params.eql_tolerance_pct,
            "sweep_displacement_ticks": params.sweep_displacement_ticks,
            "tick_size": params.tick_size,
        },
        "runtime_boundary": {
            "classification": "research_evidence_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "datasets": [strip_private(dataset) for dataset in datasets],
        "aggregate": {
            "summary": aggregate_summary,
            "top_buckets": aggregate_top,
        },
    }
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(
        {
            "aggregate": aggregate_summary,
            "top_buckets": aggregate_top[:5],
            "json": str(out_prefix.with_suffix(".json")),
            "md": str(out_prefix.with_suffix(".md")),
            "can_trade": False,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
