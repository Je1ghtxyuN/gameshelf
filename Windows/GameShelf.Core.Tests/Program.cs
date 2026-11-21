using GameShelf.Core;
using GameShelf.Windows;
var failures = 0; var count = 0;
void Test(string name, Action action) { count++; try { action(); Console.WriteLine($"PASS {name}"); } catch(Exception e) { failures++; Console.WriteLine($"FAIL {name}: {e.GetType().Name} {e.Message}"); } }
void Equal<T>(T expected,T actual) { if(!Equals(expected,actual)) throw new Exception($"Expected {expected}; got {actual}"); }
void Reject(Action action) { try { action(); } catch(ArgumentException) { return; } throw new Exception("Unsafe input accepted"); }
Test("valid relative Unicode path",()=>Equal("東方/game.exe",Safety.Relative("東方/game.exe")));
foreach(var p in new[]{"../g.exe","a/../g.exe","C:\\g.exe","\\\\nas\\g.exe","/g.exe","a:stream","CON.exe","aux/a.exe","a./g.exe","a /g.exe","a//g.exe","a\\..\\b.exe","x\u0000.exe","a/%2e%2e.exe","LPT1.txt"}) Test("reject path "+p.Replace('\0','?'),()=>Reject(()=>Safety.Relative(p)));
Test("canonical hex ID",()=>Equal(new string('a',32),Safety.Id(new string('a',32))));
Test("reject route injection",()=>Reject(()=>Safety.Id("../api/logout")));
Test("account cache separation",()=>Equal(false,Safety.CacheKey(new string('a',32),new string('b',32))==Safety.CacheKey(new string('a',32),new string('c',32))));
Test("server cache separation",()=>Equal(false,Safety.CacheKey(new string('a',32),new string('b',32))==Safety.CacheKey(new string('c',32),new string('b',32))));
Test("native game entry",()=>Equal(true,Safety.NativeEntry("th06.exe","native")));
foreach(var p in new[]{"custom.exe","uninstall.exe","setup.exe","config.exe","launcher.exe","readme.txt","script.cmd"}) Test("reject helper "+p,()=>Equal(false,Safety.NativeEntry(p,"native")));
Test("legacy wine entry runs natively on Windows",()=>Equal(true,Safety.NativeEntry("game.exe","wine")));
var sid=new string('1',32);var uid=new string('2',32);var gid=new string('3',32);var other=new string('4',32);
var user=new Account(uid,"fixture",false);
var variant=new LibraryVariant(gid,"windows",null,[new("Play.exe","wine",null)],null,true);
var game=new LibraryGame(gid,"Fixture",0,false,[],0,null,null,[variant]);
Test("reviewed account saves cannot take the direct NAS path", () =>
{
    Equal(true, variant.PermitsDirectNas());
    Equal(false, (variant with { RequiresAccountWorkspace = true }).PermitsDirectNas());
    Equal(false, (variant with { SaveCompatibilityKey = "reviewed" }).PermitsDirectNas());
});
Test("unplayed games have no playtime record",()=>Equal("尚无游玩记录",game.TimeLabel));
Test("positive subsecond playtime does not display zero seconds",()=>Equal("1 秒",(game with{PlaytimeSeconds=0.2}).TimeLabel));
var snapshot=new LibrarySnapshot(2,sid,user,[game],[]);
var helperCases=new[]{"notification_helper.exe","chrome_crashpad_handler.exe","thprac.exe","replayview_CN.exe","custom_CN.exe","configuration_tool.exe","settings64.exe","unins000.exe","setup64.exe"};
var titleCases=new[]{"CustomAdventure.exe","ConfigurationStory.exe"};
foreach(var path in helperCases.Concat(titleCases))
{
    var allowed=titleCases.Contains(path);
    var single=game with{Variants=[variant with{Entries=[new(path,"native",null)]}]};
    Test("properties entry eligibility "+path,()=>Equal(allowed,Safety.NativeEntry(path,"native")));
    Test("default selection eligibility "+path,()=>{if(allowed)Equal(path,Editions.Select(single).Entry.Path);else Throws<InvalidOperationException>(()=>Editions.Select(single));});
    Test("saved preference eligibility "+path,()=>{var preferred=single with{PreferredEntries=new(Windows:new(gid,path))};if(allowed)Equal(path,Editions.Select(preferred).Entry.Path);else Throws<InvalidOperationException>(()=>Editions.Select(preferred));});
}

void Run(Func<Task> task)=>task().GetAwaiter().GetResult();
void Throws<T>(Action a) where T:Exception {try{a();}catch(T){return;}throw new Exception("Expected "+typeof(T).Name);}
Test("NAS root mapping resolves multiple editions and rejects an escaping path", () =>
{
    var root = Path.Combine(Path.GetTempPath(), "GameShelf-nas-root-" + Guid.NewGuid());
    Directory.CreateDirectory(root);
    try
    {
        var account = new AccountStore(root, sid, uid);
        var preview = new PersonalNasPreview(account);
        var mapping = new NasSource(other, "hgame/樱御前");
        Equal(false, preview.HasRootBinding(variant with { NasSource = mapping }));
        Throws<InvalidOperationException>(() => preview.ResolveFolder(variant with { NasSource = mapping }));
        var mappings = Directory.CreateDirectory(Path.Combine(account.DirectoryPath, "nas-roots"));
        File.WriteAllText(Path.Combine(mappings.FullName, other + ".path"), root);
        Equal(true, preview.HasRootBinding(variant with { NasSource = mapping }));
        Equal(false, preview.HasRootBinding(variant));
        Equal(false, new PersonalNasPreview(new AccountStore(root, sid, other)).HasRootBinding(variant with { NasSource = mapping }));
        Equal(Path.Combine(root, "hgame", "樱御前"), preview.ResolveFolder(variant with { NasSource = mapping }));
        Equal(root, preview.ResolveFolder(variant with { NasSource = mapping with { Relative = "." } }));
        Throws<ArgumentException>(() => preview.ResolveFolder(variant with { NasSource = mapping with { Relative = "../escape" } }));
        preview.ClearRoot(other);
        Throws<InvalidOperationException>(() => preview.ResolveFolder(variant with { NasSource = mapping }));
    }
    finally { Directory.Delete(root, true); }
});
Test("Windows edition selects legacy exe natively",()=>Equal(gid,Editions.Select(game).Variant.Id));
Test("no macOS fallback",()=>Throws<InvalidOperationException>(()=>Editions.Select(game with {Variants=[variant with {Platform="macos"}]})));
Test("no unavailable edition",()=>Throws<InvalidOperationException>(()=>Editions.Select(game with {Variants=[variant with {Available=false}]})));
Test("no unsafe/helper entry",()=>Throws<InvalidOperationException>(()=>Editions.Select(game with {Variants=[variant with {Entries=[new("setup.exe","native",null)]}]})));
Test("explicit edition never silently changes",()=>Throws<InvalidOperationException>(()=>Editions.Select(game,other)));
Test("typed Windows context validates and redacts credentials",()=>{var context=new WindowsLeaseContext(gid,sid,uid,variant,variant.Entries[0],new(new string('5',32),DateTimeOffset.UtcNow.ToUnixTimeSeconds()+90,sid,uid,new(gid),new("fixture.invalid",2445,"synthetic",null,"fixture","sensitive-marker"),new("Play.exe","reviewed-relative-saves",true)));context.Validate(DateTimeOffset.UtcNow);Equal(false,context.ToString().Contains("sensitive-marker"));});
Test("GameRunner binds typed lease before opaque view connect",()=>Run(async()=>{var transport=new Transport();transport.Reply=(r,_)=>r.RequestUri!.AbsolutePath.EndsWith("/session")?new LaunchLease(new string('5',32),DateTimeOffset.UtcNow.ToUnixTimeSeconds()+90,sid,uid,new(gid),new("fixture.invalid",2445,"synthetic",null,"fixture","sensitive-marker"),new("Play.exe","reviewed-relative-saves",true)):new{};using var boundApi=new ApiClient(new Uri("http://127.0.0.1"),sid,null,true,transport);boundApi.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+90,user,sid));var dir=Path.Combine(Path.GetTempPath(),"GameShelf-bound-"+Guid.NewGuid());try{var view=new BoundFixtureView();await new GameRunner(boundApi,new AccountStore(dir,sid,uid),view,new AccountGate(),"bound-fixture").Run(game,CancellationToken.None);Equal(true,view.Bound);Equal(true,view.Disconnected);}finally{if(Directory.Exists(dir))Directory.Delete(dir,true);}}));
Test("broker frame limits reject before output",()=>Run(BrokerCases.FrameBounds));
Test("broker rejects unknown frame fields",()=>Run(BrokerCases.StrictJson));
Test("broker process Dispose leaves view channel intact",()=>Run(BrokerCases.ProxyDispose));
Test("broker operation and sequence binding",()=>Run(BrokerCases.Sequence));
Test("broker writer stop precedes mapping closure acknowledgement",()=>Run(BrokerCases.CleanupOrdering));
Test("broker failed native start still closes mapping",()=>Run(BrokerCases.FailedStartCanDisconnect));
Test("broker uncertain mapping never acknowledges disconnect",()=>Run(BrokerCases.UncertainMapping));
Test("broker mapping evidence distinguishes preflight from uncertain creation",()=>Run(BrokerCases.MappingFailureEvidence));
Test("broker RPC preserves SMB substage and pre-map evidence",()=>Run(GameShelf.Tests.BrokerRpcCases.MappingFailureEvidence));
Test("broker unacknowledged creation cannot claim clean",()=>Run(BrokerCases.UnacknowledgedCreation));
Test("broker peer EOF independently stops and disconnects",()=>Run(BrokerCases.EofStops));
Test("broker renewed deadline and independent expiry stop",()=>Run(BrokerCases.DeadlineAndRenewal));
Test("broker Dispose drains writer and mapping",()=>Run(BrokerCases.DisposeDrains));
Test("broker Dispose rejects uncertain cleanup",()=>Run(BrokerCases.DisposeRejectsUncertain));
Test("broker old expired deadline cannot be renewed",()=>Run(BrokerCases.RenewCannotResurrect));
Test("broker review crossing deadline never starts native",()=>Run(BrokerCases.ReviewCannotCrossDeadline));
Test("broker expired Connect cannot acknowledge success",()=>Run(BrokerCases.ConnectCannotAckExpired));
Test("broker full typed context strict wire roundtrip",()=>Run(BrokerCases.FullContextRoundTrip));
Test("broker peer identity and nonce rejection",()=>Run(BrokerCases.PeerAndNonce));
Test("broker metadata expiry prevents any map creation",()=>Run(BrokerCases.MetadataCannotCrossDeadline));
Test("broker repeated disconnect EOF and Dispose stay confirmed",()=>Run(BrokerCases.RepeatedCleanup));
Test("broker RPC correlation failure closes pipe",()=>Run(GameShelf.Tests.BrokerRpcCases.CorrelationPoisons));
Test("broker RPC cancellation after write closes pipe",()=>Run(GameShelf.Tests.BrokerRpcCases.CancelAfterWritePoisons));
Test("broker RPC cancellation before write keeps channel",()=>Run(GameShelf.Tests.BrokerRpcCases.CancelBeforeWritePreserves));
Test("broker RPC known negative allows verified Disconnect",()=>Run(GameShelf.Tests.BrokerRpcCases.NegativeAllowsDisconnect));
Test("broker credential domain and authenticated startup guard policy",()=>Run(BrokerCases.StartupPolicy));
Test("GameRunner forwards validated renewal to typed view",()=>Run(async()=>{var transport=new Transport();transport.Reply=(r,_)=>r.RequestUri!.AbsolutePath.EndsWith("/session")?new LaunchLease(new string('5',32),DateTimeOffset.UtcNow.ToUnixTimeSeconds()+8,sid,uid,new(gid),new("fixture.invalid",2445,"synthetic",null,"fixture","sensitive-marker"),new("Play.exe","reviewed-relative-saves",true)):r.RequestUri.AbsolutePath.EndsWith("/heartbeat")?new{leaseID=new string('5',32),expiresAt=DateTimeOffset.UtcNow.ToUnixTimeSeconds()+90}:(object)new{};using var boundApi=new ApiClient(new Uri("http://127.0.0.1"),sid,null,true,transport);boundApi.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+90,user,sid));var dir=Path.Combine(Path.GetTempPath(),"GameShelf-renew-"+Guid.NewGuid());try{var view=new BoundFixtureView{WaitDelayMs=2800};await new GameRunner(boundApi,new AccountStore(dir,sid,uid),view,new AccountGate(),"renew-fixture").Run(game,CancellationToken.None);Equal(true,view.Renewed);}finally{if(Directory.Exists(dir))Directory.Delete(dir,true);}}));
Test("broker concurrent Status Renew frames stay serialized",()=>Run(GameShelf.Tests.BrokerRpcCases.ConcurrentStatusRenew));
var tmp=Path.Combine(Path.GetTempPath(),"GameShelf-tests-"+Guid.NewGuid());Directory.CreateDirectory(tmp);
var store=new AccountStore(tmp,sid,uid);
Test("cache roundtrip",()=>Run(async()=>{await store.SaveLibrary(snapshot);Equal(gid,(await store.ReadLibrary())!.Games[0].Id);}));
Test("cache rejects foreign identity",()=>Throws<InvalidDataException>(()=>Run(()=>store.SaveLibrary(snapshot with{User=user with{Id=other}}))));
Test("another account has no inherited cache",()=>Run(async()=>Equal(null,await new AccountStore(tmp,sid,other).ReadLibrary())));
var time=new TimeEvent(Guid.NewGuid(),"fixture-device",12,1794096000);
Test("outbox identical enqueue counted once",()=>Run(async()=>{await store.Enqueue(gid,time);await store.Enqueue(gid,time);Equal(1,(await store.Pending()).Length);}));
Test("outbox changed event rejected",()=>Throws<ArgumentException>(()=>Run(()=>store.Enqueue(gid,time with{ElapsedSeconds=13}))));
Test("outbox duplicate across games rejected",()=>Throws<ArgumentException>(()=>Run(()=>store.Enqueue(other,time))));
Test("outbox survives restart",()=>Run(async()=>Equal(time.EventID,(await new AccountStore(tmp,sid,uid).Pending())[0].Event.EventID)));
Test("failed upload retains event ID",()=>Run(async()=>{try{await store.Flush(_=>throw new IOException());}catch(IOException){}Equal(time.EventID,(await store.Pending())[0].Event.EventID);}));
Test("acknowledged event removed",()=>Run(async()=>{await store.Flush(_=>Task.CompletedTask);Equal(0,(await store.Pending()).Length);}));
foreach(var seconds in new[]{double.NaN,double.PositiveInfinity,-1,86401})Test("invalid time "+seconds,()=>Throws<ArgumentException>(()=>Run(()=>store.Enqueue(gid,time with{ElapsedSeconds=seconds}))));
Test("block switch while running",()=>{var gate=new AccountGate();gate.Begin();Throws<InvalidOperationException>(gate.CanSwitch);gate.End();gate.CanSwitch();});
var wire=new Transport{Reply=(r,b)=>r.RequestUri!.AbsolutePath=="/api/login"?new LoginResult("fixture-token",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,sid):snapshot};
ApiClient Client(Transport transport)=>new(new Uri("http://127.0.0.1:9"),sid,null,true,transport);
Test("login then authenticated library",()=>Run(async()=>{using var api=Client(wire);await api.Login("fixture","fixture-only");Equal(uid,(await api.Library()).User.Id);Equal("fixture-token",wire.Requests.Last().Token);}));
Test("login rejects substituted server",()=>Run(async()=>{using var api=Client(new(){Reply=(_,_)=>new LoginResult("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,other)});Throws<InvalidDataException>(()=>Run(()=>api.Login("fixture","fixture")));}));
Test("library rejects switched user",()=>Run(async()=>{using var api=Client(new(){Reply=(_,_)=>snapshot with{User=user with{Id=other}}});api.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,sid));Throws<InvalidDataException>(()=>Run(()=>api.Library()));}));
Test("expired restore refused",()=>{using var api=Client(wire);Throws<InvalidDataException>(()=>api.Restore(new("fixture",0,user,sid)));});
Test("no implicit insecure HTTP",()=>Throws<ArgumentException>(()=>new ApiClient(new Uri("http://fixture.invalid"),sid,null)));
Test("HTTPS requires pin",()=>Throws<ArgumentException>(()=>new ApiClient(new Uri("https://fixture.invalid"),sid,null)));
Test("HTTP conflict remains actionable",()=>{using var api=Client(new(){Status=409});try{Run(()=>api.Library());throw new Exception("Missing conflict");}catch(ApiFailure e){Equal(409,e.Status);}});
var share=new SmbShare("127.0.0.1",1445,"fixture",null,"fixture-user","fixture-secret");
var lease=new LaunchLease(new string('5',32),DateTimeOffset.UtcNow.ToUnixTimeSeconds()+90,sid,uid,new(gid),share,new("Play.exe","reviewed-relative-saves",true));
Test("lease validates reviewed profile",()=>lease.Validate(sid,uid,variant,variant.Entries[0],DateTimeOffset.UtcNow));
Test("owner overlay accepts a published Windows entry without a fingerprint", () =>
{
    var ownerVariant = variant with { NasSource = new(gid, "Games/Fixture") };
    var overlay = lease with { WindowsLaunch = new("Play.exe", "owner-private-overlay", false) };
    var now = DateTimeOffset.UtcNow;
    overlay.Validate(sid, uid, ownerVariant, ownerVariant.Entries[0], now);
    Throws<InvalidDataException>(() => overlay.Validate(sid, uid, variant, variant.Entries[0], now));
    var forged = overlay with { WindowsLaunch = overlay.WindowsLaunch! with { SaveProfileVerified = true } };
    Throws<InvalidDataException>(() => forged.Validate(sid, uid, ownerVariant, ownerVariant.Entries[0], now));
});
Test("family workspace accepts only the published Windows entry", () =>
{
    var protectedVariant = variant with { RequiresAccountWorkspace = true, FamilyWindowsOverlay = true };
    var overlay = lease with { WindowsLaunch = new("Play.exe", "account-private-overlay", false) };
    var now = DateTimeOffset.UtcNow;
    overlay.Validate(sid, uid, protectedVariant, protectedVariant.Entries[0], now);
    Throws<InvalidDataException>(() => overlay.Validate(sid, uid, variant, variant.Entries[0], now));
    Throws<InvalidDataException>(() => overlay.Validate(sid, other, protectedVariant, protectedVariant.Entries[0], now));
});
Test("mounted lease rejects SMB credentials or a substituted lease folder", () =>
{
    var protectedVariant = variant with { RequiresAccountWorkspace = true, FamilyWindowsOverlay = true };
    var mounted = lease with {
        Share = null, WorkspaceReceipt = new string('a', 64),
        MountedWorkspace = new("account-workspace", lease.LeaseID),
        WindowsLaunch = new("Play.exe", "account-private-overlay", false)
    };
    var now = DateTimeOffset.UtcNow;
    mounted.Validate(sid, uid, protectedVariant, protectedVariant.Entries[0], now);
    Throws<InvalidDataException>(() => (mounted with { Share = share }).Validate(sid, uid, protectedVariant, protectedVariant.Entries[0], now));
    Throws<InvalidDataException>(() => (mounted with { MountedWorkspace = new("account-workspace", other) }).Validate(sid, uid, protectedVariant, protectedVariant.Entries[0], now));
    Throws<InvalidDataException>(() => (mounted with { WorkspaceReceipt = null }).Validate(sid, uid, protectedVariant, protectedVariant.Entries[0], now));
});
Test("mounted workspace selects only a unique system-mapped share root", () =>
{
    var mappings = new ShellMappingBackend.ExistingMapping[] {
        new("Z:", @"\\nas\Game"),
        new("Y:", @"\\"),
        new("X:", @"\\nas\account-workspace\subfolder"),
        new("W:", @"\\nas\ACCOUNT-WORKSPACE\")
    };
    Equal(@"W:\", ShellMappingBackend.ResolveExistingShare("account-workspace", mappings));
    Throws<InvalidOperationException>(() => ShellMappingBackend.ResolveExistingShare("account-workspace", mappings.Take(3)));
    Throws<InvalidOperationException>(() => ShellMappingBackend.ResolveExistingShare("account-workspace",
        mappings.Append(new("V:", @"\\other-nas\account-workspace"))));
});
Test("mounted launch uses existing view and releases after game stop", () => Run(async () =>
{
    var family = variant with { RequiresAccountWorkspace = true, FamilyWindowsOverlay = true };
    var mounted = lease with {
        Share = null, WorkspaceReceipt = new string('a', 64),
        MountedWorkspace = new("account-workspace", lease.LeaseID),
        WindowsLaunch = new("Play.exe", "account-private-overlay", false)
    };
    var transport = new Transport();
    var view = new MountedFixtureView();
    transport.Reply = (request, body) =>
    {
        if (request.Method == HttpMethod.Post)
        {
            using var payload = System.Text.Json.JsonDocument.Parse(body);
            Equal(true, payload.RootElement.GetProperty("mountedWorkspace").GetBoolean());
            Equal("Play.exe", payload.RootElement.GetProperty("windowsEntry").GetString());
            return mounted;
        }
        Equal(true, view.Process.Stopped);
        Equal(true, view.Process.Disconnected);
        return new object();
    };
    using var api = Client(transport);
    api.Restore(new("fixture", DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600, user, sid));
    await new GameRunner(api, store, view, new(), "fixture", mountedWorkspaceShare:"account-workspace")
        .Run(game with { Variants = [family] }, CancellationToken.None);
    Equal(true, view.Process.Started);
    Equal(false, view.Process.Connected);
    Equal("DELETE", transport.Requests.Last().Method);
}));
Test("SMB enrollment proof is confined to the named fixed-size file", () => Run(async () =>
{
    var directory = Path.Combine(Path.GetTempPath(),"GameShelf-proof-"+Guid.NewGuid());
    Directory.CreateDirectory(directory);
    try
    {
        var id = new string('a',32);
        var challenge = new SmbEnrollmentChallenge(id,"fixture",id+".proof");
        var proof = new string('b',64);
        await File.WriteAllTextAsync(Path.Combine(directory,challenge.Filename),proof);
        Equal(proof,await challenge.ReadProof(directory,CancellationToken.None));
        Throws<InvalidDataException>(() => Run(() => (challenge with { Filename="../outside" })
            .ReadProof(directory,CancellationToken.None)));
        await File.AppendAllTextAsync(Path.Combine(directory,challenge.Filename),"x");
        Throws<InvalidDataException>(() => Run(() => challenge.ReadProof(directory,CancellationToken.None)));
    }
    finally { Directory.Delete(directory,true); }
}));
Test("mounted workspace receipt rejects stale and foreign leases", () =>
{
    var root = Path.Combine(Path.GetTempPath(), "GameShelf-workspace-" + Guid.NewGuid());
    var profile = Directory.CreateDirectory(Path.Combine(root, ".gameshelf-profile"));
    var path = Path.Combine(profile.FullName, "workspace-receipt-v1");
    try
    {
        var stamped = lease with { WorkspaceReceipt = new string('a', 64) };
        File.WriteAllText(path, stamped.LeaseID + "\n" + stamped.WorkspaceReceipt + "\n");
        stamped.VerifyWorkspace(root);
        Throws<InvalidDataException>(() => (stamped with { LeaseID = new string('b', 32) }).VerifyWorkspace(root));
        Throws<InvalidDataException>(() => (stamped with { WorkspaceReceipt = new string('c', 64) }).VerifyWorkspace(root));
        File.AppendAllText(path, "extra");
        Throws<InvalidDataException>(() => stamped.VerifyWorkspace(root));
    }
    finally { Directory.Delete(root, true); }
});
Test("lease rejects unknown saves",()=>Throws<InvalidDataException>(()=>(lease with{WindowsLaunch=null}).Validate(sid,uid,variant,variant.Entries[0],DateTimeOffset.UtcNow)));
Test("lease rejects foreign account",()=>Throws<InvalidDataException>(()=>lease.Validate(sid,other,variant,variant.Entries[0],DateTimeOffset.UtcNow)));
Test("lease rejects expired view",()=>Throws<InvalidDataException>(()=>(lease with{ExpiresAt=0}).Validate(sid,uid,variant,variant.Entries[0],DateTimeOffset.UtcNow)));
Test("lease rejects entry substitution",()=>Throws<InvalidDataException>(()=>(lease with{WindowsLaunch=new("Other.exe","reviewed-relative-saves",true)}).Validate(sid,uid,variant,variant.Entries[0],DateTimeOffset.UtcNow)));
Test("SMB retains nondefault port",()=>Equal(1445,share.Port));
Test("SMB diagnostic redacts password",()=>Equal(false,share.ToString().Contains("fixture-secret")));
Test("SMB invalid port rejected",()=>Reject(()=>(share with{Port=0}).Validate()));
Test("session start failure releases lease",()=>Run(async()=>{var t=new Transport{Reply=(r,_)=>r.Method==HttpMethod.Post?lease:new object()};using var api=Client(t);api.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,sid));var v=new FixtureView{FailStart=true};var g=new AccountGate();try{await new GameRunner(api,store,v,g,"fixture").Run(game,CancellationToken.None);}catch(IOException){}Equal(false,g.Active);Equal(true,v.Disconnected);Equal("DELETE",t.Requests.Last().Method);}));
Test("unreviewed profile never connects or starts",()=>Run(async()=>{var t=new Transport{Reply=(r,_)=>r.Method==HttpMethod.Post?lease with{WindowsLaunch=null}:new object()};using var api=Client(t);api.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,sid));var v=new FixtureView();try{await new GameRunner(api,store,v,new(),"fixture").Run(game,CancellationToken.None);}catch(InvalidDataException){}Equal(false,v.Connected);Equal(false,v.Started);Equal("DELETE",t.Requests.Last().Method);}));
Test("Windows SMB hint only travels on unified launch requests",()=>Run(async()=>{
    var observed=new List<(string Path,bool Windows)>();
    var transport=new Transport{Reply=(request,_)=>{
        var path=request.RequestUri!.AbsolutePath;
        observed.Add((path,request.Headers.TryGetValues("X-GameShelf-Client-Platform",out var values)
                           && values.SequenceEqual(new[]{"windows"})));
        return new object();
    }};
    using var api=Client(transport);
    await api.Post($"/api/library/games/{gid}/session",new{});
    await api.Post($"/api/games/{gid}/session",new{});
    await api.Post($"/api/library/games/{gid}/playtime",new{});
    Equal(true,observed[0].Windows);
    Equal(false,observed[1].Windows);
    Equal(false,observed[2].Windows);
}));
Test("successful session stops before release and queues time",()=>Run(async()=>{var t=new Transport{Reply=(r,_)=>r.Method==HttpMethod.Post?lease:new object()};using var api=Client(t);api.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,sid));var v=new FixtureView();await new GameRunner(api,store,v,new(),"fixture").Run(game,CancellationToken.None);Equal(true,v.Stopped);Equal(true,v.Disconnected);Equal("DELETE",t.Requests.Last().Method);}));
Test("nonfinite end timestamp rejected",()=>Throws<ArgumentException>(()=>Run(()=>store.Enqueue(gid,time with{EventID=Guid.NewGuid(),EndedAt=double.NaN}))));
Test("missing device rejected",()=>Throws<ArgumentException>(()=>Run(()=>store.Enqueue(gid,time with{EventID=Guid.NewGuid(),DeviceID=""}))));
Test("lease rejects nonfinite expiry",()=>Throws<InvalidDataException>(()=>(lease with{ExpiresAt=double.NaN}).Validate(sid,uid,variant,variant.Entries[0],DateTimeOffset.UtcNow)));
Test("same edition may expose two platforms on one card",()=>Run(async()=>{using var api=Client(new(){Reply=(_,_)=>snapshot with{Games=[game with{Variants=[variant,variant with{Platform="macos"}]}]}});api.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,sid));await api.Library();}));
Test("edition cannot belong to two canonical cards",()=>Run(async()=>{using var api=Client(new(){Reply=(_,_)=>snapshot with{Games=[game,game with{Id=other}]}});api.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,sid));Throws<InvalidDataException>(()=>Run(()=>api.Library()));}));
Test("duplicate canonical card rejected",()=>Run(async()=>{using var api=Client(new(){Reply=(_,_)=>snapshot with{Games=[game,game]}});api.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,sid));Throws<InvalidDataException>(()=>Run(()=>api.Library()));}));
Test("HTTP POST uses bounded Content-Length, never chunked",()=>Run(async()=>{
    var listener=new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback,0);listener.Start();
    try{
        var port=((System.Net.IPEndPoint)listener.LocalEndpoint).Port;
        var inspect=Task.Run(async()=>{using var client=await listener.AcceptTcpClientAsync();using var stream=client.GetStream();using var reader=new StreamReader(stream,System.Text.Encoding.UTF8,false,1024,true);await reader.ReadLineAsync();string? header;bool length=false,chunked=false;while(!string.IsNullOrEmpty(header=await reader.ReadLineAsync())){length|=header.StartsWith("Content-Length:",StringComparison.OrdinalIgnoreCase);chunked|=header.StartsWith("Transfer-Encoding:",StringComparison.OrdinalIgnoreCase);}var response=System.Text.Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}");await stream.WriteAsync(response);return length&&!chunked;});
        using var api=new ApiClient(new Uri($"http://127.0.0.1:{port}"),sid,null,true);
        await api.Post("/api/fixture",new{message="fixture"});Equal(true,await inspect);
    }finally{listener.Stop();}
}));
foreach(var ip in new[]{"127.0.0.1","10.1.2.3","172.16.0.1","192.168.1.2","::1","fd00::1"})Test("LAN address "+ip,()=>Equal(true,Discovery.PrivateAddress(System.Net.IPAddress.Parse(ip))));
foreach(var ip in new[]{"8.8.8.8","172.15.0.1","192.169.0.1","2001:4860:4860::8888"})Test("public address "+ip,()=>Equal(false,Discovery.PrivateAddress(System.Net.IPAddress.Parse(ip))));
Test("HTTPS certificate pin compares exact DER",()=>{using var key=System.Security.Cryptography.RSA.Create(2048);var req=new System.Security.Cryptography.X509Certificates.CertificateRequest("CN=fixture",key,System.Security.Cryptography.HashAlgorithmName.SHA256,System.Security.Cryptography.RSASignaturePadding.Pkcs1);using var certificate=req.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-1),DateTimeOffset.UtcNow.AddDays(1));var pin=Convert.ToHexStringLower(System.Security.Cryptography.SHA256.HashData(certificate.RawData));Equal(true,ApiClient.MatchPin(certificate,pin));Equal(false,ApiClient.MatchPin(certificate,new string('0',64)));});
Test("R1 outbox disk error still disconnects/releases/unlocks",()=>Run(async()=>{var blocked=new AccountStore(Path.Combine(tmp,"blocked"),sid,uid);Directory.CreateDirectory(Path.Combine(blocked.DirectoryPath,"outbox.json.tmp"));var t=new Transport{Reply=(r,_)=>r.Method==HttpMethod.Post?lease:new object()};using var api=Client(t);api.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,sid));var v=new FixtureView();var gate=new AccountGate();try{await new GameRunner(api,blocked,v,gate,"fixture").Run(game,CancellationToken.None);}catch(IOException){}catch(UnauthorizedAccessException){}Equal(true,v.Stopped);Equal(true,v.Disconnected);Equal(false,gate.Active);Equal("DELETE",t.Requests.Last().Method);}));
Test("R2 total timeout includes stalled response body",()=>Run(async()=>{using var api=new ApiClient(new Uri("http://127.0.0.1:9"),sid,null,true,new SlowTransport());var client=(HttpClient)typeof(ApiClient).GetField("http",System.Reflection.BindingFlags.NonPublic|System.Reflection.BindingFlags.Instance)!.GetValue(api)!;client.Timeout=TimeSpan.FromMilliseconds(80);using var cancel=new CancellationTokenSource();var pending=api.Send<System.Text.Json.JsonElement>(HttpMethod.Get,"/api/fixture",ct:cancel.Token);var completed=await Task.WhenAny(pending,Task.Delay(400))==pending;cancel.Cancel();try{await pending;}catch(OperationCanceledException){}Equal(true,completed);}));
Test("R3 discovery rejects oversize at headers before body",()=>Run(async()=>{var listener=new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback,0);listener.Start();var send=new TaskCompletionSource();try{var port=((System.Net.IPEndPoint)listener.LocalEndpoint).Port;var server=Task.Run(async()=>{using var peer=await listener.AcceptTcpClientAsync();using var stream=peer.GetStream();using var reader=new StreamReader(stream);while(!string.IsNullOrEmpty(await reader.ReadLineAsync())){}await stream.WriteAsync(System.Text.Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Length: 1048576\r\nConnection: close\r\n\r\n"));await send.Task;try{await stream.WriteAsync(new byte[1048576]);}catch(IOException){}});var inspect=Discovery.Inspect($"http://127.0.0.1:{port}");var rejected=await Task.WhenAny(inspect,Task.Delay(400))==inspect;send.SetResult();try{await inspect;}catch(InvalidDataException){}await server;Equal(true,rejected);}finally{send.TrySetResult();listener.Stop();}}));
Test("R4 duplicate edition/platform pair rejected",()=>{using var api=Client(new(){Reply=(_,_)=>snapshot with{Games=[game with{Variants=[variant,variant with{Available=false}]}]}});api.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,sid));Throws<InvalidDataException>(()=>Run(()=>api.Library()));});
Test("R5 failed mapping claim never removes foreign same UNC mapping",()=>Run(async()=>{var backend=new ForeignMappingBackend();var view=new GameShelf.Windows.WindowsView(backend);try{await view.Connect(share,CancellationToken.None);}catch(InvalidOperationException){}await view.Disconnect();Equal(0,backend.Removed);}));
Test("R1 unconfirmed writer stop keeps lease/gate/mapping held",()=>Run(async()=>{var t=new Transport{Reply=(r,_)=>r.Method==HttpMethod.Post?lease:new object()};using var api=Client(t);api.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,sid));var view=new FixtureView{FailStop=true};var gate=new AccountGate();try{await new GameRunner(api,store,view,gate,"fixture").Run(game,CancellationToken.None);}catch(IOException){}Equal(true,gate.Active);Equal(false,view.Disconnected);Equal(false,t.Requests.Any(r=>r.Method=="DELETE"));}));
Test("unconfirmed mapping disconnect keeps lease and account gate held",()=>Run(async()=>{var t=new Transport{Reply=(r,_)=>r.Method==HttpMethod.Post?lease:new object()};using var api=Client(t);api.Restore(new("fixture",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,user,sid));var view=new FixtureView{FailDisconnect=true};var gate=new AccountGate();try{await new GameRunner(api,store,view,gate,"fixture").Run(game,CancellationToken.None);}catch(IOException){}Equal(true,view.Stopped);Equal(false,view.Disconnected);Equal(true,gate.Active);Equal(false,t.Requests.Any(r=>r.Method=="DELETE"));}));
Test("R5 only acknowledged own mapping is removed once",()=>Run(async()=>{var backend=new OwnedMappingBackend();var view=new GameShelf.Windows.WindowsView(backend);Equal("Z:\\",await view.Connect(share,CancellationToken.None));await view.Disconnect();await view.Disconnect();Equal(1445,backend.Port);Equal(1,backend.Removed);}));
Test("Windows preference chooses exact own Windows entry",()=>{var entry2=new LibraryEntry("Chinese.exe","wine",null);var preferred=game with{Variants=[variant with{Entries=[variant.Entries[0],entry2]}],PreferredEntries=new(Windows:new(gid,"Chinese.exe"))};Equal("Chinese.exe",Editions.Select(preferred).Entry.Path);});
Test("macOS preference never overrides Windows default",()=>Equal("Play.exe",Editions.Select(game with{PreferredEntries=new(Macos:new(other,"Native.app"))}).Entry.Path));
Test("unavailable Windows preference fails closed",()=>Throws<InvalidOperationException>(()=>Editions.Select(game with{PreferredEntries=new(Windows:new(other,"Unknown.exe"))})));
var localRoot=Path.Combine(tmp,"standalone");var source=Path.Combine(tmp,"sources");Directory.CreateDirectory(source);
void Exe(string folder,string name){Directory.CreateDirectory(folder);var bytes=new byte[128];bytes[0]=(byte)'M';bytes[1]=(byte)'Z';bytes[60]=64;bytes[64]=(byte)'P';bytes[65]=(byte)'E';bytes[68]=0x64;bytes[69]=0x86;File.WriteAllBytes(Path.Combine(folder,name),bytes);}
var folderA=Path.Combine(source,"Game A");var folderB=Path.Combine(source,"Game B");Exe(folderA,"Play.exe");Exe(folderA,"Chinese.exe");Exe(folderA,"setup.exe");Exe(folderA,"custom.exe");Exe(folderA,"config.exe");Exe(folderA,"installer.exe");Exe(folderB,"Game.exe");File.WriteAllText(Path.Combine(folderB,"Fake.exe"),"MZ invalid");
var boundarySource=Path.Combine(tmp,"boundary-sources");var boundaryFolder=Path.Combine(boundarySource,"Boundary Game");
foreach(var path in helperCases.Concat(titleCases))Exe(boundaryFolder,path);
Test("standalone scan excludes bounded helpers and retains real title prefixes",()=>Run(async()=>{var scanned=await StandaloneLibrary.Open(Path.Combine(tmp,"boundary-library"));await scanned.AddFolder(boundarySource);await scanned.Scan();var entries=scanned.Candidates.Single().Entries.Select(e=>e.Path).Order().ToArray();Equal(string.Join("|",titleCases.Order()),string.Join("|",entries));}));
StandaloneLibrary local=null!;
Test("standalone folders scan cached without adding",()=>Run(async()=>{local=await StandaloneLibrary.Open(localRoot);await local.AddFolder(source);await local.Scan();Equal(2,local.Candidates.Length);Equal(0,local.Library().Games.Length);}));
Test("standalone language entries grouped, helpers excluded",()=>{var entries=local.Candidates.Single(c=>c.Title=="Game A").Entries;Equal(2,entries.Length);Equal(true,entries.Any(e=>e.Path=="Chinese.exe"));});
Test("standalone header probe rejects fake PE and script",()=>{Equal(false,StandaloneLibrary.ProbeExe(Path.Combine(folderB,"Fake.exe")));Equal(false,Safety.NativeEntry("run.cmd","native"));});
Test("standalone cached candidates survive restart",()=>Run(async()=>Equal(2,(await StandaloneLibrary.Open(localRoot)).Candidates.Length)));
Test("standalone adds selected only with chosen language",()=>Run(async()=>{var a=local.Candidates.Single(c=>c.Title=="Game A");await local.AddSelected([a.Id],new(){[a.Id]="Chinese.exe"});Equal(1,local.Library().Games.Length);Equal("Chinese.exe",Editions.Select(local.Library().Games[0]).Entry.Path);}));
Test("standalone rejects unknown selected candidate",()=>Throws<ArgumentException>(()=>Run(()=>local.AddSelected([other]))));
Test("standalone time persists and event retries are idempotent",()=>Run(async()=>{var id=local.Library().Games[0].Id;var eid=Guid.NewGuid();await local.RecordTime(id,eid,5,1000);await local.RecordTime(id,eid,5,1000);Equal(5d,(await StandaloneLibrary.Open(localRoot)).Library().Games[0].PlaytimeSeconds);}));
Test("standalone metadata is separate from every connected identity",()=>Run(async()=>{var connected=new AccountStore(localRoot,sid,uid);Equal(null,await connected.ReadLibrary());Equal(false,connected.DirectoryPath==local.DirectoryPath);await connected.SaveLibrary(snapshot);Equal("Game A",(await StandaloneLibrary.Open(localRoot)).Library().Games[0].Title);}));
Test("standalone unknown game cannot receive time",()=>Throws<ArgumentException>(()=>Run(()=>local.RecordTime(other,Guid.NewGuid(),5,1000))));
Test("standalone rejects selected helper path",()=>Throws<InvalidOperationException>(()=>Run(()=>local.AddSelected([local.Candidates.Single(c=>c.Title=="Game A").Id],new(){[local.Candidates.Single(c=>c.Title=="Game A").Id]="setup.exe"}))));
Test("standalone disk failure rolls back time and permits retry",()=>Run(async()=>{var id=local.Library().Games[0].Id;var before=local.Library().Games[0].PlaytimeSeconds;var eid=Guid.NewGuid();var block=Path.Combine(local.DirectoryPath,"library.json.tmp");Directory.CreateDirectory(block);try{await local.RecordTime(id,eid,3,1001);}catch(IOException){}catch(UnauthorizedAccessException){}Directory.Delete(block);await local.RecordTime(id,eid,3,1001);Equal(before+3,(await StandaloneLibrary.Open(localRoot)).Library().Games[0].PlaytimeSeconds);}));
Test("standalone confirmed stop releases gate despite dispose error",()=>Run(async()=>{var gate=new AccountGate();var view=new FixtureView{FailDispose=true};try{await new StandaloneRunner(local,gate,view.Start).Run(local.Library().Games[0].Id,CancellationToken.None);}catch(IOException){}Equal(true,view.Stopped);Equal(false,gate.Active);}));
var renpyRoot=Path.Combine(tmp,"renpy-view");LaunchLease renpy=null!;var renpyVariant=variant with{Entries=[new("Play.exe","renpy","RenPy")],SaveCompatibilityKey="fixture-compatibility"};
Test("RenPy fixture MIT patch equals known exported module",()=>Equal(RenpyAdapter.PatchedSHA256,RenpyFixture.Digest(RenpyFixture.PatchedModule())));
Test("namespace six fields validate own canonical/compatibility",()=>Run(async()=>{renpy=await RenpyFixture.Export(renpyRoot,sid,uid,gid,gid);renpy.SaveNamespace!.Validate(sid,uid,gid,renpyVariant.SaveCompatibilityKey);}));
Test("namespace rejects other account",()=>Throws<InvalidDataException>(()=>renpy.SaveNamespace!.Validate(sid,other,gid)));
Test("namespace rejects path override",()=>Throws<InvalidDataException>(()=>(renpy.SaveNamespace! with{RelativePath="../outside"}).Validate(sid,uid,gid)));
Test("RenPy proof matches namespace, edition and exact entry",()=>RenpyAdapter.ValidateProof(renpy,renpyVariant,renpyVariant.Entries[0],gid));
Test("RenPy 8.3.2 proof and export require the paired patch",()=>Run(async()=>
{
    var root=Path.Combine(tmp,"renpy-832-view");
    var lease=await RenpyFixture.Export(root,sid,uid,gid,gid);
    const string source="3ed845b5c9d13dcf581a9143b5fc933fc1dfad8f03c93bccc2ef0955adb7e884";
    var review=lease.RenpyLaunch!;
    var bytes=System.Text.Encoding.UTF8.GetBytes(System.Text.Encoding.UTF8.GetString(
        RenpyFixture.PatchedModule()).Replace("Copyright 2004-2025","Copyright 2004-2024"));
    var mixed=review.Fingerprints.Select(f=>f.Role=="locations"?f with{SourceSHA256=source}:f).ToArray();
    Throws<InvalidDataException>(()=>RenpyAdapter.ValidateProof(
        lease with{RenpyLaunch=review with{Fingerprints=mixed}},renpyVariant,renpyVariant.Entries[0],gid));
    var paired=mixed.Select(f=>f.Role=="locations"?f with{ExportSHA256=RenpyFixture.Digest(bytes)}:f).ToArray();
    lease=lease with{RenpyLaunch=review with{Fingerprints=paired}};
    RenpyAdapter.ValidateProof(lease,renpyVariant,renpyVariant.Entries[0],gid);
    await File.WriteAllBytesAsync(Path.Combine(root,"renpy/savelocation.py"),bytes);
    await RenpyAdapter.Prepare(root,lease);
}));
foreach(var (version,sourceDigest,patchedDigest) in new[]
{
    ("8.1.3","2fcd8f8dd88e85fd689a91db1f98c7ab7c5790d3b871b1f89420e27db3bba8f2","178aaa36fba90afcaed683e31048200328653fbbf4bc7c6c7e9d5ac65f721c24"),
    ("8.5.2","e0b32aab4127325a307a8396b6a9b636781161fd23d595127e668d784342b85c","1b4b59ac6e0359d99da69378a18a446eecb0c08038e7d06452e56a19bbca46d4"),
})
{
    Test($"RenPy {version} accepts only its paired single-save-location patch",()=>
    {
        var proof=renpy.RenpyLaunch!;
        var exact=proof.Fingerprints.Select(f=>f.Role=="locations"?f with{SourceSHA256=sourceDigest,ExportSHA256=patchedDigest}:f).ToArray();
        RenpyAdapter.ValidateProof(renpy with{RenpyLaunch=proof with{Fingerprints=exact}},renpyVariant,renpyVariant.Entries[0],gid);
        var original=exact.Select(f=>f.Role=="locations"?f with{ExportSHA256=sourceDigest}:f).ToArray();
        Throws<InvalidDataException>(()=>RenpyAdapter.ValidateProof(
            renpy with{RenpyLaunch=proof with{Fingerprints=original}},renpyVariant,renpyVariant.Entries[0],gid));
    });
}
Test("RenPy mismatched review revision rejected",()=>Throws<InvalidDataException>(()=>RenpyAdapter.ValidateProof(renpy with{WindowsLaunch=renpy.WindowsLaunch! with{ProfileRevision=2}},renpyVariant,renpyVariant.Entries[0],gid)));
Test("RenPy missing critical runtime role rejected",()=>Throws<InvalidDataException>(()=>RenpyAdapter.ValidateProof(renpy with{RenpyLaunch=renpy.RenpyLaunch! with{Fingerprints=renpy.RenpyLaunch!.Fingerprints.Where(f=>f.Role!="config").ToArray()}},renpyVariant,renpyVariant.Entries[0],gid)));
Test("RenPy launch uses only fixed savedir/three env names",()=>Run(async()=>{var plan=await RenpyAdapter.Prepare(renpyRoot,renpy);Equal("--savedir",plan.Arguments[0]);Equal(Path.GetFullPath(Path.Combine(renpyRoot,RenpyAdapter.RelativePath)),plan.Arguments[1]);Equal(3,plan.Environment.Count);Equal(false,plan.Environment.ContainsKey("APPDATA"));Equal("1",plan.Environment["PYTHONDONTWRITEBYTECODE"]);}));
Test("RenPy missing namespace directory never opens empty fallback",()=>{var missing=Path.Combine(tmp,"missing-view");Directory.CreateDirectory(missing);Throws<InvalidDataException>(()=>Run(()=>RenpyAdapter.Prepare(missing,renpy)));});
Test("RenPy changed export module rejected before execution",()=>Run(async()=>{var path=Path.Combine(renpyRoot,"renpy/savelocation.py");await File.WriteAllTextAsync(path,"unreviewed");Throws<InvalidDataException>(()=>Run(()=>RenpyAdapter.Prepare(renpyRoot,renpy)));await File.WriteAllBytesAsync(path,RenpyFixture.PatchedModule());}));
Test("server raw environment and arguments fields rejected",()=>{var json=System.Text.Json.JsonSerializer.Serialize(renpy.WindowsLaunch,AccountStore.Json).TrimEnd('}')+",\"environment\":{\"APPDATA\":\"other\"},\"arguments\":[\"--bad\"]}";Throws<System.Text.Json.JsonException>(()=>System.Text.Json.JsonSerializer.Deserialize<WindowsLaunch>(json,AccountStore.Json));});
Test("namespace without advertised compatibility key rejected",()=>Throws<InvalidDataException>(()=>RenpyAdapter.ValidateProof(renpy,renpyVariant with{SaveCompatibilityKey=null},renpyVariant.Entries[0],gid)));
Test("namespace empty or oversized compatibility key rejected",()=>{foreach(var value in new[]{"",new string('a',129)})Throws<InvalidDataException>(()=>renpy.SaveNamespace!.Validate(sid,uid,gid,value));});
Test("RenPy runtime/config fingerprint cannot target profile storage",()=>Throws<InvalidDataException>(()=>RenpyAdapter.ValidateProof(renpy with{RenpyLaunch=renpy.RenpyLaunch! with{Fingerprints=renpy.RenpyLaunch!.Fingerprints.Select(f=>f.Role=="config"?f with{Path=".gameshelf-profile/Config.py"}:f).ToArray()}},renpyVariant,renpyVariant.Entries[0],gid)));
Test("RenPy critical runtime role cannot point to a decoy module",()=>Throws<InvalidDataException>(()=>RenpyAdapter.ValidateProof(renpy with{RenpyLaunch=renpy.RenpyLaunch! with{Fingerprints=renpy.RenpyLaunch!.Fingerprints.Select(f=>f.Role=="locations"?f with{Path="other/location.py"}:f).ToArray()}},renpyVariant,renpyVariant.Entries[0],gid)));
Directory.Delete(tmp,true);
Console.WriteLine($"{count-failures}/{count} passed"); return failures==0?0:1;
