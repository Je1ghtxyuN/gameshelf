using GameShelf.Core;
namespace GameShelf.NetworkSession;
public sealed class BrokerRpc(Stream stream,Guid operation)
{
    readonly SemaphoreSlim serial=new(1,1);long sequence;bool poisoned;
    public async Task<BrokerStatus> Call(BrokerCommand command,CancellationToken ct,WindowsLeaseContext? context=null,string? lease=null,double? expires=null)
    {
        await serial.WaitAsync(ct);bool written=false,completed=false;try{
            if(poisoned)throw new BrokerFailure(BrokerStage.Transport);
            using var timeout=CancellationTokenSource.CreateLinkedTokenSource(ct);timeout.CancelAfter(TimeSpan.FromSeconds(command is BrokerCommand.Connect or BrokerCommand.Start?45:12));
            var request=new BrokerRequest(1,operation,sequence+1,command,context,lease,expires);
            var frame=BrokerWire.Encode(request);timeout.Token.ThrowIfCancellationRequested();written=true;sequence++;
            await stream.WriteAsync(frame,timeout.Token);await stream.FlushAsync(timeout.Token);var reply=await BrokerWire.Read<BrokerReply>(stream,timeout.Token);
            if(reply.Version!=1||reply.Operation!=operation||reply.Sequence!=sequence||reply.Command!=command)throw new BrokerFailure(BrokerStage.Protocol);
            if(!reply.Success){if(reply.Error is not BrokerStage stage||!Enum.IsDefined(stage)||reply.Status!=null||reply.NativeCode is <0 or >65535)throw new BrokerFailure(BrokerStage.Protocol);completed=true;throw new BrokerFailure(stage,reply.NativeCode);}
            var status=reply.Status??throw new BrokerFailure(BrokerStage.Protocol);if(reply.Error!=null||reply.NativeCode!=null)throw new BrokerFailure(BrokerStage.Protocol);completed=true;return status;
        }catch(BrokerFailure){if(written&&!completed){poisoned=true;stream.Dispose();}throw;}catch{if(written&&!completed){poisoned=true;stream.Dispose();}throw new BrokerFailure(BrokerStage.Transport);}finally{serial.Release();}
    }
}
public sealed class BrokerProcessProxy(Func<BrokerCommand,CancellationToken,Task<BrokerStatus>> call):IGameProcess
{
    public async Task Wait(CancellationToken ct){while(true){if((await call(BrokerCommand.Status,ct)).Exited)return;await Task.Delay(250,ct);}}
    public async Task Stop(){if(!(await call(BrokerCommand.Stop,CancellationToken.None)).NativeStopped)throw new BrokerFailure(BrokerStage.Stop);}
    // The view, not a disposed game-process proxy, owns the mapping/session channel.
    public ValueTask DisposeAsync()=>ValueTask.CompletedTask;
}
