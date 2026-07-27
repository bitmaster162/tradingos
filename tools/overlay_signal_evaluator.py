#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CTI_CONFIG = ROOT / "configs" / "ARBITER_CTI_PANEL_v1.json"
ETHBTC_CONFIG = ROOT / "configs" / "ETHBTC_CORE_HEDGE_DASHBOARD_v1.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def require_signal(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < -1.0 or value > 1.0:
        raise ValueError(f"{name} must be normalized into -1..1, got {value}")
    return value


def classify_cti(config: dict[str, Any], cti: int) -> tuple[str, str]:
    modes = config.get("cti_modes", {})
    alt = modes.get("alt_mode", {})
    defensive = modes.get("btc_defensive_mode", {})
    if cti >= int(alt.get("cti_min", 65)):
        return "alt_mode", str(alt.get("action", "Rotate toward ETH/ALTS in tiers."))
    if cti <= int(defensive.get("cti_max", 35)):
        return "btc_defensive_mode", str(defensive.get("action", "Reduce ALTS, prefer BTC/cash/stables."))
    neutral = modes.get("neutral_mode", {})
    return "neutral_mode", str(neutral.get("action", "Neutral exposure, level-to-level tactics."))


def evaluate_cti(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config = read_json(config_path)
    raw_inputs = {
        "ethbtc_trend": require_signal("ethbtc_trend", float(args.ethbtc_trend)),
        "btcd": require_signal("btcd", float(args.btcd)),
        "oi_mix": require_signal("oi_mix", float(args.oi_mix)),
        "funding_compression": require_signal("funding_compression", float(args.funding_compression)),
        "stablecoin_inflow": require_signal("stablecoin_inflow", float(args.stablecoin_inflow)),
    }

    components = config.get("components", {})
    contributions: dict[str, dict[str, float | bool]] = {}
    raw_score = 0.0
    for name, value in raw_inputs.items():
        comp = components.get(name, {})
        weight = float(comp.get("weight", 0.0))
        invert = bool(comp.get("invert_sign", False))
        normalized = -value if invert else value
        contribution = weight * normalized
        raw_score += contribution
        contributions[name] = {
            "input": value,
            "normalized_for_alt_mode": normalized,
            "weight": weight,
            "contribution": round(contribution, 6),
            "invert_sign": invert,
        }

    cti = int(clamp(round(50 + 50 * raw_score), 0, 100))
    mode, action = classify_cti(config, cti)
    core_blocks = config.get("confirmation", {}).get("core_blocks", [])
    alt_agree = sum(1 for name in core_blocks if contributions.get(name, {}).get("normalized_for_alt_mode", 0.0) > 0)
    btc_agree = sum(1 for name in core_blocks if contributions.get(name, {}).get("normalized_for_alt_mode", 0.0) < 0)
    min_blocks = int(config.get("confirmation", {}).get("minimum_core_blocks_agreeing", 2))
    confirmed_tfs = [tf for tf, ok in {"h4": args.confirm_h4, "d1": args.confirm_d1}.items() if ok]

    return {
        "generated_at": now_iso(),
        "overlay": "arbiter_cti_panel_v1",
        "config": config_path.relative_to(ROOT).as_posix(),
        "inputs": raw_inputs,
        "contributions": contributions,
        "cti_raw": round(raw_score, 6),
        "cti": cti,
        "mode": mode,
        "rotation_action": action,
        "confirmation": {
            "core_blocks": core_blocks,
            "minimum_core_blocks_agreeing": min_blocks,
            "alt_core_blocks_agreeing": alt_agree,
            "btc_core_blocks_agreeing": btc_agree,
            "confirm_h4": bool(args.confirm_h4),
            "confirm_d1": bool(args.confirm_d1),
            "primary_tf_confirmed": bool(args.confirm_h4 and args.confirm_d1),
            "confirmed_tfs": confirmed_tfs,
        },
        "policy": {
            "trade_permission": False,
            "entry_permission": "blocked_overlay_only",
            "risk_multiplier": 0.0,
            "reason": "CTI is a rotation/context overlay, not a direct entry trigger.",
        },
    }


def read_ohlc_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"close", "high", "low"}
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"CSV missing required columns: {missing}")
        for raw in reader:
            rows.append(
                {
                    "time": raw.get("time") or raw.get("date") or "",
                    "close": float(raw["close"]),
                    "high": float(raw["high"]),
                    "low": float(raw["low"]),
                }
            )
    return rows


def demo_ethbtc_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    close = 0.031
    for index in range(220):
        drift = 0.000012 if index < 190 else 0.00011
        close += drift
        high = close * (1.004 if index < 190 else 1.012)
        low = close * (0.996 if index < 190 else 0.988)
        if index == 205:
            high = max(high, close * 1.08)
        if index == 195:
            low = min(low, close * 0.92)
        rows.append({"time": f"demo-{index + 1:03d}", "close": close, "high": high, "low": low})
    return rows


def sma_at(rows: list[dict[str, Any]], index: int, length: int) -> float | None:
    start = index - length + 1
    if start < 0:
        return None
    closes = [float(item["close"]) for item in rows[start : index + 1]]
    return sum(closes) / length


def trailing_above_sma_count(rows: list[dict[str, Any]], length: int) -> int:
    count = 0
    for index in range(len(rows) - 1, -1, -1):
        sma = sma_at(rows, index, length)
        if sma is None:
            break
        if float(rows[index]["close"]) > sma:
            count += 1
            continue
        break
    return count


def evaluate_ethbtc(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config = read_json(config_path)
    rows = demo_ethbtc_rows() if args.demo else read_ohlc_csv(Path(args.csv))

    sma_len = int(config.get("metrics", {}).get("sma200", {}).get("length", 200))
    range_len = int(config.get("metrics", {}).get("high30", {}).get("length", 30))
    if len(rows) < max(sma_len, range_len):
        state = "insufficient_history"
        latest = rows[-1] if rows else {}
        return {
            "generated_at": now_iso(),
            "overlay": "ethbtc_core_hedge_dashboard_v1",
            "config": config_path.relative_to(ROOT).as_posix(),
            "rows": len(rows),
            "state": state,
            "latest": latest,
            "policy": {
                "trade_permission": False,
                "entry_permission": "blocked_overlay_only",
                "risk_multiplier": 0.0,
                "reason": "Need enough daily history before portfolio role classification.",
            },
        }

    latest = rows[-1]
    sma200 = sma_at(rows, len(rows) - 1, sma_len)
    assert sma200 is not None
    window = rows[-range_len:]
    high30 = max(float(item["high"]) for item in window)
    low30 = min(float(item["low"]) for item in window)
    hl30 = high30 / low30 if low30 else float("inf")
    close = float(latest["close"])
    signals = config.get("signals", {})
    hold_required = int(signals.get("holding_above_200dma_closes", 3))
    near_band_pct = float(signals.get("near_200dma_band_pct", 2.0))
    core_hl = float(signals.get("core_impulse_hl30_min", 1.15))
    risk_hl = float(signals.get("risk_off_hl30_max", 1.05))
    fail_below_pct = float(signals.get("failsafe_below_sma_pct", 2.0))
    hold_count = trailing_above_sma_count(rows, sma_len)
    distance_pct = ((close - sma200) / sma200) * 100.0 if sma200 else 0.0

    above_200 = close > sma200
    near_200 = abs(distance_pct) <= near_band_pct
    fail_below = distance_pct <= -fail_below_pct
    if fail_below or hl30 <= risk_hl:
        state = "risk_off"
    elif hold_count >= hold_required or hl30 >= core_hl:
        state = "core"
    elif near_200 and hl30 < core_hl:
        state = "hedge"
    else:
        state = "hedge"

    actions = config.get("actions", {})
    return {
        "generated_at": now_iso(),
        "overlay": "ethbtc_core_hedge_dashboard_v1",
        "config": config_path.relative_to(ROOT).as_posix(),
        "source": "demo" if args.demo else str(Path(args.csv)),
        "rows": len(rows),
        "latest": {
            "time": latest.get("time"),
            "close": round(close, 8),
            "sma200": round(sma200, 8),
            "distance_to_sma200_pct": round(distance_pct, 4),
            "high30": round(high30, 8),
            "low30": round(low30, 8),
            "hl30_ratio": round(hl30, 6),
            "above_200dma": above_200,
            "hold_above_200dma_count": hold_count,
        },
        "state": state,
        "portfolio_action": str(actions.get(state, "No configured action.")),
        "thresholds": {
            "hold_required_closes": hold_required,
            "near_200dma_band_pct": near_band_pct,
            "core_impulse_hl30_min": core_hl,
            "risk_off_hl30_max": risk_hl,
            "failsafe_below_sma_pct": fail_below_pct,
        },
        "policy": {
            "trade_permission": False,
            "entry_permission": "blocked_overlay_only",
            "risk_multiplier": 0.0,
            "reason": "ETHBTC role is a portfolio overlay, not a direct entry trigger.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate safe, non-trading overlay signals from bundled configs.")
    sub = parser.add_subparsers(dest="mode", required=True)

    cti = sub.add_parser("cti", help="Evaluate Arbiter CTI 0..100 from normalized -1..1 inputs.")
    cti.add_argument("--config", default=str(CTI_CONFIG.relative_to(ROOT)))
    cti.add_argument("--ethbtc-trend", type=float, required=True)
    cti.add_argument("--btcd", type=float, required=True, help="BTC.D normalized slope; falling dominance should be negative.")
    cti.add_argument("--oi-mix", type=float, required=True)
    cti.add_argument("--funding-compression", type=float, required=True)
    cti.add_argument("--stablecoin-inflow", type=float, required=True)
    cti.add_argument("--confirm-h4", action="store_true")
    cti.add_argument("--confirm-d1", action="store_true")
    cti.add_argument("--out")

    ethbtc = sub.add_parser("ethbtc", help="Evaluate ETHBTC core/hedge/risk-off from daily OHLC CSV.")
    ethbtc.add_argument("--config", default=str(ETHBTC_CONFIG.relative_to(ROOT)))
    ethbtc.add_argument("--csv", help="Daily CSV with close, high, low and optional time/date columns.")
    ethbtc.add_argument("--demo", action="store_true", help="Use deterministic bundled demo rows.")
    ethbtc.add_argument("--out")

    args = parser.parse_args()
    try:
        if args.mode == "cti":
            payload = evaluate_cti(args)
        elif args.mode == "ethbtc":
            if not args.demo and not args.csv:
                raise ValueError("ethbtc mode requires --csv or --demo")
            payload = evaluate_ethbtc(args)
        else:
            raise ValueError(f"unknown mode: {args.mode}")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    out_path = Path(args.out) if getattr(args, "out", None) else None
    if out_path is not None and not out_path.is_absolute():
        out_path = ROOT / out_path
    write_json(out_path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
