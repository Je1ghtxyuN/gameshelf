using System.Buffers.Binary;
using System.Text.Json;
using System.Text.Json.Serialization;
using GameShelf.Core;
namespace GameShelf.NetworkSession;
public enum BrokerCommand { Connect,Start,Status,Renew,Stop,Disconnect }
public enum BrokerStage { Protocol,Identity,Context,Connect,Review,Start,Renew,Stop,Disconnect,Deadline,Transport,Package }
public sealed class BrokerFailure(BrokerStage stage,int? nativeCode=null):InvalidOperationException($"Windows network session failed: {stage}"+(nativeCode is int n?$" ({n})":""))
{ public BrokerStage Stage{get;}=stage;public int? NativeCode{get;}=nativeCode; }
public sealed record BrokerStatus(bool Started,bool Exited,bool NativeStopped,bool MappingGone);
public sealed record BrokerRequest(int Version,Guid Operation,long Sequence,BrokerCommand Command,WindowsLeaseContext? Context=null,string? LeaseID=null,double? ExpiresAt=null)
{ public override string ToString()=>"Broker request (redacted)"; }
public sealed record BrokerReply(int Version,Guid Operation,long Sequence,BrokerCommand Command,bool Success,BrokerStage? Error=null,int? NativeCode=null,BrokerStatus? Status=null);
public static class BrokerWire
{
    public const int Limit=32768;
    public static readonly JsonSerializerOptions Json=new(JsonSerializerDefaults.Web){UnmappedMemberHandling=JsonUnmappedMemberHandling.Disallow,MaxDepth=24};
    public static byte[] Encode<T>(T value)
    {
        var bytes=JsonSerializer.SerializeToUtf8Bytes(value,Json);if(bytes.Length is <=0 or >Limit)throw new BrokerFailure(BrokerStage.Protocol);
        var header=new byte[4];BinaryPrimitives.WriteInt32LittleEndian(header,bytes.Length);
        var frame=new byte[4+bytes.Length];header.CopyTo(frame,0);bytes.CopyTo(frame,4);return frame;
    }
    public static async Task Write<T>(Stream stream,T value,CancellationToken ct)
    {
        var frame=Encode(value);await stream.WriteAsync(frame,ct);await stream.FlushAsync(ct);
    }
    public static async Task<T> Read<T>(Stream stream,CancellationToken ct)
    {
        var header=new byte[4];await stream.ReadExactlyAsync(header,ct);int length=BinaryPrimitives.ReadInt32LittleEndian(header);
        if(length is <=0 or >Limit)throw new BrokerFailure(BrokerStage.Protocol);
        var bytes=new byte[length];await stream.ReadExactlyAsync(bytes,ct);
        try{return JsonSerializer.Deserialize<T>(bytes,Json)??throw new BrokerFailure(BrokerStage.Protocol);}catch(JsonException){throw new BrokerFailure(BrokerStage.Protocol);}
    }
}
