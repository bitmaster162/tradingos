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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_step(name: str, command: list[str], timeout_s: int) -> dict[str, Any]:
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        return {
            "name": name,
            "command": command,
            "returncode": completed.returncode,
            "timed_out": False,
            "duration_s": round(time.time() - started, 3),
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "command": command,
            "returncode": None,
            "timed_out": True,
            "duration_s": round(time.time() - started, 3),
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
        }


def make_top_subset(extractor_json: Path, out_json: Path, top_cards: int) -> dict[str, Any]:
    payload = read_json(extractor_json)
    cards = payload.get("rule_cards") if isinstance(payload.get("rule_cards"), list) else []
    codable = [card for card in cards if card.get("codable_status") == "codable_now_existing_data"]
    subset = codable[:top_cards]
    subset_payload = dict(payload)
    subset_payload["rule_cards"] = subset
    subset_payload["subset"] = {
        "source": portable(extractor_json),
        "mode": "top_codable_now_existing_data",
        "top_cards": top_cards,
        "selected_cards": len(subset),
        "can_trade": False,
    }
    write_json(out_json, subset_payload)
    return {
        "source_rule_cards": len(cards),
        "codable_now_existing_data": len(codable),
        "selected_cards": len(subset),
        "subset_path": portable(out_json),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Parallel Edge Search Pass",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        "",
        "## Boundary",
        "",
        "- Research-only orchestration.",
        "- No private credentials, no Telegram send, no paper entries, no orders.",
        "- Passing batch candidates are not live signals; they require separate OOS/forward validation.",
        "",
        "## Results",
        "",
        f"- Discovery processed now: `{report['discovery'].get('processed_now')}`",
        f"- Extracted rule cards: `{report['extractor'].get('rule_cards')}`",
        f"- Codable now: `{report['subset'].get('codable_now_existing_data')}`",
        f"- Tested subset cards: `{report['subset'].get('selected_cards')}`",
        f"- Batch completed tests: `{report['batch'].get('completed_tests')}`",
        f"- Batch pass count: `{report['batch'].get('pass_count')}`",
        f"- Batch watchlist count: `{report['batch'].get('watchlist_count')}`",
        "",
        "## Steps",
        "",
        "| Step | Return | Timeout | Seconds |",
        "|---|---:|---:|---:|",
    ]
    for step in report.get("steps", []):
        lines.append(
            f"| `{step['name']}` | `{step.get('returncode')}` | `{step.get('timed_out')}` | `{step.get('duration_s')}` |"
        )
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded parallel edge-search orchestration: discovery -> extraction -> top-N RR batch")
    parser.add_argument("--tag", default="PARALLEL_EDGE_SEARCH_2026-07-01")
    parser.add_argument("--roots", default="workspace,downloads")
    parser.add_argument("--discovery-limit", type=int, default=10)
    parser.add_argument("--skip-discovery", action="store_true")
    parser.add_argument("--registry", default="docs/STRATEGY_DISCOVERY_REGISTRY_2026-06-08.json")
    parser.add_argument("--max-cards", type=int, default=100)
    parser.add_argument("--top-cards", type=int, default=12)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", type=float, default=3.0)
    parser.add_argument("--max-hold-bars", type=int, default=24)
    parser.add_argument("--batch-timeout-s", type=int, default=240)
    parser.add_argument("--out-prefix", default="docs/PARALLEL_EDGE_SEARCH_PASS_2026-07-01")
    args = parser.parse_args()

    tag = args.tag
    steps: list[dict[str, Any]] = []

    discovery_prefix = f"docs/STRATEGY_DISCOVERY_PIPELINE_{tag}"
    extractor_prefix = f"docs/TARGETED_STRATEGY_RULE_EXTRACTOR_{tag}"
    subset_path = resolve_path(f"docs/TARGETED_STRATEGY_RULE_EXTRACTOR_{tag}_TOP{args.top_cards}.json")
    rr_label = f"RR{args.stop_atr:g}X{args.take_atr:g}".replace(".", "P")
    batch_prefix = f"docs/DOCUMENT_RULE_CARD_BATCH_TEST_{tag}_TOP{args.top_cards}_{rr_label}"

    if not args.skip_discovery:
        steps.append(
            run_step(
                "discovery",
                [
                    sys.executable,
                    "tools/strategy_discovery_pipeline.py",
                    "--limit",
                    str(args.discovery_limit),
                    "--roots",
                    args.roots,
                    "--out-prefix",
                    discovery_prefix,
                    "--backlog",
                    f"docs/STRATEGY_DISCOVERY_BACKLOG_{tag}.json",
                ],
                timeout_s=max(60, args.discovery_limit * 10),
            )
        )

    steps.append(
        run_step(
            "extract_rules",
            [
                sys.executable,
                "tools/targeted_strategy_rule_extractor.py",
                "--registry",
                args.registry,
                "--out-prefix",
                extractor_prefix,
                "--min-source-score",
                "20",
                "--max-cards",
                str(args.max_cards),
            ],
            timeout_s=120,
        )
    )

    extractor_json = resolve_path(extractor_prefix).with_suffix(".json")
    subset = make_top_subset(extractor_json, subset_path, args.top_cards)
    steps.append(
        run_step(
            "batch_test_top_subset",
            [
                sys.executable,
                "tools/document_rule_card_batch_tester.py",
                "--rule-cards",
                portable(subset_path),
                "--cache-dir",
                "data/cache/binance_spot_perp_extended",
                "--workers",
                str(args.workers),
                "--stop-atr",
                str(args.stop_atr),
                "--take-atr",
                str(args.take_atr),
                "--max-hold-bars",
                str(args.max_hold_bars),
                "--fee-bps",
                "6",
                "--slippage-bps",
                "4",
                "--min-trades",
                "60",
                "--min-winrate-pct",
                "35",
                "--min-expectancy-r",
                "0.05",
                "--min-stable-folds",
                "3",
                "--max-drawdown-r",
                "20",
                "--out-prefix",
                batch_prefix,
            ],
            timeout_s=args.batch_timeout_s,
        )
    )

    discovery = read_json(resolve_path(discovery_prefix).with_suffix(".json"))
    extractor = read_json(extractor_json)
    batch = read_json(resolve_path(batch_prefix).with_suffix(".json"))
    pass_count = int(batch.get("pass_count") or 0)
    watchlist_count = int(batch.get("watchlist_count") or 0)
    timed_out = any(step.get("timed_out") for step in steps)
    failed = any((step.get("returncode") not in {0, None}) for step in steps)
    decision = "parallel_edge_search_no_promotable_candidate"
    next_action = "continue with a different independent edge class or increase top-cards after runtime remains acceptable"
    if timed_out:
        decision = "parallel_edge_search_batch_timeout"
        next_action = "reduce top-cards or optimize the specific slow signal generator before expanding search"
    elif failed:
        decision = "parallel_edge_search_step_failed"
        next_action = "inspect step stderr before trusting this pass"
    elif pass_count:
        decision = "parallel_edge_search_found_oos_candidates"
        next_action = "run sealed OOS/forward validation for pass candidates only; do not trade"
    elif watchlist_count:
        decision = "parallel_edge_search_watchlist_only"
        next_action = "diagnose watchlist candidates; do not retune on opened validation"

    report = {
        "generated_at": now_iso(),
        "tool": "tools/parallel_edge_search_pass.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {
            "research_only": True,
            "sends_orders": False,
            "opens_paper_entries": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "settings": vars(args),
        "steps": steps,
        "discovery": {
            "processed_now": discovery.get("processed_now"),
            "remaining_candidates": discovery.get("remaining_candidates"),
            "path": portable(resolve_path(discovery_prefix).with_suffix(".json")),
        },
        "extractor": {
            "rule_cards": extractor.get("rule_cards") or len(extractor.get("rule_cards", [])),
            "by_status": extractor.get("by_status"),
            "by_family": extractor.get("by_family"),
            "path": portable(extractor_json),
        },
        "subset": subset,
        "batch": {
            "decision": batch.get("decision"),
            "completed_tests": batch.get("completed_tests"),
            "pass_count": pass_count,
            "watchlist_count": watchlist_count,
            "path": portable(resolve_path(batch_prefix).with_suffix(".json")),
        },
        "next_action": next_action,
    }
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "processed_now": report["discovery"].get("processed_now"),
                "selected_cards": subset.get("selected_cards"),
                "completed_tests": report["batch"].get("completed_tests"),
                "pass_count": pass_count,
                "watchlist_count": watchlist_count,
                "out": portable(out_prefix.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not timed_out and not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
