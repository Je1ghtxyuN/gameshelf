// Loopback-only synthetic acceptance server. Never reads a real library or NAS.
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using GameShelf.Core;
if(args.Length==3&&args[0]=="--seed-local") {var local=await StandaloneLibrary.Open(args[1]);await local.AddFolder(args[2]);await local.Scan();Console.WriteLine("Seeded only standalone folder/candidate cache");return;}
var listener=new TcpListener(IPAddress.Loopback,0);listener.Start();
Console.WriteLine(((IPEndPoint)listener.LocalEndpoint).Port);Console.Out.Flush();
var sid=new string('1',32);var uid=new string('2',32);var gid=new string('3',32);var second=new string('6',32);var rejected=false;var uploadedTime=new Dictionary<Guid,double>();
while(true)
{
    using var client=await listener.AcceptTcpClientAsync();using var stream=client.GetStream();
    using var reader=new StreamReader(stream,Encoding.UTF8,false,1024,true);
    var line=await reader.ReadLineAsync();if(line==null)continue;
    var parts=line.Split(' ');int length=0;string auth="";string? header;
    while(!string.IsNullOrEmpty(header=await reader.ReadLineAsync())){if(header.StartsWith("Content-Length:",StringComparison.OrdinalIgnoreCase))length=int.Parse(header[15..].Trim());if(header.StartsWith("Authorization:",StringComparison.OrdinalIgnoreCase))auth=header;}
    if(length>16384)continue;var chars=new char[length];int read=0;while(read<length){var n=await reader.ReadAsync(chars.AsMemory(read));if(n==0)break;read+=n;}
    // All synthetic fields are ASCII so character count equals byte length.
    var route=parts[1];var body=new string(chars);object value=new{};int status=200;
    if(route=="/api/server-info")value=new{serverID=sid,name="Fixture Server",version="1.0"};
    else if(route=="/api/login"){
        var request=JsonDocument.Parse(body);var two=request.RootElement.GetProperty("username").GetString()=="fixture-two";
        value=new LoginResult(two?"fixture-token-two":"fixture-token-one",DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600,new(two?second:uid,two?"fixture-two":"fixture-one",true),sid);
    }else if(route=="/fixture/revoke")rejected=true;
    else if(route=="/fixture/restore")rejected=false;
    else if(route=="/api/library/games/"+gid+"/playtime"&&auth.Contains("fixture-token-one")){
        var item=JsonSerializer.Deserialize<TimeEvent>(body,AccountStore.Json) ?? throw new InvalidDataException();
        uploadedTime.TryAdd(item.EventID,item.ElapsedSeconds);
        value=new{playtimeSeconds=120+uploadedTime.Values.Sum()};
    }
    else if(route=="/api/library"&&rejected){status=401;value=new{error=new{code=401,message="Fixture session revoked"}};}
    else if(route=="/api/library"){
        var two=auth.Contains("fixture-token-two");var account=new Account(two?second:uid,two?"fixture-two":"fixture-one",true);
        value=new LibrarySnapshot(2,sid,account,[new(gid,two?"Account Two Fixture":"Windows Fixture",0,true,[],two?120:120+uploadedTime.Values.Sum(),null,null,[new(gid,"windows","1.0",[new("Play.exe","wine","unknown")],null,true)]),new(new string('9',32),"Second Windows Fixture",0,false,[],240,null,null,[new(new string('9',32),"windows",null,[new("Play.exe","native","unknown")],null,true)]),new(new string('4',32),"macOS Fixture",0,false,[],90,null,null,[new(new string('4',32),"macos",null,[new("Fixture.app","native","unknown")],null,true)])],[new("55555555-5555-4555-8555-555555555555","测试分类")]);
    }else if(route=="/api/library/roots")value=new{roots=new[]{new Root(new string('7',32),"Fixture Root")}};
    else if(route=="/api/library/candidates")value=new{candidates=new[]{new Candidate(new string('8',32),"Candidate Fixture",true,Entries:[new("Play.exe","wine","fixture"),new("Chinese.exe","wine","fixture")])}};
    else if(route=="/api/library/games/"+gid+"/cover")value=new{data=Convert.ToBase64String(await File.ReadAllBytesAsync(Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,"../../../../GameShelf.Windows/Assets/AppIcon.png")))),mime="image/png"};
    else if(route.EndsWith("/cover")){status=404;value=new{error=new{code=404,message="No fixture cover"}};}
    else if(route=="/api/admin/users")value=new{users=new[]{new Account(uid,"fixture-one",true),new Account(second,"fixture-two",false)}};
    else if(route=="/api/admin/games")value=new{games=new[]{new AdminEdition(gid,"Windows Fixture")}};
    else if(route.EndsWith("/session")){status=409;value=new{error=new{code=409,message="Fixture has no reviewed save profile"}};}
    else if(route!="/api/logout"&&parts[0]=="GET"){status=404;value=new{error=new{code=404,message="Not found"}};}
    var bytes=JsonSerializer.SerializeToUtf8Bytes(value,AccountStore.Json);
    var response=Encoding.ASCII.GetBytes($"HTTP/1.1 {status} {(status==200?"OK":"Rejected")}\r\nContent-Type: application/json\r\nContent-Length: {bytes.Length}\r\nConnection: close\r\n\r\n");await stream.WriteAsync(response);await stream.WriteAsync(bytes);
}
