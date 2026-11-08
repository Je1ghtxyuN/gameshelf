using System.Security.Cryptography;
using GameShelf.Core;
using GameShelf.NetworkSession;
namespace GameShelf.Windows;
internal static class BrokerReview
{
    const string EntryHash="23da1a8de2ea9c3aaf4b484b39357c4f41e8c5255f3bddcf5f45610b9003942b";
    public static async Task Validate(WindowsLeaseContext context,string root,CancellationToken ct)
    {
        context.Validate(DateTimeOffset.UtcNow);
        if(context.Lease.SaveNamespace!=null){RenpyAdapter.ValidateProof(context.Lease,context.Variant,context.Entry,context.CanonicalID);return;}
        var proof=context.Lease.WindowsLaunch;
        if(proof is not {Synthetic:true,ReviewedProfileID:"gameshelf.synthetic-relative-windows.v1",ProfileRevision:1,EntryPath:"Fixture.exe",Adapter:null,RelativeSavePath:null}||proof.EntrySHA256!=EntryHash||proof.RulesSHA256!="4817ddeea5d8d6b96ac6b9ca8a080e4f3f452d6deef329eb2a112b1f1f0d5f25"||proof.EvidenceSHA256!="5d86fc7be6ad08c33c2c61fea46fd2e9d03fbd65249ea8ce4478aaac2eda78ec"||proof.SourceSignature?.Length!=64||!proof.SourceSignature.All(Uri.IsHexDigit)||proof.SavePaths is not [{Path:"savedata",Kind:"directory"}])throw new BrokerFailure(BrokerStage.Review);
        if(root.Length==0)return;
        string path=RenpyAdapter.CheckedPath(root,context.Entry.Path,false);
        if(new FileInfo(path).Length!=1536)throw new BrokerFailure(BrokerStage.Review);
        await using var stream=File.OpenRead(path);if(Convert.ToHexStringLower(await SHA256.HashDataAsync(stream,ct))!=EntryHash)throw new BrokerFailure(BrokerStage.Review);
    }
}
