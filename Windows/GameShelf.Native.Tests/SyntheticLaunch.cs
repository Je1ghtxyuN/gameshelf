using GameShelf.Core;
using GameShelf.Windows;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
static class SyntheticLaunch
{
    public static async Task Run()
    {
        var root=Path.Combine(Path.GetTempPath(),"GameShelf-isolation-"+Guid.NewGuid());Directory.CreateDirectory(root);
        try{
            await using var server=new LeaseServer();
            var exe=Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,"../../../../GameShelf.LaunchFixture/bin/Release/net10.0"));
            foreach(var account in new[]{"A","B"}){
                var viewRoot=Path.Combine(root,"views",account);Directory.CreateDirectory(viewRoot);
                foreach(var name in new[]{"Fixture.exe","Fixture.dll","Fixture.deps.json","Fixture.runtimeconfig.json"})File.Copy(Path.Combine(exe,name),Path.Combine(viewRoot,name));
                using var api=new ApiClient(new Uri($"http://127.0.0.1:{server.Port}"),LeaseServer.ServerID,null,true);
                var login=await api.Login(account,"synthetic-only");var snapshot=await api.Library();
                var store=new AccountStore(Path.Combine(root,"cache"),login.ServerID,login.User.Id);await store.SaveLibrary(snapshot);
                var view=new LocalApprovedView(viewRoot,"fixture-"+account);var gate=new AccountGate();
                await new GameRunner(api,store,view,gate,"windows-fixture-device").Run(snapshot.Games[0],CancellationToken.None);
                if(gate.Active||!view.Disconnected||server.Active.Contains(login.User.Id))throw new Exception("Lease/view not cleaned");
                var save=Path.Combine(viewRoot,"savedata/progress.txt");if(await File.ReadAllTextAsync(save)!=account)throw new Exception("Save written outside selected view");
                if(account=="A"&&File.Exists(Path.Combine(root,"views/B/savedata/progress.txt")))throw new Exception("Account B inherited save");
                var pending=await store.Pending();if(pending.Length!=1||pending[0].Event.ElapsedSeconds<2)throw new Exception("Elapsed time not observed");
                var retainedID=pending[0].Event.EventID;
                await store.Flush(async item=>{await api.Post($"/api/library/games/{item.GameID}/playtime",item.Event);});
                // Simulate response lost/retry with identical stable event UUID.
                await api.Post($"/api/library/games/{LeaseServer.GameID}/playtime",pending[0].Event);
                if((await store.Pending()).Length!=0||server.Events[login.User.Id].Count!=1||!server.Events[login.User.Id].Contains(retainedID))throw new Exception("Time event not acknowledged idempotently");
            }
            if(await File.ReadAllTextAsync(Path.Combine(root,"views/A/savedata/progress.txt"))!="A")throw new Exception("Account A save overwritten");
            Console.WriteLine("OBSERVED two real synthetic EXE launches; A/B relative saves isolated; two unique time events >2s; identical retries counted once; leases closed");
        }finally{Directory.Delete(root,true);}
    }
    public static async Task RunStandalone()
    {
        var root=Path.Combine(Path.GetTempPath(),"GameShelf-standalone-"+Guid.NewGuid());Directory.CreateDirectory(root);
        try{
            var source=Path.Combine(root,"games","LocalFixtureGame");Directory.CreateDirectory(source);
            var binaries=Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,"../../../../GameShelf.LaunchFixture/bin/Release/net10.0"));
            foreach(var name in new[]{"Fixture.exe","Fixture.dll","Fixture.deps.json","Fixture.runtimeconfig.json"})File.Copy(Path.Combine(binaries,name),Path.Combine(source,name));
            var profile=await StandaloneLibrary.Open(Path.Combine(root,"data"));await profile.AddFolder(Path.Combine(root,"games"));await profile.Scan();if(profile.Candidates.Length!=1||profile.Library().Games.Length!=0)throw new Exception("Scan implicitly added fixture");await profile.AddSelected([profile.Candidates[0].Id]);
            var gate=new AccountGate();var running=new StandaloneRunner(profile,gate,(folder,entry,ct)=>Task.FromResult<IGameProcess>(NativeJob.Start(Path.Combine(folder,entry),folder))).Run(profile.Library().Games[0].Id,CancellationToken.None);
            try{gate.CanSwitch();throw new Exception("Standalone allowed identity switching during play");}catch(InvalidOperationException){}
            await running;gate.CanSwitch();var loaded=await StandaloneLibrary.Open(Path.Combine(root,"data"));if(loaded.Library().Games[0].PlaytimeSeconds<2)throw new Exception("Standalone time did not persist");if(await File.ReadAllTextAsync(Path.Combine(source,"savedata/progress.txt"))!="LocalFixtureGame")throw new Exception("Standalone usual relative save missing");if(await new AccountStore(Path.Combine(root,"data"),LeaseServer.ServerID,new string('2',32)).ReadLibrary()!=null)throw new Exception("Standalone adopted by connected account");
            Console.WriteLine("OBSERVED standalone fixture EXE; usual relative saves; local time >2s persisted; identity switch blocked while running; no connected-account adoption");
        }finally{Directory.Delete(root,true);}
    }
    public static async Task RunRenpyAdapter()
    {
        var root=Path.Combine(Path.GetTempPath(),"GameShelf-renpy-adapter-"+Guid.NewGuid());Directory.CreateDirectory(root);
        try{
            var sid=new string('1',32);var uid=new string('2',32);var canonical=new string('3',32);var variantID=new string('4',32);
            var lease=await RenpyFixture.Export(root,sid,uid,canonical,variantID);
            var binaryDir=Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,"../../../../GameShelf.LaunchFixture/bin/Release/net10.0"));
            File.Copy(Path.Combine(binaryDir,"Fixture.exe"),Path.Combine(root,"Play.exe"),true);
            foreach(var name in new[]{"Fixture.dll","Fixture.deps.json","Fixture.runtimeconfig.json"})File.Copy(Path.Combine(binaryDir,name),Path.Combine(root,name),true);
            var hash=RenpyFixture.Digest(await File.ReadAllBytesAsync(Path.Combine(root,"Play.exe")));
            lease=lease with{WindowsLaunch=lease.WindowsLaunch! with{EntrySHA256=hash},RenpyLaunch=lease.RenpyLaunch! with{Fingerprints=lease.RenpyLaunch!.Fingerprints.Select(f=>f.Role=="entry"?f with{SourceSHA256=hash,ExportSHA256=hash}:f).ToArray()}};
            var variant=new LibraryVariant(variantID,"windows",null,[new("Play.exe","renpy","RenPy")],"fixture-compatibility",true);
            lease.Validate(sid,uid,variant,variant.Entries[0],DateTimeOffset.UtcNow,canonical);
            var originalAppData=Environment.GetEnvironmentVariable("APPDATA");
            await using var process=await new WindowsView().StartReviewed(root,"Play.exe",lease,CancellationToken.None);
            using var timeout=new CancellationTokenSource(15000);await process.Wait(timeout.Token);await process.Stop();
            var save=Path.GetFullPath(Path.Combine(root,RenpyAdapter.RelativePath));var observed=JsonSerializer.Deserialize<JsonElement>(await File.ReadAllTextAsync(Path.Combine(save,"native-adapter-observed.json")));
            var arguments=observed.GetProperty("arguments").Deserialize<string[]>()!;
            if(arguments.Length!=2||arguments[0]!="--savedir"||arguments[1]!=save||observed.GetProperty("auxiliary").GetString()!=Path.Combine(save,"Auxiliary")||observed.GetProperty("multiPersistent").GetString()!=Path.Combine(save,"MultiPersistent")||observed.GetProperty("dontWriteBytecode").GetString()!="1")throw new Exception("Fixed adapter fields differ");
            if(observed.GetProperty("appData").GetString()!=originalAppData||Environment.GetEnvironmentVariable("APPDATA")!=originalAppData)throw new Exception("OS AppData was overridden");
            if(File.Exists(Path.Combine(root,"savedata/progress.txt")))throw new Exception("Adapter silently fell back to original relative save");
            Console.WriteLine("OBSERVED native reviewed adapter: exact --savedir; fixed Auxiliary/MultiPersistent/PYTHONDONTWRITEBYTECODE; original APPDATA unchanged; pinned module verified; no NAS or actual RenPy game");
        }finally{Directory.Delete(root,true);}
    }
    sealed class LocalApprovedView(string root,string name):IWindowsView
    {
        public bool Disconnected;
        public Task<string> Connect(SmbShare share,CancellationToken ct){if(share.ShareName!=name||share.Port!=1445)throw new Exception("Wrong fixture view/port");return Task.FromResult(root);}
        public Task Disconnect(){Disconnected=true;return Task.CompletedTask;}
        public Task<IGameProcess> Start(string view,string entry,CancellationToken ct){if(view!=root||Safety.Relative(entry)!="Fixture.exe")throw new Exception("Unsafe fixture launch");return Task.FromResult<IGameProcess>(NativeJob.Start(Path.Combine(root,entry),root));}
    }
    sealed class LeaseServer:IAsyncDisposable
    {
        public static string ServerID=>new('1',32);public static string GameID=>new('3',32);
        readonly TcpListener listener=new(IPAddress.Loopback,0);readonly CancellationTokenSource stop=new();readonly Task loop;
        public int Port=>((IPEndPoint)listener.LocalEndpoint).Port;
        public HashSet<string> Active {get;}=[];public Dictionary<string,HashSet<Guid>> Events {get;}=[];
        public LeaseServer(){listener.Start();loop=Serve();}
        async Task Serve()
        {
            while(!stop.IsCancellationRequested){
                TcpClient client;try{client=await listener.AcceptTcpClientAsync(stop.Token);}catch(OperationCanceledException){break;}
                using(client){using var stream=client.GetStream();using var reader=new StreamReader(stream,Encoding.UTF8,false,1024,true);
                var first=await reader.ReadLineAsync();if(first==null)continue;var parts=first.Split(' ');var length=0;string auth="";string? h;
                while(!string.IsNullOrEmpty(h=await reader.ReadLineAsync())){if(h.StartsWith("Content-Length:",StringComparison.OrdinalIgnoreCase))length=int.Parse(h[15..]);if(h.StartsWith("Authorization:",StringComparison.OrdinalIgnoreCase))auth=h;}
                if(length>16384)throw new Exception("Unbounded request");var chars=new char[length];var read=0;while(read<length){var n=await reader.ReadAsync(chars.AsMemory(read));if(n==0)throw new EndOfStreamException();read+=n;}var body=new string(chars);var route=parts[1];var account=auth.EndsWith("B")?"B":"A";var uid=new string(account=="A"?'2':'6',32);object value=new{};
                if(route=="/api/login"){account=JsonDocument.Parse(body).RootElement.GetProperty("username").GetString()!;uid=new string(account=="A"?'2':'6',32);value=new LoginResult("fixture-token-"+account,DateTimeOffset.UtcNow.ToUnixTimeSeconds()+300,new(uid,account,false),ServerID);}
                else if(route=="/api/library")value=new LibrarySnapshot(2,ServerID,new(uid,account,false),[new(GameID,"Synthetic Approved EXE",0,false,[],0,null,null,[new(GameID,"windows",null,[new("Fixture.exe","native","fixture")],null,true)])],[]);
                else if(route.EndsWith("/session")){if(!Active.Add(uid))throw new Exception("Duplicate writer");value=new LaunchLease(new string(account=="A"?'5':'7',32),DateTimeOffset.UtcNow.ToUnixTimeSeconds()+90,ServerID,uid,new(GameID),new("127.0.0.1",1445,"fixture-"+account,null,"synthetic","synthetic"),new("Fixture.exe","reviewed-relative-saves",true));}
                else if(parts[0]=="DELETE")Active.Remove(uid);
                else if(route.EndsWith("/playtime")){var item=JsonSerializer.Deserialize<TimeEvent>(body,AccountStore.Json)!;if(!Events.TryGetValue(uid,out var ids))Events[uid]=ids=[];ids.Add(item.EventID);value=new{playtimeSeconds=item.ElapsedSeconds,lastPlayed=item.EndedAt};}
                else throw new Exception("Unexpected fixture route");
                var bytes=JsonSerializer.SerializeToUtf8Bytes(value,AccountStore.Json);var headers=Encoding.ASCII.GetBytes($"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {bytes.Length}\r\nConnection: close\r\n\r\n");await stream.WriteAsync(headers);await stream.WriteAsync(bytes);
                }
            }
        }
        public async ValueTask DisposeAsync(){stop.Cancel();listener.Stop();await loop;stop.Dispose();}
    }
}
