using System.Net;
using System.Net.Security;
using System.Security.Cryptography;
using System.Text.Json;
namespace GameShelf.Core;
public sealed class CertificateTrustRequired(string fingerprint):Exception("请比较服务器证书") {public string Fingerprint{get;}=fingerprint;}
public sealed record DiscoveredServer(string Address,string ServerID,string? CertificateSHA256,bool PlainHttp);
public static class Discovery
{
    public static bool PrivateAddress(IPAddress ip)
    {
        if(IPAddress.IsLoopback(ip))return true;
        if(ip.IsIPv4MappedToIPv6)ip=ip.MapToIPv4();var bytes=ip.GetAddressBytes();
        if(bytes.Length==4)return bytes[0]==10||bytes[0]==192&&bytes[1]==168||bytes[0]==172&&bytes[1]>=16&&bytes[1]<=31||bytes[0]==169&&bytes[1]==254;
        return (bytes[0]&0xFE)==0xFC||(bytes[0]==0xFE&&(bytes[1]&0xC0)==0x80);
    }
    public static async Task<DiscoveredServer> Inspect(string address,string? trustedPin=null)
    {
        if(!address.Contains("://"))address="https://"+address;
        var uri=new Uri(address.Trim());
        if(uri.AbsolutePath!="/"||uri.UserInfo!=""||uri.Query!=""||uri.Fragment!=""||uri.Scheme is not ("http" or "https"))throw new ArgumentException("请输入服务器地址");
        if(uri.Scheme=="http"){
            var ips=await Dns.GetHostAddressesAsync(uri.DnsSafeHost);
            if(ips.Length==0||!ips.All(PrivateAddress))throw new ArgumentException("公网服务器需要 HTTPS");
        }
        string? observed=null;bool certificateRejected=false;
        using var handler=new HttpClientHandler{AllowAutoRedirect=false,ServerCertificateCustomValidationCallback=(_,cert,_,errors)=>{
            if(cert==null)return false;observed=Convert.ToHexStringLower(SHA256.HashData(cert.RawData));
            var accepted=trustedPin!=null?ApiClient.MatchPin(cert,trustedPin):errors==SslPolicyErrors.None;certificateRejected=!accepted;return accepted;
        }};
        using var client=new HttpClient(handler){BaseAddress=uri,Timeout=TimeSpan.FromSeconds(15)};
        try{
            using var deadline=new CancellationTokenSource(TimeSpan.FromSeconds(15));
            using var response=await client.GetAsync("/api/server-info",HttpCompletionOption.ResponseHeadersRead,deadline.Token);if(!response.IsSuccessStatusCode)throw new ApiFailure((int)response.StatusCode);
            if(response.Content.Headers.ContentLength>16384)throw new InvalidDataException("Server info too large");
            using var stream=await response.Content.ReadAsStreamAsync(deadline.Token);using var output=new MemoryStream();var buffer=new byte[4096];int count;
            while((count=await stream.ReadAsync(buffer.AsMemory(0,(int)Math.Min(buffer.Length,16385-output.Length)),deadline.Token))>0){await output.WriteAsync(buffer.AsMemory(0,count),deadline.Token);if(output.Length>16384)throw new InvalidDataException("Server info too large");}
            var data=output.ToArray();
            var value=JsonSerializer.Deserialize<JsonElement>(data);var id=Safety.Id(value.GetProperty("serverID").GetString()!);
            return new(uri.GetLeftPart(UriPartial.Authority)+"/",id,observed,uri.Scheme=="http");
        }catch(HttpRequestException)when(certificateRejected&&observed!=null){throw new CertificateTrustRequired(observed);}
    }
}
