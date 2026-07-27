#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: str | Path) -> dict[str, Any]:
    p = resolve_path(path)
    if not p.is_file():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_step(name: str, args: list[str], timeout_s: int) -> dict[str, Any]:
    started = now_iso()
    try:
        proc = subprocess.run(
            [sys.executable, *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "name": name,
            "started_at": started,
            "finished_at": now_iso(),
            "exit_code": proc.returncode,
            "command": " ".join([sys.executable, *args]),
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "started_at": started,
            "finished_at": now_iso(),
            "exit_code": 124,
            "command": " ".join([sys.executable, *args]),
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
            "error": "timeout",
        }


def compact_source(path: str) -> dict[str, Any]:
    payload = read_json(path)
    return {
        "path": path,
        "decision": payload.get("decision"),
        "review_action": payload.get("review_action"),
        "summary": payload.get("summary"),
        "evidence": payload.get("evidence"),
        "blockers": payload.get("blockers"),
        "can_trade": payload.get("can_trade", False),
        "orders_allowed": payload.get("orders_allowed", False),
    }


def classify(failed_steps: list[dict[str, Any]], sources: dict[str, dict[str, Any]]) -> tuple[str, str]:
    if failed_steps:
        return "real_edge_observer_pulse_failed_step", "inspect failed step output before relying on observer state"
    focus_decision = str(sources["live_data_focus"].get("decision") or "")
    if focus_decision in {
        "live_data_focus_inputs_missing_fail_closed",
        "live_data_focus_inputs_stale_fail_closed",
    }:
        return (
            "real_edge_observer_pulse_canonical_source_attention_required",
            "refresh missing or stale canonical live-data reports before relying on edge readiness",
        )
    transition_decision = str(sources["transition_monitor"].get("decision") or "")
    waiting_decision = str(sources["edge_waiting_board"].get("decision") or "")
    if transition_decision == "real_edge_transition_attention_required":
        return "real_edge_observer_pulse_attention_required", "review transition monitor before running more research"
    if waiting_decision == "edge_waiting_board_manual_attention_required":
        return "real_edge_observer_pulse_manual_attention_required", "review edge waiting board attention row"
    return "real_edge_observer_pulse_observing_no_trade", "keep collectors running and rerun pulse after new post-lock events or microstructure SLA recovery"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Real Edge Observer Pulse",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        "- Orders allowed: `false`",
        "",
        "## Step Status",
        "",
        "| Step | Exit | Report |",
        "|---|---:|---|",
    ]
    for step in report["steps"]:
        lines.append(f"| `{step['name']}` | `{step.get('exit_code')}` | `{step.get('report_path') or ''}` |")
    lines.extend(["", "## Source Decisions", "", "| Source | Decision | Review/Blockers |", "|---|---|---|"])
    for name, item in report["sources"].items():
        extra = item.get("review_action") or item.get("blockers") or ""
        lines.append(f"| `{name}` | `{item.get('decision')}` | `{extra}` |")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Safe observer pulse only.",
            "- Does not emit signals, paper entries, Telegram messages or orders.",
            "- `can_trade=false` and `orders_allowed=false` are hard boundaries.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="One safe pulse for current real-edge observer stack.")
    parser.add_argument("--out-prefix", default="docs/REAL_EDGE_OBSERVER_PULSE_2026-07-03")
    parser.add_argument("--timeout-s", type=int, default=240)
    args = parser.parse_args()

    report_paths = {
        "bybit_side_semantics_audit": "docs/BYBIT_LIQUIDATION_SIDE_SEMANTICS_AUDIT_2026-07-13.json",
        "bybit_canonical_v2_tombstone": "docs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_V2_DESIGN_TOMBSTONE_2026-07-13.json",
        "bybit_canonical_v3_clock_tombstone": "docs/BYBIT_LIQUIDATION_CANONICAL_V3_CLOCK_TOMBSTONE_2026-07-14.json",
        "bybit_canonical_v4_packet_tombstone": "docs/BYBIT_LIQUIDATION_CANONICAL_V4_PACKET_IDENTITY_TOMBSTONE_2026-07-15.json",
        "bybit_canonical_v5_source_tombstone": "docs/BYBIT_LIQUIDATION_CANONICAL_V5_SOURCE_COMPAT_TOMBSTONE_2026-07-15.json",
        "bybit_canonical_kline_refresh": "docs/BYBIT_LIQUIDATION_CANONICAL_KLINE_REFRESH_V4_2026-07-14.json",
        "bybit_canonical_input_quality": "docs/BYBIT_LIQUIDATION_CANONICAL_INPUT_QUALITY_V5R2_2026-07-18.json",
        "bybit_canonical_forward": "docs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V5R2_2026-07-18.json",
        "bybit_gate_pulse": "docs/BYBIT_LIQUIDATION_FORWARD_SEMANTIC_TOMBSTONE_2026-07-13.json",
        "post_liq_absorption": "docs/POST_LIQUIDATION_ABSORPTION_SEMANTIC_TOMBSTONE_2026-07-13.json",
        "liquidation_timing_vol": "docs/LIQUIDATION_TIMING_VOL_SEMANTIC_TOMBSTONE_2026-07-13.json",
        "liquidation_book_replenishment": "docs/LIQUIDATION_BOOK_REPLENISHMENT_FORWARD_OBSERVER_2026-07-12.json",
        "liquidation_book_replenishment_independence": "docs/LIQUIDATION_BOOK_REPLENISHMENT_INDEPENDENCE_GATE_2026-07-12.json",
        "liquidation_cross_venue_paired_leadership": "docs/LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_FORWARD_OBSERVER_V4_2026-07-15.json",
        "exogenous_liquidity_regime": "docs/EXOGENOUS_LIQUIDITY_REGIME_FORWARD_OBSERVER_2026-07-12.json",
        "deribit_options_runtime_audit": "docs/DERIBIT_OPTIONS_V3_DATA_LAYER_AUDIT_2026-07-16.json",
        "force_order_transport_continuity": "docs/LIQUIDATION_FORCE_ORDER_TRANSPORT_CONTINUITY_2026-07-15.json",
        "funding_successor_admission": "docs/CEX_FUNDING_SUCCESSOR_ADMISSION_2026-07-16.json",
        "spot_perp_flow_snapshot_guard": "docs/BINANCE_SPOT_PERP_AGGRESSOR_FLOW_SNAPSHOT_GUARD_2026-07-15.json",
        "live_data_focus": "docs/LIVE_DATA_EDGE_FOCUS_SUMMARY_2026-07-03.json",
        "tombstone_registry": "docs/EDGE_TOMBSTONE_REGISTRY_2026-07-03_AFTER_BYBIT_FORWARD_REVIEW.json",
        "strategy_frontier": "docs/STRATEGY_RESEARCH_FRONTIER_MATRIX_2026-07-03_AFTER_OBSERVER_PULSE.json",
        "active_observer_runtime_coverage": "docs/ACTIVE_OBSERVER_RUNTIME_COVERAGE_2026-07-13.json",
        "bitunix_wo105_v3r4_first_cycle_gate": "docs/BITUNIX_WO105_V3R4_FIRST_CYCLE_GATE_2026-07-15.json",
        "bitunix_wo105_v3r4_forward_health": "docs/BITUNIX_WO105_V3R4_FORWARD_HEALTH_2026-07-15.json",
        "edge_waiting_board": "docs/EDGE_WAITING_BOARD_2026-07-03_AFTER_OBSERVER_PULSE.json",
        "transition_monitor": "docs/REAL_EDGE_TRANSITION_ALERT_MONITOR_2026-07-03_OBSERVER_PULSE.json",
    }
    steps = [
        (
            "bybit_side_semantics_audit",
            [
                "tools/bybit_liquidation_side_semantics_audit.py",
                "--out-prefix",
                report_paths["bybit_side_semantics_audit"][:-5],
            ],
            report_paths["bybit_side_semantics_audit"],
        ),
        (
            "bybit_liquidation_canonical_v2_bar_closure_audit",
            [
                "tools/bybit_liquidation_canonical_v2_bar_closure_audit.py",
                "--out-prefix",
                "docs/BYBIT_LIQUIDATION_CANONICAL_V2_BAR_CLOSURE_AUDIT_2026-07-13",
            ],
            report_paths["bybit_canonical_v2_tombstone"],
        ),
        (
            "bybit_liquidation_canonical_kline_refresh_v4",
            [
                "tools/binance_rest_kline_tail_gap_filler_v2.py",
                "--market",
                "futures",
                "--symbols",
                "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,LTCUSDT",
                "--interval",
                "1h",
                "--max-pages",
                "1",
                "--no-backup",
                "--out-prefix",
                report_paths["bybit_canonical_kline_refresh"][:-5],
            ],
            report_paths["bybit_canonical_kline_refresh"],
        ),
        (
            "bybit_liquidation_canonical_input_quality_v5",
            [
                "tools/bybit_liquidation_canonical_input_quality_v5.py",
                "--prereg",
                "configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V5R2_2026-07-18.json",
                "--out-prefix",
                report_paths["bybit_canonical_input_quality"][:-5],
            ],
            report_paths["bybit_canonical_input_quality"],
        ),
        (
            "bybit_liquidation_canonical_forward_observer_v5",
            [
                "tools/bybit_liquidation_canonical_forward_observer_v5.py",
                "run-once",
                "--lock",
                "configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V5R2_2026-07-18.json",
                "--out-prefix",
                report_paths["bybit_canonical_forward"][:-5],
                "--terminal-receipt",
                "logs/bybit_liquidation_canonical_forward_v5r2/terminal_receipt.json",
            ],
            report_paths["bybit_canonical_forward"],
        ),
        (
            "liquidation_book_replenishment_forward_observer",
            [
                "tools/liquidation_book_replenishment_forward_observer.py",
                "--out-prefix",
                report_paths["liquidation_book_replenishment"][:-5],
            ],
            report_paths["liquidation_book_replenishment"],
        ),
        (
            "liquidation_book_replenishment_independence_gate",
            [
                "tools/liquidation_book_replenishment_independence_gate.py",
                "--out-prefix",
                report_paths["liquidation_book_replenishment_independence"][:-5],
            ],
            report_paths["liquidation_book_replenishment_independence"],
        ),
        (
            "liquidation_cross_venue_paired_leadership_v4",
            [
                "tools/liquidation_cross_venue_canonical_paired_leadership_forward_observer_v4.py",
                "run-once",
                "--lock",
                "configs/LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_LOCK_V4_2026-07-15.json",
                "--out-prefix",
                report_paths["liquidation_cross_venue_paired_leadership"][:-5],
                "--terminal-receipt",
                "logs/liquidation_cross_venue_canonical_paired_leadership_v4/terminal_receipt.json",
            ],
            report_paths["liquidation_cross_venue_paired_leadership"],
        ),
        (
            "exogenous_liquidity_regime_forward_observer",
            [
                "tools/exogenous_liquidity_regime_forward_observer.py",
                "--out-prefix",
                report_paths["exogenous_liquidity_regime"][:-5],
            ],
            report_paths["exogenous_liquidity_regime"],
        ),
        (
            "deribit_options_v3_runtime_audit",
            [
                "tools/deribit_options_v3_runtime_audit.py",
                "--out-prefix",
                report_paths["deribit_options_runtime_audit"][:-5],
            ],
            report_paths["deribit_options_runtime_audit"],
        ),
        (
            "liquidation_force_order_transport_continuity",
            [
                "tools/liquidation_force_order_transport_continuity.py",
                "--out-prefix",
                report_paths["force_order_transport_continuity"][:-5],
            ],
            report_paths["force_order_transport_continuity"],
        ),
        (
            "cex_funding_successor_admission_gate",
            [
                "tools/cex_funding_successor_admission_gate.py",
                "--out-prefix",
                report_paths["funding_successor_admission"][:-5],
            ],
            report_paths["funding_successor_admission"],
        ),
        (
            "bitunix_wo105_v3r4_first_cycle_gate",
            [
                "tools/bitunix_wo105_v2_first_cycle_gate.py",
                "--lock",
                "configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json",
                "--loop-status",
                "logs/bitunix_wo105_v3r4/bitunix_wo105_v3r4_forward_loop_status.json",
                "--rest-root",
                "data/forward/bitunix_wo105_v3r4_rest",
                "--ws-intake",
                "_dl/bitunix_wo105_v3r4_ws_intake/WS_INTAKE_MANIFEST.json",
                "--packet-status",
                "_dl/bitunix_wo105_shadow_v3r4/PACKET_ASSEMBLY_STATUS.json",
                "--milestone-journal",
                "logs/bitunix_wo105_v3r4/bitunix_wo105_v3r4_first_cycle_milestones.jsonl",
                "--out-prefix",
                report_paths["bitunix_wo105_v3r4_first_cycle_gate"][:-5],
            ],
            report_paths["bitunix_wo105_v3r4_first_cycle_gate"],
        ),
        (
            "bitunix_wo105_v3r4_forward_health",
            [
                "tools/bitunix_wo105_v3r4_forward_health.py",
                "--out-prefix",
                report_paths["bitunix_wo105_v3r4_forward_health"][:-5],
            ],
            report_paths["bitunix_wo105_v3r4_forward_health"],
        ),
        (
            "binance_spot_perp_aggressor_flow_snapshot_guard",
            [
                "tools/binance_spot_perp_aggressor_flow_snapshot_guard.py",
                "--out-prefix",
                report_paths["spot_perp_flow_snapshot_guard"][:-5],
            ],
            report_paths["spot_perp_flow_snapshot_guard"],
        ),
        (
            "live_data_edge_focus_summary",
            [
                "tools/live_data_edge_focus_summary.py",
                "--bybit-canonical-forward",
                report_paths["bybit_canonical_forward"],
                "--force-order-progress",
                "docs/LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS_2026-07-12.json",
                "--force-order-continuity",
                report_paths["force_order_transport_continuity"],
                "--microstructure-unblock",
                "docs/MICROSTRUCTURE_UNBLOCK_STATUS_2026-07-03.json",
                "--deribit-audit",
                report_paths["deribit_options_runtime_audit"],
                "--funding-readiness",
                "docs/CEX_FUNDING_RESEARCH_READINESS_2026-07-13.json",
                "--funding-successor-admission",
                report_paths["funding_successor_admission"],
                "--spot-perp-flow-readiness",
                "docs/BINANCE_SPOT_PERP_AGGRESSOR_FLOW_DATA_QUALITY_2026-07-15.json",
                "--spot-perp-flow-snapshot",
                report_paths["spot_perp_flow_snapshot_guard"],
                "--out-prefix",
                report_paths["live_data_focus"][:-5],
            ],
            report_paths["live_data_focus"],
        ),
        (
            "edge_tombstone_registry",
            [
                "tools/edge_tombstone_registry.py",
                "--out-prefix",
                report_paths["tombstone_registry"][:-5],
            ],
            report_paths["tombstone_registry"],
        ),
        (
            "strategy_research_frontier_matrix",
            [
                "tools/strategy_research_frontier_matrix.py",
                "--out-prefix",
                report_paths["strategy_frontier"][:-5],
            ],
            report_paths["strategy_frontier"],
        ),
        (
            "active_observer_runtime_coverage_audit",
            [
                "tools/active_observer_runtime_coverage_audit.py",
                "--frontier",
                report_paths["strategy_frontier"],
                "--deribit-audit",
                report_paths["deribit_options_runtime_audit"],
                "--out-prefix",
                report_paths["active_observer_runtime_coverage"][:-5],
            ],
            report_paths["active_observer_runtime_coverage"],
        ),
        (
            "edge_waiting_board",
            [
                "tools/edge_waiting_board.py",
                "--strategy-frontier",
                report_paths["strategy_frontier"],
                "--tombstone-registry",
                report_paths["tombstone_registry"],
                "--bybit-canonical-forward",
                report_paths["bybit_canonical_forward"],
                "--bybit-canonical-v2-tombstone",
                report_paths["bybit_canonical_v2_tombstone"],
                "--binance-force-order-continuity",
                report_paths["force_order_transport_continuity"],
                "--out-prefix",
                report_paths["edge_waiting_board"][:-5],
            ],
            report_paths["edge_waiting_board"],
        ),
        (
            "real_edge_transition_alert_monitor",
            [
                "tools/real_edge_transition_alert_monitor.py",
                "--tombstone-registry",
                report_paths["tombstone_registry"],
                "--out-prefix",
                report_paths["transition_monitor"][:-5],
            ],
            report_paths["transition_monitor"],
        ),
    ]

    step_reports: list[dict[str, Any]] = []
    for name, command, path in steps:
        result = run_step(name, command, args.timeout_s)
        result["report_path"] = path
        step_reports.append(result)

    failed_steps = [step for step in step_reports if step.get("exit_code") != 0]
    sources = {name: compact_source(path) for name, path in report_paths.items()}
    decision, next_action = classify(failed_steps, sources)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/real_edge_observer_pulse.py",
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
        "orders_allowed": False,
        "steps": step_reports,
        "failed_steps": failed_steps,
        "sources": sources,
        "boundary": {
            "observer_pulse_only": True,
            "emits_trade_signals": False,
            "opens_paper_entries": False,
            "sends_telegram": False,
            "sends_orders": False,
            "can_trade": False,
            "orders_allowed": False,
        },
    }
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "failed_steps": len(failed_steps),
                "transition_decision": sources["transition_monitor"].get("decision"),
                "waiting_board_decision": sources["edge_waiting_board"].get("decision"),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed_steps else 0


if __name__ == "__main__":
    raise SystemExit(main())
