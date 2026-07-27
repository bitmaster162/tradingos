#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def latest(pattern: str, docs_dir: Path) -> Path | None:
    paths = sorted(docs_dir.glob(pattern), key=lambda item: item.stat().st_mtime)
    return paths[-1] if paths else None


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": str(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": str(path)}


def compact_strategy_rows(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in inventory.get("strategies") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "family": item.get("family"),
                "role": item.get("role"),
                "runtime_status": item.get("runtime_status"),
                "observer_status": item.get("observer_status"),
                "scoreboard": item.get("scoreboard"),
                "promotion": item.get("promotion"),
                "strategy_id": item.get("strategy_id"),
            }
        )
    return rows


def compact_tombstones(tombstone: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in tombstone.get("entries") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "family": item.get("family"),
                "status": item.get("status"),
                "reason": item.get("reason"),
                "reuse_rule": item.get("reuse_rule"),
            }
        )
    return rows


def compact_frontier(frontier: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in frontier.get("families") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "family": item.get("family"),
                "status": item.get("status"),
                "decision": item.get("decision"),
                "metrics": item.get("metrics"),
                "path": item.get("path"),
            }
        )
    return rows


def _legacy_build_hermes_prompt(report: dict[str, Any]) -> str:
    blockers = report.get("current_blockers") or []
    no_go = report.get("do_not_repeat") or []
    next_actions = report.get("next_actions") or []
    source_files = report.get("source_of_truth") or {}
    prompt = f"""# Hermes Prompt: Trading Bot Anti-Loop Runtime State Audit

Ты работаешь в торговом проекте. Твоя задача - не строить заново одно и то же, а сначала восстановить фактическое состояние проекта и только потом делать один bounded следующий шаг.

Текущая дата: 2026-06-30.

## Жесткие правила
- Не считай фичу рабочей, если нет runnable proof: команда, exit code, отчет, лог или тест.
- Не считай JSON/config/spec рабочей фичей, если нет реального consumer/runtime.
- Не ретюнь стратегии, которые уже tombstoned или failed OOS/validation.
- Не открывай live/paper trading, пока `can_trade=false` в аудитах.
- Не повторяй старый research batch без materially new preregistered mechanism.
- Не путай data collector, observer, paper executor и live executor.
- Не подглядывай в OOS: если train gate failed, validation/OOS не открывать.
- Сначала прочитай source-of-truth отчеты, потом действуй.

## Source-of-truth файлы, которые надо прочитать первыми
"""
    for name, path in source_files.items():
        prompt += f"- {name}: `{path}`\n"

    prompt += "\n## Текущие блокеры\n"
    for blocker in blockers:
        prompt += f"- `{blocker}`\n"

    prompt += "\n## Что запрещено повторять без новой preregistration\n"
    for item in no_go:
        prompt += f"- {item}\n"

    prompt += "\n## Правильный формат работы\n"
    prompt += """1. Сначала выведи `current-state snapshot`: built/running, partial, planned-only, rejected/tombstoned, waiting-for-data.
2. Сравни код с docs/handoffs и укажи drift.
3. Выбери один bounded next action, который можно доказать командой.
4. Выполни его.
5. Прогони smoke/audit.
6. Запиши артефакты в `docs/`.
7. Если edge не доказан, прямо пиши `can_trade=false`.

## Следующие допустимые действия
"""
    for action in next_actions:
        prompt += f"- {action}\n"

    prompt += """
## Требуемый ответ
Дай:
1. Что реально работает сейчас.
2. Что частично работает.
3. Что только spec/docs.
4. Что уже проверено и отвергнуто.
5. Что еще не изучено/ждет данных.
6. Текущий главный блокер.
7. Один следующий bounded шаг.
8. Команды запуска и результаты.
9. Что изменил в файлах.

Если тебя тянет повторить старую стратегию, сначала проверь tombstone/frontier matrix и объясни, почему это не повтор.
"""
    return prompt


def build_hermes_prompt(report: dict[str, Any]) -> str:
    blockers = report.get("current_blockers") or []
    no_go = report.get("do_not_repeat") or []
    next_actions = report.get("next_actions") or []
    source_files = report.get("source_of_truth") or {}
    lines = [
        "# Hermes Prompt: Trading Bot Anti-Loop Runtime State Audit",
        "",
        "Restore the factual project state before writing code. Do not rebuild an existing component under a new name.",
        "",
        "## Hard Rules",
        "",
        "- A feature is working only with runnable proof: command, exit code, report, log or test.",
        "- A config or specification is not a working feature without a real consumer.",
        "- Do not retune tombstoned or failed OOS/validation strategy families.",
        "- Keep paper/live execution disabled while `can_trade=false`.",
        "- Do not repeat a research batch without a materially new preregistered mechanism.",
        "- Keep collectors, observers, paper executors and live executors distinct.",
        "- Do not open validation/OOS when the preceding gate failed.",
        "- Read the source-of-truth reports before choosing one bounded action.",
        "",
        "## Source Of Truth",
        "",
    ]
    for name, path in source_files.items():
        lines.append(f"- {name}: `{path}`")
    lines.extend(["", "## Current Blockers", ""])
    for blocker in blockers:
        lines.append(f"- `{blocker}`")
    lines.extend(["", "## Do Not Repeat Without New Preregistration", ""])
    for item in no_go:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Required Workflow",
            "",
            "1. Produce a current-state snapshot: running, partial, planned-only, rejected and waiting-for-data.",
            "2. Compare code with docs/handoffs and identify drift.",
            "3. Choose one bounded action with a runnable proof path.",
            "4. Implement it, run smoke/audit, and write durable reports under `docs/`.",
            "5. If the edge is not proven, state `can_trade=false` explicitly.",
            "",
            "## Allowed Next Actions",
            "",
        ]
    )
    for action in next_actions:
        lines.append(f"- {action}")
    lines.extend(
        [
            "",
            "## Required Output",
            "",
            "1. What is runnable now.",
            "2. What is partial.",
            "3. What is docs/spec only.",
            "4. What was tested and rejected.",
            "5. What is waiting for data.",
            "6. The main blocker.",
            "7. One bounded next action.",
            "8. Commands and observed results.",
            "9. Files changed.",
            "",
            "Before repeating an old strategy, inspect the tombstone and frontier reports and explain why the proposed mechanism is materially different.",
            "",
        ]
    )
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# TradingOS Anti-Loop State Map",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Decision",
        "",
        f"- `{report.get('decision')}`",
        f"- Can trade: `{str(report.get('can_trade')).lower()}`",
        "",
        "## Built / Running",
        "",
    ]
    for item in report.get("built_running") or []:
        lines.append(f"- `{item['name']}`: {item['status']} - {item['why']}")
    lines.extend(["", "## Partial / Waiting", ""])
    for item in report.get("partial_waiting") or []:
        lines.append(f"- `{item['name']}`: {item['status']} - {item['blocker']}")
    lines.extend(["", "## Studied / Rejected / Do Not Retune", ""])
    for item in report.get("do_not_repeat") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Current Blockers", ""])
    for blocker in report.get("current_blockers") or []:
        lines.append(f"- `{blocker}`")
    lines.extend(["", "## Studied Inputs", ""])
    studied = report.get("studied_inputs") or {}
    for key, value in studied.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Not Done / Needs Data", ""])
    for item in report.get("not_done") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Next Actions", ""])
    for action in report.get("next_actions") or []:
        lines.append(f"- {action}")
    lines.extend(["", "## Hermes Prompt", ""])
    lines.append(f"- Prompt file: `{report.get('hermes_prompt_path')}`")
    lines.extend(["", "## Source Of Truth", ""])
    for name, path in (report.get("source_of_truth") or {}).items():
        lines.append(f"- `{name}`: `{path}`")
    lines.append("")
    return "\n".join(lines)


def build_report(docs_dir: Path) -> dict[str, Any]:
    runtime_root = docs_dir.parent if docs_dir.name.lower() == "docs" else docs_dir
    runtime_shutdown_path = runtime_root / "logs" / "runtime_shutdown.request.json"
    if docs_dir.name.lower() != "docs":
        runtime_shutdown_path = docs_dir / "runtime_shutdown.request.json"
    paths = {
        "full_system_devil_audit": latest("FULL_SYSTEM_DEVIL_AUDIT_2026-06-30_POST_LIQUIDATION_LOOP_AUTOSTART_SYNCED.json", docs_dir)
        or latest("FULL_SYSTEM_DEVIL_AUDIT_*.json", docs_dir),
        "unified_readiness_matrix": latest("UNIFIED_READINESS_MATRIX_2026-06-30*.json", docs_dir),
        "readiness_pulse": latest("TRADINGOS_READINESS_PULSE_2026-06-30*.json", docs_dir),
        "active_strategy_inventory": latest("ACTIVE_STRATEGY_RUNTIME_INVENTORY_2026-06-30*.json", docs_dir),
        "strategy_frontier": latest("STRATEGY_RESEARCH_FRONTIER_MATRIX_*.json", docs_dir),
        "edge_tombstone_registry": latest("EDGE_TOMBSTONE_REGISTRY_*.json", docs_dir),
        "workspace_curation": latest("WORKSPACE_CURATION_*.json", docs_dir),
        "downloads_scan": latest("DOWNLOADS_TRADING_CANDIDATE_SCAN_*.json", docs_dir),
        "crypto_guides_ingest": latest("CRYPTO_GUIDES_WEB_INGEST_*.json", docs_dir),
        "document_rule_forward_scoreboard": latest("DOCUMENT_RULE_FORWARD_SCOREBOARD_*.json", docs_dir),
        "liquidation_data_quality": latest("LIQUIDATION_FORCE_ORDER_DATA_QUALITY_*.json", docs_dir),
        "liquidation_preregistered_progress": latest("LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS_*.json", docs_dir),
        "liquidation_force_order_transport_continuity": latest(
            "LIQUIDATION_FORCE_ORDER_TRANSPORT_CONTINUITY_*.json", docs_dir
        ),
        "liquidation_arrival_time_readiness": latest("LIQUIDATION_CROSS_VENUE_ARRIVAL_TIME_READINESS_2026-07-13.json", docs_dir),
        "liquidation_cross_venue_receipt_leadership": latest(
            "LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_FORWARD_OBSERVER_V*.json",
            docs_dir,
        ),
        "bybit_liquidation_canonical_v2_tombstone": latest("BYBIT_LIQUIDATION_CANONICAL_FORWARD_V2_DESIGN_TOMBSTONE_2026-07-13.json", docs_dir),
        "bybit_liquidation_canonical_v3_clock_tombstone": latest("BYBIT_LIQUIDATION_CANONICAL_V3_CLOCK_TOMBSTONE_2026-07-14.json", docs_dir),
        "bybit_liquidation_canonical_v4_commissioning": latest("BYBIT_LIQUIDATION_CANONICAL_V4_COMMISSIONING_2026-07-14.json", docs_dir),
        "bybit_liquidation_canonical_v4_packet_tombstone": latest("BYBIT_LIQUIDATION_CANONICAL_V4_PACKET_IDENTITY_TOMBSTONE_2026-07-15.json", docs_dir),
        "bybit_liquidation_canonical_v5_commissioning": latest("BYBIT_LIQUIDATION_CANONICAL_V5R1_COMMISSIONING_2026-07-15.json", docs_dir),
        "bybit_liquidation_canonical_v5_source_tombstone": latest("BYBIT_LIQUIDATION_CANONICAL_V5_SOURCE_COMPAT_TOMBSTONE_2026-07-15.json", docs_dir),
        "bybit_liquidation_canonical_forward": latest("BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V5R2_*.json", docs_dir),
        "microstructure_readiness": latest("CROSS_VENUE_MICROSTRUCTURE_READINESS_PROGRESS_2026-06-30*.json", docs_dir),
        "microstructure_unblock_status": latest("MICROSTRUCTURE_UNBLOCK_STATUS_2026-07-03.json", docs_dir),
        "liquidation_book_independence": latest("LIQUIDATION_BOOK_REPLENISHMENT_INDEPENDENCE_GATE_*.json", docs_dir),
        "exogenous_liquidity_regime": latest("EXOGENOUS_LIQUIDITY_REGIME_FORWARD_OBSERVER_*.json", docs_dir),
        "cex_dex_funding_data_quality": latest("CEX_DEX_FUNDING_LEAD_LAG_DATA_QUALITY_*.json", docs_dir),
        "cex_funding_direct_replication": latest("CEX_FUNDING_DIRECT_REPLICATION_DATA_QUALITY_*.json", docs_dir),
        "cex_funding_source_alignment_v2_tombstone": latest("CEX_FUNDING_SOURCE_ALIGNMENT_V2_TERMINAL_TOMBSTONE_2026-07-14.json", docs_dir),
        "cex_funding_source_alignment": latest("CEX_FUNDING_SOURCE_ALIGNMENT_V3_2026-07-14.json", docs_dir),
        "cex_funding_research_readiness": latest("CEX_FUNDING_RESEARCH_READINESS_2026-07-13.json", docs_dir),
        "cex_funding_successor_admission": latest("CEX_FUNDING_SUCCESSOR_ADMISSION_*.json", docs_dir),
        "cex_funding_freshness_watchdog": latest("CEX_FUNDING_FRESHNESS_WATCHDOG_*.json", docs_dir),
        "cex_funding_freshness_incident_alert": latest("CEX_FUNDING_FRESHNESS_INCIDENT_ALERT_2026-07-13.json", docs_dir),
        "deribit_options_runtime_audit": latest("DERIBIT_OPTIONS_RESEARCH_RUNTIME_AUDIT_2026-07-13.json", docs_dir),
        "deribit_options_v3_runtime_audit": latest("DERIBIT_OPTIONS_V3_DATA_LAYER_AUDIT_*.json", docs_dir),
        "binance_spot_perp_aggressor_flow": latest("BINANCE_SPOT_PERP_AGGRESSOR_FLOW_DATA_QUALITY_*.json", docs_dir),
        "binance_spot_perp_aggressor_flow_snapshot": latest("BINANCE_SPOT_PERP_AGGRESSOR_FLOW_SNAPSHOT_GUARD_*.json", docs_dir),
        "active_observer_runtime_coverage": latest("ACTIVE_OBSERVER_RUNTIME_COVERAGE_2026-07-13.json", docs_dir),
        "bitunix_wo104_parallel_status": latest("BITUNIX_WO104_STATUS_2026-07-13.json", docs_dir),
        "bitunix_wo105_v1_status": latest("BITUNIX_WO105_STATUS_2026-07-14.json", docs_dir),
        "bitunix_wo105_v1_tombstone": latest("BITUNIX_WO105_V1_PRE_FLOOR_UNIT_TOMBSTONE_2026-07-14.json", docs_dir),
        "bitunix_wo105_v2_tombstone": latest("BITUNIX_WO105_V2_PRE_FLOOR_RUNTIME_TOMBSTONE_2026-07-14.json", docs_dir),
        "bitunix_wo105_v3_tombstone": latest("BITUNIX_WO105_V3_FIRST_CYCLE_OPERATIONAL_TOMBSTONE_2026-07-14.json", docs_dir),
        "bitunix_wo105_v3r1_clock_tombstone": latest("BITUNIX_WO105_V3R1_CLOCK_CONTRACT_TOMBSTONE_2026-07-14.json", docs_dir),
        "bitunix_wo105_v3r2_tombstone": latest("BITUNIX_WO105_V3R2_FIRST_CYCLE_OPERATIONAL_TOMBSTONE_2026-07-14.json", docs_dir),
        "bitunix_wo105_v3r3_tombstone": latest("BITUNIX_WO105_V3R3_RECEIPT_ORDER_TOMBSTONE_2026-07-15.json", docs_dir),
        "bitunix_wo105_v3r4_status": latest("BITUNIX_WO105_V3R4_STATUS_2026-07-15.json", docs_dir),
        "bitunix_wo105_v3r4_blind_review_gate": latest("BITUNIX_WO105_V3R4_BLIND_REVIEW_GATE_2026-07-15.json", docs_dir),
        "bitunix_wo105_v3r4_first_cycle_gate": latest("BITUNIX_WO105_V3R4_FIRST_CYCLE_GATE_2026-07-15.json", docs_dir),
        "bitunix_bar_finality_audit": latest("MAIN_EDGE_SEARCH_PASS_*BITUNIX_BAR_FINALITY_AUDIT*.json", docs_dir),
        "bitunix_raw_event_replenishment": latest("MAIN_EDGE_SEARCH_PASS_*BITUNIX_RAW_EVENT_V106.json", docs_dir),
        "bitunix_raw_event_forward_intake": latest("MAIN_EDGE_SEARCH_PASS_*BITUNIX_RAW_EVENT_FORWARD_INTAKE_V107.json", docs_dir),
        "google_doc_wo004_intake": latest("GOOGLE_DOC_INTAKE_*TRADINGOS_WO004_OFFLINE_REGRESSION_ORACLE.json", docs_dir),
        "bitunix_wo108_evidence_delivery": latest("BITUNIX_WO108_EVIDENCE_DELIVERY_2026-07-14.json", docs_dir),
        "real_edge_observer_pulse": latest("REAL_EDGE_OBSERVER_PULSE_*.json", docs_dir),
        "post_fill_markout": latest("POST_FILL_MARKOUT_PROOF_*.json", docs_dir),
        "post_fill_markout_forward": latest("POST_FILL_MARKOUT_FORWARD_PROOF_*.json", docs_dir),
    }
    loaded = {name: read_json(path) for name, path in paths.items()}
    readiness = loaded["unified_readiness_matrix"]
    inventory = loaded["active_strategy_inventory"]
    tombstone = loaded["edge_tombstone_registry"]
    frontier = loaded["strategy_frontier"]
    curation = loaded["workspace_curation"]
    downloads = loaded["downloads_scan"]
    crypto_guides = loaded["crypto_guides_ingest"]
    liquidation = loaded["liquidation_data_quality"]
    liquidation_progress = loaded["liquidation_preregistered_progress"]
    liquidation_transport = loaded["liquidation_force_order_transport_continuity"]
    liquidation_arrival = loaded["liquidation_arrival_time_readiness"]
    liquidation_leadership = loaded["liquidation_cross_venue_receipt_leadership"]
    bybit_canonical_forward = loaded["bybit_liquidation_canonical_forward"]
    bybit_canonical_v2_tombstone = loaded["bybit_liquidation_canonical_v2_tombstone"]
    bybit_canonical_v3_clock_tombstone = loaded["bybit_liquidation_canonical_v3_clock_tombstone"]
    bybit_canonical_v4_commissioning = loaded["bybit_liquidation_canonical_v4_commissioning"]
    bybit_canonical_v4_packet_tombstone = loaded["bybit_liquidation_canonical_v4_packet_tombstone"]
    bybit_canonical_v5_commissioning = loaded["bybit_liquidation_canonical_v5_commissioning"]
    bybit_canonical_v5_source_tombstone = loaded["bybit_liquidation_canonical_v5_source_tombstone"]
    micro = loaded["microstructure_readiness"]
    micro_unblock = loaded["microstructure_unblock_status"]
    doc_rule = loaded["document_rule_forward_scoreboard"]
    liquidation_book = loaded["liquidation_book_independence"]
    exogenous = loaded["exogenous_liquidity_regime"]
    cex_dex_funding = loaded["cex_dex_funding_data_quality"]
    direct_funding = loaded["cex_funding_direct_replication"]
    funding_alignment_v2_tombstone = loaded["cex_funding_source_alignment_v2_tombstone"]
    funding_alignment = loaded["cex_funding_source_alignment"]
    funding_readiness = loaded["cex_funding_research_readiness"]
    funding_successor_admission = loaded["cex_funding_successor_admission"]
    funding_watchdog = loaded["cex_funding_freshness_watchdog"]
    funding_incident_alert = loaded["cex_funding_freshness_incident_alert"]
    deribit_options = loaded["deribit_options_runtime_audit"]
    deribit_options_v3 = loaded["deribit_options_v3_runtime_audit"]
    deribit_v3_state = deribit_options_v3.get("v3") if isinstance(deribit_options_v3.get("v3"), dict) else {}
    deribit_v3_metrics = (
        deribit_v3_state.get("readiness_metrics")
        if isinstance(deribit_v3_state.get("readiness_metrics"), dict)
        else {}
    )
    spot_perp_flow = loaded["binance_spot_perp_aggressor_flow"]
    spot_perp_flow_snapshot = loaded["binance_spot_perp_aggressor_flow_snapshot"]
    observer_coverage = loaded["active_observer_runtime_coverage"]
    bitunix_wo104 = loaded["bitunix_wo104_parallel_status"]
    bitunix_wo105_v1 = loaded["bitunix_wo105_v1_status"]
    bitunix_wo105_v1_tombstone = loaded["bitunix_wo105_v1_tombstone"]
    bitunix_wo105_v2_tombstone = loaded["bitunix_wo105_v2_tombstone"]
    bitunix_wo105_v3_tombstone = loaded["bitunix_wo105_v3_tombstone"]
    bitunix_wo105_v3r1_tombstone = loaded["bitunix_wo105_v3r1_clock_tombstone"]
    bitunix_wo105_v3r2_tombstone = loaded["bitunix_wo105_v3r2_tombstone"]
    bitunix_wo105_v3r3_tombstone = loaded["bitunix_wo105_v3r3_tombstone"]
    bitunix_wo105_v3r4 = loaded["bitunix_wo105_v3r4_status"]
    bitunix_wo105_v3r4_blind_gate = loaded["bitunix_wo105_v3r4_blind_review_gate"]
    bitunix_wo105_v3r4_first_cycle_gate = loaded["bitunix_wo105_v3r4_first_cycle_gate"]
    bitunix_bar_finality = loaded["bitunix_bar_finality_audit"]
    bitunix_raw_event = loaded["bitunix_raw_event_replenishment"]
    bitunix_raw_event_intake = loaded["bitunix_raw_event_forward_intake"]
    google_doc_wo004 = loaded["google_doc_wo004_intake"]
    bitunix_wo108_delivery = loaded["bitunix_wo108_evidence_delivery"]
    observer_pulse = loaded["real_edge_observer_pulse"]
    post_fill_markout = loaded["post_fill_markout"]
    post_fill_markout_forward = loaded["post_fill_markout_forward"]
    runtime_shutdown = read_json(runtime_shutdown_path)

    blockers: list[str] = []
    if runtime_shutdown:
        blockers.append("managed_runtime_operator_stopped")
    micro_snapshot_ready = bool(micro_unblock.get("snapshot_id")) or micro_unblock.get("decision") == "microstructure_snapshot_available"
    if micro_unblock:
        micro_waiting = not micro_snapshot_ready
    else:
        micro_waiting = "ready" not in str(micro.get("decision") or "")
    if micro_waiting:
        blockers.append("waiting_for_sealed_microstructure_snapshot")
    if liquidation_progress and liquidation_progress.get("ready_for_pipeline") is not True:
        blockers.append("liquidation_force_order_locked_sample_waiting_gates")
    elif "collecting_insufficient_sample" in str(liquidation.get("decision") or ""):
        blockers.append("liquidation_force_order_sample_not_review_ready")
    elif (
        liquidation_progress.get("ready_for_pipeline") is True
        and liquidation_transport.get("continuity_observed") is not True
    ):
        blockers.append("liquidation_force_order_transport_continuity_not_observed")
    if "collecting" in str(liquidation_arrival.get("decision") or ""):
        blockers.append("liquidation_cross_venue_arrival_time_waiting_overlap")
    if "waiting" in str(liquidation_leadership.get("decision") or "") or "collecting" in str(
        liquidation_leadership.get("decision") or ""
    ):
        blockers.append("liquidation_cross_venue_receipt_leadership_waiting_forward_sample")
    if "waiting" in str(bybit_canonical_forward.get("decision") or "") or "collecting" in str(
        bybit_canonical_forward.get("decision") or ""
    ):
        blockers.append("bybit_canonical_liquidation_reversal_waiting_forward_sample")
    if not bitunix_bar_finality and int(bitunix_wo105_v3r4.get("terminal_forward_events") or 0) < int(
        bitunix_wo105_v3r4.get("minimum_terminal_forward_events") or 30
    ):
        blockers.append("bitunix_wo105_v3r4_causal_shadow_waiting_forward_sample")
    if bitunix_wo105_v3r4_first_cycle_gate.get("decision") in {
        "bitunix_wo105_v3_first_cycle_operational_blocked",
        "bitunix_wo105_v3_first_cycle_hold_integrity_or_boundary_invalid",
    }:
        blockers.append("bitunix_wo105_v3r4_first_cycle_operational_blocked")
    if bitunix_bar_finality.get("decision") == (
        "bitunix_public_bar_sources_not_admissible_for_frozen_five_second_close_contract"
    ):
        blockers.append("bitunix_v3r4_exact_bar_source_terminal")
    if bitunix_raw_event and (bitunix_raw_event.get("terminal_gate") or {}).get("ready") is not True:
        blockers.append("bitunix_raw_event_replenishment_waiting_prospective_sample")
    if "collecting" in str(liquidation_book.get("decision") or ""):
        blockers.append("liquidation_book_independence_waiting_forward_sample")
    if "waiting" in str(exogenous.get("decision") or "") or "collecting" in str(exogenous.get("decision") or ""):
        blockers.append("exogenous_liquidity_waiting_forward_macro_dates")
    funding_sample = cex_dex_funding.get("sample") if isinstance(cex_dex_funding.get("sample"), dict) else {}
    funding_gate = cex_dex_funding.get("research_gate") if isinstance(cex_dex_funding.get("research_gate"), dict) else {}
    funding_alignment_state = (
        funding_readiness.get("alignment") if isinstance(funding_readiness.get("alignment"), dict) else {}
    )
    funding_stages = funding_readiness.get("stages") if isinstance(funding_readiness.get("stages"), dict) else {}
    funding_terminal = funding_alignment_state.get("terminal") is True
    funding_successor_eligible = funding_successor_admission.get("eligible_for_manual_successor_lock_review") is True
    funding_readiness_blockers = list(funding_readiness.get("contract_failures") or []) + list(
        funding_readiness.get("operational_blockers") or []
    )
    if funding_terminal:
        funding_readiness_blockers.append(
            "successor_manual_lock_review_required"
            if funding_successor_admission and funding_successor_eligible
            else "successor_admission_waiting_clean_window"
            if funding_successor_admission
            else "alignment_terminal_failure"
        )
    elif funding_stages.get("primary_observer_creation_gate_ready") is not True:
        funding_readiness_blockers.append("primary_forward_gate_not_ready")
    if funding_readiness:
        if funding_terminal:
            blockers.append(
                "cex_funding_successor_manual_lock_review_required"
                if funding_successor_admission and funding_successor_eligible
                else "cex_funding_successor_admission_waiting_clean_window"
                if funding_successor_admission
                else "cex_funding_source_alignment_terminal_failure"
            )
        elif funding_stages.get("primary_observer_creation_gate_ready") is not True:
            blockers.append("cex_funding_research_readiness_waiting_forward_gate")
    elif cex_dex_funding and int(funding_sample.get("unique_minute_buckets") or 0) < int(funding_gate.get("minimum_unique_minute_snapshots") or 0):
        blockers.append("cex_dex_funding_lead_lag_waiting_forward_span")
    if funding_watchdog and funding_watchdog.get("healthy") is not True:
        blockers.append("cex_funding_freshness_watchdog_blocked")
    if funding_incident_alert and int(funding_incident_alert.get("pending_notifications") or 0) > 0:
        blockers.append("cex_funding_incident_alert_delivery_pending")
    if deribit_options_v3 and deribit_v3_state.get("readiness_gate_ready") is not True:
        blockers.append("deribit_options_v3_waiting_locked_readiness_gate")
    elif deribit_options and (deribit_options.get("forward_progress") or {}).get("readiness_gate_ready") is not True:
        blockers.append("deribit_options_waiting_locked_readiness_gate")
    spot_perp_flow_readiness = (
        spot_perp_flow.get("research_readiness")
        if isinstance(spot_perp_flow.get("research_readiness"), dict)
        else {}
    )
    spot_perp_snapshot_decision = str(spot_perp_flow_snapshot.get("decision") or "")
    spot_perp_snapshot_sealed = (
        spot_perp_flow_snapshot.get("sealed") is True
        and bool(spot_perp_flow_snapshot.get("snapshot_id"))
        and spot_perp_snapshot_decision
        in {
            "spot_perp_flow_snapshot_sealed",
            "spot_perp_flow_snapshot_already_sealed_verified",
        }
    )
    if spot_perp_flow and not spot_perp_snapshot_sealed:
        blockers.append("binance_spot_perp_aggressor_flow_waiting_forward_data_gate")
    if not observer_coverage:
        blockers.append("active_observer_runtime_coverage_report_missing")
    elif observer_coverage.get("decision") != "active_observer_runtime_coverage_pass":
        blockers.append("active_observer_runtime_coverage_blocked")
    elif observer_coverage.get("can_trade") is not False:
        blockers.append("active_observer_runtime_coverage_trade_lock_violation")
    if int((frontier.get("summary") or {}).get("promotable") or 0) == 0:
        blockers.append("no_promotable_family")
    if post_fill_markout_forward:
        forward_snapshot = post_fill_markout_forward.get("current_forward") or {}
        if forward_snapshot.get("decision") != "markout_distribution_ready_for_manual_review":
            blockers.append("post_fill_markout_forward_waiting_evidence")
    elif post_fill_markout and int((post_fill_markout.get("current_smoke") or {}).get("raw_fill_count") or 0) < 1:
        blockers.append("post_fill_markout_waiting_authoritative_fills")
    liquidation_events = liquidation.get("events") if isinstance(liquidation.get("events"), dict) else {}
    prereg_liquidation = (
        liquidation_events.get("preregistered_sample")
        if isinstance(liquidation_events.get("preregistered_sample"), dict)
        else {}
    )
    built_running = [
        {
            "name": "managed_runtime",
            "status": "operator_stopped" if runtime_shutdown else "no_shutdown_sentinel",
            "why": (
                f"requested_at={runtime_shutdown.get('ts')}; "
                f"requested_by={runtime_shutdown.get('requested_by')}; "
                "automatic_resume=false; can_trade=false"
                if runtime_shutdown
                else "explicit shutdown sentinel absent; process liveness is proved separately by runtime coverage"
            ),
        },
        {
            "name": "web_control_panel",
            "status": "running/safe_mode",
            "why": "local panel exposes safe tasks; live trading locked",
        },
        {
            "name": "forward_observer_stack",
            "status": frontier.get("decision") or inventory.get("decision") or "observer inventory present",
            "why": f"{(frontier.get('summary') or {}).get('observer_only', '?')} active observer-only families; paper/live disabled",
        },
        {
            "name": "liquidation_force_order_collector",
            "status": liquidation.get("decision") or "unknown",
            "why": f"events={liquidation_events.get('events')}; preregistered={prereg_liquidation.get('events')}; hard_failures={(liquidation.get('hard_failures') or [])}",
        },
        {
            "name": "liquidation_force_order_preregistered_progress",
            "status": liquidation_progress.get("decision") or "missing",
            "why": f"events={(liquidation_progress.get('sample') or {}).get('events')}; raw_blocks={(liquidation_progress.get('sample') or {}).get('independent_4h_blocks')}; matured_blocks={(liquidation_progress.get('sample') or {}).get('matured_independent_4h_blocks')}; earliest={(liquidation_progress.get('velocity') or {}).get('theoretical_earliest_pipeline_at')}",
        },
        {
            "name": "liquidation_force_order_transport_continuity",
            "status": liquidation_transport.get("decision") or "missing",
            "why": f"continuity_observed={liquidation_transport.get('continuity_observed')}; blockers={liquidation_transport.get('blockers') or []}",
        },
        {
            "name": "liquidation_cross_venue_arrival_time_readiness",
            "status": liquidation_arrival.get("decision") or "missing",
            "why": f"overlap_seconds={((liquidation_arrival.get('cross_venue') or {}).get('overlapping_receipt_span_seconds'))}; shared_symbols={((liquidation_arrival.get('cross_venue') or {}).get('shared_symbol_count'))}; blockers={liquidation_arrival.get('blockers')}; leadership_observer_built=true",
        },
        {
            "name": "liquidation_cross_venue_receipt_leadership_observer",
            "status": liquidation_leadership.get("decision") or "missing",
            "why": (
                f"primary_pairs={((liquidation_leadership.get('primary_sample') or {}).get('matched_pairs'))}; "
                f"binance_share={((((liquidation_leadership.get('windows_seconds') or {}).get('5') or {}).get('leader') or {}).get('binance') or {}).get('leader_share')}; "
                f"bybit_share={((((liquidation_leadership.get('windows_seconds') or {}).get('5') or {}).get('leader') or {}).get('bybit') or {}).get('leader_share')}; "
                f"terminal={((liquidation_leadership.get('terminal') or {}).get('reached'))}; price_outcomes_read=false; can_trade=false"
            ),
        },
        {
            "name": "bybit_liquidation_canonical_reversal_v2_tombstone",
            "status": bybit_canonical_v2_tombstone.get("decision") or "missing",
            "why": "open-exit-bar design risk proven pre-floor; resolved=0; outcomes admitted to V3=false; can_trade=false",
        },
        {
            "name": "bybit_liquidation_canonical_reversal_v3_clock_tombstone",
            "status": bybit_canonical_v3_clock_tombstone.get("decision") or "missing",
            "why": (
                f"post_floor_events={((bybit_canonical_v3_clock_tombstone.get('evidence') or {}).get('post_floor_events'))}; "
                f"negative_raw_lag_pct={((bybit_canonical_v3_clock_tombstone.get('evidence') or {}).get('post_floor_negative_raw_receipt_lag_pct'))}; "
                "outcomes_computed=false; rows_not_rewritten=true; admitted_to_v4=false; can_trade=false"
            ),
        },
        {
            "name": "bybit_liquidation_canonical_reversal_v4_packet_tombstone",
            "status": bybit_canonical_v4_packet_tombstone.get("decision") or "missing",
            "why": (
                f"outcomes_computed={((bybit_canonical_v4_packet_tombstone.get('outcome_boundary') or {}).get('outcome_fields_computed_at_failure'))}; "
                "market_tuple_not_unique=true; raw_rows_mutated=false; admitted_to_v5=false; can_trade=false"
            ),
        },
        {
            "name": "bybit_liquidation_canonical_reversal_v5_source_tombstone",
            "status": bybit_canonical_v5_source_tombstone.get("decision") or "missing",
            "why": (
                f"official_floor_reached={((bybit_canonical_v5_source_tombstone.get('outcome_boundary') or {}).get('official_forward_floor_reached'))}; "
                "rows_admitted=false; outcomes=false; source_contract_fixed_in_v5r1=true; can_trade=false"
            ),
        },
        {
            "name": "bybit_liquidation_canonical_reversal_v5r1_observer",
            "status": bybit_canonical_forward.get("decision") or "missing",
            "why": (
                f"floor={((bybit_canonical_forward.get('lock') or {}).get('forward_start_at'))}; "
                f"resolved={((bybit_canonical_forward.get('sample') or {}).get('resolved_events'))}; "
                f"blocks4h={((bybit_canonical_forward.get('sample') or {}).get('independent_4h_blocks'))}; "
                f"outcomes_hidden={((bybit_canonical_forward.get('outcome_review') or {}).get('interim_outcomes_hidden'))}; "
                "legacy_v1_v2_v3_v4_outcomes_admitted=false; calibrated_receipts=true; packet_ordinals=true; packet_atomic_write=true; fully_closed_bars_only=true; can_trade=false"
            ),
        },
        {
            "name": "bybit_liquidation_canonical_v5r1_pre_floor_commissioning",
            "status": bybit_canonical_v5_commissioning.get("decision") or "missing",
            "why": (
                f"schema4_rows={((bybit_canonical_v5_commissioning.get('diagnostic_window') or {}).get('schema_v4_rows'))}; "
                f"packets={((bybit_canonical_v5_commissioning.get('diagnostic_window') or {}).get('packets'))}; "
                f"duplicate_packet_items={((bybit_canonical_v5_commissioning.get('diagnostic_window') or {}).get('duplicate_packet_item_identities'))}; "
                "sample_admission=false; outcomes=false; one_time_pre_floor_only=true; can_trade=false"
            ),
        },
        {
            "name": "bybit_liquidation_canonical_v4_pre_floor_commissioning",
            "status": bybit_canonical_v4_commissioning.get("decision") or "missing",
            "why": (
                f"schema3_rows={((bybit_canonical_v4_commissioning.get('commissioning_window') or {}).get('schema3_rows'))}; "
                f"sessions={((bybit_canonical_v4_commissioning.get('commissioning_window') or {}).get('collector_sessions'))}; "
                f"packets={((bybit_canonical_v4_commissioning.get('commissioning_window') or {}).get('unique_packets'))}; "
                f"hard_failures={bybit_canonical_v4_commissioning.get('hard_failures')}; "
                "sample_admission=false; outcomes=false; one_time_pre_floor_only=true; can_trade=false"
            ),
        },
        {
            "name": "microstructure_collector",
            "status": micro_unblock.get("decision") or micro.get("decision") or "readiness report present",
            "why": (
                f"span_hours={((micro_unblock.get('coverage') or {}).get('span_hours'))}; "
                f"book_coverage={((micro_unblock.get('coverage') or {}).get('book_coverage_pct'))}; "
                f"recent_6h_book_coverage={((micro_unblock.get('book_diagnostic') or {}).get('recent_6h_dual_book_pct'))}; "
                f"eta={((micro_unblock.get('book_diagnostic') or {}).get('eta_utc'))}; "
                f"snapshot_id={micro_unblock.get('snapshot_id')}"
                if micro_unblock
                else f"span_hours={micro.get('span_hours')}; remaining_hours={micro.get('remaining_hours')}"
            ),
        },
        {
            "name": "document_rule_forward_scoreboard",
            "status": doc_rule.get("decision") or "unknown",
            "why": f"resolved={(doc_rule.get('summary') or {}).get('resolved')}; can_trade={doc_rule.get('can_trade')}",
        },
        {
            "name": "liquidation_book_independence_gate",
            "status": liquidation_book.get("decision") or "missing",
            "why": f"candidate_events={(liquidation_book.get('evidence') or {}).get('candidate_events')}; independent_blocks={(liquidation_book.get('evidence') or {}).get('independent_blocks')}",
        },
        {
            "name": "exogenous_liquidity_regime_observer",
            "status": exogenous.get("decision") or "missing",
            "why": f"events={(exogenous.get('sample') or {}).get('events_total')}; stable_dates={((exogenous.get('source_readiness') or {}).get('stablecoin') or {}).get('metrics', {}).get('new_unique_source_dates')}; macro_dates={((exogenous.get('source_readiness') or {}).get('macro') or {}).get('metrics', {}).get('new_unique_weekly_dates')}",
        },
        {
            "name": "cex_dex_funding_lead_lag_collector",
            "status": cex_dex_funding.get("decision") or "missing",
            "why": f"snapshots={funding_sample.get('unique_minute_buckets')}; span_minutes={funding_sample.get('span_minutes')}; coverage={funding_sample.get('required_point_coverage')}; observer_not_built=true",
        },
        {
            "name": "cex_funding_direct_replication_collector",
            "status": direct_funding.get("decision") or "missing",
            "why": f"snapshots={((direct_funding.get('sample') or {}).get('unique_minute_buckets'))}; span_minutes={((direct_funding.get('sample') or {}).get('span_minutes'))}; coverage={((direct_funding.get('sample') or {}).get('required_point_coverage'))}; evaluator_not_built=true",
        },
        *(
            [
                {
                    "name": "cex_funding_source_alignment_v2_tombstone",
                    "status": funding_alignment_v2_tombstone.get("status") or "missing",
                    "why": (
                        f"max_gap_minutes={((funding_alignment_v2_tombstone.get('sample') or {}).get('maximum_consecutive_gap_minutes'))}; "
                        f"history_rewritten={((funding_alignment_v2_tombstone.get('disposition') or {}).get('history_rewritten'))}; "
                        f"retuned={((funding_alignment_v2_tombstone.get('disposition') or {}).get('retuned'))}; "
                        f"superseded_by={((funding_alignment_v2_tombstone.get('superseded_by') or {}).get('lock_id'))}; can_trade=false"
                    ),
                }
            ]
            if funding_alignment_v2_tombstone
            else []
        ),
        {
            "name": "cex_funding_source_alignment_monitor",
            "status": funding_alignment.get("decision") or "missing",
            "why": f"matching_buckets={((funding_alignment.get('sample') or {}).get('matching_minute_buckets'))}; time_coverage={((funding_alignment.get('sample') or {}).get('matching_time_coverage'))}; max_gap_minutes={((funding_alignment.get('sample') or {}).get('maximum_consecutive_gap_minutes'))}; comparisons={((funding_alignment.get('sample') or {}).get('valid_comparisons'))}; comparison_coverage={((funding_alignment.get('sample') or {}).get('comparison_coverage'))}; edge_evaluated={funding_alignment.get('edge_evaluated')}",
        },
        {
            "name": "cex_funding_research_readiness_monitor",
            "status": funding_readiness.get("decision") or "missing",
            "why": (
                f"primary={((funding_readiness.get('primary_progress') or {}).get('current'))}; "
                f"direct={((funding_readiness.get('direct_progress') or {}).get('current'))}; "
                f"primary_eta={((funding_readiness.get('primary_progress') or {}).get('theoretical_earliest_utc'))}; "
                f"observer_review={((funding_readiness.get('stages') or {}).get('observer_creation_review_allowed'))}; "
                f"edge_evaluated={((funding_readiness.get('stages') or {}).get('edge_evaluated'))}"
            ),
        },
        *(
            [
                {
                    "name": "cex_funding_successor_admission_gate",
                    "status": funding_successor_admission.get("decision") or "missing",
                    "why": (
                        f"eligible={funding_successor_eligible}; "
                        f"matching_minutes={((funding_successor_admission.get('rolling_alignment') or {}).get('sample') or {}).get('matching_minute_buckets')}; "
                        f"time_coverage={((funding_successor_admission.get('rolling_alignment') or {}).get('sample') or {}).get('matching_time_coverage')}; "
                        f"max_gap_minutes={((funding_successor_admission.get('rolling_alignment') or {}).get('sample') or {}).get('maximum_consecutive_gap_minutes')}; "
                        f"recheck={((funding_successor_admission.get('diagnostic_window') or {}).get('earliest_recheck_at_utc'))}; "
                        "successor_created=false; can_trade=false"
                    ),
                }
            ]
            if funding_successor_admission
            else []
        ),
        {
            "name": "cex_funding_freshness_watchdog",
            "status": funding_watchdog.get("decision") or "missing",
            "why": f"healthy={funding_watchdog.get('healthy')}; blockers={funding_watchdog.get('blockers')}; automatic_restart={funding_watchdog.get('automatic_restart_attempted')}; edge_evaluated={funding_watchdog.get('edge_evaluated')}",
        },
        {
            "name": "cex_funding_freshness_transition_alert",
            "status": funding_incident_alert.get("decision") or "missing",
            "why": f"transition={funding_incident_alert.get('transition_kind')}; pending={funding_incident_alert.get('pending_notifications')}; telegram_ok={funding_incident_alert.get('telegram_response_ok')}; trade_signal={funding_incident_alert.get('trade_signal')}",
        },
        (
            {
                "name": "deribit_options_v3_data_layer",
                "status": deribit_options_v3.get("decision") or "missing",
                "why": (
                    f"floor={deribit_v3_state.get('forward_floor_utc')}; "
                    f"collector_processes={deribit_v3_state.get('collector_logical_processes')}; "
                    f"readiness_processes={deribit_v3_state.get('readiness_logical_processes')}; "
                    f"span_days={deribit_v3_metrics.get('span_days')}/7.0; "
                    f"healthy_slots={deribit_v3_metrics.get('healthy_slots')}/1800; "
                    f"scheduled_coverage={deribit_v3_metrics.get('scheduled_coverage')}/0.95; "
                    "predecessor_rows=false; observer_successor=false; can_trade=false"
                ),
            }
            if deribit_options_v3
            else {
                "name": "deribit_options_skew_forward_stack",
                "status": deribit_options.get("decision") or "missing",
                "why": (
                    f"runtime_ok={((deribit_options.get('runtime') or {}).get('all_components_passed'))}; "
                    f"span_days={((deribit_options.get('forward_progress') or {}).get('span_days'))}/7.0; "
                    f"healthy_slots={((deribit_options.get('forward_progress') or {}).get('healthy_slots'))}/1800; "
                    f"scheduled_coverage={((deribit_options.get('forward_progress') or {}).get('scheduled_coverage'))}/0.95; "
                    f"events={((deribit_options.get('forward_progress') or {}).get('events_total'))}; retuning=false"
                ),
            }
        ),
        {
            "name": "binance_spot_perp_aggressor_flow_collector",
            "status": spot_perp_flow.get("classification") or "missing",
            "why": (
                f"span_hours={((spot_perp_flow.get('coverage') or {}).get('span_hours'))}/168; "
                f"dual_market_coverage={((spot_perp_flow.get('coverage') or {}).get('dual_market_coverage_pct'))}/95; "
                f"spot_gaps={(((spot_perp_flow.get('integrity') or {}).get('spot') or {}).get('missing_ids'))}; "
                f"perpetual_gaps={(((spot_perp_flow.get('integrity') or {}).get('perpetual') or {}).get('missing_ids'))}; "
                f"hypothesis_registered={((spot_perp_flow.get('runtime_boundary') or {}).get('hypothesis_registered'))}; "
                "strategy_search_allowed=false; can_trade=false"
            ),
        },
        {
            "name": "binance_spot_perp_aggressor_flow_snapshot_guard",
            "status": spot_perp_snapshot_decision or "missing",
            "why": (
                f"sealed={spot_perp_snapshot_sealed}; "
                f"snapshot_id={spot_perp_flow_snapshot.get('snapshot_id')}; "
                f"research_run={((spot_perp_flow_snapshot.get('runtime_boundary') or {}).get('research_run'))}; "
                "validation_open=false; can_trade=false"
            ),
        },
        {
            "name": "active_observer_runtime_coverage",
            "status": observer_coverage.get("decision") or "missing",
            "why": (
                f"covered={((observer_coverage.get('summary') or {}).get('covered_families'))}/"
                f"{((observer_coverage.get('summary') or {}).get('active_observer_families'))}; "
                f"blocked={((observer_coverage.get('summary') or {}).get('blocked_families'))}; "
                f"known_owners={((observer_coverage.get('summary') or {}).get('known_owner_families'))}; "
                f"can_trade={observer_coverage.get('can_trade')}"
            ),
        },
        {
            "name": "bitunix_wo104_parallel_lane",
            "status": bitunix_wo104.get("decision") or "not_started",
            "why": (
                f"phase={bitunix_wo104.get('phase')}; "
                f"replay_frames={((bitunix_wo104.get('replay') or {}).get('frames_total'))}; "
                f"canonical_replay={((bitunix_wo104.get('replay') or {}).get('canonical_replay_status'))}; "
                f"promotion={bitunix_wo104.get('promotion')}; can_trade={bitunix_wo104.get('can_trade')}"
            ),
        },
        {
            "name": "bitunix_wo105_v1_pre_floor_unit_tombstone",
            "status": bitunix_wo105_v1_tombstone.get("status") or "missing",
            "why": (
                f"events={bitunix_wo105_v1_tombstone.get('events_observed')}; "
                f"outcomes={bitunix_wo105_v1_tombstone.get('outcomes_observed')}; "
                f"superseded_by={bitunix_wo105_v1_tombstone.get('superseded_by')}; "
                "resume=false; reinterpret=false; backfill=false; can_trade=false"
            ),
        },
        {
            "name": "bitunix_wo105_v2_pre_floor_runtime_tombstone",
            "status": bitunix_wo105_v2_tombstone.get("status") or "missing",
            "why": (
                f"events={bitunix_wo105_v2_tombstone.get('events_observed')}; "
                f"outcomes={bitunix_wo105_v2_tombstone.get('outcomes_observed')}; "
                f"superseded_by={bitunix_wo105_v2_tombstone.get('superseded_by')}; "
                "resume=false; backfill=false; retune=false; can_trade=false"
            ),
        },
        {
            "name": "bitunix_wo105_v3r1_clock_contract_tombstone",
            "status": bitunix_wo105_v3r1_tombstone.get("status") or "missing",
            "why": (
                f"events={bitunix_wo105_v3r1_tombstone.get('events_observed')}; "
                f"outcomes={bitunix_wo105_v3r1_tombstone.get('outcomes_observed')}; "
                f"clock_contract_failure={bitunix_wo105_v3r1_tombstone.get('clock_contract_failure')}; "
                f"strategy_failure={bitunix_wo105_v3r1_tombstone.get('strategy_failure')}; "
                "resume=false; backfill=false; retune=false; can_trade=false"
            ),
        },
        {
            "name": "bitunix_wo105_v3r2_adapter_interface_tombstone",
            "status": bitunix_wo105_v3r2_tombstone.get("status") or "missing",
            "why": (
                f"events={bitunix_wo105_v3r2_tombstone.get('events_observed')}; "
                f"outcomes={bitunix_wo105_v3r2_tombstone.get('outcomes_observed')}; "
                f"failure_class={bitunix_wo105_v3r2_tombstone.get('failure_class')}; "
                f"strategy_failure={bitunix_wo105_v3r2_tombstone.get('strategy_failure')}; "
                "resume=false; backfill=false; retune=false; can_trade=false"
            ),
        },
        {
            "name": "bitunix_wo105_v3r3_receipt_order_tombstone",
            "status": bitunix_wo105_v3r3_tombstone.get("status") or "missing",
            "why": (
                f"candidate_setups={bitunix_wo105_v3r3_tombstone.get('candidate_setups_observed')}; "
                f"events={bitunix_wo105_v3r3_tombstone.get('events_admitted')}; "
                f"outcomes={bitunix_wo105_v3r3_tombstone.get('outcomes_observed')}; "
                f"failure_class={bitunix_wo105_v3r3_tombstone.get('failure_class')}; "
                "resume=false; backfill=false; retune=false; can_trade=false"
            ),
        },
        {
            "name": "bitunix_wo105_v3r4_causal_shadow",
            "status": bitunix_wo105_v3r4.get("decision") or "not_started",
            "why": (
                f"phase={bitunix_wo105_v3r4.get('phase')}; "
                f"floor={bitunix_wo105_v3r4.get('forward_start_at')}; "
                f"sources={','.join(((bitunix_wo105_v3r4.get('source_pipeline') or {}).get('sources') or []))}; "
                f"quorum={((bitunix_wo105_v3r4.get('source_pipeline') or {}).get('crowd_quorum_required'))}; "
                f"forward={bitunix_wo105_v3r4.get('forward_progress')}; "
                f"terminal={bitunix_wo105_v3r4.get('terminal_forward_progress')}; "
                f"blind_gate={bitunix_wo105_v3r4_blind_gate.get('decision')}; "
                f"first_cycle={bitunix_wo105_v3r4_first_cycle_gate.get('decision')}; "
                f"edge_evaluated={bitunix_wo105_v3r4.get('edge_evaluated')}; can_trade={bitunix_wo105_v3r4.get('can_trade')}"
            ),
        },
        {
            "name": "real_edge_observer_pulse",
            "status": observer_pulse.get("decision") or "missing",
            "why": f"failed_steps={len(observer_pulse.get('failed_steps') or [])}; can_trade={observer_pulse.get('can_trade')}",
        },
    ]
    if bitunix_bar_finality:
        kline = bitunix_bar_finality.get("kline_finality") or {}
        trades = bitunix_bar_finality.get("trade_bar_finality") or {}
        built_running.append(
            {
                "name": "bitunix_bar_finality_terminal_audit",
                "status": bitunix_bar_finality.get("decision"),
                "why": (
                    f"kline_exact_ohlc={kline.get('exact_ohlc_matches')}/{kline.get('comparisons')}; "
                    f"trade_exact_ohlcv={trades.get('exact_ohlcv_matches')}/{trades.get('accepted_capture_full_bars')}; "
                    "existing_cohort_retune=false; automatic_source_replacement=false; can_trade=false"
                ),
            }
        )
    if bitunix_raw_event:
        raw_gate = bitunix_raw_event.get("terminal_gate") or {}
        built_running.append(
            {
                "name": "bitunix_raw_event_replenishment_v106",
                "status": bitunix_raw_event.get("decision"),
                "why": (
                    f"floor={bitunix_raw_event.get('forward_floor_utc')}; "
                    f"resolved={raw_gate.get('resolved_events')}/{raw_gate.get('required_resolved_events')}; "
                    f"tests={((bitunix_raw_event.get('proof') or {}).get('targeted_tests'))}; "
                    "runtime_activated=false; interim_metrics=false; can_trade=false"
                ),
            }
        )
    if bitunix_raw_event_intake:
        first_run = bitunix_raw_event_intake.get("first_run") or {}
        built_running.append(
            {
                "name": "bitunix_raw_event_forward_intake_v107",
                "status": bitunix_raw_event_intake.get("decision"),
                "why": (
                    f"selected_post_floor={first_run.get('selected_completed_post_floor_runs')}; "
                    f"edge_rows={first_run.get('edge_rows_admitted')}; "
                    f"visibility={first_run.get('outcome_visibility')}; "
                    "manual_only=true; collector_created=false; autoload=false; can_trade=false"
                ),
            }
        )
    if bitunix_wo108_delivery:
        package = bitunix_wo108_delivery.get("package") if isinstance(bitunix_wo108_delivery.get("package"), dict) else {}
        verification = (
            bitunix_wo108_delivery.get("verification")
            if isinstance(bitunix_wo108_delivery.get("verification"), dict)
            else {}
        )
        built_running.append(
            {
                "name": "bitunix_wo108_evidence_delivery",
                "status": bitunix_wo108_delivery.get("decision") or "missing",
                "why": (
                    f"package={package.get('name')}; files={package.get('files_verified')}; "
                    f"missing={len(bitunix_wo108_delivery.get('missing_evidence') or [])}; "
                    f"delivery_complete={package.get('delivery_complete')}; "
                    f"runtime_loop_still_running={verification.get('runtime_loop_still_running')}; "
                    "transfer_only=true; edge_evaluated=false; can_trade=false"
                ),
            }
        )
    if post_fill_markout:
        smoke = (
            post_fill_markout.get("current_smoke")
            if isinstance(post_fill_markout.get("current_smoke"), dict)
            else {}
        )
        built_running.append(
            {
                "name": "post_fill_markout",
                "status": post_fill_markout.get("decision") or "missing",
                "why": (
                    f"archive_source_mode={smoke.get('archive_source_mode')}; "
                    f"raw_fills={smoke.get('raw_fill_count')}; "
                    f"evaluated={smoke.get('evaluated_fill_count')}; "
                    "book_mid_and_mark_proxy_built=true; guard_wired=false; can_trade=false"
                ),
            }
        )
    if post_fill_markout_forward:
        forward_snapshot = post_fill_markout_forward.get("current_forward") or {}
        capture = forward_snapshot.get("book_capture") or {}
        durable_runtime = post_fill_markout_forward.get("durable_runtime") or {}
        built_running.append(
            {
                "name": "post_fill_markout_forward_observer",
                "status": forward_snapshot.get("decision") or post_fill_markout_forward.get("decision") or "missing",
                "why": (
                    f"lock={post_fill_markout_forward.get('lock_id')}; "
                    f"book_fresh={capture.get('capture_fresh')}; "
                    f"durable_runtime={durable_runtime.get('running_verified')}; "
                    f"ownership={durable_runtime.get('ownership_decision')}; "
                    f"raw_fills={forward_snapshot.get('raw_fill_count')}; "
                    f"evaluated={forward_snapshot.get('evaluated_fill_count')}; "
                    "thresholds_locked=false; guard_wired=false; can_trade=false"
                ),
            }
        )
    partial_waiting = [
        {
            "name": component.get("name"),
            "status": component.get("status"),
            "blocker": component.get("blocker"),
        }
        for component in readiness.get("components") or []
        if isinstance(component, dict) and not component.get("ready")
    ]
    partial_waiting.extend(
        [
            {
                "name": "liquidation_book_independence_gate",
                "status": liquidation_book.get("decision"),
                "blocker": ",".join(liquidation_book.get("blockers") or []),
            },
            {
                "name": "exogenous_liquidity_regime_observer",
                "status": exogenous.get("decision"),
                "blocker": ",".join(exogenous.get("blockers") or []),
            },
            {
                "name": "liquidation_cross_venue_receipt_leadership_observer",
                "status": liquidation_leadership.get("decision"),
                "blocker": ",".join(liquidation_leadership.get("blockers") or []) if liquidation_leadership else "observer_report_missing",
            },
            {
                "name": "cex_dex_funding_lead_lag_collector",
                "status": cex_dex_funding.get("decision"),
                "blocker": "minimum_forward_span_and_direct_source_replication_not_met" if cex_dex_funding else "collector_report_missing",
            },
            {
                "name": "cex_funding_direct_replication_collector",
                "status": direct_funding.get("decision"),
                "blocker": "matching_forward_span_not_met" if direct_funding else "direct_replication_report_missing",
            },
            {
                "name": "cex_funding_source_alignment_monitor",
                "status": funding_alignment.get("decision"),
                "blocker": ",".join(funding_alignment.get("blockers") or []) if funding_alignment else "alignment_report_missing",
            },
            {
                "name": "cex_funding_research_readiness_monitor",
                "status": funding_readiness.get("decision"),
                "blocker": ",".join(funding_readiness_blockers) if funding_readiness else "readiness_report_missing",
            },
            *(
                [
                    {
                        "name": "cex_funding_successor_admission_gate",
                        "status": funding_successor_admission.get("decision"),
                        "blocker": (
                            "manual_parameter_identical_lock_review"
                            if funding_successor_eligible
                            else ",".join(
                                ((funding_successor_admission.get("rolling_alignment") or {}).get("blockers") or [])
                            )
                            or "clean_alignment_window_not_ready"
                        ),
                    }
                ]
                if funding_successor_admission
                else []
            ),
            *(
                [
                    {
                        "name": "binance_spot_perp_aggressor_flow_snapshot_guard",
                        "status": spot_perp_snapshot_decision or spot_perp_flow.get("classification"),
                        "blocker": ",".join(
                            spot_perp_flow_snapshot.get("failed_checks")
                            or spot_perp_flow_readiness.get("blockers")
                            or ["snapshot_guard_report_missing"]
                        ),
                    }
                ]
                if spot_perp_flow and not spot_perp_snapshot_sealed
                else []
            ),
        ]
    )
    if funding_watchdog and funding_watchdog.get("healthy") is not True:
        partial_waiting.append(
            {
                "name": "cex_funding_freshness_watchdog",
                "status": funding_watchdog.get("decision"),
                "blocker": ",".join(funding_watchdog.get("blockers") or []),
            }
        )
    if funding_incident_alert and int(funding_incident_alert.get("pending_notifications") or 0) > 0:
        partial_waiting.append(
            {
                "name": "cex_funding_freshness_transition_alert",
                "status": funding_incident_alert.get("decision"),
                "blocker": "pending_incident_delivery",
            }
        )
    if deribit_options_v3 and deribit_v3_state.get("readiness_gate_ready") is not True:
        partial_waiting.append(
            {
                "name": "deribit_options_v3_data_layer",
                "status": deribit_options_v3.get("decision"),
                "blocker": "clean_future_floor_7d_1800_healthy_slots_95pct_schedule_15m_max_gap_not_met",
            }
        )
    elif deribit_options and (deribit_options.get("forward_progress") or {}).get("readiness_gate_ready") is not True:
        partial_waiting.append(
            {
                "name": "deribit_options_skew_forward_stack",
                "status": deribit_options.get("decision"),
                "blocker": "minimum_7d_1800_healthy_slots_95pct_schedule_not_met",
            }
        )
    if bitunix_raw_event and (bitunix_raw_event.get("terminal_gate") or {}).get("ready") is not True:
        partial_waiting.append(
            {
                "name": "bitunix_raw_event_replenishment_v106",
                "status": bitunix_raw_event.get("decision"),
                "blocker": "manual_runtime_review_then_100_events_5_days_20_independent_4h_blocks",
            }
        )
    tombstones = compact_tombstones(tombstone)
    do_not_repeat = [
        f"`{item.get('family')}` - {item.get('reason')} Reuse rule: {item.get('reuse_rule')}"
        for item in tombstones
    ]
    if bitunix_wo108_delivery and (bitunix_wo108_delivery.get("package") or {}).get("delivery_complete") is True:
        do_not_repeat.append(
            "`bitunix_wo108_evidence_delivery` - exact WO105 V2 evidence package already delivered and hash-verified; do not create another package unless a new work order or changed evidence set explicitly requires it."
        )
    if bitunix_wo105_v3r1_tombstone:
        do_not_repeat.append(
            "`bitunix_wo105_v3r1_clock_contract` - V3R1 ended with zero admitted events after a causal clock-contract defect; preserve the tombstone and do not resume, backfill, retune or reinterpret it as a strategy failure."
        )
    if bitunix_wo105_v3r2_tombstone:
        do_not_repeat.append(
            "`bitunix_wo105_v3r2_adapter_interface` - V3R2 ended with zero admitted events because the bound adapter omitted the assembler-required load_rows interface; preserve the tombstone and do not resume, backfill, retune or reinterpret it as a strategy failure."
        )
    if bitunix_bar_finality:
        do_not_repeat.append(
            "`bitunix_v3r4_bar_source_rescue` - REST, public kline snapshots and batch-timestamp trade reconstruction failed the frozen exact-bar contract; do not retune the five-second cutoff, approximate final bars or substitute these sources automatically."
        )
    if bitunix_raw_event:
        do_not_repeat.append(
            "`bitunix_raw_event_replenishment_v106` - preregistration, immutable lock and offline oracle already exist; do not rebuild, inherit WO-105 progress, inspect interim outcomes, retune or reverse a failed rule."
        )
    if bitunix_raw_event_intake:
        do_not_repeat.append(
            "`bitunix_raw_event_forward_intake_v107` - manual completed-run discovery and fail-closed oracle intake already exist; do not create a second scanner, collector or autoload path."
        )
    if google_doc_wo004:
        do_not_repeat.append(
            "`google_doc_tradingos_wo004` - the supplied work order is already satisfied by the hash-verified WO-004 return package; do not reimplement or copy its oracle into Active."
        )
    if post_fill_markout_forward:
        do_not_repeat.append(
            "`post_fill_markout_forward_observer` - horizons, freshness budgets and common-cohort semantics are immutable; the durable singleton collector is already lifecycle-managed, so do not create a second collector, retune the lock or create an economic guard before the locked manual-review floor is reached."
        )
    elif post_fill_markout:
        do_not_repeat.append(
            "`post_fill_markout` - causal analyzer and CLI already exist; do not rebuild or wire thresholds before synchronized authoritative fills and preregistered horizons exist."
        )
    frontier_rows = compact_frontier(frontier)
    rejected_frontier = [
        row for row in frontier_rows
        if str(row.get("status")) == "rejected_research_only"
    ]
    studied_inputs = {
        "workspace_curation": f"keep_active={(curation.get('counts') or {}).get('keep_active')}; archive_processed={(curation.get('counts') or {}).get('archive_processed')}; delete_generated={(curation.get('counts') or {}).get('delete_generated')}",
        "downloads_scan": f"counts={downloads.get('counts')}",
        "crypto_guides": f"dispositions={(crypto_guides.get('summary') or {}).get('disposition_counts')}",
        "strategy_frontier": f"families={(frontier.get('summary') or {}).get('families')}; rejected={(frontier.get('summary') or {}).get('rejected')}; promotable={(frontier.get('summary') or {}).get('promotable')}",
        "tombstones": f"families={(tombstone.get('summary') or {}).get('families')}",
        "market_neutral_crypto_2026_doc": "processed_2026-07-13; adopted=cex_dex_funding_data_collection; duplicated=basis,pairs,liquidation_rebound,ofi; hip4=deferred",
        "liquidation_feeds_2026_doc": "processed_2026-07-13; adopted=local_receipt_timestamp_schema_and_cross_venue_clock_readiness; duplicated=reversal,continuation,absorption,book_replenishment; unverified_numeric_claims_not_imported",
        "bitunix_wo108_evidence_delivery": (
            f"decision={bitunix_wo108_delivery.get('decision')}; "
            f"package={((bitunix_wo108_delivery.get('package') or {}).get('name'))}; "
            f"missing={len(bitunix_wo108_delivery.get('missing_evidence') or [])}; transfer_only=true"
        ),
        "bitunix_bar_finality_audit": (
            f"decision={bitunix_bar_finality.get('decision')}; "
            f"kline_exact_ohlc={((bitunix_bar_finality.get('kline_finality') or {}).get('exact_ohlc_matches'))}; "
            f"trade_exact_ohlcv={((bitunix_bar_finality.get('trade_bar_finality') or {}).get('exact_ohlcv_matches'))}; "
            "terminal_source_finding=true; can_trade=false"
        ),
        "bitunix_raw_event_replenishment_v106": (
            f"decision={bitunix_raw_event.get('decision')}; "
            f"floor={bitunix_raw_event.get('forward_floor_utc')}; "
            f"resolved={((bitunix_raw_event.get('terminal_gate') or {}).get('resolved_events'))}; "
            f"edge_evaluated={bitunix_raw_event.get('edge_evaluated')}; runtime_activated=false; can_trade=false"
        ),
        "bitunix_raw_event_forward_intake_v107": (
            f"decision={bitunix_raw_event_intake.get('decision')}; "
            f"selected={((bitunix_raw_event_intake.get('first_run') or {}).get('selected_completed_post_floor_runs'))}; "
            f"edge_rows={((bitunix_raw_event_intake.get('first_run') or {}).get('edge_rows_admitted'))}; "
            "manual_only=true; autoload=false; can_trade=false"
        ),
        "google_doc_tradingos_wo004": (
            f"disposition={google_doc_wo004.get('disposition')}; "
            f"return_status={((google_doc_wo004.get('comparison') or {}).get('return_status'))}; "
            f"native_tests={((google_doc_wo004.get('comparison') or {}).get('native_tests'))}; "
            "new_edge_hypotheses=0; can_trade=false"
        ),
        "post_fill_markout": (
            f"decision={post_fill_markout.get('decision')}; "
            f"raw_fills={((post_fill_markout.get('current_smoke') or {}).get('raw_fill_count'))}; "
            "guard_wired=false; can_trade=false"
        ),
        "post_fill_markout_forward": (
            f"decision={((post_fill_markout_forward.get('current_forward') or {}).get('decision'))}; "
            f"blockers={((post_fill_markout_forward.get('current_forward') or {}).get('blockers'))}; "
            f"durable_runtime={((post_fill_markout_forward.get('durable_runtime') or {}).get('running_verified'))}; "
            "immutable_horizons=true; guard_wired=false; can_trade=false"
        ),
    }
    funding_not_done = (
        (
            f"CEX funding V3 is terminal and immutable; successor admission is {funding_successor_admission.get('decision')} "
            f"with earliest recheck {((funding_successor_admission.get('diagnostic_window') or {}).get('earliest_recheck_at_utc'))}. "
            "No successor is created, no V3 rows are inherited and no price outcomes are read."
        )
        if funding_terminal and funding_successor_admission
        else "CEX funding source-alignment V3 is terminal and immutable; aggregate/direct collection may continue only as raw evidence for a separately reviewed parameter-identical future-floor successor, not as progress of the failed alignment lock."
        if funding_terminal
        else f"CEX-DEX funding collector has {funding_sample.get('unique_minute_buckets')} aggregate snapshots, {((direct_funding.get('sample') or {}).get('unique_minute_buckets'))} direct snapshots and {((funding_alignment.get('sample') or {}).get('matching_minute_buckets'))} aligned buckets with time coverage {((funding_alignment.get('sample') or {}).get('matching_time_coverage'))}; no edge evaluator may be created before continuity and both locked forward spans mature."
    )
    not_done = [
        f"Microstructure snapshot not sealed yet; current book coverage is {((micro_unblock.get('coverage') or {}).get('book_coverage_pct')) if micro_unblock else micro.get('book_coverage_pct')}% with ETA {((micro_unblock.get('book_diagnostic') or {}).get('eta_utc')) if micro_unblock else None}; wait until the exact gate passes, then run the locked post-seal runner.",
        (
            f"Deribit V2 is preserved as an excluded operational cohort; V3 starts at {deribit_v3_state.get('forward_floor_utc')} with "
            f"{deribit_v3_metrics.get('span_days')} days, {deribit_v3_metrics.get('healthy_slots')} healthy slots and "
            f"{deribit_v3_metrics.get('scheduled_coverage')} scheduled coverage. No observer successor exists before the clean V3 gate passes."
            if deribit_options_v3
            else f"Deribit options stack is forward-only with {((deribit_options.get('forward_progress') or {}).get('span_days'))} days, {((deribit_options.get('forward_progress') or {}).get('healthy_slots'))} healthy slots and {((deribit_options.get('forward_progress') or {}).get('scheduled_coverage'))} scheduled coverage; keep the observer blocked until all locked readiness gates pass."
        ),
        f"Active observer runtime coverage is {((observer_coverage.get('summary') or {}).get('covered_families'))}/{((observer_coverage.get('summary') or {}).get('active_observer_families'))}; unknown or dead runtime owners fail closed, and report freshness alone is not runtime proof.",
        f"Bitunix WO-104 remains immutable provenance; WO105 V1/V2/V3/V3R1/V3R2 and V3R3 are tombstoned with no resume path. V3R3 is {bitunix_wo105_v3r3_tombstone.get('status')} after one setup but zero admitted events and zero inspected outcomes. Parameter-identical V3R4 is current at {bitunix_wo105_v3r4.get('decision')}, terminal progress {bitunix_wo105_v3r4.get('terminal_forward_progress')}, blind gate {bitunix_wo105_v3r4_blind_gate.get('decision')}, first-cycle gate {bitunix_wo105_v3r4_first_cycle_gate.get('decision')} and edge_evaluated=false.",
        f"Liquidation forceOrder feed is active with {(liquidation_progress.get('sample') or {}).get('events') or prereg_liquidation.get('events')} lock-matched events, {(liquidation_progress.get('sample') or {}).get('independent_4h_blocks')} raw blocks and {(liquidation_progress.get('sample') or {}).get('matured_independent_4h_blocks')} matured blocks; pipeline remains gated.",
        f"Cross-venue canonical paired liquidation receipt leadership is prospectively locked from {((liquidation_leadership.get('lock') or {}).get('forward_start_at'))}; primary matched pairs={((liquidation_leadership.get('primary_sample') or {}).get('matched_pairs'))}; unmatched events are excluded and no price outcomes are read.",
        "Legacy Bybit directional liquidation observers are semantic tombstones because V1 inverted documented position-side labels; corrected V2 discovery cannot inherit their locks or outcomes.",
        "Corrected Bybit liquidation reversal V2 is a terminal design tombstone; its zero resolved observations and all outcomes are excluded from successors.",
        "Corrected Bybit liquidation reversal V3 is a terminal input-evidence tombstone: raw host wall time was not calibrated to Bybit server time, no outcomes were computed, and no rows or metrics are admitted to V4.",
        "Corrected Bybit liquidation reversal V4 is a terminal packet-identity tombstone: its market tuple was not a valid unique event id, no outcomes were computed at failure, and no observations are admitted to V5.",
        "Corrected Bybit liquidation reversal V5 is a terminal pre-floor source-contract tombstone: packet identity passed, but no rows or outcomes are admitted because its source label was not canonical.",
        f"Corrected Bybit liquidation reversal V5R1 is independently locked from {((bybit_canonical_forward.get('lock') or {}).get('forward_start_at'))}; resolved={((bybit_canonical_forward.get('sample') or {}).get('resolved_events'))}; only schema-v4 calibrated packet-ordinal receipts with the stable canonical source label and fully closed bars are admitted, while interim outcomes remain hidden until every sample gate passes.",
        f"Bybit V5R1 one-time pre-floor commissioning passed on {((bybit_canonical_v5_commissioning.get('diagnostic_window') or {}).get('schema_v4_rows'))} schema-v4 rows across {((bybit_canonical_v5_commissioning.get('diagnostic_window') or {}).get('packets'))} complete packets; those rows remain diagnostic-only and excluded from the V5R1 sample.",
        f"Bybit V4 one-time pre-floor commissioning passed on {((bybit_canonical_v4_commissioning.get('commissioning_window') or {}).get('schema3_rows'))} schema-v3 rows across {((bybit_canonical_v4_commissioning.get('commissioning_window') or {}).get('collector_sessions'))} sessions; those rows remain diagnostic-only and excluded from the V4 sample.",
        "Exogenous stablecoin/macro observer is preregistered and must wait for new forward macro proxy dates; historical source rows remain excluded from strategy selection.",
        funding_not_done,
        (
            f"Binance Spot/Perp aggressor-flow snapshot `{spot_perp_flow_snapshot.get('snapshot_id')}` is sealed and hash-verified; no hypothesis is registered, research was not run, and only a separate prospective preregistration review may follow."
            if spot_perp_snapshot_sealed
            else f"Binance Spot/Perp aggressor-flow collection is data-only at {((spot_perp_flow.get('coverage') or {}).get('span_hours'))} hours and {((spot_perp_flow.get('coverage') or {}).get('dual_market_coverage_pct'))}% aligned-minute coverage; no hypothesis is registered and no outcome or parameter search is allowed before a sealed seven-day snapshot."
        ),
        "No strategy family has independent forward evidence sufficient for paper/live execution.",
        "Downloads scan still contains candidate files by priority; each must be routed through extraction/test/tombstone, not ad hoc reading.",
        "Telegram token should be rotated before production-sensitive use because it was disclosed in chat history.",
    ]
    if post_fill_markout_forward:
        not_done.append(
            "Post-fill markout forward observer and its durable singleton bookTicker capture are lifecycle-proven, but authoritative demo userTrades are unavailable; no economic drift threshold or execution guard exists."
        )
    elif post_fill_markout:
        not_done.append(
            "Post-fill markout consumer is built, but synchronized authoritative fills are absent; collect demo/paper userTrades plus one provenance-aligned bookTicker root before proposing drift thresholds."
        )
    if bitunix_bar_finality:
        not_done.append(
            "Bitunix exact 5m bar finality is terminal for the frozen V3R4 source contract; the separately locked V106 raw event-time cohort inherits no rows, outcomes, progress or parameters from it."
        )
    if bitunix_raw_event:
        not_done.append(
            "Bitunix raw-event V106 has a locked offline oracle and V107 has a manual-only intake gate, but no completed post-floor capture or event has been admitted; displayed-depth replenishment, fills and profitability remain unproven."
        )
    funding_next_action = (
        (
            "Perform one manual parameter-identical future-floor lock review; do not inherit V3 rows, backfill or retune."
            if funding_successor_eligible
            else f"Do not create a funding successor before the admission recheck at {((funding_successor_admission.get('diagnostic_window') or {}).get('earliest_recheck_at_utc'))}; keep both collectors unchanged."
        )
        if funding_terminal and funding_successor_admission
        else "Preserve the failed CEX funding alignment lock; any retry must be a reviewed parameter-identical future-floor successor and must not inherit failed-lock progress."
        if funding_terminal
        else "Keep aggregate and direct CEX funding collectors fixed for at least 14 matching forward days; do not infer latency or create entries before source-semantic alignment is audited."
    )
    funding_cadence_action = (
        "For any reviewed funding successor, preserve anchored start-to-start cadence and require at least 95% matching-minute coverage with no gap above five minutes before manual semantic review."
        if funding_terminal
        else "Preserve anchored start-to-start funding cadence and require at least 95% matching-minute coverage with no gap above five minutes before manual source-semantic review."
    )
    next_actions = [
        "Keep collectors running; do not retune rejected strategies while waiting for forceOrder/microstructure data.",
        "Keep unaffected liquidation observers unchanged; never resume legacy Bybit directional observers that used inverted position-side labels.",
        "Never resume corrected Bybit V2; keep its design tombstone immutable.",
        "Keep the corrected Bybit V3 clock tombstone immutable; never loosen its lag gate, rewrite its rows or inspect its return metrics.",
        "Never resume the corrected Bybit liquidation reversal V4 lock; its packet-identity tombstone is immutable and no V4 rows or outcomes are admitted to successors.",
        "Do not rerun or reinterpret the Bybit V4 pre-floor commissioning after its floor; retain it only as operational evidence that calibrated receipt transport worked before prospective collection.",
        "Keep only the independently sealed Bybit V5R1 observer active; admit schema-v4 canonical-source packet-ordinal events and keep interim outcomes hidden until every locked sample gate passes.",
        "Keep Binance and Bybit collectors plus the immutable receipt-leadership observer running unchanged; only a terminal leadership pass may open a separate future-floor price-impact preregistration.",
        "Keep stablecoin and macro collectors running; the exogenous observer will evaluate only new source dates after its forward floor.",
        funding_next_action,
        funding_cadence_action,
        "Keep the funding freshness watchdog fail-closed and restart-free; collector health is operational evidence only, never edge evidence.",
        "Keep funding incident notifications transition-only; never convert operational BLOCKED/RECOVERED messages into trade signals.",
        (
            "Keep Deribit V2 immutable and excluded. Keep V3 collector/readiness unchanged; do not inherit old rows, relax the 0.98 join gate, create an observer successor or inspect outcomes before the clean 7d/1800-slot/95%/15m gate opens."
            if deribit_options_v3
            else "Keep the Deribit options hypothesis immutable and forward-only; do not inspect event outcomes or retune before the independent 7d/1800-slot/95% readiness gate opens."
        ),
        (
            "Review a separate prospective Spot/Perp lead-lag preregistration against the sealed snapshot provenance; do not auto-run research or reuse the same sample for a final claim."
            if spot_perp_snapshot_sealed
            else "Keep the Binance Spot/Perp aggressor-flow collector data-only; the exactly-once guard may seal the snapshot after 168 hours, 95% aligned coverage and zero ID gaps, but it must not auto-run research."
        ),
        "Keep active observer runtime coverage in every real-edge pulse; never infer active collection from report freshness alone.",
        "Keep Bitunix WO-104 provenance and WO105 V1/V2/V3/V3R1/V3R2/V3R3/V3R4 terminal history immutable; V106 is a separate raw-event lock and must inherit no prior rows, outcomes, progress or parameters.",
        "Use source-alignment readiness only for manual semantic review; it cannot establish edge, source equivalence or trading permission.",
        "When microstructure snapshot seals, run governance chain: snapshot gate -> post-seal guard -> locked research runner -> validation approval audit.",
        "For Hermes bot work: run anti-loop prompt before new coding; require a runtime-proof pack before claiming a bot feature is live.",
    ]
    if post_fill_markout_forward:
        next_actions.append(
            "Keep the managed Binance bookTicker component and locked cohort unchanged; after an intentional component restart with read-only demo credentials, backfill only authoritative userTrades into the same root and wait for 100 evaluated fills across 3 UTC days before manual distribution review."
        )
    elif post_fill_markout:
        next_actions.append(
            "Do not rebuild post-fill markout; preregister horizons, collect synchronized authoritative fills and bookTicker, then evaluate the forward distribution without wiring a guard prematurely."
        )
    if bitunix_bar_finality and not bitunix_raw_event:
        next_actions.append(
            "Do not continue Bitunix bar-source substitution. If the venue is retained, preregister a separate shadow-only raw event-time depth/trade hypothesis that does not require exact exchange candle finality."
        )
    elif bitunix_raw_event:
        next_actions.append(
            "Rerun the existing V107 manual intake only after a completed independently accepted capture started after the V106 floor; preserve the lock and hide outcomes until 100 resolved events across five UTC days and 20 independent four-hour blocks."
        )
    source_of_truth = {name: portable(path) for name, path in paths.items() if path}
    if runtime_shutdown_path.exists():
        source_of_truth["runtime_shutdown_sentinel"] = portable(runtime_shutdown_path)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/anti_loop_state_map.py",
        "decision": "anti_loop_state_map_ready_no_trade_edge_yet",
        "can_trade": False,
        "source_of_truth": source_of_truth,
        "managed_runtime": {
            "operator_stopped": bool(runtime_shutdown),
            "shutdown_request": runtime_shutdown or None,
            "automatic_resume_allowed": False,
        },
        "current_blockers": blockers,
        "built_running": built_running,
        "active_strategies": compact_strategy_rows(inventory),
        "partial_waiting": partial_waiting,
        "frontier_families": frontier_rows,
        "rejected_frontier_count": len(rejected_frontier),
        "tombstones": tombstones,
        "do_not_repeat": do_not_repeat,
        "studied_inputs": studied_inputs,
        "not_done": not_done,
        "next_actions": next_actions,
        "hermes_prompt_path": None,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a compact anti-loop project state map and Hermes prompt")
    parser.add_argument("--docs-dir", default="docs")
    parser.add_argument("--out-prefix", default="docs/ANTI_LOOP_STATE_MAP_2026-06-30")
    parser.add_argument("--prompt-out", default="docs/HERMES_TRADING_BOT_ANTI_LOOP_PROMPT_2026-06-30.md")
    args = parser.parse_args()

    docs_dir = resolve_path(args.docs_dir)
    out_prefix = resolve_path(args.out_prefix)
    prompt_out = resolve_path(args.prompt_out)
    report = build_report(docs_dir)
    report["hermes_prompt_path"] = portable(prompt_out)
    prompt = build_hermes_prompt(report)

    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    prompt_out.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    prompt_out.write_text(prompt, encoding="utf-8")

    print(
        json.dumps(
            {
                "decision": report["decision"],
                "blockers": report["current_blockers"],
                "built_running": len(report["built_running"]),
                "tombstoned": len(report["tombstones"]),
                "can_trade": False,
                "json": portable(out_prefix.with_suffix(".json")),
                "md": portable(out_prefix.with_suffix(".md")),
                "prompt": portable(prompt_out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
