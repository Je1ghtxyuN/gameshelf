$dev = $env:GAMESHELF_BUILD_ROOT
if ([string]::IsNullOrWhiteSpace($dev)) { $dev = 'D:\GameShelfDev' }
if ($dev -notmatch '^[A-Za-z]:[\\/]') { throw 'Set GAMESHELF_BUILD_ROOT to an absolute build directory.' }
$dev = [IO.Path]::GetFullPath($dev)
if (-not (Test-Path -LiteralPath ([IO.Path]::GetPathRoot($dev)))) {
    throw 'The GameShelf build drive is unavailable.'
}
$env:GAMESHELF_BUILD_ROOT = $dev
$env:DOTNET_ROOT = Join-Path $dev 'dotnet'
$env:DOTNET_CLI_HOME = Join-Path $dev 'dotnet-home'
$env:NUGET_PACKAGES = Join-Path $dev 'packages'
$env:NUGET_HTTP_CACHE_PATH = Join-Path $dev 'nuget-http-cache'
$env:NUGET_PLUGINS_CACHE_PATH = Join-Path $dev 'nuget-plugins-cache'
$env:APPDATA = Join-Path $dev 'appdata'
$env:LOCALAPPDATA = Join-Path $dev 'localappdata'
$env:TEMP = Join-Path $dev 'temp'
$env:TMP = $env:TEMP
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
New-Item -ItemType Directory -Force $dev, $env:TEMP, $env:DOTNET_CLI_HOME, $env:APPDATA, $env:LOCALAPPDATA | Out-Null
$env:PATH = "$env:DOTNET_ROOT;$env:PATH"
