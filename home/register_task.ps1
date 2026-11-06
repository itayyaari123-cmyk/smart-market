# Creates a daily Windows scheduled task: every day at 10:00, or as soon as the computer is on if missed
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (& py -3.12 -c "import sys;print(sys.executable)").Trim()
$pyw = $py -replace 'python\.exe$', 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$dir\home_sync.py`"" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At 10:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName "Smart Basket daily sync" -Action $action -Trigger $trigger -Settings $settings `
  -Description "Downloads supermarket prices and sends them to the Smart Basket website" -Force | Out-Null
Write-Host ""
Write-Host "Scheduled task created: every day at 10:00 (or as soon as the computer is on)." -ForegroundColor Green
Write-Host "It runs in the background, no window. Log: $dir\sync.log" -ForegroundColor Green
