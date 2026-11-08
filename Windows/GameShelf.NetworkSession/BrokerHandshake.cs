using System.Security.Cryptography;
using System.Text;
namespace GameShelf.NetworkSession;
public sealed record BrokerPeer(int Pid,int Session,string Sid,string AuthenticationId);
public static class BrokerHandshake
{
    public static void Pair(BrokerPeer parent,BrokerPeer child,int expectedChild)
    {
        if(parent.Pid<=0||child.Pid!=expectedChild||child.Pid<=0||child.Pid==parent.Pid||string.IsNullOrEmpty(parent.Sid)||parent.Sid!=child.Sid||parent.Session!=child.Session||child.Session<0||parent.AuthenticationId.Length!=16||child.AuthenticationId.Length!=16||!parent.AuthenticationId.All(Uri.IsHexDigit)||!child.AuthenticationId.All(Uri.IsHexDigit)||parent.AuthenticationId==child.AuthenticationId)throw new BrokerFailure(BrokerStage.Identity);
    }
    public static void Hello(BrokerPeer actual,BrokerPeer reported){if(actual!=reported)throw new BrokerFailure(BrokerStage.Identity);}
    public static void Nonce(string expected,string actual){if(expected.Length!=64||actual.Length!=64||!expected.All(Uri.IsHexDigit)||!actual.All(Uri.IsHexDigit)||!CryptographicOperations.FixedTimeEquals(Encoding.ASCII.GetBytes(expected),Encoding.ASCII.GetBytes(actual)))throw new BrokerFailure(BrokerStage.Identity);}
}
