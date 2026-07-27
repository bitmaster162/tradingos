from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SETUP_ID = "liquidity_sweep_eq"
ALERT_ID = "sweep_return"
DEFAULT_CONFIG = Path("configs/BitEvo_composite_config.json")


@dataclass(frozen=True)
class OhlcvBar:
    index: int
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class DetectorParams:
    lookback: int
    eqh_tolerance_pct: float
    eql_tolerance_pct: float
    sweep_displacement_ticks: float
    tick_size: float


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_float(value: Any, field: str, row_number: int) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"row {row_number}: invalid {field}={value!r}") from exc


def load_ohlcv(path: Path) -> list[OhlcvBar]:
    if not path.exists():
        raise FileNotFoundError(path)
    bars: list[OhlcvBar] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"open", "high", "low", "close", "volume"}
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"missing required OHLCV columns: {', '.join(missing)}")
        for index, row in enumerate(reader):
            row_number = index + 2
            ts = str(row.get("time") or row.get("timestamp") or index).strip()
            bars.append(
                OhlcvBar(
                    index=index,
                    ts=ts,
                    open=parse_float(row.get("open"), "open", row_number),
                    high=parse_float(row.get("high"), "high", row_number),
                    low=parse_float(row.get("low"), "low", row_number),
                    close=parse_float(row.get("close"), "close", row_number),
                    volume=parse_float(row.get("volume"), "volume", row_number),
                )
            )
    if not bars:
        raise ValueError(f"no OHLCV rows found in {path}")
    return bars


def load_config_params(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    for setup in payload.get("setups", []):
        if isinstance(setup, dict) and setup.get("setup_id") == SETUP_ID:
            return setup.get("params", {})
    return {}


def build_params(args: argparse.Namespace) -> DetectorParams:
    config_params = load_config_params(Path(args.config)) if args.config else {}
    eq_detection = config_params.get("eq_detection", {}) if isinstance(config_params, dict) else {}
    lookback = args.lookback or int(config_params.get("swing_window", 50) or 50)
    return DetectorParams(
        lookback=lookback,
        eqh_tolerance_pct=float(args.eqh_tolerance_pct if args.eqh_tolerance_pct is not None else eq_detection.get("eqh_tolerance_pct", 0.15)),
        eql_tolerance_pct=float(args.eql_tolerance_pct if args.eql_tolerance_pct is not None else eq_detection.get("eql_tolerance_pct", 0.15)),
        sweep_displacement_ticks=float(
            args.sweep_displacement_ticks
            if args.sweep_displacement_ticks is not None
            else config_params.get("sweep_displacement_ticks", 2)
        ),
        tick_size=float(args.tick_size),
    )


def cluster_count_near(values: list[float], level: float, tolerance_abs: float) -> int:
    return sum(1 for value in values if abs(value - level) <= tolerance_abs)


def detect_events(bars: list[OhlcvBar], params: DetectorParams) -> list[dict[str, Any]]:
    if params.lookback < 2:
        raise ValueError("lookback must be >= 2")
    if params.tick_size <= 0:
        raise ValueError("tick_size must be > 0")
    displacement_min = params.tick_size * params.sweep_displacement_ticks
    events: list[dict[str, Any]] = []
    for offset in range(params.lookback, len(bars)):
        current = bars[offset]
        previous = bars[offset - params.lookback : offset]
        prev_highs = [bar.high for bar in previous]
        prev_lows = [bar.low for bar in previous]

        eqh_level = max(prev_highs)
        eqh_tolerance_abs = abs(eqh_level) * params.eqh_tolerance_pct / 100.0
        eqh_count = cluster_count_near(prev_highs, eqh_level, eqh_tolerance_abs)
        bearish_displacement = current.high - eqh_level
        if (
            eqh_count >= 2
            and bearish_displacement >= displacement_min
            and current.close < eqh_level
        ):
            events.append(
                build_event(
                    current=current,
                    direction="bearish",
                    side_hint="SHORT",
                    level_type="EQH",
                    liquidity_level=eqh_level,
                    sweep_extreme=current.high,
                    displacement=bearish_displacement,
                    cluster_count=eqh_count,
                    tolerance_abs=eqh_tolerance_abs,
                    params=params,
                )
            )

        eql_level = min(prev_lows)
        eql_tolerance_abs = abs(eql_level) * params.eql_tolerance_pct / 100.0
        eql_count = cluster_count_near(prev_lows, eql_level, eql_tolerance_abs)
        bullish_displacement = eql_level - current.low
        if (
            eql_count >= 2
            and bullish_displacement >= displacement_min
            and current.close > eql_level
        ):
            events.append(
                build_event(
                    current=current,
                    direction="bullish",
                    side_hint="LONG",
                    level_type="EQL",
                    liquidity_level=eql_level,
                    sweep_extreme=current.low,
                    displacement=bullish_displacement,
                    cluster_count=eql_count,
                    tolerance_abs=eql_tolerance_abs,
                    params=params,
                )
            )
    return events


def build_event(
    *,
    current: OhlcvBar,
    direction: str,
    side_hint: str,
    level_type: str,
    liquidity_level: float,
    sweep_extreme: float,
    displacement: float,
    cluster_count: int,
    tolerance_abs: float,
    params: DetectorParams,
) -> dict[str, Any]:
    return {
        "event_id": f"{current.ts}:{direction}_{level_type.lower()}_sweep_return",
        "ts": current.ts,
        "bar_index": current.index,
        "setup_id": SETUP_ID,
        "alert_id": ALERT_ID,
        "direction": direction,
        "side_hint": side_hint,
        "level_type": level_type,
        "liquidity_level": round(liquidity_level, 8),
        "sweep_extreme": round(sweep_extreme, 8),
        "close": round(current.close, 8),
        "displacement": round(displacement, 8),
        "cluster_count": cluster_count,
        "tolerance_abs": round(tolerance_abs, 8),
        "lookback": params.lookback,
        "volume": current.volume,
        "confirmations": {
            "return_inside_same_bar": True,
            "lookahead_used": False,
        },
        "runtime_policy": {
            "can_trade": False,
            "trade_permission": False,
            "risk_multiplier": 0.0,
            "reason": "detector_smoke_only_requires_derivatives_delta_fvg_and_forward_evidence",
        },
        "missing_live_confirmations": [
            "fvg_validation",
            "delta_imbalance",
            "oi_spike",
            "funding_context",
            "liquidation_cluster",
            "forward_outcome_sample",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    params = report["params"]
    lines = [
        "# Liquidity Sweep EQ Detector Smoke",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Source CSV: `{report['source_csv']}`",
        f"Rows: `{report['rows']}`",
        f"Events detected: `{report['events_detected']}`",
        "",
        "## Runtime Boundary",
        "",
        "- This is an alert-only detector smoke test.",
        "- It does not grant entry permission, does not size positions and does not send orders.",
        "- A detected sweep must still pass FVG, delta, OI/funding, liquidation-cluster and forward-evidence checks before any trading use.",
        "",
        "## Parameters",
        "",
        f"- `lookback`: `{params['lookback']}`",
        f"- `eqh_tolerance_pct`: `{params['eqh_tolerance_pct']}`",
        f"- `eql_tolerance_pct`: `{params['eql_tolerance_pct']}`",
        f"- `sweep_displacement_ticks`: `{params['sweep_displacement_ticks']}`",
        f"- `tick_size`: `{params['tick_size']}`",
        "",
        "## Events",
        "",
    ]
    if not report["events"]:
        lines.append("- No events detected.")
    for event in report["events"]:
        lines.append(
            "- "
            f"`{event['ts']}` {event['direction']} {event['level_type']} sweep, "
            f"side_hint=`{event['side_hint']}`, level=`{event['liquidity_level']}`, "
            f"extreme=`{event['sweep_extreme']}`, close=`{event['close']}`, "
            f"can_trade=`{event['runtime_policy']['can_trade']}`"
        )
    lines.extend(
        [
            "",
            "## Next Integration Step",
            "",
            "- Keep this detector standalone until it is wired into MAX Core Lite as `can_trade=false` context.",
            "- After enough forward observations, test whether this event improves or degrades existing MAX candidates.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.csv)
    params = build_params(args)
    bars = load_ohlcv(source)
    events = detect_events(bars, params)
    direction_counts: dict[str, int] = {}
    for event in events:
        direction_counts[event["direction"]] = direction_counts.get(event["direction"], 0) + 1
    return {
        "generated_at": now_iso(),
        "source_csv": str(source),
        "rows": len(bars),
        "setup_id": SETUP_ID,
        "alert_id": ALERT_ID,
        "params": {
            "lookback": params.lookback,
            "eqh_tolerance_pct": params.eqh_tolerance_pct,
            "eql_tolerance_pct": params.eql_tolerance_pct,
            "sweep_displacement_ticks": params.sweep_displacement_ticks,
            "tick_size": params.tick_size,
        },
        "events_detected": len(events),
        "direction_counts": direction_counts,
        "runtime_boundary": {
            "classification": "alert_only_detector_smoke",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "events": events,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect equal-high/equal-low liquidity sweeps in OHLCV CSV")
    parser.add_argument("--csv", required=True, help="OHLCV CSV path with open/high/low/close/volume columns")
    parser.add_argument("--out-prefix", default="docs/LIQUIDITY_SWEEP_DETECTOR_SMOKE_2026-06-03")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--lookback", type=int, default=None)
    parser.add_argument("--eqh-tolerance-pct", type=float, default=None)
    parser.add_argument("--eql-tolerance-pct", type=float, default=None)
    parser.add_argument("--sweep-displacement-ticks", type=float, default=None)
    parser.add_argument("--tick-size", type=float, default=0.01)
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(
        {
            "events_detected": report["events_detected"],
            "direction_counts": report["direction_counts"],
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
