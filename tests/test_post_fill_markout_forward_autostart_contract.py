import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_runtime_loop_is_singleton_demo_only_and_fail_closed() -> None:
    source = _read("ops/autostart/Run-PostFillMarkoutForwardLoop.ps1")

    assert "TradingOSRuntimeShutdownGate.ps1" in source
    assert "post_fill_markout_forward_loop.lock.json" in source
    assert "post_fill_markout_forward_loop_status.json" in source
    assert "tools\\post_fill_markout_forward_runtime.py" in source
    assert "POST_FILL_MARKOUT_FORWARD_PREREG_2026-07-14.json" in source
    assert '$env:BOT_ENV = "demo"' in source
    assert 'signed_read_endpoint_allowlist = @("/fapi/v1/userTrades")' in source
    assert 'orders_allowed = $false' in source
    assert 'can_trade = $false' in source
    assert "Stop-Process" not in source
    assert "taskkill" not in source.lower()


def test_explicit_start_wrapper_uses_verified_lifecycle_job() -> None:
    source = _read("ops/autostart/Start-PostFillMarkoutForwardLoop.ps1")

    assert 'ComponentId "post_fill_markout_forward"' in source
    assert "Get-TradingOSRuntimeLaunchDisposition" in source
    assert "Start-TradingOSRuntimeJobProcess" in source
    assert "Get-TradingOSRuntimeComponentLaunchConfirmation" in source
    assert "Undo-TradingOSRuntimeComponentLaunch" in source
    assert 'signed_read_endpoint_allowlist = @("/fapi/v1/userTrades")' in source
    assert 'orders_allowed = $false' in source
    assert 'can_trade = $false' in source


def test_component_is_required_and_owned_by_runtime_manifest() -> None:
    manifest = json.loads(_read("configs/TRADING_OS_RUNTIME_COMPONENTS.json"))
    matches = [item for item in manifest["components"] if item["id"] == "post_fill_markout_forward"]

    assert len(matches) == 1
    component = matches[0]
    assert component["script"] == "ops/autostart/Run-PostFillMarkoutForwardLoop.ps1"
    assert component["start_owner"] == "runtime"
    assert component["required"] is True
    assert component["lock_path"].endswith("post_fill_markout_forward_loop.lock.json")
    assert component["status_path"].endswith("post_fill_markout_forward_loop_status.json")


def test_main_runtime_launcher_wires_component_without_expanding_permissions() -> None:
    source = _read("ops/autostart/Start-TradingOSRuntime.ps1")

    assert "$PostFillMarkoutForwardPulseSeconds = 300" in source
    assert "Run-PostFillMarkoutForwardLoop.ps1" in source
    assert 'Test-ComponentStartBlocked -ComponentId "post_fill_markout_forward"' in source
    assert 'Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "post_fill_markout_forward"' in source
    assert 'post_fill_markout_forward_signed_read_endpoint_allowlist = @("/fapi/v1/userTrades")' in source
    assert "post_fill_markout_forward_orders_allowed = $false" in source
