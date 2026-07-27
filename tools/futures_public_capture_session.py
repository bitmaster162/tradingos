#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FUTURES_DIR = ROOT / "ops" / "btcusdt_binance_futures_bot"
FUTURES_SRC = FUTURES_DIR / "src"
DEFAULT_DATA_DIR_REL = "data/public_live_capture"
DEFAULT_DATA_DIR_FROM_ROOT = f"ops/btcusdt_binance_futures_bot/{DEFAULT_DATA_DIR_REL}"


@dataclass(slots=True)
class RunningCollector:
    name: str
    command: list[str]
    process: subprocess.Popen[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_tail(value: str, limit: int = 4000) -> str:
    return value[-limit:] if len(value) > limit else value


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle)


def latest_day(data_dir: Path) -> str | None:
    candidates: set[str] = set()
    for namespace in ("market", "public", "crowding"):
        root = data_dir / namespace
        if not root.exists():
            continue
        candidates.update(path.name for path in root.iterdir() if path.is_dir())
    return sorted(candidates)[-1] if candidates else None


def line_count_summary(data_dir: Path, symbol: str, day: str, depth_levels: int, crowding_period: str) -> dict[str, Any]:
    symbol_l = symbol.lower()
    files = {
        "mark_price_1s": data_dir / "market" / day / f"{symbol_l}_markPrice_1s.jsonl",
        "agg_trade": data_dir / "market" / day / f"{symbol_l}_aggTrade.jsonl",
        "book_ticker": data_dir / "public" / day / f"{symbol_l}_bookTicker.jsonl",
        "raw_depth_100ms": data_dir / "public" / day / f"{symbol_l}_depth_100ms.jsonl",
        "local_depth": data_dir / "public" / day / f"{symbol_l}_localDepth{depth_levels}.jsonl",
        "crowding": data_dir / "crowding" / day / f"{symbol_l}_{crowding_period}.jsonl",
    }
    return {
        key: {
            "path": str(path),
            "exists": path.exists(),
            "lines": count_lines(path),
        }
        for key, path in files.items()
    }


def build_env(data_dir_rel: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(FUTURES_SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    env["BOT_ENV"] = "demo"
    env["BOT_REST_BASE_URL"] = "https://fapi.binance.com"
    env["BOT_WS_PUBLIC_BASE_URL"] = "wss://fstream.binance.com/public"
    env["BOT_WS_MARKET_BASE_URL"] = "wss://fstream.binance.com/market"
    env["DATA_DIR"] = data_dir_rel
    return env


def start_collectors(args: argparse.Namespace, env: dict[str, str]) -> list[RunningCollector]:
    python = sys.executable
    crowding_iterations = max(1, math.ceil(args.duration_seconds / max(1, args.crowding_interval_seconds)) + 1)
    specs = [
        (
            "market",
            [python, "-m", "btcusdt_bot", "collect-market"],
        ),
        (
            "book_ticker",
            [python, "-m", "btcusdt_bot", "collect-book-ticker"],
        ),
        (
            "depth_book",
            [
                python,
                "-m",
                "btcusdt_bot",
                "collect-depth-book",
                "--depth-levels",
                str(args.depth_levels),
                "--snapshot-limit",
                str(args.snapshot_limit),
            ],
        ),
        (
            "crowding",
            [
                python,
                "-m",
                "btcusdt_bot",
                "collect-crowding",
                "--period",
                args.crowding_period,
                "--interval-seconds",
                str(args.crowding_interval_seconds),
                "--max-iterations",
                str(crowding_iterations),
            ],
        ),
    ]
    running: list[RunningCollector] = []
    for name, command in specs:
        process = subprocess.Popen(
            command,
            cwd=str(FUTURES_DIR),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        running.append(RunningCollector(name=name, command=command, process=process))
    return running


def stop_collectors(running: list[RunningCollector], stop_timeout_s: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in running:
        if item.process.poll() is None:
            item.process.terminate()
    for item in running:
        try:
            stdout, stderr = item.process.communicate(timeout=stop_timeout_s)
        except subprocess.TimeoutExpired:
            item.process.kill()
            stdout, stderr = item.process.communicate()
        results.append(
            {
                "name": item.name,
                "command": item.command,
                "returncode": item.process.returncode,
                "stdout_tail": safe_tail(stdout or ""),
                "stderr_tail": safe_tail(stderr or ""),
            }
        )
    return results


def run_subprocess(name: str, command: list[str], *, cwd: Path, env: dict[str, str], timeout_s: int) -> dict[str, Any]:
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
    parsed = None
    try:
        parsed = json.loads(proc.stdout.strip()) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        parsed = None
    return {
        "name": name,
        "command": command,
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "parsed_stdout": parsed,
        "stdout_tail": safe_tail(proc.stdout),
        "stderr_tail": safe_tail(proc.stderr),
    }


def evaluate_pass(summary: dict[str, Any], args: argparse.Namespace) -> tuple[bool, list[str]]:
    failures: list[str] = []
    minimums = {
        "mark_price_1s": args.min_mark_lines,
        "agg_trade": args.min_agg_trade_lines,
        "book_ticker": args.min_book_ticker_lines,
        "local_depth": args.min_local_depth_lines,
        "crowding": args.min_crowding_lines,
    }
    for label, minimum in minimums.items():
        actual = int(summary.get(label, {}).get("lines", 0) or 0)
        if actual < minimum:
            failures.append(f"{label}_lines_below_minimum:{actual}<{minimum}")
    return not failures, failures


def write_report(report: dict[str, Any], out_prefix: Path) -> tuple[Path, Path]:
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Futures Public Capture Session",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Public Binance futures data only.",
        "- No private credentials.",
        "- No order submission.",
        "- Capture proof is not a trading signal.",
        "",
        "## Result",
        "",
        f"- Passed: `{report['passed']}`.",
        f"- Duration seconds: `{report['duration_seconds']}`.",
        f"- Data dir: `{report['data_dir']}`.",
        f"- Day: `{report.get('day')}`.",
        f"- Failures: `{report['failures']}`.",
        "",
        "## Line Counts",
        "",
    ]
    for label, item in report.get("line_counts", {}).items():
        lines.append(f"- `{label}`: `{item['lines']}` lines.")
    lines.extend(["", "## Follow-up Reports", ""])
    for item in report.get("post_reports", []):
        lines.append(f"- `{item['name']}` passed=`{item['passed']}`.")
    lines.extend(
        [
            "",
            "## Use Policy",
            "",
            "- Use this to build enough data for research and walk-forward tests.",
            "- Do not use micro-sample flow-toxicity as standalone entry permission.",
            "- Promotion requires sample size, OOS, paper proof and pre-trade guardian.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Bounded public Binance futures capture session")
    parser.add_argument("--duration-seconds", type=int, default=300)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR_REL)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--depth-levels", type=int, default=20)
    parser.add_argument("--snapshot-limit", type=int, default=1000)
    parser.add_argument("--crowding-period", default="5m")
    parser.add_argument("--crowding-interval-seconds", type=int, default=30)
    parser.add_argument("--min-mark-lines", type=int, default=60)
    parser.add_argument("--min-agg-trade-lines", type=int, default=60)
    parser.add_argument("--min-book-ticker-lines", type=int, default=60)
    parser.add_argument("--min-local-depth-lines", type=int, default=60)
    parser.add_argument("--min-crowding-lines", type=int, default=1)
    parser.add_argument("--out-prefix", default="docs/FUTURES_PUBLIC_CAPTURE_SESSION_2026-06-08")
    parser.add_argument("--flow-out-prefix", default="docs/FLOW_TOXICITY_FEATURE_REPORT_CAPTURE_2026-06-08")
    args = parser.parse_args()

    data_dir_rel = args.data_dir.replace("\\", "/").strip("/")
    data_dir = FUTURES_DIR / data_dir_rel
    data_dir.mkdir(parents=True, exist_ok=True)
    env = build_env(data_dir_rel)

    started = time.monotonic()
    running = start_collectors(args, env)
    time.sleep(max(1, args.duration_seconds))
    collector_results = stop_collectors(running, stop_timeout_s=10)
    duration_s = round(time.monotonic() - started, 3)

    day = latest_day(data_dir)
    counts = line_count_summary(data_dir, args.symbol, day, args.depth_levels, args.crowding_period) if day else {}
    passed_counts, failures = evaluate_pass(counts, args) if day else (False, ["no_capture_day_found"])

    post_reports: list[dict[str, Any]] = []
    if day:
        flow_data_dir_from_root = f"ops/btcusdt_binance_futures_bot/{data_dir_rel}"
        post_reports.append(
            run_subprocess(
                "flow_toxicity_report",
                [
                    sys.executable,
                    "tools/flow_toxicity_feature_report.py",
                    "--data-dir",
                    flow_data_dir_from_root,
                    "--date",
                    day,
                    "--out-prefix",
                    args.flow_out_prefix,
                ],
                cwd=ROOT,
                env=env,
                timeout_s=60,
            )
        )

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "public_capture_session_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "passed": passed_counts and all(item["passed"] for item in post_reports),
        "duration_seconds": duration_s,
        "data_dir": str(data_dir),
        "day": day,
        "line_counts": counts,
        "failures": failures,
        "collector_results": collector_results,
        "post_reports": post_reports,
        "can_trade": False,
    }
    json_path, md_path = write_report(report, ROOT / args.out_prefix)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "json": str(json_path),
                "md": str(md_path),
                "day": day,
                "failures": failures,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
