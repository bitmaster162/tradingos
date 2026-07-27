#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CROWD_FADE_REFRESH_PACK_2026-06-19"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def run_step(name: str, command: list[str], timeout_s: int) -> dict[str, Any]:
    started = time.time()
    proc = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, timeout=timeout_s)
    return {
        "name": name,
        "command": command,
        "exit_code": proc.returncode,
        "duration_s": round(time.time() - started, 3),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Crowd-Fade Refresh Pack",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{report['can_trade']}`",
        "",
        "## Steps",
        "",
        "| Step | Exit | Duration |",
        "| --- | ---: | ---: |",
    ]
    for step in report["steps"]:
        lines.append(f"| `{step['name']}` | `{step['exit_code']}` | `{step['duration_s']}` |")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Public data refresh and observer-only signal check.",
            "- No API keys, no paper entry intent, no orders.",
            "- Run diagnostic only when requested or after enough new history accumulates.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Refresh public data and run the crowd-fade observer.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--data-pages", type=int, default=1)
    parser.add_argument("--crowd-pages", type=int, default=1)
    parser.add_argument("--with-diagnostic", action="store_true")
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()

    python = sys.executable
    steps = [
        run_step(
            "max_data_cache",
            [
                python,
                "tools/max_data_cache.py",
                "--symbol",
                args.symbol,
                "--intervals",
                args.intervals,
                "--markets",
                "futures,spot",
                "--pages",
                str(args.data_pages),
                "--limit",
                "500",
                "--cache-dir",
                args.cache_dir,
                "--out-prefix",
                "_dl/control_panel/MAX_DATA_CACHE",
            ],
            360,
        ),
        run_step(
            "binance_crowd_positioning_collector",
            [
                python,
                "tools/binance_crowd_positioning_collector.py",
                "--symbol",
                args.symbol,
                "--intervals",
                args.intervals,
                "--pages",
                str(args.crowd_pages),
                "--limit",
                "500",
                "--cache-dir",
                args.cache_dir,
                "--out-prefix",
                "docs/BINANCE_CROWD_POSITIONING_COLLECTOR_2026-06-19",
            ],
            120,
        ),
    ]
    if args.with_diagnostic:
        steps.append(
            run_step(
                "crowd_fade_positioning_diagnostic",
                [
                    python,
                    "tools/crowd_fade_positioning_diagnostic.py",
                    "--cache-dir",
                    args.cache_dir,
                    "--intervals",
                    args.intervals,
                    "--out-prefix",
                    "docs/CROWD_FADE_POSITIONING_DIAGNOSTIC_2026-06-19",
                ],
                240,
            )
        )
    steps.append(
        run_step(
            "crowd_fade_positioning_shadow_observer",
            [
                python,
                "tools/crowd_fade_positioning_shadow_observer.py",
                "--diagnostic",
                "docs/CROWD_FADE_POSITIONING_DIAGNOSTIC_2026-06-19.json",
                "--cache-dir",
                args.cache_dir,
                "--out-prefix",
                "docs/CROWD_FADE_POSITIONING_SHADOW_OBSERVER_2026-06-19",
            ],
            60,
        )
    )
    steps.append(
        run_step(
            "crowd_fade_positioning_shadow_scoreboard",
            [
                python,
                "tools/crowd_fade_positioning_shadow_scoreboard.py",
                "--journal-path",
                "logs/forward_paper_feed/crowd_fade_positioning_shadow_observer.jsonl",
                "--cache-dir",
                args.cache_dir,
                "--out-prefix",
                "docs/CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD_2026-06-19",
            ],
            60,
        )
    )
    steps.append(
        run_step(
            "crowd_fade_positioning_telegram_notify",
            [
                python,
                "tools/crowd_fade_positioning_telegram_notify.py",
                "--observer-json-path",
                "docs/CROWD_FADE_POSITIONING_SHADOW_OBSERVER_2026-06-19.json",
                "--scoreboard-json-path",
                "docs/CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD_2026-06-19.json",
                "--out-prefix",
                "docs/CROWD_FADE_POSITIONING_TELEGRAM_NOTIFY_2026-06-19",
                "--message-prefix",
                "CROWD-FADE WATCH - observer-only. No entry, no paper intent, no orders.",
            ],
            60,
        )
    )
    steps.append(
        run_step(
            "crowd_fade_positioning_promotion_gate",
            [
                python,
                "tools/crowd_fade_positioning_promotion_gate.py",
                "--out-prefix",
                "docs/CROWD_FADE_POSITIONING_PROMOTION_GATE_2026-06-19",
            ],
            60,
        )
    )
    steps.append(
        run_step(
            "four_family_forward_portfolio_scoreboard",
            [
                python,
                "tools/four_family_forward_portfolio_scoreboard.py",
                "--out-prefix",
                "docs/FOUR_FAMILY_FORWARD_PORTFOLIO_SCOREBOARD_2026-06-22",
            ],
            60,
        )
    )
    steps.append(
        run_step(
            "forward_evidence_lifecycle_controller",
            [
                python,
                "tools/forward_evidence_lifecycle_controller.py",
                "--scoreboard",
                "docs/FOUR_FAMILY_FORWARD_PORTFOLIO_SCOREBOARD_2026-06-22.json",
                "--policy",
                "configs/FORWARD_EVIDENCE_LIFECYCLE.json",
                "--out-prefix",
                "docs/FORWARD_EVIDENCE_LIFECYCLE_2026-06-23",
            ],
            60,
        )
    )

    failed = [step for step in steps if step["exit_code"] != 0]
    critical_failed = [step for step in failed if step["name"] != "crowd_fade_positioning_telegram_notify"]
    notify_failed = any(step["name"] == "crowd_fade_positioning_telegram_notify" for step in failed)
    if critical_failed:
        decision = "refresh_pack_failed"
    elif notify_failed:
        decision = "refresh_pack_completed_notification_warning"
    else:
        decision = "refresh_pack_completed_observer_only"
    report = {
        "generated_at": now_iso(),
        "engine": "CROWD_FADE_REFRESH_PACK",
        "engine_version": "1.0.0",
        "decision": decision,
        "steps": steps,
        "failed_steps": [step["name"] for step in failed],
        "critical_failed_steps": [step["name"] for step in critical_failed],
        "with_diagnostic": bool(args.with_diagnostic),
        "can_trade": False,
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "failed_steps": report["failed_steps"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 1 if critical_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
