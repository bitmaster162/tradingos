#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_causal_shadow_evaluator as evaluator


def record(schema: str, payload: dict[str, Any], observed_at: int, received_at: int, source_id: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "observed_at": observed_at,
        "received_at": received_at,
        "source_hash": evaluator.canonical_sha256(payload),
        "schema_version": schema,
        "payload": payload,
    }


def read_pre_book_prefix(path: Path) -> str:
    lines: list[str] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip() == '"books": [':
                break
            lines.append(line)
        else:
            raise ValueError("top-level books array not found")
    prefix = "".join(lines)
    if not prefix.rstrip().endswith("],"):
        raise ValueError("unexpected pre-book packet boundary")
    return prefix


def reduce_packet(source: Path) -> dict[str, Any]:
    prefix = read_pre_book_prefix(source)
    partial = json.loads(prefix.rstrip()[:-1] + "\n}")
    signal_bars = partial.get("signal_bars")
    if not isinstance(signal_bars, list) or not signal_bars:
        raise ValueError("signal bars missing")
    last_signal = signal_bars[-1]
    payload = last_signal.get("payload") if isinstance(last_signal, dict) else None
    if not isinstance(payload, dict):
        raise ValueError("last signal payload missing")
    signal_close_ms = int(payload["close_ms"])
    close = float(payload["close"])

    book_payload = {
        "bids": [[round(close - 0.1, 1), 1.0]],
        "asks": [[round(close + 0.1, 1), 1.0]],
    }
    trade_payload = {"price": close, "size": 0.001, "side": "buy"}
    outcome_payload = {
        "open_ms": signal_close_ms,
        "close_ms": signal_close_ms + 300_000,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "symbol": "BTCUSDT",
        "interval": "5m",
        "price_type": "LAST_PRICE",
    }
    partial["books"] = [
        record("public-book-v1", book_payload, signal_close_ms + 100, signal_close_ms + 200, "wo008:synthetic-tail:book")
    ]
    partial["trades"] = [
        record("public-trade-v1", trade_payload, signal_close_ms + 300, signal_close_ms + 400, "wo008:synthetic-tail:trade")
    ]
    partial["outcome_bars"] = [
        record(
            "ohlcv-bar-v1",
            outcome_payload,
            outcome_payload["close_ms"],
            outcome_payload["close_ms"] + 10,
            "wo008:synthetic-tail:outcome",
        )
    ]
    partial["funding_events"] = []
    return partial


def main() -> int:
    parser = argparse.ArgumentParser(description="Reduce a live WO105 packet to its exact pre-veto prefix")
    parser.add_argument("source")
    parser.add_argument("output")
    args = parser.parse_args()

    output = Path(args.output).resolve()
    packet = reduce_packet(Path(args.source).resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "decision": "wo008_live_packet_prefix_reduced",
                "signal_bars": len(packet["signal_bars"]),
                "htf_bars": len(packet["htf_bars"]),
                "crowd_rows": len(packet["crowd"]),
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
