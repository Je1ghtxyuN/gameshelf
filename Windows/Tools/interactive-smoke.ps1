param([string]$Script='smoke.ps1',[string]$Log='interactive-smoke.log')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
. (Join-Path $PSScriptRoot 'build-environment.ps1')
$arguments='-NoProfile -ExecutionPolicy Bypass -Command "& { & '+[char]39+(Join-Path $PSScriptRoot $Script)+[char]39+' *> '+[char]39+(Join-Path $dev $Log)+[char]39+' }"'
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
try {
 Register-ScheduledTask -TaskName 'GameShelfDev-Smoke' -Action $action -Principal $principal -Force | Out-Null
 Start-ScheduledTask -TaskName 'GameShelfDev-Smoke'
 Start-Sleep -Seconds 1
 for($n=0;$n -lt 55;$n++){if((Get-ScheduledTask -TaskName 'GameShelfDev-Smoke').State -ne 'Running'){break};Start-Sleep -Seconds 1}
 Get-ScheduledTaskInfo -TaskName 'GameShelfDev-Smoke' | Select-Object LastTaskResult
 Get-Content (Join-Path $dev $Log) -ErrorAction SilentlyContinue
} finally {
 Unregister-ScheduledTask -TaskName 'GameShelfDev-Smoke' -Confirm:$false -ErrorAction SilentlyContinue
}
