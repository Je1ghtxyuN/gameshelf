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
        var checkPath=Path.GetTempFileName();await File.WriteAllTextAsync(checkPath,compiled);
        var info=new ProcessStartInfo("powershell.exe"){UseShellExecute=false,RedirectStandardOutput=true,RedirectStandardError=true,CreateNoWindow=true};
        info.ArgumentList.Add("-NoProfile");info.ArgumentList.Add("-File");info.ArgumentList.Add(Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,"../../../../Tools/test-smb-powershell.ps1")));
        info.ArgumentList.Add("-CompiledCheckPath");info.ArgumentList.Add(checkPath);
        using var process=Process.Start(info)!;var stdout=process.StandardOutput.ReadToEndAsync();var stderr=process.StandardError.ReadToEndAsync();await process.WaitForExitAsync();Console.Write(await stdout);await stderr;File.Delete(checkPath);if(process.ExitCode!=0)throw new Exception("Actual generated PowerShell predicate/issuance fixtures failed");
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
