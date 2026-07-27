#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BITEVO = ROOT / "configs" / "BitEvo_composite_config.json"
DEFAULT_SMARTMONEY = ROOT / "smartmoney" / "SmartMoney_Alerts_Config.json"
VALID_TF = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1D", "1w", "1W"}
EXPECTED_SMARTMONEY_LINKS = {
    "diver_confirmed": "rsi_ao_div",
    "sweep_return": "liquidity_sweep_eq",
    "smt_ethbtc_anomaly": "smt_btc_eth",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_positive_number(value: Any, path: str, errors: list[str], *, allow_zero: bool = False) -> None:
    if not is_number(value):
        errors.append(f"{path}: expected number")
        return
    if allow_zero:
        if value < 0:
            errors.append(f"{path}: expected >= 0")
    elif value <= 0:
        errors.append(f"{path}: expected > 0")


def validate_timeframes(values: Any, path: str, errors: list[str], warnings: list[str]) -> list[str]:
    if not isinstance(values, list) or not values:
        errors.append(f"{path}: expected non-empty list")
        return []
    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str):
            errors.append(f"{path}: timeframe must be string")
            continue
        normalized.append(item)
        if item not in VALID_TF:
            warnings.append(f"{path}: non-standard timeframe {item!r}")
    return normalized


def validate_tp_scheme(values: Any, path: str, errors: list[str]) -> None:
    if not isinstance(values, list) or not values:
        errors.append(f"{path}: expected non-empty tp list")
        return
    previous = 0.0
    for index, item in enumerate(values):
        validate_positive_number(item, f"{path}[{index}]", errors)
        if is_number(item) and item < previous:
            errors.append(f"{path}: should be ascending")
        if is_number(item):
            previous = float(item)


def validate_bit_evo_registry(payload: Any) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    setup_summaries: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["root: expected object"], "warnings": [], "setups": []}

    for key in ("datasources", "setups", "composite_filters", "risk_manager", "scoring"):
        if key not in payload:
            errors.append(f"{key}: missing top-level key")

    if not isinstance(payload.get("datasources"), dict):
        errors.append("datasources: expected object")

    risk_manager = payload.get("risk_manager")
    if not isinstance(risk_manager, dict):
        errors.append("risk_manager: expected object")
        risk_manager = {}
    else:
        for key in ("account_risk_pct_max", "per_trade_risk_pct_max"):
            if key in risk_manager:
                validate_positive_number(risk_manager[key], f"risk_manager.{key}", errors)
        if (
            is_number(risk_manager.get("per_trade_risk_pct_max"))
            and is_number(risk_manager.get("account_risk_pct_max"))
            and risk_manager["per_trade_risk_pct_max"] > risk_manager["account_risk_pct_max"]
        ):
            errors.append("risk_manager.per_trade_risk_pct_max: cannot exceed account_risk_pct_max")

    scoring = payload.get("scoring")
    if not isinstance(scoring, dict):
        errors.append("scoring: expected object")
    else:
        weights = scoring.get("weights")
        if not isinstance(weights, dict) or not weights:
            errors.append("scoring.weights: expected non-empty object")
        else:
            total = 0.0
            for key, value in weights.items():
                validate_positive_number(value, f"scoring.weights.{key}", errors, allow_zero=True)
                if is_number(value):
                    total += float(value)
            if abs(total - 1.0) > 0.001:
                warnings.append(f"scoring.weights: sum is {total:.6f}, expected 1.0")
        thresholds = scoring.get("thresholds")
        if not isinstance(thresholds, dict):
            errors.append("scoring.thresholds: expected object")
        else:
            for key in ("enter", "add", "exit"):
                if key not in thresholds:
                    errors.append(f"scoring.thresholds.{key}: missing")
                else:
                    validate_positive_number(thresholds[key], f"scoring.thresholds.{key}", errors, allow_zero=True)
            if all(is_number(thresholds.get(key)) for key in ("exit", "enter", "add")):
                if not thresholds["exit"] < thresholds["enter"] < thresholds["add"]:
                    errors.append("scoring.thresholds: expected exit < enter < add")

    setup_ids: set[str] = set()
    setups = payload.get("setups")
    if not isinstance(setups, list) or not setups:
        errors.append("setups: expected non-empty list")
    else:
        for index, setup in enumerate(setups):
            setup_path = f"setups[{index}]"
            if not isinstance(setup, dict):
                errors.append(f"{setup_path}: expected object")
                continue
            setup_id = setup.get("setup_id")
            if not isinstance(setup_id, str) or not setup_id:
                errors.append(f"{setup_path}.setup_id: expected non-empty string")
                setup_id = f"unknown_{index}"
            elif setup_id in setup_ids:
                errors.append(f"{setup_path}.setup_id: duplicate {setup_id!r}")
            setup_ids.add(str(setup_id))
            if not isinstance(setup.get("enabled"), bool):
                errors.append(f"{setup_path}.enabled: expected boolean")
            tfs = validate_timeframes(setup.get("timeframes"), f"{setup_path}.timeframes", errors, warnings)
            if not isinstance(setup.get("params"), dict):
                errors.append(f"{setup_path}.params: expected object")
            if not isinstance(setup.get("filters"), dict):
                errors.append(f"{setup_path}.filters: expected object")
            risk = setup.get("risk")
            if not isinstance(risk, dict):
                errors.append(f"{setup_path}.risk: expected object")
                risk = {}
            else:
                if "tp_scheme" in risk:
                    validate_tp_scheme(risk["tp_scheme"], f"{setup_path}.risk.tp_scheme", errors)
                else:
                    errors.append(f"{setup_path}.risk.tp_scheme: missing")
                if "max_risk_per_trade_pct" in risk:
                    validate_positive_number(risk["max_risk_per_trade_pct"], f"{setup_path}.risk.max_risk_per_trade_pct", errors)
                    if (
                        is_number(risk.get("max_risk_per_trade_pct"))
                        and is_number(risk_manager.get("per_trade_risk_pct_max"))
                        and risk["max_risk_per_trade_pct"] > risk_manager["per_trade_risk_pct_max"]
                    ):
                        errors.append(f"{setup_path}.risk.max_risk_per_trade_pct: exceeds risk_manager limit")
                else:
                    errors.append(f"{setup_path}.risk.max_risk_per_trade_pct: missing")
            setup_summaries.append(
                {
                    "setup_id": setup_id,
                    "enabled": setup.get("enabled"),
                    "timeframes": tfs,
                    "has_params": isinstance(setup.get("params"), dict),
                    "has_filters": isinstance(setup.get("filters"), dict),
                    "has_risk": isinstance(setup.get("risk"), dict),
                }
            )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "setups": setup_summaries,
        "setup_ids": sorted(setup_ids),
    }


def validate_smartmoney_registry(payload: Any, bit_evo_setup_ids: set[str]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    alert_summaries: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["root: expected object"], "warnings": [], "alerts": []}

    if not isinstance(payload.get("version"), str):
        errors.append("version: expected string")
    validate_timeframes(payload.get("timeframes"), "timeframes", errors, warnings)

    filters_global = payload.get("filters_global")
    if not isinstance(filters_global, dict):
        errors.append("filters_global: expected object")
    else:
        for key in ("cooldown_min", "max_concurrent_per_symbol", "latency_budget_ms"):
            if key not in filters_global:
                errors.append(f"filters_global.{key}: missing")
            else:
                validate_positive_number(filters_global[key], f"filters_global.{key}", errors)

    alerts = payload.get("alerts")
    if not isinstance(alerts, dict) or not alerts:
        errors.append("alerts: expected non-empty object")
    else:
        for alert_id, alert in alerts.items():
            path = f"alerts.{alert_id}"
            if not isinstance(alert_id, str) or not alert_id:
                errors.append("alerts: alert id must be non-empty string")
            if not isinstance(alert, dict):
                errors.append(f"{path}: expected object")
                continue
            has_if = isinstance(alert.get("if"), dict)
            has_risk = isinstance(alert.get("risk"), dict)
            has_action = isinstance(alert.get("action"), str)
            if not has_if:
                errors.append(f"{path}.if: expected object")
            if not (has_risk or has_action):
                errors.append(f"{path}: expected risk object or action string")
            linked_setup = EXPECTED_SMARTMONEY_LINKS.get(alert_id)
            if linked_setup and linked_setup not in bit_evo_setup_ids:
                warnings.append(f"{path}: expected linked BitEvo setup {linked_setup!r} not found")
            alert_summaries.append(
                {
                    "alert_id": alert_id,
                    "has_if": has_if,
                    "has_risk": has_risk,
                    "has_action": has_action,
                    "linked_setup": linked_setup,
                }
            )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "alerts": alert_summaries,
    }


def build_report(bit_evo_path: Path, smartmoney_path: Path) -> dict[str, Any]:
    bit_evo_payload = read_json(bit_evo_path)
    bit_evo = validate_bit_evo_registry(bit_evo_payload)
    setup_ids = set(bit_evo.get("setup_ids", []))
    smartmoney_payload = read_json(smartmoney_path)
    smartmoney = validate_smartmoney_registry(smartmoney_payload, setup_ids)
    cross_warnings: list[str] = []
    smart_alerts = {item.get("alert_id") for item in smartmoney.get("alerts", [])}
    if "funding_hot" in smart_alerts and not any("funding" in str(setup).lower() for setup in bit_evo.get("setups", [])):
        cross_warnings.append("funding_hot exists as global SmartMoney alert; BitEvo funding is represented as filters, not a dedicated setup")

    return {
        "generated_at": now_iso(),
        "ok": bool(bit_evo["ok"] and smartmoney["ok"]),
        "inputs": {
            "bit_evo": rel(bit_evo_path),
            "smartmoney": rel(smartmoney_path),
        },
        "bit_evo": bit_evo,
        "smartmoney": smartmoney,
        "cross_warnings": cross_warnings,
        "policy": {
            "registry_validation_only": True,
            "detectors_not_proven": True,
            "no_alert_emission": True,
            "no_trade_permission": True,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BitEvo / SmartMoney Registry Validation",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Overall: `{'PASS' if report['ok'] else 'FAIL'}`",
        "",
        "## Scope",
        "",
        "- Validates registry/config structure only.",
        "- Does not prove that any detector is implemented.",
        "- Does not emit alerts, send webhooks or allow trades.",
        "",
        "## BitEvo Composite Registry",
        "",
        f"- file: `{report['inputs']['bit_evo']}`",
        f"- status: `{'PASS' if report['bit_evo']['ok'] else 'FAIL'}`",
        f"- setups: `{len(report['bit_evo'].get('setups', []))}`",
    ]
    for setup in report["bit_evo"].get("setups", []):
        lines.append(f"- setup `{setup['setup_id']}` enabled=`{setup['enabled']}` tfs=`{','.join(setup['timeframes'])}`")
    for warning in report["bit_evo"].get("warnings", []):
        lines.append(f"- warning: {warning}")
    for error in report["bit_evo"].get("errors", []):
        lines.append(f"- error: {error}")

    lines.extend(
        [
            "",
            "## SmartMoney Alert Registry",
            "",
            f"- file: `{report['inputs']['smartmoney']}`",
            f"- status: `{'PASS' if report['smartmoney']['ok'] else 'FAIL'}`",
            f"- alerts: `{len(report['smartmoney'].get('alerts', []))}`",
        ]
    )
    for alert in report["smartmoney"].get("alerts", []):
        linked = alert.get("linked_setup") or "-"
        mode = "risk" if alert.get("has_risk") else "action"
        lines.append(f"- alert `{alert['alert_id']}` mode=`{mode}` linked_setup=`{linked}`")
    for warning in report["smartmoney"].get("warnings", []):
        lines.append(f"- warning: {warning}")
    for error in report["smartmoney"].get("errors", []):
        lines.append(f"- error: {error}")

    if report.get("cross_warnings"):
        lines.extend(["", "## Cross Notes", ""])
        for warning in report["cross_warnings"]:
            lines.append(f"- {warning}")

    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- PASS here means registry readiness only.",
            "- A setup still needs a detector, data source, backtest/paper proof and risk gate before it can become live logic.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate BitEvo and SmartMoney setup/alert registries.")
    parser.add_argument("--bitevo", default=str(DEFAULT_BITEVO))
    parser.add_argument("--smartmoney", default=str(DEFAULT_SMARTMONEY))
    parser.add_argument("--out-prefix", default="docs/BITEVO_REGISTRY_VALIDATION_2026-06-02")
    args = parser.parse_args()

    bit_evo_path = Path(args.bitevo)
    smartmoney_path = Path(args.smartmoney)
    if not bit_evo_path.is_absolute():
        bit_evo_path = ROOT / bit_evo_path
    if not smartmoney_path.is_absolute():
        smartmoney_path = ROOT / smartmoney_path
    report = build_report(bit_evo_path, smartmoney_path)

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
                "bitevo_setups": len(report["bit_evo"].get("setups", [])),
                "smartmoney_alerts": len(report["smartmoney"].get("alerts", [])),
                "bitevo_errors": len(report["bit_evo"].get("errors", [])),
                "smartmoney_errors": len(report["smartmoney"].get("errors", [])),
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
