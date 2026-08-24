$cmds = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' }
foreach ($c in $cmds) {
  $kids = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $c.ProcessId }
  $kidNames = ($kids | ForEach-Object { $_.Name }) -join ','
  $line = $c.CommandLine
  if ($line) {
    # show just the script name and its arguments, not the full paths
    $short = ($line -replace '.*scratch\\', '') -replace '" *>>.*', ''
  } else { $short = '(no command line)' }
  Write-Output ("cmd PID {0} -> child [{1}] : {2}" -f $c.ProcessId, $kidNames, $short)
}
Write-Output ("total cmd.exe windows: " + ($cmds | Measure-Object).Count)
