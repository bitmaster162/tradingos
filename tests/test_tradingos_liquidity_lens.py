from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "tradingos_liquidity_lens_core.py"
spec = importlib.util.spec_from_file_location("liq_lens_core", PATH)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def book(bid_scale=1.0, ask_scale=1.0, bid_wall=False, ask_wall=False):
    mid=100.0
    bids=[]; asks=[]
    for i in range(1,31):
        bp=mid-i*0.02; ap=mid+i*0.02
        bq=1.0*bid_scale; aq=1.0*ask_scale
        if bid_wall and i==3: bq=6.0*bid_scale
        if ask_wall and i==4: aq=7.0*ask_scale
        bids.append([f"{bp:.2f}",f"{bq:.4f}"])
        asks.append([f"{ap:.2f}",f"{aq:.4f}"])
    return {"lastUpdateId":123,"bids":bids,"asks":asks}


def capture(btc,eth=None):
    eth=eth or book()
    return {"schema":m.CAPTURE_SCHEMA,"captured_at":"2026-08-09T17:00:00Z","symbols":["BTCUSDT","ETHUSDT"],"credentials_used":False,"private_api_used":False,"books":{"BTCUSDT":{"snapshot":btc},"ETHUSDT":{"snapshot":eth}}}


def test_balanced_book_and_render_contract() -> None:
    report=m.build_lens(capture(book()))
    btc=next(x for x in report["matrix"] if x["symbol"]=="BTCUSDT")
    assert btc["state"]=="BALANCED"
    assert abs(btc["composite_imbalance"])<0.02
    assert report["safety"]["liquidation_map"] is False
    render_path=ROOT / "tools" / "tradingos_liquidity_lens.py"
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    r_spec=importlib.util.spec_from_file_location("liq_render",render_path); assert r_spec and r_spec.loader
    renderer=importlib.util.module_from_spec(r_spec); r_spec.loader.exec_module(renderer)
    page=renderer.render_html(report)
    assert "visible book snapshot, not liquidation map" in page
    assert "can_trade=false" in page


def test_bid_heavy_and_ask_heavy_classification() -> None:
    report=m.build_lens(capture(book(bid_scale=3,ask_scale=1),book(bid_scale=1,ask_scale=3)))
    states={x["symbol"]:x["state"] for x in report["matrix"]}
    assert states["BTCUSDT"]=="BID_HEAVY"
    assert states["ETHUSDT"]=="ASK_HEAVY"


def test_wall_detection_finds_nearest_side_walls() -> None:
    row=m.analyze_book("BTCUSDT",book(bid_wall=True,ask_wall=True))
    assert row["nearest_bid_wall"] is not None
    assert row["nearest_ask_wall"] is not None
    assert row["nearest_bid_wall"]["multiple_of_median"]>=3
    assert row["nearest_ask_wall"]["multiple_of_median"]>=3
    assert "NEAR_BID_WALL" in row["flags"]
    assert "NEAR_ASK_WALL" in row["flags"]


def test_crossed_malformed_and_private_capture_fail_closed() -> None:
    crossed=book(); crossed["bids"][0][0]="101"; crossed["asks"][0][0]="100"
    try:
        m.analyze_book("BTCUSDT",crossed)
    except ValueError as exc:
        assert "crossed" in str(exc)
    else:
        raise AssertionError("crossed book accepted")
    malformed=book(); malformed["bids"]=malformed["bids"][:2]
    try:
        m.analyze_book("BTCUSDT",malformed)
    except ValueError as exc:
        assert "at least" in str(exc)
    else:
        raise AssertionError("short book accepted")
    private=capture(book()); private["credentials_used"]=True
    try:
        m.build_lens(private)
    except ValueError as exc:
        assert "public and credential-free" in str(exc)
    else:
        raise AssertionError("private capture accepted")

def test_incomplete_band_coverage_does_not_claim_imbalance_state() -> None:
    tiny=book()
    tiny["bids"]=tiny["bids"][:5]
    tiny["asks"]=tiny["asks"][:5]
    row=m.analyze_book("BTCUSDT",tiny)
    assert row["quality"]=="PARTIAL"
    assert row["state"]=="INSUFFICIENT_DEPTH_COVERAGE"
    assert row["composite_imbalance"] is None
    assert row["complete_bands_bps"]==[]
    assert "INSUFFICIENT_DEPTH_COVERAGE" in row["flags"]
