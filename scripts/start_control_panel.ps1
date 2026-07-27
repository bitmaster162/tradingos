param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8765,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $root "_dl\control_panel"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

if (-not $PythonExe) {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $PythonExe = "python"
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $PythonExe = "py -3"
    }
    else {
        throw "No Python interpreter found. Install Python 3.11+ and rerun."
    }
}

Push-Location $root
try {
    & $PythonExe "ops/control_panel/control_panel.py" --host $HostAddress --port $Port
}
finally {
    Pop-Location
}
