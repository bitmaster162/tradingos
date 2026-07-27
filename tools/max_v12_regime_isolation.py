from __future__ import annotations

import argparse
import itertools
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


def parse_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def percentile(values: list[float], p: float) -> float | None:
    clean = sorted(v for v in values if not math.isnan(v))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * p
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return clean[int(rank)]
    weight = rank - lo
    return clean[lo] * (1 - weight) + clean[hi] * weight


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    values = [parse_float(trade.get("net_r"), 0.0) for trade in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(trades) * 100, 3) if trades else None,
        "expectancy_r": round(sum(values) / len(values), 6) if values else None,
        "net_r_total": round(sum(values), 6) if values else 0.0,
        "avg_win_r": round(sum(wins) / len(wins), 6) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 6) if losses else None,
        "max_drawdown_r": round(max_dd, 6),
    }


def add_features(trades: list[dict[str, Any]], source_report: dict[str, Any]) -> list[dict[str, Any]]:
    folds = source_report.get("folds") if isinstance(source_report.get("folds"), list) else []
    enriched: list[dict[str, Any]] = []
    for trade in trades:
        item = dict(trade)
        entry_time = str(item.get("entry_time", ""))
        item["_year"] = entry_time[:4] if len(entry_time) >= 4 else "unknown"
        try:
            item["_month"] = entry_time[:7]
        except Exception:
            item["_month"] = "unknown"
        entry_row = int(item.get("entry_row", -1))
        fold_id = "unknown"
        for fold in folds:
            start = int(fold.get("row_start", -1))
            end = int(fold.get("row_end", -1))
            if start <= entry_row < end:
                fold_id = f"fold_{fold.get('fold')}"
                break
        item["_fold"] = fold_id
        enriched.append(item)
    return enriched


def bucket_conditions(trades: list[dict[str, Any]], *, include_post_trade: bool = False) -> list[tuple[str, Any]]:
    width_values = [parse_float(t.get("donchian_width_atr")) for t in trades]
    spot_values = [parse_float(t.get("spot_volume_ratio")) for t in trades]
    atr_values = [parse_float(t.get("atr14")) for t in trades]
    atr_rank_values = [parse_float(t.get("atr_pct_rank_500")) for t in trades]
    width_rank_values = [parse_float(t.get("donchian_width_atr_rank_500")) for t in trades]
    ema_distance_values = [parse_float(t.get("ema200_distance_pct")) for t in trades]
    trend_strength_values = [parse_float(t.get("trend_strength_20_atr")) for t in trades]
    div12_values = [parse_float(t.get("spot_perp_divergence_12")) for t in trades]
    bars_values = [parse_float(t.get("bars_held")) for t in trades]
    width_q33 = percentile(width_values, 0.33)
    width_q66 = percentile(width_values, 0.66)
    spot_q33 = percentile(spot_values, 0.33)
    spot_q66 = percentile(spot_values, 0.66)
    atr_q33 = percentile(atr_values, 0.33)
    atr_q66 = percentile(atr_values, 0.66)
    ema_dist_q33 = percentile(ema_distance_values, 0.33)
    ema_dist_q66 = percentile(ema_distance_values, 0.66)
    trend_q33 = percentile(trend_strength_values, 0.33)
    trend_q66 = percentile(trend_strength_values, 0.66)

    def num(name: str) -> Any:
        return lambda t: parse_float(t.get(name))

    conditions: list[tuple[str, Any]] = [
        ("year=2026", lambda t: t.get("_year") == "2026"),
        ("year=2025", lambda t: t.get("_year") == "2025"),
        ("htf_bias=SHORT", lambda t: t.get("htf_bias") == "SHORT"),
        ("htf_bias=LONG", lambda t: t.get("htf_bias") == "LONG"),
        ("htf_bias=NEUTRAL", lambda t: t.get("htf_bias") == "NEUTRAL"),
        ("htf_regime=trend_down", lambda t: t.get("htf_regime") == "htf_trend_down"),
        ("htf_regime=down_bias", lambda t: t.get("htf_regime") == "htf_down_bias"),
        ("ema_state=below_stack", lambda t: t.get("ema_state") == "below_ema_stack"),
        ("ema_state=below_ema200", lambda t: t.get("ema_state") in {"below_ema_stack", "below_ema200"}),
        ("ema_state=above_ema200", lambda t: t.get("ema_state") in {"above_ema_stack", "above_ema200"}),
        ("ema200_dist<=-2", lambda t: num("ema200_distance_pct")(t) <= -2),
        ("ema200_dist<=-5", lambda t: num("ema200_distance_pct")(t) <= -5),
        ("ema200_dist>=0", lambda t: num("ema200_distance_pct")(t) >= 0),
        ("trend20atr<=-2", lambda t: num("trend_strength_20_atr")(t) <= -2),
        ("trend20atr<=-4", lambda t: num("trend_strength_20_atr")(t) <= -4),
        ("trend20atr>=0", lambda t: num("trend_strength_20_atr")(t) >= 0),
        ("atr_pct_rank<=0.33", lambda t: num("atr_pct_rank_500")(t) <= 0.33),
        ("atr_pct_rank>=0.66", lambda t: num("atr_pct_rank_500")(t) >= 0.66),
        ("width_rank<=0.33", lambda t: num("donchian_width_atr_rank_500")(t) <= 0.33),
        ("width_rank>=0.66", lambda t: num("donchian_width_atr_rank_500")(t) >= 0.66),
        ("spot_div12=spot_stronger", lambda t: t.get("spot_perp_divergence_12_sign") == "spot_stronger"),
        ("spot_div12=spot_weaker", lambda t: t.get("spot_perp_divergence_12_sign") == "spot_weaker"),
        ("spot_div12>=0", lambda t: num("spot_perp_divergence_12")(t) >= 0),
        ("spot_div12<0", lambda t: num("spot_perp_divergence_12")(t) < 0),
    ]
    if include_post_trade:
        conditions.extend(
            [
                ("exit=take_profit", lambda t: t.get("exit_reason") == "take_profit"),
                ("exit=stop", lambda t: t.get("exit_reason") == "stop"),
                ("exit=time_stop", lambda t: t.get("exit_reason") == "time_stop"),
                ("bars_held<=4", lambda t: num("bars_held")(t) <= 4),
                ("bars_held>=12", lambda t: num("bars_held")(t) >= 12),
            ]
        )
    if width_q33 is not None and width_q66 is not None:
        conditions.extend(
            [
                (f"width_atr<=q33({width_q33:.3f})", lambda t, v=width_q33: num("donchian_width_atr")(t) <= v),
                (
                    f"width_atr_mid({width_q33:.3f}..{width_q66:.3f})",
                    lambda t, lo=width_q33, hi=width_q66: lo < num("donchian_width_atr")(t) <= hi,
                ),
                (f"width_atr>q66({width_q66:.3f})", lambda t, v=width_q66: num("donchian_width_atr")(t) > v),
                ("width_atr<=4", lambda t: num("donchian_width_atr")(t) <= 4),
                ("width_atr>=6", lambda t: num("donchian_width_atr")(t) >= 6),
            ]
        )
    if spot_q33 is not None and spot_q66 is not None:
        conditions.extend(
            [
                (f"spot_vol<=q33({spot_q33:.3f})", lambda t, v=spot_q33: num("spot_volume_ratio")(t) <= v),
                (
                    f"spot_vol_mid({spot_q33:.3f}..{spot_q66:.3f})",
                    lambda t, lo=spot_q33, hi=spot_q66: lo < num("spot_volume_ratio")(t) <= hi,
                ),
                (f"spot_vol>q66({spot_q66:.3f})", lambda t, v=spot_q66: num("spot_volume_ratio")(t) > v),
                ("spot_vol<=0.55", lambda t: num("spot_volume_ratio")(t) <= 0.55),
                ("spot_vol>=0.70", lambda t: num("spot_volume_ratio")(t) >= 0.70),
            ]
        )
    if atr_q33 is not None and atr_q66 is not None:
        conditions.extend(
            [
                (f"atr<=q33({atr_q33:.2f})", lambda t, v=atr_q33: num("atr14")(t) <= v),
                (f"atr_mid({atr_q33:.2f}..{atr_q66:.2f})", lambda t, lo=atr_q33, hi=atr_q66: lo < num("atr14")(t) <= hi),
                (f"atr>q66({atr_q66:.2f})", lambda t, v=atr_q66: num("atr14")(t) > v),
            ]
        )
    if ema_dist_q33 is not None and ema_dist_q66 is not None:
        conditions.extend(
            [
                (f"ema200_dist<=q33({ema_dist_q33:.2f})", lambda t, v=ema_dist_q33: num("ema200_distance_pct")(t) <= v),
                (
                    f"ema200_dist_mid({ema_dist_q33:.2f}..{ema_dist_q66:.2f})",
                    lambda t, lo=ema_dist_q33, hi=ema_dist_q66: lo < num("ema200_distance_pct")(t) <= hi,
                ),
                (f"ema200_dist>q66({ema_dist_q66:.2f})", lambda t, v=ema_dist_q66: num("ema200_distance_pct")(t) > v),
            ]
        )
    if trend_q33 is not None and trend_q66 is not None:
        conditions.extend(
            [
                (f"trend20atr<=q33({trend_q33:.2f})", lambda t, v=trend_q33: num("trend_strength_20_atr")(t) <= v),
                (
                    f"trend20atr_mid({trend_q33:.2f}..{trend_q66:.2f})",
                    lambda t, lo=trend_q33, hi=trend_q66: lo < num("trend_strength_20_atr")(t) <= hi,
                ),
                (f"trend20atr>q66({trend_q66:.2f})", lambda t, v=trend_q66: num("trend_strength_20_atr")(t) > v),
            ]
        )
    folds = sorted({str(t.get("_fold")) for t in trades if t.get("_fold")})
    for fold in folds:
        conditions.append((fold, lambda t, f=fold: t.get("_fold") == f))
    return conditions


def evaluate_slices(
    trades: list[dict[str, Any]],
    *,
    min_trades: int,
    min_expectancy: float,
    min_winrate: float,
    max_conditions: int,
    include_post_trade: bool = False,
) -> list[dict[str, Any]]:
    conditions = bucket_conditions(trades, include_post_trade=include_post_trade)
    results: list[dict[str, Any]] = []
    for width in range(1, max(1, max_conditions) + 1):
        for combo in itertools.combinations(conditions, width):
            labels = [label for label, _fn in combo]
            fns = [fn for _label, fn in combo]
            subset = [trade for trade in trades if all(fn(trade) for fn in fns)]
            summary = summarize(subset)
            if summary["trades"] < min_trades:
                continue
            expectancy = summary["expectancy_r"]
            winrate = summary["winrate_pct"]
            pass_soft = bool(
                expectancy is not None
                and expectancy >= min_expectancy
                and winrate is not None
                and winrate >= min_winrate
            )
            results.append(
                {
                    "conditions": labels,
                    "summary": summary,
                    "pass_soft": pass_soft,
                }
            )
    results.sort(
        key=lambda item: (
            bool(item["pass_soft"]),
            float(item["summary"].get("expectancy_r") or -999),
            float(item["summary"].get("winrate_pct") or 0),
            int(item["summary"].get("trades") or 0),
        ),
        reverse=True,
    )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for item in results:
        key = tuple(item["conditions"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MAX Core Lite v1.2 Regime Isolation",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Source: `{report['source_path']}`",
        f"- Candidate: `{report['candidate']}`",
        "",
        "## Baseline",
        "",
    ]
    baseline = report["baseline"]
    lines.extend(
        [
            f"- Trades: `{baseline['trades']}`",
            f"- Winrate: `{baseline['winrate_pct']}`",
            f"- Expectancy: `{baseline['expectancy_r']}`",
            f"- Net R: `{baseline['net_r_total']}`",
            "",
            "## Top Slices",
            "",
            "| Conditions | Trades | Winrate | Expectancy | Net R | Soft Pass |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for item in report["top_slices"]:
        summary = item["summary"]
        lines.append(
            f"| `{ ' + '.join(item['conditions']) }` | {summary['trades']} | {summary['winrate_pct']} | "
            f"{summary['expectancy_r']} | {summary['net_r_total']} | `{item['pass_soft']}` |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            report["decision"],
            "",
            "## Boundary",
            "",
            report["runtime_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def is_date_or_fold_slice(item: dict[str, Any]) -> bool:
    conditions = item.get("conditions") if isinstance(item.get("conditions"), list) else []
    return any(str(condition).startswith("fold_") or str(condition).startswith("year=") for condition in conditions)


def main() -> int:
    parser = argparse.ArgumentParser(description="Regime/slice isolation over v1.1 candidate trades")
    parser.add_argument("--source", default="_dl/control_panel/MAX_CORE_LITE_V11_1H_WEAK_BID.json")
    parser.add_argument("--out-prefix", default="_dl/v12/MAX_CORE_LITE_V12_REGIME_ISOLATION")
    parser.add_argument("--min-trades", type=int, default=15)
    parser.add_argument("--min-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-winrate-pct", type=float, default=50.0)
    parser.add_argument("--min-robust-structural-trades", type=int, default=30)
    parser.add_argument("--min-robust-structural-expectancy-r", type=float, default=0.15)
    parser.add_argument("--max-conditions", type=int, default=2)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--include-post-trade-features", action="store_true")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_absolute():
        source = ROOT / source
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    payload = json.loads(source.read_text(encoding="utf-8"))
    trades = add_features(payload.get("trades", []), payload)
    baseline = summarize(trades)
    slices = evaluate_slices(
        trades,
        min_trades=args.min_trades,
        min_expectancy=args.min_expectancy_r,
        min_winrate=args.min_winrate_pct,
        max_conditions=args.max_conditions,
        include_post_trade=args.include_post_trade_features,
    )
    pass_slices = [item for item in slices if item["pass_soft"]]
    structural_pass_slices = [item for item in pass_slices if not is_date_or_fold_slice(item)]
    robust_structural_slices = [
        item
        for item in structural_pass_slices
        if int(item["summary"].get("trades") or 0) >= args.min_robust_structural_trades
        and float(item["summary"].get("expectancy_r") or -999.0) >= args.min_robust_structural_expectancy_r
    ]
    decision = (
        "Robust structural slices exist, but they are not approved. Build a new pre-trade candidate and rerun v1.1 validation."
        if robust_structural_slices
        else "No robust standalone structural regime found. Positive slices are date/fold dependent or too weak. Keep candidate blocked."
    )
    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_V12_REGIME_ISOLATION",
        "engine_version": "1.2.0",
        "source_path": str(source),
        "candidate": (payload.get("candidate") or {}).get("id", "unknown"),
        "config": {
            "min_trades": args.min_trades,
            "min_expectancy_r": args.min_expectancy_r,
            "min_winrate_pct": args.min_winrate_pct,
            "min_robust_structural_trades": args.min_robust_structural_trades,
            "min_robust_structural_expectancy_r": args.min_robust_structural_expectancy_r,
            "max_conditions": args.max_conditions,
            "top": args.top,
            "include_post_trade_features": args.include_post_trade_features,
        },
        "baseline": baseline,
        "top_slices": slices[: args.top],
        "pass_slices": pass_slices,
        "structural_pass_slices": structural_pass_slices,
        "robust_structural_slices": robust_structural_slices,
        "decision": decision,
        "runtime_boundary": (
            "Research-only slice isolation over already simulated trades. It does not change strategy parameters, "
            "does not place orders, and does not approve paper/live trading."
        ),
    }
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(json_path),
                "md": str(md_path),
                "baseline": baseline,
                "pass_slices": len(pass_slices),
                "top_slice": slices[0] if slices else None,
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
