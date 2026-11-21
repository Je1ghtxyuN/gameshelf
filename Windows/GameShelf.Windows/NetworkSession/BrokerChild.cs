using System.IO.Pipes;
using System.Security.Cryptography;
using GameShelf.NetworkSession;
namespace GameShelf.Windows;
public static class BrokerChild
{
    public static async Task<int> Run(string[] args)
    {
        if(args.Length!=4||args[0]!="--broker-child"||!Guid.TryParseExact(args[2],"N",out Guid operation)||!int.TryParse(args[3],out int parent)||parent<=0||args[1]!="GameShelf-Broker-"+operation.ToString("N"))return 2;
        BrokerEngine? engine=null;
        try{
            using var pipe=new NamedPipeClientStream(".",args[1],PipeDirection.InOut,PipeOptions.Asynchronous|PipeOptions.CurrentUserOnly);
            using var handshake=new CancellationTokenSource(TimeSpan.FromSeconds(15));await pipe.ConnectAsync(handshake.Token);
            if(!NetworkNative.GetNamedPipeServerProcessId(pipe.SafePipeHandle.DangerousGetHandle(),out uint pid)||pid!=parent)throw new BrokerFailure(BrokerStage.Identity);
            var own=NetworkNative.Identity(NetworkNative.GetCurrentProcess());var expected=NetworkNative.Parent(parent);
            BrokerHandshake.Pair(expected,own,own.Pid);
            await BrokerWire.Write(pipe,own,handshake.Token);var nonce=await BrokerWire.Read<BrokerChallenge>(pipe,handshake.Token);
            if(nonce.Value.Length!=64||!nonce.Value.All(Uri.IsHexDigit))throw new BrokerFailure(BrokerStage.Identity);
            await BrokerWire.Write(pipe,nonce,handshake.Token);
            engine=new(operation,new WindowsView(),BrokerReview.Validate,e=>e is SmbMappingException smb?smb.Win32Error:e is MappedUserQueryException mapped?(int)mapped.NativeErrorCode:e is System.ComponentModel.Win32Exception native?native.NativeErrorCode:null,
                smbStage: e => e is SmbMappingException mapping ? mapping.Stage : null);
            using var lost=new CancellationTokenSource();
            Task<BrokerRequest> read=BrokerWire.Read<BrokerRequest>(pipe,lost.Token);
            while(true){
                var request=await read;
                // Read the next header while executing, so EOF cancels an in-flight map/start.
                read=BrokerWire.Read<BrokerRequest>(pipe,lost.Token);
                var execute=engine.Execute(request,lost.Token);
                if(await Task.WhenAny(execute,read)==read){lost.Cancel();try{await execute.WaitAsync(TimeSpan.FromSeconds(12));}catch{}try{await read;}catch{}throw new BrokerFailure(BrokerStage.Transport);}
                var reply=await execute;
                using var sendDeadline=new CancellationTokenSource(TimeSpan.FromSeconds(10));await BrokerWire.Write(pipe,reply,sendDeadline.Token);
                if(request.Command==BrokerCommand.Disconnect&&reply.Success&&reply.Status is {NativeStopped:true,MappingGone:true}){lost.Cancel();try{await read;}catch{}return 0;}
            }
        }catch{return 1;}
        finally
        {
            if(engine!=null){try{await engine.PeerLost().WaitAsync(TimeSpan.FromSeconds(12));}catch{}try{await engine.DisposeAsync().AsTask().WaitAsync(TimeSpan.FromSeconds(12));}catch{}}
        }
    }
}
