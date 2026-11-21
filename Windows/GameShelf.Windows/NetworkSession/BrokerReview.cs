using System.Security.Cryptography;
using GameShelf.Core;
using GameShelf.NetworkSession;
namespace GameShelf.Windows;
internal static class BrokerReview
{
    const string EntryHash="23da1a8de2ea9c3aaf4b484b39357c4f41e8c5255f3bddcf5f45610b9003942b";
    const string MzEntryHash="b2fe0f8cd0adb1d221d662e5f33b36b8245413178d8986b0d6dd3c42fc6d78de";
    const string MzSystemHash="3f0f873d9bced90b87578956b4a13da3ac954353b7a5317e125c8762f1291685";
    public static async Task Validate(WindowsLeaseContext context,string root,CancellationToken ct)
    {
        context.Validate(DateTimeOffset.UtcNow);
        if(root.Length!=0)context.Lease.VerifyWorkspace(root);
        if(context.Lease.SaveNamespace!=null){RenpyAdapter.ValidateProof(context.Lease,context.Variant,context.Entry,context.CanonicalID);return;}
        var proof=context.Lease.WindowsLaunch;
        if(proof?.IsPrivateOverlay(context.Variant,context.Entry.Path)==true)
        {
            if(root.Length!=0 && !StandaloneLibrary.ProbeExe(RenpyAdapter.CheckedPath(root,context.Entry.Path,false)))
                throw new BrokerFailure(BrokerStage.Review);
            return;
        }
        if(proof is {Synthetic:false,ReviewedProfileID:"gameshelf.mz-sakura-windows.v1",ProfileRevision:1,
                     EntryPath:"Game.exe",Adapter:null,RelativeSavePath:null,SavePaths:[{Path:"save",Kind:"directory"}]}
            &&proof.EntrySHA256==MzEntryHash)
        {
            if(root.Length==0)return;
            await VerifyMzFile(root,context.Entry.Path,MzEntryHash,ct);
            await VerifyMzFile(root,"data/System.json",MzSystemHash,ct);
            return;
        }
        if(proof is not {Synthetic:true,ReviewedProfileID:"gameshelf.synthetic-relative-windows.v1",ProfileRevision:1,EntryPath:"Fixture.exe",Adapter:null,RelativeSavePath:null}||proof.EntrySHA256!=EntryHash||proof.RulesSHA256!="4817ddeea5d8d6b96ac6b9ca8a080e4f3f452d6deef329eb2a112b1f1f0d5f25"||proof.EvidenceSHA256!="5d86fc7be6ad08c33c2c61fea46fd2e9d03fbd65249ea8ce4478aaac2eda78ec"||proof.SourceSignature?.Length!=64||!proof.SourceSignature.All(Uri.IsHexDigit)||proof.SavePaths is not [{Path:"savedata",Kind:"directory"}])throw new BrokerFailure(BrokerStage.Review);
        if(root.Length==0)return;
        string path=RenpyAdapter.CheckedPath(root,context.Entry.Path,false);
        if(new FileInfo(path).Length!=1536)throw new BrokerFailure(BrokerStage.Review);
        await using var stream=File.OpenRead(path);if(Convert.ToHexStringLower(await SHA256.HashDataAsync(stream,ct))!=EntryHash)throw new BrokerFailure(BrokerStage.Review);
    }
    static async Task VerifyMzFile(string root,string relative,string expected,CancellationToken ct)
    {
        string path=RenpyAdapter.CheckedPath(root,relative,false);
        if(new FileInfo(path).Length is <=0 or >4*1024*1024)throw new BrokerFailure(BrokerStage.Review);
        await using var stream=File.OpenRead(path);
        if(Convert.ToHexStringLower(await SHA256.HashDataAsync(stream,ct))!=expected)
            throw new BrokerFailure(BrokerStage.Review);
    }

}
