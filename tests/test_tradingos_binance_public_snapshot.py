from __future__ import annotations
import importlib.util, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "tools" / "tradingos_binance_public_snapshot.py"
spec = importlib.util.spec_from_file_location("snapshot", MOD); assert spec and spec.loader
snapshot = importlib.util.module_from_spec(spec); spec.loader.exec_module(snapshot)


def fixture() -> dict:
    rows=[]; spot=[]; premium=[]
    start=1785873600000
    for i in range(30):
        o=64000+i*40; c=o+20; h=c+100; l=o-100; v=1000+i*10; buy=v*0.55
        rows.append([start+i*14400000,str(o),str(h),str(l),str(c),str(v),start+i*14400000+14399999,"0",1,str(buy),"0","0"])
        spot.append([start+i*14400000,str(o),str(h),str(l),str(c+5),str(v),start+i*14400000+14399999,"0",1,str(buy),"0","0"])
        premium.append([start+i*14400000,"-0.0004","-0.0002","-0.0006",str(-0.0004+i*0.000001),"0",start+i*14400000+14399999,"0",1,"0","0","0"])
    return {
        "schema":"tradingos.binance_public_capture.v1","symbol":"BTCUSDT","captured_at":"2026-08-09T16:05:00Z","credentials_used":False,"private_api_used":False,
        "futures_24h":{"priceChangePercent":"1.2"},
        "mark_price":{"markPrice":"65200","indexPrice":"65220","lastFundingRate":"0.00007","time":1786291500000},
        "open_interest":{"openInterest":"107000","time":1786291490000},
        "open_interest_stats_4h":[{"sumOpenInterest":"106000"}],
        "funding_history":[{"fundingRate":str(0.00002+i*0.000002)} for i in range(30)],
        "futures_klines_4h":rows,"spot_klines_4h":spot,"premium_index_4h":premium,
    }


def test_public_capture_builds_safe_snapshot():
    out=snapshot.build_snapshot(fixture())
    assert out["can_trade"] is False
    assert out["symbol"]=="BTCUSDT"
    assert out["data_quality"]["present_sources"]==["ohlcv","open_interest","funding","spot_flow"]
    assert out["flow"]["spot_cvd_direction"]=="up"


def test_private_or_credential_capture_is_rejected():
    payload=fixture(); payload["credentials_used"]=True
    try: snapshot.build_snapshot(payload)
    except ValueError as exc: assert "credential-free" in str(exc)
    else: raise AssertionError("unsafe capture accepted")
