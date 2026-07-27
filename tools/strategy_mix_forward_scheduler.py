#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def run_command(command: list[str], timeout_s: int) -> dict[str, Any]:
    started = time.time()
    proc = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, timeout=timeout_s)
    return {
        "command": command,
        "exit_code": proc.returncode,
        "duration_s": round(time.time() - started, 3),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def range_historically_rejected(path: str) -> bool:
    report_path = resolve_path(path)
    if not report_path.exists():
        return False
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    return any(
        isinstance(row, dict)
        and row.get("family") == "RANGE_REFINED_4H"
        and str(row.get("decision") or "").startswith("reject_oos")
        for row in payload.get("families", [])
    )


def edge_candidate_locked(path: str) -> bool:
    lock_path = resolve_path(path)
    if not lock_path.exists():
        return False
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    boundaries = payload.get("boundaries") if isinstance(payload.get("boundaries"), dict) else {}
    return (
        payload.get("enabled") is True
        and isinstance(payload.get("candidate"), dict)
        and boundaries.get("observer_only") is True
        and boundaries.get("allow_orders") is False
        and boundaries.get("can_trade") is False
    )


def family_historically_rejected(path: str, family: str) -> bool:
    lock_path = resolve_path(path)
    if not lock_path.exists():
        return False
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        payload.get("family") == family
        and payload.get("enabled") is False
        and str(payload.get("status") or "").startswith("historically_rejected")
        and (payload.get("boundaries") or {}).get("can_trade") is False
    )


def cycle(args: argparse.Namespace, cycle_index: int) -> dict[str, Any]:
    range_rejected = range_historically_rejected(args.range_edge_nested_holdout)
    edge_locked = edge_candidate_locked(args.edge_forward_candidate_lock)
    trend_rejected = family_historically_rejected(args.trend_mix_lock, "TREND_MIX_4H")
    feed_result: dict[str, Any] | None = None
    regime_observer_result: dict[str, Any] | None = None
    oi_funding_context_result: dict[str, Any] | None = None
    range_refined_observer_result: dict[str, Any] | None = None
    range_refined_scoreboard_result: dict[str, Any] | None = None
    range_refined_scarcity_result: dict[str, Any] | None = None
    range_refined_pending_watch_result: dict[str, Any] | None = None
    range_refined_pending_watch_notify_result: dict[str, Any] | None = None
    edge_registry_result: dict[str, Any] | None = None
    edge_forward_export_result: dict[str, Any] | None = None
    edge_forward_observer_result: dict[str, Any] | None = None
    edge_liquidation_context_shadow_result: dict[str, Any] | None = None
    edge_forward_pending_watch_result: dict[str, Any] | None = None
    edge_forward_scoreboard_result: dict[str, Any] | None = None
    edge_liquidation_context_scoreboard_result: dict[str, Any] | None = None
    edge_liquidation_score_evidence_gate_result: dict[str, Any] | None = None
    edge_forward_pending_watch_notify_result: dict[str, Any] | None = None
    edge_forward_promotion_gate_result: dict[str, Any] | None = None
    derivatives_event_forward_observer_result: dict[str, Any] | None = None
    derivatives_event_pending_watch_result: dict[str, Any] | None = None
    derivatives_event_forward_scoreboard_result: dict[str, Any] | None = None
    derivatives_event_promotion_gate_result: dict[str, Any] | None = None
    derivatives_event_telegram_notify_result: dict[str, Any] | None = None
    edge_same_shape_shadow_observer_result: dict[str, Any] | None = None
    edge_same_shape_shadow_scoreboard_result: dict[str, Any] | None = None
    edge_compression_guard_shadow_observer_result: dict[str, Any] | None = None
    edge_compression_guard_shadow_scoreboard_result: dict[str, Any] | None = None
    range_refined_shadow_forward_observer_result: dict[str, Any] | None = None
    range_refined_shadow_forward_scoreboard_result: dict[str, Any] | None = None
    range_refined_shadow_promotion_gate_result: dict[str, Any] | None = None
    range_refined_promotion_gate_result: dict[str, Any] | None = None
    range_refined_alert_guard_result: dict[str, Any] | None = None
    oi_funding_scoreboard_result: dict[str, Any] | None = None
    scoreboard_result: dict[str, Any] | None = None
    oi_guard_promotion_gate_result: dict[str, Any] | None = None
    forward_outcome_accumulator_result: dict[str, Any] | None = None
    notify_result: dict[str, Any] | None = None
    if not args.skip_feed and not trend_rejected:
        feed_command = [
            sys.executable,
            "tools/strategy_mix_forward_paper_feed.py",
            "--source-report",
            args.source_report,
            "--symbol",
            args.symbol,
            "--interval",
            args.interval,
            "--limit",
            str(args.limit),
            "--out-prefix",
            args.feed_out_prefix,
        ]
        if args.with_spot:
            feed_command.append("--with-spot")
        feed_result = run_command(feed_command, args.feed_timeout_s)
    if args.regime_observer and not trend_rejected:
        regime_command = [
            sys.executable,
            "tools/canonical_regime_forward_observer.py",
            "--ohlcv-csv",
            args.cache_csv,
            "--card-json-path",
            args.signal_card_json_path,
            "--out-prefix",
            args.regime_observer_out_prefix,
        ]
        regime_observer_result = run_command(regime_command, args.regime_observer_timeout_s)
    if args.oi_funding_context and not trend_rejected:
        oi_funding_command = [
            sys.executable,
            "tools/oi_funding_forward_context_observer.py",
            "--symbol",
            args.symbol,
            "--interval",
            args.interval,
            "--ohlcv-csv",
            args.cache_csv,
            "--card-json-path",
            args.signal_card_json_path,
            "--out-prefix",
            args.oi_funding_context_out_prefix,
        ]
        if args.oi_funding_context_source:
            oi_funding_command.extend(["--source", args.oi_funding_context_source])
        oi_funding_context_result = run_command(oi_funding_command, args.oi_funding_context_timeout_s)
    if args.range_refined_observer:
        range_refined_observer_command = [
            sys.executable,
            "tools/range_refined_forward_observer.py",
            "--cache-dir",
            args.range_refined_observer_cache_dir,
            "--out-prefix",
            args.range_refined_observer_out_prefix,
        ]
        range_refined_observer_result = run_command(range_refined_observer_command, args.range_refined_observer_timeout_s)
    if args.range_refined_scoreboard and not range_rejected:
        range_refined_scoreboard_command = [
            sys.executable,
            "tools/range_refined_observer_scoreboard.py",
            "--out-prefix",
            args.range_refined_scoreboard_out_prefix,
        ]
        range_refined_scoreboard_result = run_command(range_refined_scoreboard_command, args.range_refined_scoreboard_timeout_s)
    if args.range_refined_scarcity_diagnostic and not range_rejected:
        range_refined_scarcity_command = [
            sys.executable,
            "tools/range_refined_signal_scarcity_diagnostic.py",
            "--cache-dir",
            args.range_refined_observer_cache_dir,
            "--out-prefix",
            args.range_refined_scarcity_out_prefix,
        ]
        range_refined_scarcity_result = run_command(range_refined_scarcity_command, args.range_refined_scarcity_timeout_s)
    if args.range_refined_pending_watch and not range_rejected:
        range_refined_pending_watch_command = [
            sys.executable,
            "tools/range_refined_pending_watch_monitor.py",
            "--cache-dir",
            args.range_refined_observer_cache_dir,
            "--out-prefix",
            args.range_refined_pending_watch_out_prefix,
        ]
        range_refined_pending_watch_result = run_command(range_refined_pending_watch_command, args.range_refined_pending_watch_timeout_s)
    if args.range_refined_pending_watch_notify and not range_rejected:
        range_refined_pending_watch_notify_command = [
            sys.executable,
            "tools/range_refined_pending_watch_telegram_notify.py",
            "--pending-watch-json-path",
            args.range_refined_pending_watch_out_prefix + ".json",
            "--out-prefix",
            args.range_refined_pending_watch_notify_out_prefix,
        ]
        if args.range_refined_pending_watch_notify_dry_run:
            range_refined_pending_watch_notify_command.append("--dry-run")
        range_refined_pending_watch_notify_result = run_command(
            range_refined_pending_watch_notify_command,
            args.range_refined_pending_watch_notify_timeout_s,
        )
    if args.edge_registry and not edge_locked:
        edge_registry_command = [
            sys.executable,
            "tools/edge_registry.py",
            "--out-prefix",
            args.edge_registry_out_prefix,
        ]
        edge_registry_result = run_command(edge_registry_command, args.edge_registry_timeout_s)
    if args.edge_forward_candidate_export:
        edge_forward_export_command = [
            sys.executable,
            "tools/edge_forward_candidate_export.py",
            "--edge-registry",
            args.edge_registry_out_prefix + ".json",
            "--candidate-lock",
            args.edge_forward_candidate_lock,
            "--out-prefix",
            args.edge_forward_candidate_out_prefix,
        ]
        edge_forward_export_result = run_command(edge_forward_export_command, args.edge_forward_candidate_timeout_s)
    if args.edge_forward_observer:
        edge_forward_observer_command = [
            sys.executable,
            "tools/range_refined_forward_observer.py",
            "--refiner-report",
            args.edge_forward_candidate_out_prefix + ".json",
            "--journal-path",
            args.edge_forward_observer_journal,
            "--state-path",
            args.edge_forward_observer_state,
            "--out-prefix",
            args.edge_forward_observer_out_prefix,
        ]
        edge_forward_observer_result = run_command(edge_forward_observer_command, args.edge_forward_observer_timeout_s)
    if args.edge_liquidation_context_shadow:
        edge_liquidation_context_command = [
            sys.executable,
            "tools/edge_liquidation_context_shadow_observer.py",
            "--cache-dir",
            args.edge_liquidation_context_cache_dir,
            "--score-lock",
            args.edge_liquidation_context_score_lock,
            "--journal-path",
            args.edge_liquidation_context_journal,
            "--state-path",
            args.edge_liquidation_context_state,
            "--out-prefix",
            args.edge_liquidation_context_out_prefix,
        ]
        edge_liquidation_context_shadow_result = run_command(
            edge_liquidation_context_command,
            args.edge_liquidation_context_timeout_s,
        )
    if args.edge_forward_pending_watch:
        edge_forward_pending_watch_command = [
            sys.executable,
            "tools/range_refined_pending_watch_monitor.py",
            "--refiner-report",
            args.edge_forward_candidate_out_prefix + ".json",
            "--journal-path",
            args.edge_forward_pending_watch_journal,
            "--out-prefix",
            args.edge_forward_pending_watch_out_prefix,
        ]
        edge_forward_pending_watch_result = run_command(edge_forward_pending_watch_command, args.edge_forward_pending_watch_timeout_s)
    if args.edge_forward_scoreboard:
        edge_forward_scoreboard_command = [
            sys.executable,
            "tools/range_refined_observer_scoreboard.py",
            "--journal-path",
            args.edge_forward_observer_journal,
            "--refiner-report",
            args.edge_forward_candidate_out_prefix + ".json",
            "--cache-csv",
            args.cache_csv,
            "--out-prefix",
            args.edge_forward_scoreboard_out_prefix,
        ]
        edge_forward_scoreboard_result = run_command(edge_forward_scoreboard_command, args.edge_forward_scoreboard_timeout_s)
    if args.edge_liquidation_context_scoreboard:
        edge_liquidation_context_scoreboard_command = [
            sys.executable,
            "tools/edge_liquidation_context_shadow_scoreboard.py",
            "--edge-journal",
            args.edge_forward_observer_journal,
            "--context-journal",
            args.edge_liquidation_context_journal,
            "--cache-csv",
            args.cache_csv,
            "--out-prefix",
            args.edge_liquidation_context_scoreboard_out_prefix,
        ]
        edge_liquidation_context_scoreboard_result = run_command(
            edge_liquidation_context_scoreboard_command,
            args.edge_liquidation_context_scoreboard_timeout_s,
        )
    if args.edge_liquidation_score_evidence_gate:
        edge_liquidation_score_evidence_gate_command = [
            sys.executable,
            "tools/edge_liquidation_score_evidence_gate.py",
            "--scoreboard",
            args.edge_liquidation_context_scoreboard_out_prefix + ".json",
            "--score-lock",
            args.edge_liquidation_context_score_lock,
            "--out-prefix",
            args.edge_liquidation_score_evidence_gate_out_prefix,
        ]
        edge_liquidation_score_evidence_gate_result = run_command(
            edge_liquidation_score_evidence_gate_command,
            args.edge_liquidation_score_evidence_gate_timeout_s,
        )
    if args.edge_forward_pending_watch_notify:
        edge_forward_pending_watch_notify_command = [
            sys.executable,
            "tools/range_refined_pending_watch_telegram_notify.py",
            "--pending-watch-json-path",
            args.edge_forward_pending_watch_out_prefix + ".json",
            "--state-path",
            args.edge_forward_pending_watch_notify_state,
            "--card-json-path",
            args.edge_forward_pending_watch_notify_card_json,
            "--card-md-path",
            args.edge_forward_pending_watch_notify_card_md,
            "--out-prefix",
            args.edge_forward_pending_watch_notify_out_prefix,
            "--message-prefix",
            "EDGE FORWARD WATCH - observer-only strict candidate. No entry, no paper intent, no orders.",
        ]
        if args.edge_forward_pending_watch_notify_dry_run:
            edge_forward_pending_watch_notify_command.append("--dry-run")
        edge_forward_pending_watch_notify_result = run_command(
            edge_forward_pending_watch_notify_command,
            args.edge_forward_pending_watch_notify_timeout_s,
        )
    if args.edge_forward_promotion_gate:
        edge_forward_promotion_gate_command = [
            sys.executable,
            "tools/edge_forward_promotion_gate.py",
            "--edge-export",
            args.edge_forward_candidate_out_prefix + ".json",
            "--observer",
            args.edge_forward_observer_out_prefix + ".json",
            "--scoreboard",
            args.edge_forward_scoreboard_out_prefix + ".json",
            "--pending-watch",
            args.edge_forward_pending_watch_out_prefix + ".json",
            "--pending-watch-notify",
            args.edge_forward_pending_watch_notify_out_prefix + ".json",
            "--out-prefix",
            args.edge_forward_promotion_gate_out_prefix,
        ]
        edge_forward_promotion_gate_result = run_command(
            edge_forward_promotion_gate_command,
            args.edge_forward_promotion_gate_timeout_s,
        )
    if args.derivatives_event_forward_observer:
        derivatives_event_forward_observer_command = [
            sys.executable,
            "tools/derivatives_event_forward_observer.py",
            "--miner-report",
            args.derivatives_event_miner_report,
            "--journal-path",
            args.derivatives_event_forward_observer_journal,
            "--state-path",
            args.derivatives_event_forward_observer_state,
            "--latest-card-json",
            args.derivatives_event_forward_observer_card_json,
            "--latest-card-md",
            args.derivatives_event_forward_observer_card_md,
            "--out-prefix",
            args.derivatives_event_forward_observer_out_prefix,
        ]
        derivatives_event_forward_observer_result = run_command(
            derivatives_event_forward_observer_command,
            args.derivatives_event_forward_observer_timeout_s,
        )
    if args.derivatives_event_pending_watch:
        derivatives_event_pending_watch_command = [
            sys.executable,
            "tools/derivatives_event_pending_watch.py",
            "--miner-report",
            args.derivatives_event_miner_report,
            "--out-prefix",
            args.derivatives_event_pending_watch_out_prefix,
        ]
        derivatives_event_pending_watch_result = run_command(
            derivatives_event_pending_watch_command,
            args.derivatives_event_pending_watch_timeout_s,
        )
    if args.derivatives_event_forward_scoreboard:
        derivatives_event_forward_scoreboard_command = [
            sys.executable,
            "tools/derivatives_event_forward_scoreboard.py",
            "--miner-report",
            args.derivatives_event_miner_report,
            "--journal-path",
            args.derivatives_event_forward_observer_journal,
            "--out-prefix",
            args.derivatives_event_forward_scoreboard_out_prefix,
        ]
        derivatives_event_forward_scoreboard_result = run_command(
            derivatives_event_forward_scoreboard_command,
            args.derivatives_event_forward_scoreboard_timeout_s,
        )
    if args.derivatives_event_promotion_gate:
        derivatives_event_promotion_gate_command = [
            sys.executable,
            "tools/derivatives_event_promotion_gate.py",
            "--miner-report",
            args.derivatives_event_miner_report,
            "--observer",
            args.derivatives_event_forward_observer_out_prefix + ".json",
            "--scoreboard",
            args.derivatives_event_forward_scoreboard_out_prefix + ".json",
            "--out-prefix",
            args.derivatives_event_promotion_gate_out_prefix,
        ]
        derivatives_event_promotion_gate_result = run_command(
            derivatives_event_promotion_gate_command,
            args.derivatives_event_promotion_gate_timeout_s,
        )
    if args.derivatives_event_telegram_notify:
        derivatives_event_telegram_notify_command = [
            sys.executable,
            "tools/derivatives_event_telegram_notify.py",
            "--observer-json-path",
            args.derivatives_event_forward_observer_out_prefix + ".json",
            "--scoreboard-json-path",
            args.derivatives_event_forward_scoreboard_out_prefix + ".json",
            "--gate-json-path",
            args.derivatives_event_promotion_gate_out_prefix + ".json",
            "--state-path",
            args.derivatives_event_telegram_notify_state,
            "--card-json-path",
            args.derivatives_event_telegram_notify_card_json,
            "--card-md-path",
            args.derivatives_event_telegram_notify_card_md,
            "--out-prefix",
            args.derivatives_event_telegram_notify_out_prefix,
        ]
        if args.derivatives_event_telegram_notify_dry_run:
            derivatives_event_telegram_notify_command.append("--dry-run")
        derivatives_event_telegram_notify_result = run_command(
            derivatives_event_telegram_notify_command,
            args.derivatives_event_telegram_notify_timeout_s,
        )
    if args.edge_same_shape_shadow_observer and not edge_locked:
        edge_same_shape_shadow_observer_command = [
            sys.executable,
            "tools/edge_same_shape_shadow_observer.py",
            "--out-prefix",
            args.edge_same_shape_shadow_out_prefix,
            "--journal-path",
            args.edge_same_shape_shadow_journal,
            "--state-path",
            args.edge_same_shape_shadow_state,
            "--top-n",
            str(args.edge_same_shape_shadow_top_n),
        ]
        edge_same_shape_shadow_observer_result = run_command(
            edge_same_shape_shadow_observer_command,
            args.edge_same_shape_shadow_timeout_s,
        )
    if args.edge_same_shape_shadow_scoreboard and not edge_locked:
        edge_same_shape_shadow_scoreboard_command = [
            sys.executable,
            "tools/edge_same_shape_shadow_scoreboard.py",
            "--journal-path",
            args.edge_same_shape_shadow_journal,
            "--cache-csv",
            args.cache_csv,
            "--out-prefix",
            args.edge_same_shape_shadow_scoreboard_out_prefix,
        ]
        edge_same_shape_shadow_scoreboard_result = run_command(
            edge_same_shape_shadow_scoreboard_command,
            args.edge_same_shape_shadow_scoreboard_timeout_s,
        )
    if args.edge_compression_guard_shadow_observer and not edge_locked:
        edge_compression_guard_shadow_observer_command = [
            sys.executable,
            "tools/edge_compression_guard_shadow_observer.py",
            "--out-prefix",
            args.edge_compression_guard_shadow_out_prefix,
            "--journal-path",
            args.edge_compression_guard_shadow_journal,
            "--state-path",
            args.edge_compression_guard_shadow_state,
        ]
        edge_compression_guard_shadow_observer_result = run_command(
            edge_compression_guard_shadow_observer_command,
            args.edge_compression_guard_shadow_timeout_s,
        )
    if args.edge_compression_guard_shadow_scoreboard and not edge_locked:
        edge_compression_guard_shadow_scoreboard_command = [
            sys.executable,
            "tools/edge_compression_guard_shadow_scoreboard.py",
            "--journal-path",
            args.edge_compression_guard_shadow_journal,
            "--cache-csv",
            args.cache_csv,
            "--out-prefix",
            args.edge_compression_guard_shadow_scoreboard_out_prefix,
        ]
        edge_compression_guard_shadow_scoreboard_result = run_command(
            edge_compression_guard_shadow_scoreboard_command,
            args.edge_compression_guard_shadow_scoreboard_timeout_s,
        )
    if args.range_refined_shadow_forward_observer and not range_rejected:
        range_refined_shadow_forward_command = [
            sys.executable,
            "tools/range_refined_filter_shadow_forward_observer.py",
            "--cache-dir",
            args.range_refined_observer_cache_dir,
            "--out-prefix",
            args.range_refined_shadow_forward_out_prefix,
        ]
        range_refined_shadow_forward_observer_result = run_command(
            range_refined_shadow_forward_command,
            args.range_refined_shadow_forward_timeout_s,
        )
    if args.range_refined_shadow_forward_scoreboard and not range_rejected:
        range_refined_shadow_forward_scoreboard_command = [
            sys.executable,
            "tools/range_refined_filter_shadow_forward_scoreboard.py",
            "--out-prefix",
            args.range_refined_shadow_forward_scoreboard_out_prefix,
        ]
        range_refined_shadow_forward_scoreboard_result = run_command(
            range_refined_shadow_forward_scoreboard_command,
            args.range_refined_shadow_forward_scoreboard_timeout_s,
        )
    if args.range_refined_shadow_promotion_gate and not range_rejected:
        range_refined_shadow_promotion_gate_command = [
            sys.executable,
            "tools/range_refined_filter_shadow_promotion_gate.py",
            "--scoreboard",
            args.range_refined_shadow_forward_scoreboard_out_prefix + ".json",
            "--ablation",
            args.range_refined_filter_ablation_report,
            "--out-prefix",
            args.range_refined_shadow_promotion_gate_out_prefix,
        ]
        range_refined_shadow_promotion_gate_result = run_command(
            range_refined_shadow_promotion_gate_command,
            args.range_refined_shadow_promotion_gate_timeout_s,
        )
    if args.range_refined_promotion_gate and not range_rejected:
        range_refined_promotion_gate_command = [
            sys.executable,
            "tools/range_refined_promotion_gate.py",
            "--refiner",
            args.range_refined_refiner_report,
            "--observer",
            args.range_refined_observer_out_prefix + ".json",
            "--scoreboard",
            args.range_refined_scoreboard_out_prefix + ".json",
            "--alert-drill",
            args.range_refined_alert_drill_report,
            "--out-prefix",
            args.range_refined_promotion_gate_out_prefix,
        ]
        range_refined_promotion_gate_result = run_command(range_refined_promotion_gate_command, args.range_refined_promotion_gate_timeout_s)
    if args.range_refined_alert_guard and not range_rejected:
        range_refined_alert_guard_command = [
            sys.executable,
            "tools/range_refined_signal_alert_guard.py",
            "--out-prefix",
            args.range_refined_alert_guard_out_prefix,
        ]
        if args.range_refined_alert_guard_dry_run:
            range_refined_alert_guard_command.append("--dry-run")
        range_refined_alert_guard_result = run_command(range_refined_alert_guard_command, args.range_refined_alert_guard_timeout_s)
    if not trend_rejected:
        scoreboard_command = [
            sys.executable,
            "tools/strategy_mix_forward_scoreboard.py",
            "--journal-path",
            args.journal_path,
            "--cache-csv",
            args.cache_csv,
            "--out-prefix",
            args.scoreboard_out_prefix,
        ]
        scoreboard_result = run_command(scoreboard_command, args.scoreboard_timeout_s)
    if args.oi_funding_scoreboard and not trend_rejected:
        oi_funding_scoreboard_command = [
            sys.executable,
            "tools/oi_funding_forward_context_scoreboard.py",
            "--context-journal",
            args.oi_funding_context_journal,
            "--forward-scoreboard",
            args.scoreboard_out_prefix + ".json",
            "--out-prefix",
            args.oi_funding_scoreboard_out_prefix,
        ]
        oi_funding_scoreboard_result = run_command(oi_funding_scoreboard_command, args.oi_funding_scoreboard_timeout_s)
    if args.oi_guard_promotion_gate and not trend_rejected:
        oi_guard_promotion_gate_command = [
            sys.executable,
            "tools/oi_guard_promotion_gate.py",
            "--validation",
            args.oi_guard_validation,
            "--forward-scoreboard",
            args.oi_funding_scoreboard_out_prefix + ".json",
            "--data-quality",
            args.oi_guard_data_quality,
            "--candidate",
            args.oi_guard_candidate,
            "--out-prefix",
            args.oi_guard_promotion_gate_out_prefix,
        ]
        oi_guard_promotion_gate_result = run_command(oi_guard_promotion_gate_command, args.oi_guard_promotion_gate_timeout_s)
    if args.forward_outcome_accumulator and not trend_rejected:
        forward_outcome_accumulator_command = [
            sys.executable,
            "tools/forward_outcome_accumulator.py",
            "--forward-scoreboard",
            args.scoreboard_out_prefix + ".json",
            "--oi-funding-scoreboard",
            args.oi_funding_scoreboard_out_prefix + ".json",
            "--promotion-gate",
            args.oi_guard_promotion_gate_out_prefix + ".json",
            "--forward-journal",
            args.journal_path,
            "--context-journal",
            args.oi_funding_context_journal,
            "--out-prefix",
            args.forward_outcome_accumulator_out_prefix,
        ]
        forward_outcome_accumulator_result = run_command(forward_outcome_accumulator_command, args.forward_outcome_accumulator_timeout_s)
    if args.telegram_notify and not trend_rejected:
        notify_command = [
            sys.executable,
            "tools/strategy_mix_forward_telegram_notify.py",
            "--card-json-path",
            args.signal_card_json_path,
            "--out-prefix",
            args.telegram_out_prefix,
        ]
        if args.telegram_dry_run:
            notify_command.append("--dry-run")
        notify_result = run_command(notify_command, args.telegram_timeout_s)
    event = {
        "event_type": "forward_scheduler_cycle",
        "ts_emitted": now_iso(),
        "cycle_index": cycle_index,
        "skip_feed": args.skip_feed,
        "trend_historically_rejected": trend_rejected,
        "feed": feed_result,
        "regime_observer": regime_observer_result,
        "oi_funding_context": oi_funding_context_result,
        "range_refined_observer": range_refined_observer_result,
        "range_refined_scoreboard": range_refined_scoreboard_result,
        "range_refined_scarcity_diagnostic": range_refined_scarcity_result,
        "range_refined_pending_watch": range_refined_pending_watch_result,
        "range_refined_pending_watch_telegram_notify": range_refined_pending_watch_notify_result,
        "edge_registry": edge_registry_result,
        "edge_forward_candidate_export": edge_forward_export_result,
        "edge_forward_observer": edge_forward_observer_result,
        "edge_liquidation_context_shadow": edge_liquidation_context_shadow_result,
        "edge_forward_pending_watch": edge_forward_pending_watch_result,
        "edge_forward_scoreboard": edge_forward_scoreboard_result,
        "edge_liquidation_context_scoreboard": edge_liquidation_context_scoreboard_result,
        "edge_liquidation_score_evidence_gate": edge_liquidation_score_evidence_gate_result,
        "edge_forward_pending_watch_telegram_notify": edge_forward_pending_watch_notify_result,
        "edge_forward_promotion_gate": edge_forward_promotion_gate_result,
        "derivatives_event_forward_observer": derivatives_event_forward_observer_result,
        "derivatives_event_pending_watch": derivatives_event_pending_watch_result,
        "derivatives_event_forward_scoreboard": derivatives_event_forward_scoreboard_result,
        "derivatives_event_promotion_gate": derivatives_event_promotion_gate_result,
        "derivatives_event_telegram_notify": derivatives_event_telegram_notify_result,
        "edge_same_shape_shadow_observer": edge_same_shape_shadow_observer_result,
        "edge_same_shape_shadow_scoreboard": edge_same_shape_shadow_scoreboard_result,
        "edge_compression_guard_shadow_observer": edge_compression_guard_shadow_observer_result,
        "edge_compression_guard_shadow_scoreboard": edge_compression_guard_shadow_scoreboard_result,
        "range_refined_filter_shadow_forward_observer": range_refined_shadow_forward_observer_result,
        "range_refined_filter_shadow_forward_scoreboard": range_refined_shadow_forward_scoreboard_result,
        "range_refined_filter_shadow_promotion_gate": range_refined_shadow_promotion_gate_result,
        "range_refined_promotion_gate": range_refined_promotion_gate_result,
        "range_refined_alert_guard": range_refined_alert_guard_result,
        "scoreboard": scoreboard_result,
        "oi_funding_scoreboard": oi_funding_scoreboard_result,
        "oi_guard_promotion_gate": oi_guard_promotion_gate_result,
        "forward_outcome_accumulator": forward_outcome_accumulator_result,
        "telegram_notify": notify_result,
        "can_trade": False,
        "decision": "forward_scheduler_edge_only_no_orders" if trend_rejected else "forward_scheduler_public_data_only_no_orders",
    }
    append_jsonl(resolve_path(args.scheduler_journal), event)
    return event


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest_cycle") if isinstance(report.get("latest_cycle"), dict) else {}
    feed = latest.get("feed") if isinstance(latest.get("feed"), dict) else {}
    scoreboard = latest.get("scoreboard") if isinstance(latest.get("scoreboard"), dict) else {}
    regime_observer = latest.get("regime_observer") if isinstance(latest.get("regime_observer"), dict) else {}
    oi_funding_context = latest.get("oi_funding_context") if isinstance(latest.get("oi_funding_context"), dict) else {}
    range_refined_observer = latest.get("range_refined_observer") if isinstance(latest.get("range_refined_observer"), dict) else {}
    range_refined_scoreboard = latest.get("range_refined_scoreboard") if isinstance(latest.get("range_refined_scoreboard"), dict) else {}
    range_refined_scarcity = latest.get("range_refined_scarcity_diagnostic") if isinstance(latest.get("range_refined_scarcity_diagnostic"), dict) else {}
    range_refined_pending_watch = latest.get("range_refined_pending_watch") if isinstance(latest.get("range_refined_pending_watch"), dict) else {}
    range_refined_pending_watch_notify = latest.get("range_refined_pending_watch_telegram_notify") if isinstance(latest.get("range_refined_pending_watch_telegram_notify"), dict) else {}
    edge_registry = latest.get("edge_registry") if isinstance(latest.get("edge_registry"), dict) else {}
    edge_forward_export = latest.get("edge_forward_candidate_export") if isinstance(latest.get("edge_forward_candidate_export"), dict) else {}
    edge_forward_observer = latest.get("edge_forward_observer") if isinstance(latest.get("edge_forward_observer"), dict) else {}
    edge_liquidation_context_shadow = latest.get("edge_liquidation_context_shadow") if isinstance(latest.get("edge_liquidation_context_shadow"), dict) else {}
    edge_forward_pending_watch = latest.get("edge_forward_pending_watch") if isinstance(latest.get("edge_forward_pending_watch"), dict) else {}
    edge_forward_scoreboard = latest.get("edge_forward_scoreboard") if isinstance(latest.get("edge_forward_scoreboard"), dict) else {}
    edge_liquidation_context_scoreboard = latest.get("edge_liquidation_context_scoreboard") if isinstance(latest.get("edge_liquidation_context_scoreboard"), dict) else {}
    edge_liquidation_score_evidence_gate = latest.get("edge_liquidation_score_evidence_gate") if isinstance(latest.get("edge_liquidation_score_evidence_gate"), dict) else {}
    edge_forward_pending_watch_notify = latest.get("edge_forward_pending_watch_telegram_notify") if isinstance(latest.get("edge_forward_pending_watch_telegram_notify"), dict) else {}
    edge_forward_promotion_gate = latest.get("edge_forward_promotion_gate") if isinstance(latest.get("edge_forward_promotion_gate"), dict) else {}
    derivatives_event_forward_observer = latest.get("derivatives_event_forward_observer") if isinstance(latest.get("derivatives_event_forward_observer"), dict) else {}
    derivatives_event_pending_watch = latest.get("derivatives_event_pending_watch") if isinstance(latest.get("derivatives_event_pending_watch"), dict) else {}
    derivatives_event_forward_scoreboard = latest.get("derivatives_event_forward_scoreboard") if isinstance(latest.get("derivatives_event_forward_scoreboard"), dict) else {}
    derivatives_event_promotion_gate = latest.get("derivatives_event_promotion_gate") if isinstance(latest.get("derivatives_event_promotion_gate"), dict) else {}
    derivatives_event_telegram_notify = latest.get("derivatives_event_telegram_notify") if isinstance(latest.get("derivatives_event_telegram_notify"), dict) else {}
    edge_same_shape_shadow_observer = latest.get("edge_same_shape_shadow_observer") if isinstance(latest.get("edge_same_shape_shadow_observer"), dict) else {}
    edge_same_shape_shadow_scoreboard = latest.get("edge_same_shape_shadow_scoreboard") if isinstance(latest.get("edge_same_shape_shadow_scoreboard"), dict) else {}
    edge_compression_guard_shadow_observer = latest.get("edge_compression_guard_shadow_observer") if isinstance(latest.get("edge_compression_guard_shadow_observer"), dict) else {}
    edge_compression_guard_shadow_scoreboard = latest.get("edge_compression_guard_shadow_scoreboard") if isinstance(latest.get("edge_compression_guard_shadow_scoreboard"), dict) else {}
    range_refined_shadow_forward = latest.get("range_refined_filter_shadow_forward_observer") if isinstance(latest.get("range_refined_filter_shadow_forward_observer"), dict) else {}
    range_refined_shadow_forward_scoreboard = latest.get("range_refined_filter_shadow_forward_scoreboard") if isinstance(latest.get("range_refined_filter_shadow_forward_scoreboard"), dict) else {}
    range_refined_shadow_promotion_gate = latest.get("range_refined_filter_shadow_promotion_gate") if isinstance(latest.get("range_refined_filter_shadow_promotion_gate"), dict) else {}
    range_refined_promotion_gate = latest.get("range_refined_promotion_gate") if isinstance(latest.get("range_refined_promotion_gate"), dict) else {}
    range_refined_alert_guard = latest.get("range_refined_alert_guard") if isinstance(latest.get("range_refined_alert_guard"), dict) else {}
    notify = latest.get("telegram_notify") if isinstance(latest.get("telegram_notify"), dict) else {}
    oi_funding_scoreboard = latest.get("oi_funding_scoreboard") if isinstance(latest.get("oi_funding_scoreboard"), dict) else {}
    oi_guard_promotion_gate = latest.get("oi_guard_promotion_gate") if isinstance(latest.get("oi_guard_promotion_gate"), dict) else {}
    forward_outcome_accumulator = latest.get("forward_outcome_accumulator") if isinstance(latest.get("forward_outcome_accumulator"), dict) else {}
    return "\n".join(
        [
            "# Strategy Mix Forward Scheduler",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Runs public forward paper feed, observers, scoreboards, promotion gates, outcome accumulator and Telegram notifier.",
            "- No private credentials, no account, no exchange orders.",
            "- Use `--cycles 0 --sleep-seconds 14400` for continuous 4H monitoring.",
            "",
            "## Latest Cycle",
            "",
            f"- Feed exit: `{feed.get('exit_code')}` duration `{feed.get('duration_s')}`s.",
            f"- Regime observer exit: `{regime_observer.get('exit_code')}` duration `{regime_observer.get('duration_s')}`s.",
            f"- OI/funding context exit: `{oi_funding_context.get('exit_code')}` duration `{oi_funding_context.get('duration_s')}`s.",
            f"- Range refined observer exit: `{range_refined_observer.get('exit_code')}` duration `{range_refined_observer.get('duration_s')}`s.",
            f"- Range refined scoreboard exit: `{range_refined_scoreboard.get('exit_code')}` duration `{range_refined_scoreboard.get('duration_s')}`s.",
            f"- Range refined scarcity diagnostic exit: `{range_refined_scarcity.get('exit_code')}` duration `{range_refined_scarcity.get('duration_s')}`s.",
            f"- Range refined pending watch exit: `{range_refined_pending_watch.get('exit_code')}` duration `{range_refined_pending_watch.get('duration_s')}`s.",
            f"- Range refined pending-watch Telegram notify exit: `{range_refined_pending_watch_notify.get('exit_code')}` duration `{range_refined_pending_watch_notify.get('duration_s')}`s.",
            f"- Edge registry exit: `{edge_registry.get('exit_code')}` duration `{edge_registry.get('duration_s')}`s.",
            f"- Edge forward candidate export exit: `{edge_forward_export.get('exit_code')}` duration `{edge_forward_export.get('duration_s')}`s.",
            f"- Edge forward observer exit: `{edge_forward_observer.get('exit_code')}` duration `{edge_forward_observer.get('duration_s')}`s.",
            f"- Edge liquidation context shadow exit: `{edge_liquidation_context_shadow.get('exit_code')}` duration `{edge_liquidation_context_shadow.get('duration_s')}`s.",
            f"- Edge forward pending watch exit: `{edge_forward_pending_watch.get('exit_code')}` duration `{edge_forward_pending_watch.get('duration_s')}`s.",
            f"- Edge forward scoreboard exit: `{edge_forward_scoreboard.get('exit_code')}` duration `{edge_forward_scoreboard.get('duration_s')}`s.",
            f"- Edge liquidation context scoreboard exit: `{edge_liquidation_context_scoreboard.get('exit_code')}` duration `{edge_liquidation_context_scoreboard.get('duration_s')}`s.",
            f"- Edge liquidation score evidence gate exit: `{edge_liquidation_score_evidence_gate.get('exit_code')}` duration `{edge_liquidation_score_evidence_gate.get('duration_s')}`s.",
            f"- Edge forward pending-watch Telegram notify exit: `{edge_forward_pending_watch_notify.get('exit_code')}` duration `{edge_forward_pending_watch_notify.get('duration_s')}`s.",
            f"- Edge forward promotion gate exit: `{edge_forward_promotion_gate.get('exit_code')}` duration `{edge_forward_promotion_gate.get('duration_s')}`s.",
            f"- Derivatives-event forward observer exit: `{derivatives_event_forward_observer.get('exit_code')}` duration `{derivatives_event_forward_observer.get('duration_s')}`s.",
            f"- Derivatives-event pending watch exit: `{derivatives_event_pending_watch.get('exit_code')}` duration `{derivatives_event_pending_watch.get('duration_s')}`s.",
            f"- Derivatives-event forward scoreboard exit: `{derivatives_event_forward_scoreboard.get('exit_code')}` duration `{derivatives_event_forward_scoreboard.get('duration_s')}`s.",
            f"- Derivatives-event promotion gate exit: `{derivatives_event_promotion_gate.get('exit_code')}` duration `{derivatives_event_promotion_gate.get('duration_s')}`s.",
            f"- Derivatives-event Telegram notify exit: `{derivatives_event_telegram_notify.get('exit_code')}` duration `{derivatives_event_telegram_notify.get('duration_s')}`s.",
            f"- Edge same-shape shadow observer exit: `{edge_same_shape_shadow_observer.get('exit_code')}` duration `{edge_same_shape_shadow_observer.get('duration_s')}`s.",
            f"- Edge same-shape shadow scoreboard exit: `{edge_same_shape_shadow_scoreboard.get('exit_code')}` duration `{edge_same_shape_shadow_scoreboard.get('duration_s')}`s.",
            f"- Edge compression guard shadow observer exit: `{edge_compression_guard_shadow_observer.get('exit_code')}` duration `{edge_compression_guard_shadow_observer.get('duration_s')}`s.",
            f"- Edge compression guard shadow scoreboard exit: `{edge_compression_guard_shadow_scoreboard.get('exit_code')}` duration `{edge_compression_guard_shadow_scoreboard.get('duration_s')}`s.",
            f"- Range refined filter shadow forward observer exit: `{range_refined_shadow_forward.get('exit_code')}` duration `{range_refined_shadow_forward.get('duration_s')}`s.",
            f"- Range refined filter shadow forward scoreboard exit: `{range_refined_shadow_forward_scoreboard.get('exit_code')}` duration `{range_refined_shadow_forward_scoreboard.get('duration_s')}`s.",
            f"- Range refined filter shadow promotion gate exit: `{range_refined_shadow_promotion_gate.get('exit_code')}` duration `{range_refined_shadow_promotion_gate.get('duration_s')}`s.",
            f"- Range refined promotion gate exit: `{range_refined_promotion_gate.get('exit_code')}` duration `{range_refined_promotion_gate.get('duration_s')}`s.",
            f"- Range refined alert guard exit: `{range_refined_alert_guard.get('exit_code')}` duration `{range_refined_alert_guard.get('duration_s')}`s.",
            f"- Scoreboard exit: `{scoreboard.get('exit_code')}` duration `{scoreboard.get('duration_s')}`s.",
            f"- OI/funding scoreboard exit: `{oi_funding_scoreboard.get('exit_code')}` duration `{oi_funding_scoreboard.get('duration_s')}`s.",
            f"- OI guard promotion gate exit: `{oi_guard_promotion_gate.get('exit_code')}` duration `{oi_guard_promotion_gate.get('duration_s')}`s.",
            f"- Forward outcome accumulator exit: `{forward_outcome_accumulator.get('exit_code')}` duration `{forward_outcome_accumulator.get('duration_s')}`s.",
            f"- Telegram notify exit: `{notify.get('exit_code')}` duration `{notify.get('duration_s')}`s.",
            f"- Scheduler journal: `{report.get('scheduler_journal')}`.",
            "",
            "## Decision",
            "",
            f"- `{report.get('decision')}`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="4H forward paper scheduler for strategy mix candidate")
    parser.add_argument("--source-report", default="docs/STRATEGY_MIX_FORWARD_LOCKED_CANDIDATE_2026-06-29_4H_GUARDED_SHORT.json")
    parser.add_argument("--trend-mix-lock", default="configs/TREND_MIX_FORWARD_LOCK.json")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--limit", type=int, default=320)
    parser.add_argument("--with-spot", action="store_true")
    parser.add_argument("--cycles", type=int, default=1, help="Number of cycles. Use 0 for infinite.")
    parser.add_argument("--sleep-seconds", type=int, default=14400)
    parser.add_argument("--skip-feed", action="store_true", help="Run scoreboard only; useful for local smoke tests without network.")
    parser.add_argument("--feed-timeout-s", type=int, default=180)
    parser.add_argument("--scoreboard-timeout-s", type=int, default=60)
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/strategy_mix_forward_paper_feed.jsonl")
    parser.add_argument("--cache-csv", default="_dl/forward_paper_feed/cache/futures/BTCUSDT/4h_klines.csv")
    parser.add_argument("--signal-card-json-path", default="logs/forward_paper_feed/latest_signal_card.json")
    parser.add_argument("--scheduler-journal", default="logs/forward_paper_feed/strategy_mix_forward_scheduler.jsonl")
    parser.add_argument("--feed-out-prefix", default="docs/STRATEGY_MIX_FORWARD_PAPER_FEED_2026-06-08")
    parser.add_argument("--scoreboard-out-prefix", default="docs/STRATEGY_MIX_FORWARD_SCOREBOARD_2026-06-08")
    parser.add_argument("--regime-observer-out-prefix", default="docs/CANONICAL_REGIME_FORWARD_OBSERVER_2026-06-09")
    parser.add_argument("--regime-observer-timeout-s", type=int, default=30)
    parser.add_argument("--regime-observer", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--oi-funding-context-out-prefix", default="docs/OI_FUNDING_FORWARD_CONTEXT_OBSERVER_2026-06-09")
    parser.add_argument("--oi-funding-context-timeout-s", type=int, default=60)
    parser.add_argument("--oi-funding-context-source", choices=["auto", "live", "cache"], default="auto")
    parser.add_argument("--oi-funding-context", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--oi-funding-context-journal", default="logs/forward_paper_feed/oi_funding_forward_context_observer.jsonl")
    parser.add_argument("--range-refined-observer-out-prefix", default="docs/RANGE_REFINED_FORWARD_OBSERVER_2026-06-16")
    parser.add_argument("--range-refined-observer-cache-dir", default="_dl/forward_paper_feed/cache")
    parser.add_argument("--range-edge-nested-holdout", default="docs/RANGE_EDGE_NESTED_HOLDOUT_2026-06-23.json")
    parser.add_argument("--range-refined-observer-timeout-s", type=int, default=30)
    parser.add_argument("--range-refined-observer", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--range-refined-scoreboard-out-prefix", default="docs/RANGE_REFINED_OBSERVER_SCOREBOARD_2026-06-16")
    parser.add_argument("--range-refined-scoreboard-timeout-s", type=int, default=30)
    parser.add_argument("--range-refined-scoreboard", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--range-refined-scarcity-out-prefix", default="docs/RANGE_REFINED_SIGNAL_SCARCITY_DIAGNOSTIC_2026-06-17")
    parser.add_argument("--range-refined-scarcity-timeout-s", type=int, default=30)
    parser.add_argument("--range-refined-scarcity-diagnostic", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--range-refined-pending-watch-out-prefix", default="docs/RANGE_REFINED_PENDING_WATCH_2026-06-17")
    parser.add_argument("--range-refined-pending-watch-timeout-s", type=int, default=30)
    parser.add_argument("--range-refined-pending-watch", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--range-refined-pending-watch-notify-out-prefix", default="docs/RANGE_REFINED_PENDING_WATCH_TELEGRAM_NOTIFY_2026-06-18")
    parser.add_argument("--range-refined-pending-watch-notify-timeout-s", type=int, default=30)
    parser.add_argument("--range-refined-pending-watch-notify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--range-refined-pending-watch-notify-dry-run", action="store_true")
    parser.add_argument("--edge-registry-out-prefix", default="docs/EDGE_REGISTRY_2026-06-18")
    parser.add_argument("--edge-registry-timeout-s", type=int, default=120)
    parser.add_argument("--edge-registry", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--edge-forward-candidate-out-prefix", default="docs/EDGE_FORWARD_CANDIDATE_REFINER_2026-06-18")
    parser.add_argument("--edge-forward-candidate-lock", default="configs/EDGE_FORWARD_LOCK.json")
    parser.add_argument("--edge-forward-candidate-timeout-s", type=int, default=60)
    parser.add_argument("--edge-forward-candidate-export", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--edge-forward-observer-out-prefix", default="docs/EDGE_FORWARD_RANGE_OBSERVER_2026-06-18")
    parser.add_argument("--edge-forward-observer-journal", default="logs/forward_paper_feed/edge_forward_range_observer.jsonl")
    parser.add_argument("--edge-forward-observer-state", default="logs/forward_paper_feed/edge_forward_range_observer_state.json")
    parser.add_argument("--edge-forward-observer-timeout-s", type=int, default=60)
    parser.add_argument("--edge-forward-observer", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--edge-liquidation-context-cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--edge-liquidation-context-score-lock", default="configs/EDGE_LIQUIDATION_SCORE_SHADOW_LOCK.json")
    parser.add_argument("--edge-liquidation-context-journal", default="logs/forward_paper_feed/edge_liquidation_context_shadow.jsonl")
    parser.add_argument("--edge-liquidation-context-state", default="logs/forward_paper_feed/edge_liquidation_context_shadow_state.json")
    parser.add_argument("--edge-liquidation-context-out-prefix", default="docs/EDGE_LIQUIDATION_CONTEXT_SHADOW_OBSERVER_2026-06-23")
    parser.add_argument("--edge-liquidation-context-timeout-s", type=int, default=30)
    parser.add_argument("--edge-liquidation-context-shadow", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--edge-forward-pending-watch-out-prefix", default="docs/EDGE_FORWARD_PENDING_WATCH_2026-06-18")
    parser.add_argument("--edge-forward-pending-watch-journal", default="logs/forward_paper_feed/edge_forward_pending_watch.jsonl")
    parser.add_argument("--edge-forward-pending-watch-timeout-s", type=int, default=60)
    parser.add_argument("--edge-forward-pending-watch", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--edge-forward-scoreboard-out-prefix", default="docs/EDGE_FORWARD_RANGE_SCOREBOARD_2026-06-18")
    parser.add_argument("--edge-forward-scoreboard-timeout-s", type=int, default=60)
    parser.add_argument("--edge-forward-scoreboard", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--edge-liquidation-context-scoreboard-out-prefix", default="docs/EDGE_LIQUIDATION_CONTEXT_SHADOW_SCOREBOARD_2026-06-23")
    parser.add_argument("--edge-liquidation-context-scoreboard-timeout-s", type=int, default=30)
    parser.add_argument("--edge-liquidation-context-scoreboard", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--edge-liquidation-score-evidence-gate-out-prefix", default="docs/EDGE_LIQUIDATION_SCORE_EVIDENCE_GATE_2026-06-23")
    parser.add_argument("--edge-liquidation-score-evidence-gate-timeout-s", type=int, default=30)
    parser.add_argument("--edge-liquidation-score-evidence-gate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--edge-forward-pending-watch-notify-out-prefix", default="docs/EDGE_FORWARD_PENDING_WATCH_TELEGRAM_NOTIFY_2026-06-18")
    parser.add_argument("--edge-forward-pending-watch-notify-state", default="logs/forward_paper_feed/edge_forward_pending_watch_telegram_state.json")
    parser.add_argument("--edge-forward-pending-watch-notify-card-json", default="logs/forward_paper_feed/latest_edge_forward_pending_watch_card.json")
    parser.add_argument("--edge-forward-pending-watch-notify-card-md", default="logs/forward_paper_feed/latest_edge_forward_pending_watch_card.md")
    parser.add_argument("--edge-forward-pending-watch-notify-timeout-s", type=int, default=60)
    parser.add_argument("--edge-forward-pending-watch-notify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--edge-forward-pending-watch-notify-dry-run", action="store_true")
    parser.add_argument("--edge-forward-promotion-gate-out-prefix", default="docs/EDGE_FORWARD_PROMOTION_GATE_2026-06-18")
    parser.add_argument("--edge-forward-promotion-gate-timeout-s", type=int, default=60)
    parser.add_argument("--edge-forward-promotion-gate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--derivatives-event-miner-report", default="docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json")
    parser.add_argument("--derivatives-event-forward-observer-out-prefix", default="docs/DERIVATIVES_EVENT_FORWARD_OBSERVER_2026-06-26")
    parser.add_argument("--derivatives-event-forward-observer-journal", default="logs/forward_paper_feed/derivatives_event_forward_observer.jsonl")
    parser.add_argument("--derivatives-event-forward-observer-state", default="logs/forward_paper_feed/derivatives_event_forward_observer_state.json")
    parser.add_argument("--derivatives-event-forward-observer-card-json", default="logs/forward_paper_feed/latest_derivatives_event_card.json")
    parser.add_argument("--derivatives-event-forward-observer-card-md", default="logs/forward_paper_feed/latest_derivatives_event_card.md")
    parser.add_argument("--derivatives-event-forward-observer-timeout-s", type=int, default=60)
    parser.add_argument("--derivatives-event-forward-observer", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--derivatives-event-pending-watch-out-prefix", default="docs/DERIVATIVES_EVENT_PENDING_WATCH_2026-06-27")
    parser.add_argument("--derivatives-event-pending-watch-timeout-s", type=int, default=60)
    parser.add_argument("--derivatives-event-pending-watch", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--derivatives-event-forward-scoreboard-out-prefix", default="docs/DERIVATIVES_EVENT_FORWARD_SCOREBOARD_2026-06-26")
    parser.add_argument("--derivatives-event-forward-scoreboard-timeout-s", type=int, default=60)
    parser.add_argument("--derivatives-event-forward-scoreboard", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--derivatives-event-promotion-gate-out-prefix", default="docs/DERIVATIVES_EVENT_PROMOTION_GATE_2026-06-26")
    parser.add_argument("--derivatives-event-promotion-gate-timeout-s", type=int, default=60)
    parser.add_argument("--derivatives-event-promotion-gate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--derivatives-event-telegram-notify-out-prefix", default="docs/DERIVATIVES_EVENT_TELEGRAM_NOTIFY_2026-06-26")
    parser.add_argument("--derivatives-event-telegram-notify-state", default="logs/forward_paper_feed/derivatives_event_telegram_notify_state.json")
    parser.add_argument("--derivatives-event-telegram-notify-card-json", default="logs/forward_paper_feed/latest_derivatives_event_watch_card.json")
    parser.add_argument("--derivatives-event-telegram-notify-card-md", default="logs/forward_paper_feed/latest_derivatives_event_watch_card.md")
    parser.add_argument("--derivatives-event-telegram-notify-timeout-s", type=int, default=60)
    parser.add_argument("--derivatives-event-telegram-notify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--derivatives-event-telegram-notify-dry-run", action="store_true")
    parser.add_argument("--edge-same-shape-shadow-out-prefix", default="docs/EDGE_SAME_SHAPE_SHADOW_OBSERVER_2026-06-19")
    parser.add_argument("--edge-same-shape-shadow-journal", default="logs/forward_paper_feed/edge_same_shape_shadow_observer.jsonl")
    parser.add_argument("--edge-same-shape-shadow-state", default="logs/forward_paper_feed/edge_same_shape_shadow_observer_state.json")
    parser.add_argument("--edge-same-shape-shadow-top-n", type=int, default=12)
    parser.add_argument("--edge-same-shape-shadow-timeout-s", type=int, default=60)
    parser.add_argument("--edge-same-shape-shadow-observer", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--edge-same-shape-shadow-scoreboard-out-prefix", default="docs/EDGE_SAME_SHAPE_SHADOW_SCOREBOARD_2026-06-19")
    parser.add_argument("--edge-same-shape-shadow-scoreboard-timeout-s", type=int, default=60)
    parser.add_argument("--edge-same-shape-shadow-scoreboard", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--edge-compression-guard-shadow-out-prefix", default="docs/EDGE_COMPRESSION_GUARD_SHADOW_OBSERVER_2026-06-19")
    parser.add_argument("--edge-compression-guard-shadow-journal", default="logs/forward_paper_feed/edge_compression_guard_shadow_observer.jsonl")
    parser.add_argument("--edge-compression-guard-shadow-state", default="logs/forward_paper_feed/edge_compression_guard_shadow_observer_state.json")
    parser.add_argument("--edge-compression-guard-shadow-timeout-s", type=int, default=60)
    parser.add_argument("--edge-compression-guard-shadow-observer", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--edge-compression-guard-shadow-scoreboard-out-prefix", default="docs/EDGE_COMPRESSION_GUARD_SHADOW_SCOREBOARD_2026-06-19")
    parser.add_argument("--edge-compression-guard-shadow-scoreboard-timeout-s", type=int, default=60)
    parser.add_argument("--edge-compression-guard-shadow-scoreboard", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--range-refined-shadow-forward-out-prefix", default="docs/RANGE_REFINED_FILTER_SHADOW_FORWARD_OBSERVER_2026-06-17")
    parser.add_argument("--range-refined-shadow-forward-timeout-s", type=int, default=30)
    parser.add_argument("--range-refined-shadow-forward-observer", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--range-refined-shadow-forward-scoreboard-out-prefix", default="docs/RANGE_REFINED_FILTER_SHADOW_FORWARD_SCOREBOARD_2026-06-17")
    parser.add_argument("--range-refined-shadow-forward-scoreboard-timeout-s", type=int, default=30)
    parser.add_argument("--range-refined-shadow-forward-scoreboard", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--range-refined-filter-ablation-report", default="docs/RANGE_REFINED_FILTER_SHADOW_ABLATION_2026-06-17.json")
    parser.add_argument("--range-refined-shadow-promotion-gate-out-prefix", default="docs/RANGE_REFINED_FILTER_SHADOW_PROMOTION_GATE_2026-06-17")
    parser.add_argument("--range-refined-shadow-promotion-gate-timeout-s", type=int, default=30)
    parser.add_argument("--range-refined-shadow-promotion-gate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--range-refined-refiner-report", default="docs/RANGE_WATCHLIST_REFINER_2026-06-16.json")
    parser.add_argument("--range-refined-alert-drill-report", default="docs/RANGE_REFINED_SIGNAL_ALERT_DRILL_2026-06-17.json")
    parser.add_argument("--range-refined-promotion-gate-out-prefix", default="docs/RANGE_REFINED_PROMOTION_GATE_2026-06-17")
    parser.add_argument("--range-refined-promotion-gate-timeout-s", type=int, default=30)
    parser.add_argument("--range-refined-promotion-gate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--range-refined-alert-guard-out-prefix", default="docs/RANGE_REFINED_SIGNAL_ALERT_GUARD_2026-06-16")
    parser.add_argument("--range-refined-alert-guard-timeout-s", type=int, default=30)
    parser.add_argument("--range-refined-alert-guard", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--range-refined-alert-guard-dry-run", action="store_true")
    parser.add_argument("--oi-funding-scoreboard-out-prefix", default="docs/OI_FUNDING_FORWARD_CONTEXT_SCOREBOARD_2026-06-15")
    parser.add_argument("--oi-funding-scoreboard-timeout-s", type=int, default=30)
    parser.add_argument("--oi-funding-scoreboard", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--oi-guard-validation", default="docs/STRATEGY_MIX_OI_GUARD_VALIDATION_2026-06-15.json")
    parser.add_argument("--oi-guard-data-quality", default="docs/OI_FUNDING_DATA_QUALITY_2026-06-15.json")
    parser.add_argument("--oi-guard-candidate", default="keep_oi_expansion_strong")
    parser.add_argument("--oi-guard-promotion-gate-out-prefix", default="docs/OI_GUARD_PROMOTION_GATE_2026-06-15")
    parser.add_argument("--oi-guard-promotion-gate-timeout-s", type=int, default=30)
    parser.add_argument("--oi-guard-promotion-gate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--forward-outcome-accumulator-out-prefix", default="docs/FORWARD_OUTCOME_ACCUMULATOR_2026-06-16")
    parser.add_argument("--forward-outcome-accumulator-timeout-s", type=int, default=30)
    parser.add_argument("--forward-outcome-accumulator", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--telegram-out-prefix", default="docs/STRATEGY_MIX_FORWARD_TELEGRAM_NOTIFY_2026-06-08")
    parser.add_argument("--telegram-timeout-s", type=int, default=30)
    parser.add_argument("--telegram-notify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--telegram-dry-run", action="store_true")
    parser.add_argument("--out-prefix", default="docs/STRATEGY_MIX_FORWARD_SCHEDULER_2026-06-08")
    args = parser.parse_args()

    events: list[dict[str, Any]] = []
    cycle_index = 0
    while True:
        cycle_index += 1
        events.append(cycle(args, cycle_index))
        if args.cycles > 0 and cycle_index >= args.cycles:
            break
        time.sleep(max(1, args.sleep_seconds))

    report = {
        "generated_at": now_iso(),
        "cycles": len(events),
        "latest_cycle": events[-1] if events else None,
        "scheduler_journal": str(resolve_path(args.scheduler_journal)),
        "runtime_boundary": {
            "classification": "forward_scheduler_public_data_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "decision": "forward_scheduler_public_data_only_no_orders",
        "can_trade": False,
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(out_prefix.with_suffix(".json")), "md": str(out_prefix.with_suffix(".md")), "cycles": len(events), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
