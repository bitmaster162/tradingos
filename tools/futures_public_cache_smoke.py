#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FUTURES_DIR = ROOT / "ops" / "btcusdt_binance_futures_bot"
FUTURES_SRC = FUTURES_DIR / "src"
LIVE_PUBLIC_DATA_DIR_REL = "data/public_live_smoke"
LIVE_PUBLIC_DATA_DIR_FROM_ROOT = "ops/btcusdt_binance_futures_bot/data/public_live_smoke"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_step(name: str, command: list[str], *, cwd: Path, env: dict[str, str], timeout_s: int) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_s,
    )
    ended = datetime.now(timezone.utc)
    parsed_stdout = parse_json_object(proc.stdout)
    return {
        "name": name,
        "command": command,
        "cwd": str(cwd),
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "parsed_stdout": parsed_stdout,
        "duration_s": round((ended - started).total_seconds(), 3),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def parse_json_object(value: str) -> dict[str, Any] | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def enforce_step_contracts(results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    for step in results:
        parsed = step.get("parsed_stdout") if isinstance(step.get("parsed_stdout"), dict) else {}
        status = parsed.get("status") if isinstance(parsed.get("status"), dict) else {}
        validation_errors: list[str] = []
        if step["name"] == "collect_market" and int(status.get("messages_received", 0) or 0) < args.market_messages:
            validation_errors.append("market_messages_below_requested")
        if step["name"] == "collect_book_ticker" and int(status.get("messages_received", 0) or 0) < args.book_messages:
            validation_errors.append("book_messages_below_requested")
        if step["name"] == "collect_depth_book":
            if int(status.get("messages_received", 0) or 0) < args.depth_messages:
                validation_errors.append("depth_messages_below_requested")
            if int(status.get("local_snapshots_written", 0) or 0) < 1:
                validation_errors.append("depth_local_snapshot_not_written")
        if step["name"] == "collect_crowding" and int(status.get("snapshots_written", 0) or 0) < 1:
            validation_errors.append("crowding_snapshot_not_written")
        if validation_errors:
            step["passed"] = False
            step["validation_errors"] = validation_errors


def write_outputs(report: dict[str, Any], out_prefix: Path) -> tuple[Path, Path]:
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Futures Public Cache Smoke",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Public market data only.",
        "- No private credentials.",
        "- No order submission.",
        "- No live-trading permission.",
        "",
        "## Result",
        "",
        f"- Overall passed: `{report['passed']}`.",
        f"- Flow report: `{report.get('flow_report_json', '')}`.",
        "",
        "## Steps",
        "",
    ]
    for step in report["steps"]:
        lines.extend(
            [
                f"### {step['name']}",
                "",
                f"- Passed: `{step['passed']}`.",
                f"- Return code: `{step['returncode']}`.",
                f"- Duration seconds: `{step['duration_s']}`.",
                "",
            ]
        )
        if step.get("stdout_tail"):
            lines.extend(["Stdout tail:", "", "```text", step["stdout_tail"].strip(), "```", ""])
        if step.get("stderr_tail"):
            lines.extend(["Stderr tail:", "", "```text", step["stderr_tail"].strip(), "```", ""])
    lines.extend(
        [
            "## Use Policy",
            "",
            "- Treat a successful run as data-ingestion proof only.",
            "- Treat flow-toxicity output as guard/abstention evidence only.",
            "- Do not promote any strategy until sample size and OOS/paper gates pass.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Public-only futures data cache smoke + flow-toxicity report")
    parser.add_argument("--market-messages", type=int, default=20)
    parser.add_argument("--book-messages", type=int, default=20)
    parser.add_argument("--depth-messages", type=int, default=50)
    parser.add_argument("--depth-levels", type=int, default=20)
    parser.add_argument("--snapshot-limit", type=int, default=1000)
    parser.add_argument("--crowding-period", default="5m")
    parser.add_argument("--timeout-s", type=int, default=90)
    parser.add_argument("--out-prefix", default="docs/FUTURES_PUBLIC_CACHE_SMOKE_2026-06-08")
    parser.add_argument("--flow-out-prefix", default="docs/FLOW_TOXICITY_FEATURE_REPORT_REAL_2026-06-08")
    args = parser.parse_args()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(FUTURES_SRC)
    env["BOT_ENV"] = "demo"
    env["BOT_REST_BASE_URL"] = "https://fapi.binance.com"
    env["BOT_WS_PUBLIC_BASE_URL"] = "wss://fstream.binance.com/public"
    env["BOT_WS_MARKET_BASE_URL"] = "wss://fstream.binance.com/market"
    env["DATA_DIR"] = LIVE_PUBLIC_DATA_DIR_REL
    env["PYTHONIOENCODING"] = "utf-8"

    python = sys.executable
    steps = [
        (
            "collect_market",
            [python, "-m", "btcusdt_bot", "collect-market", "--max-messages", str(args.market_messages)],
            FUTURES_DIR,
        ),
        (
            "collect_book_ticker",
            [python, "-m", "btcusdt_bot", "collect-book-ticker", "--max-messages", str(args.book_messages)],
            FUTURES_DIR,
        ),
        (
            "collect_depth_book",
            [
                python,
                "-m",
                "btcusdt_bot",
                "collect-depth-book",
                "--max-messages",
                str(args.depth_messages),
                "--depth-levels",
                str(args.depth_levels),
                "--snapshot-limit",
                str(args.snapshot_limit),
            ],
            FUTURES_DIR,
        ),
        (
            "collect_crowding",
            [
                python,
                "-m",
                "btcusdt_bot",
                "collect-crowding",
                "--period",
                args.crowding_period,
                "--interval-seconds",
                "1",
                "--max-iterations",
                "1",
            ],
            FUTURES_DIR,
        ),
        (
            "flow_toxicity_report",
            [
                python,
                "tools/flow_toxicity_feature_report.py",
                "--data-dir",
                LIVE_PUBLIC_DATA_DIR_FROM_ROOT,
                "--out-prefix",
                args.flow_out_prefix,
            ],
            ROOT,
        ),
    ]

    results: list[dict[str, Any]] = []
    for name, command, cwd in steps:
        results.append(run_step(name, command, cwd=cwd, env=env, timeout_s=args.timeout_s))
    enforce_step_contracts(results, args)

    flow_json = (ROOT / args.flow_out_prefix).with_suffix(".json")
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "public_cache_smoke_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "passed": all(step["passed"] for step in results),
        "steps": results,
        "flow_report_json": str(flow_json),
        "can_trade": False,
    }
    json_path, md_path = write_outputs(report, ROOT / args.out_prefix)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "json": str(json_path),
                "md": str(md_path),
                "flow_report_json": str(flow_json),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
