#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot compute quantile of empty values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def derive_train_thresholds(labelled_trades: list[dict[str, Any]]) -> dict[str, Any]:
    train_scores = [
        float(row.get("strongest_context_score") or 0.0)
        for row in labelled_trades
        if row.get("window") == "train"
    ]
    positive = [value for value in train_scores if value > 0.0]
    if len(train_scores) < 40 or len(positive) < 20:
        raise ValueError("insufficient train-only score sample")
    return {
        "train_trades": len(train_scores),
        "positive_train_scores": len(positive),
        "zero_train_scores": sum(value == 0.0 for value in train_scores),
        "positive_q25": round(quantile(positive, 0.25), 6),
        "positive_q50": round(quantile(positive, 0.50), 6),
        "positive_q75": round(quantile(positive, 0.75), 6),
        "positive_q90_reference_only": round(quantile(positive, 0.90), 6),
        "positive_max_reference_only": round(max(positive), 6),
    }


def render_markdown(lock: dict[str, Any]) -> str:
    thresholds = lock["train_only_derivation"]
    return "\n".join(
        [
            "# Edge Liquidation Continuous Score Shadow Lock",
            "",
            f"Generated: `{lock['generated_at']}`",
            "",
            "- Threshold derivation uses Edge train rows only; OOS is not read for threshold selection.",
            f"- Train/positive scores: `{thresholds['train_trades']}` / `{thresholds['positive_train_scores']}`.",
            f"- Positive q25/q50/q75: `{thresholds['positive_q25']}` / `{thresholds['positive_q50']}` / `{thresholds['positive_q75']}`.",
            "- Score bins are labels only and cannot filter or veto Edge.",
            "- Automatic reselection is disabled.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze train-only continuous liquidation score bins for future Edge shadow evidence")
    parser.add_argument("--replay-report", default="docs/EDGE_LIQUIDATION_CONTEXT_HISTORICAL_REPLAY_2026-06-23.json")
    parser.add_argument("--lock-path", default="configs/EDGE_LIQUIDATION_SCORE_SHADOW_LOCK.json")
    parser.add_argument("--out-prefix", default="docs/EDGE_LIQUIDATION_SCORE_SHADOW_LOCK_2026-06-23")
    args = parser.parse_args()

    replay_path = resolve_path(args.replay_report)
    replay_bytes = replay_path.read_bytes()
    replay = json.loads(replay_bytes.decode("utf-8-sig"))
    reproduction = replay.get("reproduction") if isinstance(replay.get("reproduction"), dict) else {}
    if reproduction.get("exact_trade_count_match") is not True:
        raise ValueError("historical Edge reproduction must match before freezing score bins")
    labelled = replay.get("labelled_trades") if isinstance(replay.get("labelled_trades"), list) else []
    derived = derive_train_thresholds(labelled)
    q25 = derived["positive_q25"]
    q50 = derived["positive_q50"]
    q75 = derived["positive_q75"]
    lock = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "family": "EDGE_LIQUIDATION_CONTINUOUS_SHADOW",
        "status": "frozen_train_only_forward_shadow",
        "score_formula": "abs(displacement_atr) * max(0,-oi_delta_pct/2.0) * max(0,volume_z/1.5)",
        "source": {
            "replay_report": rel(replay_path),
            "replay_sha256": hashlib.sha256(replay_bytes).hexdigest().upper(),
            "edge_split": replay.get("data", {}).get("split_ts"),
            "threshold_source": "train_rows_only",
            "oos_used_for_thresholds": False,
        },
        "train_only_derivation": derived,
        "bins": [
            {"id": "inactive", "min_inclusive": 0.0, "max_inclusive": 0.0},
            {"id": "low", "min_exclusive": 0.0, "max_inclusive": q25},
            {"id": "medium", "min_exclusive": q25, "max_inclusive": q50},
            {"id": "elevated", "min_exclusive": q50, "max_inclusive": q75},
            {"id": "extreme", "min_exclusive": q75, "max_inclusive": None},
        ],
        "boundaries": {
            "label_only": True,
            "allow_parameter_changes": False,
            "allow_automatic_reselection": False,
            "allow_filter": False,
            "allow_veto": False,
            "allow_paper_execution": False,
            "allow_live_execution": False,
            "allow_orders": False,
            "can_trade": False,
        },
        "next_action": "label only new forward Edge evidence; require independent outcomes before any new research proposal",
        "can_trade": False,
    }
    lock_path = resolve_path(args.lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(lock), encoding="utf-8")
    print(json.dumps({"status": lock["status"], "train_only_derivation": derived, "lock": rel(lock_path), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
