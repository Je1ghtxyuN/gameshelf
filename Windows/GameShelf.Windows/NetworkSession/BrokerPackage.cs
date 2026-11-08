using System.Reflection;
using System.Security.Cryptography;
using GameShelf.Core;
using GameShelf.NetworkSession;
namespace GameShelf.Windows;
internal sealed class BrokerPackage:IDisposable
{
    readonly List<FileStream> pins=[];
    public string Executable{get;}
    public BrokerPackage()
    {
        try{
            using var resource=typeof(BrokerPackage).Assembly.GetManifestResourceStream("GameShelf.BrokerPins")??throw new BrokerFailure(BrokerStage.Package);
            pins.AddRange(BrokerPins.Verify(AppContext.BaseDirectory,BrokerPins.Read(resource)));
            Executable=Path.Combine(AppContext.BaseDirectory,"GameShelf.Broker.exe");
        }catch{Dispose();throw new BrokerFailure(BrokerStage.Package);}
    }
    public void Dispose(){foreach(var stream in pins)stream.Dispose();pins.Clear();}
}
