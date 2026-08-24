$active = (powercfg /getactivescheme)
Write-Output "active: $active"
if ($active -match '([0-9a-fA-F-]{36})') { $guid = $Matches[1] } else { Write-Output 'no scheme guid'; exit 1 }
$SUB_BUTTONS = '4f971e89-eebd-4455-a8de-9e59040e7347'
$LIDACTION   = '5ca83367-6e45-459f-a27b-476b1d01c936'
& powercfg /setacvalueindex $guid $SUB_BUTTONS $LIDACTION 0
Write-Output "setacvalueindex exit=$LASTEXITCODE"
& powercfg /setactive $guid
Write-Output "setactive exit=$LASTEXITCODE"
$q = (& powercfg /q $guid $SUB_BUTTONS $LIDACTION) -join "`n"
$lines = $q -split "`n" | Where-Object { $_ -match 'Index|Setting|Power Setting GUID' }
Write-Output ($lines -join "`n")
