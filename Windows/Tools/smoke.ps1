$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
. (Join-Path $PSScriptRoot 'build-environment.ps1')
$app=Join-Path $dev 'src/Windows/GameShelf.Windows/bin/x64/Release/net10.0-windows10.0.19041.0/win-x64/GameShelf.Windows.exe'
$p=Start-Process -FilePath $app -PassThru
Start-Sleep -Seconds 8
$p.Refresh()
if($p.HasExited){Write-Output "FAIL app exited: $($p.ExitCode)";exit 1}
Write-Output "App running: $($p.ProcessName), window=$($p.MainWindowHandle), title=$($p.MainWindowTitle)"
if($p.MainWindowHandle -eq 0){Stop-Process -Id $p.Id;throw 'No window handle'}
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class WinCapture {
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint msg,IntPtr w,IntPtr l);
}
'@
Add-Type -AssemblyName System.Drawing
$r=New-Object WinCapture+RECT
[WinCapture]::GetWindowRect($p.MainWindowHandle,[ref]$r) | Out-Null
$bitmap=New-Object Drawing.Bitmap(($r.R-$r.L),($r.B-$r.T))
$g=[Drawing.Graphics]::FromImage($bitmap)
$dc=$g.GetHdc()
$ok=[WinCapture]::PrintWindow($p.MainWindowHandle,$dc,2)
$g.ReleaseHdc($dc)
$bitmap.Save((Join-Path $dev 'login-smoke.png'))
$g.Dispose();$bitmap.Dispose()
Write-Output "PrintWindow=$ok"
[WinCapture]::PostMessage($p.MainWindowHandle,0x10,[IntPtr]::Zero,[IntPtr]::Zero) | Out-Null
if(-not $p.WaitForExit(5000)){Stop-Process -Id $p.Id}
Write-Output 'PASS native window launch/close'
