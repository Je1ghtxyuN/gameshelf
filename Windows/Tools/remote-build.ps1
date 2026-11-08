$ErrorActionPreference='Stop'
$dev=Join-Path $env:USERPROFILE 'GameShelfDev'
$env:DOTNET_ROOT=Join-Path $dev 'dotnet'
$env:PATH="$env:DOTNET_ROOT;$env:PATH"
$env:NUGET_PACKAGES=Join-Path $dev 'packages'
$env:DOTNET_CLI_TELEMETRY_OPTOUT='1'
$ProgressPreference='SilentlyContinue'

Set-Location (Join-Path $dev 'src/Windows')
& dotnet --version
& dotnet run --project GameShelf.Core.Tests -p:UseSharedCompilation=false
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& dotnet build GameShelf.LaunchFixture -c Release -p:UseSharedCompilation=false
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& dotnet run --project GameShelf.Native.Tests -p:UseSharedCompilation=false
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& dotnet build GameShelf.UiFixture -c Release -p:UseSharedCompilation=false
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& dotnet build GameShelf.Windows -c Release -m:1 -p:Platform=x64 -p:UseSharedCompilation=false
exit $LASTEXITCODE
