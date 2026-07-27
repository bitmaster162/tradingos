#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT = ROOT / "docs" / "DOCS_CANON_AUDIT_2026-06-02.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def path_exists(path: str) -> bool:
    return (ROOT / path).exists()


def consumer(paths: list[str]) -> dict[str, Any]:
    return {
        "paths": paths,
        "exists": all(path_exists(path) for path in paths),
    }


def map_item(item: dict[str, Any]) -> dict[str, Any]:
    path = str(item.get("path", ""))
    flags = item.get("flags", [])
    base = {
        "path": path,
        "title": item.get("title"),
        "audit_category": item.get("category"),
        "audit_flags": flags,
        "runtime_decision": "active_reference_only",
        "consumer_status": "no_direct_consumer",
        "consumer": consumer([]),
        "risk_boundary": "reference_only; not a live trading signal",
        "next_action": "Keep as active reference unless a bounded consumer is added.",
    }

    if path in {"configs/MAX_PIPELINE_CONFIG_SAMPLE.json", "configs/MAX_PIPELINE_CONFIG_SMOKE.json"}:
        base.update(
            {
                "runtime_decision": "already_consumed",
                "consumer_status": "runnable_consumer_exists",
                "consumer": consumer(["portable/MAX_ops_preflight.py", "portable/run_max_pipeline.py"]),
                "risk_boundary": "local composite/preflight only; no orders",
                "next_action": "Keep as current MAX Core Lite config input.",
            }
        )
        return base

    if path == "bitevo/examples/alert_entry_example.json":
        base.update(
            {
                "runtime_decision": "already_consumed",
                "consumer_status": "runnable_consumer_exists",
                "consumer": consumer(["scripts/validate_bitevo_alerts.py", "tools/bitevo_contract_checker.py"]),
                "risk_boundary": "schema/field validation only; no alert delivery",
                "next_action": "Use as positive validator and contract-check fixture.",
            }
        )
        return base

    if path == "bitevo/examples/alert_cancel_example.json":
        base.update(
            {
                "runtime_decision": "runtime_consumer_added",
                "consumer_status": "bounded_lifecycle_contract_consumer",
                "consumer": consumer(["tools/bitevo_contract_checker.py"]),
                "risk_boundary": "lifecycle event validation only; no alert delivery",
                "next_action": "Keep as cancel lifecycle contract fixture.",
            }
        )
        return base

    if path in {"smartmoney/example_smartmoney_alert.json", "smartmoney/telegram_message_smartmoney.txt"}:
        base.update(
            {
                "runtime_decision": "already_consumed",
                "consumer_status": "runnable_consumer_exists",
                "consumer": consumer(["smartmoney/format_smartmoney_alert.py"]),
                "risk_boundary": "message rendering only; no Telegram send",
                "next_action": "Keep as renderer fixture/template.",
            }
        )
        return base

    if path in {"v7/alerts_rules.json", "v7/regex_test_sample.txt"}:
        base.update(
            {
                "runtime_decision": "already_consumed",
                "consumer_status": "runnable_consumer_exists",
                "consumer": consumer(["v7/rule_engine_template.py"]),
                "risk_boundary": "local text/rule match only; no trading",
                "next_action": "Keep as v7 smoke fixture.",
            }
        )
        return base

    if path == "configs/ARBITER_CTI_PANEL_v1.json":
        base.update(
            {
                "runtime_decision": "runtime_consumer_added",
                "consumer_status": "bounded_overlay_consumer",
                "consumer": consumer(["tools/overlay_signal_evaluator.py"]),
                "risk_boundary": "rotation overlay only; not a direct entry trigger",
                "next_action": "Use CTI evaluator as a dashboard metric; require separate strategy confirmation.",
            }
        )
        return base

    if path == "configs/ETHBTC_CORE_HEDGE_DASHBOARD_v1.json":
        base.update(
            {
                "runtime_decision": "runtime_consumer_added",
                "consumer_status": "bounded_overlay_consumer",
                "consumer": consumer(["tools/overlay_signal_evaluator.py"]),
                "risk_boundary": "portfolio role overlay only; not a direct entry trigger",
                "next_action": "Use ETHBTC evaluator to classify ETH as core/hedge/risk-off after daily data is supplied.",
            }
        )
        return base

    if path.startswith("bitevo/schemas/") or path == "bitevo/openapi.yaml":
        base.update(
            {
                "runtime_decision": "runtime_consumer_added" if path.startswith("bitevo/schemas/") else "validator_candidate",
                "consumer_status": "bounded_schema_contract_consumer" if path.startswith("bitevo/schemas/") else "spec_without_full_consumer",
                "consumer": consumer(["tools/bitevo_contract_checker.py"]) if path.startswith("bitevo/schemas/") else consumer([]),
                "risk_boundary": "contract/spec only; no API server in this package",
                "next_action": "Use stdlib contract checker for schemas; OpenAPI still needs a separate API server proof.",
            }
        )
        return base

    if path == "bitevo/templates/telegram_message.txt":
        base.update(
            {
                "runtime_decision": "runtime_consumer_added",
                "consumer_status": "bounded_template_contract_consumer",
                "consumer": consumer(["tools/bitevo_contract_checker.py"]),
                "risk_boundary": "template only; no send path",
                "next_action": "Keep template under contract-check; no Telegram send path is implied.",
            }
        )
        return base

    if path in {"configs/BitEvo_composite_config.json", "smartmoney/SmartMoney_Alerts_Config.json"}:
        base.update(
            {
                "runtime_decision": "runtime_consumer_added",
                "consumer_status": "bounded_registry_contract_consumer",
                "consumer": consumer(["tools/bitevo_registry_validator.py"]),
                "risk_boundary": "setup registry only; detectors must prove signals separately",
                "next_action": "Keep under registry validation; do not imply alerts are detected.",
            }
        )
        return base

    if path in {"configs/BTC_TREND_FLEX_SYSTEM.json", "docs/BTC_TREND_FLEX_SYSTEM.md", "smartmoney/BTC_TrendFlex_Checklist.md"}:
        base.update(
            {
                "runtime_decision": "manual_gate_reference",
                "consumer_status": "no_strategy_engine_consumer",
                "consumer": consumer([]),
                "risk_boundary": "manual playbook/risk gate; no proven automated strategy runtime",
                "next_action": "Extract only pre-trade gate checks into code after defining input data columns.",
            }
        )
        return base

    if path in {"configs/HL3_REGIME_PLAYBOOK_v1.json", "smartmoney/HL3_Regime_Checklist.md", "smartmoney/HL3_Telegram_Card_Template.md"}:
        base.update(
            {
                "runtime_decision": "manual_gate_reference",
                "consumer_status": "visual_context_not_automated",
                "consumer": consumer([]),
                "risk_boundary": "manual/visual regime checklist; not an automated signal",
                "next_action": "Keep as discretionary checklist unless strict measurable inputs are defined.",
            }
        )
        return base

    if path.startswith("docs/") or path.startswith("knowledge/") or path.startswith("smartmoney/") or path.startswith("v7/"):
        base.update(
            {
                "runtime_decision": "active_reference_only",
                "consumer_status": "documentation_or_checklist",
                "consumer": consumer([]),
                "risk_boundary": "reference/checklist only",
                "next_action": "Use for human review or extract one bounded rule at a time.",
            }
        )
        return base

    return base


def build_report(audit: dict[str, Any]) -> dict[str, Any]:
    active = [item for item in audit.get("items", []) if item.get("category") == "active_reference"]
    mapped = [map_item(item) for item in active]
    decision_counts = Counter(item["runtime_decision"] for item in mapped)
    status_counts = Counter(item["consumer_status"] for item in mapped)
    return {
        "generated_at": now_iso(),
        "audit_source": rel(DEFAULT_AUDIT) if DEFAULT_AUDIT.exists() else str(DEFAULT_AUDIT),
        "active_reference_count": len(active),
        "decision_counts": dict(sorted(decision_counts.items())),
        "consumer_status_counts": dict(sorted(status_counts.items())),
        "policy": {
            "config_is_not_runtime": True,
            "no_live_trading_permission": True,
            "no_order_execution": True,
            "safe_extraction_rule": "Only add code for bounded evaluators, validators or dashboard metrics.",
        },
        "items": mapped,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Active Reference Runtime Extraction",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Audit source: `{report['audit_source']}`",
        f"Active references mapped: `{report['active_reference_count']}`",
        "",
        "## Bottom Line",
        "",
        "- Active reference docs are not automatically runtime.",
        "- Two bounded overlay consumers are now explicitly mapped: CTI rotation and ETHBTC core/hedge.",
        "- Both overlays are context/evidence only: no entry permission, no orders, no keys.",
        "- The next useful extraction target is a BitEvo schema/template validator, not a live strategy.",
        "",
        "## Decision Counts",
        "",
    ]
    for key, value in report["decision_counts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Consumer Status Counts", ""])
    for key, value in report["consumer_status_counts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Runtime Map", ""])
    for item in report["items"]:
        consumer_paths = item["consumer"]["paths"]
        consumer_text = ", ".join(f"`{path}`" for path in consumer_paths) if consumer_paths else "-"
        lines.append(f"### `{item['path']}`")
        lines.append(f"- decision: `{item['runtime_decision']}`")
        lines.append(f"- consumer_status: `{item['consumer_status']}`")
        lines.append(f"- consumer: {consumer_text}")
        lines.append(f"- boundary: {item['risk_boundary']}")
        lines.append(f"- next: {item['next_action']}")
        lines.append("")
    lines.extend(
        [
            "## What To Code Next",
            "",
            "1. Keep CTI and ETHBTC overlays as dashboard metrics only.",
            "2. Add a BitEvo template/schema validator so alert contracts are stricter.",
            "3. Extract BTC Trend-Flex gate checks only after columns and data source are fixed.",
            "4. Do not promote HL3/manual visual logic into code until measurable inputs are defined.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Map active reference docs/configs to runtime consumers and next actions.")
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT))
    parser.add_argument("--out-prefix", default="docs/ACTIVE_REFERENCE_RUNTIME_EXTRACTION_2026-06-02")
    args = parser.parse_args()

    audit_path = Path(args.audit)
    if not audit_path.is_absolute():
        audit_path = ROOT / audit_path
    audit = read_json(audit_path)
    report = build_report(audit)

    out_prefix = ROOT / args.out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "active_reference_count": report["active_reference_count"],
                "decision_counts": report["decision_counts"],
                "consumer_status_counts": report["consumer_status_counts"],
                "out_json": rel(out_prefix.with_suffix(".json")),
                "out_md": rel(out_prefix.with_suffix(".md")),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
