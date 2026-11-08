using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
namespace GameShelf.Core;
public sealed record LocalCandidate(string Id,string Title,string Folder,LibraryEntry[] Entries);
public sealed record LocalGame(string Folder,LibraryGame Game,bool Visible=true);
public sealed record LocalLibraryState(string[] Folders,LocalCandidate[] Candidates,LocalGame[] Games,Collection[] Collections,Guid[] TimeEvents);
public sealed class StandaloneLibrary
{
    public string DirectoryPath{get;}
    readonly SemaphoreSlim gate=new(1,1);
    LocalLibraryState state=new([],[],[],[],[]);
    LocalLibraryState committed=new([],[],[],[],[]);
    string StatePath=>Path.Combine(DirectoryPath,"library.json");
    StandaloneLibrary(string dataRoot)=>DirectoryPath=Path.Combine(dataRoot,"LocalLibrary");
    public static async Task<StandaloneLibrary> Open(string dataRoot){var result=new StandaloneLibrary(dataRoot);if(File.Exists(result.StatePath))result.state=JsonSerializer.Deserialize<LocalLibraryState>(await File.ReadAllTextAsync(result.StatePath),AccountStore.Json)??throw new InvalidDataException("本地游戏库无效");result.committed=result.state;return result;}
    async Task Save(){try{Directory.CreateDirectory(DirectoryPath);var temp=StatePath+".tmp";await File.WriteAllTextAsync(temp,JsonSerializer.Serialize(state,AccountStore.Json));File.Move(temp,StatePath,true);committed=state;}catch{state=committed;throw;}}
    public string[] Folders=>state.Folders.ToArray();
    public LocalCandidate[] Candidates=>state.Candidates.ToArray();
    public LibrarySnapshot Library()=>new(2,new string('0',32),new(new string('f',32),"本地使用",false),state.Games.Where(g=>g.Visible).Select(g=>g.Game).ToArray(),state.Collections);
    public async Task AddFolder(string folder)
    {
        var path=Path.GetFullPath(folder);if((OperatingSystem.IsWindows()&&new DriveInfo(Path.GetPathRoot(path)!).DriveType==DriveType.Network)||!Directory.Exists(path)||path.StartsWith(@"\\")||(File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)throw new ArgumentException("请选择本机游戏资料夹");
        await gate.WaitAsync();try{state=state with{Folders=state.Folders.Append(path).Distinct(StringComparer.OrdinalIgnoreCase).ToArray()};await Save();}finally{gate.Release();}
    }
    public static bool ProbeExe(string path)
    {
        if(!Safety.NativeEntry(Path.GetFileName(path),"native")||(File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)return false;
        using var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite);if(stream.Length<88)return false;Span<byte> dos=stackalloc byte[64];stream.ReadExactly(dos);if(dos[0]!='M'||dos[1]!='Z')return false;
        var offset=System.Buffers.Binary.BinaryPrimitives.ReadInt32LittleEndian(dos[60..]);if(offset<64||offset>1048576||offset>stream.Length-24)return false;stream.Position=offset;Span<byte> pe=stackalloc byte[6];stream.ReadExactly(pe);var machine=System.Buffers.Binary.BinaryPrimitives.ReadUInt16LittleEndian(pe[4..]);return pe[0]=='P'&&pe[1]=='E'&&pe[2]==0&&pe[3]==0&&(machine is 0x14c or 0x8664);
    }
    public async Task Scan(CancellationToken ct=default)
    {
        var found=await Task.Run(()=>{
            var output=new List<LocalCandidate>();var visited=new HashSet<string>(StringComparer.OrdinalIgnoreCase);var queue=new Queue<(string,int)>();foreach(var folder in state.Folders)queue.Enqueue((folder,0));int files=0;
            while(queue.Count>0){ct.ThrowIfCancellationRequested();var(folder,depth)=queue.Dequeue();if(!visited.Add(folder))continue;if(visited.Count>2000)throw new InvalidOperationException("扫描目录过多，请选择更小的资料夹");
                try{
                    if((File.GetAttributes(folder)&FileAttributes.ReparsePoint)!=0)continue;
                    var entries=new List<LibraryEntry>();
                    foreach(var path in Directory.EnumerateFiles(folder)){if(++files>20000)throw new InvalidOperationException("扫描文件过多，请选择更小的资料夹");if(!path.EndsWith(".exe",StringComparison.OrdinalIgnoreCase))continue;try{if(ProbeExe(path))entries.Add(new(Safety.Relative(Path.GetFileName(path)),"native","unknown"));}catch(IOException){}catch(UnauthorizedAccessException){}}
                    if(entries.Count>0){var id=Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(Path.GetFullPath(folder).ToUpperInvariant())))[..32];output.Add(new(id,Path.GetFileName(folder.TrimEnd(Path.DirectorySeparatorChar)),folder,entries.OrderBy(e=>e.Path,StringComparer.OrdinalIgnoreCase).ToArray()));}
                    if(depth<4)foreach(var child in Directory.EnumerateDirectories(folder)){if(queue.Count+visited.Count>=2000)throw new InvalidOperationException("扫描目录过多，请选择更小的资料夹");queue.Enqueue((child,depth+1));}
                }catch(IOException){}catch(UnauthorizedAccessException){}
            }return output.ToArray();
        },ct);
        await gate.WaitAsync(ct);try{state=state with{Candidates=found};await Save();}finally{gate.Release();}
    }
    public async Task AddSelected(string[] ids,Dictionary<string,string>? selectedEntries=null)
    {
        if(ids.Length==0||ids.Length>100||ids.Distinct().Count()!=ids.Length)throw new ArgumentException("请选择 1–100 项");
        await gate.WaitAsync();try{
            var records=state.Games.ToDictionary(g=>g.Game.Id);
            foreach(var id in ids){var candidate=state.Candidates.SingleOrDefault(c=>c.Id==id)??throw new ArgumentException("候选项已变化");var entry=selectedEntries!=null&&selectedEntries.TryGetValue(id,out var chosen)?candidate.Entries.SingleOrDefault(e=>e.Path==chosen):candidate.Entries.FirstOrDefault();if(entry==null||!ProbeExe(Path.Combine(candidate.Folder,Safety.Relative(entry.Path))))throw new InvalidOperationException("所选入口已变化");
                var game=records.TryGetValue(id,out var old)?old.Game:new LibraryGame(id,candidate.Title,0,false,[],0,null,null,[new(id,"windows",null,candidate.Entries,null,true)]);
                records[id]=new(candidate.Folder,game with{PreferredEntries=new(Windows:new(id,entry.Path))});
            }state=state with{Games=records.Values.ToArray()};await Save();
        }finally{gate.Release();}
    }
    public (string Folder,LibraryGame Game) Resolve(string id){var record=state.Games.Single(g=>g.Visible&&g.Game.Id==id);var root=state.Folders.FirstOrDefault(f=>record.Folder.Equals(f,StringComparison.OrdinalIgnoreCase)||record.Folder.StartsWith(f.TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))??throw new InvalidDataException("入口不属于配置的资料夹");for(var p=record.Folder;p!=null&&p.Length>=root.Length;p=Path.GetDirectoryName(p))if((File.GetAttributes(p)&FileAttributes.ReparsePoint)!=0)throw new InvalidDataException("本地入口包含链接");return(record.Folder,record.Game);}
    public async Task RecordTime(string id,Guid eventID,double elapsed,double endedAt)
    {
        if(eventID==Guid.Empty||!double.IsFinite(elapsed)||elapsed<0||!double.IsFinite(endedAt)||endedAt<0)throw new ArgumentException("无效时长");await gate.WaitAsync();try{if(!state.Games.Any(g=>g.Game.Id==id))throw new ArgumentException("游戏不属于本地游戏库");if(state.TimeEvents.Contains(eventID))return;state=state with{Games=state.Games.Select(r=>r.Game.Id==id?r with{Game=r.Game with{PlaytimeSeconds=r.Game.PlaytimeSeconds+elapsed,LastPlayed=JsonSerializer.SerializeToElement(endedAt)}}:r).ToArray(),TimeEvents=state.TimeEvents.Append(eventID).ToArray()};await Save();}finally{gate.Release();}
    }
    public async Task<JsonElement> Mutate(HttpMethod method,string route,object? body=null)
    {
        var data=JsonSerializer.SerializeToElement(body??new{},AccountStore.Json);var parts=route.Split('/');await gate.WaitAsync();try{
            if(route=="/api/library/collections"&&method==HttpMethod.Post){var id=Guid.Parse(data.GetProperty("id").GetString()!).ToString();var name=data.GetProperty("name").GetString()!;if(string.IsNullOrWhiteSpace(name))throw new ArgumentException("请输入分类名称");state=state with{Collections=state.Collections.Where(c=>c.Id!=id).Append(new(id,name)).ToArray()};}
            else if(parts.Length==5&&parts[3]=="collections"&&method==HttpMethod.Delete){var id=Guid.Parse(parts[4]).ToString();state=state with{Collections=state.Collections.Where(c=>c.Id!=id).ToArray(),Games=state.Games.Select(r=>r with{Game=r.Game with{CollectionIDs=r.Game.CollectionIDs.Where(c=>c!=id).ToArray(),Revision=r.Game.Revision+1}}).ToArray()};}
            else if(parts.Length>=5&&parts[3]=="games"){
                var id=Safety.Id(parts[4]);var record=state.Games.Single(r=>r.Game.Id==id);var game=record.Game;
                if(method==HttpMethod.Delete&&parts.Length==5)record=record with{Visible=false};
                else if(parts.Length==6&&parts[5]=="metadata"){if(data.GetProperty("revision").GetInt64()!=game.Revision)throw new ApiFailure(409);record=record with{Game=game with{Title=data.TryGetProperty("title",out var title)?title.GetString()!:game.Title,Favorite=data.TryGetProperty("favorite",out var favorite)?favorite.GetBoolean():game.Favorite,CollectionIDs=data.TryGetProperty("collectionIDs",out var collections)?collections.Deserialize<string[]>()!:game.CollectionIDs,Revision=game.Revision+1}};}
                else if(parts.Length==6&&parts[5]=="launch-preferences"){var variant=data.GetProperty("variantID");LaunchPreferences? preferences=null;if(variant.ValueKind!=JsonValueKind.Null){var path=data.GetProperty("entryPath").GetString()!;if(variant.GetString()!=id||!game.Variants.Any(v=>v.Id==id&&v.Entries.Any(e=>e.Path==path&&Safety.NativeEntry(path,e.Backend))))throw new ArgumentException("无效入口");preferences=new(Windows:new(id,path));}record=record with{Game=game with{PreferredEntries=preferences}};}
                else throw new InvalidOperationException("本地操作不可用");state=state with{Games=state.Games.Select(g=>g.Game.Id==id?record:g).ToArray()};
            }else throw new InvalidOperationException("本地操作不可用");await Save();return JsonSerializer.SerializeToElement(new{});
        }finally{gate.Release();}
    }
}
public sealed class StandaloneRunner(StandaloneLibrary library,AccountGate gate,Func<string,string,CancellationToken,Task<IGameProcess>> start)
{
    public async Task Run(string gameID,CancellationToken ct)
    {
        var(folder,game)=library.Resolve(gameID);var(_,entry)=Editions.Select(game);if(!StandaloneLibrary.ProbeExe(Path.Combine(folder,entry.Path)))throw new InvalidOperationException("入口已变化");gate.Begin();IGameProcess? process=null;Stopwatch? clock=null;
        Exception? failure=null;
        try{process=await start(folder,entry.Path,ct);clock=Stopwatch.StartNew();await process.Wait(ct);}catch(Exception e){failure=e;}
        finally{
            var stopped=process==null;
            if(process!=null){try{await process.Stop();stopped=true;}catch(Exception e){failure??=e;}if(stopped)try{await process.DisposeAsync();}catch(Exception e){failure??=e;}}
            if(stopped)try{if(clock!=null){clock.Stop();await library.RecordTime(gameID,Guid.NewGuid(),clock.Elapsed.TotalSeconds,DateTimeOffset.UtcNow.ToUnixTimeSeconds());}}catch(Exception e){failure??=e;}finally{gate.End();}
        }
        if(failure!=null)System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(failure).Throw();
    }
}
