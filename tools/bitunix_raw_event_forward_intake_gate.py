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

from tools import bitunix_raw_event_replenishment_oracle as oracle


DEFAULT_CONFIG = "configs/BITUNIX_RAW_EVENT_REPLENISHMENT_PREREG_2026-07-16.json"
DEFAULT_LOCK = "configs/BITUNIX_RAW_EVENT_REPLENISHMENT_LOCK_2026-07-16.json"
DEFAULT_CAPTURE_ROOT = "data/forward/bitunix_wo105_v3r4_ws"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def discover_completed_runs(capture_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    source = config["source_contract"]
    floor_ms = oracle.parse_iso_ms(config["forward_floor_utc"])
    selected: list[Path] = []
    pre_floor: list[str] = []
    in_progress: list[str] = []
    invalid_metadata: list[str] = []
    if not capture_root.is_dir():
        return {
            "selected": [],
            "pre_floor": [],
            "in_progress": [],
            "invalid_metadata": [],
            "failures": ["capture_root_missing"],
        }
    for run_dir in sorted((path for path in capture_root.iterdir() if path.is_dir()), key=lambda path: path.name):
        manifest_path = run_dir / source["manifest_file"]
        acceptance_path = run_dir / source["acceptance_file"]
        if not manifest_path.is_file() or not acceptance_path.is_file():
            in_progress.append(portable(run_dir))
            continue
        try:
            manifest = read_object(manifest_path)
        except (OSError, ValueError, json.JSONDecodeError):
            invalid_metadata.append(portable(run_dir))
            continue
        started_ms = oracle.parse_iso_ms(manifest.get("started_utc"))
        ended_ms = oracle.parse_iso_ms(manifest.get("ended_utc"))
        if started_ms is None or ended_ms is None or ended_ms < started_ms:
            invalid_metadata.append(portable(run_dir))
        elif floor_ms is None or started_ms < floor_ms:
            pre_floor.append(portable(run_dir))
        else:
            selected.append(run_dir)
    return {
        "selected": selected,
        "pre_floor": pre_floor,
        "in_progress": in_progress,
        "invalid_metadata": invalid_metadata,
        "failures": ["completed_capture_metadata_invalid"] if invalid_metadata else [],
    }


def source_hashes(run_dirs: list[Path], config: dict[str, Any]) -> dict[str, str]:
    source = config["source_contract"]
    names = (
        source["manifest_file"],
        source["acceptance_file"],
        source["raw_frames_file"],
        source["raw_index_file"],
    )
    result: dict[str, str] = {}
    for run_dir in run_dirs:
        for name in names:
            path = run_dir / name
            if path.is_file():
                result[f"{portable(run_dir)}/{name}"] = sha256_file(path)
    return dict(sorted(result.items()))


def build_report(
    *,
    config_path: Path,
    lock_path: Path,
    capture_root: Path,
) -> dict[str, Any]:
    config = read_object(config_path)
    config_failures = oracle.validate_config(config)
    lock_failures = oracle.validate_lock(lock_path, config_path)
    discovery = discover_completed_runs(capture_root, config)
    selected: list[Path] = discovery.pop("selected")
    failures = sorted(set(config_failures + lock_failures + discovery["failures"]))
    before = source_hashes(selected, config)
    oracle_report: dict[str, Any] | None = None
    if not failures and selected:
        oracle_report = oracle.build_report(
            config_path,
            selected,
            mode="blind-forward",
            lock_path=lock_path,
        )
        if oracle_report.get("quality_pass") is not True:
            failures.append("oracle_source_quality_fail")
    after = source_hashes(selected, config)
    input_immutable = before == after
    if not input_immutable:
        failures.append("input_mutation_detected")

    if failures:
        decision = "FORWARD_INTAKE_BLOCKED_FAIL_CLOSED"
        edge_rows = 0
        visibility = "HIDDEN_INTAKE_FAILURE"
    elif not selected:
        decision = "WAIT_NO_COMPLETED_POST_FLOOR_CAPTURE"
        edge_rows = 0
        visibility = "HIDDEN_UNTIL_TERMINAL_GATE"
    else:
        decision = str(oracle_report["decision"])
        edge_rows = int(oracle_report["edge_rows_admitted"])
        visibility = str(oracle_report["outcome_metrics"].get("visibility", "TERMINAL_GATE_OPEN"))

    selected_rows = [portable(path) for path in selected]
    return {
        "schema": "bitunix-raw-event-forward-intake-gate-v1",
        "generated_at_utc": now_iso(),
        "decision": decision,
        "prereg_id": config.get("prereg_id"),
        "forward_floor_utc": config.get("forward_floor_utc"),
        "capture_root": portable(capture_root),
        "selected_completed_post_floor_runs": selected_rows,
        "selected_run_count": len(selected_rows),
        "discovery": discovery,
        "failures": sorted(set(failures)),
        "input_hashes_before": before,
        "input_hashes_after": after,
        "input_immutable": input_immutable,
        "edge_rows_admitted": edge_rows,
        "outcome_visibility": visibility,
        "oracle_report": oracle_report,
        "runtime_boundary": {
            "manual_invocation_only": True,
            "autoload_changed": False,
            "collector_created": False,
            "network_calls": 0,
            "telegram_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "capital_permission": "DENY",
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Bitunix Raw Event Forward Intake Gate",
            "",
            f"- Generated: `{report['generated_at_utc']}`.",
            f"- Decision: `{report['decision']}`.",
            f"- Forward floor: `{report['forward_floor_utc']}`.",
            f"- Selected completed post-floor runs: `{report['selected_run_count']}`.",
            f"- Edge rows admitted: `{report['edge_rows_admitted']}`.",
            f"- Outcome visibility: `{report['outcome_visibility']}`.",
            f"- Input immutable: `{report['input_immutable']}`.",
            f"- Failures: `{', '.join(report['failures']) or 'none'}`.",
            "- This gate is manual-only and does not create or restart a collector.",
            "- No network, Telegram, signal, paper-entry, order or capital path is available.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual fail-closed intake for completed post-floor Bitunix captures")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--lock", default=DEFAULT_LOCK)
    parser.add_argument("--capture-root", default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--out-prefix", default="_dl/bitunix_raw_event_replenishment_v1/FORWARD_INTAKE")
    args = parser.parse_args()

    report = build_report(
        config_path=resolve(args.config),
        lock_path=resolve(args.lock),
        capture_root=resolve(args.capture_root),
    )
    out_prefix = resolve(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "selected_run_count": report["selected_run_count"],
                "edge_rows_admitted": report["edge_rows_admitted"],
                "outcome_visibility": report["outcome_visibility"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
