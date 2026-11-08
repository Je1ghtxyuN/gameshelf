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
public sealed record WindowsLaunch(string EntryPath,string Isolation,bool SaveProfileVerified,string? ReviewedProfileID=null,int ProfileRevision=0,string? EntrySHA256=null,string? RulesSHA256=null,string? EvidenceSHA256=null,string? SourceSignature=null,SaveRule[]? SavePaths=null,bool Synthetic=false,string? Adapter=null,string? RelativeSavePath=null);
public sealed record SaveRule(string Path,string Kind);
public sealed record LaunchLease(string LeaseID,double ExpiresAt,string ServerID,string UserID,LeaseGame Game,SmbShare Share,WindowsLaunch? WindowsLaunch,SaveNamespace? SaveNamespace=null,RenpyReview? RenpyLaunch=null)
{
    public void Validate(string server,string user,LibraryVariant variant,LibraryEntry entry,DateTimeOffset now,string? canonicalID=null)
    {
        Safety.Id(LeaseID);
        if(ServerID!=server||UserID!=user||Game.Id!=variant.Id||!double.IsFinite(ExpiresAt)||ExpiresAt<=now.ToUnixTimeSeconds()+5)throw new InvalidDataException("Invalid lease identity/expiry");
        Share.Validate();
        if(SaveNamespace!=null){RenpyAdapter.ValidateProof(this,variant,entry,canonicalID??throw new InvalidDataException("缺少逻辑游戏标识"));return;}
        if(WindowsLaunch?.Adapter!=null||RenpyLaunch!=null)throw new InvalidDataException("适配器缺少命名空间");
        if(WindowsLaunch is not {SaveProfileVerified:true,Isolation:"reviewed-relative-saves"}||Safety.Relative(WindowsLaunch.EntryPath)!=Safety.Relative(entry.Path))throw new InvalidDataException("此版本尚无经过验证的 Windows 账号存档隔离配置");
    }
}
public interface IGameProcess: IAsyncDisposable {Task Wait(CancellationToken ct); Task Stop();}
public interface IWindowsView {Task<string> Connect(SmbShare share,CancellationToken ct);Task Disconnect();Task<IGameProcess> Start(string root,string relative,CancellationToken ct);}
public sealed class GameRunner(ApiClient api,AccountStore store,IWindowsView view,AccountGate gate,string deviceID)
{
    public async Task Run(LibraryGame game,CancellationToken ct)
    {
        var (variant,entry)=Editions.Select(game); gate.Begin(); LaunchLease? lease=null;IGameProcess? process=null;Stopwatch? clock=null;
        Exception? failure=null;
        try{
            lease=await api.Send<LaunchLease>(HttpMethod.Post,$"/api/library/games/{Safety.Id(game.Id)}/session",new{variantID=variant.Id,deviceID,adapter=RenpyAdapter.Adapter},ct);
            lease.Validate(api.ServerID,api.UserID!,variant,entry,DateTimeOffset.UtcNow,game.Id);
            var root=await view.Connect(lease.Share,ct);
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
                try{await view.Disconnect();}catch(Exception e){failure??=e;}
                try{if(lease!=null)await api.Delete($"/api/sessions/{Safety.Id(lease.LeaseID)}");}catch(Exception e){failure??=e;}
                finally{gate.End();}
            }
        }
        if(failure!=null)System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(failure).Throw();
    }
}
