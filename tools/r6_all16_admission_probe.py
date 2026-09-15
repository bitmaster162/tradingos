#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
R83_PATH = ROOT / "tools" / "r6_btc_admission_probe.py"
SPEC = importlib.util.spec_from_file_location("r83_probe", R83_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load R83 admission probe")
r83 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r83
SPEC.loader.exec_module(r83)

ALL16 = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT", "TRXUSDT",
    "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT", "LTCUSDT",
    "BCHUSDT", "AAVEUSDT", "NEARUSDT", "UNIUSDT",
)
SCHEMA = "tradingos.r6_all16_admission_probe.v1"
SYMBOL_SCHEMA = "tradingos.r6_symbol_admission_evidence.v1"
def _bar_return_pct(state: dict[str, Any]) -> float:
    return (float(state["close"]) / float(state["open"]) - 1.0) * 100.0


def relative_state(
    target: dict[str, Any], btc: dict[str, Any], eth: dict[str, Any]
) -> dict[str, float]:
    tret = _bar_return_pct(target)
    bret = _bar_return_pct(btc)
    eret = _bar_return_pct(eth)
    return {
        "target_bar_return_pct": tret,
        "btc_bar_return_pct": bret,
        "eth_bar_return_pct": eret,
        "target_minus_btc_pct": tret - bret,
        "target_minus_eth_pct": tret - eret,
        "eth_minus_btc_pct": eret - bret,
    }


def summarize_tf_safe(rows: list[list[Any]]) -> dict[str, Any]:
    closes = [float(r[4]) for r in rows]
    last = rows[-1]
    e25 = r83.ema(closes, 25) if len(closes) >= 25 else None
    e99 = r83.ema(closes, 99) if len(closes) >= 99 else None
    close = closes[-1]
    if e25 is None or e99 is None:
        ema_state = "UNKNOWN_INSUFFICIENT_HISTORY"
    elif close > e25 > e99:
        ema_state = "BULL"
    elif close < e25 < e99:
        ema_state = "BEAR"
    else:
        ema_state = "MIXED"
    prior5 = rows[-6:-1]
    prior = rows[-21:-1]
    prior_high = max(float(r[2]) for r in prior)
    prior_low = min(float(r[3]) for r in prior)
    high, low = float(last[2]), float(last[3])
    return {
        "open_time_ms": int(last[0]), "close_time_ms": int(last[6]),
        "open": float(last[1]), "high": high, "low": low, "close": close,
        "volume": float(last[5]), "history_bars": len(rows), "ema25": e25, "ema99": e99,
        "ema_state": ema_state, "rsi14": r83.rsi_wilder(closes) if len(closes) > 14 else None,
        "ao5_34": r83.ao_value(rows) if len(rows) >= 34 else None,
        "bos_up_vs_prior5": close > max(float(r[2]) for r in prior5),
        "bos_down_vs_prior5": close < min(float(r[3]) for r in prior5),
        "prior_high": prior_high, "prior_low": prior_low,
        "swept_bsl_rejected": high > prior_high and close < prior_high,
        "swept_ssl_reclaimed": low < prior_low and close > prior_low,
        "indicator_completeness": "FULL" if e99 is not None else "PARTIAL_INSUFFICIENT_EMA99",
    }


def fetch_ctha(symbol: str) -> dict[str, Any]:
    return {tf: summarize_tf_safe(r83.fetch_klines(symbol, tf, limit)) for tf, limit in r83.TF_LIMITS.items()}
async def capture_symbol(
    symbol: str,
    references: dict[str, dict[str, Any]],
    *,
    max_age_ms: int = 2000,
) -> dict[str, Any]:
    if symbol not in ALL16:
        raise ValueError(f"symbol not in canonical ALL16: {symbol}")
    target = references.get(symbol) or fetch_ctha(symbol)
    btc = references["BTCUSDT"]
    eth = references["ETHUSDT"]
    quote = await r83.quote_mod.capture_quote_with_restarts(
        symbol=symbol,
        max_age_ms=max_age_ms,
        timeout_s=10.0,
        max_events=500,
        max_restarts=2,
    )
    if quote.get("schema") != "tradingos.binance_spot_event_time_quote.v1":
        raise RuntimeError("unexpected quote schema")
    if quote.get("symbol") != symbol:
        raise RuntimeError("quote symbol mismatch")
    return {
        "schema": SYMBOL_SCHEMA,
        "symbol": symbol,
        "observed_at_ms": int(time.time() * 1000),
        "target_ctha": target,
        "reference_ctha": {"BTCUSDT": btc, "ETHUSDT": eth},
        "relative_strength": {
            tf: relative_state(target[tf], btc[tf], eth[tf]) for tf in r83.TF_LIMITS
        },
        "quote": quote,
        "admission_authority": "EVIDENCE_ONLY_FROZEN_CURRENT_RULES_APPLY",
        "can_trade": False,
        "capital_permission": "DENY",
    }


async def run_probe(symbols: list[str], max_age_ms: int = 2000) -> dict[str, Any]:
    bad = [s for s in symbols if s not in ALL16]
    if bad:
        raise ValueError(f"non-canonical symbols: {bad}")
    references = {"BTCUSDT": fetch_ctha("BTCUSDT"), "ETHUSDT": fetch_ctha("ETHUSDT")}
    results: list[dict[str, Any]] = []
    for symbol in symbols:
        if symbol not in references:
            references[symbol] = fetch_ctha(symbol)
        results.append(await capture_symbol(symbol, references, max_age_ms=max_age_ms))
    return {
        "schema": SCHEMA,
        "observed_at_ms": int(time.time() * 1000),
        "universe": list(ALL16),
        "requested_symbols": symbols,
        "evidence": results,
        "scored_admission_decision": "NOT_COMPUTED_BY_PROBE",
        "can_trade": False,
        "capital_permission": "DENY",
    }
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--all16", action="store_true")
    parser.add_argument("--max-age-ms", type=int, default=2000)
    parser.add_argument("--output")
    args = parser.parse_args()
    symbols = list(ALL16) if args.all16 else (args.symbols or ["BTCUSDT"])
    payload = asyncio.run(run_probe(symbols, max_age_ms=args.max_age_ms))
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
