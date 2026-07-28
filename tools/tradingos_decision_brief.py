#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "configs" / "TRADINGOS_DECISION_BRIEF_POLICY_V1.json"
GENERATOR_VERSION = "1.0.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty ISO-8601 timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def nested(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    value = snapshot.get(key)
    return value if isinstance(value, dict) else {}


def text_value(value: Any, default: str = "unknown") -> str:
    return str(value).strip() if isinstance(value, str) and value.strip() else default


def required_field_errors(snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_objects = (
        "provenance",
        "price",
        "market_structure",
        "derivatives",
        "flow",
        "data_quality",
        "operator",
    )
    for key in ("snapshot_id", "as_of", "symbol", "timeframe", "can_trade"):
        if key not in snapshot:
            errors.append(f"missing:{key}")
    for key in required_objects:
        if not isinstance(snapshot.get(key), dict):
            errors.append(f"missing_or_invalid_object:{key}")

    required_numbers = (
        ("price", "last"),
        ("price", "change_pct"),
        ("price", "ema_fast"),
        ("price", "ema_slow"),
        ("price", "atr_pct"),
        ("market_structure", "support"),
        ("market_structure", "resistance"),
        ("derivatives", "open_interest_change_pct"),
        ("derivatives", "funding_z"),
        ("derivatives", "basis_z"),
        ("flow", "relative_volume"),
    )
    for parent, key in required_numbers:
        if not finite_number(nested(snapshot, parent).get(key)):
            errors.append(f"missing_or_invalid_number:{parent}.{key}")

    for parent, key in (
        ("market_structure", "trend"),
        ("flow", "spot_cvd_direction"),
        ("flow", "perp_cvd_direction"),
    ):
        if text_value(nested(snapshot, parent).get(key)) == "unknown":
            errors.append(f"missing_or_invalid_text:{parent}.{key}")
    return errors


def validate_snapshot(
    snapshot: dict[str, Any],
    policy: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    blockers = required_field_errors(snapshot)
    missing_data: list[str] = []
    conflicts: list[str] = []
    age_minutes: float | None = None

    if snapshot.get("can_trade") is not False:
        blockers.append("unsafe_permission:can_trade_must_be_false")
    if snapshot.get("symbol") != policy.get("supported_symbol"):
        blockers.append("unsupported_symbol")
    if snapshot.get("timeframe") not in policy.get("allowed_timeframes", []):
        blockers.append("unsupported_timeframe")

    try:
        as_of = parse_time(snapshot.get("as_of"), "as_of")
        age_minutes = (now - as_of).total_seconds() / 60.0
        if age_minutes > float(policy["max_snapshot_age_minutes"]):
            blockers.append("stale_snapshot")
        if age_minutes < -float(policy["max_future_clock_skew_minutes"]):
            blockers.append("future_snapshot_clock_skew")
    except (TypeError, ValueError) as exc:
        blockers.append(f"invalid_as_of:{exc}")

    quality = nested(snapshot, "data_quality")
    present_sources = quality.get("present_sources")
    if not isinstance(present_sources, list):
        present_sources = []
        blockers.append("missing_or_invalid_list:data_quality.present_sources")
    present = {str(item) for item in present_sources}
    for source in policy.get("required_sources", []):
        if source not in present:
            missing_data.append(str(source))
    if missing_data:
        blockers.append("missing_required_sources")

    explicit_conflicts = quality.get("conflicts")
    if explicit_conflicts is None:
        explicit_conflicts = []
    if not isinstance(explicit_conflicts, list):
        blockers.append("missing_or_invalid_list:data_quality.conflicts")
    else:
        conflicts.extend(str(item) for item in explicit_conflicts if str(item).strip())
    if conflicts:
        blockers.append("conflicting_data")

    provenance = nested(snapshot, "provenance")
    sources = provenance.get("sources")
    if not isinstance(sources, list) or not sources:
        blockers.append("missing_provenance_sources")
    else:
        kinds = {
            str(item.get("kind"))
            for item in sources
            if isinstance(item, dict) and item.get("kind")
        }
        missing_provenance = sorted(set(policy.get("required_sources", [])) - kinds)
        if missing_provenance:
            blockers.append("missing_provenance_for_required_sources")
            missing_data.extend(f"provenance:{item}" for item in missing_provenance)

    price = nested(snapshot, "price")
    structure = nested(snapshot, "market_structure")
    if finite_number(price.get("last")) and finite_number(structure.get("support")):
        if float(structure["support"]) >= float(price["last"]):
            conflicts.append("support_not_below_last_price")
    if finite_number(price.get("last")) and finite_number(structure.get("resistance")):
        if float(structure["resistance"]) <= float(price["last"]):
            conflicts.append("resistance_not_above_last_price")
    if any(item in {"support_not_below_last_price", "resistance_not_above_last_price"} for item in conflicts):
        blockers.append("conflicting_market_structure")

    blockers = sorted(set(blockers))
    missing_data = sorted(set(missing_data))
    conflicts = sorted(set(conflicts))
    return {
        "passed": not blockers,
        "blockers": blockers,
        "missing_data": missing_data,
        "conflicts": conflicts,
        "snapshot_age_minutes": round(age_minutes, 3) if age_minutes is not None else None,
    }


def evidence_row(
    dimension: str,
    label: str,
    direction: str,
    strength: float,
    observation: str,
) -> dict[str, Any]:
    return {
        "dimension": dimension,
        "label": label,
        "direction": direction,
        "strength": round(float(strength), 3),
        "observation": observation,
    }


def build_evidence(snapshot: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    price = nested(snapshot, "price")
    structure = nested(snapshot, "market_structure")
    derivatives = nested(snapshot, "derivatives")
    flow = nested(snapshot, "flow")

    trend = text_value(structure.get("trend")).lower()
    if trend in {"up", "bull", "bullish"}:
        rows.append(evidence_row("market_structure", "HTF trend", "LONG", 2.0, f"trend={trend}"))
    elif trend in {"down", "bear", "bearish"}:
        rows.append(evidence_row("market_structure", "HTF trend", "SHORT", 2.0, f"trend={trend}"))
    else:
        rows.append(evidence_row("market_structure", "HTF trend", "NEUTRAL", 0.0, f"trend={trend}"))

    fast = price.get("ema_fast")
    slow = price.get("ema_slow")
    if finite_number(fast) and finite_number(slow):
        if float(fast) > float(slow):
            rows.append(evidence_row("price_trend", "EMA alignment", "LONG", 1.0, "ema_fast > ema_slow"))
        elif float(fast) < float(slow):
            rows.append(evidence_row("price_trend", "EMA alignment", "SHORT", 1.0, "ema_fast < ema_slow"))
        else:
            rows.append(evidence_row("price_trend", "EMA alignment", "NEUTRAL", 0.0, "ema_fast = ema_slow"))

    change = float(price.get("change_pct", 0.0)) if finite_number(price.get("change_pct")) else 0.0
    oi_change = (
        float(derivatives.get("open_interest_change_pct", 0.0))
        if finite_number(derivatives.get("open_interest_change_pct"))
        else 0.0
    )
    if change > 0 and oi_change > 0:
        rows.append(evidence_row("open_interest", "Price/OI alignment", "LONG", 1.25, f"price={change}% OI={oi_change}%"))
    elif change < 0 and oi_change > 0:
        rows.append(evidence_row("open_interest", "Price/OI alignment", "SHORT", 1.25, f"price={change}% OI={oi_change}%"))
    elif change > 0 and oi_change < 0:
        rows.append(evidence_row("open_interest", "Short-covering risk", "NEUTRAL", 0.75, f"price={change}% OI={oi_change}%"))
    elif change < 0 and oi_change < 0:
        rows.append(evidence_row("open_interest", "Position flush", "NEUTRAL", 0.75, f"price={change}% OI={oi_change}%"))
    else:
        rows.append(evidence_row("open_interest", "Price/OI alignment", "NEUTRAL", 0.0, f"price={change}% OI={oi_change}%"))

    spot = text_value(flow.get("spot_cvd_direction")).lower()
    perp = text_value(flow.get("perp_cvd_direction")).lower()
    if spot == "up":
        rows.append(evidence_row("spot_flow", "Spot CVD", "LONG", 1.25, f"spot={spot}, perp={perp}"))
    elif spot == "down":
        rows.append(evidence_row("spot_flow", "Spot CVD", "SHORT", 1.25, f"spot={spot}, perp={perp}"))
    else:
        rows.append(evidence_row("spot_flow", "Spot CVD", "NEUTRAL", 0.0, f"spot={spot}, perp={perp}"))
    if spot != "unknown" and perp != "unknown" and spot != perp:
        rows.append(evidence_row("spot_perp_divergence", "Spot/perp disagreement", "NEUTRAL", 1.0, f"spot={spot}, perp={perp}"))

    funding_z = float(derivatives.get("funding_z", 0.0)) if finite_number(derivatives.get("funding_z")) else 0.0
    basis_z = float(derivatives.get("basis_z", 0.0)) if finite_number(derivatives.get("basis_z")) else 0.0
    funding_limit = float(policy["funding_z_extreme"])
    basis_limit = float(policy["basis_z_extreme"])
    if funding_z >= funding_limit or basis_z >= basis_limit:
        rows.append(
            evidence_row(
                "derivatives_crowding",
                "Positive crowding",
                "SHORT",
                1.0,
                f"funding_z={funding_z}, basis_z={basis_z}",
            )
        )
    elif funding_z <= -funding_limit or basis_z <= -basis_limit:
        rows.append(
            evidence_row(
                "derivatives_crowding",
                "Negative crowding",
                "LONG",
                1.0,
                f"funding_z={funding_z}, basis_z={basis_z}",
            )
        )
    else:
        rows.append(
            evidence_row(
                "derivatives_crowding",
                "Crowding balanced",
                "NEUTRAL",
                0.0,
                f"funding_z={funding_z}, basis_z={basis_z}",
            )
        )

    volume = float(flow.get("relative_volume", 0.0)) if finite_number(flow.get("relative_volume")) else 0.0
    if volume >= float(policy["relative_volume_confirm"]) and change != 0:
        direction = "LONG" if change > 0 else "SHORT"
        rows.append(evidence_row("volume", "Relative volume confirmation", direction, 1.0, f"relative_volume={volume}"))
    elif volume < float(policy["relative_volume_weak"]):
        rows.append(evidence_row("volume", "Weak relative volume", "NEUTRAL", 0.75, f"relative_volume={volume}"))
    else:
        rows.append(evidence_row("volume", "Relative volume", "NEUTRAL", 0.0, f"relative_volume={volume}"))
    return rows


def hypothesis(direction: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    opposite = "SHORT" if direction == "LONG" else "LONG"
    supporting = [item for item in evidence if item["direction"] == direction]
    contradicting = [item for item in evidence if item["direction"] == opposite]
    ambiguous = [item for item in evidence if item["direction"] == "NEUTRAL" and item["strength"] > 0]
    score = sum(float(item["strength"]) for item in supporting)
    counter_score = sum(float(item["strength"]) for item in contradicting)
    return {
        "intent": f"{direction}_CONTINUATION_OR_ROTATION",
        "direction": direction,
        "support_score": round(score, 3),
        "counter_score": round(counter_score, 3),
        "independent_support_dimensions": len({item["dimension"] for item in supporting}),
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting + ambiguous,
    }


def edge_decision(
    hypotheses: list[dict[str, Any]],
    validation: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    if not validation["passed"]:
        return {
            "stance": "NO_ACTION",
            "reason": "input_gate_failed",
            "lead_direction": None,
            "score_margin": 0.0,
            "edge_sufficient": False,
        }
    ordered = sorted(hypotheses, key=lambda item: float(item["support_score"]), reverse=True)
    lead, alternative = ordered
    margin = float(lead["support_score"]) - float(alternative["support_score"])
    gate = policy["edge_gate"]
    sufficient = (
        float(lead["support_score"]) >= float(gate["minimum_direction_score"])
        and margin >= float(gate["minimum_score_margin"])
        and int(lead["independent_support_dimensions"]) >= int(gate["minimum_independent_dimensions"])
    )
    return {
        "stance": f"WATCH_{lead['direction']}" if sufficient else "NO_ACTION",
        "reason": "edge_gate_passed" if sufficient else "insufficient_independent_edge",
        "lead_direction": lead["direction"],
        "score_margin": round(margin, 3),
        "edge_sufficient": sufficient,
    }


def regime(snapshot: dict[str, Any]) -> dict[str, Any]:
    price = nested(snapshot, "price")
    structure = nested(snapshot, "market_structure")
    trend = text_value(structure.get("trend")).lower()
    atr_pct = float(price.get("atr_pct", 0.0)) if finite_number(price.get("atr_pct")) else 0.0
    if trend in {"up", "bull", "bullish"}:
        label = "TREND_UP"
    elif trend in {"down", "bear", "bearish"}:
        label = "TREND_DOWN"
    elif trend in {"range", "sideways"}:
        label = "RANGE"
    else:
        label = "UNCLEAR"
    volatility = "EXPANDED" if atr_pct >= 3.0 else "COMPRESSED" if atr_pct <= 1.2 else "NORMAL"
    return {
        "label": label,
        "volatility": volatility,
        "basis": [
            f"market_structure.trend={trend}",
            f"price.atr_pct={atr_pct}",
        ],
    }


def scenarios(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    structure = nested(snapshot, "market_structure")
    support = structure.get("support")
    resistance = structure.get("resistance")
    timeframe = snapshot.get("timeframe")
    return [
        {
            "name": "bull",
            "trigger": f"{timeframe} close above {resistance} with spot-flow and OI confirmation",
            "invalidation": f"{timeframe} close back below {resistance} or loss of {support}",
            "operator_use": "reassess WATCH_LONG; this brief itself is not an entry signal",
        },
        {
            "name": "base",
            "trigger": f"price remains between {support} and {resistance}",
            "invalidation": f"accepted close outside [{support}, {resistance}]",
            "operator_use": "NO_ACTION in the middle of the range; wait for new evidence",
        },
        {
            "name": "bear",
            "trigger": f"{timeframe} close below {support} with spot-flow and OI confirmation",
            "invalidation": f"{timeframe} close back above {support} or reclaim of {resistance}",
            "operator_use": "reassess WATCH_SHORT; this brief itself is not an entry signal",
        },
    ]


def derivatives_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    derivatives = nested(snapshot, "derivatives")
    funding_z = derivatives.get("funding_z")
    basis_z = derivatives.get("basis_z")
    oi_change = derivatives.get("open_interest_change_pct")
    if finite_number(funding_z) and abs(float(funding_z)) >= 1.5:
        funding_read = "crowded_positive" if float(funding_z) > 0 else "crowded_negative"
    else:
        funding_read = "balanced_or_unconfirmed"
    if finite_number(oi_change) and float(oi_change) > 0:
        oi_read = "leverage_building"
    elif finite_number(oi_change) and float(oi_change) < 0:
        oi_read = "leverage_reducing"
    else:
        oi_read = "flat_or_unknown"
    return {
        "open_interest_change_pct": oi_change,
        "open_interest_read": oi_read,
        "funding_rate": derivatives.get("funding_rate"),
        "funding_z": funding_z,
        "funding_read": funding_read,
        "basis_pct": derivatives.get("basis_pct"),
        "basis_z": basis_z,
        "liquidation_bias": derivatives.get("liquidation_bias", "unknown"),
    }


def operator_next_action(snapshot: dict[str, Any], decision: dict[str, Any], validation: dict[str, Any]) -> str:
    if validation["blockers"]:
        return f"Do not trade; repair `{validation['blockers'][0]}` and generate a fresh brief."
    structure = nested(snapshot, "market_structure")
    timeframe = snapshot.get("timeframe")
    if decision["stance"] == "WATCH_LONG":
        return (
            f"Wait for a {timeframe} close above {structure.get('resistance')} with spot-flow and OI confirmation; "
            "do not place an order from this brief."
        )
    if decision["stance"] == "WATCH_SHORT":
        return (
            f"Wait for a {timeframe} close below {structure.get('support')} with spot-flow and OI confirmation; "
            "do not place an order from this brief."
        )
    return f"Do not trade; refresh the snapshot after the next {timeframe} close or a material evidence change."


def build_brief(
    snapshot: dict[str, Any],
    input_path: Path,
    policy: dict[str, Any],
    policy_path: Path,
    now: datetime,
) -> dict[str, Any]:
    validation = validate_snapshot(snapshot, policy, now)
    evidence = build_evidence(snapshot, policy) if not required_field_errors(snapshot) else []
    hypotheses = [hypothesis("LONG", evidence), hypothesis("SHORT", evidence)]
    decision = edge_decision(hypotheses, validation, policy)
    input_sha = sha256_file(input_path)
    brief_id = hashlib.sha256(
        f"{policy['policy_id']}:{GENERATOR_VERSION}:{input_sha}".encode("utf-8")
    ).hexdigest()[:24]
    operator = nested(snapshot, "operator")
    return {
        "schema_version": 1,
        "brief_id": brief_id,
        "snapshot_id": snapshot.get("snapshot_id"),
        "generated_at": iso_utc(now),
        "as_of": snapshot.get("as_of"),
        "symbol": snapshot.get("symbol"),
        "timeframe": snapshot.get("timeframe"),
        "status": "READY" if validation["passed"] else "BLOCKED",
        "decision": decision,
        "regime": regime(snapshot),
        "intent_hypotheses": hypotheses,
        "derivatives_context": derivatives_context(snapshot),
        "scenarios": scenarios(snapshot),
        "invalidation": {
            "global": "Any stale, missing, conflicting, or unsafe input invalidates the brief.",
            "long": scenarios(snapshot)[0]["invalidation"],
            "short": scenarios(snapshot)[2]["invalidation"],
        },
        "uncertainty": {
            "input_gate_passed": validation["passed"],
            "snapshot_age_minutes": validation["snapshot_age_minutes"],
            "missing_data": validation["missing_data"],
            "conflicts": validation["conflicts"],
            "blockers": validation["blockers"],
            "model_probability_claimed": False,
            "caveats": [
                "Scores are deterministic evidence weights, not calibrated probabilities.",
                "A WATCH stance is an observation priority, not an entry signal.",
                "This generator does not assess execution, account state, fees, or position sizing.",
            ],
        },
        "operator_feedback": {
            "prior_decision": operator.get("prior_decision"),
            "changed_decision": operator.get("changed_decision"),
            "prevented_decision": operator.get("prevented_decision"),
        },
        "operator_next_action": operator_next_action(snapshot, decision, validation),
        "provenance": {
            "input_path": portable_path(input_path),
            "input_sha256": input_sha,
            "input_producer": nested(snapshot, "provenance").get("producer"),
            "input_sources": nested(snapshot, "provenance").get("sources", []),
            "policy_path": portable_path(policy_path),
            "policy_id": policy["policy_id"],
            "policy_sha256": sha256_file(policy_path),
            "generator": "tools/tradingos_decision_brief.py",
            "generator_version": GENERATOR_VERSION,
        },
        "permissions": {
            "read_only_analysis": True,
            "signals_allowed": False,
            "orders_allowed": False,
            "uses_credentials": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
        "can_trade": False,
    }


def markdown_list(items: list[Any], empty: str = "none") -> str:
    if not items:
        return f"- {empty}"
    return "\n".join(f"- {item}" for item in items)


def render_markdown(brief: dict[str, Any]) -> str:
    decision = brief["decision"]
    lines = [
        "# TradingOS VIP Daily Decision Brief",
        "",
        f"**{brief.get('symbol')} · {brief.get('timeframe')} · {brief.get('as_of')}**",
        "",
        f"> **{decision['stance']}** — {decision['reason']}. This is a read-only operator brief, not a signal or order.",
        "",
        "## Today",
        "",
        f"- Regime: `{brief['regime']['label']}` / `{brief['regime']['volatility']}`",
        f"- Input status: `{brief['status']}`",
        f"- Edge sufficient: `{decision['edge_sufficient']}`",
        f"- Score margin: `{decision['score_margin']}`",
        f"- One next action: **{brief['operator_next_action']}**",
        "",
        "## Competing Intent Hypotheses",
        "",
    ]
    for item in brief["intent_hypotheses"]:
        lines.extend(
            [
                f"### {item['direction']}",
                "",
                f"- Support score: `{item['support_score']}`",
                f"- Counter score: `{item['counter_score']}`",
                f"- Independent support dimensions: `{item['independent_support_dimensions']}`",
                "- Supporting evidence:",
            ]
        )
        lines.extend(
            f"  - {e['label']}: {e['observation']} (weight {e['strength']})"
            for e in item["supporting_evidence"]
        )
        if not item["supporting_evidence"]:
            lines.append("  - none")
        lines.append("- Contradicting or ambiguous evidence:")
        lines.extend(
            f"  - {e['label']}: {e['observation']} (weight {e['strength']})"
            for e in item["contradicting_evidence"]
        )
        if not item["contradicting_evidence"]:
            lines.append("  - none")
        lines.append("")
    lines.extend(["## Scenarios", ""])
    for item in brief["scenarios"]:
        lines.extend(
            [
                f"### {item['name'].upper()}",
                "",
                f"- Trigger: {item['trigger']}",
                f"- Invalidation: {item['invalidation']}",
                f"- Use: {item['operator_use']}",
                "",
            ]
        )
    uncertainty = brief["uncertainty"]
    feedback = brief["operator_feedback"]
    lines.extend(
        [
            "## Derivatives Context",
            "",
            f"- OI: `{brief['derivatives_context']['open_interest_read']}` ({brief['derivatives_context']['open_interest_change_pct']}%)",
            f"- Funding: `{brief['derivatives_context']['funding_read']}` (z={brief['derivatives_context']['funding_z']})",
            f"- Basis z: `{brief['derivatives_context']['basis_z']}`",
            f"- Liquidation bias: `{brief['derivatives_context']['liquidation_bias']}`",
            "",
            "## Uncertainty And Data Quality",
            "",
            f"- Snapshot age: `{uncertainty['snapshot_age_minutes']}` minutes",
            f"- Missing: `{', '.join(uncertainty['missing_data']) or 'none'}`",
            f"- Conflicts: `{', '.join(uncertainty['conflicts']) or 'none'}`",
            f"- Blockers: `{', '.join(uncertainty['blockers']) or 'none'}`",
            "- Scores are not probabilities. `WATCH_*` is not permission to trade.",
            "",
            "## Pilot Feedback",
            "",
            f"- Prior decision: `{feedback.get('prior_decision')}`",
            f"- Changed decision: `{feedback.get('changed_decision')}`",
            f"- Prevented decision: `{feedback.get('prevented_decision')}`",
            "",
            "## Provenance",
            "",
            f"- Brief ID: `{brief['brief_id']}`",
            f"- Input SHA-256: `{brief['provenance']['input_sha256']}`",
            f"- Policy: `{brief['provenance']['policy_id']}`",
            f"- Generated: `{brief['generated_at']}`",
            "- `can_trade=false`; capital permission `DENY`.",
            "",
        ]
    )
    return "\n".join(lines)


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def evidence_html(items: list[dict[str, Any]]) -> str:
    if not items:
        return "<li>None</li>"
    return "".join(
        f"<li><strong>{esc(item['label'])}</strong>: {esc(item['observation'])} "
        f"<span class=\"weight\">w={esc(item['strength'])}</span></li>"
        for item in items
    )


def render_html(brief: dict[str, Any]) -> str:
    decision = brief["decision"]
    hypothesis_cards = "".join(
        f"""
        <section class="hypothesis">
          <div class="eyebrow">{esc(item['direction'])} HYPOTHESIS</div>
          <div class="score">{esc(item['support_score'])}<small> support</small></div>
          <p>{esc(item['independent_support_dimensions'])} independent dimensions</p>
          <h3>Supporting</h3><ul>{evidence_html(item['supporting_evidence'])}</ul>
          <h3>Counterevidence</h3><ul>{evidence_html(item['contradicting_evidence'])}</ul>
        </section>"""
        for item in brief["intent_hypotheses"]
    )
    scenario_rows = "".join(
        f"<tr><th>{esc(item['name'].upper())}</th><td>{esc(item['trigger'])}</td>"
        f"<td>{esc(item['invalidation'])}</td><td>{esc(item['operator_use'])}</td></tr>"
        for item in brief["scenarios"]
    )
    blockers = ", ".join(brief["uncertainty"]["blockers"]) or "none"
    missing = ", ".join(brief["uncertainty"]["missing_data"]) or "none"
    conflicts = ", ".join(brief["uncertainty"]["conflicts"]) or "none"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TradingOS Decision Brief · {esc(brief.get('symbol'))}</title>
  <style>
    :root {{ --ink:#132238; --muted:#5f6b78; --paper:#f5f0e6; --card:#fffdf8;
      --accent:#b95d2a; --line:#d8cdbb; --safe:#286a56; --block:#9b3434; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:
      radial-gradient(circle at 8% 5%, #fff9e9 0, transparent 32rem),
      linear-gradient(135deg, #e8dfd1, var(--paper)); font-family:Georgia, "Times New Roman", serif; }}
    main {{ max-width:1080px; margin:0 auto; padding:52px 28px 72px; }}
    header {{ border-top:7px solid var(--accent); padding:26px 0 22px; border-bottom:1px solid var(--line); }}
    .eyebrow {{ font:700 12px/1.3 "Aptos Narrow", "Segoe UI", sans-serif; letter-spacing:.16em; color:var(--accent); }}
    h1 {{ margin:8px 0 12px; font-size:clamp(34px,5vw,64px); line-height:.95; letter-spacing:-.04em; }}
    h2 {{ margin-top:38px; font-size:26px; }}
    h3 {{ margin:18px 0 7px; font-size:15px; text-transform:uppercase; letter-spacing:.08em; }}
    .meta, .subtle {{ color:var(--muted); }}
    .stance {{ margin:28px 0; padding:22px 24px; background:var(--ink); color:white; border-radius:2px;
      display:grid; grid-template-columns:auto 1fr; gap:18px; align-items:center; }}
    .stance b {{ color:#ffd09a; font:800 22px/1 "Aptos Display", "Segoe UI", sans-serif; }}
    .next {{ background:#f1d7b7; border-left:5px solid var(--accent); padding:18px 20px; font-size:18px; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }}
    .hypothesis {{ background:var(--card); border:1px solid var(--line); padding:24px; box-shadow:0 8px 26px #4a39240d; }}
    .score {{ font:800 42px/1 "Aptos Display", "Segoe UI", sans-serif; margin-top:9px; }}
    .score small {{ font-size:14px; color:var(--muted); }}
    ul {{ padding-left:20px; line-height:1.48; }}
    .weight {{ color:var(--muted); font-family:Consolas, monospace; font-size:12px; }}
    table {{ width:100%; border-collapse:collapse; background:var(--card); }}
    th,td {{ text-align:left; vertical-align:top; padding:13px; border:1px solid var(--line); }}
    th {{ color:var(--accent); font-family:"Aptos Narrow", "Segoe UI", sans-serif; }}
    .quality {{ display:grid; grid-template-columns:repeat(3,1fr); gap:1px; background:var(--line); border:1px solid var(--line); }}
    .quality div {{ background:var(--card); padding:16px; overflow-wrap:anywhere; }}
    footer {{ margin-top:42px; padding-top:16px; border-top:1px solid var(--line); font-size:13px; color:var(--muted); }}
    @media (max-width:760px) {{ .grid,.quality {{ grid-template-columns:1fr; }} .stance {{ grid-template-columns:1fr; }} }}
    @media print {{ body {{ background:white; }} main {{ max-width:none; padding:12mm; }}
      .hypothesis {{ box-shadow:none; break-inside:avoid; }} h2 {{ break-after:avoid; }} }}
  </style>
</head>
<body><main>
  <header>
    <div class="eyebrow">TRADINGOS · VIP DAILY DECISION BRIEF</div>
    <h1>{esc(brief.get('symbol'))}</h1>
    <div class="meta">{esc(brief.get('timeframe'))} · as of {esc(brief.get('as_of'))} · {esc(brief['regime']['label'])} / {esc(brief['regime']['volatility'])}</div>
  </header>
  <div class="stance"><b>{esc(decision['stance'])}</b><span>{esc(decision['reason'])}. Read-only analysis; never an order.</span></div>
  <div class="next"><strong>One next action:</strong> {esc(brief['operator_next_action'])}</div>
  <h2>Competing intent hypotheses</h2><div class="grid">{hypothesis_cards}</div>
  <h2>Scenarios and invalidation</h2>
  <table><thead><tr><th>Case</th><th>Trigger</th><th>Invalidation</th><th>Operator use</th></tr></thead>
  <tbody>{scenario_rows}</tbody></table>
  <h2>Derivatives context</h2>
  <p>OI: <strong>{esc(brief['derivatives_context']['open_interest_read'])}</strong>
  ({esc(brief['derivatives_context']['open_interest_change_pct'])}%). Funding:
  <strong>{esc(brief['derivatives_context']['funding_read'])}</strong>
  (z={esc(brief['derivatives_context']['funding_z'])}). Basis z={esc(brief['derivatives_context']['basis_z'])}.
  Liquidations: {esc(brief['derivatives_context']['liquidation_bias'])}.</p>
  <h2>Uncertainty and data quality</h2>
  <div class="quality"><div><strong>Missing</strong><br>{esc(missing)}</div>
  <div><strong>Conflicts</strong><br>{esc(conflicts)}</div>
  <div><strong>Blockers</strong><br>{esc(blockers)}</div></div>
  <h2>Pilot feedback</h2>
  <p>Changed decision: <strong>{esc(brief['operator_feedback'].get('changed_decision'))}</strong><br>
  Prevented decision: <strong>{esc(brief['operator_feedback'].get('prevented_decision'))}</strong></p>
  <footer>Brief {esc(brief['brief_id'])} · Input SHA-256 {esc(brief['provenance']['input_sha256'])}<br>
  Scores are deterministic evidence weights, not probabilities. can_trade=false · capital DENY.</footer>
</main></body></html>"""


def pilot_row(brief: dict[str, Any], pilot_day: str | None) -> dict[str, Any]:
    feedback = brief["operator_feedback"]
    return {
        "pilot_day": pilot_day or str(brief.get("as_of", ""))[:10],
        "brief_id": brief["brief_id"],
        "snapshot_id": brief.get("snapshot_id"),
        "generated_at": brief["generated_at"],
        "decision": brief["decision"]["stance"],
        "status": brief["status"],
        "changed_decision": feedback.get("changed_decision"),
        "prevented_decision": feedback.get("prevented_decision"),
        "operator_next_action": brief["operator_next_action"],
        "can_trade": False,
    }


def append_pilot(path: Path, row: dict[str, Any]) -> str:
    existing: list[dict[str, Any]] = []
    if path.exists():
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError(f"invalid pilot row {number}")
            existing.append(item)
    key = (row["pilot_day"], row["brief_id"])
    if any((item.get("pilot_day"), item.get("brief_id")) == key for item in existing):
        return "duplicate_suppressed"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return "appended"


def generate(
    input_path: Path,
    out_dir: Path,
    policy_path: Path,
    now: datetime,
    pilot_log: Path | None = None,
    pilot_day: str | None = None,
) -> tuple[dict[str, Any], dict[str, Path], str | None]:
    snapshot = read_json(input_path)
    policy = read_json(policy_path)
    if policy.get("output_permissions", {}).get("can_trade") is not False:
        raise ValueError("policy can_trade must be false")
    if policy.get("output_permissions", {}).get("capital_permission") != "DENY":
        raise ValueError("policy capital permission must be DENY")
    brief = build_brief(snapshot, input_path, policy, policy_path, now)
    paths = {
        "json": out_dir / "brief.json",
        "markdown": out_dir / "brief.md",
        "html": out_dir / "brief.html",
    }
    write_json(paths["json"], brief)
    write_text(paths["markdown"], render_markdown(brief))
    write_text(paths["html"], render_html(brief))
    pilot_status = append_pilot(pilot_log, pilot_row(brief, pilot_day)) if pilot_log else None
    return brief, paths, pilot_status


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Generate a read-only BTCUSDT operator decision brief from a market snapshot"
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--now", help="Frozen UTC clock for deterministic replay/testing")
    parser.add_argument("--pilot-log", type=Path)
    parser.add_argument("--pilot-day")
    args = parser.parse_args()
    now = parse_time(args.now, "now") if args.now else utc_now()
    try:
        brief, paths, pilot_status = generate(
            args.input.resolve(),
            args.out_dir.resolve(),
            args.policy.resolve(),
            now,
            args.pilot_log.resolve() if args.pilot_log else None,
            args.pilot_day,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "result": "PASS" if brief["status"] == "READY" else "FAIL_CLOSED",
                "status": brief["status"],
                "decision": brief["decision"]["stance"],
                "brief_id": brief["brief_id"],
                "outputs": {key: str(path) for key, path in paths.items()},
                "pilot_log": pilot_status,
                "can_trade": False,
                "capital_permission": "DENY",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if brief["status"] == "READY" else 3


if __name__ == "__main__":
    raise SystemExit(main())
