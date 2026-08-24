$targets = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*gzip_loop*' }
foreach ($p in $targets) {
  Write-Output ("stopping gzip_loop PID {0}" -f $p.ProcessId)
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
$left = (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*gzip_loop*' } | Measure-Object).Count
Write-Output "gzip_loop remaining: $left"
