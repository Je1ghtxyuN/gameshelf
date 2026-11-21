$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
. (Join-Path $PSScriptRoot 'build-environment.ps1')
$src=Join-Path $dev 'src/Windows'
$app=Join-Path $src 'GameShelf.Windows/bin/x64/UIAcceptance/net10.0-windows10.0.19041.0/win-x64/GameShelf.Windows.exe'
if(-not (Test-Path -LiteralPath $app -PathType Leaf)){throw 'Build GameShelf.Windows with -c UIAcceptance before running this fixture.'}
$dotnet=Join-Path $dev 'dotnet/dotnet.exe'
$fixture=Join-Path $src 'GameShelf.UiFixture/bin/Release/net10.0/GameShelf.UiFixture.dll'
$server=Start-Process $dotnet -ArgumentList ('"'+$fixture+'"') -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $dev 'fixture-port.txt')
$p=$null
try {
 Start-Sleep -Seconds 2
 $port=[int](Get-Content (Join-Path $dev 'fixture-port.txt') -First 1)
 Remove-Item (Join-Path $dev 'ui-fixture-data/connection.json') -ErrorAction SilentlyContinue
 $account=Join-Path (Join-Path $dev 'ui-fixture-data') (('1'*32)+'-'+('2'*32))
 New-Item -ItemType Directory -Force $account|Out-Null
 $pending=@{serverID=('1'*32);userID=('2'*32);gameID=('3'*32);event=@{eventID=[guid]::NewGuid();deviceID='ui-fixture';elapsedSeconds=60;endedAt=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()}}
 ConvertTo-Json -InputObject @($pending) -Depth 4 -Compress | Set-Content -LiteralPath (Join-Path $account 'outbox.json') -Encoding ASCII

 $p=Start-Process $app -ArgumentList ('"--data-dir='+(Join-Path $dev 'ui-fixture-data')+'"') -PassThru
 Start-Sleep -Seconds 6
 $p.Refresh()
 if($p.HasExited -or $p.MainWindowHandle -eq 0){throw 'No fixture window'}
 Add-Type -AssemblyName UIAutomationClient
 Add-Type -AssemblyName UIAutomationTypes
 Add-Type -AssemblyName System.Drawing
 Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class Capture {
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
 [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h,IntPtr after,int x,int y,int width,int height,uint flags);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint flags);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint msg,IntPtr w,IntPtr l);
 [StructLayout(LayoutKind.Sequential)] public struct RECT {public int L,T,R,B;}
}
'@
 [Capture]::SetThreadDpiAwarenessContext([IntPtr](-4))|Out-Null
 $root=[Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
 function Find-ID($id){$c=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty,$id);return $root.FindFirst([Windows.Automation.TreeScope]::Descendants,$c)}
 function Find-Name($name){$c=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::NameProperty,$name);return $root.FindFirst([Windows.Automation.TreeScope]::Descendants,$c)}
 function Set-Value($id,$value){$e=Find-ID $id;if($null -eq $e){throw "Missing $id"};$e.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue($value)}
 function Click-Name($name){$e=Find-Name $name;if($null -eq $e){throw "Missing button $name"};$e.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()}
 function Capture-App($name){$prior=[Capture]::SetThreadDpiAwarenessContext([IntPtr](-4));$r=New-Object Capture+RECT;[Capture]::GetWindowRect($p.MainWindowHandle,[ref]$r)|Out-Null;$bitmap=New-Object Drawing.Bitmap(($r.R-$r.L),($r.B-$r.T));$g=[Drawing.Graphics]::FromImage($bitmap);$dc=$g.GetHdc();[Capture]::PrintWindow($p.MainWindowHandle,$dc,2)|Out-Null;$g.ReleaseHdc($dc);$bitmap.Save((Join-Path $dev $name));$g.Dispose();$bitmap.Dispose();[Capture]::SetThreadDpiAwarenessContext($prior)|Out-Null}
 Capture-App 'connection-fixture.png'
 Set-Value 'Address' "http://127.0.0.1:$port"
 (Find-ID 'ConnectButton').GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
 Start-Sleep -Seconds 2
 Capture-App 'login-fixture.png'
 Set-Value 'Username' 'fixture-one'
 Set-Value 'Password' 'synthetic-only-password'

 (Find-ID 'LoginButton').GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
 Start-Sleep -Seconds 4
 if($null -eq (Find-Name 'Windows Fixture')){throw 'First account library missing'}
 $cache=Get-Content -LiteralPath (Join-Path $account 'library.json') -Raw | ConvertFrom-Json
 if($cache.games[0].playtimeSeconds -ne 180){throw 'Login did not reload the library after uploading pending playtime'}
 if((Get-Content -LiteralPath (Join-Path $account 'outbox.json') -Raw).Trim() -ne '[]'){throw 'Uploaded playtime still in outbox'}
 Write-Output 'PASS pending playtime uploaded and reflected during login'

 $node=Find-Name '未分类'
 for($i=0;$i -lt 4 -and $null -ne $node;$i++){$pattern=$null;if($node.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$pattern)){$pattern.Expand();break};$node=[Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($node)}
 Start-Sleep -Milliseconds 400
 [Capture]::SetWindowPos($p.MainWindowHandle,[IntPtr]::Zero,0,0,([int](1200*[Capture]::GetDpiForWindow($p.MainWindowHandle)/96)),([int](800*[Capture]::GetDpiForWindow($p.MainWindowHandle)/96)),6)|Out-Null;Start-Sleep -Seconds 1
 Capture-App 'library-1200.png'
 $grid=Find-ID 'Covers';$condition=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ControlTypeProperty,[Windows.Automation.ControlType]::ListItem)
 $items=$grid.FindAll([Windows.Automation.TreeScope]::Descendants,$condition)
 if($items.Count -lt 3){throw 'Fixture grid did not expose three cards'}
 $tops=@();foreach($item in $items){if(-not $item.Current.BoundingRectangle.IsEmpty){$tops+=([math]::Round($item.Current.BoundingRectangle.Top))}}
 if($tops.Count -ne 3 -or ($tops|Select-Object -Unique).Count -ne 1){throw '1200 grid did not form three visible columns'}
 $lastRight=($items|ForEach-Object {$_.Current.BoundingRectangle.Right}|Measure-Object -Maximum).Maximum
 if($grid.Current.BoundingRectangle.Right-$lastRight -gt 90){throw 'Three cards did not fill the three-column viewport'}
 [Capture]::SetWindowPos($p.MainWindowHandle,[IntPtr]::Zero,0,0,([int](780*[Capture]::GetDpiForWindow($p.MainWindowHandle)/96)),([int](800*[Capture]::GetDpiForWindow($p.MainWindowHandle)/96)),6)|Out-Null;Start-Sleep -Seconds 1
 Capture-App 'library-780.png'
 $right=(Find-ID 'Covers').Current.BoundingRectangle.Right
 foreach($item in (Find-ID 'Covers').FindAll([Windows.Automation.TreeScope]::Descendants,$condition)){if(-not $item.Current.BoundingRectangle.IsEmpty -and $item.Current.BoundingRectangle.Right -gt ($right+2)){throw 'Narrow grid overflows horizontally'}}
 Write-Output 'PASS responsive grid: three columns at1200; no horizontal overflow at780'
 [Capture]::SetWindowPos($p.MainWindowHandle,[IntPtr]::Zero,0,0,([int](1200*[Capture]::GetDpiForWindow($p.MainWindowHandle)/96)),([int](800*[Capture]::GetDpiForWindow($p.MainWindowHandle)/96)),6)|Out-Null;Start-Sleep -Seconds 1
 Capture-App 'library-fixture.png'
 Write-Output 'PASS authenticated fixture library/tree/grid'
 [Capture]::PostMessage($p.MainWindowHandle,0x10,[IntPtr]::Zero,[IntPtr]::Zero)|Out-Null
 if(-not $p.WaitForExit(5000)){throw 'First window failed to close'}
 $p=Start-Process $app -ArgumentList ('"--data-dir='+(Join-Path $dev 'ui-fixture-data')+'"') -PassThru
 Start-Sleep -Seconds 5
 $p.Refresh();$root=[Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
 if($null -eq (Find-Name 'Windows Fixture')){throw 'Session restore failed'}
 Write-Output 'PASS credential-vault session restore without password'

 Click-Name '扫描游戏'
 Start-Sleep -Seconds 2
 if($null -eq (Find-Name 'Candidate Fixture')){throw 'Persisted candidate missing'}
 Capture-App 'candidates-fixture.png'
 Click-Name '关闭'
 Start-Sleep -Seconds 1
 Write-Output 'PASS persisted server candidate page'
 Click-Name '设置'
 Start-Sleep -Seconds 2
 Capture-App 'settings-fixture.png'
 Click-Name '切换账号'
 Start-Sleep -Seconds 2
 if($null -eq (Find-ID 'LoginButton')){throw 'Logout did not return to login'}
 Set-Value 'Username' 'fixture-two'
 Set-Value 'Password' 'synthetic-only-password'
 (Find-ID 'LoginButton').GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
 Start-Sleep -Seconds 3
 if($null -eq (Find-Name 'Account Two Fixture')){throw 'Second account library missing'}
 if($null -ne (Find-Name 'Windows Fixture')){throw 'First account data retained'}
 Capture-App 'account-two-fixture.png'
 Write-Output 'PASS account switch does not retain first account library'
 Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/fixture/revoke" | Out-Null
 [Capture]::PostMessage($p.MainWindowHandle,0x10,[IntPtr]::Zero,[IntPtr]::Zero)|Out-Null
 if(-not $p.WaitForExit(5000)){throw 'Second window failed to close'}
 $p=Start-Process $app -ArgumentList ('"--data-dir='+(Join-Path $dev 'ui-fixture-data')+'"') -PassThru
 Start-Sleep -Seconds 5;$p.Refresh();$root=[Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
 $name=(Find-ID 'Username').GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).Current.Value
 if($name -ne ''){throw 'Failed restoration prefilled another account username'}
 Write-Output 'PASS failed restored session clears username'
 Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/fixture/restore" | Out-Null
 Set-Value 'Username' 'fixture-two';Set-Value 'Password' 'synthetic-only-password'
 (Find-ID 'LoginButton').GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();Start-Sleep -Seconds 2

 Click-Name '设置';Start-Sleep -Seconds 1;Click-Name '切换账号';Start-Sleep -Seconds 1
 [Capture]::PostMessage($p.MainWindowHandle,0x10,[IntPtr]::Zero,[IntPtr]::Zero)|Out-Null
 if(-not $p.WaitForExit(5000)){Stop-Process -Id $p.Id}
 Write-Output 'PASS fixture UI close'
} finally {
 if($p -and -not $p.HasExited){Stop-Process -Id $p.Id}
 if(-not $server.HasExited){Stop-Process -Id $server.Id}
}
