from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "tradingos_liquidity_capture.py"
spec = importlib.util.spec_from_file_location("liq_capture", PATH)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def test_capture_is_public_and_bounded() -> None:
    seen=[]
    def fake(url: str):
        seen.append(url); return {"lastUpdateId": 1, "bids": [["1","1"]]*5, "asks": [["2","1"]]*5}
    payload=m.capture(["BTCUSDT","ETHUSDT"],100,fake,datetime(2026,8,9,17,0,tzinfo=timezone.utc))
    assert payload["schema"]==m.SCHEMA
    assert payload["credentials_used"] is False and payload["private_api_used"] is False
    assert len(seen)==2
    assert all(url.startswith(m.FAPI+"/fapi/v1/depth?") for url in seen)
    assert all("signature" not in url and "apiKey" not in url for url in seen)


def test_capture_rejects_bad_symbol_and_limit() -> None:
    for symbols,limit in [(["BTCUSD"],100),(["BTCUSDT"],7)]:
        try:
            m.capture(symbols,limit,lambda _: {})
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe capture contract accepted")
