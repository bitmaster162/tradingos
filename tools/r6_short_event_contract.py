#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "tools" / "r6_event_definition_contract.py"
COMPILER_PATH = ROOT / "tools" / "r6_short_candidate_compiler.py"

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

base = _load("r92_base_r89", BASE_PATH)
compiler = _load("r92_short_compiler", COMPILER_PATH)

INPUT_SCHEMA = "tradingos.r92_short_event_input.v1"
OUTPUT_SCHEMA = "tradingos.r92_short_event_result.v1"
CONTRACT_ID = "R92_MACHINE_SHORT_EVENT_DEFINITION_V1_20260916"
CONTRACT = {"contract_id": CONTRACT_ID, "direction": "SHORT", "timeframe": "15m",
            "mirror_of": base.CONTRACT_ID, "thresholds_identical_magnitude": True,
            "invalidation": "SWEEP_BAR_HIGH",
            "target_pool": "R84_PRIOR_LOW_15M_1H_4H_1D_1W_1M_NEAREST_DOWNSIDE",
            "authority": {"can_trade": False, "capital_permission": "DENY"}}

def stable_json(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {"schema": OUTPUT_SCHEMA, "result": "NO_SHORT_CANDIDATE_FAIL_CLOSED", "reason": reason,
            "details": details, "contract_id": CONTRACT_ID, "candidate_compiled": False,
            "ledger_write_authority": False, "can_trade": False, "capital_permission": "DENY"}


def _is_displacement(bars: list[dict[str, Any]], i: int) -> bool:
    b = bars[i]
    tr = base._true_range(bars, i)
    if tr <= 0 or b["close"] >= b["open"]:
        return False
    atr = base._atr14_before(bars, i)
    body = b["open"] - b["close"]
    close_loc_from_high = (b["high"] - b["close"]) / (b["high"] - b["low"])
    return (tr >= base.DISPLACEMENT_TR_ATR * atr
            and body / tr >= base.DISPLACEMENT_BODY_FRAC
            and close_loc_from_high >= base.DISPLACEMENT_CLOSE_LOC
            and b["close"] < bars[i - 1]["low"])


def _fvg_after_displacement(bars: list[dict[str, Any]], d: int) -> dict[str, Any] | None:
    if d <= 0 or d + 1 >= len(bars):
        return None
    left_low = bars[d - 1]["low"]
    right_high = bars[d + 1]["high"]
    gap = left_low - right_high
    atr = base._atr14_before(bars, d)
    if gap <= 0 or gap < base.FVG_MIN_ATR * atr:
        return None
    return {"lower": right_high, "upper": left_low, "gap": gap,
            "known_at_ms": bars[d + 1]["close_time_ms"], "bar_index": d + 1}

def _find_sequence(bars: list[dict[str, Any]], decision_ms: int) -> dict[str, Any] | None:
    lows = base._pivot_lows(bars)
    highs = base._pivot_highs(bars)
    start = max(20, len(bars) - base.MAX_SEQUENCE_BARS - 8)
    for s in range(len(bars) - 1, start - 1, -1):
        sweep = bars[s]
        ref_low = base._latest_reference(lows, s, sweep["open_time_ms"])
        ref_high = base._latest_reference(highs, s, sweep["open_time_ms"])
        if ref_low is None or ref_high is None:
            continue
        if not (sweep["high"] > ref_high["price"] and sweep["close"] < ref_high["price"]):
            continue
        if ref_low["price"] >= sweep["close"]:
            continue
        d = next((i for i in range(s + 1, min(len(bars), s + 1 + base.MAX_DISPLACEMENT_DELAY))
                  if _is_displacement(bars, i)), None)
        if d is None:
            continue
        fvg = _fvg_after_displacement(bars, d)
        if fvg is None:
            continue
        m = next((i for i in range(d + 1, min(len(bars), d + 1 + base.MAX_SHIFT_DELAY))
                  if bars[i]["close"] < ref_low["price"]), None)
        if m is None:
            continue
        impulse_low = min(bars[i]["low"] for i in range(d, m + 1))
        b = next((i for i in range(m + 1, min(len(bars), m + 1 + base.MAX_BOS_DELAY))
                  if bars[i]["close"] < impulse_low), None)
        if b is None or b - s > base.MAX_SEQUENCE_BARS:
            continue
        if decision_ms - bars[b]["close_time_ms"] > base.MAX_DECISION_LAG_MS:
            continue
        return {"sweep_i": s, "displacement_i": d, "shift_i": m, "bos_i": b,
                "ref_low": ref_low, "ref_high": ref_high, "fvg": fvg,
                "impulse_low": impulse_low}
    return None

def _ctha_assessment(evidence: dict[str, Any], decision_ms: int) -> dict[str, Any]:
    target = evidence.get("target_ctha")
    if not isinstance(target, dict) or any(tf not in target for tf in base.REQUIRED_TFS):
        raise ValueError("ctha_missing")
    for tf in base.REQUIRED_TFS:
        state = target[tf]
        if tf == "1M":
            if int(state.get("history_bars") or 0) < 36 or state.get("ema25") is None:
                raise ValueError("ctha_1M_history_insufficient")
        elif state.get("indicator_completeness") != "FULL":
            raise ValueError(f"ctha_{tf}_incomplete")
        if type(state.get("close_time_ms")) is not int or state["close_time_ms"] >= decision_ms:
            raise ValueError(f"ctha_{tf}_time_invalid")
    def strong_bull(tf: str) -> bool:
        s = target[tf]
        if tf == "1M":
            return s.get("bos_up_vs_prior5") is True and base.dec(s.get("close"), "1M_close") > base.dec(s.get("ema25"), "1M_ema25")
        return s.get("ema_state") == "BULL" and s.get("bos_up_vs_prior5") is True
    if any(strong_bull(tf) for tf in ("1M", "1w", "1d")):
        raise ValueError("ctha_material_htf_bull_contradiction")
    if strong_bull("4h") and strong_bull("1h"):
        raise ValueError("ctha_material_operational_bull_contradiction")
    def support(tf: str) -> bool:
        s = target[tf]
        return s.get("ema_state") == "BEAR" or s.get("bos_down_vs_prior5") is True
    support_count = sum(1 for tf in ("1d", "4h", "1h") if support(tf))
    bias = "bearish" if support_count >= 2 else "range"
    known = max(int(target[tf]["close_time_ms"]) for tf in base.REQUIRED_TFS)
    evidence_sha = base.sha256_json(evidence)
    refs = [f"R92:R84:{evidence_sha}:{tf}" for tf in base.REQUIRED_TFS]
    return {"r84_evidence_sha256": evidence_sha, "status": "PASS", "material_contradiction": False,
            "direction": "SHORT", "bias": bias, "mapped_timeframes": list(base.REQUIRED_TFS),
            "known_at_ms": known, "evidence_refs": refs}

def _targets(evidence: dict[str, Any], bid: Decimal, decision_ms: int) -> list[dict[str, Any]]:
    target = evidence["target_ctha"]
    evidence_sha = base.sha256_json(evidence)
    out: list[dict[str, Any]] = []
    for tf in ("15m", "1h", "4h", "1d", "1w", "1M"):
        state = target[tf]
        price = base.dec(state.get("prior_low"), f"{tf}_prior_low")
        known = int(state["close_time_ms"])
        if known >= decision_ms:
            raise ValueError(f"target_{tf}_time_invalid")
        if 0 < price < bid:
            out.append({"price": str(price), "valid": True, "known_at_ms": known,
                        "source_timeframe": tf,
                        "provenance": f"R92:R84:{evidence_sha}:{tf}:prior_low"})
    if not out:
        raise ValueError("no_r84_downside_target")
    return out


def _event(symbol: str, kind: str, bar: dict[str, Any], rid: str,
           m15_sha: str, level: Decimal | None = None) -> dict[str, Any]:
    item = {"type": kind, "direction": "SHORT", "reaction_id": rid,
            "time_ms": bar["close_time_ms"], "source_timeframe": "15m",
            "provenance": f"R92:M15:{m15_sha}:{symbol}:{bar['open_time_ms']}"}
    if level is not None:
        item["level"] = str(level)
    return item


def _reaction_id(symbol: str, seq: dict[str, Any], bars: list[dict[str, Any]]) -> str:
    identity = {"contract": CONTRACT_ID, "symbol": symbol,
                "ref_low_index": seq["ref_low"]["index"], "ref_high_index": seq["ref_high"]["index"],
                "events": [bars[seq[k]]["close_time_ms"] for k in
                           ("sweep_i", "displacement_i", "shift_i", "bos_i")]}
    return "R92S_" + base.sha256_json(identity)[:20].upper()

def build_compiler_input(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != INPUT_SCHEMA:
        raise ValueError("input_schema_invalid")
    evidence = payload.get("r84_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("r84_evidence_missing")
    quote = evidence.get("quote")
    decision_ms = quote.get("decision_time_ms") if isinstance(quote, dict) else None
    if type(decision_ms) is not int or decision_ms <= 0:
        raise ValueError("decision_time_invalid")
    if base.r88.r85.validate_r84_evidence(evidence, decision_ms).get("status") != "PASS":
        raise ValueError("r84_evidence_invalid")
    bars = base._bars(payload.get("m15_closed_bars"), decision_ms)
    seq = _find_sequence(bars, decision_ms)
    if seq is None:
        raise ValueError("no_complete_r92_short_sequence")
    symbol = evidence["symbol"]
    m15_sha = base.sha256_json(payload["m15_closed_bars"])
    rid = _reaction_id(symbol, seq, bars)
    sweep = bars[seq["sweep_i"]]
    displacement = bars[seq["displacement_i"]]
    shift = bars[seq["shift_i"]]
    bos = bars[seq["bos_i"]]
    ctha = _ctha_assessment(evidence, decision_ms)
    bid = base.dec(quote.get("bid"), "quote_bid")
    targets = _targets(evidence, bid, decision_ms)
    nearest = max(base.dec(x["price"], "target") for x in targets)
    events = [
        _event(symbol, "LIQUIDITY_SWEEP_REJECT", sweep, rid, m15_sha, seq["ref_high"]["price"]),
        _event(symbol, "DISPLACEMENT", displacement, rid, m15_sha),
        _event(symbol, "MSS", shift, rid, m15_sha, seq["ref_low"]["price"]),
        _event(symbol, "BOS", bos, rid, m15_sha, seq["impulse_low"]),
    ]
    fvg = seq["fvg"]
    confluence = [{"type": "FVG", "status": "PASS", "direction": "SHORT",
                   "reaction_id": rid, "known_at_ms": fvg["known_at_ms"],
                   "linked_to_displacement": True, "source_timeframe": "15m",
                   "provenance": f"R92:M15:{m15_sha}:{symbol}:FVG:{fvg['bar_index']}"}]
    invalidation = {"price": str(sweep["high"]), "known_at_ms": sweep["close_time_ms"],
                    "source_timeframe": "15m",
                    "provenance": f"R92:M15:{m15_sha}:{symbol}:SWEEP_HIGH"}
    structural_levels = {"invalidation": invalidation, "targets": targets,
                         "liquidity_objective": f"nearest_R84_prior_low={nearest}"}
    return {"schema": compiler.INPUT_SCHEMA, "r84_evidence": evidence,
            "ctha_assessment": ctha, "structural_event_evidence": events,
            "confluence_evidence": confluence, "structural_levels": structural_levels,
            "cost_model": payload.get("cost_model"), "risk_state": payload.get("risk_state"),
            "expectancy": payload.get("expectancy", {"state": "RESEARCH_PENDING"})}


def compile_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        compiler_input = build_compiler_input(payload)
    except Exception as exc:
        return fail("R92_SHORT_EVIDENCE_DEFINITION_FAILED", error=str(exc))
    compiled = compiler.compile_candidate(compiler_input)
    if compiled.get("candidate_compiled") is not True:
        return fail("R92_SHORT_COMPILER_REJECTED", compiler_result=compiled)
    return {"schema": OUTPUT_SCHEMA, "result": "R92_SHORT_CANDIDATE_COMPILED_PASS",
            "contract": CONTRACT, "candidate_compiled": True,
            "compiler_input": compiler_input, "compiler_input_sha256": base.sha256_json(compiler_input),
            "compiler_result": compiled, "compiler_result_sha256": base.sha256_json(compiled),
            "ledger_write_authority": False, "can_trade": False, "capital_permission": "DENY"}

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Apply frozen R92 SHORT event definitions")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = compile_candidate(json.loads(args.input.read_text(encoding="utf-8")))
        text = stable_json(result)
        if args.output:
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
