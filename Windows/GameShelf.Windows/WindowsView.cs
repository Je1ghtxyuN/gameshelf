using System.Diagnostics;
using System.Text;
using System.Text.Json;
using GameShelf.Core;
namespace GameShelf.Windows;
public sealed record MappingReceipt(Guid OperationID,string Drive,string Unc,string Host,string Share,int Port,string Credential)
{
    public override string ToString()=>"Owned account mapping (identity redacted)";
}
public interface IMappingBackend
{
    string ChooseDrive();
    Task<MappingReceipt> Create(SmbShare share,string drive,Guid operationID,CancellationToken ct);
    Task Remove(MappingReceipt receipt);
}
public sealed class WindowsView(IMappingBackend? mappingBackend=null):IReviewedWindowsView
{
    readonly IMappingBackend backend=mappingBackend??new ShellMappingBackend();
    MappingReceipt? owned;
    public async Task<string> Connect(SmbShare share,CancellationToken ct)
    {
        share.Validate();if(owned!=null)throw new InvalidOperationException("视图已连接");
        var drive=backend.ChooseDrive();var operation=Guid.NewGuid();
        var receipt=await backend.Create(share,drive,operation,ct);
        if(receipt.OperationID!=operation||receipt.Drive!=drive||receipt.Unc!=share.Unc||receipt.Port!=share.Port||receipt.Host!=share.Host||receipt.Share!=share.ShareName)throw new InvalidDataException("Mapping acknowledgement mismatch");
        owned=receipt;return receipt.Drive+@"\";
    }
    public async Task Disconnect(){if(owned==null)return;await backend.Remove(owned);owned=null;}
    public async Task<IGameProcess> StartReviewed(string root,string relative,LaunchLease lease,CancellationToken ct)
    {
        if(lease.WindowsLaunch?.EntryPath!=relative)throw new InvalidDataException("审查入口已变化");
        var plan=await RenpyAdapter.Prepare(root,lease,ct);ct.ThrowIfCancellationRequested();
        if(lease.ExpiresAt<=DateTimeOffset.UtcNow.ToUnixTimeSeconds()+2)throw new InvalidDataException("启动租约已过期");
        var full=RenpyAdapter.CheckedPath(root,relative,false);return NativeJob.StartRenpy(full,Path.GetDirectoryName(full)!,plan);
    }
    public Task<IGameProcess> Start(string root,string relative,CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();var safe=Safety.Relative(relative);var full=Path.GetFullPath(Path.Combine(root,safe.Replace('/',Path.DirectorySeparatorChar)));
        if(!full.StartsWith(Path.GetFullPath(root),StringComparison.OrdinalIgnoreCase))throw new ArgumentException("Unsafe entry");
        for(var path=full;path!=null&&path.Length>root.Length;path=Path.GetDirectoryName(path))if((File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)throw new InvalidDataException("入口包含链接");
        if(!File.Exists(full))throw new FileNotFoundException("Windows 入口不可用");
        ct.ThrowIfCancellationRequested();return Task.FromResult<IGameProcess>(NativeJob.Start(full,Path.GetDirectoryName(full)!));
    }
}
public enum SmbOperationStage { Initialize,Preflight,Create,Verify,Cleanup,Remove,Acknowledge,Timeout }
public sealed class SmbMappingException(SmbOperationStage stage,int? win32Error):InvalidOperationException($"SMB 账号视图操作失败：{stage}"+(win32Error is int code?$" (Win32 {code})":""))
{
    public SmbOperationStage Stage {get;}=stage;
    public int? Win32Error {get;}=win32Error;
}
public sealed class ShellMappingBackend:IMappingBackend
{
    readonly HashSet<Guid> operations=[];
    public string ChooseDrive(){var used=DriveInfo.GetDrives().Select(d=>d.Name[..2]).ToHashSet(StringComparer.OrdinalIgnoreCase);return Enumerable.Range('D',23).Select(i=>$"{(char)i}:").Reverse().FirstOrDefault(d=>!used.Contains(d))??throw new InvalidOperationException("没有可用盘符");}
    // Positive port evidence and exact share/credential identity are mandatory.
    // Missing/null/zero redirector port metadata never implies default445.
    const string Check="function Match($m,$p){if(-not $m -or $m.RemotePath -ne $p.unc){return $false}; if(-not $m.PSObject.Properties['TcpPort'] -or $null -eq $m.TcpPort -or [int]$m.TcpPort -le 0){return $false};if([int]$m.TcpPort -ne $p.port){return $false}; $c=@(Get-SmbConnection -ServerName $p.host | Where-Object {$_.ShareName -eq $p.share -and ($_.Credential -eq $p.credential -or $_.Credential -eq $p.username -or $_.Credential.EndsWith(('\\'+$p.username),[StringComparison]::OrdinalIgnoreCase))}); return $c.Count -eq 1}; ";
    public async Task<MappingReceipt> Create(SmbShare share,string drive,Guid operationID,CancellationToken ct)
    {
        const string script="$stage='Preflight';if(Get-SmbMapping -LocalPath $p.drive -ErrorAction SilentlyContinue){throw 'Drive already mapped'}; if($p.port -ne 445 -and -not (Get-Command New-SmbMapping).Parameters.ContainsKey('TcpPort')){throw 'Alternate SMB port unsupported'}; $a=@{LocalPath=$p.drive;RemotePath=$p.unc;UserName=$p.username;Password=$p.password;Persistent=$false};if($p.port -ne 445){$a.TcpPort=[uint16]$p.port}; $created=$false;try{$stage='Create';$m=New-SmbMapping @a;$created=$true;$stage='Verify';if([int]$m.Status -ne 0 -or -not (Test-Path ($p.drive+'\\')) -or -not (Match $m $p)){throw 'Account mapping not verified'};$c=@(Get-SmbConnection -ServerName $p.host | Where-Object {$_.ShareName -eq $p.share -and ($_.Credential -eq $p.username -or $_.Credential.EndsWith(('\\'+$p.username),[StringComparison]::OrdinalIgnoreCase))})[0];@{operationID=$p.operationID;drive=$p.drive;unc=$p.unc;host=$p.host;share=$p.share;port=$p.port;credential=$c.Credential}|ConvertTo-Json -Compress}catch{$failure=$_;$failedStage=$stage;if($created){$stage='Cleanup';$current=Get-SmbMapping -LocalPath $p.drive -ErrorAction SilentlyContinue;if($current){if(-not (Match $current $p)){throw 'Mapping identity unavailable; preserved'};$current|Remove-SmbMapping -Force -UpdateProfile:$false};$stage=$failedStage};throw $failure}";
        var output=await PowerShell(Check+script,new{operationID,drive,unc=share.Unc,host=share.Host,share=share.ShareName,port=share.Port,username=share.Username,password=share.Password,credential=share.Username},TimeSpan.FromSeconds(30),ct);
        MappingReceipt receipt;
        try{receipt=JsonSerializer.Deserialize<MappingReceipt>(output,AccountStore.Json)??throw new JsonException();}
        catch(JsonException){throw new SmbMappingException(SmbOperationStage.Acknowledge,null);}
        if(receipt.OperationID!=operationID||receipt.Drive!=drive||receipt.Unc!=share.Unc||receipt.Port!=share.Port)throw new SmbMappingException(SmbOperationStage.Acknowledge,null);
        operations.Add(operationID);return receipt;
    }
    public async Task Remove(MappingReceipt receipt)
    {
        if(!operations.Contains(receipt.OperationID))throw new SmbMappingException(SmbOperationStage.Remove,null);
        const string script="$stage='Remove';$m=Get-SmbMapping -LocalPath $p.drive -ErrorAction SilentlyContinue;if($m){if(-not (Match $m $p)){throw 'Mapping identity changed'};$m|Remove-SmbMapping -Force -UpdateProfile:$false};Write-Output 'OK'";
        await PowerShell(Check+script,new{drive=receipt.Drive,unc=receipt.Unc,host=receipt.Host,share=receipt.Share,port=receipt.Port,credential=receipt.Credential,username=""},TimeSpan.FromSeconds(5),CancellationToken.None);operations.Remove(receipt.OperationID);
    }
    // Never preserve raw ErrorRecord, stderr, request input or an inner exception.
    // Only a fixed stage and the FQID Windows System Error numeric token escape.
    static SmbMappingException SafeFailure(string stderr)
    {
        try{
            using var doc=JsonDocument.Parse(stderr.Trim());var root=doc.RootElement;
            if(!Enum.TryParse<SmbOperationStage>(root.GetProperty("stage").GetString(),out var stage)||!Enum.IsDefined(stage))return new(SmbOperationStage.Initialize,null);
            int? code=null;if(root.TryGetProperty("win32Error",out var field)&&field.ValueKind==JsonValueKind.Number&&field.TryGetInt32(out var number)&&number is >=0 and <=65535)code=number;
            return new(stage,code);
        }catch(Exception e)when(e is JsonException or KeyNotFoundException or InvalidOperationException or ArgumentException){return new(SmbOperationStage.Initialize,null);}
    }
    static async Task<string> PowerShell(string script,object input,TimeSpan timeout,CancellationToken ct)
    {
        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(ct);deadline.CancelAfter(timeout);
        var info=new ProcessStartInfo("powershell.exe"){UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true};
        const string prefix="$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';$stage='Initialize';try{$p=([Console]::In.ReadLine()|ConvertFrom-Json);";
        const string suffix="}catch{$code=$null;$match=[regex]::Match($_.FullyQualifiedErrorId,'(?:^|[ ,;])Windows System Error ([0-9]{1,5})(?=$|[ ,;])');if($match.Success){$number=[int]$match.Groups[1].Value;if($number -le 65535){$code=$number}};if($stage -notin @('Initialize','Preflight','Create','Verify','Cleanup','Remove','Acknowledge','Timeout')){$stage='Initialize'};[Console]::Error.WriteLine((@{stage=$stage;win32Error=$code}|ConvertTo-Json -Compress));exit 1}";
        info.ArgumentList.Add("-NoProfile");info.ArgumentList.Add("-NonInteractive");info.ArgumentList.Add("-EncodedCommand");info.ArgumentList.Add(Convert.ToBase64String(Encoding.Unicode.GetBytes(prefix+script+suffix)));
        Process process;
        try{process=Process.Start(info)??throw new InvalidOperationException();}catch{throw new SmbMappingException(SmbOperationStage.Initialize,null);}
        using(process){
            var stdout=process.StandardOutput.ReadToEndAsync(deadline.Token);var stderr=process.StandardError.ReadToEndAsync(deadline.Token);
            try{await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(input).AsMemory(),deadline.Token);process.StandardInput.Close();await process.WaitForExitAsync(deadline.Token);var error=await stderr;var result=await stdout;if(process.ExitCode!=0)throw SafeFailure(error);return result.Trim();}
            catch(OperationCanceledException)when(!ct.IsCancellationRequested){throw new SmbMappingException(SmbOperationStage.Timeout,null);}
            finally{if(!process.HasExited){process.Kill(true);try{await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5));}catch{}}}
        }
    }
}
