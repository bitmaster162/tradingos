from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def default_candidates(out_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "id": "v10_15m_fade_short",
            "strategy": "v10_15m_fade_short",
            "interval": "15m",
            "tf": "15m",
            "pages": 2,
            "htf_interval": "4h",
            "stop_atr": 1.0,
            "take_atr": 1.2,
            "max_hold_bars": 16,
            "out_prefix": out_dir / "V10_15M_FADE_SHORT",
            "source_slice": "v0.9 15m: near_high + rsi14>=60 + relative_volume<=0.8",
        },
        {
            "id": "v10_1h_weak_bid_short",
            "strategy": "v10_1h_weak_bid_short",
            "interval": "1h",
            "tf": "1h",
            "pages": 4,
            "htf_interval": "4h",
            "stop_atr": 1.0,
            "take_atr": 1.5,
            "max_hold_bars": 16,
            "out_prefix": out_dir / "V10_1H_WEAK_BID_SHORT",
            "source_slice": "v0.9 1h: near_low + spot_volume_ratio<=0.8 + donchian_width_atr_between_2_8",
        },
        {
            "id": "v10_4h_range_long",
            "strategy": "v10_4h_range_long",
            "interval": "4h",
            "tf": "4h",
            "pages": 3,
            "htf_interval": "1d",
            "stop_atr": 1.0,
            "take_atr": 1.4,
            "max_hold_bars": 10,
            "out_prefix": out_dir / "V10_4H_RANGE_LONG",
            "source_slice": "v0.9 4h: range_ok + htf_bias=NEUTRAL + rsi14>=70",
        },
    ]


def run_candidate(candidate: dict[str, Any], *, limit: int, min_trades: int, use_cache: bool, cache_dir: str) -> dict[str, Any]:
    out_prefix = Path(candidate["out_prefix"])
    command = [
        sys.executable,
        "tools/max_backtest.py",
        "--fetch-binance",
        "--fetch-derivatives",
        *(["--use-cache", "--cache-dir", cache_dir] if use_cache else []),
        "--market",
        "futures",
        "--symbol",
        "BTCUSDT",
        "--interval",
        str(candidate["interval"]),
        "--tf",
        str(candidate["tf"]),
        "--limit",
        str(limit),
        "--pages",
        str(candidate["pages"]),
        "--strategy",
        str(candidate["strategy"]),
        "--htf-interval",
        str(candidate["htf_interval"]),
        "--stop-atr",
        str(candidate["stop_atr"]),
        "--take-atr",
        str(candidate["take_atr"]),
        "--max-hold-bars",
        str(candidate["max_hold_bars"]),
        "--allow-price-only",
        "--min-trades",
        str(min_trades),
        "--out-prefix",
        str(out_prefix),
    ]
    started = now_iso()
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    json_path = out_prefix.with_suffix(".json")
    payload = read_json(json_path) if json_path.exists() else {}
    return {
        "id": candidate["id"],
        "source_slice": candidate["source_slice"],
        "started_at": started,
        "finished_at": now_iso(),
        "exit_code": result.returncode,
        "command": " ".join(command),
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
        "json": str(json_path),
        "md": str(out_prefix.with_suffix(".md")),
        "summary": payload.get("summary"),
        "research_gate": payload.get("research_gate"),
        "params": payload.get("params"),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MAX Core Lite v1.0 Candidate Hardening",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Min trades gate: `{report['config']['min_trades']}`",
        "",
        "## Candidates",
        "",
        "| Candidate | Trades | Winrate | Expectancy | Verdict | Source slice |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for item in report["candidates"]:
        summary = item.get("summary") or {}
        gate = item.get("research_gate") or {}
        lines.append(
            f"| `{item['id']}` | {summary.get('trades')} | {summary.get('winrate_pct')} | "
            f"{summary.get('expectancy_r')} | `{gate.get('verdict')}` | {item['source_slice']} |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            report["decision"],
            "",
            "## Runtime Boundary",
            "",
            report["runtime_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX Core Lite v1.0 candidate hardening runner")
    parser.add_argument("--out-prefix", default="_dl/hardening/MAX_CORE_LITE_V10_HARDENING")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--use-cache", action="store_true", help="Use data/cache Binance files when available.")
    parser.add_argument("--cache-dir", default="data/cache/binance")
    args = parser.parse_args()

    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_dir = out_prefix.parent / f"{out_prefix.name}_runs"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = [
        run_candidate(candidate, limit=args.limit, min_trades=args.min_trades, use_cache=args.use_cache, cache_dir=args.cache_dir)
        for candidate in default_candidates(out_dir)
    ]
    passed = [item for item in results if (item.get("research_gate") or {}).get("pass") is True]
    decision = (
        "At least one candidate passed the research gate and can be reviewed for paper-trading design."
        if passed
        else "No v1.0 candidate passed the research gate. Keep all candidates blocked from paper/live trading."
    )
    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_CANDIDATE_HARDENING",
        "engine_version": "1.0.0",
        "config": {
            "limit": args.limit,
            "min_trades": args.min_trades,
            "use_cache": args.use_cache,
            "cache_dir": args.cache_dir,
        },
        "candidates": results,
        "passed": passed,
        "decision": decision,
        "files": {
            "json": str(out_prefix.with_suffix(".json")),
            "md": str(out_prefix.with_suffix(".md")),
        },
        "runtime_boundary": (
            "Research-only hardening runner. It uses public market data and deterministic backtests; "
            "it does not use private keys, does not place orders, and does not approve live trading."
        ),
    }
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": report["files"]["json"],
                "md": report["files"]["md"],
                "passed": len(passed),
                "decision": decision,
                "candidates": [
                    {
                        "id": item["id"],
                        "summary": item.get("summary"),
                        "research_gate": item.get("research_gate"),
                    }
                    for item in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
