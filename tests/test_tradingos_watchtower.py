from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
PATH = TOOLS / "tradingos_watchtower.py"
spec = importlib.util.spec_from_file_location("watchtower", PATH)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NOW = datetime(2026, 8, 16, 16, 30, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)


def rows(direction: int, interval_ms: int, count: int = 50, base: float = 100.0, *, future_last: bool = False):
    out = []
    for i in range(count):
        open_ms = NOW_MS - (count - i) * interval_ms
        if future_last and i == count - 1:
            open_ms = NOW_MS
        close_ms = open_ms + interval_ms - 1
        mid = base + direction * i * 0.7
        o = mid - direction * 0.2
        c = mid + direction * 0.2
        h = max(o, c) + 0.5
        lo = min(o, c) - 0.5
        vol = 100 + i
        buy = vol * 0.65 if direction > 0 else vol * 0.35 if direction < 0 else vol * 0.5
        out.append([open_ms, str(o), str(h), str(lo), str(c), str(vol), close_ms, "0", 100, str(buy), "0", "0"])
    return out


def asset(direction_map: dict[str, int], symbol: str):
    base = 100 if symbol == "BTCUSDT" else 50
    r4 = rows(direction_map["4h"], m.TF_INTERVAL_MS["4h"], 50, base)
    spot = rows(direction_map["4h"], m.TF_INTERVAL_MS["4h"], 30, base)
    premium = rows(0, m.TF_INTERVAL_MS["4h"], 30, 1.0)
    for i, row in enumerate(premium):
        row[1] = row[2] = row[3] = row[4] = str(1.0001 + i * 0.000001)
    return {
        "futures_24h": {"priceChangePercent": "1.0"},
        "mark_price": {"markPrice": "130", "indexPrice": "129.9", "lastFundingRate": "0.0001", "time": NOW_MS},
        "open_interest": {"openInterest": "1010", "time": NOW_MS},
        "open_interest_stats_4h": [
            {"sumOpenInterest": "990", "timestamp": NOW_MS - 8 * 3_600_000},
            {"sumOpenInterest": "1000", "timestamp": NOW_MS - 4 * 3_600_000},
        ],
        "funding_history": [{"fundingRate": str(0.00005 + i * 0.000001), "fundingTime": NOW_MS - (30 - i) * 8 * 3_600_000} for i in range(30)],
        "premium_index_4h": premium,
        "spot_klines_4h": spot,
        "futures_klines": {
            "1h": rows(direction_map["1h"], m.TF_INTERVAL_MS["1h"], 50, base),
            "4h": r4,
            "1d": rows(direction_map["1d"], m.TF_INTERVAL_MS["1d"], 50, base),
        },
    }


def capture(btc=None, eth=None):
    btc = btc or {"1h": 1, "4h": 1, "1d": 1}
    eth = eth or {"1h": -1, "4h": -1, "1d": -1}
    return {
        "schema": m.CAPTURE_SCHEMA,
        "captured_at": NOW.isoformat().replace("+00:00", "Z"),
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "intervals": ["1h", "4h", "1d"],
        "credentials_used": False,
        "private_api_used": False,
        "assets": {"BTCUSDT": asset(btc, "BTCUSDT"), "ETHUSDT": asset(eth, "ETHUSDT")},
    }


def by_symbol(report, symbol):
    return next(row for row in report["matrix"] if row["symbol"] == symbol)


def test_aligned_bull_and_bear_rank_into_watch_biases() -> None:
    report = m.build_watchtower(capture())
    btc, eth = by_symbol(report, "BTCUSDT"), by_symbol(report, "ETHUSDT")
    assert btc["bias"] == "WATCH_LONG" and btc["weighted_confluence"] == 6
    assert eth["bias"] == "WATCH_SHORT" and eth["weighted_confluence"] == -6
    assert report["cross_asset"]["state"] == "DIVERGENT"
    assert report["safety"]["can_trade"] is False


def test_htf_ltf_conflict_fails_closed_to_no_action() -> None:
    report = m.build_watchtower(capture({"1h": -1, "4h": -1, "1d": 1}, {"1h": 1, "4h": 1, "1d": 1}))
    btc = by_symbol(report, "BTCUSDT")
    assert btc["conflict"] == "HTF_LTF_CONFLICT"
    assert btc["bias"] == "NO_ACTION"
    assert btc["clarity"] == "CONFLICT"


def test_private_or_wrong_schema_capture_is_rejected() -> None:
    payload = capture(); payload["credentials_used"] = True
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "credential-free" in str(exc)
    else: raise AssertionError("private capture accepted")
    payload = capture(); payload["schema"] = "wrong"
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "unsupported" in str(exc)
    else: raise AssertionError("wrong schema accepted")


def test_symbols_are_unique_valid_and_exactly_bound_to_assets() -> None:
    payload = capture(); payload["symbols"] = ["BTCUSDT", "BTCUSDT"]
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "duplicate symbol" in str(exc)
    else: raise AssertionError("duplicate symbol accepted")
    payload = capture(); payload["symbols"][0] = "btc<script>"; payload["assets"]["btc<script>"] = payload["assets"].pop("BTCUSDT")
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "symbol format" in str(exc)
    else: raise AssertionError("malformed symbol accepted")
    payload = capture(); payload["assets"]["SOLUSDT"] = asset({"1h": 1, "4h": 1, "1d": 1}, "SOLUSDT")
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "exactly match" in str(exc)
    else: raise AssertionError("extra asset accepted")


def test_intervals_are_exact_and_timestamp_is_timezone_aware() -> None:
    payload = capture(); payload["intervals"] = ["4h", "1h", "1d"]
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "intervals" in str(exc)
    else: raise AssertionError("wrong intervals accepted")
    payload = capture(); payload["captured_at"] = "2026-08-16T16:30:00"
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "timezone" in str(exc)
    else: raise AssertionError("naive timestamp accepted")


def test_kline_exact_arity_fails_closed() -> None:
    payload = capture(); row = payload["assets"]["BTCUSDT"]["futures_klines"]["1h"][0]
    assert len(row) == 12

    payload = capture(); payload["assets"]["BTCUSDT"]["futures_klines"]["1h"][0] = row[:10]
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "exactly 12 fields" in str(exc)
    else: raise AssertionError("10-field kline accepted")

    payload = capture(); payload["assets"]["BTCUSDT"]["futures_klines"]["1h"][0] = row + ["EXTRA"]
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "exactly 12 fields" in str(exc)
    else: raise AssertionError("overlong kline accepted")


def test_kline_order_interval_and_ohlc_are_strict() -> None:
    payload = capture(); kl = payload["assets"]["BTCUSDT"]["futures_klines"]["1h"]
    kl[10], kl[11] = kl[11], kl[10]
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "strictly increasing" in str(exc)
    else: raise AssertionError("unsorted klines accepted")
    payload = capture(); row = payload["assets"]["BTCUSDT"]["futures_klines"]["1h"][0]; row[6] += 1
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "interval mismatch" in str(exc)
    else: raise AssertionError("wrong interval accepted")
    payload = capture(); row = payload["assets"]["BTCUSDT"]["futures_klines"]["1h"][0]; row[2] = "1"
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "OHLC" in str(exc)
    else: raise AssertionError("invalid OHLC accepted")


def test_taker_buy_and_volume_bounds_fail_closed() -> None:
    payload = capture(); row = payload["assets"]["BTCUSDT"]["futures_klines"]["4h"][-1]; row[9] = str(float(row[5]) + 1)
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "taker buy" in str(exc)
    else: raise AssertionError("impossible taker volume accepted")
    payload = capture(); rows_v = payload["assets"]["BTCUSDT"]["futures_klines"]["4h"]
    for row in rows_v[-21:-1]: row[5] = row[9] = "0"
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "prior volume mean" in str(exc)
    else: raise AssertionError("zero prior-volume baseline accepted")


def test_future_bar_is_excluded_and_minimum_closed_bars_enforced() -> None:
    payload = capture(); payload["assets"]["BTCUSDT"]["futures_klines"]["1h"] = rows(1, m.TF_INTERVAL_MS["1h"], 23, 100, future_last=True)
    report = m.build_watchtower(payload)
    assert by_symbol(report, "BTCUSDT")["timeframes"]["1h"]["state"] == "LONG"
    payload = capture(); payload["assets"]["BTCUSDT"]["futures_klines"]["1h"] = rows(1, m.TF_INTERVAL_MS["1h"], 22, 100, future_last=True)
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "not enough closed bars" in str(exc)
    else: raise AssertionError("insufficient closed history accepted")


def test_derivative_timestamps_and_histories_fail_closed() -> None:
    payload = capture(); payload["assets"]["BTCUSDT"]["mark_price"]["time"] = NOW_MS + 1
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "after captured_at" in str(exc)
    else: raise AssertionError("future mark timestamp accepted")
    payload = capture(); payload["assets"]["BTCUSDT"]["mark_price"].pop("time")
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "timestamp missing" in str(exc)
    else: raise AssertionError("missing mark timestamp accepted")
    payload = capture(); payload["assets"]["BTCUSDT"]["open_interest"].pop("time")
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "timestamp missing" in str(exc)
    else: raise AssertionError("missing open-interest timestamp accepted")
    payload = capture(); payload["assets"]["BTCUSDT"]["funding_history"] = payload["assets"]["BTCUSDT"]["funding_history"][:9]
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "at least 10" in str(exc)
    else: raise AssertionError("short funding history accepted")


def test_funding_history_requires_timestamp_binding_and_excludes_future_rows() -> None:
    baseline = capture()
    expected = by_symbol(m.build_watchtower(baseline), "BTCUSDT")

    payload = capture()
    payload["assets"]["BTCUSDT"]["funding_history"].append(
        {"fundingRate": "99", "fundingTime": NOW_MS + 1}
    )
    actual = by_symbol(m.build_watchtower(payload), "BTCUSDT")
    assert actual["derivatives"]["funding_z"] == expected["derivatives"]["funding_z"]
    assert actual["attention_score"] == expected["attention_score"]

    payload = capture(); payload["assets"]["BTCUSDT"]["funding_history"][0].pop("fundingTime")
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "malformed funding row" in str(exc)
    else: raise AssertionError("missing fundingTime accepted")

    payload = capture(); payload["assets"]["BTCUSDT"]["funding_history"][0]["fundingTime"] = "not-an-int"
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "invalid integer" in str(exc)
    else: raise AssertionError("malformed fundingTime accepted")

    payload = capture(); payload["assets"]["BTCUSDT"]["funding_history"][0]["fundingTime"] = -1
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "negative timestamp" in str(exc)
    else: raise AssertionError("negative fundingTime accepted")


def test_funding_history_requires_ten_nonfuture_strictly_ordered_rows() -> None:
    payload = capture()
    hist = payload["assets"]["BTCUSDT"]["funding_history"]
    for i, row in enumerate(hist):
        row["fundingTime"] = NOW_MS - (9 - i) if i < 9 else NOW_MS + (i - 8)
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "at least 10 nonfuture rows" in str(exc)
    else: raise AssertionError("insufficient nonfuture funding history accepted")

    payload = capture()
    hist = payload["assets"]["BTCUSDT"]["funding_history"]
    hist[10]["fundingTime"] = hist[9]["fundingTime"]
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "strictly increasing" in str(exc)
    else: raise AssertionError("unordered funding history accepted")


def test_open_interest_reference_uses_latest_nonfuture_observation() -> None:
    payload = capture()
    stats = payload["assets"]["BTCUSDT"]["open_interest_stats_4h"]
    stats.append({"sumOpenInterest": "5000", "timestamp": NOW_MS + 1})
    report = m.build_watchtower(payload)
    assert by_symbol(report, "BTCUSDT")["derivatives"]["open_interest_change_pct"] == 1.0


def test_capture_fingerprint_is_stable_and_input_sensitive() -> None:
    a = capture(); b = json.loads(json.dumps(a, sort_keys=True))
    ra, rb = m.build_watchtower(a), m.build_watchtower(b)
    assert ra["provenance"]["capture_sha256"] == rb["provenance"]["capture_sha256"]
    c = capture(); c["assets"]["BTCUSDT"]["open_interest"]["openInterest"] = "1011"
    rc = m.build_watchtower(c)
    assert rc["provenance"]["capture_sha256"] != ra["provenance"]["capture_sha256"]


def test_build_is_deterministic_for_identical_capture() -> None:
    payload = capture()
    assert m.build_watchtower(payload) == m.build_watchtower(json.loads(json.dumps(payload)))


def test_renderer_rejects_unsafe_or_invalid_report_and_repeats_boundary() -> None:
    report = m.build_watchtower(capture())
    page = m.render_html(report)
    assert "MULTI-ASSET WATCHTOWER" in page
    assert "urgency ≠ trade quality" in page
    assert "no network fetch" in page
    assert "can_trade=false" in page and "capital_permission=DENY" in page
    bad = json.loads(json.dumps(report)); bad["safety"]["can_trade"] = True
    try: m.render_html(bad)
    except ValueError as exc: assert "unsafe" in str(exc)
    else: raise AssertionError("unsafe report rendered")
    bad = json.loads(json.dumps(report)); bad["matrix"][0]["bias"] = "BUY_NOW"
    try: m.render_html(bad)
    except ValueError as exc: assert "bias" in str(exc)
    else: raise AssertionError("invalid bias rendered")


def test_cli_generates_json_and_html_offline(tmp_path: Path) -> None:
    inp = tmp_path / "capture.json"; out = tmp_path / "out"
    inp.write_text(json.dumps(capture()), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(PATH), "--input", str(inp), "--out-dir", str(out)], text=True, capture_output=True, env={**os.environ, "PYTHONPATH": str(TOOLS)}, check=False)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads((out / "watchtower.json").read_text(encoding="utf-8"))
    assert payload["schema"] == m.SCHEMA and payload["version"] == "1.1.0"
    assert payload["safety"]["can_trade"] is False
    assert (out / "watchtower.html").exists()


def test_nonfinite_market_values_fail_closed() -> None:
    payload = capture(); payload["assets"]["BTCUSDT"]["mark_price"]["lastFundingRate"] = math.inf
    try: m.build_watchtower(payload)
    except ValueError as exc: assert "non-finite" in str(exc)
    else: raise AssertionError("infinite funding accepted")


def test_candidate_has_no_network_send_exchange_or_execution_imports() -> None:
    text = PATH.read_text(encoding="utf-8").lower()
    forbidden = ["urllib", "requests", "httpx", "socket", "telegram", "webhook", "create_order", "place_order", "send_message", "ccxt"]
    assert all(token not in text for token in forbidden)
    assert "capital_permission\": \"deny" in text
