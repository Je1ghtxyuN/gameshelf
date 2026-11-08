using GameShelf.Core;
namespace GameShelf.NetworkSession;
// No filesystem or OS calls here: the helper supplies the actual same-LUID view/reviewer.
public sealed class BrokerEngine:IAsyncDisposable
{
    readonly Guid operation;readonly IWindowsView view;readonly Func<WindowsLeaseContext,string,CancellationToken,Task> review;
    readonly Func<Exception,int?> errorCode;readonly TimeProvider time;readonly SemaphoreSlim mutex=new(1,1);
    readonly CancellationTokenSource cancelled=new(),finished=new();readonly Task watchdog;
    WindowsLeaseContext? context;string? root;IGameProcess? process;Task? wait;long sequence;
    bool attempted,connected,started,stopped=true,gone=true,closed;double expires;Task? disposal;
    public BrokerEngine(Guid operation,IWindowsView view,Func<WindowsLeaseContext,string,CancellationToken,Task> review,Func<Exception,int?>? errorCode=null,TimeProvider? time=null)
    {if(operation==Guid.Empty)throw new BrokerFailure(BrokerStage.Protocol);this.operation=operation;this.view=view;this.review=review;this.errorCode=errorCode??(_=>null);this.time=time??TimeProvider.System;watchdog=Watch();}
    void CheckLive()
    {
        double deadline=Volatile.Read(ref expires);
        if(cancelled.IsCancellationRequested||(deadline>0&&time.GetUtcNow().ToUnixTimeMilliseconds()/1000d>=deadline-2)){cancelled.Cancel();throw new BrokerFailure(BrokerStage.Deadline);}
    }
    BrokerStatus Status()=>new(started,started&&wait?.IsCompletedSuccessfully==true,stopped,gone);
    public async Task<BrokerReply> Execute(BrokerRequest request,CancellationToken ct)
    {
        await mutex.WaitAsync(ct);BrokerStage stage=BrokerStage.Protocol;
        try{
            if(request.Version!=1||request.Operation!=operation||request.Sequence!=sequence+1||!Enum.IsDefined(request.Command)||(closed&&request.Command!=BrokerCommand.Disconnect))throw new BrokerFailure(stage);
            sequence=request.Sequence;
            if(request.Command!=BrokerCommand.Connect&&request.Context!=null)throw new BrokerFailure(BrokerStage.Protocol);
            if(request.Command!=BrokerCommand.Renew&&(request.LeaseID!=null||request.ExpiresAt!=null))throw new BrokerFailure(BrokerStage.Protocol);
            if(closed&&request.Command==BrokerCommand.Disconnect)return new(1,operation,request.Sequence,request.Command,true,Status:Status());
            if(request.Command is not (BrokerCommand.Stop or BrokerCommand.Disconnect))CheckLive();
            using var active=CancellationTokenSource.CreateLinkedTokenSource(ct,cancelled.Token);
            switch(request.Command){
                case BrokerCommand.Connect:
                    stage=BrokerStage.Context;if(context!=null||request.Context==null)throw new BrokerFailure(stage);
                    request.Context.Validate(time.GetUtcNow());context=request.Context;Volatile.Write(ref expires,context.Lease.ExpiresAt);await review(context,"",active.Token);CheckLive();active.Token.ThrowIfCancellationRequested();
                    stage=BrokerStage.Connect;attempted=true;gone=false;
                    root=await view.Connect(context.Lease.Share,active.Token);connected=true;CheckLive();break;
                case BrokerCommand.Start:
                    stage=BrokerStage.Review;if(!connected||started||context==null||root==null)throw new BrokerFailure(stage);
                    context.Validate(time.GetUtcNow());await review(context,root,active.Token);CheckLive();active.Token.ThrowIfCancellationRequested();
                    stage=BrokerStage.Start;stopped=false;
                    process=context.Lease.SaveNamespace!=null?view is IReviewedWindowsView reviewed?await reviewed.StartReviewed(root,context.Entry.Path,context.Lease,active.Token):throw new BrokerFailure(stage):await view.Start(root,context.Entry.Path,active.Token);
                    started=true;wait=process.Wait(CancellationToken.None);break;
                case BrokerCommand.Status:
                    if(context==null)throw new BrokerFailure(BrokerStage.Context);if(wait?.IsFaulted==true)throw new BrokerFailure(BrokerStage.Start);break;
                case BrokerCommand.Renew:
                    stage=BrokerStage.Renew;if(context==null||request.LeaseID!=context.Lease.LeaseID||request.ExpiresAt is not double next||!double.IsFinite(next)||next<=time.GetUtcNow().ToUnixTimeMilliseconds()/1000d+2)throw new BrokerFailure(stage);
                    Volatile.Write(ref expires,next);break;
                case BrokerCommand.Stop:stage=BrokerStage.Stop;await Stop();break;
                case BrokerCommand.Disconnect:stage=BrokerStage.Disconnect;await Drain();closed=true;finished.Cancel();break;
            }
            return new(1,operation,request.Sequence,request.Command,true,Status:Status());
        }catch(Exception e){var failure=e as BrokerFailure;return new(1,operation,request.Sequence,request.Command,false,failure?.Stage??(cancelled.IsCancellationRequested?BrokerStage.Deadline:stage),failure?.NativeCode??errorCode(e));}
        finally{mutex.Release();}
    }
    async Task Stop()
    {
        if(stopped)return;
        if(process==null)throw new BrokerFailure(BrokerStage.Stop);
        await process.Stop();if(wait!=null)await wait;await process.DisposeAsync();stopped=true;
    }
    async Task Drain()
    {
        await Stop();if(attempted&&!connected&&!gone)throw new BrokerFailure(BrokerStage.Disconnect);
        if(!gone){await view.Disconnect();gone=true;connected=false;}
    }
    async Task Watch()
    {
        try{while(!finished.IsCancellationRequested){await Task.Delay(50,finished.Token);double deadline=Volatile.Read(ref expires);if(deadline>0&&time.GetUtcNow().ToUnixTimeMilliseconds()/1000d>=deadline-2){cancelled.Cancel();await PeerLost();return;}}}catch(OperationCanceledException){}catch{/* Never convert failed cleanup to a positive acknowledgement. */}
    }
    public async Task PeerLost()
    {
        if(closed&&gone&&stopped)return;cancelled.Cancel();await mutex.WaitAsync();try{await Drain();}finally{mutex.Release();}
    }
    public ValueTask DisposeAsync()=>new(disposal??=DisposeCore());
    async Task DisposeCore()
    {
        Exception? failure=null;await mutex.WaitAsync();
        try{await Drain();closed=true;}catch(Exception e){failure=e;}finally{mutex.Release();}
        // A failed drain is a faulted/pending disposal, never a positive closure.
        finished.Cancel();cancelled.Cancel();try{await watchdog;}catch{}finished.Dispose();cancelled.Dispose();
        if(failure!=null)throw new BrokerFailure(BrokerStage.Disconnect,errorCode(failure));
    }
}
