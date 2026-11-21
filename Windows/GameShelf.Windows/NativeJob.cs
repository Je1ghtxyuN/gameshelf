using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections;
using GameShelf.Core;
namespace GameShelf.Windows;
// A suspended child is assigned before its first instruction: descendants stay in this job.
// Never set BREAKAWAY_OK. Closing the job kills every remaining writer.
public sealed class NativeJob:IGameProcess
{
    IntPtr job;
    NativeJob(IntPtr job)=>this.job=job;
    public static NativeJob Start(string executable,string directory,string? arguments=null,CancellationToken cancellation=default)=>StartCore(executable,directory,arguments,null,cancellation);
    public static NativeJob StartRenpy(string executable,string directory,RenpyLaunchPlan plan,CancellationToken cancellation=default)
    {
        if(!Path.IsPathFullyQualified(plan.SaveRoot)||plan.SaveRoot.Contains('"')||plan.SaveRoot.Any(char.IsControl)||!plan.SaveRoot.Replace('\\','/').EndsWith("/"+RenpyAdapter.RelativePath,StringComparison.Ordinal))throw new ArgumentException("Invalid reviewed save path");
        return StartCore(executable,directory,"--savedir \""+plan.SaveRoot+"\"",plan.Environment,cancellation);
    }
    static NativeJob StartCore(string executable,string directory,string? arguments,IReadOnlyDictionary<string,string>? overrides,CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();var job=CreateJobObject(IntPtr.Zero,null);if(job==IntPtr.Zero)throw new Win32Exception(Marshal.GetLastWin32Error());
        var limits=new ExtendedLimits();limits.Basic.LimitFlags=0x2000;
        if(!SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf<ExtendedLimits>())){var error=Marshal.GetLastWin32Error();CloseHandle(job);throw new Win32Exception(error);}
        var startup=new StartupInfo{Size=(uint)Marshal.SizeOf<StartupInfo>()};
        var command=new StringBuilder("\""+executable+"\""+(arguments==null?"":" "+arguments));
        IntPtr environment=IntPtr.Zero;ProcessInfo process;
        try{
            if(overrides!=null){var values=System.Environment.GetEnvironmentVariables().Cast<DictionaryEntry>().ToDictionary(v=>(string)v.Key,v=>(string?)v.Value??"",StringComparer.OrdinalIgnoreCase);foreach(var item in overrides){if(item.Key is not ("RENPY_PATH_TO_SAVES" or "RENPY_MULTIPERSISTENT" or "PYTHONDONTWRITEBYTECODE"))throw new ArgumentException("Unreviewed environment variable");values[item.Key]=item.Value;}var block=string.Join('\0',values.OrderBy(v=>v.Key,StringComparer.OrdinalIgnoreCase).Select(v=>v.Key+"="+v.Value))+"\0\0";environment=Marshal.StringToHGlobalUni(block);}
            if(!CreateProcess(executable,command,IntPtr.Zero,IntPtr.Zero,false,0x4|0x400,environment,directory,ref startup,out process))throw new Win32Exception(Marshal.GetLastWin32Error());
        }catch{CloseHandle(job);throw;}finally{if(environment!=IntPtr.Zero)Marshal.FreeHGlobal(environment);}
        try{
            if(!AssignProcessToJobObject(job,process.Process)){var error=Marshal.GetLastWin32Error();TerminateProcess(process.Process,1);throw new Win32Exception(error);}
            cancellation.ThrowIfCancellationRequested();
            if(ResumeThread(process.Thread)==uint.MaxValue){var error=Marshal.GetLastWin32Error();TerminateJobObject(job,1);throw new Win32Exception(error);}
            return new(job);
        }catch{CloseHandle(job);throw;}finally{CloseHandle(process.Thread);CloseHandle(process.Process);}
    }
    uint Active()
    {
        if(job==IntPtr.Zero)return 0;
        if(!QueryInformationJobObject(job,1,out var stats,(uint)Marshal.SizeOf<BasicAccounting>(),IntPtr.Zero))throw new Win32Exception(Marshal.GetLastWin32Error());return stats.ActiveProcesses;
    }
    public async Task Wait(CancellationToken ct){while(Active()!=0)await Task.Delay(250,ct);}
    public async Task Stop(){if(job==IntPtr.Zero)return;if(Active()!=0&&!TerminateJobObject(job,0))throw new Win32Exception(Marshal.GetLastWin32Error());await Wait(CancellationToken.None);}
    public ValueTask DisposeAsync(){if(job!=IntPtr.Zero){CloseHandle(job);job=IntPtr.Zero;}return ValueTask.CompletedTask;}
    [StructLayout(LayoutKind.Sequential)]struct StartupInfo{public uint Size;public IntPtr Reserved,Desktop,Title;public uint X,Y,XSize,YSize,XChars,YChars,Fill,Flags;public ushort Show,Reserved2;public IntPtr ReservedPtr,Input,Output,Error;}
    [StructLayout(LayoutKind.Sequential)]struct ProcessInfo{public IntPtr Process,Thread;public uint ProcessID,ThreadID;}
    [StructLayout(LayoutKind.Sequential)]struct BasicLimits{public long ProcessTime,JobTime;public uint LimitFlags;public UIntPtr MinWorkingSet,MaxWorkingSet;public uint ActiveLimit;public UIntPtr Affinity;public uint Priority,Scheduling;}
    [StructLayout(LayoutKind.Sequential)]struct IoCounters{public ulong ReadOps,WriteOps,OtherOps,ReadBytes,WriteBytes,OtherBytes;}
    [StructLayout(LayoutKind.Sequential)]struct ExtendedLimits{public BasicLimits Basic;public IoCounters IO;public UIntPtr ProcessMemory,JobMemory,PeakProcessMemory,PeakJobMemory;}
    [StructLayout(LayoutKind.Sequential)]struct BasicAccounting{public long UserTime,KernelTime,PeriodUserTime,PeriodKernelTime;public uint PageFaults,TotalProcesses,ActiveProcesses,TerminatedProcesses;}
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateJobObject(IntPtr attributes,string? name);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool SetInformationJobObject(IntPtr job,int info,ref ExtendedLimits value,uint size);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool QueryInformationJobObject(IntPtr job,int info,out BasicAccounting value,uint size,IntPtr returned);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true,EntryPoint="CreateProcessW")]static extern bool CreateProcess(string app,StringBuilder command,IntPtr processAttributes,IntPtr threadAttributes,bool inherit,uint flags,IntPtr environment,string directory,ref StartupInfo startup,out ProcessInfo process);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
    [DllImport("kernel32.dll",SetLastError=true)]static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool TerminateProcess(IntPtr process,uint code);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool TerminateJobObject(IntPtr job,uint code);
    [DllImport("kernel32.dll")]static extern bool CloseHandle(IntPtr handle);
}
