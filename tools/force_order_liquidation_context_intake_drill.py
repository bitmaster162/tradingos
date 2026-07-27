#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APPROVED_SOURCE = "binance_usdm_forceOrder_websocket"


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


def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": str(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": str(path)}


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def fixture_row(symbol: str, side: str, event_time_ms: int, price: float, quantity: float) -> dict[str, Any]:
    return {
        "event_time_ms": event_time_ms,
        "event_time": ms_to_iso(event_time_ms),
        "trade_time_ms": event_time_ms,
        "trade_time": ms_to_iso(event_time_ms),
        "symbol": symbol.upper(),
        "side": side.upper(),
        "price": price,
        "quantity": quantity,
        "notional_usd": round(price * quantity, 8),
        "source": APPROVED_SOURCE,
        "is_real_liquidation_feed": True,
        "fixture_only": True,
    }


def write_fixture(data_dir: Path) -> list[dict[str, Any]]:
    rows = [
        fixture_row("BTCUSDT", "SELL", 1609459800000, 29000.0, 0.50),
        fixture_row("BTCUSDT", "SELL", 1609460400000, 29100.0, 0.40),
        fixture_row("ETHUSDT", "BUY", 1609463400000, 740.0, 20.0),
        fixture_row("ETHUSDT", "BUY", 1609463700000, 742.0, 10.0),
        fixture_row("SOLUSDT", "BUY", 1609464000000, 1.55, 5000.0),
        fixture_row("SOLUSDT", "SELL", 1609464300000, 1.56, 5000.0),
    ]
    for row in rows:
        symbol_dir = data_dir / row["symbol"]
        append_jsonl(symbol_dir / "20210101.jsonl", row)
    return rows


def reset_fixture_dir(work_dir: Path, data_dir: Path) -> None:
    resolved_work = work_dir.resolve()
    resolved_data = data_dir.resolve()
    if resolved_data == resolved_work or resolved_work not in resolved_data.parents:
        raise RuntimeError(f"refusing to reset unsafe fixture path: {resolved_data}")
    if "_dl" not in resolved_data.parts or "runtime_drills" not in resolved_data.parts:
        raise RuntimeError(f"refusing to reset non-drill fixture path: {resolved_data}")
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)


def run_intake(data_dir: Path, out_prefix: Path, timeout_s: int) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "force_order_liquidation_context_intake.py"),
        "--data-dir",
        str(data_dir),
        "--symbols",
        "BTCUSDT,ETHUSDT,SOLUSDT",
        "--interval",
        "1h",
        "--min-events-for-research",
        "6",
        "--min-event-bars-for-research",
        "3",
        "--out-prefix",
        str(out_prefix),
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout_s, check=False)
    report = read_json(out_prefix.with_suffix(".json"))
    return {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout[-12000:],
        "stderr": result.stderr[-12000:],
        "report": report,
    }


def render_markdown(report: dict[str, Any]) -> str:
    checks = report.get("checks", {})
    lines = [
        "# ForceOrder Context Intake Drill",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        f"- Fixture rows: `{report['fixture_rows']}`",
        f"- Intake decision: `{report['intake'].get('decision')}`",
        "",
        "## Boundary",
        "",
        "- Synthetic fixture drill only.",
        "- Fixture is written under `_dl/runtime_drills`, never under live forceOrder storage.",
        "- Does not enter frontier as real evidence.",
        "- Does not create signals, intents, notifications or orders.",
        "",
        "## Checks",
        "",
    ]
    for key, value in checks.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Intake Output", "", f"- `{report.get('intake_report_path')}`", f"- `{report.get('intake_csv_path')}`", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic fixture drill for forceOrder context intake plumbing")
    parser.add_argument("--work-dir", default="_dl/runtime_drills/force_order_liquidation_context_intake")
    parser.add_argument("--out-prefix", default="docs/FORCE_ORDER_CONTEXT_INTAKE_DRILL_2026-07-01")
    parser.add_argument("--timeout-s", type=int, default=60)
    args = parser.parse_args()

    work_dir = resolve_path(args.work_dir)
    data_dir = work_dir / "fixture_feed"
    intake_prefix = work_dir / "fixture_intake"
    reset_fixture_dir(work_dir, data_dir)
    fixture_rows = write_fixture(data_dir)
    intake_run = run_intake(data_dir, intake_prefix, args.timeout_s)
    intake = intake_run["report"]
    summary = intake.get("summary") if isinstance(intake.get("summary"), dict) else {}
    context_counts = intake.get("context_counts") if isinstance(intake.get("context_counts"), dict) else {}
    checks = {
        "intake_exit_zero": intake_run["exit_code"] == 0,
        "intake_can_trade_false": intake.get("can_trade") is False,
        "intake_ready_on_fixture": intake.get("decision") == "force_order_context_ready_for_preregistered_research",
        "events_count_match": summary.get("events") == len(fixture_rows),
        "event_bars_count_match": summary.get("event_bars") == 3,
        "matched_event_bars_count_match": summary.get("matched_event_bars") == 3,
        "long_flush_present": int(context_counts.get("long_liquidation_flush") or 0) >= 1,
        "short_squeeze_present": int(context_counts.get("short_liquidation_squeeze") or 0) >= 1,
        "mixed_present": int(context_counts.get("mixed") or 0) >= 1,
        "aggregate_csv_written": bool(intake.get("aggregate_csv")) and resolve_path(str(intake.get("aggregate_csv"))).exists(),
    }
    passed = all(checks.values())
    report = {
        "generated_at": now_iso(),
        "tool": "tools/force_order_liquidation_context_intake_drill.py",
        "decision": "force_order_context_intake_drill_passed" if passed else "force_order_context_intake_drill_failed",
        "can_trade": False,
        "boundary": {
            "synthetic_fixture_only": True,
            "writes_live_feed": False,
            "enters_frontier_as_evidence": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "fixture_rows": len(fixture_rows),
        "fixture_data_dir": portable(data_dir),
        "intake_report_path": portable(intake_prefix.with_suffix(".json")),
        "intake_csv_path": intake.get("aggregate_csv"),
        "intake": {
            "decision": intake.get("decision"),
            "summary": summary,
            "context_counts": context_counts,
            "by_symbol": intake.get("by_symbol"),
            "can_trade": intake.get("can_trade"),
        },
        "run": {key: value for key, value in intake_run.items() if key != "report"},
        "checks": checks,
        "next_action": "fixture plumbing ready; wait for real forceOrder rows" if passed else "fix intake plumbing before relying on real forceOrder rows",
    }
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "checks_passed": sum(checks.values()), "checks_total": len(checks), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
