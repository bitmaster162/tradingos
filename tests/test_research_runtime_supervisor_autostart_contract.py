from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOSTART = ROOT / "ops" / "autostart"


def read(name: str) -> str:
    return (AUTOSTART / name).read_text(encoding="utf-8-sig")


def test_launcher_is_idempotent_and_fail_closed() -> None:
    source = read("Start-ResearchRuntimeSupervisor.ps1")

    assert "TradingOSResearchRuntimeSupervisorLauncher" in source
    assert "blocked_duplicate_supervisor_processes" in source
    assert "blocked_preflight_failed" in source
    assert "Get-LogicalProcessRoots" in source
    assert "$Candidates = @(Get-LogicalProcessRoots" in source
    assert "Test-ProcessBelongsToRoot" in source
    assert "$MatchedCandidates += $StatusProcess" in source
    assert "$SupervisorScript run-once" in source
    assert "automatic_restart_allowed = $false" in source
    assert "process_stop_allowed = $false" in source
    assert "orders_allowed = $false" in source
    assert "can_trade = $false" in source
    assert "Stop-Process" not in source
    assert "taskkill" not in source.lower()


def test_runtime_start_wires_launcher_without_restart_permission() -> None:
    source = read("Start-TradingOSRuntime.ps1")

    assert "Start-ResearchRuntimeSupervisor.ps1" in source
    assert "ResearchRuntimeSupervisorSleepSeconds" in source
    assert "research_runtime_supervisor_alive" in source
    assert "research_runtime_supervisor_automatic_restart_allowed = $false" in source


def test_optimizer_observes_supervisor_without_using_it_as_required_start_gate() -> None:
    source = read("Optimize-TradingOSRuntime.ps1")

    assert "ResearchRuntimeSupervisorAliveBefore" in source
    assert '$RuntimeMissingBefore.Count -gt 0' in source
    assert "Get-TradingOSControlPanelOwnershipState" in source
    start_gate = source.split('if (-not $PanelHealthyBefore -or ', 1)[1].split(') {', 1)[0]
    assert "ResearchRuntimeSupervisorAliveBefore" not in start_gate
    assert "optional_research_runtime_supervisor_alive" in source
    assert "research_runtime_supervisor_loop_alive" in source
    assert "research_runtime_supervisor_automatic_restart_allowed = $false" in source


def test_status_command_exposes_launcher_loop_and_report() -> None:
    source = read("Get-TradingOSAutostartStatus.ps1")

    assert "research_runtime_supervisor_launcher" in source
    assert "research_runtime_supervisor_loop" in source
    assert "research_runtime_supervisor_report" in source


def test_options_skew_launcher_is_observer_only_and_duplicate_guarded() -> None:
    source = read("Start-DeribitOptionsSkewForwardObserver.ps1")

    assert "TradingOSDeribitOptionsSkewForwardLauncher" in source
    assert "blocked_duplicate_observer_processes" in source
    assert "blocked_preflight_failed" in source
    assert "Get-LogicalProcessRoots" in source
    assert "$Candidates = @(Get-LogicalProcessRoots" in source
    assert "Test-ProcessBelongsToRoot" in source
    assert "$MatchedCandidates += $StatusProcess" in source
    assert "$ObserverScript run-once" in source
    assert "automatic_restart_allowed = $false" in source
    assert "process_stop_allowed = $false" in source
    assert "observer_only = $true" in source
    assert "orders_allowed = $false" in source
    assert "can_trade = $false" in source
    assert "Stop-Process" not in source


def test_options_upstream_launcher_is_fail_closed_and_orderless() -> None:
    source = read("Start-DeribitOptionsResearchComponent.ps1")

    assert 'ValidateSet("collector", "readiness")' in source
    assert "TradingOSDeribitOptionsSurfaceCollectorLauncher" in source
    assert "TradingOSDeribitOptionsReadinessGuardLauncher" in source
    assert "blocked_duplicate_component_processes" in source
    assert "blocked_upstream_collector_not_alive" in source
    assert "blocked_preflight_failed" in source
    assert "Get-LogicalProcessRoots" in source
    assert "$Candidates = @(Get-LogicalProcessRoots" in source
    assert "Test-ProcessBelongsToRoot" in source
    assert "$MatchedCandidates += $StatusProcess" in source
    assert "automatic_restart_allowed = $false" in source
    assert "process_stop_allowed = $false" in source
    assert "orders_allowed = $false" in source
    assert "can_trade = $false" in source
    assert "Stop-Process" not in source
    assert "taskkill" not in source.lower()


def test_runtime_keeps_options_research_explicit_and_optimizer_observes_it() -> None:
    runtime = read("Start-TradingOSRuntime.ps1")
    optimizer = read("Optimize-TradingOSRuntime.ps1")
    status = read("Get-TradingOSAutostartStatus.ps1")

    assert "Start-DeribitOptionsSkewForwardObserver.ps1" in runtime
    assert "Start-DeribitOptionsResearchComponent.ps1" in runtime
    assert "DeribitOptionsSurfaceCollectorSleepSeconds" in runtime
    assert "DeribitOptionsReadinessSleepSeconds" in runtime
    assert 'status = "separate_explicit_research_lifecycle"' in runtime
    assert "$ResearchRuntimeSupervisorEligible = $false" in runtime
    assert "$DeribitOptionsSurfaceCollectorEligible = $false" in runtime
    assert "$DeribitOptionsReadinessEligible = $false" in runtime
    assert "$DeribitOptionsSkewForwardEligible = $false" in runtime
    assert "-Component collector" not in runtime
    assert "-Component readiness" not in runtime
    assert "$DeribitOptionsSkewForwardRaw" not in runtime
    assert "deribit_options_surface_collector_alive" in runtime
    assert "deribit_options_readiness_alive" in runtime
    assert "deribit_options_skew_forward_alive" in runtime
    assert "deribit_options_surface_collector_automatic_restart_allowed = $false" in runtime
    assert "deribit_options_readiness_automatic_restart_allowed = $false" in runtime
    assert "deribit_options_skew_forward_automatic_restart_allowed = $false" in runtime
    assert "DeribitOptionsSurfaceCollectorAliveBefore" in optimizer
    assert "DeribitOptionsReadinessAliveBefore" in optimizer
    assert "deribit_options_surface_collector_loop_alive" in optimizer
    assert "deribit_options_readiness_loop_alive" in optimizer
    assert "DeribitOptionsSkewForwardAliveBefore" in optimizer
    start_gate = optimizer.split('if (-not $PanelHealthyBefore -or ', 1)[1].split(') {', 1)[0]
    assert "DeribitOptionsSkewForwardAliveBefore" not in start_gate
    assert "optional_deribit_options_skew_forward_alive" in optimizer
    assert "deribit_options_skew_forward_loop_alive" in optimizer
    assert "deribit_options_skew_forward_launcher" in status
    assert "deribit_options_surface_collector_launcher" in status
    assert "deribit_options_surface_collector_loop" in status
    assert "deribit_options_surface_collector_report" in status
    assert "deribit_options_readiness_launcher" in status
    assert "deribit_options_readiness_loop" in status
    assert "deribit_options_readiness_report" in status
    assert "deribit_options_skew_forward_loop" in status
    assert "deribit_options_skew_forward_report" in status
