using System.Text.Json;
using System.Diagnostics;
namespace GameShelf.Core;
public sealed record SmbShare(string Host,int Port,string? Name,string? Share,string Username,string Password)
{
    public string ShareName=>Share??Name??throw new ArgumentException("Missing share");
    public void Validate(){if(Uri.CheckHostName(Host)==UriHostNameType.Unknown||Host.Contains(':')||Port<1||Port>65535||ShareName.Length>80||ShareName.Any(c=>!char.IsAsciiLetterOrDigit(c)&&c!='_'&&c!='-')||string.IsNullOrEmpty(Username)||string.IsNullOrEmpty(Password))throw new ArgumentException("Invalid account share");}
    public string Unc {get{Validate();return @"\\"+Host+@"\"+ShareName;}}
    public override string ToString()=>"Account SMB view (credentials redacted)";
}
public sealed record LeaseGame(string Id);
[System.Text.Json.Serialization.JsonUnmappedMemberHandling(System.Text.Json.Serialization.JsonUnmappedMemberHandling.Disallow)]
public sealed record WindowsLaunch(string EntryPath,string Isolation,bool SaveProfileVerified,string? ReviewedProfileID=null,int ProfileRevision=0,string? EntrySHA256=null,string? RulesSHA256=null,string? EvidenceSHA256=null,string? SourceSignature=null,SaveRule[]? SavePaths=null,bool Synthetic=false,string? Adapter=null,string? RelativeSavePath=null)
{
    public bool IsPrivateOverlay(LibraryVariant variant, string path) =>
        ((Isolation=="owner-private-overlay" && variant.NasSource!=null) ||
         (Isolation=="account-private-overlay" && variant.RequiresAccountWorkspace && variant.FamilyWindowsOverlay)) &&
        EntryPath==path && !SaveProfileVerified &&
        ReviewedProfileID==null && ProfileRevision==0 && EntrySHA256==null &&
        RulesSHA256==null && EvidenceSHA256==null && SourceSignature==null &&
        SavePaths==null && !Synthetic && Adapter==null && RelativeSavePath==null;
}
public sealed record SaveRule(string Path,string Kind);
public sealed record LaunchLease(string LeaseID,double ExpiresAt,string ServerID,string UserID,LeaseGame Game,SmbShare? Share,WindowsLaunch? WindowsLaunch,SaveNamespace? SaveNamespace=null,RenpyReview? RenpyLaunch=null,string? WorkspaceReceipt=null,MountedWorkspaceLocation? MountedWorkspace=null)
{
    public override string ToString() => "Launch lease (credentials and receipt redacted)";

    public void VerifyWorkspace(string root)
    {
        if (WorkspaceReceipt == null) return;
        if (WorkspaceReceipt.Length != 64 || WorkspaceReceipt.Any(c => c is not (>= '0' and <= '9' or >= 'a' and <= 'f')))
            throw new InvalidDataException("账号工作区校验信息无效。");
        var path = RenpyAdapter.CheckedPath(root, ".gameshelf-profile/workspace-receipt-v1", false);
        var expected = System.Text.Encoding.ASCII.GetBytes(Safety.Id(LeaseID) + "\n" + WorkspaceReceipt + "\n");
        using var file = File.OpenRead(path);
        if (file.Length != expected.Length) throw new InvalidDataException("NAS 工作区与当前账号会话不匹配。");
        var actual = new byte[expected.Length];
        file.ReadExactly(actual);
        if (file.ReadByte() != -1 || !actual.AsSpan().SequenceEqual(expected))
            throw new InvalidDataException("NAS 工作区与当前账号会话不匹配。");
    }

    public void Validate(string server,string user,LibraryVariant variant,LibraryEntry entry,DateTimeOffset now,string? canonicalID=null)
    {
        Safety.Id(LeaseID);
        if(ServerID!=server||UserID!=user||Game.Id!=variant.Id||!double.IsFinite(ExpiresAt)||ExpiresAt<=now.ToUnixTimeSeconds()+5)throw new InvalidDataException("Invalid lease identity/expiry");
        if (MountedWorkspace is { } mounted)
        {
            if (Share != null || WorkspaceReceipt == null || mounted.Relative != LeaseID)
                throw new InvalidDataException("账号工作区地址与租约不匹配。");
            mounted.Validate();
        }
        else (Share ?? throw new InvalidDataException("服务器未提供 SMB 会话。")).Validate();
        if(SaveNamespace!=null){RenpyAdapter.ValidateProof(this,variant,entry,canonicalID??throw new InvalidDataException("缺少逻辑游戏标识"));return;}
        if(WindowsLaunch?.Adapter!=null||RenpyLaunch!=null)throw new InvalidDataException("适配器缺少命名空间");
        if(WindowsLaunch?.IsPrivateOverlay(variant,entry.Path)==true && Safety.NativeEntry(entry.Path,entry.Backend))return;
        if(WindowsLaunch is not {SaveProfileVerified:true,Isolation:"reviewed-relative-saves"}||Safety.Relative(WindowsLaunch.EntryPath)!=Safety.Relative(entry.Path))throw new InvalidDataException("此版本尚无经过验证的 Windows 账号存档隔离配置");
    }
}
public sealed record MountedWorkspaceLocation(string Share, string Relative)
{
    public void Validate()
    {
        if (Share is not { Length: >= 1 and <= 64 } || !char.IsAsciiLetterOrDigit(Share[0]) ||
            Share.Any(c => !char.IsAsciiLetterOrDigit(c) && c is not ('-' or '_' or '.')))
            throw new InvalidDataException("NAS 共享名称无效。");
        Safety.Id(Relative);
    }
}
public interface IMountedWindowsView : IReviewedWindowsView
{
    Task<string> ConnectMounted(WindowsLeaseContext context, string expectedShare, CancellationToken ct);
}
public interface IGameProcess: IAsyncDisposable {Task Wait(CancellationToken ct); Task Stop();}
public interface IWindowsView {Task<string> Connect(SmbShare share,CancellationToken ct);Task Disconnect();Task<IGameProcess> Start(string root,string relative,CancellationToken ct);}
public sealed record WindowsLeaseContext(string CanonicalID,string ServerID,string UserID,LibraryVariant Variant,LibraryEntry Entry,LaunchLease Lease)
{
    public void Validate(DateTimeOffset now){Safety.Id(CanonicalID);if(Variant.Platform!="windows"||!Variant.Available||!Safety.NativeEntry(Entry.Path,Entry.Backend)||!Variant.Entries.Contains(Entry))throw new InvalidDataException("Invalid Windows launch context");Lease.Validate(ServerID,UserID,Variant,Entry,now,CanonicalID);}
    public override string ToString()=>"Windows lease context (redacted)";
}
public interface ILeaseBoundWindowsView:IWindowsView
{
    Task BindLease(WindowsLeaseContext context,CancellationToken ct);
    Task RenewLease(string leaseID,double expiresAt,CancellationToken ct);
}
public sealed class GameRunner(ApiClient api,AccountStore store,IWindowsView view,AccountGate gate,string deviceID,bool ownerWindowsOverlay=false,string? mountedWorkspaceShare=null)
{
    public async Task Run(LibraryGame game,CancellationToken ct)
    {
        var (variant,entry)=Editions.Select(game); gate.Begin(); LaunchLease? lease=null;IGameProcess? process=null;Stopwatch? clock=null;
        Exception? failure=null;
        try{
            var request = new Dictionary<string,object> {
                ["variantID"] = variant.Id, ["deviceID"] = deviceID, ["adapter"] = RenpyAdapter.Adapter
            };
            if (variant.FamilyWindowsOverlay || (ownerWindowsOverlay && variant.NasSource != null))
                request["windowsEntry"] = entry.Path;
            if (mountedWorkspaceShare != null) request["mountedWorkspace"] = true;
            lease=await api.Send<LaunchLease>(HttpMethod.Post,$"/api/library/games/{Safety.Id(game.Id)}/session",request,ct);
            lease.Validate(api.ServerID,api.UserID!,variant,entry,DateTimeOffset.UtcNow,game.Id);
            var context = new WindowsLeaseContext(game.Id,api.ServerID,api.UserID!,variant,entry,lease);
            string root;
            if (mountedWorkspaceShare != null)
            {
                if (lease.MountedWorkspace?.Share != mountedWorkspaceShare || view is not IMountedWindowsView mounted)
                    throw new InvalidDataException("服务器返回的 NAS 工作区与请求不匹配。");
                root = await mounted.ConnectMounted(context,mountedWorkspaceShare,ct);
            }
            else
            {
                if (lease.Share is not { } share) throw new InvalidDataException("服务器返回了不支持的工作区地址。");
                if(view is ILeaseBoundWindowsView bound)await bound.BindLease(context,ct);
                root=await view.Connect(share,ct);
            }
            lease.Validate(api.ServerID,api.UserID!,variant,entry,DateTimeOffset.UtcNow,game.Id);
            using var launchDeadline=CancellationTokenSource.CreateLinkedTokenSource(ct);launchDeadline.CancelAfter(TimeSpan.FromSeconds(Math.Max(0.01,lease.ExpiresAt-DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()/1000.0-2)));
            var launchToken=launchDeadline.Token;
            process=lease.SaveNamespace!=null?view is IReviewedWindowsView reviewed?await reviewed.StartReviewed(root,entry.Path,lease,launchToken):throw new InvalidOperationException("此客户端启动器不支持审查适配器"):await view.Start(root,entry.Path,launchToken);clock=Stopwatch.StartNew();
            var wait=process.Wait(ct);
            var expires=lease.ExpiresAt;
            while(!wait.IsCompleted)
            {
                var remaining=expires-DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()/1000.0-2;
                if(remaining<=0)throw new InvalidDataException("Lease expired");
                var delay=Task.Delay(TimeSpan.FromSeconds(Math.Min(15,remaining/3)),ct);
                if(await Task.WhenAny(wait,delay)==wait)break;
                ct.ThrowIfCancellationRequested();
                using var deadline=CancellationTokenSource.CreateLinkedTokenSource(ct);
                deadline.CancelAfter(TimeSpan.FromSeconds(Math.Max(0.01,expires-DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()/1000.0-2)));
                var beat=await api.Send<JsonElement>(HttpMethod.Post,$"/api/sessions/{lease.LeaseID}/heartbeat",new{},deadline.Token);
                expires=beat.GetProperty("expiresAt").GetDouble();
                if(beat.GetProperty("leaseID").GetString()!=lease.LeaseID||!double.IsFinite(expires)||expires<=DateTimeOffset.UtcNow.ToUnixTimeSeconds()+5)throw new InvalidDataException("Lease expired");
                if(view is ILeaseBoundWindowsView renewing)await renewing.RenewLease(lease.LeaseID,expires,deadline.Token);
            }
            await wait;
        }catch(Exception e){failure=e;}
        finally{
            // A failed stop keeps the writer gate and lease held. Once stop is confirmed,
            // persistence errors must not skip independent mapping/lease cleanup.
            var stopped=process==null;
            if(process!=null){try{await process.Stop();stopped=true;}catch(Exception e){failure??=e;}if(stopped)try{await process.DisposeAsync();}catch(Exception e){failure??=e;}}
            if(stopped)
            {
                try{if(clock!=null){clock.Stop();var ended=DateTimeOffset.UtcNow;var seconds=clock.Elapsed.TotalSeconds;while(seconds>0){var part=Math.Min(86400,seconds);await store.Enqueue(game.Id,new(Guid.NewGuid(),deviceID,part,ended.ToUnixTimeSeconds()));seconds-=part;}}}catch(Exception e){failure??=e;}
                // A remaining or unverified account mapping must keep its lease and switch gate.
                var disconnected=false;
                try{await view.Disconnect();disconnected=true;}catch(Exception e){failure??=e;}
                if(disconnected){
                    try{if(lease!=null)await api.Delete($"/api/sessions/{Safety.Id(lease.LeaseID)}");}catch(Exception e){failure??=e;}
                    finally{gate.End();}
                }
            }
        }
        if(failure!=null)System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(failure).Throw();
    }
}
