$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
. (Join-Path $PSScriptRoot 'build-environment.ps1')
$src=Join-Path $dev 'src/Windows'
$app=Join-Path $src 'GameShelf.Windows/bin/x64/UIAcceptance/net10.0-windows10.0.19041.0/win-x64/GameShelf.Windows.exe'
if(-not (Test-Path -LiteralPath $app -PathType Leaf)){throw 'Build GameShelf.Windows with -c UIAcceptance before running this fixture.'}
$profile=Join-Path $dev 'ui-local-data'
$source=Join-Path $dev 'ui-local-sources/Local Fixture Game'
New-Item -ItemType Directory -Force $source | Out-Null
$fixture=Join-Path $src 'GameShelf.LaunchFixture/bin/Release/net10.0'
foreach($name in @('Fixture.exe','Fixture.dll','Fixture.deps.json','Fixture.runtimeconfig.json')){Copy-Item (Join-Path $fixture $name) $source -Force}
Copy-Item (Join-Path $src 'GameShelf.Windows/Assets/AppIcon.png') (Join-Path $source 'cover.png') -Force
# Only reset this synthetic test profile, never the user's actual app data.
if(Test-Path $profile){Remove-Item $profile -Recurse -Force}
$dotnet=Join-Path $dev 'dotnet/dotnet.exe'
& $dotnet (Join-Path $src 'GameShelf.UiFixture/bin/Release/net10.0/GameShelf.UiFixture.dll') --seed-local $profile (Join-Path $dev 'ui-local-sources')
if($LASTEXITCODE -ne 0){throw 'Local fixture cache seed failed'}
$p=Start-Process $app -ArgumentList ('"--data-dir='+$profile+'"') -PassThru
try {
 Add-Type -AssemblyName UIAutomationClient
 Add-Type -AssemblyName UIAutomationTypes
 Add-Type -AssemblyName System.Drawing
 Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class LocalCapture {
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr v);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint flags);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
 [StructLayout(LayoutKind.Sequential)] public struct RECT{public int L,T,R,B;}
}
'@
 [LocalCapture]::SetThreadDpiAwarenessContext([IntPtr](-4))|Out-Null
 $deadline=[DateTime]::UtcNow.AddSeconds(20)
 do {
  $p.Refresh()
  if($p.HasExited){throw "Local fixture app exited: $($p.ExitCode)"}
  $window=$p.MainWindowHandle
  if($window -ne [IntPtr]::Zero){break}
  Start-Sleep -Milliseconds 250
 } while([DateTime]::UtcNow -lt $deadline)
 if($window -eq [IntPtr]::Zero){throw 'Local fixture window did not open within 20 seconds'}
 $root=[Windows.Automation.AutomationElement]::FromHandle($window)
 function Find-ID($id){return $root.FindFirst([Windows.Automation.TreeScope]::Descendants,(New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty,$id)))}
 function Find-Name($name){return $root.FindFirst([Windows.Automation.TreeScope]::Descendants,(New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::NameProperty,$name)))}
 function Click($name){$e=Find-Name $name;if($null -eq $e){throw "Missing $name"};$e.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()}
 (Find-ID 'LocalUseButton').GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();Start-Sleep -Seconds 2
 Click '扫描游戏';Start-Sleep -Seconds 1
 $item=Find-Name 'Local Fixture Game';$selected=$false
 for($n=0;$n -lt 5 -and $null -ne $item;$n++){$pattern=$null;if($item.TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern,[ref]$pattern)){$pattern.Select();$selected=$true;break};$item=[Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($item)}
 if(-not $selected){throw 'Could not select cached local fixture'}
 Click '添加';Start-Sleep -Seconds 2
 if($null -eq (Find-Name 'Local Fixture Game')){throw 'Selected local fixture not added'}
 Write-Output 'PASS local choice -> cached candidates -> selected add in same library UI'
 Click '启动';Start-Sleep -Seconds 4
 $state=Get-Content (Join-Path $profile 'LocalLibrary/library.json') -Raw | ConvertFrom-Json
 if($state.games[0].game.playtimeSeconds -lt 2){throw 'Local UI launch time did not persist'}
 if((Get-Content (Join-Path $source 'savedata/progress.txt') -Raw) -ne 'Local Fixture Game'){throw 'Usual local relative save missing'}
 Write-Output 'PASS local WinUI actual harmless EXE launch, usual save, time persisted'
 $r=New-Object LocalCapture+RECT;[LocalCapture]::GetWindowRect($p.MainWindowHandle,[ref]$r)|Out-Null;$b=New-Object Drawing.Bitmap(($r.R-$r.L),($r.B-$r.T));$g=[Drawing.Graphics]::FromImage($b);$dc=$g.GetHdc();[LocalCapture]::PrintWindow($p.MainWindowHandle,$dc,2)|Out-Null;$g.ReleaseHdc($dc);$b.Save((Join-Path $dev 'local-fixture.png'));$g.Dispose();$b.Dispose()
 Click '设置';Start-Sleep -Seconds 1;Click '连接服务器';Start-Sleep -Seconds 1
 if($null -eq (Find-ID 'ConnectButton') -or $null -ne (Find-Name 'Local Fixture Game')){throw 'Local library retained in server login'}
 $username=Find-ID 'Username';if($username -and $username.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).Current.Value -ne ''){throw 'Server login prefilled owner'}
 if(@(Get-ChildItem $profile -Directory | Where-Object {$_.Name -match '^[a-f0-9]{32}-[a-f0-9]{32}$'}).Count -ne 0){throw 'Local metadata migrated into a server-account cache'}
 Write-Output 'PASS neutral server connection return; no local metadata/account-cache adoption'
 [LocalCapture]::PostMessage($p.MainWindowHandle,0x10,[IntPtr]::Zero,[IntPtr]::Zero)|Out-Null
 if(-not $p.WaitForExit(5000)){throw 'Local UI failed to close'}
} finally {if(-not $p.HasExited){Stop-Process -Id $p.Id}}
