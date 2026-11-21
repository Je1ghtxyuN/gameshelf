# GameShelf Windows

C#/.NET 10 LTS, WinUI 3, x64. Native system title bar and Fluent controls. Windows 10 1809+ is the UI minimum; SMB ports other than 445 require Windows 11 24H2+ and a `New-SmbMapping` implementation with `TcpPort`.

## Build and test

From `Windows/`, use .NET SDK 10.0.103 (see global.json):

```
dotnet run --project GameShelf.Core.Tests
dotnet run --project GameShelf.Native.Tests
dotnet restore GameShelf.Windows --locked-mode -p:Platform=x64
dotnet build GameShelf.Windows -c Release -p:Platform=x64 --no-restore
dotnet publish GameShelf.Windows -c Release -p:Platform=x64 --no-restore -o .build/publish
```

Core tests are a dependency-free executable returning nonzero on failed assertions; use `dotnet run`, not `dotnet test`. Native tests run only on Windows and exercise suspended launch, child lifetime and job termination using harmless built-in commands. macOS can validate Core but cannot compile XAML.

Windows App SDK is locked to stable `1.8.260921001`, including transitive hashes in packages.lock.json. The NuGet stable feed was queried during implementation; 1.8 is deliberately pinned to the serviced stable line, not floating to another major release. Official package: https://www.nuget.org/packages/Microsoft.WindowsAppSDK/1.8.260921001 . Official release notes: https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/release-notes/windows-app-sdk-1-8 . This is an unpackaged, self-contained x64 app: no administrator runtime installation or machine-wide .NET replacement is needed.

Build host uses SDK 10.0.103 with the .NET 10.0.14 servicing runtime in the same user tool directory. Intermittent native CLR crashes were observed inside compiler/MSBuild tasks on this host, including one after updating the servicing runtime; successful builds and the failed runs are recorded separately in private QA. No root cause or universal host fix is claimed.

`Tools/bootstrap.ps1` installs the SDK and servicing runtime only into `D:/GameShelfDev/dotnet` by default. Set `GAMESHELF_BUILD_ROOT` to override the absolute workspace path. The build scripts also place temporary files and NuGet caches under this root. `Tools/sync-build.py`, run from the repository root on Mac, copies only Windows sources into that user's development directory and invokes `Tools/remote-build.ps1` over the preconfigured `win` SSH alias. No NAS credentials are copied. It retrieves the project lock files. Logs and screenshots remain under ignored `Windows/build/`; the source archive excludes all build/QA output. Build output remains in that directory. Never install VS workloads, Windows SDK installers, services, or global runtimes without separate authorization.

## Connection and isolation

Login first asks only for the server address, then inspects the existing `/api/server-info` endpoint and presents username/password. The left blue artwork and application icon follow the Mac layout. OS-trusted HTTPS discovers and pins the certificate automatically; an untrusted or changed certificate requires explicit first-use fingerprint comparison in a trust dialog. No ordinary user invitation, manual server ID, SHA entry or HTTP checkbox is required. Configured LAN/loopback HTTP addresses are supported directly; public HTTP addresses require HTTPS. Redirects are disabled. Passwords are never saved; the optional Keep signed in setting restores sessions through Windows Credential Vault. Failed restoration clears the username. Connection/account controls live in Settings.

Per-account snapshots/outbox live under `%LOCALAPPDATA%/GameShelf/<serverID>-<userID>`. A valid authenticated library response is required before showing data. Switching accounts while a game/session is active is blocked. `--data-dir=<absolute path inside the current user profile>` selects a separate application configuration/cache and credential namespace for isolated acceptance runs; it never rewrites OS AppData or game environment variables.

Candidates load from server scan results immediately using `/api/library/candidates`, with account-scoped All/Added/Ignored state and persistent ignore/restore actions. Scanning and publishing/granting are separate explicit actions in one dialog; Add uses `/api/admin/library/add` once and grants only the current account. No client-side NAS walk, source reorganization, download, or second publish/import is performed.

Logical cards choose available Windows `.exe` entries only. Historical wire backend `wine` is accepted as a Windows executable, never passed to Wine. Helpers/settings/installers and unsafe paths are rejected. Metadata uses server CAS revisions; time uses bounded finite events with persistent stable UUIDs and retry-safe account outboxes. Existing personal snapshots are never imported. No cross-variant save sharing is advertised.

Connected native launch uses a matching account and edition lease, an account-only SMB overlay, and a Windows job that stops all writers before lease cleanup. Administrators on a supporting gateway can launch a published Windows executable through that overlay without a game-specific fingerprint. The overlay isolates relative writes and masks known seed-save paths. AppData and registry storage require separate runtime support. Ordinary shared accounts still require a reviewed save profile. See `Contracts/Windows-launch-support.md` for reviewed adapter details.

Administrators who already open the NAS in File Explorer can select the corresponding mounted directory once in **Settings → Folders**. Game cards under that server root then launch their Windows executable through the existing Windows connection and continue recording account playtime. Root bindings are per device and account. Game-managed saves use their usual locations on this route. Without a root binding, the client uses the existing account-lease launch path.

The window build and synthetic process tests do not certify isolation of a real game's saves. Do not remove this gate just because a game launches successfully.

Account Windows entry preferences are modeled and selected independently of macOS/web preferences. Properties saves `/api/library/games/{id}/launch-preferences`, with automatic mode resetting only Windows. Scan entry selection submits `entryPaths` in the single add transaction. Removing a card or collection uses only the authenticated own-account DELETE routes; it never unpublishes an edition or deletes source/save files.

Core regression coverage also includes stopped-writer cleanup after a real outbox I/O failure, end-to-end response deadlines, streaming discovery limits, duplicate `(edition ID, platform)` rejection, mapping operation acknowledgements and explicit Windows preference selection. Live SMB 1445/445 co-existence is still an integration acceptance item; synthetic view/process tests do not claim to validate the Windows redirector against a real NAS.


## Standalone use

The first connection page offers 本地使用. This uses the same library/sidebar/covers/properties and a separate `LocalLibrary` profile; the last local choice is remembered. Configure a local folder in Settings, explicitly Scan, then select cached candidates and their Windows entry before Add. Scan probes only bounded DOS/PE headers, excludes setup/custom/config/install/update helpers, groups multiple language EXEs in one directory as entry choices, skips reparse links and limits depth, directories and files. It does not execute files, download dependencies, move sources or automatically add every candidate.

Standalone launching uses the existing Windows Job process monitor and records time only in LocalLibrary. These are ordinary Windows launches with their usual original save locations; **no account save isolation is claimed for standalone mode**. Connecting a server returns to the neutral address/login flow and never adopts, migrates or uploads the local catalog, metadata, time or saves. Switching while a local game is running is blocked.

Build the fixture client with `dotnet build GameShelf.Windows -c UIAcceptance -p:Platform=x64` from the configured Windows build workspace. Both UI fixture scripts use this separate output and reserve `ui-local-data` and `ui-fixture-data` under the workspace for synthetic profiles. The Release publisher uses the separate Release output.

`Tools/local-ui-acceptance.ps1` tests the real local choice, cached selection, harmless fixture EXE launch/save/time and neutral server return in its own development-only profile. It does not open a real NAS/game. The OS folder picker uses an HWND-bound WinUI/Windows native picker; live user folder contents are never used by the acceptance script.


## Reviewed Ren'Py namespace adapter

Current server contract is documented in `Contracts/Windows-launch-support.md`. The client explicitly advertises `renpy-savedir-v1`, validates the six public namespace fields plus mandatory advertised compatibility key/digest and matching review evidence, and anchors critical fingerprint roles to the real known Windows module paths. It verifies exported files and the pinned patched savelocation module before starting. It supplies only fixed `--savedir`, `RENPY_PATH_TO_SAVES`, `RENPY_MULTIPERSISTENT`, and `PYTHONDONTWRITEBYTECODE` to the child; parent/OS AppData is never replaced. Arbitrary server arguments/environment maps and unknown profiles/layouts are rejected. No real-game/NAS certification follows from the synthetic native tests.

The MIT Ren'Py 8.3.7 savelocation test fixture under Core.Tests/Fixtures is copied from the shared contract fixture with its original full notice retained; `.gitattributes` preserves its pinned bytes on Windows checkout. The client does not patch source games; the server verifies the exported reviewed patch.

After successful real Windows tests/build, `Tools/publish-candidate.ps1` self-contained-publishes and overwrites exactly one private `D:/GameShelfDev/artifacts/GameShelf-Windows-x64.zip`, excluding symbols/logs/private profiles and emitting its SHA256. This is a private review candidate, not a GitHub release or production install. The final frozen artifact path/checksum and true validation limits are recorded in `work/implementation/windows-report.md`.


Release delivery privacy: Directory.Build.props disables Release debug symbols/PDB emission and maps build source roots to a neutral path. The publisher must run `Tools/audit-candidate.ps1` successfully before emitting a candidate digest. This is a full byte-content audit of every ZIP file in UTF-8/UTF-16LE/UTF-16BE, including builder-home/username markers; omitting PDB filenames alone is insufficient. A failed audit deletes the candidate. The private audit JSON is kept beside the remote ZIP, never included in the archive.


SMB diagnostics retain only a fixed `SmbOperationStage` and an optional numeric Win32 code extracted from the PowerShell ErrorRecord FQID `Windows System Error N`. Raw stderr, UNC, credentials and inner exceptions are not retained. Missing/null/zero mapping port metadata fails closed, including requested445; it is never inferred from UNC. Cleanup still requires an operation receipt plus matching current share/port/credential identity.

`GameShelf.Native.Tests -- --smb-powershell-only` runs offline real-PowerShell tests using actual generated production scripts with in-memory cmdlet fixtures and actual compiled Check comparison. Qualified synthetic credentials are constructed with `[char]92`, assert exactly one separator and exclude equal-credential/username OR short circuits. These tests create no SMB mapping and do not establish live NAS acceptance.
