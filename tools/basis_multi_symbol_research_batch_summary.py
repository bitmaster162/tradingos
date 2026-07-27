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


def resolve_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: str | Path) -> dict[str, Any]:
    p = resolve_path(path)
    if not p.is_file():
        return {"_missing": portable(p)}
    try:
        payload = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(p)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": portable(p)}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def nested(data: dict[str, Any], *keys: str) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def failed_checks(gate: Any) -> list[str]:
    checks = gate.get("checks") if isinstance(gate, dict) else {}
    return [str(name) for name, passed in checks.items() if not passed] if isinstance(checks, dict) else []


def carry_or_shock_summary(name: str, path: str, data: dict[str, Any]) -> dict[str, Any]:
    selected = data.get("selected_on_train") if isinstance(data.get("selected_on_train"), dict) else {}
    train = selected.get("train") if isinstance(selected.get("train"), dict) else {}
    validation = data.get("validation") if isinstance(data.get("validation"), dict) else {}
    return {
        "name": name,
        "path": path,
        "family": data.get("family"),
        "decision": data.get("decision"),
        "can_trade": data.get("can_trade") is True,
        "tested": nested(data, "search", "tested"),
        "train_qualified": nested(data, "search", "train_qualified"),
        "selected": nested(selected, "config", "strategy_id"),
        "train": nested(train, "summary") or {},
        "validation": validation.get("summary") if isinstance(validation.get("summary"), dict) else {},
        "validation_pass": nested(data, "validation_gate", "pass"),
        "validation_failed_checks": failed_checks(data.get("validation_gate")),
        "oos_opened": bool(data.get("oos_opened")),
        "oos_gate": data.get("oos_gate"),
        "next_action": data.get("next_action"),
    }


def dispersion_summary(path: str, data: dict[str, Any]) -> dict[str, Any]:
    stage_train = nested(data, "stages", "train") or {}
    evaluation = stage_train.get("evaluation") if isinstance(stage_train.get("evaluation"), dict) else {}
    return {
        "name": "basis_dispersion_reversion_multi_symbol",
        "path": path,
        "family": "BASIS_DISPERSION_REVERSION_MULTI_SYMBOL_1H",
        "decision": data.get("decision"),
        "can_trade": data.get("can_trade") is True,
        "tested": nested(data, "search", "configs_tested"),
        "train_qualified": nested(data, "search", "train_qualified_configs"),
        "selected": nested(data, "selected_config", "config_id"),
        "train": evaluation.get("summary") if isinstance(evaluation.get("summary"), dict) else {},
        "validation": {},
        "validation_pass": False,
        "validation_failed_checks": nested(stage_train, "failures") or [],
        "oos_opened": False,
        "oos_gate": None,
        "next_action": "reject_or_research_new_mechanism_without_reusing_opened_stage",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Basis Multi-Symbol Research Batch Summary",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        "",
        "## Coverage",
        "",
    ]
    coverage = report["coverage"]
    lines.extend(
        [
            f"- Coverage decision: `{coverage.get('decision')}`.",
            f"- Complete symbols: `{coverage.get('complete_symbols')}`.",
            f"- Panel rows: `{coverage.get('panel_rows')}`.",
            f"- Panel path: `{coverage.get('panel_path')}`.",
            "",
            "## Families",
            "",
            "| Family | Decision | Tested | Train qualified | Selected | Validation | OOS opened | Failed checks |",
            "|---|---|---:|---:|---|---|---|---|",
        ]
    )
    for family in report["families"]:
        lines.append(
            "| `{family}` | `{decision}` | `{tested}` | `{train_qualified}` | `{selected}` | `{validation}` | `{oos}` | `{failed}` |".format(
                family=family["name"],
                decision=family["decision"],
                tested=family.get("tested"),
                train_qualified=family.get("train_qualified"),
                selected=family.get("selected"),
                validation="pass" if family.get("validation_pass") else "fail/not_run",
                oos=family.get("oos_opened"),
                failed=", ".join(family.get("validation_failed_checks") or []),
            )
        )
    lines.extend(["", "## Key Readout", ""])
    for item in report["readout"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Research-only batch.",
            "- OOS remains unopened when validation fails.",
            "- No paper/live execution permission.",
            "- `can_trade=false` is preserved.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize the multi-symbol basis research batch.")
    parser.add_argument("--coverage", default="docs/MULTI_SYMBOL_BASIS_COVERAGE_2026-07-02.json")
    parser.add_argument("--carry", default="docs/BASIS_FUNDING_CARRY_MULTI_SYMBOL_NESTED_HOLDOUT_2026-07-02.json")
    parser.add_argument("--shock", default="docs/BASIS_SHOCK_REVERSION_MULTI_SYMBOL_NESTED_HOLDOUT_2026-07-02.json")
    parser.add_argument("--dispersion", default="docs/BASIS_DISPERSION_REVERSION_MULTI_SYMBOL_NESTED_HOLDOUT_2026-07-02.json")
    parser.add_argument("--out-prefix", default="docs/BASIS_MULTI_SYMBOL_RESEARCH_BATCH_SUMMARY_2026-07-02")
    args = parser.parse_args()

    coverage_raw = read_json(args.coverage)
    coverage = {
        "decision": coverage_raw.get("decision"),
        "complete_symbols": nested(coverage_raw, "summary", "complete_symbols"),
        "panel_rows": nested(coverage_raw, "summary", "panel_rows"),
        "panel_path": nested(coverage_raw, "summary", "panel_path"),
        "can_trade": coverage_raw.get("can_trade") is True,
    }
    carry = carry_or_shock_summary("basis_funding_carry_multi_symbol", args.carry, read_json(args.carry))
    shock = carry_or_shock_summary("basis_shock_reversion_multi_symbol", args.shock, read_json(args.shock))
    dispersion = dispersion_summary(args.dispersion, read_json(args.dispersion))
    families = [carry, shock, dispersion]

    promotable = [item for item in families if item.get("oos_opened") and item.get("can_trade")]
    if promotable:
        decision = "basis_multi_symbol_batch_unexpected_trade_candidate"
        next_action = "manual audit required before any promotion"
    else:
        decision = "basis_multi_symbol_batch_rejected_research_only"
        next_action = "Do not retune opened reports. If continuing basis research, preregister a genuinely new mechanism or wait for more/live-real data class evidence."

    readout = [
        "Input coverage is sufficient for research: 4 complete symbols and a 190k-row panel.",
        "Funding carry found train-qualified candidates, but selected validation produced zero trades, so OOS stayed closed.",
        "Basis shock reversion had a plausible validation mean, but failed minimum validation trades and cost-stress gates, so OOS stayed closed.",
        "Basis dispersion reversion failed train gates; validation and OOS stayed closed.",
    ]
    report = {
        "generated_at": now_iso(),
        "tool": "tools/basis_multi_symbol_research_batch_summary.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "coverage": coverage,
        "families": families,
        "readout": readout,
        "next_action": next_action,
        "boundary": {
            "research_only": True,
            "uses_private_credentials": False,
            "sends_orders": False,
            "opens_paper_entries": False,
            "can_trade": False,
        },
    }
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "decision": decision,
        "families": len(families),
        "promotable": len(promotable),
        "out": portable(out.with_suffix(".json")),
        "can_trade": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
