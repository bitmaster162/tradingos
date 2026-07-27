#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
from dataclasses import dataclass
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


def safe_float(value: Any, default: float = math.nan) -> float:
    try:
        if value in {None, ""}:
            return default
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if not math.isnan(out) else default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def has_required_schema(rows: list[dict[str, str]]) -> bool:
    if not rows:
        return False
    keys = set(rows[0])
    return "r_net" in keys and ("entry_ts" in keys or "signal_ts" in keys)


def sort_key(row: dict[str, str]) -> str:
    return str(row.get("entry_ts") or row.get("signal_ts") or row.get("exit_ts") or "")


def summarize(rows: list[dict[str, str]]) -> dict[str, Any]:
    values = [safe_float(row.get("r_net")) for row in rows]
    values = [value for value in values if not math.isnan(value)]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    losing_streak = 0
    max_losing = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        if value <= 0:
            losing_streak += 1
            max_losing = max(max_losing, losing_streak)
        else:
            losing_streak = 0
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(values) * 100.0, 3) if values else None,
        "expectancy_r": round(sum(values) / len(values), 6) if values else None,
        "net_r_total": round(sum(values), 6) if values else 0.0,
        "avg_win_r": round(sum(wins) / len(wins), 6) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 6) if losses else None,
        "max_drawdown_r": round(max_dd, 6),
        "max_losing_streak": max_losing,
    }


def chronological_split(rows: list[dict[str, str]], oos_fraction: float) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    ordered = sorted(rows, key=sort_key)
    if len(ordered) < 2:
        return ordered, []
    oos_count = max(1, round(len(ordered) * oos_fraction))
    if oos_count >= len(ordered):
        oos_count = max(1, len(ordered) // 3)
    split_at = len(ordered) - oos_count
    return ordered[:split_at], ordered[split_at:]


@dataclass(frozen=True)
class Predicate:
    field: str
    op: str
    value: str | float
    label: str

    def match(self, row: dict[str, str]) -> bool:
        raw = row.get(self.field)
        if self.op == "eq":
            return str(raw) == str(self.value)
        numeric = safe_float(raw)
        if math.isnan(numeric):
            return False
        threshold = float(self.value)
        if self.op == ">=":
            return numeric >= threshold
        if self.op == "<=":
            return numeric <= threshold
        if self.op == ">":
            return numeric > threshold
        if self.op == "<":
            return numeric < threshold
        if self.op == "abs>=":
            return abs(numeric) >= threshold
        if self.op == "abs<=":
            return abs(numeric) <= threshold
        return False


def predicate_text(predicates: tuple[Predicate, ...]) -> str:
    return " & ".join(item.label for item in predicates)


def filter_rows(rows: list[dict[str, str]], predicates: tuple[Predicate, ...]) -> list[dict[str, str]]:
    return [row for row in rows if all(predicate.match(row) for predicate in predicates)]


def categorical_predicates(rows: list[dict[str, str]], max_uniques: int) -> list[Predicate]:
    fields = (
        "side",
        "oi_regime",
        "volume_regime",
        "spot_perp_regime",
        "atr_regime",
        "funding_regime",
    )
    out: list[Predicate] = []
    if not rows:
        return out
    available = set(rows[0])
    for field in fields:
        if field not in available:
            continue
        values = sorted({str(row.get(field) or "") for row in rows if str(row.get(field) or "")})
        if 1 < len(values) <= max_uniques:
            out.extend(Predicate(field, "eq", value, f"{field}={value}") for value in values)
    return out


def fixed_numeric_predicates(rows: list[dict[str, str]]) -> list[Predicate]:
    available = set(rows[0]) if rows else set()
    specs: dict[str, list[tuple[str, float, str]]] = {
        "volume_z": [
            (">=", 0.5, "volume_z>=0.5"),
            (">=", 1.0, "volume_z>=1.0"),
            ("<=", -0.5, "volume_z<=-0.5"),
        ],
        "oi_delta_pct": [
            (">=", 0.1, "oi_delta_pct>=0.1"),
            (">=", 1.0, "oi_delta_pct>=1.0"),
            ("<=", -0.1, "oi_delta_pct<=-0.1"),
        ],
        "funding": [
            ("abs<=", 0.0002, "abs(funding)<=0.0002"),
            (">=", 0.0002, "funding>=0.0002"),
            ("<=", -0.0002, "funding<=-0.0002"),
        ],
        "spot_perp_divergence_pct": [
            (">=", 0.05, "spot_perp_divergence_pct>=0.05"),
            ("<=", -0.05, "spot_perp_divergence_pct<=-0.05"),
            ("abs<=", 0.05, "abs(spot_perp_divergence_pct)<=0.05"),
        ],
        "atr_ratio": [
            (">=", 1.2, "atr_ratio>=1.2"),
            ("<=", 0.8, "atr_ratio<=0.8"),
        ],
        "body_pct": [
            (">=", 0.3, "body_pct>=0.3"),
            (">=", 0.6, "body_pct>=0.6"),
        ],
        "close_location": [
            (">=", 0.6, "close_location>=0.6"),
            ("<=", 0.4, "close_location<=0.4"),
        ],
    }
    out: list[Predicate] = []
    for field, predicates in specs.items():
        if field not in available:
            continue
        for op, value, label in predicates:
            out.append(Predicate(field, op, value, label))
    return out


def predicate_pool(rows: list[dict[str, str]], max_uniques: int) -> list[Predicate]:
    seen: set[str] = set()
    out: list[Predicate] = []
    for predicate in [*categorical_predicates(rows, max_uniques), *fixed_numeric_predicates(rows)]:
        if predicate.label in seen:
            continue
        seen.add(predicate.label)
        out.append(predicate)
    return out


def is_contradictory(predicates: tuple[Predicate, ...]) -> bool:
    eq_by_field: dict[str, str] = {}
    labels = {predicate.label for predicate in predicates}
    for predicate in predicates:
        if predicate.op == "eq":
            previous = eq_by_field.get(predicate.field)
            if previous is not None and previous != str(predicate.value):
                return True
            eq_by_field[predicate.field] = str(predicate.value)
    contradictory_pairs = (
        ("volume_z>=0.5", "volume_z<=-0.5"),
        ("volume_z>=1.0", "volume_z<=-0.5"),
        ("oi_delta_pct>=0.1", "oi_delta_pct<=-0.1"),
        ("oi_delta_pct>=1.0", "oi_delta_pct<=-0.1"),
        ("funding>=0.0002", "funding<=-0.0002"),
        ("atr_ratio>=1.2", "atr_ratio<=0.8"),
        ("body_pct>=0.3", "body_pct>=0.6"),
        ("close_location>=0.6", "close_location<=0.4"),
        ("spot_perp_divergence_pct>=0.05", "spot_perp_divergence_pct<=-0.05"),
    )
    return any(left in labels and right in labels for left, right in contradictory_pairs)


def gate_result(
    baseline: dict[str, Any],
    guarded: dict[str, Any],
    *,
    min_trades: int,
    min_expectancy: float,
    min_retention: float,
    baseline_count: int,
) -> dict[str, Any]:
    trades = safe_int(guarded.get("trades"))
    expectancy = safe_float(guarded.get("expectancy_r"), -999.0)
    baseline_exp = safe_float(baseline.get("expectancy_r"), -999.0)
    drawdown = safe_float(guarded.get("max_drawdown_r"), -999.0)
    baseline_drawdown = safe_float(baseline.get("max_drawdown_r"), -999.0)
    retention = trades / baseline_count if baseline_count else 0.0
    checks = {
        "min_trades": trades >= min_trades,
        "min_expectancy_r": expectancy >= min_expectancy,
        "beats_baseline_expectancy": expectancy > baseline_exp,
        "drawdown_not_worse": drawdown >= baseline_drawdown,
        "min_retention": retention >= min_retention,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "retention": round(retention, 6),
    }


def evaluate_ledger(path: Path, rows: list[dict[str, str]], args: argparse.Namespace) -> dict[str, Any]:
    train, oos = chronological_split(rows, args.oos_fraction)
    baseline = {
        "full": summarize(rows),
        "train": summarize(train),
        "oos": summarize(oos),
    }
    pool = predicate_pool(train, args.max_uniques)
    candidates: list[dict[str, Any]] = []
    for size in range(1, min(args.max_guard_size, len(pool)) + 1):
        for predicates in itertools.combinations(pool, size):
            if is_contradictory(predicates):
                continue
            train_rows = filter_rows(train, predicates)
            train_summary = summarize(train_rows)
            train_gate = gate_result(
                baseline["train"],
                train_summary,
                min_trades=args.min_train_trades,
                min_expectancy=args.min_train_expectancy_r,
                min_retention=args.min_train_retention,
                baseline_count=len(train),
            )
            if not train_gate["passed"]:
                continue
            oos_rows = filter_rows(oos, predicates)
            full_rows = filter_rows(rows, predicates)
            oos_summary = summarize(oos_rows)
            full_summary = summarize(full_rows)
            oos_gate = gate_result(
                baseline["oos"],
                oos_summary,
                min_trades=args.min_oos_trades,
                min_expectancy=args.min_oos_expectancy_r,
                min_retention=args.min_oos_retention,
                baseline_count=len(oos),
            )
            verdict = "guard_candidate_needs_forward_observer" if oos_gate["passed"] else "train_only_rejected_oos"
            candidates.append(
                {
                    "guard": predicate_text(predicates),
                    "predicate_count": len(predicates),
                    "baseline": baseline,
                    "full": full_summary,
                    "train": train_summary,
                    "oos": oos_summary,
                    "train_gate": train_gate,
                    "oos_gate": oos_gate,
                    "verdict": verdict,
                }
            )
    candidates.sort(
        key=lambda item: (
            1 if item["verdict"] == "guard_candidate_needs_forward_observer" else 0,
            safe_float(item["oos"].get("expectancy_r"), -999.0) - safe_float(baseline["oos"].get("expectancy_r"), -999.0),
            safe_int(item["oos"].get("trades")),
            safe_float(item["train"].get("expectancy_r"), -999.0),
        ),
        reverse=True,
    )
    passing = [item for item in candidates if item["verdict"] == "guard_candidate_needs_forward_observer"]
    return {
        "ledger": portable(path),
        "source_trades": len(rows),
        "train_trades": len(train),
        "oos_trades": len(oos),
        "predicate_pool_size": len(pool),
        "baseline": baseline,
        "tested_train_pass_candidates": len(candidates),
        "passing_oos_candidates": len(passing),
        "top_candidates": candidates[: args.keep_top],
    }


def discover_ledgers(docs_dir: Path, max_files: int) -> list[Path]:
    paths = []
    for path in docs_dir.glob("*_trades.csv"):
        name = path.name.upper()
        if "TOP_TRADES" in name:
            continue
        paths.append(path)
    paths.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return paths[:max_files]


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "ledger",
        "rank",
        "verdict",
        "guard",
        "full_trades",
        "full_expectancy_r",
        "full_winrate_pct",
        "full_max_drawdown_r",
        "train_trades",
        "train_expectancy_r",
        "train_winrate_pct",
        "oos_trades",
        "oos_expectancy_r",
        "oos_winrate_pct",
        "oos_max_drawdown_r",
        "baseline_oos_trades",
        "baseline_oos_expectancy_r",
        "baseline_oos_winrate_pct",
        "oos_retention",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Trade Ledger Guard Matrix",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-only guard optimizer.",
        "- Reads historical trade ledgers only.",
        "- No network, no private credentials, no orders, no live/paper permission.",
        "- Filters are selected on train and checked on OOS; passing means forward-observer candidate only.",
        "",
        "## Summary",
        "",
        f"- Ledgers scanned: `{report['summary']['ledgers_scanned']}`",
        f"- Usable ledgers: `{report['summary']['usable_ledgers']}`",
        f"- OOS guard candidates: `{report['summary']['oos_guard_candidates']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        "",
        "## Top Candidates",
        "",
        "| Rank | Ledger | Guard | OOS Trades | OOS Exp R | OOS Winrate | OOS Retention | Baseline OOS Exp | Verdict |",
        "|---:|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for index, item in enumerate(report["top_candidates"][:30], start=1):
        oos = item["oos"]
        baseline_oos = item["baseline"]["oos"]
        lines.append(
            "| `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` |".format(
                index,
                item["ledger"],
                item["guard"],
                oos.get("trades"),
                oos.get("expectancy_r"),
                oos.get("winrate_pct"),
                item["oos_gate"].get("retention"),
                baseline_oos.get("expectancy_r"),
                item["verdict"],
            )
        )
    lines.extend(["", "## Ledger Decisions", ""])
    for ledger in report["ledgers"]:
        base_oos = ledger["baseline"]["oos"]
        lines.append(
            "- `{}`: source_trades=`{}`, baseline_oos_exp=`{}`, passing_oos_candidates=`{}`".format(
                ledger["ledger"],
                ledger["source_trades"],
                base_oos.get("expectancy_r"),
                ledger["passing_oos_candidates"],
            )
        )
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train/OOS guard matrix for existing trade ledgers")
    parser.add_argument("--docs-dir", default="docs")
    parser.add_argument("--trades-csv", action="append", default=[])
    parser.add_argument("--max-files", type=int, default=12)
    parser.add_argument("--max-guard-size", type=int, default=2)
    parser.add_argument("--max-uniques", type=int, default=8)
    parser.add_argument("--oos-fraction", type=float, default=0.30)
    parser.add_argument("--min-train-trades", type=int, default=20)
    parser.add_argument("--min-oos-trades", type=int, default=8)
    parser.add_argument("--min-train-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-oos-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-train-retention", type=float, default=0.15)
    parser.add_argument("--min-oos-retention", type=float, default=0.15)
    parser.add_argument("--keep-top", type=int, default=25)
    parser.add_argument("--out-prefix", default="docs/TRADE_LEDGER_GUARD_MATRIX_2026-06-30")
    args = parser.parse_args()

    docs_dir = resolve_path(args.docs_dir)
    if args.trades_csv:
        paths = [resolve_path(item) for item in args.trades_csv]
    else:
        paths = discover_ledgers(docs_dir, args.max_files)

    ledgers: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for path in paths:
        try:
            rows = read_csv(path)
        except OSError as exc:
            skipped.append({"path": portable(path), "reason": str(exc)})
            continue
        if not has_required_schema(rows):
            skipped.append({"path": portable(path), "reason": "missing_required_schema_or_empty"})
            continue
        if len(rows) < args.min_train_trades + args.min_oos_trades:
            skipped.append({"path": portable(path), "reason": "too_few_rows", "rows": len(rows)})
            continue
        ledgers.append(evaluate_ledger(path, rows, args))

    flat: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    for ledger in ledgers:
        for candidate in ledger["top_candidates"]:
            item = {"ledger": ledger["ledger"], **candidate}
            flat.append(item)
    flat.sort(
        key=lambda item: (
            1 if item["verdict"] == "guard_candidate_needs_forward_observer" else 0,
            safe_float(item["oos"].get("expectancy_r"), -999.0) - safe_float(item["baseline"]["oos"].get("expectancy_r"), -999.0),
            safe_int(item["oos"].get("trades")),
            safe_float(item["full"].get("expectancy_r"), -999.0),
        ),
        reverse=True,
    )
    for rank, item in enumerate(flat[:200], start=1):
        csv_rows.append(
            {
                "ledger": item["ledger"],
                "rank": rank,
                "verdict": item["verdict"],
                "guard": item["guard"],
                "full_trades": item["full"].get("trades"),
                "full_expectancy_r": item["full"].get("expectancy_r"),
                "full_winrate_pct": item["full"].get("winrate_pct"),
                "full_max_drawdown_r": item["full"].get("max_drawdown_r"),
                "train_trades": item["train"].get("trades"),
                "train_expectancy_r": item["train"].get("expectancy_r"),
                "train_winrate_pct": item["train"].get("winrate_pct"),
                "oos_trades": item["oos"].get("trades"),
                "oos_expectancy_r": item["oos"].get("expectancy_r"),
                "oos_winrate_pct": item["oos"].get("winrate_pct"),
                "oos_max_drawdown_r": item["oos"].get("max_drawdown_r"),
                "baseline_oos_trades": item["baseline"]["oos"].get("trades"),
                "baseline_oos_expectancy_r": item["baseline"]["oos"].get("expectancy_r"),
                "baseline_oos_winrate_pct": item["baseline"]["oos"].get("winrate_pct"),
                "oos_retention": item["oos_gate"].get("retention"),
            }
        )

    passing = [item for item in flat if item["verdict"] == "guard_candidate_needs_forward_observer"]
    decision = "guard_candidates_need_forward_observer" if passing else "no_oos_guard_candidate"
    next_action = (
        "freeze the top guard and route it into observer-only forward scoring; do not trade from this report"
        if passing
        else "do not promote guards; keep collecting real OI/funding/liquidation/microstructure data and test a different mechanism"
    )
    report = {
        "generated_at": now_iso(),
        "tool": "tools/trade_ledger_guard_matrix.py",
        "runtime_boundary": {
            "classification": "research_guard_matrix_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required": False,
        },
        "settings": {
            "max_guard_size": args.max_guard_size,
            "oos_fraction": args.oos_fraction,
            "min_train_trades": args.min_train_trades,
            "min_oos_trades": args.min_oos_trades,
            "min_train_expectancy_r": args.min_train_expectancy_r,
            "min_oos_expectancy_r": args.min_oos_expectancy_r,
            "min_train_retention": args.min_train_retention,
            "min_oos_retention": args.min_oos_retention,
        },
        "leakage_policy": {
            "allowed_guard_fields": [
                "side",
                "oi_regime",
                "volume_regime",
                "spot_perp_regime",
                "atr_regime",
                "funding_regime",
                "volume_z",
                "oi_delta_pct",
                "funding",
                "spot_perp_divergence_pct",
                "atr_ratio",
                "body_pct",
                "close_location",
            ],
            "explicitly_excluded_result_fields": [
                "r_net",
                "exit",
                "exit_ts",
                "exit_reason",
                "bars_held",
                "take",
                "stop",
            ],
        },
        "summary": {
            "ledgers_scanned": len(paths),
            "usable_ledgers": len(ledgers),
            "skipped_ledgers": len(skipped),
            "oos_guard_candidates": len(passing),
        },
        "skipped": skipped,
        "ledgers": ledgers,
        "top_candidates": flat[:100],
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }

    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    csv_path = out_prefix.with_name(out_prefix.name + "_top_candidates").with_suffix(".csv")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    write_csv(csv_rows, csv_path)
    print(
        json.dumps(
            {
                "decision": decision,
                "summary": report["summary"],
                "top_candidate": flat[0] if flat else None,
                "json": portable(json_path),
                "md": portable(md_path),
                "csv": portable(csv_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
