using GameShelf.NetworkSession;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Security.Cryptography;
using System.Text.Json;
if(args.Length==3&&args[0]=="--compare-pins"){try{using var first=BrokerPins.ReadGuiResource(args[1]);using var second=BrokerPins.ReadGuiResource(args[2]);if(!SHA256.HashData(first).SequenceEqual(SHA256.HashData(second)))throw new InvalidDataException();Console.WriteLine("PASS bootstrap embedded pins equal actual published GUI pins; metadata only");return 0;}catch{Console.Error.WriteLine("FAIL embedded pins differ");return 1;}}
if(args.Length==3&&args[0]=="--export-pins"){try{using var source=BrokerPins.ReadGuiResource(Path.Combine(args[1],"GameShelf.Windows.dll"));using var output=File.Create(args[2]);source.CopyTo(output);Console.WriteLine("PASS GUI broker pins exported; metadata only");return 0;}catch{Console.Error.WriteLine("FAIL GUI pin export");return 1;}}
if(args.Length is <1 or >2){Console.Error.WriteLine("Package directory required");return 2;}
string root=args[^1];
if(args.Length==2&&args[0]=="--diagnose"){
    var resources=new List<string>();using(var file=File.OpenRead(Path.Combine(root,"GameShelf.Windows.dll")))using(var pe=new PEReader(file)){var metadata=pe.GetMetadataReader();foreach(var item in metadata.ManifestResources){string name=metadata.GetString(metadata.GetManifestResource(item).Name);resources.Add(name.Contains('/')||name.Contains('\\')?"[nonlogical-resource-name]":name);}}
    Dictionary<string,string>? pins=null;try{using var r=BrokerPins.ReadGuiResource(Path.Combine(root,"GameShelf.Windows.dll"));pins=BrokerPins.Read(r);}catch{}
    Console.WriteLine(JsonSerializer.Serialize(new{resources,pinsReadable=pins!=null,files=BrokerPins.Names.Select(name=>new{name,exists=File.Exists(Path.Combine(root,name)),matches=pins!=null&&pins.TryGetValue(name,out var expected)&&File.Exists(Path.Combine(root,name))&&Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(Path.Combine(root,name)))).Equals(expected,StringComparison.OrdinalIgnoreCase)})}));return 0;
}
try{using var resource=BrokerPins.ReadGuiResource(Path.Combine(root,"GameShelf.Windows.dll"));var files=BrokerPins.Verify(root,BrokerPins.Read(resource));foreach(var file in files)file.Dispose();Console.WriteLine("PASS six published broker dependencies match GUI embedded fingerprints; metadata/hash reads only");return 0;}catch{Console.Error.WriteLine("FAIL broker package pins; no native or network operation attempted");return 1;}
