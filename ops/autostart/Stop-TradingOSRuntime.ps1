param(
    [ValidateRange(1024, 65535)][int]$ControlPanelPort = 8765,
    [switch]$LoopsOnly,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "TradingOSRuntimeLifecycle.ps1")

# Compatibility inventory retained for wiring-contract checks. Runtime behavior
# is driven by configs\TRADING_OS_RUNTIME_COMPONENTS.json.
# logs\forward_paper_feed\forward_scheduler_loop.lock.json
# logs\forward_paper_feed\forward_runtime_watchdog_loop.lock.json
# logs\forward_paper_feed\crowd_fade_observer_loop.lock.json
# logs\runtime_backup\daily_drive_backup_loop.lock.json
# logs\cross_venue_data\cross_venue_data_loop.lock.json
# logs\cross_venue_microstructure\microstructure_loop.lock.json
# logs\cross_venue_microstructure\microstructure_book_loop.lock.json
# logs\cross_venue_microstructure\microstructure_watchdog_loop.lock.json
# logs\cross_venue_microstructure\microstructure_unblock_status_loop.lock.json
# logs\liquidation_bybit\bybit_all_liquidation_watchdog_loop.lock.json
# logs\liquidation_bybit\bybit_all_liquidation_loop.lock.json
# logs\liquidation_force_order\liquidation_force_order_loop.lock.json
# logs\liquidation_force_order\liquidation_force_order_watchdog_loop.lock.json
# logs\cross_stack_replication\cross_stack_replication_transition_loop.lock.json
# logs\real_edge_observer\real_edge_observer_pulse_loop.lock.json
# logs\cex_dex_funding\cex_dex_funding_collector_loop.lock.json
# logs\cex_dex_funding\cex_dex_funding_freshness_watchdog_loop.lock.json
# logs\bitunix_wo105_v2\bitunix_wo105_v2_forward_loop.lock.json

$Manifest = Get-TradingOSRuntimeManifest -Root $Root
$LifecycleComponents = @($Manifest.components) + @($Manifest.shutdown_only_components)
$StatusPath = Join-Path $Root "logs\runtime\runtime_stop_status.json"
$OperationMutexName = Get-TradingOSRuntimeMutexName -Root $Root
$OperationMutex = New-Object System.Threading.Mutex($false, $OperationMutexName)
$OperationMutexAcquired = $false

function Get-MatchingRuntimeScriptPids {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [hashtable]$ProcessSnapshot
    )
    if (-not $ProcessSnapshot) { $ProcessSnapshot = Get-TradingOSProcessSnapshot }
    return @($ProcessSnapshot.Values | Where-Object {
        Test-TradingOSManagedScriptProcess -CimProcess $_ -ExpectedScriptPath $ScriptPath -AllowedPowerShellExecutables $script:AllowedPowerShellExecutables
    } | ForEach-Object { [int]$_.ProcessId } | Sort-Object -Unique)
}

function Test-MatchingRuntimeScriptProcess {
    param([Parameter(Mandatory = $true)][string]$ScriptPath, [hashtable]$ProcessSnapshot)
    return @(Get-MatchingRuntimeScriptPids -ScriptPath $ScriptPath -ProcessSnapshot $ProcessSnapshot).Count -gt 0
}

function Get-LockCapture {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Capture = [ordered]@{ path = $Path; present = Test-Path -LiteralPath $Path; read_ok = $false; raw = $null; pid = 0 }
    if (-not $Capture.present) { return [pscustomobject]$Capture }
    try {
        $Capture.raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
        $Capture.pid = [int](($Capture.raw | ConvertFrom-Json -ErrorAction Stop).pid)
        $Capture.read_ok = $Capture.pid -gt 0
    } catch {}
    return [pscustomobject]$Capture
}

function Remove-VerifiedCapturedLock {
    param(
        [Parameter(Mandatory = $true)]$Capture,
        [Parameter(Mandatory = $true)][int]$ExpectedProcessId,
        [Parameter(Mandatory = $true)][string]$ExpectedScriptPath
    )
    if (-not $Capture.present) {
        if ((Test-Path -LiteralPath $Capture.path) -or (Test-MatchingRuntimeScriptProcess -ScriptPath $ExpectedScriptPath)) {
            return [pscustomobject]@{ safe = $false; action = "retained"; detail = "lock_or_process_reappeared_after_capture" }
        }
        return [pscustomobject]@{ safe = $true; action = "already_absent"; detail = "lock_absent_and_process_stopped" }
    }
    if (-not $Capture.read_ok -or [int]$Capture.pid -ne $ExpectedProcessId) {
        return [pscustomobject]@{ safe = $false; action = "retained"; detail = "captured_lock_unreadable_or_pid_mismatch" }
    }
    if (Test-MatchingRuntimeScriptProcess -ScriptPath $ExpectedScriptPath) {
        return [pscustomobject]@{ safe = $false; action = "retained"; detail = "matching_process_present" }
    }
    if (-not (Test-Path -LiteralPath $Capture.path)) {
        return [pscustomobject]@{ safe = $true; action = "removed_by_process_exit"; detail = "captured_process_removed_its_lock" }
    }
    $Quarantine = "$($Capture.path).stop-quarantine.$([guid]::NewGuid().ToString('N'))"
    try {
        Move-TradingOSFileAtomic -TemporaryPath $Capture.path -DestinationPath $Quarantine
        $MovedRaw = Get-Content -LiteralPath $Quarantine -Raw -ErrorAction Stop
        $MovedPid = [int](($MovedRaw | ConvertFrom-Json -ErrorAction Stop).pid)
        if (-not $MovedRaw.Equals([string]$Capture.raw, [System.StringComparison]::Ordinal) -or $MovedPid -ne $ExpectedProcessId) {
            if (-not (Test-Path -LiteralPath $Capture.path)) { Move-TradingOSFileAtomic -TemporaryPath $Quarantine -DestinationPath $Capture.path }
            return [pscustomobject]@{ safe = $false; action = "retained"; detail = "captured_lock_changed" }
        }
        if ((Test-Path -LiteralPath $Capture.path) -or (Test-MatchingRuntimeScriptProcess -ScriptPath $ExpectedScriptPath)) {
            if (-not (Test-Path -LiteralPath $Capture.path)) { Move-TradingOSFileAtomic -TemporaryPath $Quarantine -DestinationPath $Capture.path }
            return [pscustomobject]@{ safe = $false; action = "retained"; detail = "concurrent_lock_or_process_reappeared" }
        }
        Remove-Item -LiteralPath $Quarantine -Force -ErrorAction Stop
        return [pscustomobject]@{ safe = $true; action = "removed_verified_stale_lock"; detail = "captured_content_and_pid_verified" }
    } catch {
        if ((Test-Path -LiteralPath $Quarantine) -and -not (Test-Path -LiteralPath $Capture.path)) {
            try { Move-TradingOSFileAtomic -TemporaryPath $Quarantine -DestinationPath $Capture.path } catch {}
        }
        return [pscustomobject]@{ safe = $false; action = "retained"; detail = "lock_validation_or_removal_failed:$($_.Exception.Message)" }
    }
}

function Quarantine-UnverifiableCapturedLock {
    param(
        [Parameter(Mandatory = $true)]$Capture,
        [Parameter(Mandatory = $true)][string]$ExpectedScriptPath,
        [Parameter(Mandatory = $true)][string]$RequestId
    )
    if (-not $Capture.present -or -not (Test-Path -LiteralPath $Capture.path)) {
        if (Test-MatchingRuntimeScriptProcess -ScriptPath $ExpectedScriptPath) { return [pscustomobject]@{ safe = $false; action = 'retained'; detail = 'matching_process_present' } }
        return [pscustomobject]@{ safe = $true; action = 'already_absent'; detail = 'unverifiable_lock_absent_after_job_stop' }
    }
    if (Test-MatchingRuntimeScriptProcess -ScriptPath $ExpectedScriptPath) { return [pscustomobject]@{ safe = $false; action = 'retained'; detail = 'matching_process_present' } }
    $Quarantine = "$($Capture.path).stop-unverified.$(([guid]$RequestId).ToString('N')).$([guid]::NewGuid().ToString('N'))"
    try {
        Move-TradingOSFileAtomic -TemporaryPath $Capture.path -DestinationPath $Quarantine
        if ((Test-Path -LiteralPath $Capture.path) -or (Test-MatchingRuntimeScriptProcess -ScriptPath $ExpectedScriptPath)) {
            if (-not (Test-Path -LiteralPath $Capture.path)) { Move-TradingOSFileAtomic -TemporaryPath $Quarantine -DestinationPath $Capture.path }
            return [pscustomobject]@{ safe = $false; action = 'retained'; detail = 'concurrent_lock_or_process_reappeared' }
        }
        return [pscustomobject]@{ safe = $true; action = 'quarantined_unverifiable_lock'; detail = 'managed_lock_path_quarantined_after_verified_job_stop'; quarantine_path = $Quarantine }
    } catch {
        if ((Test-Path -LiteralPath $Quarantine) -and -not (Test-Path -LiteralPath $Capture.path)) {
            try { Move-TradingOSFileAtomic -TemporaryPath $Quarantine -DestinationPath $Capture.path } catch {}
        }
        return [pscustomobject]@{ safe = $false; action = 'retained'; detail = "unverifiable_lock_quarantine_failed:$($_.Exception.Message)" }
    }
}

try {
    try { $OperationMutexAcquired = $OperationMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $OperationMutexAcquired = $true }
    if (-not $OperationMutexAcquired) {
        $Blocked = [ordered]@{ ts = (Get-Date).ToUniversalTime().ToString("o"); status = "blocked_runtime_operation_in_progress"; root = $Root; operation_mutex = $OperationMutexName; live_trading_locked = $true; can_trade = $false }
        $Blocked | ConvertTo-Json -Depth 5
        throw "TradingOS stop blocked because another runtime lifecycle operation is in progress."
    }

    $ShutdownSentinelPath = Get-TradingOSRuntimeShutdownSentinelPath -Root $Root
    $ShutdownRequestId = [guid]::NewGuid().ToString()
    $RequestProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $PID" -ErrorAction SilentlyContinue
    $RequestParentProcess = $null
    if ($RequestProcess -and [int]$RequestProcess.ParentProcessId -gt 0) {
        $RequestParentProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$RequestProcess.ParentProcessId)" -ErrorAction SilentlyContinue
    }
    if (-not $WhatIf) {
        Write-TradingOSJsonFileAtomic -Path $ShutdownSentinelPath -Payload ([ordered]@{
            ts = (Get-Date).ToUniversalTime().ToString("o")
            request_id = $ShutdownRequestId
            root = $Root
            requested_by = "Stop-TradingOSRuntime.ps1"
            request_process_id = $PID
            request_parent_process_id = if ($RequestProcess) { [int]$RequestProcess.ParentProcessId } else { 0 }
            request_parent_name = if ($RequestParentProcess) { [string]$RequestParentProcess.Name } else { $null }
            request_parent_executable = if ($RequestParentProcess) { [string]$RequestParentProcess.ExecutablePath } else { $null }
            request_parent_command_line = if ($RequestParentProcess) { [string]$RequestParentProcess.CommandLine } else { $null }
            loops_only = [bool]$LoopsOnly
            control_panel_port = $ControlPanelPort
            live_trading_locked = $true
            can_trade = $false
        }) -Depth 5
    }

    $Actions = New-Object System.Collections.Generic.List[object]
    $Failures = New-Object System.Collections.Generic.List[string]
    $TerminationPlan = New-Object System.Collections.Generic.List[object]
    $StaleLockPlan = New-Object System.Collections.Generic.List[object]
    $StaleReceiptPlan = New-Object System.Collections.Generic.List[object]
    $RetainedReceiptInventory = New-Object System.Collections.Generic.List[object]
    $InitialVerifiedCount = 0
    $script:AllowedPowerShellExecutables = Get-TradingOSAllowedPowerShellExecutables
    $PreflightSnapshot = Get-TradingOSProcessSnapshot

    # Inventory every receipt before any process is terminated. A receipt outside
    # the manifest or selected panel lifecycle makes an all-or-nothing stop unsafe.
    $KnownComponentIds = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($KnownComponent in $LifecycleComponents) { $null = $KnownComponentIds.Add([string]$KnownComponent.id) }
    $RequestedPanelComponentId = "control_panel_$ControlPanelPort"
    $ReceiptDirectory = Join-Path $Root 'logs\runtime_jobs'
    if (Test-Path -LiteralPath $ReceiptDirectory) {
        foreach ($ReceiptFile in @(Get-ChildItem -LiteralPath $ReceiptDirectory -Filter '*.json' -File -ErrorAction Stop)) {
            $ReceiptComponentId = [System.IO.Path]::GetFileNameWithoutExtension($ReceiptFile.Name)
            try { $ReceiptComponentId = Assert-TradingOSRuntimeComponentId -ComponentId $ReceiptComponentId } catch {
                $Failures.Add("unknown_runtime_job_receipt:$($ReceiptFile.Name)") | Out-Null
                continue
            }
            if ($KnownComponentIds.Contains($ReceiptComponentId) -or $ReceiptComponentId -ceq $RequestedPanelComponentId) { continue }
            if ($ReceiptComponentId -match '^control_panel_(?<port>[0-9]{1,5})$' -and [int]$Matches.port -ge 1024 -and [int]$Matches.port -le 65535) {
                if (-not $LoopsOnly) {
                    $Failures.Add("unrequested_control_panel_receipt_blocks_full_stop:$ReceiptComponentId") | Out-Null
                    continue
                }
                try {
                    $RetainedPanelScript = Join-Path $Root 'ops\control_panel\control_panel.py'
                    $RetainedState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $ReceiptComponentId -ExpectedScriptPath $RetainedPanelScript -ProcessSnapshot $PreflightSnapshot
                    $RetainedReceiptInventory.Add([pscustomobject]@{ component = $ReceiptComponentId; decision = [string]$RetainedState.decision; action = 'retained_loops_only' }) | Out-Null
                    if ($RetainedState.process) { try { $RetainedState.process.Dispose() } catch {} }
                } catch { $Failures.Add("invalid_retained_panel_receipt:${ReceiptComponentId}:$($_.Exception.Message)") | Out-Null }
                continue
            }
            $Failures.Add("unknown_runtime_job_receipt:$ReceiptComponentId") | Out-Null
        }
    }

    $StopComponents = @($LifecycleComponents | Sort-Object @{ Expression = {
        if ([string]$_.id -in @("bybit_all_liquidation_watchdog", "liquidation_force_order_watchdog", "forward_runtime_watchdog", "cross_venue_microstructure_watchdog", "cex_funding_freshness_watchdog")) { 0 }
        elseif ([string]$_.id -in @("bybit_all_liquidation_collector", "liquidation_force_order_collector")) { 1 }
        else { 2 }
    } }, @{ Expression = { [string]$_.id } })

    foreach ($Component in $StopComponents) {
        $ScriptPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component.script)
        $LockPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component.lock_path)
        $State = Get-TradingOSRuntimeComponentState -Root $Root -Component $Component -ProcessSnapshot $PreflightSnapshot -AllowedPowerShellExecutables $script:AllowedPowerShellExecutables
        $ExactPids = @($State.matching_script_pids)
        $LockCapture = Get-LockCapture -Path $LockPath
        $JobState = $null
        try {
            try {
                $JobState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId ([string]$Component.id) -ExpectedScriptPath $ScriptPath -ProcessSnapshot $PreflightSnapshot -AllowedPowerShellExecutables $script:AllowedPowerShellExecutables
            } catch {
                $Failures.Add("invalid_job_receipt:$($Component.id):$($_.Exception.Message)") | Out-Null
                continue
            }

            $AllExactInReceiptJob = $JobState.decision -eq 'running_verified_job_contained' -and $ExactPids.Count -gt 0
            if ($AllExactInReceiptJob) {
                foreach ($ExactPid in $ExactPids) {
                    if (-not (Test-TradingOSRuntimeJobContainsProcess -Root $Root -ComponentId ([string]$Component.id) -ProcessId ([int]$ExactPid))) { $AllExactInReceiptJob = $false; break }
                }
            }
            $ReceiptOwnsExactJob = $AllExactInReceiptJob -and [int]$JobState.receipt.pid -in $ExactPids

            if ($State.decision -eq "running_verified") {
                $InitialVerifiedCount++
                $ExactIdentity = $ExactPids.Count -eq 1 -and [int]$ExactPids[0] -eq [int]$State.pid
                $Contained = $JobState.decision -eq "running_verified_job_contained" -and [int]$JobState.receipt.pid -eq [int]$State.pid
                $LockOwned = $LockCapture.present -and $LockCapture.read_ok -and [int]$LockCapture.pid -eq [int]$State.pid
                if ($ExactIdentity -and $Contained -and $LockOwned) {
                    $TerminationPlan.Add([pscustomobject]@{ component = $Component; script_path = $ScriptPath; lock_capture = $LockCapture; receipt = $JobState.receipt; pid = [int]$State.pid; recovery = $false; lock_cleanup = 'verified_remove' }) | Out-Null
                    $Actions.Add([pscustomobject]@{ component = $Component.id; pid = $State.pid; action = $(if ($WhatIf) { "loop_would_stop" } else { "verified_job_planned" }); safe_to_remove_lock = $true; containment = "verified_job_object" }) | Out-Null
                } elseif ($ReceiptOwnsExactJob) {
                    $LockCleanup = if ($LockCapture.present -and $LockCapture.read_ok -and [int]$LockCapture.pid -eq [int]$JobState.receipt.pid) { 'verified_remove' } elseif ($LockCapture.present) { 'quarantine_unverified' } else { 'verified_remove' }
                    $TerminationPlan.Add([pscustomobject]@{ component = $Component; script_path = $ScriptPath; lock_capture = $LockCapture; receipt = $JobState.receipt; pid = [int]$JobState.receipt.pid; recovery = $true; lock_cleanup = $LockCleanup }) | Out-Null
                    $Actions.Add([pscustomobject]@{ component = $Component.id; pid = [int]$JobState.receipt.pid; exact_pids = $ExactPids; action = $(if ($WhatIf) { 'owned_duplicate_or_lock_drift_job_would_stop' } else { 'owned_duplicate_or_lock_drift_job_planned' }); safe_to_remove_lock = $LockCleanup -eq 'verified_remove'; containment = 'verified_receipt_named_job' }) | Out-Null
                } else {
                    $Failures.Add("blocked_uncontained_duplicate_or_unowned_runtime:$($Component.id)") | Out-Null
                    $Actions.Add([pscustomobject]@{ component = $Component.id; pid = $State.pid; exact_pids = $ExactPids; action = "unsafe_lock_retained_for_manual_review"; reason = "blocked_uncontained_legacy_runtime"; job_decision = $JobState.decision }) | Out-Null
                }
                continue
            }

            if ($ReceiptOwnsExactJob) {
                $LockCleanup = if (-not $LockCapture.present) { 'verified_remove' } elseif ($LockCapture.read_ok -and [int]$LockCapture.pid -eq [int]$JobState.receipt.pid) { 'verified_remove' } else { 'quarantine_unverified' }
                $TerminationPlan.Add([pscustomobject]@{ component = $Component; script_path = $ScriptPath; lock_capture = $LockCapture; receipt = $JobState.receipt; pid = [int]$JobState.receipt.pid; recovery = $true; lock_cleanup = $LockCleanup }) | Out-Null
                $Actions.Add([pscustomobject]@{ component = $Component.id; pid = [int]$JobState.receipt.pid; exact_pids = $ExactPids; action = $(if ($WhatIf) { 'owned_job_with_lock_drift_would_stop' } else { 'owned_job_with_lock_drift_planned' }); safe_to_remove_lock = $LockCleanup -eq 'verified_remove'; containment = 'verified_receipt_named_job' }) | Out-Null
                continue
            }

            $RecoverableOrphanJob = $JobState.decision -in @('job_active_preidentity_receipt', 'job_active_root_process_absent', 'job_active_root_pid_reused')
            $AllExactInOrphanJob = $RecoverableOrphanJob
            if ($AllExactInOrphanJob) {
                foreach ($ExactPid in $ExactPids) {
                    if (-not (Test-TradingOSRuntimeJobContainsProcess -Root $Root -ComponentId ([string]$Component.id) -ProcessId ([int]$ExactPid))) { $AllExactInOrphanJob = $false; break }
                }
            }
            if ($RecoverableOrphanJob -and $AllExactInOrphanJob) {
                $LockCleanup = if (-not $LockCapture.present) { 'verified_remove' } elseif ([int]$JobState.receipt.pid -gt 0 -and $LockCapture.read_ok -and [int]$LockCapture.pid -eq [int]$JobState.receipt.pid) { 'verified_remove' } else { 'quarantine_unverified' }
                $TerminationPlan.Add([pscustomobject]@{ component = $Component; script_path = $ScriptPath; lock_capture = $LockCapture; receipt = $JobState.receipt; pid = [int]$JobState.receipt.pid; recovery = $true; lock_cleanup = $LockCleanup }) | Out-Null
                $Actions.Add([pscustomobject]@{ component = $Component.id; pid = [int]$JobState.receipt.pid; exact_pids = $ExactPids; action = $(if ($WhatIf) { 'recoverable_orphan_job_would_stop' } else { 'recoverable_orphan_job_planned' }); safe_to_remove_lock = $LockCleanup -eq 'verified_remove'; containment = 'verified_receipt_named_job' }) | Out-Null
                continue
            }

            if ($State.decision -eq "missing_lock") {
                $RecoverableActiveJob = $JobState.decision -in @('job_active_preidentity_receipt', 'job_active_root_process_absent', 'job_active_root_pid_reused')
                $AllExactInReceiptJob = $true
                if ($ExactPids.Count -gt 0) {
                    foreach ($ExactPid in $ExactPids) {
                        if (-not (Test-TradingOSRuntimeJobContainsProcess -Root $Root -ComponentId ([string]$Component.id) -ProcessId ([int]$ExactPid))) { $AllExactInReceiptJob = $false; break }
                    }
                }
                if ($ExactPids.Count -gt 0 -and (-not $RecoverableActiveJob -or -not $AllExactInReceiptJob)) {
                    $Failures.Add("unowned_matching_process_without_lock_not_stopped:$($Component.id)") | Out-Null
                    $Actions.Add([pscustomobject]@{ component = $Component.id; exact_pids = $ExactPids; action = "unowned_matching_process_without_lock_not_stopped"; reason = "missing_lock_with_exact_script_process" }) | Out-Null
                } elseif ($RecoverableActiveJob) {
                    $TerminationPlan.Add([pscustomobject]@{ component = $Component; script_path = $ScriptPath; lock_capture = $LockCapture; receipt = $JobState.receipt; pid = [int]$JobState.receipt.pid; recovery = $true; lock_cleanup = 'verified_remove' }) | Out-Null
                    $Actions.Add([pscustomobject]@{ component = $Component.id; pid = [int]$JobState.receipt.pid; action = $(if ($WhatIf) { 'recoverable_orphan_job_would_stop' } else { 'recoverable_orphan_job_planned' }); safe_to_remove_lock = $true; containment = 'verified_receipt_named_job' }) | Out-Null
                } elseif ($JobState.decision -in @('stale_receipt_process_absent', 'stale_receipt_pid_reused', 'stale_receipt_session_mismatch', 'reserved_receipt_no_active_job')) {
                    $StaleReceiptPlan.Add([pscustomobject]@{ component_id = [string]$Component.id; script_path = $ScriptPath; pid = [int]$JobState.receipt.pid }) | Out-Null
                    $Actions.Add([pscustomobject]@{ component = $Component.id; action = 'not_running_stale_receipt_planned'; state = $JobState.decision }) | Out-Null
                } elseif ($JobState.decision -eq 'missing_receipt') {
                    $Actions.Add([pscustomobject]@{ component = $Component.id; action = "not_running"; state = $State.decision }) | Out-Null
                } else {
                    $Failures.Add("unverifiable_job_without_lock:$($Component.id):$($JobState.decision)") | Out-Null
                    $Actions.Add([pscustomobject]@{ component = $Component.id; action = "unsafe_lock_retained_for_manual_review"; reason = $JobState.decision }) | Out-Null
                }
                continue
            }

            if ($State.decision -eq "stale_lock_dead_pid") {
                if ($ExactPids.Count -gt 0) {
                    $Failures.Add("duplicate_exact_process_with_stale_lock:$($Component.id)") | Out-Null
                } elseif (-not $LockCapture.read_ok -or [int]$LockCapture.pid -ne [int]$State.pid) {
                    $Failures.Add("stale_lock_capture_failed:$($Component.id)") | Out-Null
                    $Actions.Add([pscustomobject]@{ component = $Component.id; action = "unsafe_lock_retained_for_manual_review"; reason = "stale_lock_was_unreadable_or_changed" }) | Out-Null
                } elseif ($JobState.decision -eq 'job_active_root_process_absent' -and [int]$JobState.receipt.pid -eq [int]$LockCapture.pid) {
                    $TerminationPlan.Add([pscustomobject]@{ component = $Component; script_path = $ScriptPath; lock_capture = $LockCapture; receipt = $JobState.receipt; pid = [int]$JobState.receipt.pid; recovery = $true; lock_cleanup = 'verified_remove' }) | Out-Null
                    $Actions.Add([pscustomobject]@{ component = $Component.id; pid = [int]$JobState.receipt.pid; action = $(if ($WhatIf) { 'orphan_descendant_job_would_stop' } else { 'orphan_descendant_job_planned' }); safe_to_remove_lock = $true; containment = 'verified_receipt_named_job' }) | Out-Null
                } elseif ($JobState.decision -in @('missing_receipt', 'stale_receipt_process_absent', 'stale_receipt_pid_reused', 'stale_receipt_session_mismatch', 'reserved_receipt_no_active_job')) {
                    $StaleLockPlan.Add([pscustomobject]@{ component = $Component; script_path = $ScriptPath; lock_capture = $LockCapture; pid = [int]$State.pid }) | Out-Null
                    if ($JobState.receipt) { $StaleReceiptPlan.Add([pscustomobject]@{ component_id = [string]$Component.id; script_path = $ScriptPath; pid = [int]$JobState.receipt.pid }) | Out-Null }
                    $Actions.Add([pscustomobject]@{ component = $Component.id; pid = $State.pid; action = $(if ($WhatIf) { "stale_dead_lock_would_remove" } else { "stale_lock_removal_planned" }); safe_to_remove_lock = $true }) | Out-Null
                } else {
                    $Failures.Add("active_or_mismatched_job_with_stale_lock:$($Component.id):$($JobState.decision)") | Out-Null
                    $Actions.Add([pscustomobject]@{ component = $Component.id; action = "unsafe_lock_retained_for_manual_review"; reason = $JobState.decision }) | Out-Null
                }
                continue
            }

            if ($State.decision -eq 'pid_identity_mismatch' -and $ExactPids.Count -eq 0 -and
                $JobState.decision -eq 'job_active_root_pid_reused' -and $LockCapture.read_ok -and
                [int]$LockCapture.pid -eq [int]$JobState.receipt.pid) {
                $TerminationPlan.Add([pscustomobject]@{ component = $Component; script_path = $ScriptPath; lock_capture = $LockCapture; receipt = $JobState.receipt; pid = [int]$JobState.receipt.pid; recovery = $true; lock_cleanup = 'verified_remove' }) | Out-Null
                $Actions.Add([pscustomobject]@{ component = $Component.id; pid = [int]$JobState.receipt.pid; action = $(if ($WhatIf) { 'pid_reuse_orphan_job_would_stop' } else { 'pid_reuse_orphan_job_planned' }); safe_to_remove_lock = $true; containment = 'verified_receipt_named_job' }) | Out-Null
                continue
            }

            if ($State.decision -eq 'pid_identity_mismatch' -and $ExactPids.Count -eq 0 -and
                $LockCapture.read_ok -and [int]$LockCapture.pid -eq [int]$State.pid -and
                $JobState.decision -in @('missing_receipt', 'stale_receipt_process_absent', 'stale_receipt_pid_reused', 'stale_receipt_session_mismatch', 'reserved_receipt_no_active_job')) {
                $StaleLockPlan.Add([pscustomobject]@{ component = $Component; script_path = $ScriptPath; lock_capture = $LockCapture; pid = [int]$State.pid }) | Out-Null
                if ($JobState.receipt) { $StaleReceiptPlan.Add([pscustomobject]@{ component_id = [string]$Component.id; script_path = $ScriptPath; pid = [int]$JobState.receipt.pid }) | Out-Null }
                $Actions.Add([pscustomobject]@{ component = $Component.id; pid = $State.pid; action = $(if ($WhatIf) { 'pid_reuse_stale_lock_would_remove' } else { 'pid_reuse_stale_lock_removal_planned' }); safe_to_remove_lock = $true }) | Out-Null
                continue
            }

            $Failures.Add("unsafe_component_state:$($Component.id):$($State.decision)") | Out-Null
            $Actions.Add([pscustomobject]@{ component = $Component.id; pid = $State.pid; action = "unsafe_lock_retained_for_manual_review"; reason = $State.decision }) | Out-Null
        } finally {
            if ($JobState -and $JobState.process) { try { $JobState.process.Dispose() } catch {} }
        }
    }

    $PanelState = $null
    $PanelPlan = $null
    $PanelAction = "skipped_loops_only"
    if (-not $LoopsOnly) {
        $PanelState = Get-TradingOSControlPanelState -Root $Root -Port $ControlPanelPort
        $PanelComponentId = "control_panel_$ControlPanelPort"
        $PanelScript = Join-Path $Root "ops\control_panel\control_panel.py"
        $PanelExactPids = @(Get-MatchingRuntimeScriptPids -ScriptPath $PanelScript -ProcessSnapshot $PreflightSnapshot)
        $PanelJobState = $null
        try { $PanelJobState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $PanelComponentId -ExpectedScriptPath $PanelScript -ProcessSnapshot $PreflightSnapshot -AllowedPowerShellExecutables $script:AllowedPowerShellExecutables } catch { $Failures.Add("invalid_panel_job_receipt:$($_.Exception.Message)") | Out-Null }
        if ($PanelState.decision -eq "running_verified") {
            $AllExactContained = $PanelExactPids.Count -gt 0 -and [int]$PanelState.pid -in $PanelExactPids
            foreach ($PanelExactPid in $PanelExactPids) {
                if (-not (Test-TradingOSRuntimeJobContainsProcess -Root $Root -ComponentId $PanelComponentId -ProcessId ([int]$PanelExactPid))) { $AllExactContained = $false; break }
            }
            $PanelReceiptJobRecoverable = $PanelJobState -and $PanelJobState.decision -in @('running_verified_job_contained', 'job_active_root_process_absent', 'job_active_root_pid_reused')
            $ListenerContained = $PanelReceiptJobRecoverable -and (Test-TradingOSRuntimeJobContainsProcess -Root $Root -ComponentId $PanelComponentId -ProcessId ([int]$PanelState.pid))
            if (-not $ListenerContained -or -not $AllExactContained) {
                $Failures.Add("panel_identity_mismatch_not_stopped:blocked_uncontained_legacy_runtime") | Out-Null
                $PanelAction = "panel_identity_mismatch_not_stopped"
            } else {
                $PanelPlan = [pscustomobject]@{ component_id = $PanelComponentId; receipt = $PanelJobState.receipt; root_pid = [int]$PanelJobState.receipt.pid; listener_pid = [int]$PanelState.pid; script_path = $PanelScript; exact_pids = $PanelExactPids; recovery = $false }
                $PanelAction = if ($WhatIf) { "panel_would_stop" } else { "verified_panel_job_planned" }
            }
        } elseif ($PanelState.decision -eq "missing_listener") {
            $RecoverablePanelJob = $PanelJobState -and $PanelJobState.decision -in @('running_verified_job_contained', 'job_active_preidentity_receipt', 'job_active_root_process_absent', 'job_active_root_pid_reused')
            $AllExactContained = $true
            if ($PanelExactPids.Count -gt 0) {
                foreach ($PanelExactPid in $PanelExactPids) {
                    if (-not (Test-TradingOSRuntimeJobContainsProcess -Root $Root -ComponentId $PanelComponentId -ProcessId ([int]$PanelExactPid))) { $AllExactContained = $false; break }
                }
            }
            if ($RecoverablePanelJob -and $AllExactContained) {
                $PanelPlan = [pscustomobject]@{ component_id = $PanelComponentId; receipt = $PanelJobState.receipt; root_pid = [int]$PanelJobState.receipt.pid; listener_pid = 0; script_path = $PanelScript; exact_pids = $PanelExactPids; recovery = $true }
                $PanelAction = if ($WhatIf) { 'recoverable_panel_job_would_stop' } else { 'recoverable_panel_job_planned' }
            } elseif ($PanelExactPids.Count -gt 0) {
                $Failures.Add("panel_exact_process_without_verified_listener:$($PanelExactPids -join ',')") | Out-Null
                $PanelAction = "panel_identity_mismatch_not_stopped"
            } elseif ($PanelJobState -and $PanelJobState.decision -in @('stale_receipt_process_absent', 'stale_receipt_pid_reused', 'stale_receipt_session_mismatch', 'reserved_receipt_no_active_job')) {
                $StaleReceiptPlan.Add([pscustomobject]@{ component_id = $PanelComponentId; script_path = $PanelScript; pid = [int]$PanelJobState.receipt.pid }) | Out-Null
                $PanelAction = "panel_not_running"
            } elseif (-not $PanelJobState -or $PanelJobState.decision -eq 'missing_receipt') {
                $PanelAction = "panel_not_running"
            } else {
                $Failures.Add("panel_job_active_without_verified_listener:$($PanelJobState.decision)") | Out-Null
                $PanelAction = "panel_identity_mismatch_not_stopped"
            }
        } else {
            $Failures.Add("panel_identity_mismatch_not_stopped:$($PanelState.decision)") | Out-Null
            $PanelAction = "panel_identity_mismatch_not_stopped"
        }
        if ($PanelJobState -and $PanelJobState.process) { try { $PanelJobState.process.Dispose() } catch {} }
    }

    $PreflightSucceeded = $Failures.Count -eq 0
    $StoppedJobs = New-Object System.Collections.Generic.List[object]
    $LockActions = New-Object System.Collections.Generic.List[object]
    if (-not $WhatIf -and $PreflightSucceeded) {
        foreach ($Plan in $TerminationPlan) {
            try {
                $Stopped = Stop-TradingOSRuntimeJobReceipt -Root $Root -ComponentId ([string]$Plan.component.id) -ExpectedProcessId ([int]$Plan.pid) -ExpectedScriptPath ([string]$Plan.script_path)
                $StoppedJobs.Add($Stopped) | Out-Null
                $LockResult = if ([string]$Plan.lock_cleanup -eq 'quarantine_unverified') {
                    Quarantine-UnverifiableCapturedLock -Capture $Plan.lock_capture -ExpectedScriptPath ([string]$Plan.script_path) -RequestId $ShutdownRequestId
                } else {
                    Remove-VerifiedCapturedLock -Capture $Plan.lock_capture -ExpectedProcessId ([int]$Plan.pid) -ExpectedScriptPath ([string]$Plan.script_path)
                }
                $LockActions.Add([pscustomobject]@{ component = $Plan.component.id; result = $LockResult }) | Out-Null
                if (-not $LockResult.safe) { $Failures.Add("job_stopped_lock_retained:$($Plan.component.id):$($LockResult.detail)") | Out-Null }
            } catch { $Failures.Add("job_termination_failed:$($Plan.component.id):$($_.Exception.Message)") | Out-Null }
        }
        foreach ($Plan in $StaleLockPlan) {
            try {
                $LockResult = Remove-VerifiedCapturedLock -Capture $Plan.lock_capture -ExpectedProcessId ([int]$Plan.pid) -ExpectedScriptPath ([string]$Plan.script_path)
                $LockActions.Add([pscustomobject]@{ component = $Plan.component.id; result = $LockResult }) | Out-Null
                if (-not $LockResult.safe) { $Failures.Add("stale_lock_removal_failed:$($Plan.component.id):$($LockResult.detail)") | Out-Null }
            } catch { $Failures.Add("stale_cleanup_failed:$($Plan.component.id):$($_.Exception.Message)") | Out-Null }
        }
        foreach ($Plan in $StaleReceiptPlan) {
            try {
                $Stopped = Stop-TradingOSRuntimeJobReceipt -Root $Root -ComponentId ([string]$Plan.component_id) -ExpectedProcessId ([int]$Plan.pid) -ExpectedScriptPath ([string]$Plan.script_path)
                $StoppedJobs.Add($Stopped) | Out-Null
            } catch { $Failures.Add("stale_receipt_cleanup_failed:$($Plan.component_id):$($_.Exception.Message)") | Out-Null }
        }
        if ($PanelPlan) {
            try {
                $Stopped = Stop-TradingOSRuntimeJobReceipt -Root $Root -ComponentId $PanelPlan.component_id -ExpectedProcessId ([int]$PanelPlan.root_pid) -ExpectedScriptPath $PanelPlan.script_path
                $StoppedJobs.Add($Stopped) | Out-Null
                $PanelAction = "verified_panel_job_stopped"
            } catch {
                $Failures.Add("panel_job_termination_failed:$($_.Exception.Message)") | Out-Null
                $PanelAction = "panel_identity_mismatch_not_stopped"
            }
        }
    }

    $ResidualProcesses = New-Object System.Collections.Generic.List[object]
    $ResidualLocks = New-Object System.Collections.Generic.List[object]
    $ResidualReceipts = New-Object System.Collections.Generic.List[object]
    if (-not $WhatIf -and $PreflightSucceeded) {
        for ($Pass = 0; $Pass -lt 20; $Pass++) {
            $ResidualProcesses.Clear(); $ResidualLocks.Clear(); $ResidualReceipts.Clear()
            $Snapshot = Get-TradingOSProcessSnapshot
            foreach ($Component in $LifecycleComponents) {
                $ScriptPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component.script)
                foreach ($Row in $Snapshot.Values) {
                    if (Test-TradingOSManagedScriptProcess -CimProcess $Row -ExpectedScriptPath $ScriptPath -AllowedPowerShellExecutables $script:AllowedPowerShellExecutables) {
                        $ResidualProcesses.Add([pscustomobject]@{ component = $Component.id; pid = [int]$Row.ProcessId }) | Out-Null
                    }
                }
                $LockPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component.lock_path)
                if (Test-Path -LiteralPath $LockPath) { $ResidualLocks.Add([pscustomobject]@{ component = $Component.id; lock_path = $LockPath }) | Out-Null }
            }
            if (Test-Path -LiteralPath $ReceiptDirectory) {
                foreach ($ReceiptFile in @(Get-ChildItem -LiteralPath $ReceiptDirectory -Filter '*.json' -File -ErrorAction Stop)) {
                    $ReceiptComponentId = [System.IO.Path]::GetFileNameWithoutExtension($ReceiptFile.Name)
                    if ($LoopsOnly -and $ReceiptComponentId -match '^control_panel_[0-9]{1,5}$') { continue }
                    $ResidualReceipts.Add([pscustomobject]@{ component = $ReceiptComponentId; receipt_path = $ReceiptFile.FullName }) | Out-Null
                }
            }
            if (-not $LoopsOnly) {
                $ResidualPanelState = Get-TradingOSControlPanelState -Root $Root -Port $ControlPanelPort
                $ResidualPanelPids = @(Get-MatchingRuntimeScriptPids -ScriptPath (Join-Path $Root 'ops\control_panel\control_panel.py') -ProcessSnapshot $Snapshot)
                foreach ($ResidualPanelPid in $ResidualPanelPids) { $ResidualProcesses.Add([pscustomobject]@{ component = 'control_panel'; pid = [int]$ResidualPanelPid }) | Out-Null }
            } else { $ResidualPanelState = $null }
            if ($ResidualProcesses.Count -eq 0 -and $ResidualLocks.Count -eq 0 -and $ResidualReceipts.Count -eq 0 -and (-not $ResidualPanelState -or $ResidualPanelState.decision -eq "missing_listener")) { break }
            Start-Sleep -Milliseconds 100
        }
        if ($ResidualProcesses.Count -gt 0 -or $ResidualLocks.Count -gt 0 -or $ResidualReceipts.Count -gt 0) { $Failures.Add("residual_process_lock_or_job_receipt_detected") | Out-Null }
        if (-not $LoopsOnly -and $ResidualPanelState -and $ResidualPanelState.decision -ne "missing_listener") { $Failures.Add("panel_listener_remained_after_job_stop") | Out-Null }
    }

    $StopSucceeded = $Failures.Count -eq 0
    $FinalStatus = if ($WhatIf -and $StopSucceeded) { "what_if_completed" } elseif ($WhatIf) { "what_if_degraded" } elseif ($StopSucceeded) { "completed" } else { "degraded" }
    $Report = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("o")
        status = $FinalStatus
        root = $Root
        what_if = [bool]$WhatIf
        loops_only = [bool]$LoopsOnly
        operation_mutex = $OperationMutexName
        shutdown_sentinel = [ordered]@{ path = $ShutdownSentinelPath; request_id = $ShutdownRequestId; action = if ($WhatIf) { "would_create" } else { "created_and_retained_until_explicit_start" } }
        manifest_components = @($Manifest.components).Count
        shutdown_inventory_components = @($LifecycleComponents).Count
        initially_verified_components = $InitialVerifiedCount
        containment = "named_windows_job_objects"
        preflight_succeeded = $PreflightSucceeded
        stop_succeeded = $StopSucceeded
        actions = $Actions.ToArray()
        stopped_process_jobs = $StoppedJobs.ToArray()
        lock_actions = $LockActions.ToArray()
        failures = $Failures.ToArray()
        residual_processes = $ResidualProcesses.ToArray()
        residual_locks = $ResidualLocks.ToArray()
        residual_job_receipts = $ResidualReceipts.ToArray()
        retained_receipts = $RetainedReceiptInventory.ToArray()
        control_panel = [ordered]@{ port = $ControlPanelPort; initial_decision = if ($PanelState) { $PanelState.decision } else { "not_checked" }; action = $PanelAction }
        live_trading_locked = $true
        can_trade = $false
    }
    Write-TradingOSJsonFileAtomic -Path $StatusPath -Payload $Report -Depth 10
    $Report | ConvertTo-Json -Depth 10
    if (-not $StopSucceeded) { throw "TradingOS stop blocked or degraded; inspect $StatusPath" }
} finally {
    if ($OperationMutexAcquired) { try { $OperationMutex.ReleaseMutex() } catch {} }
    $OperationMutex.Dispose()
}
