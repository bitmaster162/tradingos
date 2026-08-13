from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "tradingos_watchtower.py"
spec = importlib.util.spec_from_file_location("watchtower", PATH)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

NOW = datetime(2026,8,9,16,30,tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp()*1000)


def rows(direction: int, interval_ms: int, count: int = 50, base: float = 100.0):
    out=[]
    for i in range(count):
        open_ms = NOW_MS - (count-i)*interval_ms
        close_ms = open_ms + interval_ms - 1
        mid = base + direction*i*0.7
        o = mid - direction*0.2; c = mid + direction*0.2
        h=max(o,c)+0.5; lo=min(o,c)-0.5
        vol=100+i
        buy = vol*0.65 if direction>0 else vol*0.35 if direction<0 else vol*0.5
        out.append([open_ms,str(o),str(h),str(lo),str(c),str(vol),close_ms,"0",100,str(buy),"0","0"])
    return out


def asset(direction_map: dict[str,int], symbol: str):
    r4=rows(direction_map["4h"],4*3600_000,50,100 if symbol=="BTCUSDT" else 50)
    spot=rows(direction_map["4h"],4*3600_000,30,100 if symbol=="BTCUSDT" else 50)
    premium=rows(0,4*3600_000,30,0.0002)
    for i,r in enumerate(premium): r[4]=str(0.0001 + i*0.000001)
    return {
        "futures_24h":{"priceChangePercent":"1.0"},
        "mark_price":{"markPrice":"130","indexPrice":"129.9","lastFundingRate":"0.0001","time":NOW_MS},
        "open_interest":{"openInterest":"1010","time":NOW_MS},
        "open_interest_stats_4h":[{"sumOpenInterest":"1000","timestamp":NOW_MS-4*3600_000}],
        "funding_history":[{"fundingRate":str(0.00005+i*0.000001)} for i in range(30)],
        "premium_index_4h":premium,
        "spot_klines_4h":spot,
        "futures_klines":{
            "1h":rows(direction_map["1h"],3600_000,50,100 if symbol=="BTCUSDT" else 50),
            "4h":r4,
            "1d":rows(direction_map["1d"],24*3600_000,50,100 if symbol=="BTCUSDT" else 50),
        },
    }


def capture(btc: dict[str,int], eth: dict[str,int]):
    return {"schema":m.CAPTURE_SCHEMA,"captured_at":NOW.isoformat().replace("+00:00","Z"),"symbols":["BTCUSDT","ETHUSDT"],"intervals":["1h","4h","1d"],"credentials_used":False,"private_api_used":False,"assets":{"BTCUSDT":asset(btc,"BTCUSDT"),"ETHUSDT":asset(eth,"ETHUSDT")}}


def by_symbol(report, symbol):
    return next(a for a in report["matrix"] if a["symbol"]==symbol)


def test_aligned_bull_and_bear_rank_into_watch_biases() -> None:
    report=m.build_watchtower(capture({"1h":1,"4h":1,"1d":1},{"1h":-1,"4h":-1,"1d":-1}))
    btc,eth=by_symbol(report,"BTCUSDT"),by_symbol(report,"ETHUSDT")
    assert btc["bias"]=="WATCH_LONG" and btc["weighted_confluence"]==6
    assert eth["bias"]=="WATCH_SHORT" and eth["weighted_confluence"]==-6
    assert report["cross_asset"]["state"]=="DIVERGENT"
    assert report["safety"]["can_trade"] is False


def test_htf_ltf_conflict_fails_closed_to_no_action() -> None:
    report=m.build_watchtower(capture({"1h":-1,"4h":-1,"1d":1},{"1h":1,"4h":1,"1d":1}))
    btc=by_symbol(report,"BTCUSDT")
    assert btc["conflict"]=="HTF_LTF_CONFLICT"
    assert btc["bias"]=="NO_ACTION"
    assert btc["clarity"]=="CONFLICT"


def test_urgency_is_not_execution_permission_and_html_is_matrix() -> None:
    report=m.build_watchtower(capture({"1h":1,"4h":1,"1d":1},{"1h":1,"4h":1,"1d":1}))
    assert all(a["can_trade"] is False for a in report["matrix"])
    page=m.render_html(report)
    assert "MULTI-ASSET WATCHTOWER" in page
    assert "BTCUSDT" in page and "ETHUSDT" in page
    assert "urgency ≠ trade quality" in page
    assert "can_trade=false" in page


def test_private_capture_is_rejected() -> None:
    payload=capture({"1h":1,"4h":1,"1d":1},{"1h":1,"4h":1,"1d":1}); payload["credentials_used"]=True
    try:
        m.build_watchtower(payload)
    except ValueError as exc:
        assert "credential-free" in str(exc)
    else:
        raise AssertionError("unsafe capture accepted")
