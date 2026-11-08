param([Parameter(Mandatory=$true)][string]$ZipPath,[string]$ReportPath,[string[]]$AdditionalMarkers=@())
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type @'
using System;
using System.IO;
public static class CandidateByteAudit {
 static byte Fold(byte b){return b>=65&&b<=90?(byte)(b+32):b;}
 public static bool Contains(Stream stream,byte[] pattern){
  var p=new byte[pattern.Length];for(int i=0;i<p.Length;i++)p[i]=Fold(pattern[i]);
  var prefix=new int[p.Length];for(int i=1,j=0;i<p.Length;i++){while(j>0&&p[i]!=p[j])j=prefix[j-1];if(p[i]==p[j])j++;prefix[i]=j;}
  var buffer=new byte[65536];int matched=0,n;
  while((n=stream.Read(buffer,0,buffer.Length))>0){for(int i=0;i<n;i++){byte b=Fold(buffer[i]);while(matched>0&&b!=p[matched])matched=prefix[matched-1];if(b==p[matched])matched++;if(matched==p.Length)return true;}}
  return false;
 }
}
'@
$homePath=[Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
$markers=@($homePath,$homePath.Replace('\','/'),$homePath.Replace('\','\\'),[Environment]::UserName)+$AdditionalMarkers
$markers=@($markers | Where-Object {$_ -and $_.Length -ge 5} | Select-Object -Unique)
$encodings=@([Text.Encoding]::UTF8,[Text.Encoding]::Unicode,[Text.Encoding]::BigEndianUnicode)
$archive=[IO.Compression.ZipFile]::OpenRead($ZipPath)
$findings=New-Object 'System.Collections.Generic.List[object]'
$files=0
try {
 foreach($entry in $archive.Entries){
  if($entry.FullName.EndsWith('/')){continue};$files++
  foreach($marker in $markers){foreach($encoding in $encodings){
   $stream=$entry.Open()
   try{if([CandidateByteAudit]::Contains($stream,$encoding.GetBytes($marker))){$findings.Add([pscustomobject]@{file=$entry.FullName;encoding=$encoding.WebName;marker=$marker})}}
   finally{$stream.Dispose()}
  }}
 }
}finally{$archive.Dispose()}
$result=[pscustomobject]@{sha256=(Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLower();bytes=(Get-Item $ZipPath).Length;filesScanned=$files;markerCount=$markers.Count;encodings=@('utf-8','utf-16le','utf-16be');findings=@($findings.ToArray())}
if($ReportPath){[IO.File]::WriteAllText($ReportPath,($result|ConvertTo-Json -Depth 6))}
if($findings.Count -ne 0){throw ('Candidate private-content audit failed: '+$findings.Count+' matches')}
Write-Output ('PASS full archive byte-content privacy audit; files='+$files+'; findings=0; UTF8/UTF16LE/UTF16BE')
