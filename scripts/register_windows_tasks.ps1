param(
    [string]$PythonExe,
    [switch]$IncludeAlerts
)

$ErrorActionPreference = "Stop"

if (-not $PythonExe) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $PythonExe = $pythonCommand.Source
}

$runnerPath = Join-Path $PSScriptRoot "run_scheduled_job.ps1"
$currentUser = "{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

function New-TccAction {
    param(
        [string]$Job,
        [string]$CycleLabel
    )

    $parts = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $runnerPath),
        "-PythonExe", ('"{0}"' -f $PythonExe),
        "-Job", $Job
    )

    if ($CycleLabel) {
        $parts += @("-CycleLabel", $CycleLabel)
    }

    $arguments = $parts -join " "
    New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
}

function Register-TccTask {
    param(
        [string]$TaskName,
        [Microsoft.Management.Infrastructure.CimInstance[]]$Trigger,
        [string]$Description,
        [string]$Job,
        [string]$CycleLabel
    )

    $action = New-TccAction -Job $Job -CycleLabel $CycleLabel
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $Trigger `
        -Principal $principal `
        -Settings $settings `
        -Description $Description `
        -Force | Out-Null
}

Register-TccTask `
    -TaskName "TCC Daily 0700" `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 7:00AM) `
    -Description "Actualizacion TCC y reporte diario 07:00" `
    -Job "daily" `
    -CycleLabel "0700"

Register-TccTask `
    -TaskName "TCC Daily 1200" `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 12:00PM) `
    -Description "Actualizacion TCC y reporte diario 12:00" `
    -Job "daily" `
    -CycleLabel "1200"

Register-TccTask `
    -TaskName "TCC Daily 1600" `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 4:00PM) `
    -Description "Actualizacion TCC y reporte diario 16:00" `
    -Job "daily" `
    -CycleLabel "1600"

Register-TccTask `
    -TaskName "TCC Weekly Monday" `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 7:10AM) `
    -Description "Consolidado semanal TCC" `
    -Job "weekly" `
    -CycleLabel ""

Register-TccTask `
    -TaskName "TCC Cleanup Monday" `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 6:00AM) `
    -Description "Limpieza de guias antiguas TCC" `
    -Job "cleanup" `
    -CycleLabel ""

if ($IncludeAlerts) {
    $alertsTrigger = New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date "2000-01-01 00:00") `
        -RepetitionInterval (New-TimeSpan -Minutes 30) `
        -RepetitionDuration (New-TimeSpan -Days 9999)

    Register-TccTask `
        -TaskName "TCC Alerts 30m" `
        -Trigger $alertsTrigger `
        -Description "Verificacion de alertas cada 30 minutos" `
        -Job "alerts" `
        -CycleLabel ""
}

foreach ($taskName in @(
    "TCC Daily 0700",
    "TCC Daily 1200",
    "TCC Daily 1600",
    "TCC Weekly Monday",
    "TCC Cleanup Monday",
    "TCC Alerts 30m"
)) {
    try {
        schtasks.exe /Query /TN $taskName
    } catch {
    }
}
