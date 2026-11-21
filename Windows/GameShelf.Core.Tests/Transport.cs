using System.Net;
using System.Text;
using System.Text.Json;
using GameShelf.Core;
sealed class Transport:HttpMessageHandler
{
    public Func<HttpRequestMessage,string,object> Reply {get;set;}=(_,_)=>new{};
    public int Status {get;set;}=200;
    public List<(string Method,string Path,string? Token,string Body)> Requests {get;}=[];
    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage r,CancellationToken ct)
    {
        var body=r.Content==null?"":await r.Content.ReadAsStringAsync(ct);Requests.Add((r.Method.Method,r.RequestUri!.AbsolutePath,r.Headers.Authorization?.Parameter,body));
        return new HttpResponseMessage((HttpStatusCode)Status){Content=new StringContent(JsonSerializer.Serialize(Reply(r,body),AccountStore.Json),Encoding.UTF8,"application/json")};
    }
}
sealed class FixtureView: IWindowsView
{
    public bool Connected,Started,Disconnected,Stopped;
    public bool FailStart,FailStop,FailDispose,FailDisconnect;
    public Task<string> Connect(SmbShare share,CancellationToken ct){Connected=true;return Task.FromResult("Z:\\");}
    public Task Disconnect(){if(FailDisconnect)throw new IOException("Fixture could not confirm disconnect");Disconnected=true;return Task.CompletedTask;}
    public Task<IGameProcess> Start(string root,string path,CancellationToken ct){Started=true;if(FailStart)throw new IOException("fixture start failed");return Task.FromResult<IGameProcess>(new Process(this));}
    sealed class Process(FixtureView owner):IGameProcess
    {
        public Task Wait(CancellationToken ct)=>Task.CompletedTask;
        public Task Stop(){if(owner.FailStop)throw new IOException("Fixture could not confirm stop");owner.Stopped=true;return Task.CompletedTask;}
        public ValueTask DisposeAsync(){if(owner.FailDispose)throw new IOException("Fixture dispose failed");return ValueTask.CompletedTask;}
    }
}
sealed class SlowTransport:HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage r,CancellationToken ct)=>Task.FromResult(new HttpResponseMessage(System.Net.HttpStatusCode.OK){Content=new StreamContent(new SlowBody())});
    sealed class SlowBody:Stream
    {
        public override bool CanRead=>true;public override bool CanSeek=>false;public override bool CanWrite=>false;public override long Length=>throw new NotSupportedException();public override long Position{get=>throw new NotSupportedException();set=>throw new NotSupportedException();}
        public override async ValueTask<int> ReadAsync(Memory<byte> data,CancellationToken ct=default){await Task.Delay(Timeout.Infinite,ct);return 0;}
        public override int Read(byte[] b,int o,int n)=>throw new NotSupportedException();public override void Flush()=>throw new NotSupportedException();public override long Seek(long o,SeekOrigin s)=>throw new NotSupportedException();public override void SetLength(long n)=>throw new NotSupportedException();public override void Write(byte[] b,int o,int n)=>throw new NotSupportedException();
    }
}

sealed class ForeignMappingBackend:GameShelf.Windows.IMappingBackend
{
    public int Removed;
    public string ChooseDrive()=>"Z:";
    public Task<GameShelf.Windows.MappingReceipt> Create(SmbShare s,string d,Guid operation,CancellationToken ct)=>throw new InvalidOperationException("Drive already mapped by someone else");
    public Task Remove(GameShelf.Windows.MappingReceipt r){Removed++;return Task.CompletedTask;}
}

sealed class OwnedMappingBackend:GameShelf.Windows.IMappingBackend
{
    public int Removed;public int Port;
    public string ChooseDrive()=>"Z:";
    public Task<GameShelf.Windows.MappingReceipt> Create(SmbShare s,string d,Guid operation,CancellationToken ct){Port=s.Port;return Task.FromResult(new GameShelf.Windows.MappingReceipt(operation,d,s.Unc,s.Host,s.ShareName,s.Port,"fixture-credential"));}
    public Task Remove(GameShelf.Windows.MappingReceipt r){if(r.Port!=Port||r.Credential!="fixture-credential")throw new Exception("Wrong mapping identity");Removed++;return Task.CompletedTask;}
}

sealed class BoundFixtureView:ILeaseBoundWindowsView
{
    public bool Bound,Renewed,Disconnected;public bool RejectRenew=false;public int WaitDelayMs=0;
    public Task BindLease(WindowsLeaseContext context,CancellationToken ct){context.Validate(DateTimeOffset.UtcNow);Bound=true;return Task.CompletedTask;}
    public Task RenewLease(string id,double expires,CancellationToken ct){if(RejectRenew)throw new IOException("Fixture lost renewal acknowledgement");Renewed=true;return Task.CompletedTask;}
    public Task<string> Connect(SmbShare share,CancellationToken ct){if(!Bound)throw new InvalidOperationException("Lease not bound before connect");return Task.FromResult("broker:fixture");}
    public Task Disconnect(){Disconnected=true;return Task.CompletedTask;}
    public Task<IGameProcess> Start(string root,string entry,CancellationToken ct)=>Task.FromResult<IGameProcess>(new BoundProcess(WaitDelayMs));
    sealed class BoundProcess(int delay):IGameProcess {public Task Wait(CancellationToken ct)=>Task.Delay(delay,ct);public Task Stop()=>Task.CompletedTask;public ValueTask DisposeAsync()=>ValueTask.CompletedTask;}
}


sealed class MountedFixtureView : IMountedWindowsView
{
    public FixtureView Process { get; } = new();

    public Task<string> ConnectMounted(WindowsLeaseContext context, string expectedShare, CancellationToken ct)
    {
        context.Validate(DateTimeOffset.UtcNow);
        if (context.Lease.MountedWorkspace?.Share != expectedShare)
            throw new InvalidDataException("Wrong fixture share");
        return Task.FromResult(@"Z:\fixture");
    }

    public Task<string> Connect(SmbShare share, CancellationToken ct) =>
        throw new InvalidOperationException("Unexpected request to create an SMB connection");

    public Task Disconnect() => Process.Disconnect();

    public Task<IGameProcess> Start(string root, string entry, CancellationToken ct) =>
        Process.Start(root, entry, ct);

    public Task<IGameProcess> StartReviewed(string root, string entry, LaunchLease lease, CancellationToken ct) =>
        throw new InvalidOperationException("Unexpected engine-specific launch");
}
