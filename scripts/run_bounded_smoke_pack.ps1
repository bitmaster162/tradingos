param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $root "_dl\\smoke"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

if (-not $PythonExe) {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $PythonExe = "python"
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $probe = (& py -0p 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0 -or $probe -match "No Installed Pythons Found") {
            throw "Python launcher found, but no installed interpreter is available. Install Python 3.11+ first."
        }
        $PythonExe = "py -3"
    }
    else {
        throw "No Python interpreter found. Install Python 3.11+ and rerun."
    }
}

function Invoke-Python {
    param([string]$CommandLine)
    Write-Host ">>> $CommandLine"
    Invoke-Expression "$PythonExe $CommandLine"
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $CommandLine"
    }
}

Push-Location $root
try {
    Invoke-Python 'portable/MAX_ops_preflight.py --config configs/MAX_PIPELINE_CONFIG_SMOKE.json --out _dl/smoke/MAX_OPS_PREFLIGHT.json'
    Invoke-Python 'scripts/validate_bitevo_alerts.py bitevo/examples/alert_entry_example.json'
    Invoke-Python 'v7/rule_engine_template.py v7/regex_test_sample.txt --rules v7/alerts_rules.json --pretty > _dl/smoke/rule_hits.json'
    Invoke-Python 'v7/risk_of_ruin_sim.py 0.45 2.0 -1.0 0.5 1000 20000 > _dl/smoke/risk_of_ruin.json'
    Write-Host "Smoke pack completed. Outputs in _dl/smoke/"
}
finally {
    Pop-Location
}
