$ErrorActionPreference = 'Stop'
$dev = Join-Path $env:USERPROFILE 'GameShelfDev'
New-Item -ItemType Directory -Force $dev | Out-Null
$env:DOTNET_CLI_TELEMETRY_OPTOUT='1'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1'
$installer = Join-Path $dev 'dotnet-install.ps1'
Invoke-WebRequest 'https://dot.net/v1/dotnet-install.ps1' -UseBasicParsing -OutFile $installer
& $installer -Version '10.0.103' -Architecture x64 -InstallDir (Join-Path $dev 'dotnet') -NoPath
& (Join-Path $dev 'dotnet/dotnet.exe') --info
$zip=Join-Path $dev 'runtime.zip'
curl.exe -fL --retry 2 --max-time 600 'https://builds.dotnet.microsoft.com/dotnet/Runtime/10.0.14/dotnet-runtime-10.0.14-win-x64.zip' -o $zip
if($LASTEXITCODE -ne 0){throw 'Runtime download failed'}
if((Get-FileHash $zip -Algorithm SHA512).Hash.ToLower() -ne 'a176095f1db69b42c81acc60940744078d6bf2d2ac643e418342aa9c81889f4bd13e42f4621b131f9e77e4bc781ad46e375956219df9a4f979031d2a5a8d93cd'){throw 'Runtime digest mismatch'}
tar -xf $zip -C (Join-Path $dev 'dotnet')
if($LASTEXITCODE -ne 0){throw 'Runtime extraction failed'}
Remove-Item $zip
