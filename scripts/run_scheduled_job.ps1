param(
    [Parameter(Mandatory = $true)]
    [string]$Job,
    [string]$CycleLabel,
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"

function Import-DotEnvFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }

        $name, $value = $line -split "=", 2
        $name = $name.Trim()
        $value = $value.Trim().Trim('"')

        if ($name) {
            [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

$backendRoot = Split-Path -Parent $PSScriptRoot
$pythonScript = Join-Path $backendRoot "scripts\run_job.py"
$logsDir = Join-Path $backendRoot "logs\scheduled"
$label = if ($CycleLabel) { "$Job`_$CycleLabel" } else { $Job }
$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$logFile = Join-Path $logsDir "$timestamp`_$label.log"

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

Import-DotEnvFile (Join-Path $backendRoot ".env")
Import-DotEnvFile (Join-Path $backendRoot ".env.local")

[System.Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Process")
[System.Environment]::SetEnvironmentVariable("APP_ENV", "production", "Process")

$arguments = @($pythonScript, $Job)
if ($CycleLabel) {
    $arguments += $CycleLabel
}

Push-Location $backendRoot
try {
    & $PythonExe @arguments 2>&1 | Tee-Object -FilePath $logFile -Append
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    throw "Scheduled job failed with exit code $exitCode. See $logFile"
}
