#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidation_impulse_reversal_nested_holdout import parse_ts  # noqa: E402
from tools.max_backtest import candle_value  # noqa: E402
from tools.max_v11_candidate_validator import atr14_at  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def completed_index(rows: list[dict[str, str]], as_of: datetime) -> int | None:
    result = None
    for index, row in enumerate(rows):
        try:
            opened = parse_ts(str(row.get("time")))
        except ValueError:
            continue
        if opened + timedelta(hours=1) <= as_of:
            result = index
        else:
            break
    return result


def classify_context(
    feature: dict[str, Any],
    *,
    displacement_threshold: float,
    oi_drop_threshold: float,
    volume_z_threshold: float,
) -> str:
    displacement = float(feature["displacement_atr"])
    oi_delta = float(feature["oi_delta_pct"])
    volume_z = float(feature["volume_z"])
    close_location = float(feature["close_location"])
    if oi_delta > -abs(oi_drop_threshold) or volume_z < volume_z_threshold:
        return "none"
    if displacement >= abs(displacement_threshold):
        return "up_liquidation_impulse" if close_location >= 0.60 else "up_dislocation_unconfirmed"
    if displacement <= -abs(displacement_threshold):
        return "down_liquidation_impulse" if close_location <= 0.40 else "down_dislocation_unconfirmed"
    return "none"


def continuous_score(feature: dict[str, Any], thresholds: dict[str, float]) -> float:
    displacement = abs(float(feature["displacement_atr"]))
    oi_component = max(0.0, -float(feature["oi_delta_pct"]) / max(abs(thresholds["oi_drop_pct"]), 1e-12))
    volume_component = max(0.0, float(feature["volume_z"]) / max(abs(thresholds["volume_z"]), 1e-12))
    return displacement * oi_component * volume_component


def score_bin(score: float, lock: dict[str, Any]) -> str:
    bins = lock.get("bins") if isinstance(lock.get("bins"), list) else []
    for item in bins:
        if not isinstance(item, dict):
            continue
        minimum = item.get("min_inclusive", item.get("min_exclusive"))
        maximum = item.get("max_inclusive")
        if item.get("min_exclusive") is not None and score <= float(item["min_exclusive"]):
            continue
        if item.get("min_inclusive") is not None and score < float(item["min_inclusive"]):
            continue
        if maximum is not None and score > float(maximum):
            continue
        return str(item.get("id") or "unknown")
    return "unknown"


def feature_at(rows: list[dict[str, str]], derivatives: list[dict[str, str]], index: int) -> dict[str, Any] | None:
    if index < 100:
        return None
    derivatives_by_time = {str(row.get("time")): row for row in derivatives}
    current = derivatives_by_time.get(str(rows[index].get("time")))
    previous = derivatives_by_time.get(str(rows[index - 3].get("time")))
    if not current or not previous:
        return None
    try:
        oi = float(current["open_interest"])
        previous_oi = float(previous["open_interest"])
    except (KeyError, TypeError, ValueError):
        return None
    atr = atr14_at(rows, index)
    if not math.isfinite(atr) or atr <= 0 or previous_oi == 0:
        return None
    close = candle_value(rows[index], "close")
    previous_close = candle_value(rows[index - 3], "close")
    high = candle_value(rows[index], "high")
    low = candle_value(rows[index], "low")
    volume = candle_value(rows[index], "volume")
    volumes = [candle_value(row, "volume") for row in rows[index - 100 : index]]
    sigma = statistics.pstdev(volumes)
    candle_range = max(high - low, 1e-12)
    return {
        "atr14": atr,
        "displacement_atr": (close - previous_close) / atr,
        "oi_delta_pct": (oi - previous_oi) / previous_oi * 100.0,
        "volume_z": (volume - statistics.mean(volumes)) / sigma if sigma > 0 else 0.0,
        "close_location": (close - low) / candle_range,
    }


def thresholds_from_lock(path: Path) -> dict[str, float]:
    lock = read_json(path)
    candidate = lock.get("candidate") if isinstance(lock.get("candidate"), dict) else {}
    return {
        "displacement_atr": float(candidate.get("displacement_atr") or 1.5),
        "oi_drop_pct": float(candidate.get("oi_drop_pct") or 2.0),
        "volume_z": float(candidate.get("volume_z") or 1.5),
    }


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest", {})
    return "\n".join(
        [
            "# Edge Liquidation Context Shadow Observer",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "- Context-only shadow observer; never changes the frozen Edge signal.",
            "- No filter, veto, paper intent, Telegram message, credentials, or orders.",
            f"- Latest completed 1H bar: `{latest.get('bar_ts')}`.",
            f"- Context: `{latest.get('context')}`.",
            f"- Continuous score / frozen train-only bin: `{latest.get('continuous_score')}` / `{latest.get('score_bin')}`.",
            f"- Displacement / OI delta / volume z: `{latest.get('displacement_atr')}` / `{latest.get('oi_delta_pct')}` / `{latest.get('volume_z')}`.",
            f"- Edge effect: `{latest.get('edge_effect')}`.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Label Edge forward evidence with liquidation/OI context without filtering signals")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--research-lock", default="configs/LIQUIDATION_IMPULSE_CONTINUATION_RESEARCH_LOCK.json")
    parser.add_argument("--edge-lock", default="configs/EDGE_FORWARD_LOCK.json")
    parser.add_argument("--score-lock", default="configs/EDGE_LIQUIDATION_SCORE_SHADOW_LOCK.json")
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/edge_liquidation_context_shadow.jsonl")
    parser.add_argument("--state-path", default="logs/forward_paper_feed/edge_liquidation_context_shadow_state.json")
    parser.add_argument("--as-of", default="")
    parser.add_argument("--out-prefix", default="docs/EDGE_LIQUIDATION_CONTEXT_SHADOW_OBSERVER_2026-06-23")
    args = parser.parse_args()

    cache = resolve_path(args.cache_dir)
    rows = read_csv(cache / "futures" / "BTCUSDT" / "1h_klines.csv")
    derivatives = read_csv(cache / "futures" / "BTCUSDT" / "1h_oi_aligned.csv")
    as_of = parse_ts(args.as_of) if args.as_of else datetime.now(timezone.utc)
    index = completed_index(rows, as_of)
    thresholds = thresholds_from_lock(resolve_path(args.research_lock))
    edge_lock = read_json(resolve_path(args.edge_lock))
    continuous_lock = read_json(resolve_path(args.score_lock))
    edge_candidate = edge_lock.get("candidate") if isinstance(edge_lock.get("candidate"), dict) else {}
    feature = feature_at(rows, derivatives, index) if index is not None else None
    if index is None or feature is None:
        latest = {
            "status": "context_unavailable",
            "bar_ts": rows[index].get("time") if index is not None and rows else None,
            "context": "unknown",
            "edge_effect": "label_only_no_filter",
            "data_degraded": True,
        }
    else:
        context = classify_context(
            feature,
            displacement_threshold=thresholds["displacement_atr"],
            oi_drop_threshold=thresholds["oi_drop_pct"],
            volume_z_threshold=thresholds["volume_z"],
        )
        score = continuous_score(feature, thresholds)
        latest = {
            "event_type": "edge_liquidation_context_shadow",
            "status": "shadow_context_observed",
            "bar_ts": rows[index]["time"],
            "context": context,
            "continuous_score": round(score, 6),
            "score_bin": score_bin(score, continuous_lock),
            "score_lock_status": continuous_lock.get("status"),
            "displacement_atr": round(float(feature["displacement_atr"]), 6),
            "oi_delta_pct": round(float(feature["oi_delta_pct"]), 6),
            "volume_z": round(float(feature["volume_z"]), 6),
            "close_location": round(float(feature["close_location"]), 6),
            "thresholds": thresholds,
            "edge_strategy_id": edge_candidate.get("strategy_id"),
            "edge_effect": "label_only_no_filter",
            "filter_applied": False,
            "veto_applied": False,
            "data_degraded": False,
            "ts_emitted": now_iso(),
            "can_trade": False,
        }
    state_path = resolve_path(args.state_path)
    state = read_json(state_path)
    schema_upgrade = bool(
        latest.get("bar_ts")
        and latest.get("bar_ts") == state.get("last_bar_ts")
        and latest.get("score_bin")
        and not state.get("last_score_bin")
    )
    appended = bool(latest.get("bar_ts") and (latest.get("bar_ts") != state.get("last_bar_ts") or schema_upgrade))
    if appended:
        latest["journal_reason"] = "continuous_score_schema_upgrade" if schema_upgrade else "new_completed_bar"
        append_jsonl(resolve_path(args.journal_path), latest)
        write_json(
            state_path,
            {
                "last_bar_ts": latest.get("bar_ts"),
                "last_context": latest.get("context"),
                "last_continuous_score": latest.get("continuous_score"),
                "last_score_bin": latest.get("score_bin"),
                "updated_at": now_iso(),
            },
        )
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {"classification": "edge_context_shadow_only", "changes_edge_signal": False, "applies_filter": False, "applies_veto": False, "sends_orders": False, "can_trade": False},
        "paths": {
            "cache_dir": rel_path(cache),
            "journal": rel_path(resolve_path(args.journal_path)),
            "state": rel_path(state_path),
            "score_lock": rel_path(resolve_path(args.score_lock)),
        },
        "latest": latest,
        "journal_appended": appended,
        "schema_upgrade_appended": schema_upgrade,
        "decision": "edge_liquidation_context_shadow_no_trade_permission",
        "next_action": "collect_context_labels_alongside_frozen_edge_outcomes",
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "context": latest.get("context"), "bar_ts": latest.get("bar_ts"), "journal_appended": appended, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
