from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "autostart" / "Run-CrossVenueMicrostructureBookLoop.ps1"
LAUNCHER = ROOT / "ops" / "autostart" / "Start-CrossVenueMicrostructureBookLoop.ps1"


def test_book_loop_uses_unique_cycle_capture_and_nonfatal_spill() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "function Move-CycleCaptureToLog" in source
    assert '[System.IO.FileShare]::ReadWrite' in source
    assert 'microstructure_book_loop_stdout.$PID.$CaptureId.tmp' in source
    assert 'microstructure_book_loop_stderr.$PID.$CaptureId.tmp' in source
    assert "capture_spilled_after_append_contention" in source
    assert "stdout_log_decision" in source
    assert "stderr_log_decision" in source
    assert '$ErrorActionPreference = "Continue"' in source
    assert '$ErrorActionPreference = $PreviousCycleErrorActionPreference' in source
    assert '"book_cycle_failed"' in source
    assert 'shared_status_preserved = $true' in source
    assert "@PythonArgs >> $StdoutPath" not in source


def test_book_loop_powershell_syntax_is_valid() -> None:
    command = (
        "$errors=$null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{SCRIPT}', [ref]$null, [ref]$errors); "
        "if($errors.Count -gt 0){$errors|ForEach-Object{$_.Message};exit 1}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_book_launcher_preserves_process_level_diagnostics() -> None:
    source = LAUNCHER.read_text(encoding="utf-8-sig")

    assert "microstructure_book_process_stdout.log" in source
    assert "microstructure_book_process_stderr.log" in source
    assert "-StdoutPath $ProcessStdoutPath" in source
    assert "-StderrPath $ProcessStderrPath" in source
