namespace GameShelf.NetworkSession;
public sealed record BrokerCredentialName(string User,string? Domain)
{
    public override string ToString()=>"Network credential name (redacted)";
    public static BrokerCredentialName Parse(string value)
    {
        if(string.IsNullOrEmpty(value)||value.Length>256||value.Any(char.IsControl))throw new BrokerFailure(BrokerStage.Context);
        var parts=value.Split('\\');if(parts.Length==2&&parts.All(p=>p.Length>0)&&!value.Contains('@'))return new(parts[1],parts[0]);
        if(parts.Length!=1)throw new BrokerFailure(BrokerStage.Context);
        return new(value,value.Contains('@')?null:".");
    }
}
public static class BrokerStartupPolicy
{
    public static uint ReleaseKillFlag(uint flags,bool authenticated)
    {
        if(!authenticated||(flags&0x1800u)!=0)throw new BrokerFailure(BrokerStage.Identity);
        return flags&~0x2000u;
    }
}
