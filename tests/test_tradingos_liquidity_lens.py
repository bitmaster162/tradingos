from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
CORE = TOOLS / "tradingos_liquidity_lens_core.py"
spec = importlib.util.spec_from_file_location("liq_lens_core", CORE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def book(bid_scale=1.0, ask_scale=1.0, bid_wall=False, ask_wall=False, levels=40):
    mid = 100.0
    bids, asks = [], []
    for i in range(1, levels + 1):
        bp, ap = mid - i * 0.02, mid + i * 0.02
        bq, aq = 1.0 * bid_scale, 1.0 * ask_scale
        if bid_wall and i == 3:
            bq = 6.0 * bid_scale
        if ask_wall and i == 4:
            aq = 7.0 * ask_scale
        bids.append([f"{bp:.2f}", f"{bq:.4f}"])
        asks.append([f"{ap:.2f}", f"{aq:.4f}"])
    return {"lastUpdateId": 123, "bids": bids, "asks": asks}


def capture(btc=None, eth=None):
    btc = btc or book()
    eth = eth or book()
    return {
        "schema": m.CAPTURE_SCHEMA,
        "captured_at": "2026-08-16T15:00:00Z",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "limit": 500,
        "credentials_used": False,
        "private_api_used": False,
        "source": "binance_usds_futures_public_depth",
        "books": {
            "BTCUSDT": {"source_url": "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=500", "snapshot": btc},
            "ETHUSDT": {"source_url": "https://fapi.binance.com/fapi/v1/depth?symbol=ETHUSDT&limit=500", "snapshot": eth},
        },
    }


def row(report, symbol="BTCUSDT"):
    return next(x for x in report["matrix"] if x["symbol"] == symbol)


def test_balanced_full_coverage_and_safety_contract() -> None:
    report = m.build_lens(capture())
    btc = row(report)
    assert btc["quality"] == "PASS"
    assert btc["state"] == "BALANCED"
    assert abs(btc["composite_imbalance"]) < 0.02
    assert btc["complete_bands_bps"] == [10, 25, 50]
    assert report["safety"] == {
        "visible_book_only": True,
        "liquidation_map": False,
        "hidden_liquidity_inferred": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }


def test_bid_heavy_and_ask_heavy_classification() -> None:
    report = m.build_lens(capture(book(bid_scale=3, ask_scale=1), book(bid_scale=1, ask_scale=3)))
    assert row(report, "BTCUSDT")["state"] == "BID_HEAVY"
    assert row(report, "ETHUSDT")["state"] == "ASK_HEAVY"


def test_wall_detection_finds_nearest_side_walls() -> None:
    r = m.analyze_book("BTCUSDT", book(bid_wall=True, ask_wall=True))
    assert r["nearest_bid_wall"] is not None
    assert r["nearest_ask_wall"] is not None
    assert r["nearest_bid_wall"]["multiple_of_median"] >= 3
    assert r["nearest_ask_wall"]["multiple_of_median"] >= 3
    assert "NEAR_BID_WALL" in r["flags"]
    assert "NEAR_ASK_WALL" in r["flags"]


def test_partial_even_with_some_complete_bands_cannot_claim_directional_state() -> None:
    r = m.analyze_book("BTCUSDT", book(bid_scale=5, ask_scale=1, levels=10))
    assert 10 in r["complete_bands_bps"]
    assert len(r["complete_bands_bps"]) < 3
    assert r["quality"] == "PARTIAL"
    assert r["state"] == "INSUFFICIENT_DEPTH_COVERAGE"
    assert r["composite_imbalance"] is None
    assert "INSUFFICIENT_DEPTH_COVERAGE" in r["flags"]


def test_private_or_wrong_schema_fail_closed() -> None:
    payload = capture()
    payload["credentials_used"] = True
    try:
        m.build_lens(payload)
    except ValueError as exc:
        assert "public and credential-free" in str(exc)
    else:
        raise AssertionError("private capture accepted")
    payload = capture()
    payload["schema"] = "wrong"
    try:
        m.build_lens(payload)
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("wrong schema accepted")


def test_capture_timestamp_must_be_timezone_aware() -> None:
    payload = capture()
    payload["captured_at"] = "2026-08-16T15:00:00"
    try:
        m.build_lens(payload)
    except ValueError as exc:
        assert "timezone" in str(exc)
    else:
        raise AssertionError("naive timestamp accepted")


def test_symbol_list_is_unique_nonempty_and_exactly_bound_to_books() -> None:
    payload = capture()
    payload["symbols"] = ["BTCUSDT", "BTCUSDT"]
    try:
        m.build_lens(payload)
    except ValueError as exc:
        assert "duplicate symbol" in str(exc)
    else:
        raise AssertionError("duplicate symbol accepted")

    payload = capture()
    payload["symbols"] = []
    try:
        m.build_lens(payload)
    except ValueError as exc:
        assert "non-empty" in str(exc)
    else:
        raise AssertionError("empty symbols accepted")

    payload = capture()
    payload["books"]["SOLUSDT"] = {"snapshot": book()}
    try:
        m.build_lens(payload)
    except ValueError as exc:
        assert "exactly match" in str(exc)
    else:
        raise AssertionError("unbound extra book accepted")


def test_order_book_requires_strict_sorted_unique_prices() -> None:
    bad = book()
    bad["bids"][1][0] = bad["bids"][0][0]
    try:
        m.analyze_book("BTCUSDT", bad)
    except ValueError as exc:
        assert "duplicate price" in str(exc)
    else:
        raise AssertionError("duplicate bid price accepted")

    bad = book()
    bad["asks"][1][0] = "99.00"
    try:
        m.analyze_book("BTCUSDT", bad)
    except ValueError as exc:
        assert "strictly ascending" in str(exc) or "crossed" in str(exc)
    else:
        raise AssertionError("unsorted asks accepted")


def test_crossed_short_and_nonfinite_books_fail_closed() -> None:
    crossed = book()
    crossed["bids"][0][0] = "101"
    crossed["asks"][0][0] = "100"
    try:
        m.analyze_book("BTCUSDT", crossed)
    except ValueError as exc:
        assert "crossed" in str(exc)
    else:
        raise AssertionError("crossed book accepted")

    short = book(levels=4)
    try:
        m.analyze_book("BTCUSDT", short)
    except ValueError as exc:
        assert "at least" in str(exc)
    else:
        raise AssertionError("short book accepted")

    bad = book()
    bad["bids"][0][1] = "nan"
    try:
        m.analyze_book("BTCUSDT", bad)
    except ValueError as exc:
        assert "non-positive/non-finite" in str(exc)
    else:
        raise AssertionError("NaN accepted")


def test_capture_fingerprint_is_key_order_stable_and_input_sensitive() -> None:
    a = capture()
    b = json.loads(json.dumps(a, sort_keys=True))
    ra, rb = m.build_lens(a), m.build_lens(b)
    assert ra["provenance"]["capture_sha256"] == rb["provenance"]["capture_sha256"]
    c = capture()
    c["books"]["BTCUSDT"]["snapshot"]["bids"][0][1] = "2.0"
    rc = m.build_lens(c)
    assert rc["provenance"]["capture_sha256"] != ra["provenance"]["capture_sha256"]


def test_renderer_rejects_unsafe_report_and_repeats_boundary() -> None:
    sys.path.insert(0, str(TOOLS))
    render_path = TOOLS / "tradingos_liquidity_lens.py"
    r_spec = importlib.util.spec_from_file_location("liq_render", render_path)
    assert r_spec and r_spec.loader
    renderer = importlib.util.module_from_spec(r_spec)
    r_spec.loader.exec_module(renderer)
    report = m.build_lens(capture())
    page = renderer.render_html(report)
    assert "visible book snapshot, not liquidation map" in page
    assert "overall directional state requires complete coverage of all bands" in page
    assert "can_trade=false" in page
    assert "capital_permission=DENY" in page
    report["safety"]["can_trade"] = True
    try:
        renderer.render_html(report)
    except ValueError as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("unsafe report rendered")


def test_cli_generates_json_html_without_network_dependency(tmp_path: Path) -> None:
    inp = tmp_path / "capture.json"
    out = tmp_path / "out"
    inp.write_text(json.dumps(capture()), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(TOOLS / "tradingos_liquidity_lens.py"), "--input", str(inp), "--out-dir", str(out)],
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(TOOLS)},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads((out / "liquidity_lens.json").read_text(encoding="utf-8"))
    assert payload["schema"] == m.SCHEMA
    assert payload["version"] == "1.1.0"
    assert payload["safety"]["can_trade"] is False
    assert (out / "liquidity_lens.html").exists()



def test_capture_source_limit_symbol_format_and_update_id_are_strict() -> None:
    payload = capture()
    payload["source"] = "other"
    try:
        m.build_lens(payload)
    except ValueError as exc:
        assert "source" in str(exc)
    else:
        raise AssertionError("wrong capture source accepted")

    payload = capture()
    payload["limit"] = 42
    try:
        m.build_lens(payload)
    except ValueError as exc:
        assert "limit" in str(exc)
    else:
        raise AssertionError("unsupported capture limit accepted")

    payload = capture()
    payload["books"]["btc<script>"] = payload["books"].pop("BTCUSDT")
    payload["symbols"][0] = "btc<script>"
    try:
        m.build_lens(payload)
    except ValueError as exc:
        assert "symbol format" in str(exc)
    else:
        raise AssertionError("malformed symbol accepted")

    payload = capture()
    payload["books"]["BTCUSDT"]["snapshot"]["lastUpdateId"] = -1
    try:
        m.build_lens(payload)
    except ValueError as exc:
        assert "lastUpdateId" in str(exc)
    else:
        raise AssertionError("negative update id accepted")

def test_candidate_has_no_network_send_exchange_or_execution_imports() -> None:
    text = (CORE.read_text(encoding="utf-8") + "\n" + (TOOLS / "tradingos_liquidity_lens.py").read_text(encoding="utf-8")).lower()
    forbidden = ["urllib", "requests", "httpx", "socket", "telegram", "webhook", "exchange", "create_order", "place_order", "send_message"]
    assert all(token not in text for token in forbidden)
