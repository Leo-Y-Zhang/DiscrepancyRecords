$pats = 'run_campaign', 'cube_wave2', 'sample_prune', 'check_and_prune', 'gzip_loop'
$procs = Get-CimInstance Win32_Process | Where-Object {
  $n = $_.Name
  $c = $_.CommandLine
  ($n -eq 'kissat.exe') -or ($n -eq 'drat-trim-rebuilt.exe') -or
  ($c -and ($pats | Where-Object { $c -like ('*' + $_ + '*') }))
}
$groups = $procs | Group-Object Name | Sort-Object Name
foreach ($g in $groups) { Write-Output ("{0,-26} {1}" -f $g.Name, $g.Count) }
foreach ($p in $procs) {
  if ($p.Name -eq 'python.exe' -and $p.CommandLine) {
    $c = $p.CommandLine
    $tail = $c.Substring([Math]::Max(0, $c.Length - 70))
    Write-Output ("  python PID {0}: ...{1}" -f $p.ProcessId, $tail)
  }
}
