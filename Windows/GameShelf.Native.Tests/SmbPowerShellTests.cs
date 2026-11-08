using System.Diagnostics;
using System.Reflection;
using GameShelf.Windows;
static class SmbPowerShellTests
{
    public static async Task Run()
    {
        var compiled=(string)typeof(ShellMappingBackend).GetField("Check",BindingFlags.Static|BindingFlags.NonPublic)!.GetRawConstantValue()!;
        var prefix=".EndsWith(('";var start=compiled.IndexOf(prefix,StringComparison.Ordinal)+prefix.Length;var end=compiled.IndexOf('\'',start);var suffix=compiled[start..end];
        Console.WriteLine("Compiled production Check suffix character count="+suffix.Length);
        if(suffix.Length!=1||suffix[0]!='\\')throw new Exception("Compiled suffix is not exactly one separator");
        var checkPath=Path.GetTempFileName();await File.WriteAllTextAsync(checkPath,compiled);var nativePath=Path.GetTempFileName();await File.WriteAllTextAsync(nativePath,ShellMappingBackend.NativeSource());
        var info=new ProcessStartInfo("powershell.exe"){UseShellExecute=false,RedirectStandardOutput=true,RedirectStandardError=true,CreateNoWindow=true};
        info.ArgumentList.Add("-NoProfile");info.ArgumentList.Add("-File");info.ArgumentList.Add(Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,"../../../../Tools/test-smb-powershell.ps1")));
        info.ArgumentList.Add("-CompiledCheckPath");info.ArgumentList.Add(checkPath);info.ArgumentList.Add("-NativeSourcePath");info.ArgumentList.Add(nativePath);
        using var process=Process.Start(info)!;var stdout=process.StandardOutput.ReadToEndAsync();var stderr=process.StandardError.ReadToEndAsync();await process.WaitForExitAsync();Console.Write(await stdout);await stderr;File.Delete(checkPath);File.Delete(nativePath);if(process.ExitCode!=0)throw new Exception("Actual generated PowerShell predicate/issuance fixtures failed");
    }
    public static Task Allocation()
    {
        var method=typeof(ShellMappingBackend).GetMethod("ChooseAvailableDrive",BindingFlags.Static|BindingFlags.NonPublic)??throw new Exception("Pure mapped-letter allocator is missing");
        string Pick(string[] visible,string[] mapped)=>(string)method.Invoke(null,[visible,mapped])!;
        if(Pick(["C:"],["Z:"])!="Y:"||Pick(["C:","Y:"],["z:"])!="X:")throw new Exception("Mapped unavailable letter was selected");
        try{Pick([],Enumerable.Range('D',23).Select(c=>$"{(char)c}:").ToArray());throw new Exception("Occupied letters accepted");}catch(TargetInvocationException e)when(e.InnerException is InvalidOperationException){}
        try{Pick([],["Z:\\private"]);throw new Exception("Malformed mapping letter accepted");}catch(TargetInvocationException e)when(e.InnerException is InvalidDataException){}
        return Task.CompletedTask;
    }
    public static async Task NonBlockingChoice()
    {
        var backend=new BlockingChoice();var view=new WindowsView(backend);var share=new GameShelf.Core.SmbShare("fixture.invalid",2445,"synthetic",null,"fixture","synthetic-only");
        using var returned=new ManualResetEventSlim();Task<string>? pending=null;Exception? error=null;
        var caller=new Thread(()=>{try{pending=view.Connect(share,CancellationToken.None);}catch(Exception e){error=e;}finally{returned.Set();}});caller.Start();
        bool responsive=false;
        try{if(!backend.Entered.Wait(5000))throw new Exception("Choice did not start");responsive=returned.Wait(500);}
        finally{backend.Release.Set();caller.Join(5000);}
        if(error!=null)throw error;if(pending!=null)await pending;await view.Disconnect();
        if(!responsive)throw new Exception("Connect blocked its calling thread while selecting a drive");
    }
    sealed class BlockingChoice:IMappingBackend
    {
        public readonly ManualResetEventSlim Entered=new(),Release=new();
        public string ChooseDrive(){Entered.Set();if(!Release.Wait(5000))throw new TimeoutException();return "Z:";}
        public Task<MappingReceipt> Create(GameShelf.Core.SmbShare share,string drive,Guid operationID,CancellationToken ct)=>Task.FromResult(new MappingReceipt(operationID,drive,share.Unc,share.Host,share.ShareName,share.Port,"fixture"));
        public Task Remove(MappingReceipt receipt)=>Task.CompletedTask;
    }
    public static async Task Failure(string fqid,string expectedStage,int? code)
    {
        // Invoke the actual production PowerShell launcher with a synthetic ErrorRecord.
        // No SMB commands or credentials are used; the marker must never escape in exceptions.
        var script="$stage='"+expectedStage+"';$ex=New-Object Exception('secret-fixture-only \\\\fixture.invalid\\private');$er=New-Object Management.Automation.ErrorRecord($ex,'"+fqid+"',[Management.Automation.ErrorCategory]::InvalidOperation,$null);throw $er";
        var method=typeof(ShellMappingBackend).GetMethod("PowerShell",BindingFlags.Static|BindingFlags.NonPublic)!;
        Exception? failure=null;try{await (Task<string>)method.Invoke(null,[script,new{},TimeSpan.FromSeconds(10),CancellationToken.None])!;}catch(Exception e){failure=e;}
        if(failure==null||failure.GetType().Name!="SmbMappingException"||failure.GetType().GetProperty("Stage")?.GetValue(failure)?.ToString()!=expectedStage||!Equals(failure.GetType().GetProperty("Win32Error")?.GetValue(failure),code))throw new Exception("Structured stage/Win32 failure missing");
        if(failure.InnerException!=null||failure.ToString().Contains("secret-fixture-only")||failure.ToString().Contains("fixture.invalid"))throw new Exception("Unredacted PowerShell error escaped");
    }
}
