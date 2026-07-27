#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.execution_realism_metrics import albers_obi_fill_probability, cdar, fleet_cdar  # noqa: E402


R_NET_FIELDS = ("r_net", "net_r", "r")
SIDE_FIELDS = ("side", "direction", "position_side")
OBI_FIELDS = ("obi", "order_book_imbalance", "book_imbalance", "imbalance")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return str(path)


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc)}
    return payload if isinstance(payload, dict) else {"_read_error": "json_root_not_object"}


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        value_f = float(value)
        return value_f if math.isfinite(value_f) else default
    text = str(value).strip()
    if not text:
        return default
    try:
        value_f = float(text)
    except ValueError:
        return default
    return value_f if math.isfinite(value_f) else default


def first_present(row: dict[str, Any], fields: tuple[str, ...]) -> Any | None:
    for field in fields:
        if field in row and str(row.get(field, "")).strip() != "":
            return row.get(field)
    return None


def normalize_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"long", "buy", "bid"} or "long" in text or "buy" in text:
        return "buy"
    if text in {"short", "sell", "ask"} or "short" in text or "sell" in text:
        return "sell"
    return "buy"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def resolve_ledger_paths(guard_matrix: Path | None, explicit_ledgers: list[str]) -> list[Path]:
    paths: list[Path] = []
    if explicit_ledgers:
        paths.extend(Path(item) for item in explicit_ledgers)
    elif guard_matrix and guard_matrix.exists():
        matrix = json.loads(guard_matrix.read_text(encoding="utf-8-sig"))
        for item in matrix.get("ledgers", []):
            ledger = item.get("ledger")
            if ledger:
                paths.append(Path(ledger))
    else:
        paths.extend(sorted((ROOT / "docs").glob("*trades.csv")))

    resolved: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        full = path if path.is_absolute() else ROOT / path
        key = str(full.resolve())
        if key in seen:
            continue
        seen.add(key)
        resolved.append(full)
    return resolved


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "winrate": 0.0,
            "total_r": 0.0,
            "expectancy_r": 0.0,
            "median_r": 0.0,
            "cdar_80": 0.0,
        }
    wins = sum(1 for value in values if value > 0)
    losses = sum(1 for value in values if value < 0)
    return {
        "count": len(values),
        "wins": wins,
        "losses": losses,
        "winrate": round(wins / len(values), 6),
        "total_r": round(sum(values), 6),
        "expectancy_r": round(statistics.mean(values), 6),
        "median_r": round(statistics.median(values), 6),
        "cdar_80": cdar(values, alpha=0.8),
    }


def row_obi(row: dict[str, Any]) -> tuple[float, str]:
    raw = first_present(row, OBI_FIELDS)
    if raw is None:
        return 0.0, "neutral_default"
    value = safe_float(raw)
    if value is None:
        return 0.0, "neutral_default_parse_failed"
    return clamp(value, -1.0, 1.0), "ledger_field"


def analyze_ledger(path: Path, missed_fill_penalty_r: float) -> dict[str, Any]:
    if not path.exists():
        return {"ledger": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path), "status": "missing"}
    try:
        rows = read_csv_rows(path)
    except Exception as exc:  # noqa: BLE001
        return {
            "ledger": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
            "status": "read_error",
            "error": str(exc),
        }

    original: list[float] = []
    shadow: list[float] = []
    fill_probabilities: list[float] = []
    obi_sources: dict[str, int] = {}
    skipped = 0
    side_counts: dict[str, int] = {}

    for row in rows:
        r_value = safe_float(first_present(row, R_NET_FIELDS))
        if r_value is None:
            skipped += 1
            continue
        side = normalize_side(first_present(row, SIDE_FIELDS))
        obi, obi_source = row_obi(row)
        try:
            fill_probability = albers_obi_fill_probability(side=side, obi=obi)
        except ValueError:
            fill_probability = albers_obi_fill_probability(side="buy", obi=0.0)
        shadow_r = (fill_probability * r_value) - ((1.0 - fill_probability) * missed_fill_penalty_r)

        original.append(r_value)
        shadow.append(round(shadow_r, 6))
        fill_probabilities.append(fill_probability)
        obi_sources[obi_source] = obi_sources.get(obi_source, 0) + 1
        side_counts[side] = side_counts.get(side, 0) + 1

    original_summary = summarize(original)
    shadow_summary = summarize(shadow)
    rel = str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
    return {
        "ledger": rel,
        "ledger_sha256": sha256_file(path),
        "status": "analyzed" if original else "no_r_net_rows",
        "rows": len(rows),
        "used_rows": len(original),
        "skipped_rows": skipped,
        "side_counts": side_counts,
        "fill_model": {
            "name": "albers_obi_fill_probability",
            "default_obi": 0.0,
            "missed_fill_penalty_r": missed_fill_penalty_r,
            "obi_sources": obi_sources,
            "avg_fill_probability": round(statistics.mean(fill_probabilities), 6) if fill_probabilities else 0.0,
            "min_fill_probability": round(min(fill_probabilities), 6) if fill_probabilities else 0.0,
            "max_fill_probability": round(max(fill_probabilities), 6) if fill_probabilities else 0.0,
        },
        "queue_penetration": {
            "applicable": False,
            "reason": "trade ledgers do not contain per-bar high/low at resting limit level; OBI fill-probability overlay used instead",
        },
        "original": original_summary,
        "shadow": shadow_summary,
        "delta": {
            "total_r": round(shadow_summary["total_r"] - original_summary["total_r"], 6),
            "expectancy_r": round(shadow_summary["expectancy_r"] - original_summary["expectancy_r"], 6),
            "winrate": round(shadow_summary["winrate"] - original_summary["winrate"], 6),
            "cdar_80": round(shadow_summary["cdar_80"] - original_summary["cdar_80"], 6),
        },
    }


def build_candidate_binding(
    candidate_report: Path | None,
    candidate_family: str | None,
    ledger_paths: list[Path],
) -> dict[str, Any]:
    if candidate_report is None and not candidate_family:
        return {
            "present": False,
            "status": "not_requested",
            "candidate_family": None,
            "candidate_report": None,
            "candidate_report_sha256": None,
            "ledgers": [],
            "checks": {},
        }
    if candidate_report is None or not candidate_family:
        return {
            "present": False,
            "status": "incomplete_binding_request",
            "candidate_family": candidate_family,
            "candidate_report": portable(candidate_report) if candidate_report else None,
            "candidate_report_sha256": sha256_file(candidate_report) if candidate_report else None,
            "ledgers": [],
            "checks": {
                "candidate_report_provided": candidate_report is not None,
                "candidate_family_provided": bool(candidate_family),
            },
        }

    candidate_path = candidate_report if candidate_report.is_absolute() else ROOT / candidate_report
    payload = read_json(candidate_path)
    ledgers = [
        {
            "path": portable(path),
            "sha256": sha256_file(path),
            "exists": path.is_file(),
        }
        for path in ledger_paths
    ]
    checks = {
        "candidate_report_readable": candidate_path.is_file() and not payload.get("_read_error"),
        "candidate_family_present": bool(candidate_family),
        "candidate_report_hash_present": sha256_file(candidate_path) is not None,
        "candidate_report_can_trade_false": payload.get("can_trade") is False,
        "candidate_ledgers_present": bool(ledgers),
        "candidate_ledgers_hashed": bool(ledgers)
        and all(item["exists"] and isinstance(item["sha256"], str) for item in ledgers),
    }
    return {
        "present": all(checks.values()),
        "status": "candidate_binding_created" if all(checks.values()) else "candidate_binding_invalid",
        "candidate_family": candidate_family,
        "candidate_report": portable(candidate_path),
        "candidate_report_sha256": sha256_file(candidate_path),
        "candidate_report_decision": payload.get("decision"),
        "candidate_report_can_trade": payload.get("can_trade"),
        "ledgers": ledgers,
        "checks": checks,
    }


def build_report(
    guard_matrix: Path | None,
    ledgers: list[str],
    missed_fill_penalty_r: float,
    candidate_report: Path | None = None,
    candidate_family: str | None = None,
) -> dict[str, Any]:
    ledger_paths = resolve_ledger_paths(guard_matrix, ledgers)
    analyses = [analyze_ledger(path, missed_fill_penalty_r) for path in ledger_paths]
    analyzed = [item for item in analyses if item.get("status") == "analyzed"]
    fleet_original = []
    fleet_shadow = []
    for item in analyzed:
        # CDaR path values are unavailable after summarization; use total/expectancy fields as a coarse
        # cross-ledger fleet view and keep per-ledger CDaR as the primary risk metric.
        fleet_original.append([float(item["original"]["expectancy_r"])])
        fleet_shadow.append([float(item["shadow"]["expectancy_r"])])

    report = {
        "generated_at": now_iso(),
        "tool": "execution_realism_shadow_overlay",
        "decision": "execution_realism_shadow_overlay_completed",
        "guard_matrix": str(guard_matrix) if guard_matrix else None,
        "ledgers_requested": len(ledger_paths),
        "ledgers_analyzed": len(analyzed),
        "ledgers_skipped": len(analyses) - len(analyzed),
        "missed_fill_penalty_r": missed_fill_penalty_r,
        "candidate_binding": build_candidate_binding(candidate_report, candidate_family, ledger_paths),
        "summary": {
            "original_total_r": round(sum(float(item["original"]["total_r"]) for item in analyzed), 6),
            "shadow_total_r": round(sum(float(item["shadow"]["total_r"]) for item in analyzed), 6),
            "original_weighted_expectancy_r": round(
                sum(float(item["original"]["total_r"]) for item in analyzed)
                / max(1, sum(int(item["original"]["count"]) for item in analyzed)),
                6,
            ),
            "shadow_weighted_expectancy_r": round(
                sum(float(item["shadow"]["total_r"]) for item in analyzed)
                / max(1, sum(int(item["shadow"]["count"]) for item in analyzed)),
                6,
            ),
            "fleet_original_cdar_80": fleet_cdar(fleet_original, alpha=0.8),
            "fleet_shadow_cdar_80": fleet_cdar(fleet_shadow, alpha=0.8),
        },
        "analyses": analyses,
        "runtime_boundary": {
            "shadow_only": True,
            "does_not_change_strategy_decisions": True,
            "does_not_change_candidate_parameters": True,
            "alerts_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
        },
        "can_trade": False,
    }
    report["summary"]["delta_total_r"] = round(
        report["summary"]["shadow_total_r"] - report["summary"]["original_total_r"], 6
    )
    report["summary"]["delta_weighted_expectancy_r"] = round(
        report["summary"]["shadow_weighted_expectancy_r"] - report["summary"]["original_weighted_expectancy_r"], 6
    )
    return report


def write_outputs(report: dict[str, Any], out_prefix: str) -> None:
    prefix = Path(out_prefix)
    if not prefix.is_absolute():
        prefix = ROOT / prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Execution Realism Shadow Overlay",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        f"Can trade: `{report.get('can_trade')}`",
        "",
        "## Boundary",
        "",
        "- Shadow-only execution realism layer.",
        "- Does not change strategy decisions.",
        "- No alerts, signals, paper entries or orders.",
        "",
        "## Fleet Summary",
        "",
        f"- Ledgers analyzed: `{report.get('ledgers_analyzed')}` / `{report.get('ledgers_requested')}`.",
        f"- Original total R: `{report.get('summary', {}).get('original_total_r')}`.",
        f"- Shadow total R: `{report.get('summary', {}).get('shadow_total_r')}`.",
        f"- Delta total R: `{report.get('summary', {}).get('delta_total_r')}`.",
        f"- Original weighted expectancy R: `{report.get('summary', {}).get('original_weighted_expectancy_r')}`.",
        f"- Shadow weighted expectancy R: `{report.get('summary', {}).get('shadow_weighted_expectancy_r')}`.",
        "",
        "## Ledgers",
        "",
        "| Ledger | Rows | Original Exp R | Shadow Exp R | Delta Exp R | Avg Fill P | Status |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in report.get("analyses", []):
        if item.get("status") != "analyzed":
            lines.append(f"| `{item.get('ledger')}` | 0 | 0 | 0 | 0 | 0 | `{item.get('status')}` |")
            continue
        lines.append(
            "| `{ledger}` | {rows} | {orig} | {shadow} | {delta} | {fill} | `{status}` |".format(
                ledger=item.get("ledger"),
                rows=item.get("used_rows"),
                orig=item.get("original", {}).get("expectancy_r"),
                shadow=item.get("shadow", {}).get("expectancy_r"),
                delta=item.get("delta", {}).get("expectancy_r"),
                fill=item.get("fill_model", {}).get("avg_fill_probability"),
                status=item.get("status"),
            )
        )
    lines.append("")
    prefix.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a shadow execution-realism overlay to historical trade ledgers")
    parser.add_argument(
        "--guard-matrix",
        default="docs/TRADE_LEDGER_GUARD_MATRIX_2026-06-30_NO_LEAKAGE.json",
        help="Guard matrix with ledger paths; ignored when --ledger is provided.",
    )
    parser.add_argument("--ledger", action="append", default=[], help="Explicit trade ledger CSV path.")
    parser.add_argument("--missed-fill-penalty-r", type=float, default=0.0)
    parser.add_argument("--candidate-report", help="Exact candidate report to bind by SHA-256.")
    parser.add_argument("--candidate-family", help="Frontier family expected to own the candidate report.")
    parser.add_argument("--out-prefix", default="docs/EXECUTION_REALISM_SHADOW_OVERLAY_2026-07-11")
    args = parser.parse_args()

    guard = Path(args.guard_matrix)
    if not guard.is_absolute():
        guard = ROOT / guard
    candidate_report = Path(args.candidate_report) if args.candidate_report else None
    if candidate_report is not None and not candidate_report.is_absolute():
        candidate_report = ROOT / candidate_report
    report = build_report(
        guard,
        args.ledger,
        args.missed_fill_penalty_r,
        candidate_report=candidate_report,
        candidate_family=args.candidate_family,
    )
    write_outputs(report, args.out_prefix)
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "ledgers_analyzed": report["ledgers_analyzed"],
                "original_weighted_expectancy_r": report["summary"]["original_weighted_expectancy_r"],
                "shadow_weighted_expectancy_r": report["summary"]["shadow_weighted_expectancy_r"],
                "delta_weighted_expectancy_r": report["summary"]["delta_weighted_expectancy_r"],
                "can_trade": report["can_trade"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
