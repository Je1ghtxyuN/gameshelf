using System.Text;

namespace GameShelf.Core;

public sealed record SmbEnrollmentStatus(string State, string? Share);

public sealed record SmbEnrollmentChallenge(string ChallengeID, string Share, string Filename)
{
    public async Task<string> ReadProof(string mappedRoot, CancellationToken ct)
    {
        if (Filename != Safety.Id(ChallengeID)+".proof")
            throw new InvalidDataException("服务器返回的 SMB 验证凭证无效。");
        var path = RenpyAdapter.CheckedPath(mappedRoot,Filename,false);
        await using var file = new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read,4096,true);
        if (file.Length != 64)
            throw new InvalidDataException("SMB 验证凭证无效。");
        var bytes = new byte[64];
        await file.ReadExactlyAsync(bytes,ct);
        if (file.ReadByte() != -1 || !bytes.All(value => value is >= (byte)'0' and <= (byte)'9' or >= (byte)'a' and <= (byte)'f'))
            throw new InvalidDataException("SMB 验证凭证无效。");
        return Encoding.ASCII.GetString(bytes);
    }
}
