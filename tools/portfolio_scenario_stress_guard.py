#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
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
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc)}
    return payload if isinstance(payload, dict) else {"_read_error": "json_root_not_object"}


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def r6(value: float) -> float:
    return round(value, 6)


def validate(policy: dict[str, Any], portfolio: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if policy.get("_read_error"):
        errors.append(f"policy_read_error:{policy['_read_error']}")
    if portfolio.get("_read_error"):
        errors.append(f"portfolio_read_error:{portfolio['_read_error']}")
    thresholds = policy.get("thresholds") if isinstance(policy.get("thresholds"), dict) else {}
    for field in ("max_stressed_mmr_pct", "max_equity_drawdown_pct", "min_stressed_equity_usd"):
        value = number(thresholds.get(field))
        if value is None or value < 0:
            errors.append(f"invalid_threshold:{field}")
    starting_equity = number(portfolio.get("starting_equity_usd"))
    if starting_equity is None or starting_equity <= 0:
        errors.append("invalid_starting_equity_usd")

    collaterals = portfolio.get("collateral") if isinstance(portfolio.get("collateral"), list) else None
    positions = portfolio.get("derivative_positions") if isinstance(portfolio.get("derivative_positions"), list) else None
    scenarios = policy.get("scenarios") if isinstance(policy.get("scenarios"), list) else None
    if collaterals is None:
        errors.append("collateral_not_list")
        collaterals = []
    if positions is None:
        errors.append("derivative_positions_not_list")
        positions = []
    if not scenarios:
        errors.append("scenarios_missing")
        scenarios = []

    assets: set[str] = set()
    for index, item in enumerate(collaterals):
        if not isinstance(item, dict):
            errors.append(f"collateral_not_object:{index}")
            continue
        asset = str(item.get("asset") or "").upper()
        market_value = number(item.get("market_value_usd"))
        haircut = number(item.get("haircut_pct"))
        if not asset:
            errors.append(f"collateral_asset_missing:{index}")
        else:
            assets.add(asset)
        if market_value is None or market_value < 0:
            errors.append(f"invalid_collateral_market_value:{index}")
        if haircut is None or not 0 <= haircut <= 100:
            errors.append(f"invalid_collateral_haircut:{index}")

    for index, item in enumerate(positions):
        if not isinstance(item, dict):
            errors.append(f"position_not_object:{index}")
            continue
        asset = str(item.get("underlying") or "").upper()
        quantity = number(item.get("quantity"))
        mark = number(item.get("mark_price_usd"))
        multiplier = number(item.get("contract_multiplier"))
        maintenance_rate = number(item.get("maintenance_rate_pct"))
        if not asset:
            errors.append(f"position_underlying_missing:{index}")
        else:
            assets.add(asset)
        if quantity is None or quantity == 0:
            errors.append(f"invalid_position_quantity:{index}")
        if mark is None or mark <= 0:
            errors.append(f"invalid_position_mark:{index}")
        if multiplier is None or multiplier <= 0:
            errors.append(f"invalid_contract_multiplier:{index}")
        if maintenance_rate is None or not 0 <= maintenance_rate <= 100:
            errors.append(f"invalid_maintenance_rate:{index}")

    scenario_ids: set[str] = set()
    for index, item in enumerate(scenarios):
        if not isinstance(item, dict):
            errors.append(f"scenario_not_object:{index}")
            continue
        scenario_id = str(item.get("id") or "")
        returns = item.get("returns_pct") if isinstance(item.get("returns_pct"), dict) else {}
        if not scenario_id:
            errors.append(f"scenario_id_missing:{index}")
        elif scenario_id in scenario_ids:
            errors.append(f"duplicate_scenario_id:{scenario_id}")
        scenario_ids.add(scenario_id)
        for asset in sorted(assets):
            shock = number(returns.get(asset))
            if shock is None:
                errors.append(f"scenario_asset_missing_or_invalid:{scenario_id}:{asset}")
            elif shock <= -100:
                errors.append(f"scenario_return_below_floor:{scenario_id}:{asset}")
    return sorted(set(errors))


def evaluate_scenario(
    scenario: dict[str, Any],
    portfolio: dict[str, Any],
    thresholds: dict[str, Any],
    baseline_haircut_loss: float,
) -> dict[str, Any]:
    returns = {str(key).upper(): float(value) / 100.0 for key, value in scenario["returns_pct"].items()}
    collateral_rows: list[dict[str, Any]] = []
    collateral_pnl = 0.0
    for item in portfolio["collateral"]:
        asset = str(item["asset"]).upper()
        market_value = float(item["market_value_usd"])
        shock = returns[asset]
        pnl = market_value * shock
        collateral_pnl += pnl
        collateral_rows.append(
            {
                "asset": asset,
                "return_pct": r6(shock * 100.0),
                "market_value_usd": r6(market_value),
                "haircut_loss_usd": r6(market_value * float(item["haircut_pct"]) / 100.0),
                "market_pnl_usd": r6(pnl),
            }
        )

    position_rows: list[dict[str, Any]] = []
    derivative_pnl = 0.0
    stressed_maintenance_margin = 0.0
    for item in portfolio["derivative_positions"]:
        asset = str(item["underlying"]).upper()
        quantity = float(item["quantity"])
        mark = float(item["mark_price_usd"])
        multiplier = float(item["contract_multiplier"])
        shock = returns[asset]
        pnl = quantity * mark * multiplier * shock
        stressed_price = mark * (1.0 + shock)
        stressed_notional = abs(quantity * stressed_price * multiplier)
        maintenance_margin = stressed_notional * float(item["maintenance_rate_pct"]) / 100.0
        derivative_pnl += pnl
        stressed_maintenance_margin += maintenance_margin
        position_rows.append(
            {
                "symbol": item.get("symbol"),
                "underlying": asset,
                "quantity": quantity,
                "return_pct": r6(shock * 100.0),
                "stressed_price_usd": r6(stressed_price),
                "position_pnl_usd": r6(pnl),
                "stressed_notional_usd": r6(stressed_notional),
                "stressed_maintenance_margin_usd": r6(maintenance_margin),
            }
        )

    starting_equity = float(portfolio["starting_equity_usd"])
    stressed_equity = starting_equity - baseline_haircut_loss + collateral_pnl + derivative_pnl
    drawdown_pct = max(0.0, (starting_equity - stressed_equity) / starting_equity * 100.0)
    stressed_mmr_pct = stressed_maintenance_margin / stressed_equity * 100.0 if stressed_equity > 0 else None
    checks = {
        "stressed_equity_positive": stressed_equity > 0,
        "stressed_equity_floor": stressed_equity >= float(thresholds["min_stressed_equity_usd"]),
        "drawdown_within_limit": drawdown_pct <= float(thresholds["max_equity_drawdown_pct"]),
        "mmr_within_limit": stressed_mmr_pct is not None
        and stressed_mmr_pct <= float(thresholds["max_stressed_mmr_pct"]),
    }
    return {
        "id": scenario["id"],
        "returns_pct": scenario["returns_pct"],
        "collateral": collateral_rows,
        "positions": position_rows,
        "summary": {
            "baseline_haircut_loss_usd": r6(baseline_haircut_loss),
            "collateral_market_pnl_usd": r6(collateral_pnl),
            "derivative_pnl_usd": r6(derivative_pnl),
            "stressed_adjusted_equity_usd": r6(stressed_equity),
            "equity_drawdown_pct": r6(drawdown_pct),
            "stressed_maintenance_margin_usd": r6(stressed_maintenance_margin),
            "stressed_mmr_pct": r6(stressed_mmr_pct) if stressed_mmr_pct is not None else None,
        },
        "checks": checks,
        "pass": all(checks.values()),
    }


def build_report(policy_path: Path, portfolio_path: Path) -> dict[str, Any]:
    policy = read_json(policy_path)
    portfolio = read_json(portfolio_path)
    errors = validate(policy, portfolio)
    thresholds = policy.get("thresholds") if isinstance(policy.get("thresholds"), dict) else {}
    scenarios: list[dict[str, Any]] = []
    baseline_haircut_loss = 0.0
    if not errors:
        baseline_haircut_loss = sum(
            float(item["market_value_usd"]) * float(item["haircut_pct"]) / 100.0
            for item in portfolio["collateral"]
        )
        scenarios = [
            evaluate_scenario(item, portfolio, thresholds, baseline_haircut_loss)
            for item in policy["scenarios"]
        ]
    breached = [item["id"] for item in scenarios if not item["pass"]]
    if errors:
        decision = "portfolio_scenario_stress_guard_invalid_input_blocked"
    elif breached:
        decision = "portfolio_scenario_stress_guard_breached_blocked"
    else:
        decision = "portfolio_scenario_stress_guard_passed_research_only"
    worst = None
    if scenarios:
        worst = max(
            scenarios,
            key=lambda item: (
                float(item["summary"]["equity_drawdown_pct"]),
                float(item["summary"]["stressed_mmr_pct"] or 1e12),
            ),
        )["id"]
    return {
        "generated_at": now_iso(),
        "tool": "tools/portfolio_scenario_stress_guard.py",
        "decision": decision,
        "can_trade": False,
        "inputs": {
            "policy": portable(policy_path),
            "policy_sha256": sha256_file(policy_path),
            "portfolio": portable(portfolio_path),
            "portfolio_sha256": sha256_file(portfolio_path),
        },
        "portfolio_snapshot": {
            "snapshot_id": portfolio.get("snapshot_id"),
            "snapshot_kind": portfolio.get("snapshot_kind"),
            "generated_at": portfolio.get("generated_at"),
            "source_mode": portfolio.get("source_mode"),
            "synthetic": portfolio.get("synthetic"),
            "can_trade": portfolio.get("can_trade"),
        },
        "model_boundary": {
            "classification": "independent_offline_linear_portfolio_stress_guard",
            "exchange_wce_replica": False,
            "options_or_nonlinear_products_supported": False,
            "private_api_consumer": False,
            "paper_execution_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "assumptions": policy.get("assumptions"),
        "thresholds": thresholds,
        "starting_equity_usd": portfolio.get("starting_equity_usd"),
        "baseline_haircut_loss_usd": r6(baseline_haircut_loss),
        "validation_errors": errors,
        "scenarios": scenarios,
        "summary": {
            "scenarios": len(scenarios),
            "passed": sum(1 for item in scenarios if item["pass"]),
            "breached": len(breached),
            "breached_scenarios": breached,
            "worst_scenario": worst,
        },
        "next_action": "review breached exposure before any paper-design discussion"
        if breached or errors
        else "retain as a mandatory offline risk check; this pass grants no trading permission",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Portfolio Scenario Stress Guard",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        "- Independent conservative approximation; not a Bybit WCE replica.",
        "- Linear derivatives only; no options or private account API.",
        "",
        "## Scenarios",
        "",
        "| Scenario | Equity USD | Drawdown % | MM USD | MMR % | Pass |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in report.get("scenarios") or []:
        summary = item["summary"]
        lines.append(
            f"| `{item['id']}` | `{summary['stressed_adjusted_equity_usd']}` | "
            f"`{summary['equity_drawdown_pct']}` | `{summary['stressed_maintenance_margin_usd']}` | "
            f"`{summary['stressed_mmr_pct']}` | `{item['pass']}` |"
        )
    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- Errors: `{report.get('validation_errors')}`",
            f"- Breached scenarios: `{report.get('summary', {}).get('breached_scenarios')}`",
            f"- Worst scenario: `{report.get('summary', {}).get('worst_scenario')}`",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline fail-closed linear portfolio scenario stress guard")
    parser.add_argument("--policy", default="configs/PORTFOLIO_SCENARIO_STRESS_POLICY_v1.json")
    parser.add_argument("--portfolio", default="configs/PORTFOLIO_SCENARIO_STRESS_SAMPLE.json")
    parser.add_argument("--out-prefix", default="docs/PORTFOLIO_SCENARIO_STRESS_GUARD_SMOKE_2026-07-12")
    args = parser.parse_args()
    report = build_report(resolve_path(args.policy), resolve_path(args.portfolio))
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "summary": report["summary"],
                "validation_errors": report["validation_errors"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["decision"] == "portfolio_scenario_stress_guard_passed_research_only" else 2


if __name__ == "__main__":
    raise SystemExit(main())
