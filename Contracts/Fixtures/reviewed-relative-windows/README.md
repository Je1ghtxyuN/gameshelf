# Synthetic relative-only Windows fixture

This is an auditable test asset, not a real game or a general RenPy profile. It does not run on macOS. Build with the Python standard library only; no compiler, SDK, runtime restore or download is used:

```sh
python3 Contracts/Fixtures/reviewed-relative-windows/build_fixture.py /absolute/temporary/account-view/Fixture.exe
```

The output is a deterministic 1536-byte x64 PE with SHA256 `23da1a8de2ea9c3aaf4b484b39357c4f41e8c5255f3bddcf5f45610b9003942b`. The generator itself is pinned as `8e66e82ca032adf2b2f1cf6079246534bcf2a9e397e9d0e5f5448b19db90b86a` in server review evidence.

Reviewed behavior: creates `savedata`, opens `savedata/progress.txt` with CREATE_ALWAYS, writes a fixed synthetic message, closes the handle, sleeps 2100 ms and exits. The only PE imports are CreateDirectoryW, CreateFileW, WriteFile, CloseHandle, Sleep and ExitProcess from KERNEL32.dll. No command parsing, external helpers, config or managed runtime loading, environment/AppData lookup, registry or network calls. Imported OS functionality still requires a real supported Windows host; executable identification on Mac is not execution validation.

If running it for native acceptance, use only a fresh temporary fixture view, explicitly set working directory to that view, and preserve the server's account/lease/entry checks. It overwrites only that view-relative synthetic progress file. Never run against a real game's save directory or NAS source. Compare separate account views and file metadata/content to verify one account's run does not change the other's view. Current server proof tests do not claim real Windows execution or real SMB/OverlayFS acceptance.

Serve a read-only synthetic source with `Fixture.exe` and the `savedata` relative directory, scan/publish it, explicitly grant the test account and opt in to `gameshelf.synthetic-relative-windows.v1` through the admin launch-profile endpoint. Full stable wire fields and boundaries are in `../../reviewed-launch-profiles.md`. The generated program and this profile are separate from Franklin's existing .NET LaunchFixture; its compiled runtime/dependency files are not certified by this PE hash.
