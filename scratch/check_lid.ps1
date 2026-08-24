$guid = '381b4222-f694-41f0-9685-ff5bb260df2e'
$sub  = '4f971e89-eebd-4455-a8de-9e59040e7347'
$set  = '5ca83367-6e45-459f-a27b-476b1d01c936'
$out = & powercfg @('/q', $guid, $sub, $set) 2>&1
Write-Output ($out | Out-String)
