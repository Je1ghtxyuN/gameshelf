# GameShelf

[简体中文](README_zh-CN.md) · [Latest release](https://github.com/Je1ghtxyuN/gameshelf/releases/latest) · [MIT license](LICENSE)

GameShelf is a game library and launcher for macOS and Windows. Organize games from local folders, external drives, or a NAS with covers, collections, favorites, and playtime. A shared library lets members see the same catalog with separate saves and playtime for each GameShelf account.

## Download

Open the [latest release](https://github.com/Je1ghtxyuN/gameshelf/releases/latest) and choose the client for your computer:

| Platform | Download |
|---|---|
| Mac with Apple Silicon, macOS 15 or later | Open the Mac DMG and drag GameShelf into Applications. |
| Windows x64 | Extract the Windows ZIP and run `GameShelf.Windows.exe`. |

The client is all you need for a local library or to join an existing GameShelf server. Only administrators setting up their own GameShelf server need the gateway source archive and the [server setup guide](Server/README.md).

## Get started

- **Local library.** Choose local use, register a folder, scan it, and add the games you want. Windows launches compatible Windows games directly. Mac uses the native entry where available and supports Windows games through Wine and engine-specific launch paths. Compatibility varies by game and edition.
- **Shared library.** Open the server invitation or enter the address provided by the administrator, then sign in. If the server uses system SMB, connect the account workspace through Finder or Windows File Explorer using your own NAS account. Game files are hosted on the NAS. The server handles library access, playtime, and account save isolation.
- **Saves across devices.** With a supported save layout, the same game edition can read account progress across devices. Separate Windows and Mac editions may use different save layouts.

## Build and deploy

- **Mac.** Install Apple Command Line Tools, Python 3.12+, Ruby, Node.js, and [Zig](https://ziglang.org/download/). Run `./build.sh`, `./test.sh`, and `./package-dmg.sh`. See [Mac update publishing](docs/UPDATES.md) for the release workflow.
- **Windows.** See the [Windows build guide](Windows/BUILD.md) for the C# and WinUI 3 client.
- **Gateway.** See the [NAS server setup guide](Server/README.md) for installation, invitations, accounts, and SMB configuration.

[Privacy](PRIVACY.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)
