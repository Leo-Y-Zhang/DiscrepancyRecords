#!/usr/bin/env bash
# Wait for the a(17) campaign to reach a state that needs a person, then exit
# with a code that says which. Run in the background so the session is woken by
# the event instead of polling for days.
#
#   0  DONE.json - the compute finished, harvest it
#   2  a proof failed to verify - stop everything
#   3  a SAT cube - witness candidate, a(17)=274 is in question
#   4  nothing has been running for 15 minutes - the watchdog itself failed
S="C:/dev/DiscrepancyRecords/scratch"
dead=0

while true; do
  if [ -f "$S/DONE.json" ]; then
    echo "CAMPAIGN COMPLETE: DONE.json written $(date)"
    exit 0
  fi
  if [ -f "$S/wave274tot/CHECK_FAILURES.jsonl" ]; then
    echo "CHECK FAILURE: a proof did not verify"
    head -3 "$S/wave274tot/CHECK_FAILURES.jsonl"
    exit 2
  fi
  # Only a SAT halt from the CURRENT orchestrator run counts; an old one is
  # history, not a live result.
  if tac "$S/campaign.log" 2>/dev/null | sed '/campaign orchestrator starting/q' |
       grep -q 'HALT: .*SAT verdict'; then
    echo "SAT VERDICT: witness candidate - the a(17)=274 hypothesis is in question"
    grep 'HALT:' "$S/campaign.log" | tail -2
    exit 3
  fi

  n=$(powershell.exe -NoProfile -Command "(Get-Process kissat -ErrorAction SilentlyContinue | Measure-Object).Count" 2>/dev/null | tr -d '\r')
  if [ "${n:-0}" -lt 1 ]; then
    dead=$((dead + 1))
    if [ "$dead" -ge 3 ]; then
      echo "STALLED: no solver processes for 15 minutes - the watchdog did not recover it"
      tail -5 "$S/watchdog.log"
      exit 4
    fi
  else
    dead=0
  fi

  sleep 300
done
