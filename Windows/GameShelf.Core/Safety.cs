using System.Text.RegularExpressions;
namespace GameShelf.Core;
public static partial class Safety
{
    [GeneratedRegex("^[a-f0-9]{32}$")] private static partial Regex Hex();
    [GeneratedRegex("^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])($|\\.)", RegexOptions.IgnoreCase)] private static partial Regex Device();
    public static string Id(string id) => Hex().IsMatch(id) ? id : throw new ArgumentException("Invalid identifier");
    public static string CacheKey(string server,string user) => Id(server)+"-"+Id(user);
    public static string Relative(string path)
    {
        var parts=path.Replace('\\','/').Split('/');
        if (string.IsNullOrWhiteSpace(path)||path.Length>240||parts.Any(p=>p.Length==0||p is "." or ".."||p.EndsWith('.')||p.EndsWith(' ')||Device().IsMatch(p)||p.Any(c=>char.IsControl(c)||"<>:\"|?*%".Contains(c)))) throw new ArgumentException("Unsafe Windows path");
        return string.Join('/',parts);
    }
    private static readonly HashSet<string> Auxiliary = new("thprac replayview cs2conf filecheck helper checker config configuration custom setting settings setup setup32 setup64 install installer unins uninstall uninstaller dxsetup vcredist vcredist_x86 vcredist_x64 vc_redist.x86 vc_redist.x64 dotnet dotnetfx unitycrashhandler unitycrashhandler32 unitycrashhandler64 crashreport crashreporter notification_helper chrome_crashpad_handler crashpad_handler launcher crash update updater redist prereq maintenancetool エンジン設定 設定 设置 環境設定 环境设置 环境设定 游戏设置 遊戲設定 ゲーム設定".Split(' '), StringComparer.Ordinal);
    // Match conventional helper tokens, never arbitrary substrings in game titles.
    [GeneratedRegex(@"^(?:(?:config|configuration|custom|settings?|replayview|thprac)(?:[0-9]+|[._ -](?:tool|settings?|x86|x64|32|64|jp|ja|en|cn|chs|c|sc|zh[_-]cn))|th[0-9]+[._ -]update[0-9][a-z0-9._-]*|(?:unins|uninstall|uninstaller)[0-9]*(?:[._ -][a-z0-9]+)*|(?:setup|install|installer|launcher|crash|update|updater|redist|prereq|maintenancetool)(?:[0-9]+|[._ -][a-z0-9]+)*)$")]
    private static partial Regex ConventionalHelper();
    public static bool NativeEntry(string path,string backend)
    {
        try { Relative(path); } catch(ArgumentException) { return false; }
        var name=path.Replace('\\','/').Split('/').Last().Normalize().ToLowerInvariant();
        return (backend=="native"||backend=="wine"||backend=="renpy"||backend=="rpgmz") && name.EndsWith(".exe") && !Auxiliary.Contains(name[..^4]) && !ConventionalHelper().IsMatch(name[..^4]);
    }
}
