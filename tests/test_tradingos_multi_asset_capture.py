from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "tradingos_multi_asset_capture.py"
spec = importlib.util.spec_from_file_location("multi_capture", PATH)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def test_capture_is_public_multi_asset_and_uses_expected_endpoints() -> None:
    seen: list[str] = []
    def fake(url: str):
        seen.append(url); return {"url": url}
    payload = m.capture(symbols=["BTCUSDT", "ETHUSDT"], fetch_json=fake, now=datetime(2026,8,9,16,0,tzinfo=timezone.utc))
    assert payload["schema"] == m.SCHEMA
    assert payload["credentials_used"] is False and payload["private_api_used"] is False
    assert payload["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert len(seen) == 20
    assert all(url.startswith((m.FAPI, m.SPOT)) for url in seen)
    assert all("apiKey" not in url and "signature" not in url for url in seen)
    assert set(payload["assets"]["BTCUSDT"]["futures_klines"]) == {"1h","4h","1d"}


def test_capture_rejects_non_usdt_symbol_and_wrong_intervals() -> None:
    try:
        m.capture(symbols=["BTCUSD"], fetch_json=lambda _: {})
    except ValueError as exc:
        assert "unsupported symbol" in str(exc)
    else:
        raise AssertionError("non-USDT symbol accepted")
    try:
        m.capture(intervals=["4h"], fetch_json=lambda _: {})
    except ValueError as exc:
        assert "requires exactly" in str(exc)
    else:
        raise AssertionError("partial interval contract accepted")
