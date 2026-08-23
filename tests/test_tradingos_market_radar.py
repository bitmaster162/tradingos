from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
PATH = TOOLS / "tradingos_market_radar.py"
spec = importlib.util.spec_from_file_location("radar", PATH)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NOW = datetime(2026, 8, 16, 17, 10, tzinfo=timezone.utc)
HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64


def watch_row(symbol="BTCUSDT", bias="WATCH_LONG", attention=70.0, conflict=None, confluence=None):
    if confluence is None:
        confluence = 6 if bias == "WATCH_LONG" else -6 if bias == "WATCH_SHORT" else 0
    state = "LONG" if bias == "WATCH_LONG" else "SHORT" if bias == "WATCH_SHORT" else "NEUTRAL"
    return {
        "symbol": symbol,
        "bias": bias,
        "attention_score": attention,
        "weighted_confluence": confluence,
        "conflict": conflict,
        "timeframes": {tf: {"state": state, "score": 2.5 if state == "LONG" else -2.5 if state == "SHORT" else 0.0} for tf in ("1h", "4h", "1d")},
        "can_trade": False,
    }


def watch(rows=None, at=NOW):
    rows = rows or [watch_row()]
    return {
        "schema": m.WATCHTOWER_SCHEMA,
        "version": m.WATCHTOWER_VERSION,
        "captured_at": at.isoformat().replace("+00:00", "Z"),
        "symbols": [row["symbol"] for row in rows],
        "matrix": rows,
        "cross_asset": {"state": "ALIGNED", "watch_long": [], "watch_short": [], "top_attention": rows[0]["symbol"]},
        "provenance": {"producer": "tools/tradingos_watchtower.py", "producer_sha256": m.EXPECTED_WATCHTOWER_PRODUCER_SHA256, "capture_sha256": HEX_B, "contract": "x"},
        "safety": dict(m.WATCHTOWER_SAFETY),
    }


def liq_row(symbol="BTCUSDT", state="BALANCED", quality="PASS", flags=None, attention=30.0):
    return {
        "symbol": symbol,
        "last_update_id": 123,
        "quality": quality,
        "mid": 100.0,
        "best_bid": 99.99,
        "best_ask": 100.01,
        "spread_bps": 2.0,
        "depth_bands_bps": [],
        "book_coverage_bps": {"bid": 50.0, "ask": 50.0},
        "complete_bands_bps": [10, 25, 50] if quality == "PASS" else [10],
        "composite_imbalance": 0.0 if quality == "PASS" else None,
        "state": state,
        "nearest_bid_wall": None,
        "nearest_ask_wall": None,
        "bid_wall_count": 0,
        "ask_wall_count": 0,
        "flags": flags or [],
        "attention_score": attention,
        "interpretation": "visible only",
        "can_trade": False,
    }


def liq(rows=None, at=None):
    rows = rows or [liq_row()]
    at = at or NOW + timedelta(seconds=1)
    return {
        "schema": m.LIQUIDITY_SCHEMA,
        "version": m.LIQUIDITY_VERSION,
        "captured_at": at.isoformat().replace("+00:00", "Z"),
        "matrix": rows,
        "top_attention": rows[0]["symbol"],
        "provenance": {"producer": "tools/tradingos_liquidity_lens_core.py", "producer_sha256": m.EXPECTED_LIQUIDITY_PRODUCER_SHA256, "capture_sha256": HEX_C, "books_exactly_bound_to_symbols": True, "timestamp_timezone_required": True},
        "contract": {},
        "safety": dict(m.LIQUIDITY_SAFETY),
    }


def test_aligned_inputs_build_deterministic_radar_and_bind_provenance() -> None:
    w, l = watch(), liq()
    a = m.build_radar(w, l)
    b = m.build_radar(deepcopy(w), deepcopy(l))
    assert a == b
    assert a["version"] == "1.1.0" and a["capture_skew_seconds"] == 1.0
    assert a["provenance"]["watchtower_report_sha256"] == m.stable_sha256(w)
    assert a["provenance"]["liquidity_report_sha256"] == m.stable_sha256(l)
    assert a["safety"] == m.RADAR_SAFETY


def test_liquidity_cannot_create_directional_bias() -> None:
    w = watch([watch_row(bias="NO_ACTION", confluence=0)])
    r = m.build_radar(w, liq([liq_row(state="BID_HEAVY")]))["matrix"][0]
    assert r["bias"] == "NO_ACTION" and r["decision_quality"] == "NO_ACTION"


def test_opposing_pass_liquidity_adds_caution_veto_only() -> None:
    r = m.build_radar(watch(), liq([liq_row(state="ASK_HEAVY")]))["matrix"][0]
    assert r["bias"] == "WATCH_LONG"
    assert r["decision_quality"] == "CAUTION"
    assert "MICROSTRUCTURE_OPPOSES_LONG" in r["vetoes"]


def test_partial_liquidity_cannot_modify_priority_or_vetoes() -> None:
    w = watch([watch_row(attention=73)])
    l = liq([liq_row(state="INSUFFICIENT_DEPTH_COVERAGE", quality="PARTIAL", flags=["NEAR_ASK_WALL"], attention=99)])
    r = m.build_radar(w, l)["matrix"][0]
    assert r["priority_score"] == 73
    assert r["vetoes"] == []
    assert r["decision_quality"] == "CONTEXT_PARTIAL"
    assert r["notes"] == ["LIQUIDITY_CONTEXT_PARTIAL"]


def test_pass_near_wall_becomes_directional_friction() -> None:
    r = m.build_radar(watch(), liq([liq_row(flags=["NEAR_ASK_WALL"])]))["matrix"][0]
    assert "NEAR_ASK_WALL_FRICTION" in r["vetoes"]
    assert r["decision_quality"] == "CAUTION"


def test_exact_contract_versions_required() -> None:
    w = watch(); w["version"] = "1.0.0"
    try: m.build_radar(w, liq())
    except ValueError as exc: assert "watchtower contract" in str(exc)
    else: raise AssertionError("old watchtower accepted")
    l = liq(); l["version"] = "1.0.0"
    try: m.build_radar(watch(), l)
    except ValueError as exc: assert "liquidity contract" in str(exc)
    else: raise AssertionError("old liquidity accepted")


def test_exact_safety_contracts_required() -> None:
    w = watch(); w["safety"]["can_trade"] = True
    try: m.build_radar(w, liq())
    except ValueError as exc: assert "unsafe" in str(exc)
    else: raise AssertionError("unsafe watchtower accepted")
    l = liq(); l["safety"]["orders_allowed"] = True
    try: m.build_radar(watch(), l)
    except ValueError as exc: assert "unsafe" in str(exc)
    else: raise AssertionError("unsafe liquidity accepted")


def test_symbol_sets_must_exactly_match() -> None:
    l = liq([liq_row(), liq_row("ETHUSDT")])
    try: m.build_radar(watch(), l)
    except ValueError as exc: assert "symbol sets" in str(exc)
    else: raise AssertionError("extra liquidity symbol accepted")


def test_watchtower_symbols_must_bind_matrix_order() -> None:
    w = watch([watch_row("BTCUSDT"), watch_row("ETHUSDT")]); w["symbols"] = ["ETHUSDT", "BTCUSDT"]
    l = liq([liq_row("BTCUSDT"), liq_row("ETHUSDT")])
    try: m.build_radar(w, l)
    except ValueError as exc: assert "matrix order" in str(exc)
    else: raise AssertionError("watchtower identity drift accepted")


def test_capture_skew_is_bounded_and_timezone_required() -> None:
    try: m.build_radar(watch(), liq(at=NOW + timedelta(seconds=m.MAX_CAPTURE_SKEW_SECONDS + 1)))
    except ValueError as exc: assert "skew" in str(exc)
    else: raise AssertionError("stale pair accepted")
    w = watch(); w["captured_at"] = "2026-08-16T17:10:00"
    try: m.build_radar(w, liq())
    except ValueError as exc: assert "timezone" in str(exc)
    else: raise AssertionError("naive timestamp accepted")


def test_watchtower_bias_confluence_and_timeframe_semantics_are_strict() -> None:
    w = watch([watch_row(bias="WATCH_LONG", confluence=2)])
    try: m.build_radar(w, liq())
    except ValueError as exc: assert "WATCH_LONG" in str(exc)
    else: raise AssertionError("inconsistent watch bias accepted")
    w = watch(); w["matrix"][0]["timeframes"]["4h"]["state"] = "BUY"
    try: m.build_radar(w, liq())
    except ValueError as exc: assert "timeframe state" in str(exc)
    else: raise AssertionError("invalid timeframe state accepted")


def test_priority_inputs_reject_nan_inf_and_out_of_range() -> None:
    w = watch(); w["matrix"][0]["attention_score"] = float("nan")
    try: m.build_radar(w, liq())
    except ValueError as exc: assert "non-finite" in str(exc)
    else: raise AssertionError("nan watch attention accepted")
    l = liq(); l["matrix"][0]["attention_score"] = 101
    try: m.build_radar(watch(), l)
    except ValueError as exc: assert "out-of-range" in str(exc)
    else: raise AssertionError("out-of-range liquidity attention accepted")


def test_liquidity_quality_state_contract_fails_closed() -> None:
    l = liq([liq_row(state="BID_HEAVY", quality="PARTIAL")])
    try: m.build_radar(watch(), l)
    except ValueError as exc: assert "PARTIAL" in str(exc)
    else: raise AssertionError("partial directional state accepted")
    l = liq([liq_row(state="INSUFFICIENT_DEPTH_COVERAGE", quality="PASS")])
    try: m.build_radar(watch(), l)
    except ValueError as exc: assert "PASS" in str(exc)
    else: raise AssertionError("PASS insufficient state accepted")


def test_watchtower_producer_sha256_is_bound_to_canonical_source_bytes() -> None:
    expected = hashlib.sha256((TOOLS / "tradingos_watchtower.py").read_bytes()).hexdigest()
    assert m.EXPECTED_WATCHTOWER_PRODUCER_SHA256 == expected
    bad = watch(); bad["provenance"]["producer_sha256"] = HEX_A
    try: m.build_radar(bad, liq())
    except ValueError as exc: assert "producer sha256 mismatch" in str(exc)
    else: raise AssertionError("well-formed non-canonical producer sha accepted")


def test_liquidity_producer_sha256_is_bound_to_canonical_source_bytes() -> None:
    expected = hashlib.sha256((TOOLS / "tradingos_liquidity_lens_core.py").read_bytes()).hexdigest()
    assert m.EXPECTED_LIQUIDITY_PRODUCER_SHA256 == expected

    bad = liq(); bad["provenance"].pop("producer")
    try: m.build_radar(watch(), bad)
    except ValueError as exc: assert "liquidity producer mismatch" in str(exc)
    else: raise AssertionError("missing liquidity producer accepted")

    bad = liq(); bad["provenance"]["producer_sha256"] = HEX_A
    try: m.build_radar(watch(), bad)
    except ValueError as exc: assert "liquidity producer sha256 mismatch" in str(exc)
    else: raise AssertionError("well-formed non-canonical liquidity producer sha accepted")


def test_provenance_fingerprints_are_required_and_input_sensitive() -> None:
    w, l = watch(), liq()
    a = m.build_radar(w, l)
    w2 = deepcopy(w); w2["matrix"][0]["attention_score"] = 71
    b = m.build_radar(w2, l)
    assert a["provenance"]["watchtower_report_sha256"] != b["provenance"]["watchtower_report_sha256"]
    bad = watch(); bad["provenance"]["capture_sha256"] = "bad"
    try: m.build_radar(bad, liq())
    except ValueError as exc: assert "sha256" in str(exc)
    else: raise AssertionError("bad provenance sha accepted")


def test_renderer_rejects_safety_identity_and_provenance_drift() -> None:
    report = m.build_radar(watch(), liq())
    page = m.render_html(report)
    assert "Liquidity may add friction/veto context but cannot create a directional bias." in page
    assert "partial liquidity cannot change priority or vetoes" in page
    assert "network_fetch=false" in page and "can_trade=false" in page
    bad = deepcopy(report); bad["safety"]["can_trade"] = True
    try: m.render_html(bad)
    except ValueError as exc: assert "unsafe" in str(exc)
    else: raise AssertionError("unsafe report rendered")
    bad = deepcopy(report); bad["top_priority"] = "ETHUSDT"
    try: m.render_html(bad)
    except ValueError as exc: assert "top_priority" in str(exc)
    else: raise AssertionError("identity drift rendered")


def test_cli_generates_json_and_html_without_network(tmp_path: Path) -> None:
    wp, lp, out = tmp_path / "watch.json", tmp_path / "liq.json", tmp_path / "out"
    wp.write_text(json.dumps(watch()), encoding="utf-8")
    lp.write_text(json.dumps(liq()), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(PATH), "--watchtower", str(wp), "--liquidity", str(lp), "--out-dir", str(out)], text=True, capture_output=True, env={**os.environ, "PYTHONPATH": str(TOOLS)}, check=False)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads((out / "market_radar.json").read_text(encoding="utf-8"))
    assert payload["schema"] == m.SCHEMA and payload["version"] == "1.1.0"
    assert payload["safety"]["network_fetch"] is False
    assert (out / "market_radar.html").exists()


def test_static_module_boundary_has_no_network_or_execution_imports() -> None:
    text = PATH.read_text(encoding="utf-8")
    forbidden = ("import requests", "import urllib", "import socket", "import httpx", "import ccxt", "subprocess", "websocket", "aiohttp", "telegram", "binance")
    assert all(token not in text.lower() for token in forbidden)
