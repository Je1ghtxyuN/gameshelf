using System.Text.Json;
namespace GameShelf.Core;
public sealed record Account(string Id,string Username,bool Admin);
public sealed record LibraryEntry(string Path,string Backend,string? Engine);
public sealed record LibraryVariant(string Id,string Platform,string? Version,LibraryEntry[] Entries,string? SaveCompatibilityKey,bool Available);
public sealed record LibraryGame(string Id,string Title,long Revision,bool Favorite,string[] CollectionIDs,double PlaytimeSeconds,JsonElement? LastPlayed,string? CoverSHA256,LibraryVariant[] Variants,LaunchPreferences? PreferredEntries=null)
{
    public override string ToString()=>Title;
    public string TimeLabel => PlaytimeSeconds<60 ? $"{Math.Max(0,PlaytimeSeconds):0} 秒" : TimeSpan.FromSeconds(Math.Max(0,PlaytimeSeconds)).TotalHours>=1 ? $"{PlaytimeSeconds/3600:0.0} 小时" : $"{PlaytimeSeconds/60:0} 分钟";
    public string VersionLabel => string.Join(" · ",Variants.Where(v=>v.Platform=="windows").Select(v=>v.Version??"Windows"));
}
public sealed record Collection(string Id,string Name);
public sealed record LibrarySnapshot(int SchemaVersion,string ServerID,Account User,LibraryGame[] Games,Collection[] Collections);
public sealed record LoginResult(string Token,double ExpiresAt,Account User,string ServerID);
public sealed record Root(string Id,string Title);
public sealed record Candidate(string Id,string Title,bool Available,bool Added=false,bool Ignored=false,LibraryEntry[]? Entries=null)
{ public string DisplayTitle=>Title+(Added?" · 已添加":"")+(Ignored?" · 已忽略":"")+(!Available?" · 不可用":""); }
public sealed record TimeEvent(Guid EventID,string DeviceID,double ElapsedSeconds,double EndedAt);
public sealed record PendingTime(string ServerID,string UserID,string GameID,TimeEvent Event);
public static class Editions
{
    public static (LibraryVariant Variant,LibraryEntry Entry) Select(LibraryGame game,string? variantID=null)
    {
        if(variantID==null&&game.PreferredEntries?.Windows is LaunchPreference preference)
        {
            foreach(var v in game.Variants.Where(v=>v.Id==preference.VariantID&&v.Platform=="windows"&&v.Available))
            {var entry=v.Entries.FirstOrDefault(e=>e.Path==preference.EntryPath&&Safety.NativeEntry(e.Path,e.Backend));if(entry!=null){Safety.Id(v.Id);return(v,entry);}}
            throw new InvalidOperationException("已选 Windows 入口当前不可用");
        }
        foreach(var v in game.Variants.Where(v=>v.Available&&v.Platform=="windows"&&(variantID==null||v.Id==variantID)))
        {
            Safety.Id(v.Id);
            var entry=v.Entries.FirstOrDefault(e=>Safety.NativeEntry(e.Path,e.Backend));
            if(entry!=null) return(v,entry);
        }
        throw new InvalidOperationException("没有可用的 Windows 原生版本");
    }
}

public sealed record AdminEdition(string Id,string Title);

public sealed record LaunchPreference(string VariantID,string EntryPath);
public sealed record LaunchPreferences(LaunchPreference? Windows=null,LaunchPreference? Macos=null,LaunchPreference? Web=null);
