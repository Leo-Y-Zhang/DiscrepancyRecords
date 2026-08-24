$procs = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -eq 'kissat.exe' -or ($_.CommandLine -like '*run_campaign*') -or ($_.CommandLine -like '*cube_wave2*') -or ($_.CommandLine -like '*gzip_loop*')
}
Write-Output ("alive: " + (($procs | ForEach-Object { $_.Name }) -join ' '))
Write-Output ("kissat count: " + (($procs | Where-Object { $_.Name -eq 'kissat.exe' }) | Measure-Object).Count)
