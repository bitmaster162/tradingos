#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bybit_all_liquidation_context_intake as legacy


IMPACTED_FAMILIES = [
    {
        "family": "BYBIT_LIQUIDATION_FORWARD_OBSERVER",
        "decision": "bybit_liquidation_forward_semantic_contract_invalid_tombstone",
        "report": "docs/BYBIT_LIQUIDATION_FORWARD_SEMANTIC_TOMBSTONE_2026-07-13",
        "locks": ["configs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_LOCK_2026-07-02.json"],
    },
    {
        "family": "POST_LIQUIDATION_ABSORPTION_SPOT_PERP",
        "decision": "post_liquidation_absorption_semantic_contract_invalid_tombstone",
        "report": "docs/POST_LIQUIDATION_ABSORPTION_SEMANTIC_TOMBSTONE_2026-07-13",
        "locks": ["configs/POST_LIQUIDATION_ABSORPTION_SPOT_PERP_RESEARCH_LOCK_2026-07-03.json"],
    },
    {
        "family": "LIQUIDATION_TIMING_VOL_CONTINUATION",
        "decision": "liquidation_timing_vol_semantic_contract_invalid_tombstone",
        "report": "docs/LIQUIDATION_TIMING_VOL_SEMANTIC_TOMBSTONE_2026-07-13",
        "locks": ["configs/LIQUIDATION_TIMING_VOL_CONTINUATION_FORWARD_LOCK_2026-07-03.json"],
    },
]


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def context_counts(report: dict[str, Any]) -> dict[str, int]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    contexts = summary.get("contexts") if isinstance(summary.get("contexts"), dict) else {}
    return {
        "long_liquidation_flush": int(contexts.get("long_liquidation_flush") or 0),
        "short_liquidation_squeeze": int(contexts.get("short_liquidation_squeeze") or 0),
        "mixed": int(contexts.get("mixed") or 0),
    }


def build_report(legacy_report: dict[str, Any], canonical_report: dict[str, Any]) -> dict[str, Any]:
    legacy_counts = context_counts(legacy_report)
    canonical_counts = context_counts(canonical_report)
    legacy_summary = legacy_report.get("summary") if isinstance(legacy_report.get("summary"), dict) else {}
    canonical_summary = canonical_report.get("summary") if isinstance(canonical_report.get("summary"), dict) else {}
    same_sample_shape = all(
        int(legacy_summary.get(key) or 0) == int(canonical_summary.get(key) or 0)
        for key in ("events", "aggregate_rows", "matched_price_bars")
    )
    exact_directional_swap = (
        legacy_counts["long_liquidation_flush"] == canonical_counts["short_liquidation_squeeze"]
        and legacy_counts["short_liquidation_squeeze"] == canonical_counts["long_liquidation_flush"]
        and legacy_counts["mixed"] == canonical_counts["mixed"]
    )
    contract_failure_proven = same_sample_shape and exact_directional_swap
    return {
        "generated_at": legacy.now_iso(),
        "tool": "tools/bybit_liquidation_side_semantics_audit.py",
        "decision": (
            "bybit_liquidation_side_semantics_v1_terminal_contract_failure"
            if contract_failure_proven
            else "bybit_liquidation_side_semantics_audit_inconclusive_fail_closed"
        ),
        "edge_evaluated": False,
        "can_trade": False,
        "contract_failure_proven": contract_failure_proven,
        "source_contract": {
            "official_meaning": "Bybit BUY means a long position was liquidated; SELL means a short position was liquidated.",
            "legacy_v1_mapping": {"BUY": "short_liquidation_squeeze", "SELL": "long_liquidation_flush"},
            "canonical_v2_mapping": {"BUY": "long_liquidation_flush", "SELL": "short_liquidation_squeeze"},
            "reference": "https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation",
        },
        "same_input_diagnostic": {
            "same_sample_shape": same_sample_shape,
            "exact_directional_context_swap": exact_directional_swap,
            "legacy_counts": legacy_counts,
            "canonical_counts": canonical_counts,
            "events": int(canonical_summary.get("events") or 0),
            "aggregate_rows": int(canonical_summary.get("aggregate_rows") or 0),
            "matched_price_bars": int(canonical_summary.get("matched_price_bars") or 0),
        },
        "impacted_families": IMPACTED_FAMILIES,
        "resolution": {
            "legacy_tools_or_locks_modified": False,
            "legacy_directional_results_valid": False,
            "legacy_forward_observers_must_continue": False,
            "corrected_v2_output_may_feed_old_locks": False,
            "corrected_discovery_requires_new_future_floor": True,
        },
        "runtime_boundary": {
            "audit_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    diagnostic = report["same_input_diagnostic"]
    lines = [
        "# Bybit Liquidation Side-Semantics Audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Contract failure proven: `{report['contract_failure_proven']}`",
        "- Edge evaluated: `false`",
        "- Can trade: `false`",
        "",
        "## Finding",
        "",
        "The legacy intake inverted Bybit position-side semantics. On the same source sample, all directional "
        "context bars swap labels while mixed bars remain unchanged.",
        "",
        "| Label | Legacy V1 | Canonical V2 |",
        "|---|---:|---:|",
        f"| `long_liquidation_flush` | `{diagnostic['legacy_counts']['long_liquidation_flush']}` | `{diagnostic['canonical_counts']['long_liquidation_flush']}` |",
        f"| `short_liquidation_squeeze` | `{diagnostic['legacy_counts']['short_liquidation_squeeze']}` | `{diagnostic['canonical_counts']['short_liquidation_squeeze']}` |",
        f"| `mixed` | `{diagnostic['legacy_counts']['mixed']}` | `{diagnostic['canonical_counts']['mixed']}` |",
        "",
        "## Quarantine",
        "",
    ]
    for item in report["impacted_families"]:
        lines.append(f"- `{item['family']}`: `{item['decision']}`")
    lines.extend(
        [
            "",
            "Old files remain unchanged for reproducibility. Corrected discovery cannot inherit an old lock and "
            "cannot enable trading.",
            "",
        ]
    )
    return "\n".join(lines)


def write_status_aliases(report: dict[str, Any]) -> None:
    for item in IMPACTED_FAMILIES:
        out = legacy.resolve_path(item["report"])
        payload = {
            "generated_at": report["generated_at"],
            "tool": "tools/bybit_liquidation_side_semantics_audit.py",
            "decision": item["decision"],
            "family": item["family"],
            "status": "tombstoned_no_retune",
            "reason": "legacy Bybit directional labels inverted the documented liquidated-position side",
            "source_audit": "docs/BYBIT_LIQUIDATION_SIDE_SEMANTICS_AUDIT_2026-07-13.json",
            "legacy_locks": item["locks"],
            "edge_evaluated": False,
            "can_trade": False,
            "orders_allowed": False,
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        out.with_suffix(".md").write_text(
            "\n".join(
                [
                    f"# {item['family']} Semantic Tombstone",
                    "",
                    f"- Decision: `{item['decision']}`",
                    "- Status: `tombstoned_no_retune`",
                    "- Edge evaluated by this audit: `false`",
                    "- Can trade: `false`",
                    "",
                    "The legacy Bybit context label inverted documented position-side semantics. The historical lock "
                    "is preserved but cannot support directional evidence or continued forward scoring.",
                    "",
                ]
            ),
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed audit for legacy Bybit liquidation side labels")
    parser.add_argument(
        "--legacy-report", default="docs/BYBIT_ALL_LIQUIDATION_LEGACY_SIDE_DIAGNOSTIC_2026-07-13.json"
    )
    parser.add_argument(
        "--canonical-report", default="docs/BYBIT_ALL_LIQUIDATION_CANONICAL_CONTEXT_INTAKE_V2_2026-07-13.json"
    )
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_SIDE_SEMANTICS_AUDIT_2026-07-13")
    args = parser.parse_args()
    report = build_report(
        read_json(legacy.resolve_path(args.legacy_report)),
        read_json(legacy.resolve_path(args.canonical_report)),
    )
    out = legacy.resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    write_status_aliases(report)
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "contract_failure_proven": report["contract_failure_proven"],
                "impacted_families": len(report["impacted_families"]),
                "edge_evaluated": False,
                "can_trade": False,
            },
            indent=2,
        )
    )
    return 0 if report["contract_failure_proven"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
