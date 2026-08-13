[CmdletBinding(DefaultParameterSetName='Interactive')]
param(
    [Parameter(Mandatory=$true)][string]$CertificatePath,
    [Parameter(Mandatory=$true)][string]$SecurityConfigPath,
    [string]$DestinationAlias = 'ops_primary',
    [string]$DestinationEnv = 'TRADINGOS_TELEGRAM_CHAT_ID',
    [Parameter(Mandatory=$true)][string]$OutDir,
    [Parameter(ParameterSetName='UpdateJson',Mandatory=$true)][string]$TelegramUpdateJson,
    [string]$PythonExe = 'python'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tool = Join-Path $repoRoot 'tools\tradingos_destination_intake.py'
if (-not (Test-Path -LiteralPath $tool)) { throw 'TradingOS destination intake tool not found.' }
$cert = (Resolve-Path -LiteralPath $CertificatePath).Path
$cfg = (Resolve-Path -LiteralPath $SecurityConfigPath).Path
$tempEnv = 'TRADINGOS_DESTINATION_INPUT_TMP'
$rawDestination = $null
try {
    $cliArgs = @(
        $tool,
        '--certificate', $cert,
        '--security-config', $cfg,
        '--destination-alias', $DestinationAlias,
        '--destination-env', $DestinationEnv,
        '--out-dir', $OutDir
    )
    if ($PSCmdlet.ParameterSetName -eq 'UpdateJson') {
        $update = (Resolve-Path -LiteralPath $TelegramUpdateJson).Path
        $cliArgs += @('--telegram-update-json', $update)
    } else {
        $rawDestination = Read-Host 'Telegram chat id (kept only in this process)'
        if ([string]::IsNullOrWhiteSpace($rawDestination)) { throw 'Telegram chat id is required.' }
        [Environment]::SetEnvironmentVariable($tempEnv, $rawDestination, 'Process')
        $cliArgs += @('--destination-value-env', $tempEnv)
    }
    & $PythonExe @cliArgs
    if ($LASTEXITCODE -ne 0) { throw "Destination intake failed with exit code $LASTEXITCODE." }
    $receiptPath = Join-Path $OutDir 'destination_intake_receipt.json'
    $packagePath = Join-Path $OutDir 'binding_package.json'
    if (-not (Test-Path -LiteralPath $receiptPath) -or -not (Test-Path -LiteralPath $packagePath)) { throw 'Expected HASH_READY outputs are missing.' }
    $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    $package = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
    if ($receipt.status -ne 'HASH_READY' -or $package.status -ne 'HASH_READY') { throw 'Destination intake did not reach HASH_READY.' }
    if ($receipt.destination_sha256 -notmatch '^[0-9a-f]{64}$') { throw 'Invalid destination SHA-256.' }
    if ($receipt.contract.raw_destination_persisted -ne $false) { throw 'Raw destination persistence contract violated.' }
    if ($receipt.contract.binding_apply_performed -ne $false -or $package.contract.binding_apply_performed -ne $false) { throw 'Binding apply contract violated.' }
    if ($receipt.contract.network_call -ne $false -or $package.contract.network_call -ne $false) { throw 'Network-call contract violated.' }
    if ($receipt.safety.deploy_permission -ne 'DENY' -or $package.safety.deploy_permission -ne 'DENY') { throw 'Deploy permission contract violated.' }
    [pscustomobject]@{
        result = 'PASS'
        status = 'HASH_READY'
        intake_id = $receipt.intake_id
        package_id = $package.package_id
        destination_sha256 = $receipt.destination_sha256
        raw_destination_persisted = $false
        binding_apply_performed = $false
        network_call = $false
        deploy_permission = 'DENY'
        output_directory = (Resolve-Path -LiteralPath $OutDir).Path
    } | ConvertTo-Json -Depth 4
} finally {
    [Environment]::SetEnvironmentVariable($tempEnv, $null, 'Process')
    $rawDestination = $null
}
