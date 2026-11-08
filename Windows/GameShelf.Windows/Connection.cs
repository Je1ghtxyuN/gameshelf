using System.Text.Json;
using System.Security.Cryptography;
using System.Text;
using GameShelf.Core;
using Windows.Security.Credentials;
namespace GameShelf.Windows;
public sealed record Connection(string Address,string ServerID,string Pin,bool AllowHttp,string? UserID)
{
    public static string DataRoot {get;}=ResolveDataRoot();
    static string ResolveDataRoot()
    {
        var option=Environment.GetCommandLineArgs().FirstOrDefault(x=>x.StartsWith("--data-dir="));
        if(option==null)return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"GameShelf");
        var path=Path.GetFullPath(option[11..]);var user=Environment.GetFolderPath(Environment.SpecialFolder.UserProfile)+Path.DirectorySeparatorChar;
        if(!path.StartsWith(user,StringComparison.OrdinalIgnoreCase))throw new ArgumentException("数据目录必须位于当前用户目录");return path;
    }
    string Resource=>"GameShelf:"+ServerID+":"+Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(DataRoot)));
    public static string? LastMode(){try{return File.ReadAllText(Path.Combine(DataRoot,"mode.txt")).Trim();}catch{return null;}}
    public static void SaveMode(string mode){Directory.CreateDirectory(DataRoot);File.WriteAllText(Path.Combine(DataRoot,"mode.txt"),mode);}
    static string PathName=>Path.Combine(DataRoot,"connection.json");
    public static Connection? Load(){try{return JsonSerializer.Deserialize<Connection>(File.ReadAllText(PathName),AccountStore.Json);}catch{return null;}}
    public void Save(LoginResult login,bool remember=true)
    {
        Directory.CreateDirectory(DataRoot);
        if(remember)new PasswordVault().Add(new PasswordCredential(Resource,login.User.Id,JsonSerializer.Serialize(login,AccountStore.Json)));else Forget();
        File.WriteAllText(PathName,JsonSerializer.Serialize(this with {UserID=login.User.Id},AccountStore.Json));
    }
    public LoginResult? Session(){try{var credential=new PasswordVault().Retrieve(Resource,UserID);credential.RetrievePassword();return JsonSerializer.Deserialize<LoginResult>(credential.Password,AccountStore.Json);}catch{return null;}}
    public void Forget(){try{var vault=new PasswordVault();vault.Remove(vault.Retrieve(Resource,UserID));}catch{}}
    public ApiClient Client()=>new(new Uri(Address),ServerID,Pin,AllowHttp);
}
