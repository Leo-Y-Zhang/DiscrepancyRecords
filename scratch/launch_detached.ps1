$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$script = 'C:\dev\DiscrepancyRecords\scratch\run_campaign.py'
$log = 'C:\dev\DiscrepancyRecords\scratch\campaign.log'
$cmd = ('cmd.exe /c ""{0}" "{1}" >> "{2}" 2>&1"' -f $py, $script, $log)
$r = ([wmiclass]'Win32_Process').Create($cmd)
Write-Output ("WMI Create returned {0}, PID {1}" -f $r.ReturnValue, $r.ProcessId)
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
Write-Output "sleep timers on AC disabled (rc=$LASTEXITCODE)"
