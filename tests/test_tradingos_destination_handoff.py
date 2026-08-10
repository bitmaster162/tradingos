from __future__ import annotations
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PS1=ROOT/"scripts"/"Invoke-TradingOSDestinationIntake.ps1"
CMD=ROOT/"scripts"/"RUN_TRADINGOS_DESTINATION_INTAKE.cmd"

def test_wrapper_uses_process_only_temp_env_and_finally_cleanup():
    s=PS1.read_text(encoding="utf-8")
    assert "SetEnvironmentVariable($tempEnv, $rawDestination, 'Process')" in s
    assert "finally" in s and "SetEnvironmentVariable($tempEnv, $null, 'Process')" in s
    assert "$rawDestination = $null" in s

def test_wrapper_supports_interactive_and_update_json_modes():
    s=PS1.read_text(encoding="utf-8")
    assert "Read-Host 'Telegram chat id (kept only in this process)'" in s
    assert "--destination-value-env" in s and "--telegram-update-json" in s

def test_wrapper_verifies_hash_ready_and_safety_contracts():
    s=PS1.read_text(encoding="utf-8")
    for needle in ("HASH_READY","^[0-9a-f]{64}$","raw_destination_persisted","binding_apply_performed","network_call","deploy_permission"):
        assert needle in s

def test_wrapper_never_contains_secret_or_destination_examples():
    s=PS1.read_text(encoding="utf-8").lower()
    for needle in ("-1001234567890","synthetic-token","callback-secret","bot token","webhook"):
        assert needle not in s

def test_wrapper_has_no_network_commands():
    s=PS1.read_text(encoding="utf-8").lower()
    for needle in ("invoke-webrequest","invoke-restmethod","curl ","wget ","start-bitstransfer"):
        assert needle not in s

def test_wrapper_invokes_only_existing_r12_intake_tool():
    s=PS1.read_text(encoding="utf-8")
    assert "tools\\tradingos_destination_intake.py" in s
    assert "tradingos_binding_package.py" not in s

def test_cmd_launcher_is_thin_and_forwards_exit_code():
    s=CMD.read_text(encoding="utf-8").lower()
    assert "invoke-tradingosdestinationintake.ps1" in s and "powershell" in s
    assert "%errorlevel%" in s and "exit /b %rc%" in s
    assert "read-host" not in s and "telegram chat id" not in s and "bot token" not in s

def test_wrapper_does_not_apply_binding_or_change_deploy_permission():
    s=PS1.read_text(encoding="utf-8")
    assert "binding_apply_performed = $false" in s
    assert "network_call = $false" in s
    assert "deploy_permission = 'DENY'" in s
    assert "Set-Content" not in s and "Out-File" not in s
