#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from statistics import fmean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
QUOTE_PATH = ROOT / "tools" / "binance_spot_event_time_quote_collector.py"
SPEC = importlib.util.spec_from_file_location("r82_quote", QUOTE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load R82 quote collector")
quote_mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = quote_mod
SPEC.loader.exec_module(quote_mod)

REST = "https://data-api.binance.vision/api/v3/klines"
TF_LIMITS = {"1M": 130, "1w": 130, "1d": 180, "4h": 180, "1h": 180, "15m": 180}

def fetch_json(url: str, timeout: float = 8.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "TradingOS-R6-Admission/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:  # nosec B310 public fixed HTTPS
        return json.loads(response.read().decode("utf-8"))


def fetch_klines(symbol: str, interval: str, limit: int) -> list[list[Any]]:
    query = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": limit})
    raw = fetch_json(f"{REST}?{query}")
    if not isinstance(raw, list) or len(raw) < 40:
        raise RuntimeError(f"insufficient klines {symbol} {interval}")
    now = int(time.time() * 1000)
    closed = [row for row in raw if isinstance(row, list) and int(row[6]) < now]
    if len(closed) < 40:
        raise RuntimeError(f"insufficient closed klines {symbol} {interval}")
    return closed


def ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("insufficient ema history")
    seed = fmean(values[:period])
    alpha = 2.0 / (period + 1.0)
    out = seed
    for value in values[period:]:
        out = alpha * value + (1.0 - alpha) * out
    return out

def rsi_wilder(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        raise ValueError("insufficient rsi history")
    gains, losses = [], []
    for a, b in zip(closes, closes[1:period + 1]):
        delta = b - a
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain, avg_loss = fmean(gains), fmean(losses)
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        avg_gain = ((period - 1) * avg_gain + max(delta, 0.0)) / period
        avg_loss = ((period - 1) * avg_loss + max(-delta, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def ao_value(rows: list[list[Any]]) -> float:
    mids = [(float(r[2]) + float(r[3])) / 2.0 for r in rows]
    if len(mids) < 34:
        raise ValueError("insufficient ao history")
    return fmean(mids[-5:]) - fmean(mids[-34:])


def recent_structure(rows: list[list[Any]], lookback: int = 20) -> dict[str, Any]:
    last = rows[-1]
    prior = rows[-(lookback + 1):-1]
    prior_high = max(float(r[2]) for r in prior)
    prior_low = min(float(r[3]) for r in prior)
    high, low, close = float(last[2]), float(last[3]), float(last[4])
    return {
        "prior_high": prior_high, "prior_low": prior_low,
        "swept_bsl_rejected": high > prior_high and close < prior_high,
        "swept_ssl_reclaimed": low < prior_low and close > prior_low,
    }

def summarize_tf(rows: list[list[Any]]) -> dict[str, Any]:
    closes = [float(r[4]) for r in rows]
    last = rows[-1]
    e25, e99 = ema(closes, 25), ema(closes, 99)
    close = closes[-1]
    if close > e25 > e99:
        ema_state = "BULL"
    elif close < e25 < e99:
        ema_state = "BEAR"
    else:
        ema_state = "MIXED"
    prior5 = rows[-6:-1]
    bos_up = close > max(float(r[2]) for r in prior5)
    bos_down = close < min(float(r[3]) for r in prior5)
    return {
        "open_time_ms": int(last[0]), "close_time_ms": int(last[6]),
        "open": float(last[1]), "high": float(last[2]), "low": float(last[3]), "close": close,
        "volume": float(last[5]), "ema25": e25, "ema99": e99, "ema_state": ema_state,
        "rsi14": rsi_wilder(closes), "ao5_34": ao_value(rows),
        "bos_up_vs_prior5": bos_up, "bos_down_vs_prior5": bos_down,
        **recent_structure(rows),
    }


def comparator_state(btc: dict[str, Any], eth: dict[str, Any]) -> dict[str, Any]:
    bret = (btc["close"] / btc["open"] - 1.0) * 100.0
    eret = (eth["close"] / eth["open"] - 1.0) * 100.0
    return {"btc_bar_return_pct": bret, "eth_bar_return_pct": eret,
            "eth_minus_btc_pct": eret - bret}

async def run_probe(symbol: str) -> dict[str, Any]:
    btc = {tf: summarize_tf(fetch_klines(symbol, tf, limit)) for tf, limit in TF_LIMITS.items()}
    eth = {tf: summarize_tf(fetch_klines("ETHUSDT", tf, limit)) for tf, limit in TF_LIMITS.items()}
    quote = await quote_mod.capture_quote_with_restarts(
        symbol=symbol, max_age_ms=2000, timeout_s=10.0, max_events=500, max_restarts=2
    )
    return {
        "schema": "tradingos.r6_admission_probe.v1",
        "observed_at_ms": int(time.time() * 1000),
        "symbol": symbol,
        "ctha": btc,
        "eth_comparator": {tf: comparator_state(btc[tf], eth[tf]) for tf in TF_LIMITS},
        "quote": quote,
        "can_trade": False,
        "capital_permission": "DENY",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output")
    args = parser.parse_args()
    payload = asyncio.run(run_probe(args.symbol))
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
