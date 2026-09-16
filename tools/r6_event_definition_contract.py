#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
R88_PATH = ROOT / "tools" / "r6_candidate_compiler.py"
SPEC = importlib.util.spec_from_file_location("r88_compiler", R88_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load R88 compiler")
r88 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r88
SPEC.loader.exec_module(r88)

INPUT_SCHEMA = "tradingos.r89_event_definition_input.v1"
OUTPUT_SCHEMA = "tradingos.r89_event_definition_result.v1"
CONTRACT_ID = "R89_MACHINE_EVENT_DEFINITION_V1_20260916"
INTERVAL_MS = 900_000
MIN_BARS = 80
PIVOT_SIDE = 2
PIVOT_LOOKBACK = 20
MAX_SEQUENCE_BARS = 12
MAX_DISPLACEMENT_DELAY = 3
MAX_SHIFT_DELAY = 4
MAX_BOS_DELAY = 4
MAX_DECISION_LAG_MS = 20 * 60 * 1000
DISPLACEMENT_TR_ATR = Decimal("1.20")
DISPLACEMENT_BODY_FRAC = Decimal("0.60")
DISPLACEMENT_CLOSE_LOC = Decimal("0.80")
FVG_MIN_ATR = Decimal("0.05")
REQUIRED_TFS = r88.r85.REQUIRED_TFS

CONTRACT = {
    "contract_id": CONTRACT_ID,
    "timeframe": "15m",
    "pivot_side_bars": PIVOT_SIDE,
    "pivot_lookback_bars": PIVOT_LOOKBACK,
    "max_sequence_bars": MAX_SEQUENCE_BARS,
    "max_displacement_delay_bars": MAX_DISPLACEMENT_DELAY,
    "max_shift_delay_bars": MAX_SHIFT_DELAY,
    "max_bos_delay_bars": MAX_BOS_DELAY,
    "max_decision_lag_ms": MAX_DECISION_LAG_MS,
    "displacement_true_range_atr14_min": str(DISPLACEMENT_TR_ATR),
    "displacement_body_fraction_min": str(DISPLACEMENT_BODY_FRAC),
    "displacement_close_location_min": str(DISPLACEMENT_CLOSE_LOC),
    "bullish_fvg_gap_atr14_min": str(FVG_MIN_ATR),
    "monthly_history_min_bars": 36,
    "ema99_required_from_1w_down": True,
    "invalidation": "SWEEP_BAR_LOW",
    "target_pool": "R84_PRIOR_HIGH_15M_1H_4H_1D_1W_1M_NEAREST_OVERHEAD",
    "authority": {"can_trade": False, "capital_permission": "DENY"},
}
def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def dec(value: Any, name: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{name}") from exc
    if not out.is_finite():
        raise ValueError(f"invalid_{name}")
    return out


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {
        "schema": OUTPUT_SCHEMA,
        "result": "NO_CANDIDATE_FAIL_CLOSED",
        "reason": reason,
        "details": details,
        "contract_id": CONTRACT_ID,
        "candidate_compiled": False,
        "ledger_write_authority": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }


def _bar(row: list[Any]) -> dict[str, Any]:
    if not isinstance(row, list) or len(row) < 7:
        raise ValueError("bar_shape_invalid")
    return {"open_time_ms": int(row[0]), "open": dec(row[1], "open"),
            "high": dec(row[2], "high"), "low": dec(row[3], "low"),
            "close": dec(row[4], "close"), "volume": dec(row[5], "volume"),
            "close_time_ms": int(row[6])}
def _bars(raw: Any, decision_ms: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) < MIN_BARS:
        raise ValueError("m15_history_insufficient")
    bars = [_bar(row) for row in raw]
    for i, bar in enumerate(bars):
        if bar["high"] < max(bar["open"], bar["close"], bar["low"]):
            raise ValueError(f"bar_{i}_ohlc_invalid")
        if bar["low"] > min(bar["open"], bar["close"], bar["high"]):
            raise ValueError(f"bar_{i}_ohlc_invalid")
        if bar["close_time_ms"] >= decision_ms:
            raise ValueError(f"bar_{i}_not_closed_before_decision")
        if bar["close_time_ms"] < bar["open_time_ms"]:
            raise ValueError(f"bar_{i}_time_invalid")
        if i:
            if bar["open_time_ms"] - bars[i - 1]["open_time_ms"] != INTERVAL_MS:
                raise ValueError(f"bar_{i}_sequence_gap")
            if bar["open_time_ms"] <= bars[i - 1]["open_time_ms"]:
                raise ValueError(f"bar_{i}_chronology_invalid")
    return bars


def _true_range(bars: list[dict[str, Any]], i: int) -> Decimal:
    b = bars[i]
    if i == 0:
        return b["high"] - b["low"]
    prev = bars[i - 1]["close"]
    return max(b["high"] - b["low"], abs(b["high"] - prev), abs(b["low"] - prev))
def _atr14_before(bars: list[dict[str, Any]], i: int) -> Decimal:
    if i < 15:
        raise ValueError("atr14_history_insufficient")
    trs = [_true_range(bars, j) for j in range(1, i)]
    if len(trs) < 14:
        raise ValueError("atr14_history_insufficient")
    atr = sum(trs[:14], Decimal("0")) / Decimal("14")
    for tr in trs[14:]:
        atr = (atr * Decimal("13") + tr) / Decimal("14")
    return atr


def _pivot_lows(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(PIVOT_SIDE, len(bars) - PIVOT_SIDE):
        center = bars[i]["low"]
        left = [bars[j]["low"] for j in range(i - PIVOT_SIDE, i)]
        right = [bars[j]["low"] for j in range(i + 1, i + PIVOT_SIDE + 1)]
        if all(center < x for x in left + right):
            out.append({"index": i, "price": center,
                        "known_at_ms": bars[i + PIVOT_SIDE]["close_time_ms"]})
    return out


def _pivot_highs(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(PIVOT_SIDE, len(bars) - PIVOT_SIDE):
        center = bars[i]["high"]
        left = [bars[j]["high"] for j in range(i - PIVOT_SIDE, i)]
        right = [bars[j]["high"] for j in range(i + 1, i + PIVOT_SIDE + 1)]
        if all(center > x for x in left + right):
            out.append({"index": i, "price": center,
                        "known_at_ms": bars[i + PIVOT_SIDE]["close_time_ms"]})
    return out
def _latest_reference(pivots: list[dict[str, Any]], sweep_i: int,
                      sweep_open_ms: int) -> dict[str, Any] | None:
    eligible = [p for p in pivots if p["known_at_ms"] < sweep_open_ms
                and sweep_i - PIVOT_LOOKBACK <= p["index"] < sweep_i]
    return eligible[-1] if eligible else None


def _is_displacement(bars: list[dict[str, Any]], i: int) -> bool:
    b = bars[i]
    tr = _true_range(bars, i)
    if tr <= 0 or b["close"] <= b["open"]:
        return False
    atr = _atr14_before(bars, i)
    body = b["close"] - b["open"]
    close_loc = (b["close"] - b["low"]) / (b["high"] - b["low"])
    return (tr >= DISPLACEMENT_TR_ATR * atr
            and body / tr >= DISPLACEMENT_BODY_FRAC
            and close_loc >= DISPLACEMENT_CLOSE_LOC
            and b["close"] > bars[i - 1]["high"])


def _fvg_after_displacement(bars: list[dict[str, Any]], d: int) -> dict[str, Any] | None:
    if d <= 0 or d + 1 >= len(bars):
        return None
    left_high = bars[d - 1]["high"]
    right_low = bars[d + 1]["low"]
    gap = right_low - left_high
    atr = _atr14_before(bars, d)
    if gap <= 0 or gap < FVG_MIN_ATR * atr:
        return None
    return {"lower": left_high, "upper": right_low, "gap": gap,
            "known_at_ms": bars[d + 1]["close_time_ms"], "bar_index": d + 1}
def _find_sequence(bars: list[dict[str, Any]], decision_ms: int) -> dict[str, Any] | None:
    lows = _pivot_lows(bars)
    highs = _pivot_highs(bars)
    start = max(20, len(bars) - MAX_SEQUENCE_BARS - 8)
    for s in range(len(bars) - 1, start - 1, -1):
        sweep = bars[s]
        ref_low = _latest_reference(lows, s, sweep["open_time_ms"])
        ref_high = _latest_reference(highs, s, sweep["open_time_ms"])
        if ref_low is None or ref_high is None:
            continue
        if not (sweep["low"] < ref_low["price"] and sweep["close"] > ref_low["price"]):
            continue
        if ref_high["price"] <= sweep["close"]:
            continue
        d = next((i for i in range(s + 1, min(len(bars), s + 1 + MAX_DISPLACEMENT_DELAY))
                  if _is_displacement(bars, i)), None)
        if d is None:
            continue
        fvg = _fvg_after_displacement(bars, d)
        if fvg is None:
            continue
        m = next((i for i in range(d + 1, min(len(bars), d + 1 + MAX_SHIFT_DELAY))
                  if bars[i]["close"] > ref_high["price"]), None)
        if m is None:
            continue
        impulse_high = max(bars[i]["high"] for i in range(d, m + 1))
        b = next((i for i in range(m + 1, min(len(bars), m + 1 + MAX_BOS_DELAY))
                  if bars[i]["close"] > impulse_high), None)
        if b is None or b - s > MAX_SEQUENCE_BARS:
            continue
        if decision_ms - bars[b]["close_time_ms"] > MAX_DECISION_LAG_MS:
            continue
        return {"sweep_i": s, "displacement_i": d, "shift_i": m, "bos_i": b,
                "ref_low": ref_low, "ref_high": ref_high, "fvg": fvg,
                "impulse_high": impulse_high}
    return None
def _ctha_assessment(evidence: dict[str, Any], decision_ms: int) -> dict[str, Any]:
    target = evidence.get("target_ctha")
    if not isinstance(target, dict) or any(tf not in target for tf in REQUIRED_TFS):
        raise ValueError("ctha_missing")
    for tf in REQUIRED_TFS:
        state = target[tf]
        if tf == "1M":
            if int(state.get("history_bars") or 0) < 36 or state.get("ema25") is None:
                raise ValueError("ctha_1M_history_insufficient")
        elif state.get("indicator_completeness") != "FULL":
            raise ValueError(f"ctha_{tf}_incomplete")
        if type(state.get("close_time_ms")) is not int or state["close_time_ms"] >= decision_ms:
            raise ValueError(f"ctha_{tf}_time_invalid")
    def strong_bear(tf: str) -> bool:
        s = target[tf]
        if tf == "1M":
            return s.get("bos_down_vs_prior5") is True and dec(s.get("close"), "1M_close") < dec(s.get("ema25"), "1M_ema25")
        return s.get("ema_state") == "BEAR" and s.get("bos_down_vs_prior5") is True
    if any(strong_bear(tf) for tf in ("1M", "1w", "1d")):
        raise ValueError("ctha_material_htf_bear_contradiction")
    if strong_bear("4h") and strong_bear("1h"):
        raise ValueError("ctha_material_operational_bear_contradiction")
    def support(tf: str) -> bool:
        s = target[tf]
        return s.get("ema_state") == "BULL" or s.get("bos_up_vs_prior5") is True
    support_count = sum(1 for tf in ("1d", "4h", "1h") if support(tf))
    bias = "bullish" if support_count >= 2 else "range"
    known = max(int(target[tf]["close_time_ms"]) for tf in REQUIRED_TFS)
    evidence_sha = sha256_json(evidence)
    refs = [f"R89:R84:{evidence_sha}:{tf}" for tf in REQUIRED_TFS]
    return {"r84_evidence_sha256": evidence_sha, "status": "PASS",
            "material_contradiction": False, "direction": "LONG", "bias": bias,
            "mapped_timeframes": list(REQUIRED_TFS), "known_at_ms": known,
            "evidence_refs": refs}
def _targets(evidence: dict[str, Any], ask: Decimal, decision_ms: int) -> list[dict[str, Any]]:
    target = evidence["target_ctha"]
    evidence_sha = sha256_json(evidence)
    out: list[dict[str, Any]] = []
    for tf in ("15m", "1h", "4h", "1d", "1w", "1M"):
        state = target[tf]
        price = dec(state.get("prior_high"), f"{tf}_prior_high")
        known = int(state["close_time_ms"])
        if known >= decision_ms:
            raise ValueError(f"target_{tf}_time_invalid")
        if price > ask:
            out.append({"price": str(price), "valid": True, "known_at_ms": known,
                        "source_timeframe": tf,
                        "provenance": f"R89:R84:{evidence_sha}:{tf}:prior_high"})
    if not out:
        raise ValueError("no_r84_overhead_target")
    return out


def _event(symbol: str, kind: str, bar: dict[str, Any], reaction_id: str,
           m15_sha: str, level: Decimal | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"type": kind, "direction": "LONG", "reaction_id": reaction_id,
                            "time_ms": bar["close_time_ms"], "source_timeframe": "15m",
                            "provenance": f"R89:M15:{m15_sha}:{symbol}:{bar['open_time_ms']}"}
    if level is not None:
        item["level"] = str(level)
    return item


def _reaction_id(symbol: str, seq: dict[str, Any], bars: list[dict[str, Any]]) -> str:
    identity = {"contract": CONTRACT_ID, "symbol": symbol,
                "ref_low_index": seq["ref_low"]["index"],
                "ref_high_index": seq["ref_high"]["index"],
                "events": [bars[seq[k]]["close_time_ms"] for k in
                           ("sweep_i", "displacement_i", "shift_i", "bos_i")]}
    return "R89_" + sha256_json(identity)[:20].upper()
def build_r88_input(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != INPUT_SCHEMA:
        raise ValueError("input_schema_invalid")
    evidence = payload.get("r84_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("r84_evidence_missing")
    quote = evidence.get("quote")
    decision_ms = quote.get("decision_time_ms") if isinstance(quote, dict) else None
    if type(decision_ms) is not int or decision_ms <= 0:
        raise ValueError("decision_time_invalid")
    r84_gate = r88.r85.validate_r84_evidence(evidence, decision_ms)
    if r84_gate.get("status") != "PASS":
        raise ValueError("r84_evidence_invalid")
    bars = _bars(payload.get("m15_closed_bars"), decision_ms)
    seq = _find_sequence(bars, decision_ms)
    if seq is None:
        raise ValueError("no_complete_r89_sequence")
    symbol = evidence["symbol"]
    m15_sha = sha256_json(payload["m15_closed_bars"])
    reaction_id = _reaction_id(symbol, seq, bars)
    sweep = bars[seq["sweep_i"]]
    displacement = bars[seq["displacement_i"]]
    shift = bars[seq["shift_i"]]
    bos = bars[seq["bos_i"]]
    ctha = _ctha_assessment(evidence, decision_ms)
    ask = dec(quote.get("ask"), "quote_ask")
    targets = _targets(evidence, ask, decision_ms)
    nearest = min(dec(x["price"], "target") for x in targets)
    events = [
        _event(symbol, "LIQUIDITY_SWEEP_RECLAIM", sweep, reaction_id, m15_sha,
               seq["ref_low"]["price"]),
        _event(symbol, "DISPLACEMENT", displacement, reaction_id, m15_sha),
        _event(symbol, "MSS", shift, reaction_id, m15_sha, seq["ref_high"]["price"]),
        _event(symbol, "BOS", bos, reaction_id, m15_sha, seq["impulse_high"]),
    ]
    fvg = seq["fvg"]
    confluence = [{"type": "FVG", "status": "PASS", "direction": "LONG",
                   "reaction_id": reaction_id, "known_at_ms": fvg["known_at_ms"],
                   "linked_to_displacement": True, "source_timeframe": "15m",
                   "provenance": f"R89:M15:{m15_sha}:{symbol}:FVG:{fvg['bar_index']}"}]
    invalidation = {"price": str(sweep["low"]), "known_at_ms": sweep["close_time_ms"],
                    "source_timeframe": "15m",
                    "provenance": f"R89:M15:{m15_sha}:{symbol}:SWEEP_LOW"}
    structural_levels = {"invalidation": invalidation, "targets": targets,
                         "liquidity_objective": f"nearest_R84_prior_high={nearest}"}
    return {"schema": r88.INPUT_SCHEMA, "r84_evidence": evidence,
            "ctha_assessment": ctha, "structural_event_evidence": events,
            "confluence_evidence": confluence, "structural_levels": structural_levels,
            "cost_model": payload.get("cost_model"), "risk_state": payload.get("risk_state"),
            "expectancy": payload.get("expectancy", {"state": "RESEARCH_PENDING"})}
def compile_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        r88_input = build_r88_input(payload)
    except Exception as exc:
        return fail("R89_EVIDENCE_DEFINITION_FAILED", error=str(exc))
    compiled = r88.compile_candidate(r88_input)
    if compiled.get("candidate_compiled") is not True:
        return fail("R88_REJECTED_R89_EVIDENCE", r88_result=compiled)
    return {"schema": OUTPUT_SCHEMA, "result": "R89_CANDIDATE_COMPILED_R88_PASS",
            "contract": CONTRACT, "candidate_compiled": True,
            "r88_input": r88_input, "r88_input_sha256": sha256_json(r88_input),
            "r88_result": compiled, "r88_result_sha256": sha256_json(compiled),
            "ledger_write_authority": False, "can_trade": False,
            "capital_permission": "DENY"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply frozen R89 event definitions and R88/R85 gates")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = compile_candidate(payload)
        text = stable_json(result)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result.get("candidate_compiled") else 3
    except Exception as exc:
        print(stable_json({"schema": OUTPUT_SCHEMA, "result": "ERROR", "error": str(exc),
                          "candidate_compiled": False, "ledger_write_authority": False,
                          "can_trade": False, "capital_permission": "DENY"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
