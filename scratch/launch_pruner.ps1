$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$script = 'C:\dev\DiscrepancyRecords\scratch\check_and_prune.py'
$log = 'C:\dev\DiscrepancyRecords\scratch\pruner.log'
$cmd = ('cmd.exe /c ""{0}" "{1}" wave274tot 16384 3 >> "{2}" 2>&1"' -f $py, $script, $log)
$r = ([wmiclass]'Win32_Process').Create($cmd)
Write-Output ("WMI Create returned {0}, PID {1}" -f $r.ReturnValue, $r.ProcessId)
