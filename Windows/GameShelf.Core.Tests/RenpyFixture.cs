using GameShelf.Core;
using System.Security.Cryptography;
using System.Text;
static class RenpyFixture
{
    public static string Digest(byte[] bytes)=>Convert.ToHexStringLower(SHA256.HashData(bytes));
    public static byte[] PatchedModule()
    {
        var source=File.ReadAllBytes(Path.Combine(AppContext.BaseDirectory,"Fixtures/renpy-8.3.7-savelocation.py"));
        if(Digest(source)!=RenpyAdapter.SourceSHA256)throw new Exception("MIT fixture source changed");
        var text=Encoding.UTF8.GetString(source);var start=text.IndexOf("    # 2. Game-local savedir.\n",StringComparison.Ordinal);var end=text.IndexOf("    # Scan the location once.\n",start,StringComparison.Ordinal);
        var patched=Encoding.UTF8.GetBytes(text[..start]+"    # GameShelf account adapter v1: use only the supplied account savedir.\n\n"+text[end..]);if(Digest(patched)!=RenpyAdapter.PatchedSHA256)throw new Exception("Pinned fixture patch mismatch");return patched;
    }
    public static async Task<LaunchLease> Export(string root,string server,string user,string canonical,string variant)
    {
        var files=new (string Path,string Role,byte[] Bytes)[]{("Play.exe","entry",Encoding.UTF8.GetBytes("synthetic entry")),("renpy/bootstrap.py","bootstrap",[1]),("renpy/arguments.py","arguments",[2]),("renpy/main.py","main",[3]),("renpy/savelocation.py","locations",PatchedModule()),("renpy/persistent.py","persistent",[4]),("renpy/savetoken.py","tokens",[5]),("game/options.rpy","config",[6])};
        foreach(var file in files){var path=Path.Combine(root,file.Path);Directory.CreateDirectory(Path.GetDirectoryName(path)!);await File.WriteAllBytesAsync(path,file.Bytes);}
        Directory.CreateDirectory(Path.Combine(root,RenpyAdapter.RelativePath));
        var ns=new SaveNamespace(server+"/"+user+"/"+canonical+"/"+Digest(Encoding.UTF8.GetBytes("fixture-compatibility")),RenpyAdapter.RelativePath,RenpyAdapter.Adapter,"fixture.renpy-v1",1,new string('a',64));
        var proof=new RenpyReview(RenpyAdapter.Adapter,"windows","Play.exe",RenpyAdapter.RelativePath,ns.ReviewedProfileID,ns.Revision,ns.RulesSHA256,new string('b',64),new string('c',64),files.Select(f=>new RuntimeFingerprint(f.Path,f.Role,f.Role=="locations"?RenpyAdapter.SourceSHA256:Digest(f.Bytes),Digest(f.Bytes))).ToArray());
        var windows=new WindowsLaunch("Play.exe","reviewed-relative-saves",true,ns.ReviewedProfileID,1,proof.Fingerprints[0].ExportSHA256,ns.RulesSHA256,proof.EvidenceSHA256,proof.SourceSignature,[new("game/saves","directory")],true,ns.Adapter,ns.RelativePath);
        return new(new string('d',32),DateTimeOffset.UtcNow.ToUnixTimeSeconds()+90,server,user,new(variant),new("127.0.0.1",1445,"fixture",null,"synthetic","synthetic"),windows,ns,proof);
    }
}
