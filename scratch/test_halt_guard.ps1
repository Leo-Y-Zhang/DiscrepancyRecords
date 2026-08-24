# Prove the SAT-halt parsing in resume_campaign.ps1 does what it claims, on
# synthetic logs. Two cases, and the second is the one that matters: a HALT from
# an EARLIER run must NOT block a restart of a later one, or a single historic
# SAT line would freeze the campaign forever.
$ErrorActionPreference = 'Stop'
$tmp = Join-Path $env:TEMP 'haltguard'
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

function Detects($lines) {
  $starts = @(0..($lines.Count - 1) | Where-Object { $lines[$_] -like '*campaign orchestrator starting*' })
  $from = if ($starts.Count) { $starts[-1] } else { 0 }
  $halt = @($lines[$from..($lines.Count - 1)] | Where-Object { $_ -match 'HALT: .*SAT verdict' })
  return $halt.Count -gt 0
}

$live = @(
  '[23:00:00] === campaign orchestrator starting ==='
  '[23:10:00] 50/16384 in 0.05h | UNSAT 50 SAT 0 UNK 0'
  '[23:20:00] HALT: 1 SAT verdict(s) in wave274tot - witness candidate'
)
$historic = @(
  '[20:00:00] === campaign orchestrator starting ==='
  '[20:30:00] HALT: 1 SAT verdict(s) in wave274tot - witness candidate'
  '[23:00:00] === campaign orchestrator starting ==='
  '[23:10:00] 50/16384 in 0.05h | UNSAT 50 SAT 0 UNK 0'
)
$noHalt = @(
  '[23:00:00] === campaign orchestrator starting ==='
  '[23:10:00] 50/16384 in 0.05h | UNSAT 50 SAT 0 UNK 0'
)
$hardCubes = @(
  '[23:00:00] === campaign orchestrator starting ==='
  '[23:20:00] HALT: only 900/16384 UNSAT after retries - hard cubes remain'
)

$cases = @(
  @{ n = 'SAT halt in the CURRENT run     -> block restart'; l = $live;      want = $true }
  @{ n = 'SAT halt in an EARLIER run only -> allow restart'; l = $historic;  want = $false }
  @{ n = 'no halt at all                  -> allow restart'; l = $noHalt;    want = $false }
  @{ n = 'crash-shaped halt (no SAT)      -> allow restart'; l = $hardCubes; want = $false }
)
$fail = 0
foreach ($c in $cases) {
  $got = Detects $c.l
  $ok = ($got -eq $c.want)
  if (-not $ok) { $fail++ }
  ("{0}  {1}  (blocked={2})" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $c.n, $got)
}
("{0} case(s) failed" -f $fail)
exit $fail
