$targets = Get-CimInstance Win32_Process | Where-Object {
  ($_.CommandLine -like '*check_and_prune*') -or ($_.Name -eq 'drat-trim-rebuilt.exe')
}
foreach ($p in $targets) {
  Write-Output ("stopping PID {0} ({1})" -f $p.ProcessId, $p.Name)
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 3
$left = (Get-CimInstance Win32_Process | Where-Object {
  ($_.CommandLine -like '*check_and_prune*') -or ($_.Name -eq 'drat-trim-rebuilt.exe')
} | Measure-Object).Count
Write-Output "remaining checker processes: $left"
$k = (Get-Process kissat -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Output "kissat workers still running: $k"
