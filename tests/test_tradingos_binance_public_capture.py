from __future__ import annotations
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[1]
MOD=ROOT/"tools"/"tradingos_binance_public_capture.py"
spec=importlib.util.spec_from_file_location("capture",MOD); assert spec and spec.loader
capture_mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(capture_mod)


def test_capture_uses_only_frozen_public_binance_hosts_and_no_credentials():
    seen=[]
    def fake(url):
        seen.append(url)
        return []
    out=capture_mod.capture(fake, datetime(2026,8,9,16,5,tzinfo=timezone.utc))
    assert out["credentials_used"] is False and out["private_api_used"] is False
    assert len(seen)==8
    assert {urlparse(u).netloc for u in seen} <= {"fapi.binance.com","api.binance.com"}
    assert all("apiKey" not in u and "signature" not in u for u in seen)
