using System.IO.Pipes;
using System.Security.Cryptography;
using GameShelf.Core;
using GameShelf.NetworkSession;
namespace GameShelf.Windows;
internal sealed record BrokerChallenge(string Value);
internal sealed class BrokerConnection
{
    static readonly System.Collections.Concurrent.ConcurrentDictionary<Guid,BrokerConnection> pending=new();
    readonly Guid operation;readonly BrokerRpc rpc;
    readonly NamedPipeServerStream pipe;readonly BrokerPackage package;readonly NetworkNative.ChildProcess child;
    bool closed;
    BrokerConnection(NamedPipeServerStream pipe,BrokerPackage package,NetworkNative.ChildProcess child,Guid operation){this.pipe=pipe;this.package=package;this.child=child;this.operation=operation;rpc=new(pipe,operation);}
    public static async Task<BrokerConnection> Open(SmbShare share,CancellationToken ct)
    {
        var op=Guid.NewGuid();var package=new BrokerPackage();var name="GameShelf-Broker-"+op.ToString("N");
        var pipe=new NamedPipeServerStream(name,PipeDirection.InOut,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous|PipeOptions.CurrentUserOnly|PipeOptions.FirstPipeInstance,BrokerWire.Limit,BrokerWire.Limit);
        NetworkNative.ChildProcess? child=null;
        try{
            child=NetworkNative.Launch(package.Executable,name,op,share);
            using var deadline=CancellationTokenSource.CreateLinkedTokenSource(ct);deadline.CancelAfter(TimeSpan.FromSeconds(15));
            await pipe.WaitForConnectionAsync(deadline.Token);
            if(!NetworkNative.GetNamedPipeClientProcessId(pipe.SafePipeHandle.DangerousGetHandle(),out uint pid)||pid!=child.Identity.Pid)throw new BrokerFailure(BrokerStage.Identity);
            var hello=await BrokerWire.Read<BrokerPeer>(pipe,deadline.Token);BrokerHandshake.Hello(child.Identity,hello);
            var nonce=Convert.ToHexString(RandomNumberGenerator.GetBytes(32));await BrokerWire.Write(pipe,new BrokerChallenge(nonce),deadline.Token);
            var response=await BrokerWire.Read<BrokerChallenge>(pipe,deadline.Token);BrokerHandshake.Nonce(nonce,response.Value);
            child.ReleaseStartupGuard(authenticated:true);
            var connection=new BrokerConnection(pipe,package,child,op);pending[op]=connection;return connection;
        }catch(BrokerFailure){pipe.Dispose();child?.Dispose();package.Dispose();throw;}catch{pipe.Dispose();child?.Dispose();package.Dispose();throw new BrokerFailure(BrokerStage.Identity);}
    }
    public string Root=>"broker:"+operation.ToString("N");
    public Task<BrokerStatus> Call(BrokerCommand command,CancellationToken ct,WindowsLeaseContext? context=null,string? lease=null,double? expires=null)
    {if(closed)throw new BrokerFailure(BrokerStage.Transport);return rpc.Call(command,ct,context,lease,expires);}
    public async Task Close()
    {
        if(closed)return;
        var status=await Call(BrokerCommand.Disconnect,CancellationToken.None);
        if(!status.NativeStopped||!status.MappingGone)throw new BrokerFailure(BrokerStage.Disconnect);
        using var timeout=new CancellationTokenSource(TimeSpan.FromSeconds(10));await child.NormalExit(timeout.Token);
        closed=true;pipe.Dispose();child.Dispose();package.Dispose();pending.TryRemove(operation,out _);
    }
}
