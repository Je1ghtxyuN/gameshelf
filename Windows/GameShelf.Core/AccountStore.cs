using System.Text.Json;
namespace GameShelf.Core;
public sealed class AccountStore(string root,string serverID,string userID)
{
    public string ServerID { get; }=Safety.Id(serverID);
    public string UserID { get; }=Safety.Id(userID);
    public string DirectoryPath { get; }=Path.Combine(root,Safety.CacheKey(serverID,userID));
    private readonly SemaphoreSlim gate=new(1,1);
    private string FilePath(string name)=>Path.Combine(DirectoryPath,name+".json");
    public static readonly JsonSerializerOptions Json=new(JsonSerializerDefaults.Web);
    private async Task Write<T>(string name,T value)
    {
        Directory.CreateDirectory(DirectoryPath);
        var path=FilePath(name); var temp=path+".tmp";
        await File.WriteAllTextAsync(temp,JsonSerializer.Serialize(value,Json)); File.Move(temp,path,true);
    }
    public async Task SaveLibrary(LibrarySnapshot snapshot)
    {
        if(snapshot.SchemaVersion!=2||snapshot.ServerID!=ServerID||snapshot.User.Id!=UserID) throw new InvalidDataException("Library identity mismatch");
        await gate.WaitAsync(); try { await Write("library",snapshot); } finally { gate.Release(); }
    }
    public async Task<LibrarySnapshot?> ReadLibrary()
    {
        if(!File.Exists(FilePath("library")))return null;
        var value=JsonSerializer.Deserialize<LibrarySnapshot>(await File.ReadAllTextAsync(FilePath("library")),Json)!;
        if(value.ServerID!=ServerID||value.User.Id!=UserID||value.SchemaVersion!=2)throw new InvalidDataException("Cache identity mismatch"); return value;
    }
    public async Task<PendingTime[]> Pending()=>File.Exists(FilePath("outbox"))?JsonSerializer.Deserialize<PendingTime[]>(await File.ReadAllTextAsync(FilePath("outbox")),Json)!:[];
    public async Task Enqueue(string gameID,TimeEvent item)
    {
        Safety.Id(gameID);
        if(string.IsNullOrWhiteSpace(item.DeviceID)||item.DeviceID.Length>128||!double.IsFinite(item.EndedAt)||item.EndedAt<0||item.EventID==Guid.Empty||!double.IsFinite(item.ElapsedSeconds)||item.ElapsedSeconds<0||item.ElapsedSeconds>86400)throw new ArgumentException("Invalid time event");
        await gate.WaitAsync(); try {
            var items=(await Pending()).ToList(); var old=items.FirstOrDefault(x=>x.Event.EventID==item.EventID);
            var next=new PendingTime(ServerID,UserID,gameID,item);
            if(old!=null&&old!=next)throw new ArgumentException("Conflicting event reuse");
            if(old==null){items.Add(next);await Write("outbox",items);}
        } finally{gate.Release();}
    }
    public async Task Flush(Func<PendingTime,Task> upload)
    {
        await gate.WaitAsync(); try {
            var items=(await Pending()).ToList();
            while(items.Count>0){var item=items[0];if(item.ServerID!=ServerID||item.UserID!=UserID)throw new InvalidDataException("Outbox identity mismatch");await upload(item);items.RemoveAt(0);await Write("outbox",items);}
        }finally{gate.Release();}
    }
}
public sealed class AccountGate
{
    public bool Active {get;private set;}
    public void Begin(){if(Active)throw new InvalidOperationException("游戏正在运行");Active=true;}
    public void End()=>Active=false;
    public void CanSwitch(){if(Active)throw new InvalidOperationException("请先结束游戏");}
}
