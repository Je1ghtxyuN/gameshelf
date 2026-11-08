using GameShelf.Core;
using GameShelf.NetworkSession;
namespace GameShelf.Windows;
public static class ConnectedLauncher {public static IWindowsView Create()=>new BrokerWindowsView();}
public sealed class BrokerWindowsView:IReviewedWindowsView,ILeaseBoundWindowsView
{
    WindowsLeaseContext? context;BrokerConnection? connection;bool opening;
    public Task BindLease(WindowsLeaseContext value,CancellationToken ct){ct.ThrowIfCancellationRequested();if(context!=null)throw new BrokerFailure(BrokerStage.Context);value.Validate(DateTimeOffset.UtcNow);context=value;return Task.CompletedTask;}
    public async Task<string> Connect(SmbShare share,CancellationToken ct)
    {
        if(context==null||context.Lease.Share!=share||connection!=null)throw new BrokerFailure(BrokerStage.Context);
        opening=true;try{connection=await BrokerConnection.Open(share,ct);}finally{opening=false;}
        await connection.Call(BrokerCommand.Connect,ct,context);return connection.Root;
    }
    public async Task RenewLease(string leaseID,double expiresAt,CancellationToken ct){if(connection==null)throw new BrokerFailure(BrokerStage.Renew);await connection.Call(BrokerCommand.Renew,ct,lease:leaseID,expires:expiresAt);}
    public async Task<IGameProcess> Start(string root,string relative,CancellationToken ct)
    {
        if(context==null||connection==null||root!=connection.Root||relative!=context.Entry.Path)throw new BrokerFailure(BrokerStage.Context);
        var state=await connection.Call(BrokerCommand.Start,ct);if(!state.Started)throw new BrokerFailure(BrokerStage.Start);return new BrokerProcessProxy((command,token)=>connection.Call(command,token));
    }
    public Task<IGameProcess> StartReviewed(string root,string relative,LaunchLease lease,CancellationToken ct){if(context?.Lease!=lease)throw new BrokerFailure(BrokerStage.Context);return Start(root,relative,ct);}
    public async Task Disconnect(){if(opening)throw new BrokerFailure(BrokerStage.Transport);if(connection!=null){await connection.Close();connection=null;}}
}
