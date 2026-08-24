$os = Get-CimInstance Win32_OperatingSystem
$totalMB = [math]::Round($os.TotalVisibleMemorySize / 1KB)
$freeMB  = [math]::Round($os.FreePhysicalMemory / 1KB)
Write-Output ("RAM total {0} MB, free {1} MB, used {2} MB" -f $totalMB, $freeMB, ($totalMB - $freeMB))
$k = Get-Process kissat -ErrorAction SilentlyContinue
if ($k) {
  $sum = ($k | Measure-Object WorkingSet64 -Sum).Sum / 1MB
  $max = ($k | Measure-Object WorkingSet64 -Maximum).Maximum / 1MB
  Write-Output ("kissat: {0} procs, {1} MB total, largest {2} MB" -f $k.Count, [math]::Round($sum), [math]::Round($max))
}
$pf = Get-CimInstance Win32_PageFileUsage -ErrorAction SilentlyContinue
if ($pf) { Write-Output ("pagefile in use: {0} MB of {1} MB" -f $pf.CurrentUsage, $pf.AllocatedBaseSize) }
