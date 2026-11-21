using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Net.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
namespace GameShelf.Core;
public sealed class ApiFailure(int status):Exception($"服务器请求失败 ({status})") { public int Status {get;}=status; }
public sealed class ApiClient:IDisposable
{
    readonly HttpClient http; readonly string serverID; string? token; string? userID;
    public string ServerID=>serverID;
    public string? UserID=>userID;
    public static bool MatchPin(X509Certificate2? certificate,string pin) => certificate!=null && pin.Length==64 && CryptographicOperations.FixedTimeEquals(SHA256.HashData(certificate.RawData),Convert.FromHexString(pin));
    public ApiClient(Uri address,string expectedServerID,string? certificateSHA256,bool allowHttp=false,HttpMessageHandler? handler=null)
    {
        Safety.Id(expectedServerID);serverID=expectedServerID;
        if(!address.IsAbsoluteUri||address.AbsolutePath!="/"||!string.IsNullOrEmpty(address.UserInfo)||!string.IsNullOrEmpty(address.Query)||!string.IsNullOrEmpty(address.Fragment))throw new ArgumentException("请输入服务器根地址");
        if(address.Scheme!="https"&&!(address.Scheme=="http"&&allowHttp))throw new ArgumentException("需要 HTTPS 或明确允许 HTTP");
        if(address.Scheme=="https"&&(certificateSHA256?.Length!=64||!certificateSHA256.All(Uri.IsHexDigit)))throw new ArgumentException("需要证书 SHA256");
        http=new HttpClient(handler??new HttpClientHandler {AllowAutoRedirect=false,ServerCertificateCustomValidationCallback=(_,cert,_,errors)=>MatchPin(cert,certificateSHA256!)}){BaseAddress=address,Timeout=TimeSpan.FromSeconds(30)};
    }
    public async Task<T> Send<T>(HttpMethod method,string route,object? body=null,CancellationToken ct=default)
    {
        if(!route.StartsWith("/api/",StringComparison.Ordinal)||route.Contains("..")||route.Contains('?')||route.Contains('#'))throw new ArgumentException("Invalid API route");
        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(ct);deadline.CancelAfter(http.Timeout);ct=deadline.Token;
        using var request=new HttpRequestMessage(method,route);
        if(method==HttpMethod.Post && route.StartsWith("/api/library/games/",StringComparison.Ordinal) &&
           route.EndsWith("/session",StringComparison.Ordinal))
            request.Headers.Add("X-GameShelf-Client-Platform","windows");
        if(token!=null)request.Headers.Authorization=new AuthenticationHeaderValue("Bearer",token);
        if(body!=null){var bytes=JsonSerializer.SerializeToUtf8Bytes(body,AccountStore.Json);if(bytes.Length>16384)throw new ArgumentException("Request too large");request.Content=new ByteArrayContent(bytes);request.Content.Headers.ContentType=new MediaTypeHeaderValue("application/json");}
        using var response=await http.SendAsync(request,HttpCompletionOption.ResponseHeadersRead,ct);
        if(!response.IsSuccessStatusCode)throw new ApiFailure((int)response.StatusCode);
        // Bound responses, including malicious/chunked peers.
        using var stream=await response.Content.ReadAsStreamAsync(ct); using var output=new MemoryStream(); var buffer=new byte[8192]; int n;
        while((n=await stream.ReadAsync(buffer,ct))!=0){if(output.Length+n>16*1024*1024)throw new InvalidDataException("Response too large");await output.WriteAsync(buffer.AsMemory(0,n),ct);}
        return JsonSerializer.Deserialize<T>(output.ToArray(),AccountStore.Json)??throw new InvalidDataException("Empty response");
    }
    public async Task<LoginResult> Login(string username,string password)
    {
        var login=await Send<LoginResult>(HttpMethod.Post,"/api/login",new{username,password});
        if(login.ServerID!=serverID||login.ExpiresAt<=DateTimeOffset.UtcNow.ToUnixTimeSeconds())throw new InvalidDataException("Server identity/session mismatch");
        Safety.Id(login.User.Id);token=login.Token;userID=login.User.Id;return login;
    }
    public void Restore(LoginResult login)
    {
        if(login.ServerID!=serverID||login.ExpiresAt<=DateTimeOffset.UtcNow.ToUnixTimeSeconds())throw new InvalidDataException("会话已过期");
        Safety.Id(login.User.Id);token=login.Token;userID=login.User.Id;
    }
    public async Task<LibrarySnapshot> Library()
    {
        var value=await Send<LibrarySnapshot>(HttpMethod.Get,"/api/library");
        if(value.SchemaVersion!=2||value.ServerID!=serverID||value.User.Id!=userID)throw new InvalidDataException("Library identity/schema mismatch");
        var canonicalIDs=new HashSet<string>();var editionOwners=new Dictionary<string,string>();
        var games=new List<LibraryGame>();
        foreach(var g in value.Games)
        {
            Safety.Id(g.Id);if(!canonicalIDs.Add(g.Id))throw new InvalidDataException("Duplicate canonical game");
            var pairs=new HashSet<(string,string)>();
            foreach(var v in g.Variants){Safety.Id(v.Id);if(!pairs.Add((v.Id,v.Platform)))throw new InvalidDataException("Duplicate edition/platform pair");if(editionOwners.TryGetValue(v.Id,out var owner)&&owner!=g.Id)throw new InvalidDataException("Edition belongs to multiple canonical games");editionOwners[v.Id]=g.Id;}
            games.Add(g);
        }
        return value with{Games=games.ToArray()};
    }
    public async Task Logout(){await Send<JsonElement>(HttpMethod.Post,"/api/logout",new{});token=null;userID=null;}
    public Task<JsonElement> Post(string route,object body)=>Send<JsonElement>(HttpMethod.Post,route,body);
    public Task<JsonElement> Delete(string route)=>Send<JsonElement>(HttpMethod.Delete,route);
    public void Dispose()=>http.Dispose();
}
