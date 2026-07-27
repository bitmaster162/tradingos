function ConvertTo-TradingOSWindowsArgument {
    [CmdletBinding()]
    param([AllowNull()][AllowEmptyString()][string]$Argument)

    if ($null -eq $Argument -or $Argument.Length -eq 0) {
        return '""'
    }
    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }

    $Builder = New-Object System.Text.StringBuilder
    [void]$Builder.Append('"')
    $Backslashes = 0
    foreach ($Character in $Argument.ToCharArray()) {
        if ($Character -eq '\') {
            $Backslashes += 1
            continue
        }
        if ($Character -eq '"') {
            if ($Backslashes -gt 0) {
                [void]$Builder.Append(('\' * ($Backslashes * 2)))
                $Backslashes = 0
            }
            [void]$Builder.Append('\"')
            continue
        }
        if ($Backslashes -gt 0) {
            [void]$Builder.Append(('\' * $Backslashes))
            $Backslashes = 0
        }
        [void]$Builder.Append($Character)
    }
    if ($Backslashes -gt 0) {
        [void]$Builder.Append(('\' * ($Backslashes * 2)))
    }
    [void]$Builder.Append('"')
    return $Builder.ToString()
}

function Join-TradingOSWindowsArguments {
    [CmdletBinding()]
    param([AllowEmptyCollection()][string[]]$ArgumentList = @())

    return (($ArgumentList | ForEach-Object {
        ConvertTo-TradingOSWindowsArgument -Argument $_
    }) -join ' ')
}

function Add-TradingOSUtf8LogText {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowNull()][string]$Text
    )

    if ([string]::IsNullOrEmpty($Text)) {
        return
    }
    $Directory = Split-Path -Parent $Path
    if ($Directory -and -not (Test-Path -LiteralPath $Directory -PathType Container)) {
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    }
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::AppendAllText($Path, $Text, $Utf8NoBom)
}

function Get-TradingOSLoopOwnershipMutexName {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ComponentId
    )

    if ($ComponentId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
        throw "Invalid loop component id: $ComponentId"
    }
    $CanonicalRoot = [System.IO.Path]::GetFullPath($Root).ToLowerInvariant()
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($CanonicalRoot)
    $Hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $RootHash = -join ($Hasher.ComputeHash($Bytes)[0..7] | ForEach-Object { $_.ToString('x2') })
    } finally {
        $Hasher.Dispose()
    }
    return "Local\TradingOS_Loop_${ComponentId}_$RootHash"
}

function Write-TradingOSUtf8JsonCreateNew {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Payload,
        [ValidateRange(1, 32)][int]$Depth = 8
    )

    $Directory = Split-Path -Parent $Path
    if ($Directory -and -not (Test-Path -LiteralPath $Directory -PathType Container)) {
        New-Item -ItemType Directory -Path $Directory -Force -ErrorAction Stop | Out-Null
    }
    $Json = $Payload | ConvertTo-Json -Depth $Depth
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $Bytes = $Utf8NoBom.GetBytes($Json)
    $TemporaryPath = "$Path.pending.$PID.$([guid]::NewGuid().ToString('N'))"
    $Stream = [System.IO.File]::Open(
        $TemporaryPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        $Stream.Write($Bytes, 0, $Bytes.Length)
        $Stream.Flush($true)
    } finally {
        $Stream.Dispose()
    }
    try {
        [System.IO.File]::Move($TemporaryPath, $Path)
    } finally {
        Remove-Item -LiteralPath $TemporaryPath -Force -ErrorAction SilentlyContinue
    }
}

function Write-TradingOSUtf8JsonAtomic {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Payload,
        [ValidateRange(1, 32)][int]$Depth = 8
    )

    $Directory = Split-Path -Parent $Path
    if ($Directory -and -not (Test-Path -LiteralPath $Directory -PathType Container)) {
        New-Item -ItemType Directory -Path $Directory -Force -ErrorAction Stop | Out-Null
    }
    $TemporaryPath = "$Path.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    $BackupPath = "$Path.atomic-backup.$PID.$([guid]::NewGuid().ToString('N'))"
    try {
        Write-TradingOSUtf8JsonCreateNew -Path $TemporaryPath -Payload $Payload -Depth $Depth
        $LastError = $null
        for ($Attempt = 1; $Attempt -le 5; $Attempt += 1) {
            try {
                if (Test-Path -LiteralPath $Path -PathType Leaf) {
                    [System.IO.File]::Replace($TemporaryPath, $Path, $BackupPath, $true)
                    Remove-Item -LiteralPath $BackupPath -Force -ErrorAction SilentlyContinue
                } else {
                    [System.IO.File]::Move($TemporaryPath, $Path)
                }
                return
            } catch {
                $LastError = $_.Exception
                if ($Attempt -lt 5) { Start-Sleep -Milliseconds (50 * $Attempt) }
            }
        }
        throw $LastError
    } finally {
        Remove-Item -LiteralPath $TemporaryPath, $BackupPath -Force -ErrorAction SilentlyContinue
    }
}

function Test-TradingOSExpectedScriptProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$ExpectedScriptPath,
        [AllowEmptyString()][string]$ExpectedCreationUtc = ''
    )

    if ($ProcessId -le 0) { return $false }
    try {
        $ProcessInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -OperationTimeoutSec 2 -ErrorAction Stop
        if (-not $ProcessInfo -or [string]::IsNullOrWhiteSpace([string]$ProcessInfo.CommandLine)) { return $false }
        $CanonicalScript = [System.IO.Path]::GetFullPath($ExpectedScriptPath)
        if (([string]$ProcessInfo.CommandLine).IndexOf($CanonicalScript, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
            return $false
        }
        if ($ExpectedCreationUtc) {
            $Expected = [datetimeoffset]::Parse($ExpectedCreationUtc, [System.Globalization.CultureInfo]::InvariantCulture)
            $Native = Get-Process -Id $ProcessId -ErrorAction Stop
            try { $Actual = [datetimeoffset]$Native.StartTime.ToUniversalTime() } finally { $Native.Dispose() }
            if ([math]::Abs(($Actual - $Expected).TotalSeconds) -gt 2) { return $false }
        }
        return $true
    } catch {
        return $false
    }
}

function Enter-TradingOSLoopOwnership {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ComponentId,
        [Parameter(Mandatory = $true)][string]$LockPath,
        [Parameter(Mandatory = $true)][string]$ExpectedScriptPath,
        [AllowEmptyString()][string]$LaunchAttemptId = ''
    )

    $MutexName = Get-TradingOSLoopOwnershipMutexName -Root $Root -ComponentId $ComponentId
    $Mutex = New-Object System.Threading.Mutex($false, $MutexName)
    $Acquired = $false
    try {
        try { $Acquired = $Mutex.WaitOne(0) }
        catch [System.Threading.AbandonedMutexException] { $Acquired = $true }
        if (-not $Acquired) {
            $Mutex.Dispose()
            return [pscustomobject]@{
                Acquired = $false
                ExistingPid = 0
                OwnerToken = ''
                Mutex = $null
            }
        }

        if (Test-Path -LiteralPath $LockPath) {
            $Existing = $null
            try { $Existing = Get-Content -LiteralPath $LockPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop }
            catch { $Existing = $null }
            $ExistingPid = if ($Existing -and $Existing.pid) { [int]$Existing.pid } else { 0 }
            $ExistingCreation = if ($Existing -and $Existing.process_creation_utc) {
                [string]$Existing.process_creation_utc
            } elseif ($Existing -and $Existing.process_start_utc) {
                [string]$Existing.process_start_utc
            } else { '' }
            if ($ExistingPid -gt 0 -and (Test-TradingOSExpectedScriptProcess -ProcessId $ExistingPid -ExpectedScriptPath $ExpectedScriptPath -ExpectedCreationUtc $ExistingCreation)) {
                $Mutex.ReleaseMutex()
                $Acquired = $false
                $Mutex.Dispose()
                return [pscustomobject]@{
                    Acquired = $false
                    ExistingPid = $ExistingPid
                    OwnerToken = ''
                    Mutex = $null
                }
            }
            throw "Existing loop lock is not a verified live owner; refusing to replace it: $LockPath"
        }

        $OwnerToken = [guid]::NewGuid().ToString('N')
        $Current = Get-Process -Id $PID -ErrorAction Stop
        try {
            $ProcessStartUtc = $Current.StartTime.ToUniversalTime().ToString('o')
            $ExecutablePath = [string]$Current.Path
            $SessionId = [int]$Current.SessionId
        } finally { $Current.Dispose() }
        $Payload = [ordered]@{
            schema_version = 2
            pid = $PID
            process_start_utc = $ProcessStartUtc
            process_creation_utc = $ProcessStartUtc
            owner_token = $OwnerToken
            owner_guid = $OwnerToken
            component = $ComponentId
            expected_script_path = [System.IO.Path]::GetFullPath($ExpectedScriptPath)
            script_path = [System.IO.Path]::GetFullPath($ExpectedScriptPath)
            executable_path = $ExecutablePath
            session_id = $SessionId
            mutex_name = $MutexName
            launch_attempt_id = $LaunchAttemptId
            started_at = (Get-Date).ToUniversalTime().ToString('o')
            root = [System.IO.Path]::GetFullPath($Root)
            watchdog_only = $true
            automatic_restart_allowed = $false
            can_trade = $false
        }
        Write-TradingOSUtf8JsonCreateNew -Path $LockPath -Payload $Payload -Depth 6
        return [pscustomobject]@{
            Acquired = $true
            ExistingPid = 0
            OwnerToken = $OwnerToken
            ProcessStartUtc = $ProcessStartUtc
            Mutex = $Mutex
        }
    } catch {
        if ($Acquired) { try { $Mutex.ReleaseMutex() } catch {} }
        $Mutex.Dispose()
        throw
    }
}

function Exit-TradingOSLoopOwnership {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Ownership,
        [Parameter(Mandatory = $true)][string]$LockPath
    )

    if (-not $Ownership -or -not $Ownership.Acquired -or -not $Ownership.Mutex) { return $false }
    $Removed = $false
    $ReleasePath = "$LockPath.release.$([string]$Ownership.OwnerToken)"
    try {
        if (Test-Path -LiteralPath $LockPath -PathType Leaf) {
            $BeforeRaw = [System.IO.File]::ReadAllText($LockPath)
            $Before = $BeforeRaw | ConvertFrom-Json -ErrorAction Stop
            if ($Before -and [string]$Before.owner_token -eq [string]$Ownership.OwnerToken -and [int]$Before.pid -eq $PID) {
                [System.IO.File]::Move($LockPath, $ReleasePath)
                $MovedRaw = [System.IO.File]::ReadAllText($ReleasePath)
                $Moved = $MovedRaw | ConvertFrom-Json -ErrorAction Stop
                if ($MovedRaw.Equals($BeforeRaw, [System.StringComparison]::Ordinal) -and
                    [string]$Moved.owner_token -eq [string]$Ownership.OwnerToken -and [int]$Moved.pid -eq $PID) {
                    [System.IO.File]::Delete($ReleasePath)
                    $Removed = $true
                } elseif (-not (Test-Path -LiteralPath $LockPath)) {
                    [System.IO.File]::Move($ReleasePath, $LockPath)
                }
            }
        }
        return $Removed
    } finally {
        if (Test-Path -LiteralPath $ReleasePath -PathType Leaf -ErrorAction SilentlyContinue) {
            # Never delete an unverifiable moved lock; restore only when the
            # canonical path is still free, otherwise preserve it for audit.
            if (-not (Test-Path -LiteralPath $LockPath)) {
                try { [System.IO.File]::Move($ReleasePath, $LockPath) } catch {}
            }
        }
        try { $Ownership.Mutex.ReleaseMutex() } catch {}
        $Ownership.Mutex.Dispose()
    }
}

function Stop-TradingOSChildProcessTree {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][int]$ChildProcessId,
        [AllowEmptyString()][string]$ChildProcessStartUtc = ''
    )

    if ($ChildProcessId -le 0) {
        return $true
    }

    function Test-TrackedProcessActive {
        param([int]$TrackedId, [AllowEmptyString()][string]$ExpectedStartUtc = '')
        $Tracked = Get-Process -Id $TrackedId -ErrorAction SilentlyContinue
        if (-not $Tracked) { return $false }
        try {
            if (-not $ExpectedStartUtc) { return $true }
            $Expected = [datetimeoffset]::Parse($ExpectedStartUtc, [System.Globalization.CultureInfo]::InvariantCulture)
            $Actual = [datetimeoffset]$Tracked.StartTime.ToUniversalTime()
            return [math]::Abs(($Actual - $Expected).TotalSeconds) -le 2
        } catch {
            return $false
        } finally {
            $Tracked.Dispose()
        }
    }

    function Get-BoundedProcessSnapshot {
        try {
            return @(Get-CimInstance Win32_Process -OperationTimeoutSec 2 -ErrorAction Stop | Select-Object ProcessId, ParentProcessId, CreationDate)
        } catch {
            return $null
        }
    }

    function Get-TrackedDescendants {
        param($Snapshot, [int]$RootProcessId)
        $Found = New-Object 'System.Collections.Generic.List[object]'
        if ($null -eq $Snapshot) { return $Found }
        $Pending = New-Object 'System.Collections.Generic.Queue[int]'
        $Seen = New-Object 'System.Collections.Generic.HashSet[int]'
        $Pending.Enqueue($RootProcessId)
        while ($Pending.Count -gt 0) {
            $ParentId = $Pending.Dequeue()
            foreach ($Candidate in $Snapshot) {
                $CandidateId = [int]$Candidate.ProcessId
                if ([int]$Candidate.ParentProcessId -eq $ParentId -and $CandidateId -ne $PID -and $Seen.Add($CandidateId)) {
                    $CreationUtc = ''
                    try { $CreationUtc = ([datetime]$Candidate.CreationDate).ToUniversalTime().ToString('o') } catch {}
                    $Found.Add([pscustomobject]@{ ProcessId = $CandidateId; CreationUtc = $CreationUtc })
                    $Pending.Enqueue($CandidateId)
                }
            }
        }
        return $Found
    }

    $TrackedDescendants = @{}
    $SnapshotReliable = $true
    $InitialSnapshot = Get-BoundedProcessSnapshot
    if ($null -eq $InitialSnapshot) {
        $SnapshotReliable = $false
    } else {
        foreach ($Item in @(Get-TrackedDescendants -Snapshot $InitialSnapshot -RootProcessId $ChildProcessId)) {
            $TrackedDescendants[[int]$Item.ProcessId] = [string]$Item.CreationUtc
        }
    }

    # Never address a PID after the original process handle has exited and the
    # numeric PID may have been reused. taskkill is used only while identity
    # still matches the process we launched.
    if (Test-TrackedProcessActive -TrackedId $ChildProcessId -ExpectedStartUtc $ChildProcessStartUtc) {
        $TaskKill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
        $KillInfo = New-Object System.Diagnostics.ProcessStartInfo
        $KillInfo.FileName = $TaskKill
        $KillInfo.Arguments = "/PID $ChildProcessId /T /F"
        $KillInfo.UseShellExecute = $false
        $KillInfo.CreateNoWindow = $true
        $KillInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
        $KillInfo.RedirectStandardOutput = $false
        $KillInfo.RedirectStandardError = $false
        $Killer = New-Object System.Diagnostics.Process
        try {
            $Killer.StartInfo = $KillInfo
            if ($Killer.Start() -and -not $Killer.WaitForExit(5000)) {
                try { $Killer.Kill() } catch {}
            }
        } catch {
            $SnapshotReliable = $false
        } finally {
            $Killer.Dispose()
        }
    }

    # Repeat the bounded snapshot so descendants created during termination
    # are included. Kill deepest-first and verify the exact creation identity.
    for ($Pass = 0; $Pass -lt 2; $Pass += 1) {
        $Snapshot = Get-BoundedProcessSnapshot
        if ($null -eq $Snapshot) {
            $SnapshotReliable = $false
        } else {
            $Descendants = @(Get-TrackedDescendants -Snapshot $Snapshot -RootProcessId $ChildProcessId)
            foreach ($Item in $Descendants) {
                $TrackedDescendants[[int]$Item.ProcessId] = [string]$Item.CreationUtc
            }
            for ($Index = $Descendants.Count - 1; $Index -ge 0; $Index -= 1) {
                $Item = $Descendants[$Index]
                if (Test-TrackedProcessActive -TrackedId ([int]$Item.ProcessId) -ExpectedStartUtc ([string]$Item.CreationUtc)) {
                    Stop-Process -Id ([int]$Item.ProcessId) -Force -ErrorAction SilentlyContinue
                }
            }
        }
        if (Test-TrackedProcessActive -TrackedId $ChildProcessId -ExpectedStartUtc $ChildProcessStartUtc) {
            Stop-Process -Id $ChildProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 150
    }

    $Deadline = (Get-Date).AddSeconds(3)
    do {
        $AnyTrackedAlive = Test-TrackedProcessActive -TrackedId $ChildProcessId -ExpectedStartUtc $ChildProcessStartUtc
        if (-not $AnyTrackedAlive) {
            foreach ($Entry in $TrackedDescendants.GetEnumerator()) {
                if (Test-TrackedProcessActive -TrackedId ([int]$Entry.Key) -ExpectedStartUtc ([string]$Entry.Value)) {
                    $AnyTrackedAlive = $true
                    break
                }
            }
        }
        if (-not $AnyTrackedAlive) { return $SnapshotReliable }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $Deadline)
    return $false
}

function Invoke-TradingOSChildProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [AllowEmptyCollection()][string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath,
        [ValidateRange(1, 3600)][int]$TimeoutSeconds = 90,
        [hashtable]$Environment = @{}
    )

    $StartedAt = [System.Diagnostics.Stopwatch]::StartNew()
    $ChildPid = 0
    $ChildProcessStartUtc = ''
    $TimedOut = $false
    $StreamDrainTimedOut = $false
    $TreeKillSucceeded = $true
    $Started = $false
    $Process = New-Object System.Diagnostics.Process
    $Result = $null

    try {
        if (-not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
            throw "Working directory does not exist: $WorkingDirectory"
        }

        $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        $StartInfo = New-Object System.Diagnostics.ProcessStartInfo
        $StartInfo.FileName = $FilePath
        $StartInfo.Arguments = Join-TradingOSWindowsArguments -ArgumentList $ArgumentList
        $StartInfo.WorkingDirectory = $WorkingDirectory
        $StartInfo.UseShellExecute = $false
        $StartInfo.CreateNoWindow = $true
        $StartInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
        $StartInfo.RedirectStandardOutput = $true
        $StartInfo.RedirectStandardError = $true
        $StartInfo.StandardOutputEncoding = $Utf8NoBom
        $StartInfo.StandardErrorEncoding = $Utf8NoBom
        foreach ($Name in $Environment.Keys) {
            $StartInfo.EnvironmentVariables[[string]$Name] = [string]$Environment[$Name]
        }
        # UTF-8 is part of the helper contract and cannot be weakened by a
        # caller-supplied environment override.
        $StartInfo.EnvironmentVariables['PYTHONUTF8'] = '1'
        $StartInfo.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'

        $Process.StartInfo = $StartInfo
        $Started = $Process.Start()
        if (-not $Started) {
            throw "Process start returned false: $FilePath"
        }
        $ChildPid = $Process.Id
        $ChildProcessStartUtc = $Process.StartTime.ToUniversalTime().ToString('o')
        $StdoutTask = $Process.StandardOutput.ReadToEndAsync()
        $StderrTask = $Process.StandardError.ReadToEndAsync()

        if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
            $TimedOut = $true
            $TreeKillSucceeded = Stop-TradingOSChildProcessTree -ChildProcessId $ChildPid -ChildProcessStartUtc $ChildProcessStartUtc
            $null = $Process.WaitForExit(5000)
        }

        $DrainCompleted = $false
        try {
            $DrainTasks = [System.Threading.Tasks.Task[]]@($StdoutTask, $StderrTask)
            $DrainCompleted = [System.Threading.Tasks.Task]::WaitAll($DrainTasks, 5000)
        } catch {
            $DrainCompleted = $false
        }

        if ($DrainCompleted) {
            $Stdout = $StdoutTask.GetAwaiter().GetResult()
            $Stderr = $StderrTask.GetAwaiter().GetResult()
        } else {
            $StreamDrainTimedOut = $true
            $TreeKillSucceeded = (Stop-TradingOSChildProcessTree -ChildProcessId $ChildPid -ChildProcessStartUtc $ChildProcessStartUtc) -and $TreeKillSucceeded
            try { $Process.StandardOutput.Close() } catch {}
            try { $Process.StandardError.Close() } catch {}
            $Stdout = ""
            $Stderr = "[{0}] hidden child stream drain timed out for PID {1}{2}" -f (
                (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'),
                $ChildPid,
                [Environment]::NewLine
            )
        }
        Add-TradingOSUtf8LogText -Path $StdoutPath -Text $Stdout
        Add-TradingOSUtf8LogText -Path $StderrPath -Text $Stderr

        $ExitCode = if ($TimedOut) {
            124
        } elseif ($StreamDrainTimedOut -or -not $TreeKillSucceeded) {
            125
        } elseif ($Process.HasExited) {
            [int]$Process.ExitCode
        } else {
            125
        }
        $Result = [pscustomobject]@{
            Started = $Started
            ProcessId = $ChildPid
            ExitCode = $ExitCode
            TimedOut = $TimedOut
            StreamDrainTimedOut = $StreamDrainTimedOut
            TreeKillSucceeded = $TreeKillSucceeded
            DurationMs = [int][math]::Round($StartedAt.Elapsed.TotalMilliseconds)
        }
    } catch {
        if ($Started -and $ChildPid -gt 0) {
            $TreeKillSucceeded = Stop-TradingOSChildProcessTree -ChildProcessId $ChildPid -ChildProcessStartUtc $ChildProcessStartUtc
        }
        $Message = "[{0}] hidden child launch failed: {1}{2}" -f (
            (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'),
            $_.Exception.Message,
            [Environment]::NewLine
        )
        Add-TradingOSUtf8LogText -Path $StderrPath -Text $Message
        $Result = [pscustomobject]@{
            Started = $Started
            ProcessId = $ChildPid
            ExitCode = 125
            TimedOut = $TimedOut
            StreamDrainTimedOut = $StreamDrainTimedOut
            TreeKillSucceeded = $TreeKillSucceeded
            DurationMs = [int][math]::Round($StartedAt.Elapsed.TotalMilliseconds)
        }
    } finally {
        $StartedAt.Stop()
        $Process.Dispose()
    }

    return $Result
}
