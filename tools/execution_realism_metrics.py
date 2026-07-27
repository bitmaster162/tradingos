#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def queue_penetration_fill(
    *,
    side: str,
    limit_price: float,
    bar_high: float,
    bar_low: float,
    grid_step: float,
    penetration_fraction: float = 0.25,
) -> bool:
    """Conservative paper-limit fill: price must trade through the limit by a fraction of one grid step."""
    if grid_step <= 0:
        raise ValueError("grid_step must be positive")
    side_norm = side.lower()
    penetration = abs(grid_step) * penetration_fraction
    if side_norm in {"buy", "long"}:
        return bar_low <= limit_price - penetration
    if side_norm in {"sell", "short"}:
        return bar_high >= limit_price + penetration
    raise ValueError(f"unsupported side={side}")


def albers_obi_fill_probability(
    *,
    side: str,
    obi: float,
    base: float = 0.624,
    slope: float = 0.274,
    min_probability: float = 0.30,
    max_probability: float = 0.90,
) -> float:
    """Cheap maker-fill realism proxy.

    OBI is expected in [-1, 1], where positive means bid-heavy. Same-side queue pressure lowers
    fill probability: buy uses +OBI, sell uses -OBI.
    """
    side_norm = side.lower()
    if side_norm in {"buy", "long"}:
        same_side_obi = obi
    elif side_norm in {"sell", "short"}:
        same_side_obi = -obi
    else:
        raise ValueError(f"unsupported side={side}")
    return round(clamp(base - slope * same_side_obi, min_probability, max_probability), 6)


def equity_curve(returns: Iterable[float]) -> list[float]:
    total = 0.0
    curve: list[float] = []
    for value in returns:
        total += float(value)
        curve.append(total)
    return curve


def drawdowns(returns: Iterable[float]) -> list[float]:
    curve = equity_curve(returns)
    peak = 0.0
    out: list[float] = []
    for value in curve:
        peak = max(peak, value)
        out.append(value - peak)
    return out


def cdar(returns: Iterable[float], alpha: float = 0.8) -> float:
    """Conditional Drawdown at Risk as negative-R average of the worst drawdowns."""
    values = drawdowns(returns)
    if not values:
        return 0.0
    if not 0.0 <= alpha < 1.0:
        raise ValueError("alpha must be in [0, 1)")
    worst_count = max(1, math.ceil((1.0 - alpha) * len(values)))
    worst = sorted(values)[:worst_count]
    return round(statistics.mean(worst), 6)


def fleet_cdar(paths: Iterable[Iterable[float]], alpha: float = 0.8) -> float:
    """Average CDaR across strategy/equity paths."""
    values = [cdar(path, alpha=alpha) for path in paths]
    return round(statistics.mean(values), 6) if values else 0.0


def james_stein_shrinkage(
    expectancies: dict[str, float],
    *,
    noise_variance: float | None = None,
    global_mean: float | None = None,
) -> dict[str, Any]:
    """Shrink cell expectancies toward the global mean to reduce multiple-comparison overconfidence."""
    cleaned = {str(key): float(value) for key, value in expectancies.items()}
    k = len(cleaned)
    if k == 0:
        return {"global_mean": 0.0, "shrinkage_factor": 0.0, "cells": {}}
    g = float(global_mean) if global_mean is not None else statistics.mean(cleaned.values())
    denom = sum((value - g) ** 2 for value in cleaned.values())
    if noise_variance is None:
        noise_variance = statistics.pvariance(cleaned.values()) if k > 1 else 0.0
    if k <= 3 or denom <= 0:
        factor = 0.0
    else:
        factor = clamp(1.0 - ((k - 3) * float(noise_variance)) / denom, 0.0, 1.0)
    return {
        "global_mean": round(g, 6),
        "noise_variance": round(float(noise_variance), 6),
        "shrinkage_factor": round(factor, 6),
        "cells": {
            key: {
                "raw": round(value, 6),
                "shrunk": round(g + factor * (value - g), 6),
            }
            for key, value in cleaned.items()
        },
    }


def build_smoke_report() -> dict[str, Any]:
    returns_a = [1.0, -0.5, 0.75, -1.25, 2.0, -0.25]
    returns_b = [0.25, 0.25, -0.75, 1.0, -0.5, 0.5]
    cells = {
        "squeeze_short_low_vol": 0.31,
        "squeeze_long_low_vol": 0.08,
        "alt_breadth_short_low_atr": 0.64,
        "oi_reset_long": -0.09,
        "session_drift_short": 0.02,
    }
    report = {
        "generated_at": now_iso(),
        "tool": "execution_realism_metrics",
        "decision": "execution_realism_metrics_smoke_passed",
        "queue_penetration_examples": {
            "buy_not_filled_touch_only": queue_penetration_fill(
                side="buy", limit_price=100.0, bar_high=101.0, bar_low=99.9, grid_step=1.0
            ),
            "buy_filled_through_level": queue_penetration_fill(
                side="buy", limit_price=100.0, bar_high=101.0, bar_low=99.7, grid_step=1.0
            ),
            "sell_filled_through_level": queue_penetration_fill(
                side="sell", limit_price=100.0, bar_high=100.4, bar_low=99.0, grid_step=1.0
            ),
        },
        "obi_fill_probability_examples": {
            "buy_bid_heavy": albers_obi_fill_probability(side="buy", obi=0.8),
            "buy_ask_heavy": albers_obi_fill_probability(side="buy", obi=-0.8),
            "sell_bid_heavy": albers_obi_fill_probability(side="sell", obi=0.8),
            "sell_ask_heavy": albers_obi_fill_probability(side="sell", obi=-0.8),
        },
        "risk_examples": {
            "path_a_cdar_80": cdar(returns_a, alpha=0.8),
            "path_b_cdar_80": cdar(returns_b, alpha=0.8),
            "fleet_cdar_80": fleet_cdar([returns_a, returns_b], alpha=0.8),
        },
        "shrinkage_example": james_stein_shrinkage(cells, noise_variance=0.04),
        "runtime_boundary": {
            "library_only": True,
            "alerts_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
        },
        "can_trade": False,
    }
    checks = [
        report["queue_penetration_examples"]["buy_not_filled_touch_only"] is False,
        report["queue_penetration_examples"]["buy_filled_through_level"] is True,
        0.30 <= report["obi_fill_probability_examples"]["buy_bid_heavy"] <= 0.90,
        report["risk_examples"]["fleet_cdar_80"] <= 0.0,
        report["can_trade"] is False,
    ]
    if not all(checks):
        report["decision"] = "execution_realism_metrics_smoke_failed"
    return report


def write_outputs(report: dict[str, Any], out_prefix: str) -> None:
    prefix = ROOT / out_prefix if not Path(out_prefix).is_absolute() else Path(out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Execution Realism Metrics Smoke",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        "",
        "## Boundary",
        "",
        "- Library/smoke only.",
        "- No alerts, no paper-entry intents, no orders.",
        "",
        "## Metrics",
        "",
        f"- Queue penetration examples: `{report.get('queue_penetration_examples')}`.",
        f"- OBI fill probability examples: `{report.get('obi_fill_probability_examples')}`.",
        f"- Risk examples: `{report.get('risk_examples')}`.",
        f"- Shrinkage factor: `{report.get('shrinkage_example', {}).get('shrinkage_factor')}`.",
        "",
    ]
    prefix.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stdlib-only execution realism and risk metric helpers")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--out-prefix", default="docs/EXECUTION_REALISM_METRICS_SMOKE_2026-07-03")
    args = parser.parse_args()
    if not args.smoke:
        print(json.dumps({"tool": "execution_realism_metrics", "library_only": True, "can_trade": False}, indent=2))
        return 0
    report = build_smoke_report()
    write_outputs(report, args.out_prefix)
    print(json.dumps({
        "decision": report["decision"],
        "queue_buy_touch_only": report["queue_penetration_examples"]["buy_not_filled_touch_only"],
        "fleet_cdar_80": report["risk_examples"]["fleet_cdar_80"],
        "can_trade": report["can_trade"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["decision"].endswith("_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
