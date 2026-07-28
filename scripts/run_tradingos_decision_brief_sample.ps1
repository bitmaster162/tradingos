[CmdletBinding()]
param(
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$output = Join-Path $root "_dl\decision_brief_sample"
$tool = Join-Path $root "tools\tradingos_decision_brief.py"
$sample = Join-Path $root "examples\tradingos_decision_brief\market_snapshot.sample.json"
$pilot = Join-Path $output "pilot_log.jsonl"
$html = Join-Path $output "brief.html"

$pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($pyLauncher) {
    & $pyLauncher.Source -3.11 $tool `
        --input $sample `
        --out-dir $output `
        --now "2026-07-29T00:30:00Z" `
        --pilot-log $pilot `
        --pilot-day "DAY_1"
}
else {
    $python = Get-Command python.exe -ErrorAction Stop
    & $python.Source $tool `
        --input $sample `
        --out-dir $output `
        --now "2026-07-29T00:30:00Z" `
        --pilot-log $pilot `
        --pilot-day "DAY_1"
}
if ($LASTEXITCODE -ne 0) {
    throw "Decision Brief generator exited with code $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $html)) {
    throw "Expected HTML output was not created: $html"
}

$brief = Get-Content -LiteralPath (Join-Path $output "brief.json") -Raw | ConvertFrom-Json
$receipt = [ordered]@{
    schema_version = 1
    result = "PASS"
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    launcher = "RUN_DECISION_BRIEF_SAMPLE.cmd"
    output_directory = $output
    brief_id = $brief.brief_id
    status = $brief.status
    decision = $brief.decision.stance
    html_exists = $true
    can_trade = $false
    capital_permission = "DENY"
}
$receipt | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $output "RUN_RECEIPT.json") -Encoding utf8

Write-Host ""
Write-Host "Decision Brief created:" -ForegroundColor Green
Write-Host "  $html"
Write-Host "Decision: $($brief.decision.stance) (read-only; not an order)"

if (-not $NoOpen) {
    Start-Process -FilePath $html
}
