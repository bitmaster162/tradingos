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

from tools.forward_runtime_health_telegram_notify import env_value, send_telegram  # noqa: E402
from tools.liquidation_force_order_terminal_receipt import (  # noqa: E402
    atomic_write_json,
    create_or_verify_terminal_receipt,
    resolve_path,
)


TERMINAL_GUARD_DECISIONS = {
    "force_order_preregistered_guard_completed_pass_for_manual_forward_review",
    "force_order_preregistered_guard_completed_tombstone_review_required",
    "force_order_preregistered_guard_already_completed",
}


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def notification_kind(receipt: dict[str, Any]) -> str:
    decision = str(receipt.get("terminal_evaluation_decision") or "")
    if decision == "pass_for_manual_forward_review":
        return "force_order_pass_for_manual_forward_review"
    if decision == "tombstone_review_required":
        return "force_order_tombstone_review_required"
    return "force_order_terminal_unknown"


def notification_key(receipt: dict[str, Any]) -> str:
    return "|".join(
        [
            str(receipt.get("receipt_id") or "missing_receipt_id"),
            str(receipt.get("evidence_chain_sha256") or "missing_chain_hash"),
        ]
    )


def review_card(kind: str, receipt: dict[str, Any], evaluation_report: dict[str, Any]) -> dict[str, Any]:
    evaluated = evaluation_report.get("evaluation") if isinstance(evaluation_report.get("evaluation"), dict) else {}
    primary = evaluated.get("primary") if isinstance(evaluated.get("primary"), dict) else {}
    cluster = primary.get("cluster_after_cost") if isinstance(primary.get("cluster_after_cost"), dict) else {}
    bootstrap = primary.get("cluster_bootstrap") if isinstance(primary.get("cluster_bootstrap"), dict) else {}
    concentration = (
        evaluated.get("symbol_concentration_diagnostics")
        if isinstance(evaluated.get("symbol_concentration_diagnostics"), dict)
        else {}
    )
    passed = kind == "force_order_pass_for_manual_forward_review"
    actions = (
        [
            "Verify data-quality and receipt provenance manually.",
            "Inspect leave-one-symbol-out and concentration diagnostics.",
            "Approve a separate untouched forward-observer lock or reject; never auto-promote this result.",
        ]
        if passed
        else [
            "Check whether failure is caused by data integrity rather than economics.",
            "If data is valid, register the hypothesis as a tombstone.",
            "Do not retune thresholds, horizons or contexts on the opened sample.",
        ]
    )
    return {
        "card_type": kind,
        "title": "FORCEORDER EDGE: PASS FOR MANUAL REVIEW" if passed else "FORCEORDER EDGE: TOMBSTONE REVIEW",
        "lock_id": receipt.get("lock_id"),
        "receipt_id": receipt.get("receipt_id"),
        "evidence_chain_sha256": receipt.get("evidence_chain_sha256"),
        "terminal_pipeline_decision": receipt.get("terminal_pipeline_decision"),
        "terminal_evaluation_decision": receipt.get("terminal_evaluation_decision"),
        "primary": {
            "horizon_bars": primary.get("horizon_bars"),
            "records": primary.get("records"),
            "independent_4h_blocks": primary.get("independent_4h_blocks"),
            "net_cluster_mean_bps": cluster.get("mean_bps"),
            "net_cluster_winrate_pct": cluster.get("winrate_positive_pct"),
            "bootstrap_mean_ci_bps": bootstrap.get("mean_ci_bps"),
            "bootstrap_probability_mean_gt_zero": bootstrap.get("probability_mean_gt_zero"),
        },
        "positive_horizons_after_cost": evaluated.get("positive_horizons_after_cost"),
        "symbol_concentration_diagnostics": concentration,
        "manual_actions": actions,
        "approval_required": True,
        "automatic_promotion_allowed": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def render_message(card: dict[str, Any]) -> str:
    primary = card.get("primary") if isinstance(card.get("primary"), dict) else {}
    concentration = (
        card.get("symbol_concentration_diagnostics")
        if isinstance(card.get("symbol_concentration_diagnostics"), dict)
        else {}
    )
    chain = str(card.get("evidence_chain_sha256") or "")
    lines = [
        str(card.get("title") or "FORCEORDER TERMINAL REVIEW"),
        "",
        f"Decision: {card.get('terminal_evaluation_decision')}",
        f"Lock: {card.get('lock_id')}",
        f"Evidence: {chain[:16]}..." if chain else "Evidence: missing",
        "",
        f"Primary horizon: {primary.get('horizon_bars')}h",
        f"Independent 4H blocks: {primary.get('independent_4h_blocks')}",
        f"Net cluster mean: {primary.get('net_cluster_mean_bps')} bps",
        f"Net cluster winrate: {primary.get('net_cluster_winrate_pct')}%",
        f"Bootstrap CI: {primary.get('bootstrap_mean_ci_bps')}",
        f"Positive horizons after cost: {card.get('positive_horizons_after_cost')}",
        f"Largest-symbol share: {concentration.get('primary_largest_symbol_record_share_pct')}%",
        f"Leave-one-out sign flips: {concentration.get('primary_sign_flip_symbols')}",
        "",
        "MANUAL REVIEW REQUIRED. No signal, no paper entry, no order, can_trade=false.",
    ]
    return "\n".join(lines)


def terminal_candidate(guard: dict[str, Any]) -> tuple[bool, str | None]:
    decision = str(guard.get("decision") or "")
    state = guard.get("state") if isinstance(guard.get("state"), dict) else {}
    if decision not in TERMINAL_GUARD_DECISIONS:
        return False, None
    if state.get("completed") is not True:
        return False, "guard_terminal_without_completed_state"
    pipeline_output = str(state.get("pipeline_output") or "")
    if not pipeline_output:
        return False, "guard_terminal_without_pipeline_output"
    return True, pipeline_output


def write_card(prefix: Path, card: dict[str, Any]) -> None:
    atomic_write_json(prefix.with_suffix(".json"), card)
    prefix.with_suffix(".md").write_text(render_message(card) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Receipt-gated Telegram notifier for terminal Binance forceOrder research")
    parser.add_argument("--guard-report", default="docs/LIQUIDATION_FORCE_ORDER_PREREGISTERED_SAMPLE_GUARD_2026-07-12.json")
    parser.add_argument("--prereg-lock", default="configs/BINANCE_FORCE_ORDER_EVENT_STUDY_PREREG_2026-07-12.json")
    parser.add_argument("--receipt", default="logs/liquidation_force_order/preregistered_terminal_receipt.json")
    parser.add_argument("--ledger", default="logs/liquidation_force_order/preregistered_terminal_receipts.jsonl")
    parser.add_argument("--state", default="logs/liquidation_force_order/terminal_telegram_state.json")
    parser.add_argument("--card-prefix", default="docs/LIQUIDATION_FORCE_ORDER_TERMINAL_REVIEW_CARD_2026-07-12")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_FORCE_ORDER_TERMINAL_TELEGRAM_2026-07-12")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    guard_path = resolve_path(args.guard_report)
    lock_path = resolve_path(args.prereg_lock)
    receipt_path = resolve_path(args.receipt)
    ledger_path = resolve_path(args.ledger)
    state_path = resolve_path(args.state)
    card_prefix = resolve_path(args.card_prefix)
    out_prefix = resolve_path(args.out_prefix)
    guard = read_json(guard_path)
    state = read_json(state_path)
    candidate, pipeline_output = terminal_candidate(guard)
    receipt_check: dict[str, Any] | None = None
    card: dict[str, Any] | None = None
    kind = "waiting_no_notification"
    key = "waiting"
    response: dict[str, Any] | None = None
    integrity_error = None

    if not guard:
        decision = "skipped_missing_guard_report"
    elif not candidate:
        if pipeline_output:
            integrity_error = pipeline_output
            decision = "blocked_terminal_guard_integrity"
        else:
            decision = "skipped_waiting_terminal_receipt"
    else:
        pipeline_path = resolve_path(str(pipeline_output))
        receipt_check = create_or_verify_terminal_receipt(lock_path, pipeline_path, receipt_path, ledger_path)
        if receipt_check.get("decision") not in {"terminal_receipt_created", "terminal_receipt_verified"}:
            decision = "blocked_terminal_receipt_integrity"
            integrity_error = ",".join(str(item) for item in receipt_check.get("integrity_errors") or [])
        else:
            receipt = receipt_check.get("receipt") if isinstance(receipt_check.get("receipt"), dict) else {}
            kind = notification_kind(receipt)
            key = notification_key(receipt)
            evaluation_descriptor = (receipt.get("artifacts") or {}).get("evaluation_report") or {}
            evaluation_report = read_json(resolve_path(str(evaluation_descriptor.get("path") or "")))
            card = review_card(kind, receipt, evaluation_report)
            write_card(card_prefix, card)
            notified = {
                str(item) for item in state.get("notified_keys", [])
            } if isinstance(state.get("notified_keys"), list) else set()
            if kind == "force_order_terminal_unknown":
                decision = "blocked_unknown_terminal_decision"
            elif key in notified and not args.force:
                decision = "skipped_duplicate"
            elif not args.send:
                decision = "dry_run_ready"
            else:
                env_files = [resolve_path(item) for item in args.env_file]
                token = env_value(args.token_env, env_files)
                chat_id = env_value(args.chat_id_env, env_files)
                if not token or not chat_id:
                    decision = "skipped_missing_telegram_env"
                else:
                    try:
                        response = send_telegram(token, chat_id, render_message(card), args.timeout_s)
                        decision = "sent" if response.get("ok") else "telegram_api_error"
                    except Exception as exc:  # noqa: BLE001
                        response = {"ok": False, "error_type": type(exc).__name__}
                        decision = "telegram_send_error"
            if decision == "sent":
                notified.add(key)
            state.update(
                {
                    "notified_keys": sorted(notified)[-100:],
                    "last_decision": decision,
                    "last_kind": kind,
                    "last_key": key,
                    "last_receipt_id": receipt.get("receipt_id"),
                    "last_evidence_chain_sha256": receipt.get("evidence_chain_sha256"),
                    "can_trade": False,
                }
            )
            atomic_write_json(state_path, state)

    output = {
        "tool": "tools/liquidation_force_order_terminal_telegram_notify.py",
        "decision": decision,
        "kind": kind,
        "notification_key": key,
        "send_requested": args.send,
        "guard_report": str(guard_path),
        "receipt_check": receipt_check,
        "review_card": card,
        "integrity_error": integrity_error,
        "telegram_response_ok": response.get("ok") if isinstance(response, dict) else None,
        "message_preview": render_message(card) if card else None,
        "boundary": {
            "terminal_research_notification_only": True,
            "receipt_required": True,
            "automatic_promotion": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "uses_exchange_credentials": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    atomic_write_json(out_prefix.with_suffix(".json"), output)
    print(json.dumps({"decision": decision, "kind": kind, "send_requested": args.send, "can_trade": False}, ensure_ascii=False, indent=2))
    return 2 if decision in {
        "blocked_terminal_guard_integrity",
        "blocked_terminal_receipt_integrity",
        "blocked_unknown_terminal_decision",
        "telegram_api_error",
        "telegram_send_error",
    } else 0


if __name__ == "__main__":
    raise SystemExit(main())
