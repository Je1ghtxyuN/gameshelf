$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'build-environment.ps1')
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
& dotnet build GameShelf.Broker -c Release -p:Platform=x64 -p:UseSharedCompilation=false
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& dotnet build GameShelf.Windows -c Release -m:1 -p:Platform=x64 -p:UseSharedCompilation=false
exit $LASTEXITCODE
