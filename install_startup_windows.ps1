# Run this yourself to register continuous discovery at Windows login.
# It does not start applications or bypass security checks.
$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$Launcher = Join-Path $ProjectRoot 'start_background.bat'
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $PythonPath)) { throw 'Run setup_windows.bat first.' }
$TaskName = 'Rinkle JobPilot Discovery'
$AccountName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/d /c ""{0}""' -f $Launcher) -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $AccountName
$Principal = New-ScheduledTaskPrincipal -UserId $AccountName -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description 'Read job feeds, import queue files, prepare resumes, update Excel. Never submit applications.' -Force | Out-Null
Write-Host 'Installed: Rinkle JobPilot Discovery. Starts at your next Windows login.'
Write-Host 'To start now, double-click start_background.bat.'
Write-Host 'To uninstall: Unregister-ScheduledTask -TaskName "Rinkle JobPilot Discovery" -Confirm:$false'
