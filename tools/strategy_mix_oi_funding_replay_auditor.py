#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def pct_delta(rows: list[dict[str, str]], index: int, field: str, lookback: int) -> float | None:
    if index - lookback < 0:
        return None
    current = safe_float(rows[index].get(field))
    previous = safe_float(rows[index - lookback].get(field))
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100


def latest_row_at_or_before(rows: list[dict[str, str]], target_ts: str) -> tuple[int, dict[str, str] | None]:
    target = parse_time(target_ts)
    if target is None:
        return -1, None
    best_index = -1
    best_time: datetime | None = None
    for index, row in enumerate(rows):
        row_time = parse_time(row.get("time") or row.get("timestamp"))
        if row_time is None or row_time > target:
            continue
        if best_time is None or row_time >= best_time:
            best_index = index
            best_time = row_time
    return best_index, rows[best_index] if best_index >= 0 else None


def classify_funding(funding: float | None, compressed_abs: float, hot_abs: float) -> str:
    if funding is None:
        return "unavailable"
    if funding >= hot_abs:
        return "positive_hot"
    if funding <= -hot_abs:
        return "negative_hot"
    if abs(funding) <= compressed_abs:
        return "compressed"
    return "positive_mild" if funding > 0 else "negative_mild"


def classify_oi(delta_pct: float | None, strong_abs_pct: float) -> str:
    if delta_pct is None:
        return "unavailable"
    if delta_pct >= strong_abs_pct:
        return "expansion_strong"
    if delta_pct > 0:
        return "expansion_mild"
    if delta_pct <= -strong_abs_pct:
        return "contraction_strong"
    if delta_pct < 0:
        return "contraction_mild"
    return "flat"


def classify_context_bias(price_delta_pct: float | None, oi_delta_pct: float | None, funding_state: str) -> str:
    if price_delta_pct is None or oi_delta_pct is None or funding_state == "unavailable":
        return "unavailable"
    if price_delta_pct > 0 and oi_delta_pct > 0:
        return "trend_confirmation_long"
    if price_delta_pct > 0 and oi_delta_pct < 0:
        return "short_squeeze_or_position_closing"
    if price_delta_pct < 0 and oi_delta_pct > 0:
        return "short_build_or_downtrend_confirmation"
    if price_delta_pct < 0 and oi_delta_pct < 0:
        return "deleveraging_or_capitulation"
    return "mixed"


def quantile(values: list[float], q: float) -> float | None:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * q
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return clean[int(pos)]
    weight = pos - lower
    return clean[lower] * (1 - weight) + clean[upper] * weight


def funding_bucket(funding: float | None, q25: float | None, q75: float | None) -> str:
    if funding is None:
        return "unavailable"
    if q25 is None or q75 is None:
        return "available"
    if abs(q75 - q25) <= 1e-12:
        return "funding_flat_distribution"
    if funding <= q25:
        return "funding_low_q25"
    if funding >= q75:
        return "funding_high_q75"
    return "funding_mid"


def sign_bucket(value: float | None, neutral_abs: float = 1e-12) -> str:
    if value is None:
        return "unavailable"
    if value > neutral_abs:
        return "positive"
    if value < -neutral_abs:
        return "negative"
    return "neutral"


def stats(values: list[float]) -> dict[str, Any]:
    clean = [value for value in values if math.isfinite(value)]
    wins = [value for value in clean if value > 0]
    losses = [value for value in clean if value <= 0]
    if not clean:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "winrate_pct": None,
            "expectancy_r": None,
            "net_r_total": 0.0,
            "avg_win_r": None,
            "avg_loss_r": None,
        }
    return {
        "trades": len(clean),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(clean) * 100, 3),
        "expectancy_r": round(sum(clean) / len(clean), 6),
        "net_r_total": round(sum(clean), 6),
        "avg_win_r": round(sum(wins) / len(wins), 6) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 6) if losses else None,
    }


def bucket_stats(rows: list[dict[str, Any]], field: str, overall_expectancy: float | None, min_trades: int) -> list[dict[str, Any]]:
    groups: dict[str, list[float]] = {}
    for row in rows:
        key = str(row.get(field) or "unavailable")
        value = safe_float(row.get("r_net"))
        if value is not None:
            groups.setdefault(key, []).append(value)
    output: list[dict[str, Any]] = []
    for key, values in sorted(groups.items()):
        item = {"bucket": key, **stats(values)}
        expectancy = item.get("expectancy_r")
        if isinstance(expectancy, (int, float)) and isinstance(overall_expectancy, (int, float)):
            item["expectancy_lift_r"] = round(expectancy - overall_expectancy, 6)
        else:
            item["expectancy_lift_r"] = None
        item["sample_gate"] = "pass" if item["trades"] >= min_trades else "fail_too_few_trades"
        if item["sample_gate"] == "pass" and isinstance(item["expectancy_lift_r"], (int, float)) and item["expectancy_lift_r"] >= 0.05:
            item["guard_read"] = "candidate_keep_filter"
        elif item["sample_gate"] == "pass" and isinstance(item["expectancy_lift_r"], (int, float)) and item["expectancy_lift_r"] <= -0.05:
            item["guard_read"] = "candidate_avoid_filter"
        else:
            item["guard_read"] = "observe_only"
        output.append(item)
    output.sort(
        key=lambda item: (
            item["sample_gate"] != "pass",
            -(item.get("expectancy_r") if isinstance(item.get("expectancy_r"), (int, float)) else -999),
            -item["trades"],
        )
    )
    return output


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strategy Mix OI/Funding Replay Audit",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Offline audit only.",
        "- Uses existing paper replay trades and local derivatives cache.",
        "- No network, no private credentials, no orders, no guard promotion by itself.",
        "",
        "## Summary",
        "",
    ]
    summary = report.get("summary", {})
    for key in [
        "classification",
        "total_trades",
        "overall_expectancy_r",
        "overall_winrate_pct",
        "joined_derivatives_rows",
        "funding_available_trades",
        "oi_available_trades",
        "full_context_available_trades",
        "data_degraded_trades",
    ]:
        lines.append(f"- {key}: `{summary.get(key)}`.")
    lines.extend(["", "## Data Quality", ""])
    quality = report.get("data_quality", {})
    for key, value in quality.items():
        lines.append(f"- {key}: `{value}`.")
    lines.extend(["", "## Bucket Reads", ""])
    for section, title in [
        ("by_funding_bucket", "Funding quantile bucket"),
        ("by_funding_sign", "Funding sign"),
        ("by_observer_funding_state", "Observer-compatible funding state"),
        ("by_oi_state", "OI state"),
        ("by_context_bias", "Context bias"),
        ("by_data_quality", "Data quality"),
    ]:
        lines.append(f"### {title}")
        lines.append("")
        rows = report.get("bucket_stats", {}).get(section) or []
        if not rows:
            lines.append("- No rows.")
            lines.append("")
            continue
        lines.append("| bucket | trades | winrate | expectancy | lift | gate | read |")
        lines.append("|---|---:|---:|---:|---:|---|---|")
        for row in rows:
            lines.append(
                "| {bucket} | {trades} | {winrate_pct} | {expectancy_r} | {expectancy_lift_r} | {sample_gate} | {guard_read} |".format(
                    **row
                )
            )
        lines.append("")
    lines.extend(["## Decision", "", f"- `{report.get('decision')}`.", ""])
    lines.extend(["## Next Action", "", f"- `{report.get('next_action')}`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline OI/funding audit for strategy-mix paper replay trades")
    parser.add_argument("--trades-csv", default="docs/STRATEGY_MIX_PAPER_REPLAY_2026-06-08_trades.csv")
    parser.add_argument("--derivatives-csv", default="data/cache/binance_spot_perp_extended/futures/BTCUSDT/4h_oi_aligned.csv")
    parser.add_argument("--oi-lookback", type=int, default=12)
    parser.add_argument("--price-lookback", type=int, default=12)
    parser.add_argument("--oi-strong-abs-pct", type=float, default=0.10)
    parser.add_argument("--funding-compressed-abs", type=float, default=0.0002)
    parser.add_argument("--funding-hot-abs", type=float, default=0.0008)
    parser.add_argument("--min-bucket-trades", type=int, default=10)
    parser.add_argument("--out-prefix", default="docs/STRATEGY_MIX_OI_FUNDING_REPLAY_AUDIT_2026-06-15")
    args = parser.parse_args()

    trades_path = resolve_path(args.trades_csv)
    derivatives_path = resolve_path(args.derivatives_csv)
    out_prefix = resolve_path(args.out_prefix)

    trades = read_csv_rows(trades_path)
    derivatives = read_csv_rows(derivatives_path)
    if not trades:
        raise SystemExit(f"no trades found: {trades_path}")
    if not derivatives:
        raise SystemExit(f"no derivatives rows found: {derivatives_path}")

    funding_values = [safe_float(row.get("funding")) for row in derivatives]
    funding_clean = [value for value in funding_values if value is not None]
    q25 = quantile(funding_clean, 0.25)
    q75 = quantile(funding_clean, 0.75)

    enriched: list[dict[str, Any]] = []
    for trade in trades:
        r_net = safe_float(trade.get("r_net"))
        entry_ts = trade.get("entry_ts", "")
        index, row = latest_row_at_or_before(derivatives, entry_ts)
        funding = safe_float(row.get("funding")) if row else None
        open_interest = safe_float(row.get("open_interest")) if row else None
        oi_delta = pct_delta(derivatives, index, "open_interest", args.oi_lookback) if index >= 0 else None
        price_delta = pct_delta(derivatives, index, "price", args.price_lookback) if index >= 0 else None
        funding_state = classify_funding(funding, args.funding_compressed_abs, args.funding_hot_abs)
        oi_state = classify_oi(oi_delta, args.oi_strong_abs_pct)
        context_bias = classify_context_bias(price_delta, oi_delta, funding_state)
        data_quality = "full_context_available" if funding is not None and oi_delta is not None else "data_degraded"
        if funding is None:
            data_quality = "missing_funding"
        elif oi_delta is None:
            data_quality = "missing_oi_context"
        enriched.append(
            {
                **trade,
                "r_net": r_net,
                "derivatives_ts": (row.get("time") or row.get("timestamp")) if row else "",
                "funding": funding,
                "funding_sign": sign_bucket(funding),
                "funding_bucket": funding_bucket(funding, q25, q75),
                "observer_funding_state": funding_state,
                "open_interest": open_interest,
                "oi_delta_pct": oi_delta,
                "oi_state": oi_state,
                "price_delta_pct": price_delta,
                "context_bias": context_bias,
                "data_quality": data_quality,
            }
        )

    r_values = [safe_float(row.get("r_net")) for row in enriched]
    clean_r = [value for value in r_values if value is not None]
    overall = stats(clean_r)
    overall_expectancy = overall.get("expectancy_r")
    funding_available = sum(1 for row in enriched if row.get("funding") is not None)
    oi_available = sum(1 for row in enriched if row.get("open_interest") is not None)
    full_context = sum(1 for row in enriched if row.get("data_quality") == "full_context_available")
    data_degraded = sum(1 for row in enriched if row.get("data_quality") != "full_context_available")

    classification = "audit_complete_guard_not_promoted"
    if full_context < args.min_bucket_trades:
        classification = "audit_complete_derivatives_context_too_sparse"
    elif full_context >= args.min_bucket_trades:
        classification = "audit_complete_guard_candidates_detected"

    report = {
        "generated_at": now_iso(),
        "boundary": {
            "classification": "offline_replay_audit",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required": False,
        },
        "inputs": {
            "trades_csv": str(trades_path.relative_to(ROOT) if trades_path.is_relative_to(ROOT) else trades_path),
            "derivatives_csv": str(derivatives_path.relative_to(ROOT) if derivatives_path.is_relative_to(ROOT) else derivatives_path),
            "oi_lookback": args.oi_lookback,
            "price_lookback": args.price_lookback,
            "min_bucket_trades": args.min_bucket_trades,
            "funding_q25": q25,
            "funding_q75": q75,
        },
        "summary": {
            "classification": classification,
            "total_trades": len(enriched),
            "overall_expectancy_r": overall_expectancy,
            "overall_winrate_pct": overall.get("winrate_pct"),
            "joined_derivatives_rows": sum(1 for row in enriched if row.get("derivatives_ts")),
            "funding_available_trades": funding_available,
            "oi_available_trades": oi_available,
            "full_context_available_trades": full_context,
            "data_degraded_trades": data_degraded,
        },
        "data_quality": {
            "funding_coverage_pct": round(funding_available / len(enriched) * 100, 3) if enriched else 0.0,
            "oi_raw_coverage_pct": round(oi_available / len(enriched) * 100, 3) if enriched else 0.0,
            "full_context_coverage_pct": round(full_context / len(enriched) * 100, 3) if enriched else 0.0,
            "note": "local 4h OI/funding context is available after Binance Vision historical OI backfill; audit still does not promote a live guard by itself",
        },
        "overall_stats": overall,
        "bucket_stats": {
            "by_funding_bucket": bucket_stats(enriched, "funding_bucket", overall_expectancy, args.min_bucket_trades),
            "by_funding_sign": bucket_stats(enriched, "funding_sign", overall_expectancy, args.min_bucket_trades),
            "by_observer_funding_state": bucket_stats(enriched, "observer_funding_state", overall_expectancy, args.min_bucket_trades),
            "by_oi_state": bucket_stats(enriched, "oi_state", overall_expectancy, args.min_bucket_trades),
            "by_context_bias": bucket_stats(enriched, "context_bias", overall_expectancy, args.min_bucket_trades),
            "by_data_quality": bucket_stats(enriched, "data_quality", overall_expectancy, args.min_bucket_trades),
        },
        "decision": "observe_only_no_guard_promotion_no_orders",
        "next_action": "run OI guard validator and attach accepted candidates to forward observation only",
        "enriched_trades_csv": str(out_prefix.with_name(out_prefix.name + "_enriched_trades.csv").relative_to(ROOT)),
    }

    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    enriched_path = out_prefix.with_name(out_prefix.name + "_enriched_trades.csv")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    fieldnames = list(enriched[0].keys()) if enriched else []
    write_csv(enriched_path, enriched, fieldnames)
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(f"wrote {enriched_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
