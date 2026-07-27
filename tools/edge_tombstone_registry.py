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


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: str | Path) -> dict[str, Any]:
    p = resolve_path(path)
    if not p.exists():
        return {}
    try:
        value = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def tombstone(tid: str, family: str, reason: str, evidence: dict[str, Any], rule: str) -> dict[str, Any]:
    return {
        "id": tid,
        "family": family,
        "status": "tombstoned_no_retune",
        "can_trade": False,
        "reason": reason,
        "evidence": evidence,
        "reuse_rule": rule,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build edge tombstone registry for rejected strategy families")
    parser.add_argument("--devil-audit", default="docs/FULL_SYSTEM_DEVIL_AUDIT_2026-06-30_POST_LIQUIDATION_DQ_DEPLOY.json")
    parser.add_argument("--basis-shock", default="docs/BASIS_SHOCK_REVERSION_MULTI_SYMBOL_NESTED_HOLDOUT_2026-06-30.json")
    parser.add_argument("--basis-shock-funding-alignment", default="docs/BASIS_SHOCK_FUNDING_ALIGNMENT_MULTI_SYMBOL_NESTED_HOLDOUT_2026-07-02.json")
    parser.add_argument("--basis-carry", default="docs/BASIS_FUNDING_CARRY_MULTI_SYMBOL_NESTED_HOLDOUT_2026-06-30.json")
    parser.add_argument("--basis-dispersion", default="docs/BASIS_DISPERSION_REVERSION_MULTI_SYMBOL_NESTED_HOLDOUT_2026-06-30.json")
    parser.add_argument("--bybit-liquidation-forward-runner", default="docs/BYBIT_LIQUIDATION_FORWARD_GATE_RUNNER_2026-07-03.json")
    parser.add_argument("--bybit-liquidation-review-pack", default="docs/BYBIT_LIQUIDATION_FORWARD_REVIEW_PACK_2026-07-02.json")
    parser.add_argument("--bybit-liquidation-data-quality", default="docs/BYBIT_ALL_LIQUIDATION_DATA_QUALITY_2026-07-01.json")
    parser.add_argument("--bybit-side-semantics-audit", default="docs/BYBIT_LIQUIDATION_SIDE_SEMANTICS_AUDIT_2026-07-13.json")
    parser.add_argument("--cross-asset-residual", default="docs/CROSS_ASSET_COINTEGRATION_RESIDUAL_NESTED_HOLDOUT_2026-07-12.json")
    parser.add_argument("--spot-perp-divergence", default="docs/SPOT_PERP_DIVERGENCE_HARDENING_2026-06-03.json")
    parser.add_argument("--large-trade-tail-review", default="docs/LARGE_TRADE_TAIL_TERMINAL_REVIEW_2026-07-13.json")
    parser.add_argument("--out-prefix", default="docs/EDGE_TOMBSTONE_REGISTRY_2026-06-30")
    args = parser.parse_args()
    devil = read_json(args.devil_audit)
    basis_shock = read_json(args.basis_shock)
    basis_alignment = read_json(args.basis_shock_funding_alignment)
    basis_carry = read_json(args.basis_carry)
    basis_disp = read_json(args.basis_dispersion)
    bybit_runner = read_json(args.bybit_liquidation_forward_runner)
    bybit_review = read_json(args.bybit_liquidation_review_pack)
    bybit_dq = read_json(args.bybit_liquidation_data_quality)
    bybit_semantics = read_json(args.bybit_side_semantics_audit)
    cross_asset_residual = read_json(args.cross_asset_residual)
    spot_perp_divergence = read_json(args.spot_perp_divergence)
    large_trade_tail = read_json(args.large_trade_tail_review)
    findings = {item.get("id"): item for item in devil.get("findings", []) if isinstance(item, dict)}
    entries = [
        tombstone(
            "trend_mix_4h_oos_failed",
            "TREND_MIX_4H",
            "Train winner failed untouched calendar OOS.",
            findings.get("trend_historical_invalidation", {}).get("evidence", {}),
            "Do not retune on opened OOS. Only materially new preregistered trend mechanism may be tested.",
        ),
        tombstone(
            "range_refined_4h_oos_failed",
            "RANGE_REFINED_4H",
            "Range train edge failed untouched calendar OOS.",
            findings.get("range_historical_invalidation", {}).get("evidence", {}),
            "Do not revive same family/parameters. Only new preregistered range mechanism with untouched validation.",
        ),
        tombstone(
            "crowd_fade_1h_negative",
            "CROWD_FADE_1H",
            "Crowd fade failed broader historical validation and first independent forward evidence.",
            {
                "historical": findings.get("crowd_historical_invalidation", {}).get("evidence", {}),
                "forward": findings.get("crowd_forward_negative", {}).get("evidence", {}),
            },
            "Keep rejected unless materially better independent positioning data is acquired and preregistered.",
        ),
        tombstone(
            "spot_perp_standalone_divergence_failed",
            "SPOT_PERP_STANDALONE_DIVERGENCE",
            "Standalone spot-led momentum and perpetual-overextension fade both failed the historical hardening gate.",
            {
                "source": "docs/SPOT_PERP_DIVERGENCE_HARDENING_2026-06-03.json",
                "passed_count": spot_perp_divergence.get("passed_count"),
                "top_result": (
                    {
                        "strategy_id": spot_perp_divergence.get("top_results", [{}])[0].get("strategy_id"),
                        "summary": spot_perp_divergence.get("top_results", [{}])[0].get("summary"),
                        "gate": spot_perp_divergence.get("top_results", [{}])[0].get("gate"),
                    }
                    if spot_perp_divergence.get("top_results")
                    else {}
                ),
            },
            "Do not rename or retune the same return-divergence entry. Spot/perp divergence may only be reused as context or inside a materially different preregistered mechanism.",
        ),
        tombstone(
            "basis_shock_validation_failed",
            "BASIS_SHOCK_REVERSION",
            "Multi-symbol validation failed after train selection.",
            {"decision": basis_shock.get("decision"), "selected": basis_shock.get("selected_config")},
            "No validation retune. Only new mechanism allowed.",
        ),
        tombstone(
            "basis_shock_funding_alignment_validation_failed",
            "BASIS_SHOCK_FUNDING_ALIGNMENT",
            "Positive basis-shock plus funding-alignment candidate failed validation gate after train selection.",
            {
                "decision": basis_alignment.get("decision"),
                "selected": (
                    basis_alignment.get("selected_on_train", {}).get("strategy_id")
                    if isinstance(basis_alignment.get("selected_on_train"), dict)
                    else None
                ),
                "search": basis_alignment.get("search"),
                "validation_gate": basis_alignment.get("validation_gate"),
            },
            "Do not loosen validation min-trades or cost-stress gates. Only a materially new preregistered basis/funding mechanism may be tested.",
        ),
        tombstone(
            "basis_funding_carry_validation_empty",
            "BASIS_FUNDING_CARRY",
            "Selected carry rule produced no validation trades.",
            {"decision": basis_carry.get("decision"), "selected": basis_carry.get("selected_config")},
            "No threshold loosening on opened validation.",
        ),
        tombstone(
            "basis_dispersion_train_failed",
            "BASIS_DISPERSION_REVERSION",
            "Preregistered dispersion grid had zero train-qualified configs.",
            {
                "decision": basis_disp.get("decision"),
                "configs_tested": basis_disp.get("search", {}).get("configs_tested"),
                "train_qualified": basis_disp.get("search", {}).get("train_qualified_configs"),
                "train_stage": basis_disp.get("stages", {}).get("train", {}).get("evaluation", {}).get("summary"),
            },
            "Reject this dispersion formulation. Do not mine around it without a new preregistration.",
        ),
    ]
    if bybit_semantics.get("contract_failure_proven") is True:
        for impacted in bybit_semantics.get("impacted_families", []):
            if not isinstance(impacted, dict):
                continue
            family = str(impacted.get("family") or "UNKNOWN_BYBIT_LIQUIDATION_FAMILY")
            entries.append(
                tombstone(
                    f"{family.lower()}_side_semantics_invalid",
                    family,
                    "Legacy Bybit directional labels inverted the documented liquidated-position side.",
                    {
                        "decision": bybit_semantics.get("decision"),
                        "diagnostic": bybit_semantics.get("same_input_diagnostic"),
                        "legacy_locks": impacted.get("locks"),
                    },
                    "Do not relabel or reinterpret old outcomes. Reuse requires corrected canonical labels, a new discovery review and a new untouched future floor.",
                )
            )
    elif (
        bybit_runner.get("review_action") == "manual_tombstone_review"
        or bybit_review.get("review_action") == "manual_tombstone_review"
    ):
        entries.append(
            tombstone(
                "bybit_liquidation_forward_lock_failed",
                "BYBIT_LIQUIDATION_FORWARD_OBSERVER",
                "Locked Bybit liquidation observer met sample/resolution gates but failed required positive-horizon evidence.",
                {
                    "runner_decision": bybit_runner.get("decision"),
                    "review_decision": bybit_review.get("decision"),
                    "review_action": bybit_runner.get("review_action") or bybit_review.get("review_action"),
                    "sample_progress": bybit_runner.get("sample_progress"),
                    "horizon_progress": bybit_runner.get("horizon_progress"),
                    "data_quality_decision": bybit_dq.get("decision"),
                    "data_quality_hard_failures": bybit_dq.get("hard_failures"),
                },
                "Do not retune horizons, thresholds or context on the opened forward sample. Keep only materially different preregistered liquidation mechanisms, such as timing/volatility continuation.",
            )
        )
    if str(cross_asset_residual.get("decision") or "").startswith("reject_train_gate_failed"):
        pair_evidence = {}
        for pair, payload in (cross_asset_residual.get("pairs") or {}).items():
            leaderboard = ((payload.get("train") or {}).get("leaderboard") or [])
            best = leaderboard[0] if leaderboard else {}
            pair_evidence[pair] = {
                "best_config": best.get("config_id"),
                "failures": best.get("failures"),
                "summary": ((best.get("evaluation") or {}).get("summary")),
                "stress_summary": (((best.get("evaluation") or {}).get("stress") or {}).get("summary")),
            }
        entries.append(
            tombstone(
                "cross_asset_residual_reversion_train_failed",
                "CROSS_ASSET_RESIDUAL_REVERSION",
                "Rolling BTC-alt log-price residual reversion had zero train-qualified pair configurations after two-leg costs.",
                {
                    "decision": cross_asset_residual.get("decision"),
                    "summary": cross_asset_residual.get("summary"),
                    "pairs": pair_evidence,
                },
                "Do not retune this rolling-OLS residual grid on the opened train sample. Reuse requires a materially different preregistered spread construction or independent dataset.",
            )
        )
    if large_trade_tail.get("decision") == "reject_large_trade_tail_nonpositive_forward_economics_tombstone":
        entries.append(
            tombstone(
                "cross_venue_large_trade_tail_forward_failed",
                "CROSS_VENUE_LARGE_TRADE_TAIL_CONTINUATION",
                "The immutable forward observer reached its fixed sample floor and had non-positive net expectancy at every registered horizon under base and stress costs.",
                {
                    "decision": large_trade_tail.get("decision"),
                    "hypothesis_id": large_trade_tail.get("hypothesis_id"),
                    "economics": large_trade_tail.get("economics"),
                    "sample_checks": large_trade_tail.get("sample_checks"),
                    "integrity_checks": large_trade_tail.get("integrity_checks"),
                },
                "Do not reverse, retune, rename or select a different horizon on the opened forward sample. Reuse requires a materially different preregistered trade-flow mechanism and a new forward floor.",
            )
        )
    report = {
        "generated_at": now_iso(),
        "tool": "tools/edge_tombstone_registry.py",
        "decision": "edge_tombstone_registry_active",
        "can_trade": False,
        "entries": entries,
        "summary": {"tombstoned": len(entries), "families": sorted({item["family"] for item in entries})},
        "policy": "Tombstoned families cannot be promoted, retuned on opened OOS/validation, or reintroduced without a materially new preregistered mechanism.",
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Edge Tombstone Registry",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        f"- Tombstoned entries: `{len(entries)}`",
        "",
        "| Family | Reason | Reuse Rule |",
        "|---|---|---|",
    ]
    for item in entries:
        lines.append(f"| `{item['family']}` | {item['reason']} | {item['reuse_rule']} |")
    lines.extend(["", "## Policy", "", f"- {report['policy']}", ""])
    out.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "tombstoned": len(entries), "out": portable(out.with_suffix(".json")), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
