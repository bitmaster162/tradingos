#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from string import Formatter
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "bitevo" / "schemas" / "alert.schema.json"
DEFAULT_TEMPLATE = ROOT / "bitevo" / "templates" / "telegram_message.txt"
DEFAULT_ALERTS = [
    ROOT / "bitevo" / "examples" / "alert_entry_example.json",
    ROOT / "bitevo" / "examples" / "alert_cancel_example.json",
]
DEFAULT_SCHEMA_FILES = [
    ROOT / "bitevo" / "schemas" / "alert.schema.json",
    ROOT / "bitevo" / "schemas" / "kpi.schema.json",
    ROOT / "bitevo" / "schemas" / "quality_gate.schema.json",
    ROOT / "bitevo" / "schemas" / "trade_log.schema.json",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def has_mojibake(text: str) -> bool:
    return any(marker in text for marker in ("Ã", "Ð", "Ñ", "â", "ï¿½", "�"))


def iter_payloads(path: Path) -> list[tuple[int, Any]]:
    if path.suffix.lower() == ".jsonl":
        rows: list[tuple[int, Any]] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if line.strip():
                rows.append((line_no, json.loads(line)))
        return rows
    return [(1, read_json(path))]


def type_matches(value: Any, expected: Any) -> bool:
    expected_types = expected if isinstance(expected, list) else [expected]
    for item in expected_types:
        if item == "null" and value is None:
            return True
        if item == "object" and isinstance(value, dict):
            return True
        if item == "array" and isinstance(value, list):
            return True
        if item == "string" and isinstance(value, str):
            return True
        if item == "boolean" and isinstance(value, bool):
            return True
        if item == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if item == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
    return False


def validate_schema_subset(payload: Any, schema: dict[str, Any], prefix: str = "$") -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type is not None and not type_matches(payload, expected_type):
        errors.append(f"{prefix}: expected type {expected_type}, got {type(payload).__name__}")
        return errors

    enum = schema.get("enum")
    if enum is not None and payload not in enum:
        errors.append(f"{prefix}: expected one of {enum}, got {payload!r}")

    if isinstance(payload, dict):
        for field in schema.get("required", []):
            if field not in payload:
                errors.append(f"{prefix}.{field}: missing required field")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, value in payload.items():
                child = properties.get(key)
                if isinstance(child, dict):
                    errors.extend(validate_schema_subset(value, child, f"{prefix}.{key}"))

    if isinstance(payload, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(payload):
                errors.extend(validate_schema_subset(value, item_schema, f"{prefix}[{index}]"))
    return errors


def alert_context(alert: dict[str, Any]) -> dict[str, Any]:
    risk = alert.get("risk") if isinstance(alert.get("risk"), dict) else {}
    trigger = alert.get("trigger") if isinstance(alert.get("trigger"), dict) else {}
    metrics = alert.get("metrics") if isinstance(alert.get("metrics"), dict) else {}
    source = alert.get("source") if isinstance(alert.get("source"), dict) else {}
    return {
        "id": alert.get("id", ""),
        "ts": alert.get("ts", ""),
        "bar_ts": alert.get("bar_ts", ""),
        "decision_id": alert.get("decision_id", ""),
        "symbol": alert.get("symbol", ""),
        "tf": alert.get("tf", ""),
        "setup_id": alert.get("setup_id", ""),
        "score": alert.get("score", 0.0),
        "trigger_type": trigger.get("type", ""),
        "trigger_price": trigger.get("price", ""),
        "trigger_reason": trigger.get("reason", ""),
        "side": risk.get("side", ""),
        "entry": risk.get("entry", ""),
        "sl": risk.get("sl", ""),
        "tp_list": ", ".join(str(item) for item in risk.get("tp", [])),
        "r_list": ", ".join(str(item) for item in risk.get("r_multiplies", [])),
        "size_hint_pct": risk.get("size_hint_pct", ""),
        "invalidate_on": ", ".join(str(item) for item in risk.get("invalidate_on", [])) or "-",
        "filters_passed": ", ".join(str(item) for item in alert.get("filters_passed", [])) or "-",
        "funding_ap_7dma": metrics.get("funding_ap_7dma", 0.0),
        "oi_delta_pct_1h": metrics.get("oi_delta_pct_1h", 0.0),
        "liq_cluster_usd": metrics.get("liq_cluster_usd", 0.0),
        "dom_bias": metrics.get("dom_bias", ""),
        "lat_ms": alert.get("latency_ms", 0),
        "data_degraded": alert.get("data_degraded", False),
        "source_pipeline": source.get("pipeline", ""),
        "source_version": source.get("version", ""),
    }


def template_fields(template: str) -> list[str]:
    fields: list[str] = []
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name:
            fields.append(field_name.split(".", 1)[0].split("[", 1)[0])
    return sorted(set(fields))


def validate_alert_payload(alert: Any, schema: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(alert, dict):
        return {
            "ok": False,
            "event_type": None,
            "mode": "invalid",
            "errors": [f"alert must be object, got {type(alert).__name__}"],
            "warnings": [],
            "operational_notes": [],
        }

    trigger = alert.get("trigger")
    event_type = trigger.get("type") if isinstance(trigger, dict) else None
    if event_type not in {"entry", "add", "exit", "cancel"}:
        errors.append(f"trigger.type: unsupported or missing value {event_type!r}")

    if event_type in {"entry", "add"}:
        mode = "full_alert_schema"
        errors.extend(validate_schema_subset(alert, schema))
    elif event_type in {"exit", "cancel"}:
        mode = "minimal_lifecycle_event"
        for field in ("id", "trigger"):
            if field not in alert:
                errors.append(f"{field}: missing required field for lifecycle event")
        if isinstance(trigger, dict):
            if "price" not in trigger:
                errors.append("trigger.price: missing required field for lifecycle event")
            if "reason" not in trigger:
                warnings.append("trigger.reason: recommended for lifecycle event")
        warnings.append("lifecycle event is intentionally lighter than full entry schema")
    else:
        mode = "unknown"

    operational_notes = []
    if isinstance(alert.get("source"), dict) and ("pipeline" in alert["source"] or "version" in alert["source"]):
        operational_notes.append("source.pipeline/version present")
    if "bar_ts" in alert:
        operational_notes.append("bar_ts present")
    if "latency_ms" in alert:
        operational_notes.append("latency_ms present")
    if "decision_id" in alert:
        operational_notes.append("decision_id present")
    if isinstance(alert.get("metrics"), dict) and "dom_bias" in alert["metrics"]:
        operational_notes.append("metrics.dom_bias present")
    if "data_degraded" in alert:
        operational_notes.append("data_degraded present")

    return {
        "ok": not errors,
        "event_type": event_type,
        "mode": mode,
        "errors": errors,
        "warnings": warnings,
        "operational_notes": operational_notes,
    }


def validate_template(template_path: Path, entry_alert: dict[str, Any] | None) -> dict[str, Any]:
    template = template_path.read_text(encoding="utf-8-sig")
    fields = template_fields(template)
    context = alert_context(entry_alert or {})
    missing = [field for field in fields if field not in context]
    render_error = None
    rendered_preview = ""
    if not missing:
        try:
            rendered_preview = template.format(**context)
        except Exception as exc:  # noqa: BLE001
            render_error = str(exc)
    return {
        "path": rel(template_path),
        "ok": not missing and render_error is None,
        "mojibake_detected": has_mojibake(template),
        "placeholders": fields,
        "missing_context_fields": missing,
        "render_error": render_error,
        "rendered_preview": rendered_preview.strip(),
    }


def validate_schema_files(paths: list[Path]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in paths:
        errors: list[str] = []
        try:
            payload = read_json(path)
        except Exception as exc:  # noqa: BLE001
            payload = None
            errors.append(str(exc))
        if isinstance(payload, dict):
            if payload.get("type") != "object":
                errors.append("top-level type should be object")
            if "properties" not in payload:
                errors.append("missing properties")
            if path.name == "alert.schema.json":
                required = set(payload.get("required", []))
                for field in {"id", "trigger"}:
                    if field not in required:
                        errors.append(f"alert.schema missing required {field}")
        results.append({"path": rel(path), "ok": not errors, "errors": errors})
    return results


def build_report(alert_paths: list[Path], schema_path: Path, template_path: Path, schema_files: list[Path]) -> dict[str, Any]:
    schema = read_json(schema_path)
    schema_results = validate_schema_files(schema_files)
    alert_results: list[dict[str, Any]] = []
    first_entry_alert: dict[str, Any] | None = None

    for path in alert_paths:
        for line_no, payload in iter_payloads(path):
            result = validate_alert_payload(payload, schema)
            result.update({"path": rel(path), "line": line_no})
            alert_results.append(result)
            if first_entry_alert is None and isinstance(payload, dict):
                if (payload.get("trigger") or {}).get("type") in {"entry", "add"}:
                    first_entry_alert = payload

    template_result = validate_template(template_path, first_entry_alert)
    ok = all(item["ok"] for item in schema_results) and all(item["ok"] for item in alert_results) and template_result["ok"]
    return {
        "generated_at": now_iso(),
        "ok": ok,
        "schema": rel(schema_path),
        "schema_files": schema_results,
        "alerts": alert_results,
        "template": template_result,
        "policy": {
            "contract_check_only": True,
            "no_delivery": True,
            "no_webhook": True,
            "no_trade_permission": True,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BitEvo Contract Check",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Overall: `{'PASS' if report['ok'] else 'FAIL'}`",
        "",
        "## Scope",
        "",
        "- Checks JSON schema files parse and keep required contract shape.",
        "- Checks entry/add alerts against the full alert schema subset.",
        "- Checks cancel/exit lifecycle events against a lighter lifecycle contract.",
        "- Checks Telegram template placeholders against an entry-alert render context.",
        "- Does not send Telegram/webhook messages and does not create trade permission.",
        "",
        "## Schema Files",
        "",
    ]
    for item in report["schema_files"]:
        lines.append(f"- `{item['path']}`: `{'PASS' if item['ok'] else 'FAIL'}`")
        for error in item.get("errors", []):
            lines.append(f"  - {error}")
    lines.extend(["", "## Alert Examples", ""])
    for item in report["alerts"]:
        lines.append(f"- `{item['path']}` line `{item['line']}` `{item['event_type']}` `{item['mode']}`: `{'PASS' if item['ok'] else 'FAIL'}`")
        for warning in item.get("warnings", []):
            lines.append(f"  - warning: {warning}")
        for error in item.get("errors", []):
            lines.append(f"  - error: {error}")
    template = report["template"]
    lines.extend(
        [
            "",
            "## Telegram Template",
            "",
            f"- `{template['path']}`: `{'PASS' if template['ok'] else 'FAIL'}`",
            f"- mojibake_detected: `{template['mojibake_detected']}`",
            f"- placeholders: `{', '.join(template['placeholders'])}`",
        ]
    )
    if template.get("missing_context_fields"):
        lines.append(f"- missing context: `{', '.join(template['missing_context_fields'])}`")
    if template.get("render_error"):
        lines.append(f"- render error: `{template['render_error']}`")
    if template.get("rendered_preview"):
        lines.extend(["", "## Render Preview", "", "```text", template["rendered_preview"], "```"])
    lines.extend(["", "## Boundary", "", "- PASS means contract/render readiness only, not signal quality or trading readiness.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check BitEvo schemas, alert examples and Telegram template contract.")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA))
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--alert", action="append", default=[], help="Alert JSON/JSONL file. May be repeated.")
    parser.add_argument("--schema-file", action="append", default=[], help="Schema JSON file to parse/check. May be repeated.")
    parser.add_argument("--out-prefix", default="_dl/control_panel/BITEVO_CONTRACT_CHECK")
    args = parser.parse_args()

    schema_path = Path(args.schema)
    template_path = Path(args.template)
    if not schema_path.is_absolute():
        schema_path = ROOT / schema_path
    if not template_path.is_absolute():
        template_path = ROOT / template_path
    alert_paths = [Path(item) for item in args.alert] if args.alert else DEFAULT_ALERTS
    alert_paths = [path if path.is_absolute() else ROOT / path for path in alert_paths]
    schema_files = [Path(item) for item in args.schema_file] if args.schema_file else DEFAULT_SCHEMA_FILES
    schema_files = [path if path.is_absolute() else ROOT / path for path in schema_files]

    report = build_report(alert_paths, schema_path, template_path, schema_files)
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "alerts_checked": len(report["alerts"]),
                "schema_files_checked": len(report["schema_files"]),
                "template_ok": report["template"]["ok"],
                "template_mojibake_detected": report["template"]["mojibake_detected"],
                "out_json": rel(out_prefix.with_suffix(".json")),
                "out_md": rel(out_prefix.with_suffix(".md")),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
