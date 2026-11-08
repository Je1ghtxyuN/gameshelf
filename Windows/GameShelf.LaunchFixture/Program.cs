// Harmless fixture only; no real game engine, NAS or OS-profile writes.
if(args.Length==2&&args[0]=="--savedir")
{
    var root=args[1];Directory.CreateDirectory(root);
    await File.WriteAllTextAsync(Path.Combine(root,"native-adapter-observed.json"),System.Text.Json.JsonSerializer.Serialize(new{arguments=args,auxiliary=Environment.GetEnvironmentVariable("RENPY_PATH_TO_SAVES"),multiPersistent=Environment.GetEnvironmentVariable("RENPY_MULTIPERSISTENT"),dontWriteBytecode=Environment.GetEnvironmentVariable("PYTHONDONTWRITEBYTECODE"),appData=Environment.GetEnvironmentVariable("APPDATA")}));
}
else{Directory.CreateDirectory("savedata");await File.WriteAllTextAsync(Path.Combine("savedata","progress.txt"),Path.GetFileName(Environment.CurrentDirectory));}
await Task.Delay(2100);
