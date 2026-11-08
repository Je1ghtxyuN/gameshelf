$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$dev=Join-Path $env:USERPROFILE 'GameShelfDev'
$env:DOTNET_ROOT=Join-Path $dev 'dotnet'
$env:PATH="$env:DOTNET_ROOT;$env:PATH"
$env:NUGET_PACKAGES=Join-Path $dev 'packages'
$env:DOTNET_CLI_TELEMETRY_OPTOUT='1'
Set-Location (Join-Path $dev 'src/Windows')
$output=Join-Path (Get-Location) 'GameShelf.Windows/bin/x64/Release/net10.0-windows10.0.19041.0/win-x64'
$artifacts=Join-Path $dev 'artifacts'
New-Item -ItemType Directory -Force $artifacts | Out-Null
# Reuse the just-built self-contained output to avoid a second large directory.
& dotnet publish GameShelf.Windows -c Release -p:Platform=x64 --no-build --no-restore ('-p:PublishDir='+$output.Replace('\','/')+'/')
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
$config=Get-Content (Join-Path $output 'GameShelf.Windows.runtimeconfig.json') -Raw | ConvertFrom-Json
if($config.runtimeOptions.includedFrameworks[0].version -ne '10.0.14'){throw 'Unexpected self-contained runtime'}
if(-not (Test-Path (Join-Path $output 'Microsoft.UI.Xaml.dll'))){throw 'WinUI runtime missing'}
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$path=Join-Path $artifacts 'GameShelf-Windows-x64.zip'
if(Test-Path $path){Remove-Item $path -Force}
if(Test-Path ($path+'.sha256')){Remove-Item ($path+'.sha256') -Force}
$stream=[IO.File]::Create($path)
$zip=New-Object IO.Compression.ZipArchive($stream,[IO.Compression.ZipArchiveMode]::Create,$false)
try {
 foreach($file in (Get-ChildItem $output -Recurse -File)) {
  if($file.Extension -in @('.pdb','.log','.zip','.nupkg','.tmp')){continue}
  $relative=$file.FullName.Substring($output.TrimEnd('\').Length+1).Replace('\','/')
  [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip,$file.FullName,$relative,[IO.Compression.CompressionLevel]::Optimal) | Out-Null
 }
} finally {$zip.Dispose();$stream.Dispose()}
try{& (Join-Path $PSScriptRoot 'audit-candidate.ps1') -ZipPath $path -ReportPath (Join-Path $artifacts 'windows-candidate-private-content-audit.json')}
catch{if(Test-Path $path){Remove-Item $path -Force};throw}
$hash=(Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
[IO.File]::WriteAllText(($path+'.sha256'),$hash+'  GameShelf-Windows-x64.zip'+[Environment]::NewLine)
Write-Output ('CANDIDATE_PATH='+$path)
Write-Output ('CANDIDATE_SHA256='+$hash)
Write-Output ('CANDIDATE_BYTES='+(Get-Item $path).Length)
