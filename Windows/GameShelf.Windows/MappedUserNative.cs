using System;
using System.Runtime.InteropServices;
using System.Text;
namespace GameShelf.Windows
{
    public sealed class MappedUserQueryException : Exception
    {
        public uint NativeErrorCode { get; private set; }
        public MappedUserQueryException(uint code) : base("Mapped user query failed (" + code + ")") { NativeErrorCode = code; }
    }
    // Also embedded unchanged for Windows PowerShell5.1 Add-Type; keep C#5-compatible.
    public static class MappedUserNative
    {
        internal delegate uint UserQuery(string drive, StringBuilder value, ref uint size);
        [DllImport("mpr.dll", EntryPoint="WNetGetUserW", CharSet=CharSet.Unicode, ExactSpelling=true)]
        private static extern uint WNetGetUser(string drive, StringBuilder value, ref uint size);
        public static string GetMappedUser(string drive) { return QueryUser(drive, WNetGetUser); }
        internal static string QueryUser(string drive, UserQuery query)
        {
            if (drive == null || drive.Length != 2 || drive[1] != ':' || !((drive[0] >= 'A' && drive[0] <= 'Z') || (drive[0] >= 'a' && drive[0] <= 'z')))
                throw new ArgumentException("Exact mapped local drive required");
            uint capacity = 256;
            for (int attempt = 0; attempt < 2; attempt++)
            {
                var value = new StringBuilder((int)capacity); uint size = capacity;
                uint code = query(drive, value, ref size);
                if (code == 234 && attempt == 0 && size > capacity && size <= 4096) { capacity = size; continue; }
                if (code != 0) throw new MappedUserQueryException(code);
                string user = value.ToString();
                if (user.Length == 0 || user.Length >= capacity) throw new MappedUserQueryException(13);
                foreach (char c in user) if (char.IsControl(c)) throw new MappedUserQueryException(13);
                return user;
            }
            throw new MappedUserQueryException(234);
        }
    }
}
