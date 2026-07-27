param(
    [int]$SleepSeconds = 30,
    [ValidateSet("root", "intermediate", "grandchild")][string]$Role = "root",
    [string]$GrandchildPidPath = ""
)

$ErrorActionPreference = "Stop"

if ($Role -eq "root" -and $GrandchildPidPath) {
    $IntermediateArgs = @(
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-Role", "intermediate",
        "-SleepSeconds", [string]$SleepSeconds,
        "-GrandchildPidPath", $GrandchildPidPath
    )
    $Intermediate = Start-Process -FilePath "powershell.exe" -ArgumentList $IntermediateArgs -WindowStyle Hidden -PassThru
    $Intermediate.WaitForExit()
} elseif ($Role -eq "intermediate") {
    $GrandchildArgs = @(
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-Role", "grandchild",
        "-SleepSeconds", [string]$SleepSeconds
    )
    $Grandchild = Start-Process -FilePath "powershell.exe" -ArgumentList $GrandchildArgs -WindowStyle Hidden -PassThru
    [string]$Grandchild.Id | Set-Content -LiteralPath $GrandchildPidPath -Encoding ASCII
    exit 0
}

Start-Sleep -Seconds $SleepSeconds
