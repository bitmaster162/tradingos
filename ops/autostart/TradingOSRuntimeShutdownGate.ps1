function Get-TradingOSRuntimeShutdownSentinelPath {
    param([Parameter(Mandatory = $true)][string]$Root)
    return Join-Path ([System.IO.Path]::GetFullPath($Root)) "logs\runtime_shutdown.request.json"
}

function Get-TradingOSRuntimeShutdownStartMarkerPath {
    param([Parameter(Mandatory = $true)][string]$Root)
    return Join-Path ([System.IO.Path]::GetFullPath($Root)) "logs\runtime_shutdown.starting.json"
}

function Get-TradingOSRuntimeShutdownAttemptReservationPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$AttemptId
    )
    $AttemptToken = ([guid]$AttemptId).ToString('N')
    return Join-Path ([System.IO.Path]::GetFullPath($Root)) "logs\runtime_attempts\$AttemptToken\reservation.json"
}

function Test-TradingOSRuntimeShutdownRequested {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [string]$AllowedAttemptId = ''
    )

    try {
        $CanonicalRoot = [System.IO.Path]::GetFullPath($Root)
        $SentinelPath = Get-TradingOSRuntimeShutdownSentinelPath -Root $CanonicalRoot
        $SentinelExists = Test-Path -LiteralPath $SentinelPath -ErrorAction Stop
        if (-not $SentinelExists) { return $false }
        if (-not (Test-Path -LiteralPath $SentinelPath -PathType Leaf -ErrorAction Stop)) { return $true }
        if (-not $AllowedAttemptId) { return $true }

        $AllowedAttemptId = ([guid]$AllowedAttemptId).ToString()
        if ([guid]$AllowedAttemptId -eq [guid]::Empty) { return $true }

        $MarkerPath = Get-TradingOSRuntimeShutdownStartMarkerPath -Root $CanonicalRoot
        if (-not (Test-Path -LiteralPath $MarkerPath -PathType Leaf -ErrorAction Stop)) { return $true }
        $Marker = Get-Content -LiteralPath $MarkerPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $MarkerGeneratedAt = [datetimeoffset]::ParseExact(
            [string]$Marker.generated_at,
            'o',
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::RoundtripKind
        )
        $MarkerAttemptId = ([guid][string]$Marker.attempt_id).ToString()
        $MarkerInvocationId = ([guid][string]$Marker.invocation_id).ToString()
        $MarkerValid = ($Marker.schema_version -is [int]) -and [int]$Marker.schema_version -eq 1 -and
            $MarkerAttemptId -eq $AllowedAttemptId -and
            [guid]$MarkerInvocationId -ne [guid]::Empty -and
            [System.IO.Path]::GetFullPath([string]$Marker.root).Equals($CanonicalRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
            ($Marker.live_trading_locked -is [bool]) -and [bool]$Marker.live_trading_locked -and
            ($Marker.can_trade -is [bool]) -and -not [bool]$Marker.can_trade
        if (-not $MarkerValid) { return $true }

        $ReservationPath = Get-TradingOSRuntimeShutdownAttemptReservationPath -Root $CanonicalRoot -AttemptId $AllowedAttemptId
        if (-not (Test-Path -LiteralPath $ReservationPath -PathType Leaf -ErrorAction Stop)) { return $true }
        $Reservation = Get-Content -LiteralPath $ReservationPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $ReservationGeneratedAt = [datetimeoffset]::ParseExact(
            [string]$Reservation.generated_at,
            'o',
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::RoundtripKind
        )
        $OwnerCreationUtc = [datetimeoffset]::ParseExact(
            [string]$Reservation.owner_process_creation_utc,
            'o',
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::RoundtripKind
        )
        $ReservationValid = ($Reservation.schema_version -is [int]) -and [int]$Reservation.schema_version -eq 1 -and
            ([guid][string]$Reservation.attempt_id).ToString() -eq $AllowedAttemptId -and
            ([guid][string]$Reservation.invocation_id).ToString() -eq $MarkerInvocationId -and
            [System.IO.Path]::GetFullPath([string]$Reservation.root).Equals($CanonicalRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
            [string]$Reservation.state -eq 'reserved' -and
            ($Reservation.owner_pid -is [int]) -and [int]$Reservation.owner_pid -gt 0 -and
            ($Reservation.session_id -is [int]) -and [int]$Reservation.session_id -ge 0 -and
            ($Reservation.live_trading_locked -is [bool]) -and [bool]$Reservation.live_trading_locked -and
            ($Reservation.can_trade -is [bool]) -and -not [bool]$Reservation.can_trade -and
            $MarkerGeneratedAt -ge $ReservationGeneratedAt
        if (-not $ReservationValid) { return $true }

        $OwnerProcess = Get-Process -Id ([int]$Reservation.owner_pid) -ErrorAction Stop
        $CurrentProcess = Get-Process -Id $PID -ErrorAction Stop
        try {
            $OwnerStartUtc = [datetimeoffset]$OwnerProcess.StartTime.ToUniversalTime()
            $OwnerSessionId = [int]$OwnerProcess.SessionId
            $CurrentSessionId = [int]$CurrentProcess.SessionId
            $OwnerMatches = $OwnerStartUtc.UtcDateTime.Ticks -eq $OwnerCreationUtc.UtcDateTime.Ticks -and
                $OwnerSessionId -eq [int]$Reservation.session_id -and
                $CurrentSessionId -eq [int]$Reservation.session_id
        } finally {
            $OwnerProcess.Dispose()
            $CurrentProcess.Dispose()
        }
        return -not [bool]$OwnerMatches
    } catch {
        return $true
    }
}
