#!/usr/bin/env python3
from __future__ import annotations

import json
import random
import sys


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[position]


def simulate_path(
    rng: random.Random,
    p_win: float,
    r_win: float,
    r_loss: float,
    risk_frac: float,
    n_trades: int,
) -> tuple[float, float, bool]:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    ruined = False
    ruin_floor = 0.5

    for _ in range(n_trades):
        r_multiple = r_win if rng.random() < p_win else r_loss
        equity *= 1.0 + risk_frac * r_multiple
        if equity > peak:
            peak = equity
        drawdown = 0.0 if peak <= 0 else 1.0 - (equity / peak)
        if drawdown > max_drawdown:
            max_drawdown = drawdown
        if equity <= ruin_floor:
            ruined = True
    return equity, max_drawdown, ruined


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        print("Usage: python risk_of_ruin_sim.py p_win R_win R_loss risk_% N_trades N_paths", file=sys.stderr)
        return 1

    p_win = float(argv[0])
    r_win = float(argv[1])
    r_loss = float(argv[2])
    risk_pct = float(argv[3])
    n_trades = int(argv[4])
    n_paths = int(argv[5])

    risk_frac = risk_pct / 100.0
    rng = random.Random(42)
    ending_equities: list[float] = []
    drawdowns: list[float] = []
    ruin_hits = 0

    for _ in range(n_paths):
        equity, max_drawdown, ruined = simulate_path(rng, p_win, r_win, r_loss, risk_frac, n_trades)
        ending_equities.append(equity)
        drawdowns.append(max_drawdown)
        ruin_hits += int(ruined)

    payload = {
        "assumptions": {
            "start_equity": 1.0,
            "ruin_definition": "equity <= 0.5 of start equity (50% drawdown from start)",
            "risk_pct_input_is_percent_of_equity": True,
            "geometric_compounding": True,
        },
        "inputs": {
            "p_win": p_win,
            "R_win": r_win,
            "R_loss": r_loss,
            "risk_pct": risk_pct,
            "N_trades": n_trades,
            "N_paths": n_paths,
        },
        "ruin_probability": ruin_hits / n_paths if n_paths else 0.0,
        "max_dd_quantiles": {
            "p50": quantile(drawdowns, 0.50),
            "p90": quantile(drawdowns, 0.90),
            "p95": quantile(drawdowns, 0.95),
            "p99": quantile(drawdowns, 0.99),
        },
        "ending_equity_quantiles": {
            "p10": quantile(ending_equities, 0.10),
            "p50": quantile(ending_equities, 0.50),
            "p90": quantile(ending_equities, 0.90),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
