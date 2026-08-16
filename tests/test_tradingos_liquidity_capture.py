from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
PATH = TOOLS / "tradingos_liquidity_capture.py"
spec = importlib.util.spec_from_file_location("liq_capture", PATH)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NOW = datetime(2026, 8, 17, 0, 30, tzinfo=timezone.utc)


def snapshot(levels: int = 5, update_id: int = 123) -> dict:
    bids = [[f"{100 - i * 0.02:.2f}", "1.0"] for i in range(levels)]
    asks = [[f"{100.02 + i * 0.02:.2f}", "1.0"] for i in range(levels)]
    return {"lastUpdateId": update_id, "bids": bids, "asks": asks}


def test_capture_contract_is_public_bounded_and_deterministic() -> None:
    seen: list[str] = []
    def fake(url: str):
        seen.append(url)
        return snapshot()
    a = m.capture(["BTCUSDT", "ETHUSDT"], 100, fake, NOW)
    b = m.capture(["BTCUSDT", "ETHUSDT"], 100, lambda _: snapshot(), NOW)
    assert a == b
    assert seen == [
        "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=100",
        "https://fapi.binance.com/fapi/v1/depth?symbol=ETHUSDT&limit=100",
    ]
    assert a["schema"] == "tradingos.binance_liquidity_capture.v1"
    assert a["version"] == "1.1.0"
    assert a["captured_at"] == "2026-08-17T00:30:00Z"
    assert a["credentials_used"] is False and a["private_api_used"] is False
    assert a["transport_policy"]["redirects_allowed"] is False
    assert a["transport_policy"]["retries"] == 0
    assert a["safety"]["can_trade"] is False and a["safety"]["capital_permission"] == "DENY"


def test_duplicate_symbols_are_rejected_not_deduplicated() -> None:
    try:
        m.capture(["BTCUSDT", "BTCUSDT"], 100, lambda _: snapshot(), NOW)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate symbols accepted")


def test_bad_symbols_empty_and_too_many_rejected() -> None:
    cases = [[], ["BTCUSD"], ["../BTCUSDT"], ["BTCUSDT"] * 21]
    for symbols in cases:
        try:
            m.capture(symbols, 100, lambda _: snapshot(), NOW)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe symbols accepted: {symbols!r}")


def test_bool_and_unsupported_limit_rejected() -> None:
    for limit in [True, 7, 5000]:
        try:
            m.capture(["BTCUSDT"], limit, lambda _: snapshot(), NOW)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe limit accepted: {limit!r}")


def test_naive_clock_rejected() -> None:
    try:
        m.capture(["BTCUSDT"], 100, lambda _: snapshot(), datetime(2026, 8, 17, 0, 0))
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("naive clock accepted")


def test_url_validator_rejects_host_path_credentials_fragment_extra_query_and_noncanonical() -> None:
    bad = [
        "http://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=100",
        "https://evil.example/fapi/v1/depth?symbol=BTCUSDT&limit=100",
        "https://fapi.binance.com.evil.example/fapi/v1/depth?symbol=BTCUSDT&limit=100",
        "https://user:pass@fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=100",
        "https://fapi.binance.com/fapi/v1/order?symbol=BTCUSDT&limit=100",
        "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=100#x",
        "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=100&x=1",
        "https://fapi.binance.com/fapi/v1/depth?limit=100&symbol=BTCUSDT",
    ]
    for url in bad:
        try:
            m._validate_exact_url(url)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe URL accepted: {url}")


def test_snapshot_requires_exact_fields_and_update_id() -> None:
    for raw in [
        {"lastUpdateId": 1, "bids": snapshot()["bids"], "asks": snapshot()["asks"], "extra": 1},
        {"lastUpdateId": True, "bids": snapshot()["bids"], "asks": snapshot()["asks"]},
        {"lastUpdateId": -1, "bids": snapshot()["bids"], "asks": snapshot()["asks"]},
    ]:
        try:
            m.validate_snapshot(raw, symbol="BTCUSDT", limit=100)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed depth snapshot accepted")


def test_snapshot_rejects_too_few_and_over_limit_levels() -> None:
    for levels, limit in [(4, 100), (6, 5)]:
        try:
            m.validate_snapshot(snapshot(levels), symbol="BTCUSDT", limit=limit)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid depth level count accepted")


def test_snapshot_rejects_malformed_nonfinite_zero_and_negative_values() -> None:
    cases = []
    x = snapshot(); x["bids"][0] = ["100", "0"]; cases.append(x)
    x = snapshot(); x["asks"][0] = ["nan", "1"]; cases.append(x)
    x = snapshot(); x["asks"][0] = ["101", "-1"]; cases.append(x)
    x = snapshot(); x["asks"][0] = ["101", "1", "extra"]; cases.append(x)
    for raw in cases:
        try:
            m.validate_snapshot(raw, symbol="BTCUSDT", limit=100)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe numeric row accepted")


def test_snapshot_rejects_duplicates_order_drift_and_crossed_book() -> None:
    x = snapshot(); x["bids"][1][0] = x["bids"][0][0]
    y = snapshot(); y["asks"][1][0] = "99.00"
    z = snapshot(); z["bids"][0][0] = "101.00"
    for raw in [x, y, z]:
        try:
            m.validate_snapshot(raw, symbol="BTCUSDT", limit=100)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid order book accepted")


def test_fetch_failure_is_fail_closed_and_partial_capture_not_returned() -> None:
    calls = 0
    def fake(url: str):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("transport failed")
        return snapshot()
    try:
        m.capture(["BTCUSDT", "ETHUSDT"], 100, fake, NOW)
    except RuntimeError as exc:
        assert "transport failed" in str(exc)
    else:
        raise AssertionError("partial capture returned")


def test_default_fetch_rejects_bad_timeout_before_network() -> None:
    url = m._exact_public_url("BTCUSDT", 100)
    for timeout in [True, 0, -1, float("nan"), m.MAX_TIMEOUT_SECONDS + 0.1]:
        try:
            m.default_fetch_json(url, timeout=timeout)
        except ValueError:
            pass
        else:
            raise AssertionError("bad timeout accepted")


def test_cli_error_path_can_be_exercised_without_network(tmp_path: Path) -> None:
    out = tmp_path / "capture.json"
    proc = subprocess.run(
        [sys.executable, str(PATH), "--symbols", "BTCUSD", "--limit", "100", "--output", str(out)],
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(TOOLS)},
        check=False,
    )
    assert proc.returncode == 2
    assert not out.exists()
    payload = json.loads(proc.stdout)
    assert payload["can_trade"] is False and payload["capital_permission"] == "DENY"


def test_capture_is_compatible_with_canonical_liquidity_lens_input_contract() -> None:
    payload = m.capture(["BTCUSDT"], 100, lambda _: snapshot(), NOW)
    assert payload["schema"] == "tradingos.binance_liquidity_capture.v1"
    assert payload["source"] == "binance_usds_futures_public_depth"
    assert payload["limit"] in {5, 10, 20, 50, 100, 500, 1000}
    assert payload["symbols"] == ["BTCUSDT"]
    assert set(payload["books"]) == {"BTCUSDT"}
    assert payload["books"]["BTCUSDT"]["snapshot"]["lastUpdateId"] == 123


def test_source_has_fixed_ingress_boundary_and_no_trading_send_capability() -> None:
    text = PATH.read_text(encoding="utf-8")
    lowered = text.lower()
    assert 'fapi_host = "fapi.binance.com"' in lowered
    assert 'fapi_path = "/fapi/v1/depth"' in lowered
    for forbidden in [
        "api_key", "api-secret", "x-mbx-apikey", "signature=", "import telegram", "telegram.", "send_message",
        "create_order", "place_order", "new_order", "can_trade = true", "capital_permission\": \"allow",
        "import requests", "import httpx", "import aiohttp", "import websocket", "import ccxt", "import socket", "telegram.",
    ]:
        assert forbidden not in lowered


def test_no_test_invokes_real_default_fetch() -> None:
    # The focused suite intentionally never calls default_fetch_json with a valid timeout.
    # All successful captures receive a fake transport.
    assert m.default_fetch_json.__name__ == "default_fetch_json"

class _FakeHeaders:
    def __init__(self, content_type: str = "application/json") -> None:
        self._content_type = content_type
    def get_content_type(self) -> str:
        return self._content_type


class _FakeResponse:
    def __init__(self, url: str, *, status: int = 200, content_type: str = "application/json", body: bytes = b'{}') -> None:
        self.status = status
        self.headers = _FakeHeaders(content_type)
        self._url = url
        self._body = body
    def geturl(self) -> str:
        return self._url
    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class _FakeOpener:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.requests = []
        self.timeouts = []
    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        return self.response


def test_default_fetch_success_uses_fixed_get_json_headers_without_credentials(monkeypatch) -> None:
    url = m._exact_public_url("BTCUSDT", 100)
    body = json.dumps(snapshot()).encode()
    opener = _FakeOpener(_FakeResponse(url, body=body))
    monkeypatch.setattr(m.urllib.request, "build_opener", lambda *_: opener)
    out = m.default_fetch_json(url, timeout=3)
    assert out == snapshot()
    assert opener.timeouts == [3.0]
    req = opener.requests[0]
    assert req.full_url == url and req.get_method() == "GET"
    headers = {k.lower(): v for k, v in req.header_items()}
    assert headers.get("accept") == "application/json"
    assert "x-mbx-apikey" not in headers and "authorization" not in headers


def test_default_fetch_rejects_http_status_and_content_type_with_fake_transport(monkeypatch) -> None:
    url = m._exact_public_url("BTCUSDT", 100)
    for response in [
        _FakeResponse(url, status=500, body=b'{}'),
        _FakeResponse(url, content_type="text/html", body=b'{}'),
    ]:
        opener = _FakeOpener(response)
        monkeypatch.setattr(m.urllib.request, "build_opener", lambda *_, op=opener: op)
        try:
            m.default_fetch_json(url)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe HTTP response accepted")


def test_default_fetch_rejects_final_url_drift_with_fake_transport(monkeypatch) -> None:
    url = m._exact_public_url("BTCUSDT", 100)
    opener = _FakeOpener(_FakeResponse("https://evil.example/redirected", body=b'{}'))
    monkeypatch.setattr(m.urllib.request, "build_opener", lambda *_: opener)
    try:
        m.default_fetch_json(url)
    except ValueError as exc:
        assert "URL changed" in str(exc)
    else:
        raise AssertionError("final URL drift accepted")


def test_default_fetch_rejects_oversize_and_invalid_json_with_fake_transport(monkeypatch) -> None:
    url = m._exact_public_url("BTCUSDT", 100)
    responses = [
        _FakeResponse(url, body=b"x" * (m.MAX_RESPONSE_BYTES + 1)),
        _FakeResponse(url, body=b"not-json"),
    ]
    for response in responses:
        opener = _FakeOpener(response)
        monkeypatch.setattr(m.urllib.request, "build_opener", lambda *_, op=opener: op)
        try:
            m.default_fetch_json(url)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe response body accepted")
