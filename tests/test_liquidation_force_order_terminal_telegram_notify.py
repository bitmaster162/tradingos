from __future__ import annotations

import json
from pathlib import Path

from tools.liquidation_force_order_terminal_telegram_notify import main
from tools.liquidation_force_order_terminal_telegram_drill import build_report as build_drill_report


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def terminal_guard(path: Path) -> None:
    write_json(
        path,
        {
            "decision": "force_order_preregistered_guard_completed_pass_for_manual_forward_review",
            "state": {"completed": True, "pipeline_output": str(path.parent / "pipeline.json")},
            "can_trade": False,
        },
    )


def verified_receipt(evaluation_path: Path, terminal: str = "pass_for_manual_forward_review") -> dict:
    pipeline_decision = (
        "force_order_pipeline_pass_for_manual_forward_review"
        if terminal == "pass_for_manual_forward_review"
        else "force_order_pipeline_tombstone_review_required"
    )
    receipt = {
        "receipt_id": f"lock-sha:{pipeline_decision}",
        "lock_id": "force-order-v3",
        "evidence_chain_sha256": "a" * 64,
        "terminal_pipeline_decision": pipeline_decision,
        "terminal_evaluation_decision": terminal,
        "artifacts": {"evaluation_report": {"path": str(evaluation_path)}},
        "can_trade": False,
    }
    return {"decision": "terminal_receipt_verified", "receipt": receipt, "integrity_errors": [], "can_trade": False}


def evaluation(path: Path) -> None:
    write_json(
        path,
        {
            "evaluation": {
                "primary": {
                    "horizon_bars": 2,
                    "records": 80,
                    "independent_4h_blocks": 20,
                    "cluster_after_cost": {"mean_bps": 12.5, "winrate_positive_pct": 65.0},
                    "cluster_bootstrap": {"mean_ci_bps": [2.0, 20.0], "probability_mean_gt_zero": 0.99},
                },
                "positive_horizons_after_cost": 3,
                "symbol_concentration_diagnostics": {
                    "primary_largest_symbol_record_share_pct": 35.0,
                    "primary_sign_flip_symbols": [],
                },
            }
        },
    )


def argv(tmp_path: Path, guard_path: Path, send: bool = False) -> list[str]:
    values = [
        "notify",
        "--guard-report",
        str(guard_path),
        "--prereg-lock",
        str(tmp_path / "lock.json"),
        "--receipt",
        str(tmp_path / "receipt.json"),
        "--ledger",
        str(tmp_path / "ledger.jsonl"),
        "--state",
        str(tmp_path / "state.json"),
        "--card-prefix",
        str(tmp_path / "card"),
        "--out-prefix",
        str(tmp_path / "notify"),
    ]
    if send:
        values.append("--send")
    return values


def test_waiting_guard_never_calls_telegram_or_creates_card(tmp_path, monkeypatch) -> None:
    guard_path = tmp_path / "guard.json"
    write_json(guard_path, {"decision": "force_order_preregistered_guard_waiting_sample", "state": {}})
    monkeypatch.setattr("sys.argv", argv(tmp_path, guard_path, send=True))
    monkeypatch.setattr(
        "tools.liquidation_force_order_terminal_telegram_notify.send_telegram",
        lambda *_args: (_ for _ in ()).throw(AssertionError("telegram must not be called")),
    )

    assert main() == 0
    report = json.loads((tmp_path / "notify.json").read_text(encoding="utf-8"))
    assert report["decision"] == "skipped_waiting_terminal_receipt"
    assert not (tmp_path / "card.json").exists()


def test_dry_run_builds_manual_card_without_consuming_dedupe(tmp_path, monkeypatch) -> None:
    guard_path = tmp_path / "guard.json"
    evaluation_path = tmp_path / "evaluation.json"
    terminal_guard(guard_path)
    evaluation(evaluation_path)
    monkeypatch.setattr("sys.argv", argv(tmp_path, guard_path))
    monkeypatch.setattr(
        "tools.liquidation_force_order_terminal_telegram_notify.create_or_verify_terminal_receipt",
        lambda *_args: verified_receipt(evaluation_path),
    )

    assert main() == 0
    assert main() == 0
    report = json.loads((tmp_path / "notify.json").read_text(encoding="utf-8"))
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    card = json.loads((tmp_path / "card.json").read_text(encoding="utf-8"))
    assert report["decision"] == "dry_run_ready"
    assert state["notified_keys"] == []
    assert card["primary"]["net_cluster_mean_bps"] == 12.5
    assert card["approval_required"] is True
    assert card["can_trade"] is False


def test_successful_send_is_deduplicated_by_receipt_chain(tmp_path, monkeypatch) -> None:
    guard_path = tmp_path / "guard.json"
    evaluation_path = tmp_path / "evaluation.json"
    terminal_guard(guard_path)
    evaluation(evaluation_path)
    monkeypatch.setattr("sys.argv", argv(tmp_path, guard_path, send=True))
    monkeypatch.setattr(
        "tools.liquidation_force_order_terminal_telegram_notify.create_or_verify_terminal_receipt",
        lambda *_args: verified_receipt(evaluation_path),
    )
    monkeypatch.setattr(
        "tools.liquidation_force_order_terminal_telegram_notify.env_value",
        lambda *_args: "configured",
    )
    sends: list[str] = []
    monkeypatch.setattr(
        "tools.liquidation_force_order_terminal_telegram_notify.send_telegram",
        lambda _token, _chat, message, _timeout: sends.append(message) or {"ok": True},
    )

    assert main() == 0
    first = json.loads((tmp_path / "notify.json").read_text(encoding="utf-8"))
    assert main() == 0
    second = json.loads((tmp_path / "notify.json").read_text(encoding="utf-8"))

    assert first["decision"] == "sent"
    assert second["decision"] == "skipped_duplicate"
    assert len(sends) == 1


def test_receipt_integrity_failure_cannot_be_forced(tmp_path, monkeypatch) -> None:
    guard_path = tmp_path / "guard.json"
    terminal_guard(guard_path)
    arguments = argv(tmp_path, guard_path, send=True) + ["--force"]
    monkeypatch.setattr("sys.argv", arguments)
    monkeypatch.setattr(
        "tools.liquidation_force_order_terminal_telegram_notify.create_or_verify_terminal_receipt",
        lambda *_args: {"decision": "terminal_receipt_integrity_blocked", "integrity_errors": ["tampered"]},
    )

    assert main() == 2
    report = json.loads((tmp_path / "notify.json").read_text(encoding="utf-8"))
    assert report["decision"] == "blocked_terminal_receipt_integrity"
    assert not (tmp_path / "card.json").exists()


def test_synthetic_terminal_telegram_drill_covers_pass_tombstone_and_tamper(tmp_path) -> None:
    report = build_drill_report(tmp_path)

    assert report["decision"] == "force_order_terminal_telegram_drill_passed"
    assert all(report["checks"].values())
    assert report["boundary"]["sends_telegram"] is False
