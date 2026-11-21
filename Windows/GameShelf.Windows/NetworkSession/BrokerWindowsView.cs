using GameShelf.Core;
using GameShelf.NetworkSession;
namespace GameShelf.Windows;
public static class ConnectedLauncher {public static IWindowsView Create(bool mountedWorkspace=false)=>mountedWorkspace ? new MountedWindowsView() : new BrokerWindowsView();}
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

sealed class MountedWindowsView : IMountedWindowsView
{
    readonly WindowsView launcher = new();
    WindowsLeaseContext? context;
    string? connectedRoot;

    public Task<string> Connect(SmbShare share, CancellationToken ct) =>
        throw new InvalidOperationException("请通过系统连接 NAS 账号工作区共享。");

    public async Task<string> ConnectMounted(WindowsLeaseContext value, string expectedShare, CancellationToken ct)
    {
        value.Validate(DateTimeOffset.UtcNow);
        if (context != null || value.Lease.MountedWorkspace is not { } workspace || workspace.Share != expectedShare)
            throw new InvalidDataException("账号工作区与租约不匹配。");
        var root = await ShellMappingBackend.ExistingShareRoot(expectedShare,ct);
        var folder = RenpyAdapter.CheckedPath(root,workspace.Relative,true);
        value.Lease.VerifyWorkspace(folder);
        ct.ThrowIfCancellationRequested();
        context = value;
        connectedRoot = folder;
        return folder;
    }

    public Task Disconnect()
    {
        connectedRoot = null;
        context = null;
        return Task.CompletedTask;
    }

    async Task VerifyStart(string root, string relative, CancellationToken ct)
    {
        if (context is not { } value || root != connectedRoot || relative != value.Entry.Path)
            throw new InvalidDataException("账号工作区或启动入口已变更。");
        await BrokerReview.Validate(value,root,ct);
    }

    public async Task<IGameProcess> Start(string root,string relative,CancellationToken ct)
    {
        await VerifyStart(root,relative,ct);
        return await launcher.Start(root,relative,ct);
    }

    public async Task<IGameProcess> StartReviewed(string root,string relative,LaunchLease lease,CancellationToken ct)
    {
        if (context?.Lease != lease) throw new InvalidDataException("账号租约已变更。");
        await VerifyStart(root,relative,ct);
        return await launcher.StartReviewed(root,relative,lease,ct);
    }
}
