from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "ops" / "autostart" / "Run-BitunixWO105V3ForwardLoop.ps1"


def test_native_tool_stderr_is_captured_without_terminating_the_lifecycle() -> None:
    source = CORE.read_text(encoding="utf-8-sig")
    helper = source.split("function Invoke-PublicTool", 1)[1].split("function Write-Milestone", 1)[0]

    assert '$PreviousErrorActionPreference = $ErrorActionPreference' in helper
    assert '$ErrorActionPreference = "Continue"' in helper
    assert '$ErrorActionPreference = $PreviousErrorActionPreference' in helper
    assert "$ExitCode" in helper
    assert "return $ExitCode" in helper
