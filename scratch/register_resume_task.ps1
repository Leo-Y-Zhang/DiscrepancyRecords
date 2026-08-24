# Register resume_campaign.ps1 to run at logon, so a repeat of the 22:46
# logoff costs minutes instead of the ~4.5 h of wave it cost tonight.
# Non-admin: an at-logon task for the current user needs no elevation.
$ErrorActionPreference = 'Stop'
$name = 'ErdosCampaignResume'
$script = 'C:\dev\DiscrepancyRecords\scratch\resume_campaign.ps1'
$tr = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $script + '"'

schtasks.exe /create /tn $name /tr $tr /sc onlogon /f
Write-Output ("schtasks rc = " + $LASTEXITCODE)
schtasks.exe /query /tn $name /fo LIST | Select-String 'TaskName|Status|Next|Task To Run|Logon Mode'
