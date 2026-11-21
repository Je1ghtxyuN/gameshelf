using System.Diagnostics;

namespace GameShelf.Core;

public sealed class PersonalNasPreview(AccountStore account)
{
    string BindingPath(string variantID) =>
        Path.Combine(account.DirectoryPath, "personal-nas-preview", Safety.Id(variantID) + ".path");

    public string? Folder(string variantID)
    {
        try { return File.ReadAllText(BindingPath(variantID)); }
        catch (FileNotFoundException) { return null; }
        catch (DirectoryNotFoundException) { return null; }
    }

    string RootPath(string rootID) =>
        Path.Combine(account.DirectoryPath, "nas-roots", Safety.Id(rootID) + ".path");

    public string? RootFolder(string rootID)
    {
        var path = RootPath(rootID);
        try { return File.ReadAllText(path); }
        catch (FileNotFoundException) { return null; }
        catch (DirectoryNotFoundException) { return null; }
    }

    public void BindRoot(string rootID, string folder)
    {
        var canonical = ValidateNetworkFolder(folder);
        SavePath(RootPath(rootID), canonical);
    }

    public void ClearRoot(string rootID) => File.Delete(RootPath(rootID));

    public bool HasRootBinding(LibraryVariant variant) =>
        variant.NasSource is { } source && RootFolder(source.RootID) != null;

    public string ResolveFolder(LibraryVariant variant)
    {
        if (variant.NasSource is { } source && RootFolder(source.RootID) is { } root)
            return source.Relative == "." ? root : Path.Combine(root, Safety.Relative(source.Relative).Replace('/', Path.DirectorySeparatorChar));
        return Folder(variant.Id) ?? throw new InvalidOperationException("请在「设置 → 资料夹」选择 NAS 游戏库根目录。");
    }

    public void Bind(LibraryGame game, string variantID, LibraryEntry entry, string folder)
    {
        var canonical = Validate(game, variantID, entry, folder);
        SavePath(BindingPath(variantID), canonical);
    }

    static void SavePath(string path, string canonical)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            File.WriteAllText(temporary, canonical);
            File.Move(temporary, path, overwrite: true);
        }
        finally { File.Delete(temporary); }
    }

    public void Clear(string variantID) => File.Delete(BindingPath(variantID));

    public static string Validate(LibraryGame game, string variantID, LibraryEntry entry, string folder)
    {
        Safety.Id(game.Id);
        Safety.Id(variantID);
        if (!Safety.NativeEntry(entry.Path, entry.Backend) ||
            !game.Variants.Any(variant => variant.Id == variantID && variant.Platform == "windows"
                && variant.Available && variant.Entries.Contains(entry)))
            throw new InvalidOperationException("当前游戏目录没有这个 Windows 启动入口。");

        var canonical = ValidateNetworkFolder(folder);
        var executable = RenpyAdapter.CheckedPath(canonical, entry.Path, directory: false);
        if (!StandaloneLibrary.ProbeExe(executable))
            throw new InvalidDataException("绑定资料夹中找不到有效的 Windows 游戏程序。");
        return canonical;
    }

    static string ValidateNetworkFolder(string folder)
    {
        if (!Path.IsPathFullyQualified(folder) || folder.Any(char.IsControl) ||
            folder.Contains('"') || folder.StartsWith(@"\\?\") || folder.StartsWith(@"\\.\"))
            throw new InvalidDataException("请选择已挂载 NAS 上的游戏资料夹。");
        var canonical = Path.GetFullPath(folder);
        if (OperatingSystem.IsWindows() && !canonical.StartsWith(@"\\") &&
            new DriveInfo(Path.GetPathRoot(canonical)!).DriveType != DriveType.Network)
            throw new InvalidDataException("请选择已挂载 NAS 上的游戏资料夹。");

        for (DirectoryInfo? parent = new(canonical); parent != null; parent = parent.Parent)
        {
            var attributes = File.GetAttributes(parent.FullName);
            if ((attributes & FileAttributes.Directory) == 0 || (attributes & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("NAS 游戏资料夹包含链接或不是资料夹。");
        }

        return canonical;
    }

    public async Task Run(LibraryGame game, LibraryVariant variant, LibraryEntry entry,
        AccountGate gate, string deviceID, Func<string, string, CancellationToken, Task<IGameProcess>> start,
        CancellationToken cancellation)
    {
        gate.Begin();
        IGameProcess? process = null;
        Stopwatch? clock = null;
        Exception? failure = null;
        try
        {
            var folder = ResolveFolder(variant);
            var canonical = await Task.Run(() => Validate(game, variant.Id, entry, folder), cancellation);
            cancellation.ThrowIfCancellationRequested();
            process = await start(canonical, entry.Path, cancellation);
            clock = Stopwatch.StartNew();
            await process.Wait(cancellation);
        }
        catch (Exception error) { failure = error; }
        finally
        {
            var stopped = process == null;
            if (process != null)
            {
                try { await process.Stop(); stopped = true; }
                catch (Exception error) { failure ??= error; }
                if (stopped)
                {
                    try { await process.DisposeAsync(); }
                    catch (Exception error) { failure ??= error; }
                }
            }
            if (stopped)
            {
                try
                {
                    if (clock != null)
                    {
                        clock.Stop();
                        var ended = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                        var remaining = clock.Elapsed.TotalSeconds;
                        while (remaining > 0)
                        {
                            var seconds = Math.Min(86400, remaining);
                            await account.Enqueue(game.Id, new(Guid.NewGuid(), deviceID, seconds, ended));
                            remaining -= seconds;
                        }
                    }
                }
                catch (Exception error) { failure ??= error; }
                finally { gate.End(); }
            }
        }
        if (failure != null) System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(failure).Throw();
    }
}
