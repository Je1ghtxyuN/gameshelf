param([string]$CompiledCheckPath)
# Offline: executes actual C#-generated PowerShell with in-memory cmdlet fixtures.
# No SMB cmdlet, connection, mapping, filesystem save or native game is invoked.
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$source=[IO.File]::ReadAllText((Join-Path $PSScriptRoot '../GameShelf.Windows/WindowsView.cs'))
function DecodeLiteral([string]$text,[string]$name){
 $pattern='const string '+$name+'=("(?:[^"\\]|\\.)*");'
 $match=[regex]::Match($text,$pattern)
 if(-not $match.Success){throw 'Production literal not found'}
 # These ordinary C# literals use only JSON-compatible escapes; no duplicate script.
 return ConvertFrom-Json -InputObject $match.Groups[1].Value
}
$check=DecodeLiteral $source 'Check'
if($CompiledCheckPath -and $check -cne [IO.File]::ReadAllText($CompiledCheckPath)){throw 'Compiled Check and decoded source differ'}
$createSection=$source.Substring($source.IndexOf('public async Task<MappingReceipt> Create('))
$create=DecodeLiteral $createSection 'script'
. ([scriptblock]::Create($check))
$qualified='DOMAIN'+[char]92+'fixture'
$script:connections=@();$script:current=$null;$script:created=$false;$script:removed=0;$script:createCalls=0
function Get-SmbConnection {param($ServerName) $script:connections}
function Get-SmbMapping {param($LocalPath,$ErrorAction) if($script:created){$script:current}}
function New-SmbMapping {param($LocalPath,$RemotePath,$UserName,$Password,$Persistent,$TcpPort) $script:createCalls++;$script:created=$true;$script:current=[pscustomobject]@{RemotePath=$RemotePath;Status=0;TcpPort=$TcpPort};$script:current}
function Remove-SmbMapping {param([Parameter(ValueFromPipeline=$true)]$InputObject,[switch]$Force,[bool]$UpdateProfile) process{$script:removed++;$script:created=$false}}
function Test-Path {param($Path) return $true}
# Production capability check still inspects real command metadata; stub advertises TcpPort.
$p=[pscustomobject]@{drive='Z:';unc='\\fixture.invalid\synthetic';host='fixture.invalid';share='synthetic';port=2445;username='fixture';password='synthetic-only';credential='fixture';operationID='11111111-1111-1111-1111-111111111111'}
$m=[pscustomobject]@{RemotePath=$p.unc;TcpPort=2445;Status=0}
$script:failed=0;$script:count=0
function CheckCase([string]$name,[scriptblock]$case){$script:count++;try{& $case;Write-Output ('PASS '+$name)}catch{$script:failed++;Write-Output ('FAIL '+$name)}}
function Expect($expected,$actual){if($expected -ne $actual){throw 'Assertion failed'}}
CheckCase 'actual allocation query reserves Unavailable mapped letters' {$query=DecodeLiteral $source 'AllocationQuery';$script:created=$true;$script:current=[pscustomobject]@{LocalPath='Z:';RemotePath=$p.unc;Status='Unavailable';TcpPort=$null};try{$letters=(& ([scriptblock]::Create($query)))|ConvertFrom-Json;Expect 1 @($letters).Count;Expect 'Z:' @($letters)[0]}finally{$script:created=$false}}
CheckCase 'actual Match accepts exactly one separator without OR short circuit' {$script:connections=@([pscustomobject]@{ShareName='synthetic';Credential=$qualified});$p.credential='unmatched-receipt';try{Expect 1 @($qualified.ToCharArray()|Where-Object {$_ -eq [char]92}).Count;Expect $false ($qualified -eq $p.credential);Expect $false ($qualified -eq $p.username);Expect $true (Match $m $p)}finally{$p.credential='fixture'}}
CheckCase 'actual Match accepts exact acknowledged credential' {$p.credential=$qualified;Expect $true (Match $m $p);$p.credential='fixture'}
CheckCase 'actual Match rejects foreign credential' {$script:connections=@([pscustomobject]@{ShareName='synthetic';Credential='DOMAIN\other'});Expect $false (Match $m $p)}
CheckCase 'actual Match rejects foreign port' {$script:connections=@([pscustomobject]@{ShareName='synthetic';Credential='fixture'});Expect $false (Match ([pscustomobject]@{RemotePath=$p.unc;TcpPort=445}) $p)}
CheckCase 'actual Match rejects missing port even requested445' {$p.port=445;try{Expect $false (Match ([pscustomobject]@{RemotePath=$p.unc}) $p)}finally{$p.port=2445}}
CheckCase 'actual Match rejects null port' {Expect $false (Match ([pscustomobject]@{RemotePath=$p.unc;TcpPort=$null}) $p)}
CheckCase 'actual Match rejects zero port even requested445' {$p.port=445;try{Expect $false (Match ([pscustomobject]@{RemotePath=$p.unc;TcpPort=0}) $p)}finally{$p.port=2445}}
CheckCase 'actual Create acknowledges single-backslash account credential' {$script:connections=@([pscustomobject]@{ShareName='synthetic';Credential=$qualified});$script:created=$false;Expect 1 @($qualified.ToCharArray()|Where-Object {$_ -eq [char]92}).Count;Expect $false ($qualified -eq $p.credential);Expect $false ($qualified -eq $p.username);$receipt=(& ([scriptblock]::Create($create)))|ConvertFrom-Json;Expect $qualified $receipt.credential;Expect 2445 $receipt.port;Expect 1 $script:createCalls;Expect 0 $script:removed}
CheckCase 'actual Create does not remove an unmatched mapping' {$script:connections=@([pscustomobject]@{ShareName='synthetic';Credential='DOMAIN\other'});$script:created=$false;$script:removed=0;$threw=$false;try{& ([scriptblock]::Create($create))|Out-Null}catch{$threw=$true};Expect $true $threw;Expect 0 $script:removed}
Write-Output ('{0}/{1} actual PowerShell checks passed (offline only)' -f ($script:count-$script:failed),$script:count)
if($script:failed){exit 1}
