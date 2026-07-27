#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cross_venue_catchup_nested_holdout import (
    CatchupConfig,
    causal_time_z,
    evaluate,
    gate,
    iso_from_ms,
    now_iso,
    portable_path,
    rank_key,
    read_aligned,
    read_binance,
    resolve_path,
    return_features,
    snapshot_provenance,
)


HYPOTHESIS_ID = "HYP-CROSS-VENUE-REBOUND-002"
FAMILY = "COINBASE_NEGATIVE_DISLOCATION_REBOUND_1M"
TRAIN_SNAPSHOT_ID = "20260624T072240Z-d26a7c55961d"
VALIDATION_START = "2026-06-24T02:37:00+00:00"
EARLIEST_VALIDATION_END = "2026-07-08T02:37:00+00:00"


def build_configs() -> list[CatchupConfig]:
    configs: list[CatchupConfig] = []
    for lookback in (1, 2, 5):
        for window in (360, 1440):
            for entry_z in (2.0, 2.5, 3.0):
                for minimum_return in (5.0, 10.0):
                    for hold in (1, 2, 5):
                        configs.append(
                            CatchupConfig(
                                strategy_id=(
                                    f"coinbase_negative_rebound_lb{lookback}_z{window}_e{entry_z:g}_"
                                    f"r{minimum_return:g}_h{hold}"
                                ),
                                return_lookback_minutes=lookback,
                                divergence_z_window_minutes=window,
                                entry_z=entry_z,
                                min_coinbase_return_bps=minimum_return,
                                hold_minutes=hold,
                            )
                        )
    return configs


def generate_signals(
    config: CatchupConfig,
    features: list[dict[str, float | int]],
    z_values: dict[int, float],
) -> list[dict[str, float | int]]:
    signals: list[dict[str, float | int]] = []
    for row in features:
        timestamp = int(row["time_ms"])
        z_value = z_values.get(timestamp)
        coinbase_return = float(row["coinbase_return_bps"])
        if z_value is None:
            continue
        if coinbase_return > -config.min_coinbase_return_bps or z_value > -config.entry_z:
            continue
        signals.append(
            {
                "time_ms": timestamp,
                "coinbase_return_bps": coinbase_return,
                "divergence_bps": float(row["divergence_bps"]),
                "divergence_z": z_value,
            }
        )
    return signals


def public_candidate(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {key: value for key, value in item.items() if not key.startswith("_")}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Venue Negative Rebound Train Search",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "- This is an adaptive follow-up after the positive catch-up hypothesis failed on train.",
        "- The current sealed snapshot is used for train selection only; it cannot provide validation or OOS.",
        f"- Tested / train-qualified: `{report['search']['tested']}` / `{report['search']['train_qualified']}`.",
        f"- Decision: `{report['decision']}`.",
    ]
    selected = report.get("selected_on_train")
    if selected:
        summary = selected["train"]["summary"]
        lines.append(
            f"- Frozen candidate: `{selected['strategy_id']}`, `{summary['trades']}` trades, "
            f"`{summary['mean_net_bps']}` net bps/trade."
        )
        lines.append(f"- Future validation cannot end before `{EARLIEST_VALIDATION_END}`.")
    else:
        best = report["top_train_results_regardless_of_gate"][0]
        summary = best["train"]["summary"]
        lines.append(
            f"- Best rejected: `{best['strategy_id']}`, `{summary['trades']}` trades, "
            f"`{summary['mean_net_bps']}` net bps/trade."
        )
    lines.extend(["- Validation and OOS were not opened.", "- `can_trade=false`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train-only adaptive search for negative cross-venue rebound")
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--cost-bps-per-side", type=float, default=10.0)
    parser.add_argument("--stress-extra-bps", type=float, default=5.0)
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_NEGATIVE_REBOUND_TRAIN_2026-06-24")
    parser.add_argument("--lock-path", default="configs/CROSS_VENUE_NEGATIVE_REBOUND_RESEARCH_LOCK.json")
    args = parser.parse_args()

    cache = resolve_path(args.cache_dir)
    provenance = snapshot_provenance(cache)
    if not provenance or provenance.get("snapshot_id") != TRAIN_SNAPSHOT_ID:
        raise SystemExit("exact_preregistered_train_snapshot_required")
    binance = read_binance(cache / "binance" / "BTCUSDT" / "1m_candles.csv")
    aligned = read_aligned(cache / "aligned" / "BTCUSDT__BTC-USD" / "1m_candles.csv")
    if not aligned or not binance:
        raise SystemExit("cross_venue_snapshot_empty")
    data_start_ms = int(aligned[0]["time_ms"])
    data_end_ms = int(aligned[-1]["time_ms"]) + 60_000

    feature_cache: dict[int, list[dict[str, float | int]]] = {}
    z_cache: dict[tuple[int, int], dict[int, float]] = {}
    signal_cache: dict[tuple[int, int, float, float], list[dict[str, float | int]]] = {}
    candidates: list[dict[str, Any]] = []
    for config in build_configs():
        lookback = config.return_lookback_minutes
        feature_cache.setdefault(lookback, return_features(aligned, lookback))
        z_key = (lookback, config.divergence_z_window_minutes)
        if z_key not in z_cache:
            z_cache[z_key] = causal_time_z(
                feature_cache[lookback], config.divergence_z_window_minutes, config.min_window_coverage
            )
        signal_key = z_key + (config.entry_z, config.min_coinbase_return_bps)
        if signal_key not in signal_cache:
            signal_cache[signal_key] = generate_signals(config, feature_cache[lookback], z_cache[z_key])
        train = evaluate(
            config,
            signal_cache[signal_key],
            binance,
            start_ms=data_start_ms,
            end_ms=data_end_ms,
            cost_bps_per_side=args.cost_bps_per_side,
            stress_extra_bps_per_side=args.stress_extra_bps,
            folds=4,
        )
        candidates.append(
            {
                "strategy_id": config.strategy_id,
                "config": asdict(config),
                "train": train,
                "train_gate": gate(train, "train"),
            }
        )

    candidates.sort(key=rank_key, reverse=True)
    qualified = [item for item in candidates if item["train_gate"]["pass"]]
    selected = qualified[0] if qualified else None
    decision = (
        "adaptive_rebound_candidate_frozen_waiting_future_validation"
        if selected
        else "reject_no_train_qualified_negative_rebound_candidate"
    )
    failed_checks = Counter(
        name
        for item in candidates
        for name, passed in item["train_gate"]["checks"].items()
        if not passed
    )
    report = {
        "generated_at": now_iso(),
        "hypothesis_id": HYPOTHESIS_ID,
        "family": FAMILY,
        "method": "adaptive_train_search_future_validation_required",
        "evidence_status": "adaptive_followup_not_independent_confirmation",
        "data": {
            "cache_dir": portable_path(cache),
            "snapshot": provenance,
            "aligned_rows": len(aligned),
            "binance_rows": len(binance),
            "first": iso_from_ms(data_start_ms),
            "last": iso_from_ms(data_end_ms - 60_000),
        },
        "search": {
            "tested": len(candidates),
            "unique_signal_sets": len(signal_cache),
            "train_qualified": len(qualified),
            "failed_check_counts": dict(sorted(failed_checks.items())),
            "validation_used_for_selection": False,
            "oos_used_for_selection": False,
        },
        "protocol": {
            "long_only": True,
            "negative_dislocation": True,
            "returns_only": True,
            "entry_next_minute": True,
            "cost_bps_per_side": args.cost_bps_per_side,
            "stress_extra_bps_per_side": args.stress_extra_bps,
            "bonferroni_required_probability": 0.999537037037037,
        },
        "top_train_candidates": [public_candidate(item) for item in qualified[:10]],
        "top_train_results_regardless_of_gate": [public_candidate(item) for item in candidates[:10]],
        "selected_on_train": public_candidate(selected),
        "validation": None,
        "validation_gate": None,
        "validation_opened": False,
        "oos": None,
        "oos_gate": None,
        "oos_opened": False,
        "future_validation_policy": {
            "start_inclusive": VALIDATION_START,
            "minimum_calendar_days": 14,
            "earliest_eligible_end": EARLIEST_VALIDATION_END,
            "new_verified_snapshot_required": True,
            "parameter_changes_forbidden": True,
        },
        "runtime_boundary": {
            "research_only": True,
            "observer_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "decision": decision,
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")

    lock = {
        "schema_version": 1,
        "hypothesis_id": HYPOTHESIS_ID,
        "family": FAMILY,
        "enabled": False,
        "status": decision,
        "selected_on_train": selected["config"] if selected else None,
        "validation_opened": False,
        "oos_opened": False,
        "future_validation_start": VALIDATION_START,
        "source_report": portable_path(out.with_suffix(".json")),
        "boundaries": {
            "observer_allowed": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    lock_path = resolve_path(args.lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "tested": len(candidates),
                "train_qualified": len(qualified),
                "selected": selected["strategy_id"] if selected else None,
                "validation_opened": False,
                "oos_opened": False,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
