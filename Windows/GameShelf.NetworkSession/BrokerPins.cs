using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Buffers.Binary;
using System.Security.Cryptography;
namespace GameShelf.NetworkSession;
public static class BrokerPins
{
    public static readonly string[] Names=["GameShelf.Broker.exe","GameShelf.Broker.dll","GameShelf.Broker.deps.json","GameShelf.Broker.runtimeconfig.json","GameShelf.Core.dll","GameShelf.NetworkSession.dll"];
    public static Dictionary<string,string> Read(Stream resource)
    {
        using var text=new StreamReader(resource,leaveOpen:true);var values=new Dictionary<string,string>(StringComparer.Ordinal);string? line;
        while((line=text.ReadLine())!=null){var pieces=line.Split('|');if(pieces.Length!=2||!Names.Contains(pieces[0])||pieces[1].Length!=64||!pieces[1].All(Uri.IsHexDigit)||!values.TryAdd(pieces[0],pieces[1]))throw new BrokerFailure(BrokerStage.Package);}
        if(values.Count!=Names.Length)throw new BrokerFailure(BrokerStage.Package);return values;
    }
    public static Stream ReadGuiResource(string guiAssembly)
    {
        using var file=File.OpenRead(guiAssembly);using var pe=new PEReader(file);var metadata=pe.GetMetadataReader();
        foreach(var handle in metadata.ManifestResources){var item=metadata.GetManifestResource(handle);if(metadata.GetString(item.Name)!="GameShelf.BrokerPins")continue;
            if(!item.Implementation.IsNil||pe.PEHeaders.CorHeader==null)throw new BrokerFailure(BrokerStage.Package);
            var section=pe.GetSectionData(pe.PEHeaders.CorHeader.ResourcesDirectory.RelativeVirtualAddress).GetContent();int offset=checked((int)item.Offset);
            if(offset<0||offset>section.Length-4)throw new BrokerFailure(BrokerStage.Package);int length=BinaryPrimitives.ReadInt32LittleEndian(section.AsSpan(offset,4));
            if(length<=0||length>4096||offset+4+length>section.Length)throw new BrokerFailure(BrokerStage.Package);
            return new MemoryStream(section.AsSpan(offset+4,length).ToArray(),false);
        }
        throw new BrokerFailure(BrokerStage.Package);
    }
    public static IReadOnlyList<FileStream> Verify(string folder,Dictionary<string,string> hashes)
    {
        var opened=new List<FileStream>();try{if(!hashes.Keys.ToHashSet(StringComparer.Ordinal).SetEquals(Names))throw new BrokerFailure(BrokerStage.Package);
            foreach(var name in Names){var path=Path.Combine(folder,name);if((File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)throw new BrokerFailure(BrokerStage.Package);var file=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read);opened.Add(file);if(!Convert.ToHexString(SHA256.HashData(file)).Equals(hashes[name],StringComparison.OrdinalIgnoreCase))throw new BrokerFailure(BrokerStage.Package);}return opened;
        }catch{foreach(var file in opened)file.Dispose();throw new BrokerFailure(BrokerStage.Package);}
    }
}
