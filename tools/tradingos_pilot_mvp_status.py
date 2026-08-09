#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

TARGET_PILOTS = 5
PASS_COUNT = 3
PRICE_USD = 199
VALID_EVENTS = {"paid", "renewed"}


def evaluate(rows: list[dict]) -> dict:
    qualifying: dict[str, dict] = {}
    observed: set[str] = set()
    for row in rows:
        pilot_id = str(row.get("pilot_id", "")).strip()
        if not pilot_id:
            raise ValueError("pilot_id required")
        observed.add(pilot_id)
        event = str(row.get("event", "")).strip().lower()
        amount = row.get("amount_usd")
        if event in VALID_EVENTS and isinstance(amount, (int, float)) and not isinstance(amount, bool) and float(amount) >= PRICE_USD:
            qualifying[pilot_id] = row
    q = len(qualifying)
    n = len(observed)
    if q >= PASS_COUNT:
        status = "MVP_PASS"
    elif n >= TARGET_PILOTS:
        status = "MVP_NOT_YET_PASSING"
    else:
        status = "INSUFFICIENT_PILOTS"
    return {
        "rule": "3_of_5_pay_199_or_renew",
        "target_pilots": TARGET_PILOTS,
        "qualifying_required": PASS_COUNT,
        "minimum_amount_usd": PRICE_USD,
        "observed_pilots": n,
        "qualifying_pilots": q,
        "status": status,
        "can_trade": False,
        "capital_permission": "DENY",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the 3-of-5 $199-or-renew Decision Brief MVP gate")
    parser.add_argument("--ledger", required=True, type=Path)
    args = parser.parse_args()
    rows = []
    if args.ledger.exists():
        for line_no, line in enumerate(args.ledger.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"invalid row {line_no}")
            rows.append(row)
    print(json.dumps(evaluate(rows), ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
