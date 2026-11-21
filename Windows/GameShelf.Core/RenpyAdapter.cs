using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Serialization;
namespace GameShelf.Core;
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record SaveNamespace(string Key,string RelativePath,string Adapter,string ReviewedProfileID,int Revision,string RulesSHA256)
{
    public void Validate(string server,string user,string canonical,string? compatibilityKey=null)
    {
        if(!RenpyAdapter.ProfileID(compatibilityKey))throw new InvalidDataException("版本未声明有效存档兼容标识");
        if(new[]{server,user,canonical}.Any(id=>id.Length!=32||id.Any(c=>c is not (>= '0' and <= '9' or >= 'a' and <= 'f'))))throw new InvalidDataException("命名空间身份无效");
        var parts=Key.Split('/');
        if(parts.Length!=4||parts[0]!=server||parts[1]!=user||parts[2]!=canonical||!RenpyAdapter.Hex(parts[3])||Adapter!=RenpyAdapter.Adapter||RelativePath!=RenpyAdapter.RelativePath||Revision<1||!RenpyAdapter.ProfileID(ReviewedProfileID)||!RenpyAdapter.Hex(RulesSHA256))throw new InvalidDataException("账号存档命名空间不匹配");
        if(parts[3]!=Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(compatibilityKey!))))throw new InvalidDataException("存档兼容映射不匹配");
    }
}
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RuntimeFingerprint(string Path,string Role,string SourceSHA256,string ExportSHA256);
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RenpyReview(string Adapter,string Platform,string EntryPath,string RelativeSavePath,string ReviewedProfileID,int Revision,string RulesSHA256,string EvidenceSHA256,string SourceSignature,RuntimeFingerprint[] Fingerprints);
public sealed record RenpyLaunchPlan(string SaveRoot)
{
    public string[] Arguments=>["--savedir",SaveRoot];
    public IReadOnlyDictionary<string,string> Environment=>new Dictionary<string,string>{{"RENPY_PATH_TO_SAVES",Path.Combine(SaveRoot,"Auxiliary")},{"RENPY_MULTIPERSISTENT",Path.Combine(SaveRoot,"MultiPersistent")},{"PYTHONDONTWRITEBYTECODE","1"}};
}
public static class RenpyAdapter
{
    public const string Adapter="renpy-savedir-v1";
    public const string RelativePath=".gameshelf-profile/CanonicalSaves/renpy";
    public const string SourceSHA256="7a84aea6ddccab67c7e004d94cd38092e4a910c15ccb6fabb44aecbde1108427";
    public const string PatchedSHA256="ba48a4c044ec8d557bbc2f42aab441caaf52480ebeb29f2a9208594b6dc09596";
    private const string Source832SHA256="3ed845b5c9d13dcf581a9143b5fc933fc1dfad8f03c93bccc2ef0955adb7e884";
    private const string Patched832SHA256="36e6fe2778ad3d2ca6afe0325eba33661e05e7703b264cf7c0a34dcdf8991365";

    private const string Source813SHA256="2fcd8f8dd88e85fd689a91db1f98c7ab7c5790d3b871b1f89420e27db3bba8f2";
    private const string Patched813SHA256="178aaa36fba90afcaed683e31048200328653fbbf4bc7c6c7e9d5ac65f721c24";

    private const string Source852SHA256="e0b32aab4127325a307a8396b6a9b636781161fd23d595127e668d784342b85c";
    private const string Patched852SHA256="1b4b59ac6e0359d99da69378a18a446eecb0c08038e7d06452e56a19bbca46d4";

    private static bool ApprovedLocationPatch(RuntimeFingerprint fingerprint) => fingerprint.SourceSHA256 switch
    {
        SourceSHA256 or PatchedSHA256 => fingerprint.ExportSHA256 == PatchedSHA256,
        Source832SHA256 or Patched832SHA256 => fingerprint.ExportSHA256 == Patched832SHA256,
        Source813SHA256 or Patched813SHA256 => fingerprint.ExportSHA256 == Patched813SHA256,
        Source852SHA256 or Patched852SHA256 => fingerprint.ExportSHA256 == Patched852SHA256,
        _ => false,
    };
    public static bool Hex(string? value)=>value is {Length:64}&&value.All(c=>c is >= '0' and <= '9' or >= 'a' and <= 'f');
    public static bool ProfileID(string? value)=>value is {Length:>0 and <=128}&&char.IsAsciiLetterOrDigit(value[0])&&value.All(c=>char.IsAsciiLetterOrDigit(c)||"._-".Contains(c));
    static bool CodePath(string path)=>Safety.Relative(path)==path&&!path.Split('/').Any(piece=>piece.Equals(".gameshelf-profile",StringComparison.OrdinalIgnoreCase));
    static void Require(bool condition){if(!condition)throw new InvalidDataException("Ren’Py 账号启动审查信息不匹配");}
    public static void ValidateProof(LaunchLease lease,LibraryVariant variant,LibraryEntry entry,string canonical)
    {
        var ns=lease.SaveNamespace??throw new InvalidDataException("缺少存档命名空间");ns.Validate(lease.ServerID,lease.UserID,canonical,variant.SaveCompatibilityKey);
        var w=lease.WindowsLaunch??throw new InvalidDataException("缺少 Windows 审查信息");var r=lease.RenpyLaunch??throw new InvalidDataException("缺少 Ren’Py 运行模块审查");
        Require(entry.Backend=="renpy"&&variant.Platform=="windows"&&variant.Id==lease.Game.Id&&w.EntryPath==entry.Path&&r.EntryPath==entry.Path&&Safety.NativeEntry(entry.Path,entry.Backend));
        Require(w.SaveProfileVerified&&w.Isolation=="reviewed-relative-saves"&&w.Adapter==Adapter&&r.Adapter==Adapter&&r.Platform=="windows"&&w.RelativeSavePath==RelativePath&&r.RelativeSavePath==RelativePath);
        Require(w.ReviewedProfileID==ns.ReviewedProfileID&&r.ReviewedProfileID==ns.ReviewedProfileID&&w.ProfileRevision==ns.Revision&&r.Revision==ns.Revision&&w.RulesSHA256==ns.RulesSHA256&&r.RulesSHA256==ns.RulesSHA256);
        Require(Hex(w.EntrySHA256)&&Hex(w.EvidenceSHA256)&&Hex(w.SourceSignature)&&w.EvidenceSHA256==r.EvidenceSHA256&&w.SourceSignature==r.SourceSignature&&w.SavePaths is {Length:<=32});
        foreach(var rule in w.SavePaths!)Require(rule.Kind=="directory"&&CodePath(rule.Path));
        Require(r.Fingerprints is {Length:>=8 and <=128});var paths=new HashSet<string>(StringComparer.OrdinalIgnoreCase);var roles=new HashSet<string>();
        foreach(var fingerprint in r.Fingerprints){Require(CodePath(fingerprint.Path)&&paths.Add(fingerprint.Path)&&Hex(fingerprint.SourceSHA256)&&Hex(fingerprint.ExportSHA256));Require(new[]{"entry","bootstrap","arguments","main","locations","persistent","tokens","config","metadata"}.Contains(fingerprint.Role));roles.Add(fingerprint.Role);}
        Require(new[]{"entry","bootstrap","arguments","main","locations","persistent","tokens","config"}.All(roles.Contains));
        foreach(var expected in new Dictionary<string,string>{{"bootstrap","renpy/bootstrap.py"},{"arguments","renpy/arguments.py"},{"main","renpy/main.py"},{"locations","renpy/savelocation.py"},{"persistent","renpy/persistent.py"},{"tokens","renpy/savetoken.py"}}){var matching=r.Fingerprints.Where(f=>f.Role==expected.Key).ToArray();Require(matching.Length==1&&matching[0].Path==expected.Value);}
        var entries=r.Fingerprints.Where(f=>f.Role=="entry").ToArray();Require(entries.Length==1&&entries[0].Path==entry.Path&&entries[0].ExportSHA256==w.EntrySHA256);
        var locations=r.Fingerprints.Where(f=>f.Role=="locations").ToArray();Require(locations.Length==1&&ApprovedLocationPatch(locations[0]));
    }
    public static string CheckedPath(string root,string relative,bool directory)
    {
        var fullRoot=Path.GetFullPath(root);if(fullRoot.Contains('"')||fullRoot.Any(char.IsControl))throw new InvalidDataException("账号视图路径无效");
        var current=fullRoot;var rootInfo=File.GetAttributes(current);if((rootInfo&FileAttributes.Directory)==0||(rootInfo&FileAttributes.ReparsePoint)!=0)throw new InvalidDataException("账号视图包含链接");
        var parts=Safety.Relative(relative).Split('/');
        for(var i=0;i<parts.Length;i++){current=Path.Combine(current,parts[i]);try{var info=File.GetAttributes(current);if((info&FileAttributes.ReparsePoint)!=0||((info&FileAttributes.Directory)!=0)!=(i<parts.Length-1||directory))throw new InvalidDataException("账号视图文件类型或链接不匹配");}catch(FileNotFoundException){throw new InvalidDataException("账号存档目录或审查文件缺失");}catch(DirectoryNotFoundException){throw new InvalidDataException("账号存档目录或审查文件缺失");}}
        return current;
    }
    public static async Task<RenpyLaunchPlan> Prepare(string viewRoot,LaunchLease lease,CancellationToken ct=default)
    {
        if(lease.SaveNamespace is not {RelativePath:RelativePath,Adapter:Adapter}||lease.WindowsLaunch is not {Adapter:Adapter,RelativeSavePath:RelativePath}||lease.RenpyLaunch is not {Adapter:Adapter})throw new InvalidDataException("不支持的存档适配器");
        var save=CheckedPath(viewRoot,RelativePath,true);
        foreach(var fingerprint in lease.RenpyLaunch.Fingerprints){
            var path=CheckedPath(viewRoot,fingerprint.Path,false);using var file=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read,8192,true);
            var limit=fingerprint.Role=="entry"?1024L*1024*1024:512L*1024;
            if(file.Length<=0||file.Length>limit||(fingerprint.Role=="locations"&&file.Length>128*1024))throw new InvalidDataException("审查文件大小不匹配");
            var before=file.Length;var actual=Convert.ToHexStringLower(await SHA256.HashDataAsync(file,ct));
            if(file.Length!=before||actual!=fingerprint.ExportSHA256||(fingerprint.Role=="locations"&&!ApprovedLocationPatch(fingerprint)))throw new InvalidDataException("账号专属运行模块完整性检查失败");
        }
        foreach(var name in new[]{"Auxiliary","MultiPersistent"}){var path=Path.Combine(save,name);if(!Directory.Exists(path))Directory.CreateDirectory(path);CheckedPath(viewRoot,RelativePath+"/"+name,true);}
        return new(save);
    }
}
public interface IReviewedWindowsView:IWindowsView
{
    Task<IGameProcess> StartReviewed(string root,string relative,LaunchLease lease,CancellationToken ct);
}
