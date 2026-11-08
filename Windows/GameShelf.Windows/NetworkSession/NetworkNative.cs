using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using GameShelf.Core;
using GameShelf.NetworkSession;
namespace GameShelf.Windows;
internal static class NetworkNative
{
    internal static BrokerPeer Identity(IntPtr process)
    {
        if(!OpenProcessToken(process,8,out var token))throw new BrokerFailure(BrokerStage.Identity,Marshal.GetLastWin32Error());
        using(token){using var identity=new WindowsIdentity(token.DangerousGetHandle());
            if(!GetTokenInformation(token.DangerousGetHandle(),10,out Statistics stats,Marshal.SizeOf<Statistics>(),out _)||!ProcessIdToSessionId(GetProcessId(process),out var session))throw new BrokerFailure(BrokerStage.Identity,Marshal.GetLastWin32Error());
            return new((int)GetProcessId(process),(int)session,identity.User?.Value??throw new BrokerFailure(BrokerStage.Identity),stats.Authentication.High.ToString("X8")+stats.Authentication.Low.ToString("X8"));}
    }
    internal static BrokerPeer Parent(int pid)
    {
        using var handle=OpenProcess(0x1000,false,(uint)pid);if(handle.IsInvalid)throw new BrokerFailure(BrokerStage.Identity,Marshal.GetLastWin32Error());return Identity(handle.DangerousGetHandle());
    }
    internal static string EnvironmentBlock()=>string.Join('\0',Environment.GetEnvironmentVariables().Cast<System.Collections.DictionaryEntry>().Select(e=>((string)e.Key,Value:(string?)e.Value??"")).OrderBy(e=>e.Item1,StringComparer.OrdinalIgnoreCase).Select(e=>e.Item1+"="+e.Value))+"\0\0";
    internal static ChildProcess Launch(string exe,string pipe,Guid operation,SmbShare share)
    {
        share.Validate();if(share.Username.Length>256||share.Password.Length>4096||share.Username.Any(char.IsControl)||share.Password.Contains('\0'))throw new BrokerFailure(BrokerStage.Context);
        var credential=BrokerCredentialName.Parse(share.Username);
        var own=Identity(GetCurrentProcess());var startup=new StartupInfo{Size=(uint)Marshal.SizeOf<StartupInfo>(),Flags=1,Show=0};
        var command=new StringBuilder("\""+exe+"\" --broker-child "+pipe+" "+operation.ToString("N")+" "+own.Pid);
        IntPtr env=Marshal.StringToHGlobalUni(EnvironmentBlock());Information process=default;
        try{if(!CreateProcessWithLogonW(credential.User,credential.Domain,share.Password,2,exe,command,0x4|0x400,env,Path.GetDirectoryName(exe)!,ref startup,out process))throw new BrokerFailure(BrokerStage.Identity,Marshal.GetLastWin32Error());}
        finally{Marshal.FreeHGlobal(env);}
        IntPtr job=CreateJobObject(IntPtr.Zero,null);
        try{
            if(job==IntPtr.Zero)throw new BrokerFailure(BrokerStage.Identity,Marshal.GetLastWin32Error());
            var limits=new ExtendedLimits();limits.Basic.Flags=0x2000;
            if(!SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf<ExtendedLimits>())||!AssignProcessToJobObject(job,process.Process))throw new BrokerFailure(BrokerStage.Identity,Marshal.GetLastWin32Error());
            var actual=Identity(process.Process);BrokerHandshake.Pair(own,actual,(int)process.Pid);
            if(ResumeThread(process.Thread)==uint.MaxValue)throw new BrokerFailure(BrokerStage.Identity,Marshal.GetLastWin32Error());
            return new(process.Process,job,actual);
        }catch{TerminateProcess(process.Process,1);CloseHandle(process.Process);if(job!=IntPtr.Zero)CloseHandle(job);throw;}
        finally{CloseHandle(process.Thread);}
    }
    internal sealed class ChildProcess(IntPtr process,IntPtr job,BrokerPeer identity):IDisposable
    {
        public BrokerPeer Identity{get;}=identity;
        public void ReleaseStartupGuard(bool authenticated)
        {
            if(!QueryInformationJobObject(job,9,out ExtendedLimits limits,(uint)Marshal.SizeOf<ExtendedLimits>(),IntPtr.Zero))throw new BrokerFailure(BrokerStage.Identity,Marshal.GetLastWin32Error());
            // No breakaway flags are added; clear only our kill-on-close startup guard.
            limits.Basic.Flags=BrokerStartupPolicy.ReleaseKillFlag(limits.Basic.Flags,authenticated);
            if(!SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf<ExtendedLimits>()))throw new BrokerFailure(BrokerStage.Identity,Marshal.GetLastWin32Error());
        }
        public async Task NormalExit(CancellationToken ct){while(WaitForSingleObject(process,50)==258){ct.ThrowIfCancellationRequested();await Task.Delay(25,ct);}if(!GetExitCodeProcess(process,out var code)||code!=0)throw new BrokerFailure(BrokerStage.Transport);}
        public void Dispose(){if(job!=IntPtr.Zero){CloseHandle(job);job=IntPtr.Zero;}if(process!=IntPtr.Zero){CloseHandle(process);process=IntPtr.Zero;}}
    }
    [StructLayout(LayoutKind.Sequential)]struct Luid{public uint Low;public int High;}
    [StructLayout(LayoutKind.Sequential)]struct Statistics{public Luid Token,Authentication;public long Expiration;public int Type,Impersonation;public uint Charged,Available,Groups,Privileges;public Luid Modified;}
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]struct StartupInfo{public uint Size;public string? Reserved,Desktop,Title;public uint X,Y,Width,Height,XCount,YCount,Fill,Flags;public ushort Show,ReservedCount;public IntPtr ReservedPtr,Input,Output,Error;}
    [StructLayout(LayoutKind.Sequential)]struct Information{public IntPtr Process,Thread;public uint Pid,Tid;}
    [StructLayout(LayoutKind.Sequential)]struct BasicLimits{public long ProcessTime,JobTime;public uint Flags;public UIntPtr Min,Max;public uint Active;public UIntPtr Affinity;public uint Priority,Scheduling;}
    [StructLayout(LayoutKind.Sequential)]struct Counters{public ulong A,B,C,D,E,F;}
    [StructLayout(LayoutKind.Sequential)]struct ExtendedLimits{public BasicLimits Basic;public Counters Counters;public UIntPtr ProcessMemory,JobMemory,PeakProcess,PeakJob;}
    [DllImport("kernel32.dll")]internal static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll",SetLastError=true)]static extern uint GetProcessId(IntPtr process);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool ProcessIdToSessionId(uint pid,out uint session);
    [DllImport("kernel32.dll",SetLastError=true)]static extern Microsoft.Win32.SafeHandles.SafeProcessHandle OpenProcess(uint access,bool inherit,uint pid);
    [DllImport("advapi32.dll",SetLastError=true)]static extern bool OpenProcessToken(IntPtr process,uint access,out Microsoft.Win32.SafeHandles.SafeAccessTokenHandle token);
    [DllImport("advapi32.dll",SetLastError=true)]static extern bool GetTokenInformation(IntPtr token,int kind,out Statistics value,int size,out int returned);
    [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool CreateProcessWithLogonW(string user,string? domain,string password,uint logon,string exe,StringBuilder command,uint flags,IntPtr env,string cwd,ref StartupInfo startup,out Information process);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateJobObject(IntPtr attributes,string? name);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool SetInformationJobObject(IntPtr job,int kind,ref ExtendedLimits limits,uint size);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool QueryInformationJobObject(IntPtr job,int kind,out ExtendedLimits limits,uint size,IntPtr returned);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
    [DllImport("kernel32.dll",SetLastError=true)]static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll")]static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetExitCodeProcess(IntPtr process,out uint code);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool TerminateProcess(IntPtr process,uint code);
    [DllImport("kernel32.dll")]static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll",SetLastError=true)]internal static extern bool GetNamedPipeClientProcessId(IntPtr pipe,out uint pid);
    [DllImport("kernel32.dll",SetLastError=true)]internal static extern bool GetNamedPipeServerProcessId(IntPtr pipe,out uint pid);
}
