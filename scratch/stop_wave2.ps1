$parents = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*cube_wave2.py wave wave274 *' }
foreach ($p in $parents) {
  $kids = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $p.ProcessId }
  foreach ($k in $kids) { Stop-Process -Id $k.ProcessId -Force -ErrorAction SilentlyContinue }
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  Write-Output ("stopped wave2 driver PID {0} and {1} child(ren)" -f $p.ProcessId, $kids.Count)
}
Start-Sleep -Seconds 3
# kissat workers of wave2 are grandchildren via the pool; sweep any solving the seqcount base
$orphans = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -eq 'kissat.exe' -and $_.CommandLine -like '*wave274\cube_*'
}
foreach ($o in $orphans) {
  Stop-Process -Id $o.ProcessId -Force -ErrorAction SilentlyContinue
  Write-Output ("stopped orphan seqcount kissat PID {0}" -f $o.ProcessId)
}
Start-Sleep -Seconds 2
Write-Output ("kissat now: " + (Get-Process kissat -ErrorAction SilentlyContinue | Measure-Object).Count)
