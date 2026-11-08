using GameShelf.Windows;
using System.Diagnostics;
if(!OperatingSystem.IsWindows()){Console.WriteLine("SKIP Windows native job tests (requires Windows)");return 0;}
var system=Environment.GetFolderPath(Environment.SpecialFolder.System);
var failed=0;var count=0;
async Task Test(string name,Func<Task> action){count++;try{await action();Console.WriteLine("PASS "+name);}catch(Exception e){failed++;Console.WriteLine("FAIL "+name+": "+e.Message);}}
await Test("actual generated PowerShell credential/port/ownership fixtures",SmbPowerShellTests.Run);
await Test("structured Create FQID Win32 error without secrets",()=>SmbPowerShellTests.Failure("Windows System Error 1219,New-SmbMapping","Create",1219));
await Test("unknown error number is not scraped from message",()=>SmbPowerShellTests.Failure("SyntheticFailure1219","Verify",null));
await Test("cleanup failure has separate stage",()=>SmbPowerShellTests.Failure("Windows System Error 5,Remove-SmbMapping","Cleanup",5));
await Test("pure allocator reserves mapped letters and validates input",SmbPowerShellTests.Allocation);
await Test("Connect drive selection does not block its calling thread",SmbPowerShellTests.NonBlockingChoice);
await Test("mapped-user exact device and bounded native return codes",MappedUserTests.Run);
if(args.SequenceEqual(new[]{"--smb-powershell-only"})){Console.WriteLine($"{count-failed}/{count} SMB PowerShell tests passed");return failed==0?0:1;}
await Test("native suspended launch and normal exit",async()=>{await using var job=NativeJob.Start(Path.Combine(system,"cmd.exe"),system,"/c exit 0");using var timeout=new CancellationTokenSource(10000);await job.Wait(timeout.Token);await job.Stop();});
await Test("job tracks descendants after launcher exits",async()=>{await using var job=NativeJob.Start(Path.Combine(system,"cmd.exe"),system,"/c start /b ping.exe -n 4 127.0.0.1");var clock=Stopwatch.StartNew();using var timeout=new CancellationTokenSource(15000);await job.Wait(timeout.Token);if(clock.Elapsed<TimeSpan.FromSeconds(2))throw new Exception("Descendant escaped monitoring");});
await Test("job cancellation kills all descendants",async()=>{await using var job=NativeJob.Start(Path.Combine(system,"cmd.exe"),system,"/c start /b ping.exe -n 100 127.0.0.1");await Task.Delay(400);await job.Stop();using var timeout=new CancellationTokenSource(3000);await job.Wait(timeout.Token);});
await Test("missing executable fails without surviving job",async()=>{try{await using var job=NativeJob.Start(Path.Combine(system,"gameshelf-no-such-file.exe"),system);throw new Exception("Unexpected launch");}catch(System.ComponentModel.Win32Exception){}await Task.CompletedTask;});
await Test("approved synthetic EXE per-account isolation + lease + time over loopback HTTP",SyntheticLaunch.Run);
await Test("standalone native EXE usual saves/time persistence",SyntheticLaunch.RunStandalone);
await Test("reviewed RenPy adapter fixed args/env on actual native EXE",SyntheticLaunch.RunRenpyAdapter);
Console.WriteLine($"{count-failed}/{count} native passed");return failed==0?0:1;
