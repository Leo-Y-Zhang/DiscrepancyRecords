$procs = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match 'node|claude' -and $_.CommandLine -match 'claude'
}
foreach ($p in $procs) {
  $c = $p.CommandLine
  if ($c.Length -gt 150) { $c = $c.Substring(0, 150) }
  Write-Output ("PID {0} [{1}] {2}" -f $p.ProcessId, $p.Name, $c)
}
Write-Output ("count: " + ($procs | Measure-Object).Count)
