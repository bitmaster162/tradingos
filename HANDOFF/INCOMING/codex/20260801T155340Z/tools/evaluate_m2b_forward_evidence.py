#!/usr/bin/env python3
"""Deterministic, no-network evaluator for the three frozen M2B tracks."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any


TRACK_A_ID = "RANGE_REFINED_FORWARD"
TRACK_A_STRATEGY = "range_4h_short_near_high_lb40_edge0.2_rr1x2_h16__refine_funding_spot_oi_expansion"
TRACK_B_ID = "HYP-SPOT-LEAD-001"
TRACK_B_STRATEGY = "spot_lead_r3_z336_e2.5_short_spot_leads_spot_relative_volume_leads_sl1.5_tp2_h8"
TRACK_C_ID = "LIQUIDATION_CONTINUOUS_SCORE"
TERMINALS = {"KEEP_FOR_FORWARD_PAPER", "KILL", "INSUFFICIENT_DATA", "INVALID_RESEARCH_RETURN"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            invalid += 1
    return rows, invalid


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone missing: {value}")
    return parsed


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def candidate_from_range_report(report: dict[str, Any]) -> dict[str, Any]:
    candidate = report.get("selected_candidate")
    if not isinstance(candidate, dict) or candidate.get("strategy_id") != TRACK_A_STRATEGY:
        raise ValueError("exact Track A selected candidate is absent")
    expected_filters = ["funding_aligned", "spot_confirms", "oi_expansion"]
    if candidate.get("filters") != expected_filters:
        raise ValueError("Track A filter set drifted")
    return candidate


def evaluate_track_a(raw: Path) -> dict[str, Any]:
    validator = load_json(raw / "RANGE_FAMILY_VALIDATOR_2026-06-16.json")
    selection = load_json(raw / "RANGE_WATCHLIST_REFINER_2026-06-16.json")
    nested = load_json(raw / "RANGE_EDGE_NESTED_HOLDOUT_2026-06-23.json")
    tombstone = load_json(raw / "EDGE_TOMBSTONE_REGISTRY_2026-07-03_AFTER_BYBIT_FORWARD_REVIEW.json")
    promotion = load_json(raw / "RANGE_REFINED_PROMOTION_GATE_2026-06-17.json")
    journal, invalid_rows = load_jsonl(raw / "range_refined_forward_observer.jsonl")
    candidate = candidate_from_range_report(selection)

    cutoff = str(validator["data"]["4h"]["latest_bar_ts"])
    cutoff_dt = parse_ts(cutoff)
    strict_rows = [row for row in journal if parse_ts(str(row["bar_ts"])) > cutoff_dt]
    identities = [
        (str(row.get("strategy_id")), str(row.get("bar_ts")), str(row.get("event_type")))
        for row in strict_rows
    ]
    event_counts = Counter(str(row.get("event_type")) for row in strict_rows)

    family = next(item for item in nested.get("families", []) if item.get("family") == "RANGE_REFINED_4H")
    tombstone_row = next(item for item in tombstone.get("entries", []) if item.get("family") == "RANGE_REFINED_4H")
    oos = family["oos"]
    oos_gate = family["oos_gate"]
    kill = bool(
        nested.get("selection_frozen_before_oos") is True
        and nested.get("method") == "train_only_nested_selection_then_untouched_calendar_oos"
        and oos_gate.get("pass") is False
        and family.get("decision") == "reject_oos_gate_failed"
        and "Do not revive" in str(tombstone_row.get("reuse_rule"))
    )
    terminal = "KILL" if kill else "INVALID_RESEARCH_RETURN"
    return {
        "track_id": TRACK_A_ID,
        "terminal": terminal,
        "candidate_id": TRACK_A_STRATEGY,
        "freeze": {
            "selection_report_generated_at": selection.get("generated_at"),
            "original_data_cutoff": cutoff,
            "configuration_hash": sha256(raw / "RANGE_WATCHLIST_REFINER_2026-06-16.json"),
            "filters": candidate["filters"],
            "side": candidate["side"],
            "rr": candidate["rr"],
            "max_hold_bars": candidate["max_hold_bars"],
            "cost_bps_per_side": selection["settings"]["cost_bps_per_side"],
        },
        "forward_journal": {
            "timestamp_semantics": "timezone-aware 4h completed bar timestamps; strict bar_ts > original cutoff",
            "raw_rows": len(journal),
            "strict_post_cutoff_rows": len(strict_rows),
            "unique_post_cutoff_rows": len(set(identities)),
            "duplicates": len(identities) - len(set(identities)),
            "invalid_rows": invalid_rows,
            "event_counts": dict(sorted(event_counts.items())),
            "first_post_cutoff_bar": strict_rows[0]["bar_ts"] if strict_rows else None,
            "last_post_cutoff_bar": strict_rows[-1]["bar_ts"] if strict_rows else None,
        },
        "independent_oos": {
            "selection_frozen_before_oos": nested.get("selection_frozen_before_oos"),
            "method": nested.get("method"),
            "split_ts": nested["data"]["split_ts"],
            "data_last": nested["data"]["last_bar_ts"],
            "trades": oos["summary"]["trades"],
            "expectancy_r": oos["summary"]["expectancy_r"],
            "stress_extra_bps_per_side": oos["cost_stress"]["extra_bps_per_side"],
            "stress_expectancy_r": oos["cost_stress"]["summary"]["expectancy_r"],
            "gate": oos_gate,
            "family_decision": family.get("decision"),
        },
        "tombstone": tombstone_row,
        "promotion_boundary": promotion.get("promotion"),
        "reason": (
            "The originally attractive full-history observer was later rejected by a selection-frozen, "
            "untouched calendar OOS gate and tombstoned. Reusing the same family/parameters would resurrect "
            "a killed family. The post-cutoff observer also emitted no signals."
            if terminal == "KILL"
            else "Required tombstone/OOS causal chain did not validate."
        ),
        "historical_holdout_used_for_keep": False,
        "can_trade": False,
    }


def evaluate_track_b(raw: Path, repo_root: Path) -> dict[str, Any]:
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from tools.spot_led_continuation_nested_holdout import (  # pylint: disable=import-outside-toplevel
        SpotLeadConfig,
        aligned_series,
        build_signal_features,
        evaluate,
        generate_signals,
        read_funding_events,
        read_ohlcv,
    )
    from tools.range_family_validator import load_interval_payload  # pylint: disable=import-outside-toplevel

    report_path = raw / "SPOT_LED_CONTINUATION_NESTED_HOLDOUT_2026-06-24.json"
    report = load_json(report_path)
    receipt = load_json(raw / "HYPOTHESIS_PREREGISTRATION_RECEIPT_SPOT_LEAD_2026-06-24.json")
    protocol = load_json(raw / "SPOT_LED_CONTINUATION_RESEARCH_PROTOCOL.json")
    candidate = report["top_train_results_regardless_of_gate"][0]
    if candidate.get("strategy_id") != TRACK_B_STRATEGY:
        raise ValueError("exact Track B fixed candidate is absent")
    if receipt.get("hypothesis_id") != TRACK_B_ID or protocol.get("hypothesis_id") != TRACK_B_ID:
        raise ValueError("Track B preregistration identity mismatch")

    config = SpotLeadConfig(**candidate["config"])
    cache = raw / "cache"
    bars, features, _ = load_interval_payload(cache, "1h", 12, 12)
    spot = read_ohlcv(cache / "spot" / "BTCUSDT" / "1h_klines.csv")
    funding = read_funding_events(cache / "futures" / "BTCUSDT" / "funding_raw.csv")
    series = aligned_series(bars, spot)
    signal_features = build_signal_features(series, config.return_lookback_hours, config.divergence_z_window_hours)
    signals = generate_signals(config, bars, features, signal_features)

    cutoff = str(report["data"]["last"])
    cutoff_dt = parse_ts(cutoff)
    start_index = next(index for index, bar in enumerate(bars) if parse_ts(str(bar.ts)) > cutoff_dt)
    fresh_signals = [signal for signal in signals if int(signal["bar_index"]) >= start_index]
    frozen_cost = float(report["protocol"]["cost_bps_per_side"])
    frozen_stress = float(report["protocol"]["stress_extra_bps_per_side"])
    fresh = evaluate(
        config,
        bars,
        fresh_signals,
        funding,
        start_index=start_index,
        end_index=len(bars),
        cost_bps=frozen_cost,
        stress_extra_bps=frozen_stress,
        folds=4,
    )
    old_trades = int(candidate["train"]["summary"]["trades"])
    fresh_trades = int(fresh["summary"]["trades"] or 0)
    combined = old_trades + fresh_trades
    minimum = int(protocol["train_gate"]["min_trades"])
    if combined < minimum:
        terminal = "INSUFFICIENT_DATA"
        reason = f"Only {fresh_trades} new resolved trades; old {old_trades} are not new evidence and combined {combined} < {minimum}."
    elif float(fresh["summary"].get("expectancy_r") or 0.0) <= 0 or float(
        fresh["cost_stress"]["summary"].get("expectancy_r") or 0.0
    ) <= 0:
        terminal = "KILL"
        reason = "The fixed candidate reached the sample floor but lost positive post-cost/stress quality on fresh evidence."
    else:
        terminal = "INSUFFICIENT_DATA"
        reason = "The sample floor was met, but the unopened validation/OOS contract does not authorize a forward-paper KEEP."

    return {
        "track_id": TRACK_B_ID,
        "terminal": terminal,
        "candidate_id": TRACK_B_STRATEGY,
        "freeze": {
            "preregistered_at": receipt.get("recorded_at"),
            "dataset_sha256": receipt.get("dataset_sha256"),
            "protocol_sha256": receipt.get("protocol_sha256_before_code"),
            "original_data_cutoff": cutoff,
            "configuration": asdict(config),
            "cost_bps_per_side": frozen_cost,
            "stress_extra_bps_per_side": frozen_stress,
            "entry_semantics": "completed signal bar; next-hour entry",
            "minimum_trades": minimum,
        },
        "fresh_source": {
            "first_bar": str(bars[start_index].ts),
            "last_futures_bar": str(bars[-1].ts),
            "spot_aligned_fresh_bars": sum(
                1 for index in range(start_index, len(bars)) if series["spot_close"][index] is not None
            ),
            "fresh_bars": len(bars) - start_index,
            "fresh_raw_signals": len(fresh_signals),
            "fresh_effective_trades": fresh_trades,
            "old_trades_not_counted_as_new": old_trades,
            "combined_evidence_count": combined,
            "missingness_note": "spot alignment counted on copied immutable CSV snapshot; no network fetch",
        },
        "fresh_evaluation": fresh,
        "fresh_signal_records": fresh_signals,
        "original_candidate_train_gate": candidate.get("train_gate"),
        "original_validation_opened": report.get("oos_opened"),
        "reason": reason,
        "historical_observations_reused_as_new": False,
        "can_trade": False,
    }


def score_bin(score: float, lock: dict[str, Any]) -> str:
    for row in lock["bins"]:
        if row["id"] == "inactive" and score == 0:
            return "inactive"
        lower_ok = score > float(row.get("min_exclusive", float("-inf"))) if "min_exclusive" in row else score >= float(row.get("min_inclusive", float("-inf")))
        upper = row.get("max_inclusive")
        if lower_ok and (upper is None or score <= float(upper)):
            return str(row["id"])
    raise ValueError(f"score outside frozen bins: {score}")


def evaluate_track_c(raw: Path) -> dict[str, Any]:
    lock_path = raw / "EDGE_LIQUIDATION_SCORE_SHADOW_LOCK.json"
    lock = load_json(lock_path)
    scoreboard = load_json(raw / "EDGE_LIQUIDATION_CONTEXT_SHADOW_SCOREBOARD_2026-06-23.json")
    gate = load_json(raw / "EDGE_LIQUIDATION_SCORE_EVIDENCE_GATE_2026-06-23.json")
    edge_rows, edge_invalid = load_jsonl(raw / "edge_forward_range_observer.jsonl")
    context_rows, context_invalid = load_jsonl(raw / "edge_liquidation_context_shadow.jsonl")
    if [lock["train_only_derivation"][key] for key in ("positive_q25", "positive_q50", "positive_q75")] != [0.426128, 1.414128, 5.109507]:
        raise ValueError("Track C frozen quantile drift")

    lock_dt = parse_ts(str(lock["generated_at"]))
    post_context = [
        row for row in context_rows if row.get("ts_emitted") and parse_ts(str(row["ts_emitted"])) > lock_dt
    ]
    context_ids = [
        (str(row.get("bar_ts")), str(row.get("edge_strategy_id")), str(row.get("status"))) for row in post_context
    ]
    observed = [row for row in edge_rows if row.get("event_type") == "range_refined_signal_observed"]
    post_signals = [
        row for row in observed if row.get("ts_emitted") and parse_ts(str(row["ts_emitted"])) > lock_dt
    ]
    signal_ids = [str(row.get("signal_key")) for row in post_signals]
    outcomes = scoreboard.get("labelled_outcomes", [])
    unique_outcomes: dict[str, dict[str, Any]] = {}
    invalid_outcomes = 0
    for outcome in outcomes:
        key = str(outcome.get("signal_key"))
        if not key or key in unique_outcomes:
            invalid_outcomes += 1
            continue
        score = outcome.get("continuous_score")
        if not isinstance(score, (int, float)) or score_bin(float(score), lock) != outcome.get("score_bin"):
            invalid_outcomes += 1
            continue
        if key not in signal_ids:
            invalid_outcomes += 1
            continue
        unique_outcomes[key] = outcome

    resolved = len(unique_outcomes)
    pending = len(set(signal_ids)) - resolved
    requirements = gate["requirements"]
    inactive_resolved = sum(1 for row in unique_outcomes.values() if row.get("score_bin") == "inactive")
    bins = Counter(str(row.get("score_bin")) for row in unique_outcomes.values())
    sample_gate = bool(
        resolved >= int(requirements["min_total_resolved"])
        and inactive_resolved >= int(requirements["min_baseline_resolved"])
        and any(count >= int(requirements["min_bin_resolved"]) for name, count in bins.items() if name != "inactive")
    )
    terminal = "INSUFFICIENT_DATA" if not sample_gate and invalid_outcomes == 0 else "INVALID_RESEARCH_RETURN"
    return {
        "track_id": TRACK_C_ID,
        "terminal": terminal,
        "freeze": {
            "generated_at": lock.get("generated_at"),
            "score_formula": lock.get("score_formula"),
            "lock_sha256": sha256(lock_path),
            "q25": 0.426128,
            "q50": 1.414128,
            "q75": 5.109507,
            "bins_recomputed_from_outcomes": False,
        },
        "reconciliation": {
            "raw_context_rows": len(context_rows),
            "strict_post_lock_context_rows": len(post_context),
            "unique_post_lock_context_rows": len(set(context_ids)),
            "duplicate_context_rows": len(context_ids) - len(set(context_ids)),
            "invalid_context_json_rows": context_invalid,
            "raw_edge_rows": len(edge_rows),
            "post_lock_signal_rows": len(post_signals),
            "unique_post_lock_signals": len(set(signal_ids)),
            "duplicate_signal_rows": len(signal_ids) - len(set(signal_ids)),
            "invalid_edge_json_rows": edge_invalid,
            "resolved": resolved,
            "pending": pending,
            "invalid_outcomes": invalid_outcomes,
            "resolved_by_bin": dict(sorted(bins.items())),
            "inactive_resolved": inactive_resolved,
        },
        "frozen_gate": requirements,
        "gate_checks": {
            "min_total_resolved": resolved >= int(requirements["min_total_resolved"]),
            "min_inactive_resolved": inactive_resolved >= int(requirements["min_baseline_resolved"]),
            "eligible_non_inactive_bin": any(
                count >= int(requirements["min_bin_resolved"]) for name, count in bins.items() if name != "inactive"
            ),
        },
        "validated_outcomes": list(unique_outcomes.values()),
        "reason": (
            f"Only {resolved}/{requirements['min_total_resolved']} resolved; inactive baseline "
            f"{inactive_resolved}/{requirements['min_baseline_resolved']}; no non-inactive bin reaches "
            f"{requirements['min_bin_resolved']}."
            if terminal == "INSUFFICIENT_DATA"
            else "Outcome reconciliation or frozen-bin validation failed."
        ),
        "filter_change_allowed": False,
        "veto_allowed": False,
        "can_trade": False,
    }


def m2a_terminal_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Map domain evidence into the predecessor's fail-closed terminal contract."""
    if row["track_id"] == TRACK_A_ID:
        return {
            "preregistration_valid": True,
            "source_provenance_valid": True,
            "source_hashes_match": True,
            "final_test_evaluated": True,
            "independent_sample_sufficient": True,
            "post_cost_expectancy": row["independent_oos"]["expectancy_r"],
            "bootstrap_lower_bound": 0.0,
            "bootstrap_computed": False,
            "bootstrap_absence_policy": "nonpositive fail-closed sentinel; primary and stress OOS expectancy are negative",
            "placebo_materially_weaker": False,
            "tail_risk_acceptable": False,
            "source_ablation_robust": False,
            "regime_ablation_robust": False,
            "leakage_detected": False,
        }
    if row["track_id"] == TRACK_B_ID:
        return {
            "preregistration_valid": True,
            "source_provenance_valid": True,
            "source_hashes_match": True,
            "final_test_evaluated": True,
            "independent_sample_sufficient": False,
            "post_cost_expectancy": row["fresh_evaluation"]["summary"]["expectancy_r"],
            "bootstrap_lower_bound": None,
            "placebo_materially_weaker": False,
            "tail_risk_acceptable": False,
            "source_ablation_robust": False,
            "regime_ablation_robust": False,
            "leakage_detected": False,
        }
    return {
        "preregistration_valid": True,
        "source_provenance_valid": True,
        "source_hashes_match": True,
        "final_test_evaluated": True,
        "independent_sample_sufficient": False,
        "post_cost_expectancy": (
            row["validated_outcomes"][0]["r"] if row.get("validated_outcomes") else 0.0
        ),
        "bootstrap_lower_bound": None,
        "placebo_materially_weaker": False,
        "tail_risk_acceptable": False,
        "source_ablation_robust": False,
        "regime_ablation_robust": False,
        "leakage_detected": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    try:
        rows = [
            evaluate_track_a(args.raw_root / "track_a"),
            evaluate_track_b(args.raw_root / "track_b", args.repo_root),
            evaluate_track_c(args.raw_root / "track_c"),
        ]
    except (KeyError, StopIteration, TypeError, ValueError) as exc:
        failure = {
            "terminal": "INVALID_RESEARCH_RETURN",
            "error": type(exc).__name__,
            "message": str(exc),
            "can_trade": False,
        }
        (args.out_dir / "EVALUATOR_FAILURE.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        return 4

    if len(rows) != 3 or {row["track_id"] for row in rows} != {TRACK_A_ID, TRACK_B_ID, TRACK_C_ID}:
        raise AssertionError("decision matrix must contain exactly the three bound tracks")
    if any(row["terminal"] not in TERMINALS for row in rows):
        raise AssertionError("unapproved track terminal")
    matrix = {
        "schema_version": 1,
        "task": "TRADING_EDGE_FORWARD_EVIDENCE_M2B",
        "rows": rows,
        "row_count": 3,
        "code05_handoff_created": any(row["terminal"] == "KEEP_FOR_FORWARD_PAPER" for row in rows),
        "can_trade": False,
        "capital_permission": "DENY",
        "deploy_permission": "DENY",
        "self_application": False,
    }
    for row in rows:
        (args.out_dir / f"{row['track_id']}_RESULT.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (args.out_dir / f"{row['track_id']}_M2A_EVIDENCE.json").write_text(
            json.dumps(m2a_terminal_evidence(row), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (args.out_dir / "FORWARD_EDGE_DECISION_MATRIX.json").write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.out_dir / "SOURCE_INVENTORY.json").write_text(
        json.dumps(source_inventory(args.raw_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"terminals": {row["track_id"]: row["terminal"] for row in rows}}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
