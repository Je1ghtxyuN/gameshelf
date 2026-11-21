<p align="center">
  <img src="https://count.getloli.com/@gameshelf-for-mac?theme=moebooru&amp;padding=7&amp;darkmode=auto" alt="GameShelf visitors" width="460" />
</p>

<h1 align="center">GameShelf</h1>

<p align="center">A game library and launcher for macOS and Windows.</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="MIT License" /></a>
  <a href="https://github.com/Je1ghtxyuN/gameshelf/releases/latest"><img src="https://img.shields.io/github/v/release/Je1ghtxyuN/gameshelf?style=flat-square" alt="Latest release" /></a>
  <img src="https://img.shields.io/badge/macOS-15%2B-black?style=flat-square&amp;logo=apple" alt="macOS 15+" />
  <img src="https://img.shields.io/badge/Apple_Silicon-arm64-7C3AED?style=flat-square" alt="Apple Silicon" />
  <img src="https://img.shields.io/badge/Swift-F05138?style=flat-square&amp;logo=swift&amp;logoColor=white" alt="Swift" />
</p>

<p align="center"><strong>English</strong> · <a href="README_zh-CN.md">简体中文</a></p>

GameShelf brings your games into a shared library experience on macOS and Windows. Discover games in folders on your computer, an external drive or a mounted NAS, or connect to an account-based shared library.

## Download

**[Download the latest release](https://github.com/Je1ghtxyuN/gameshelf/releases/latest)**

- **Mac (macOS 15+, Apple Silicon)** — open the DMG and drag **GameShelf.app** onto the **Applications** shortcut.
- **Windows x64** — extract the Windows ZIP and run `GameShelf.Windows.exe`.

The client is all you need for local play or to join an existing GameShelf server. Only administrators deploying their own server need the NAS gateway source package. See [server setup](Server/README.md).

On Mac, after installing v1.3.0 or later, use **GameShelf → Check for Updates…** or receive periodic update prompts. Updates are verified before installation and wait for active games to exit. Older clients need one manual upgrade. See [update publishing](docs/UPDATES.md) for maintainers.

## Features

- **One library** — organize Windows, native macOS, visual novel, and 2D games with covers and custom collections.
- **Scan, then choose** — register library folders, discover complete games and add only the editions you select. Keep language launchers together and settings utilities out of the game list.
- **Optional shared libraries** — sign in to a self-hosted gateway and add authorized games. Resources stay on the server, with account-private writable views and local Wine runtimes.
- **Import in place** — keep game files on a local disk, an external drive, or a mounted NAS. Download a local copy when you want to play offline.
- **Visual novel imports** — identify QLIE, CatSystem2, KiriKiri and NScripter markers, keep configuration utilities out of the launcher list, and put translated launchers first.
- **Choose your entry point** — select the translated launcher when a game has several executables; GameShelf remembers the choice.
- **Flexible launch paths** — launch Windows games directly on Windows. On Mac, use Wine/Wineskin, native macOS apps, Ren’Py engine adapters, or embedded WebKit for HTML games.
- **Playtime at a glance** — see total playtime below each cover, with recent sessions in **Properties → Play history**.
- **Make it yours** — rename library entries, add favorites, manage collections, and choose a local cover or search online.
- **Save management** — use engine-specific save migration, backups, and persistent game identities across folder reconnections.

## Getting started

On first launch, connect to your server and sign in, or choose **Local use** without an account. A remembered session opens the library directly. Server, account, password, and library-folder controls live in **Settings**.

1. Configure a library folder and click **Scan games**. Choose the games and launch entries to add; scanning never adds every executable automatically.
2. In connected mode, scan results stay on the server. Other devices load the same candidates and account library without rescanning the NAS.
3. Use the existing cover grid, collections, favorites, search, and **⋯ → Properties** to organize and launch games. A logical game can contain several platform editions: macOS selects its native edition first; Windows selects the Windows executable.
4. An administrator manages accounts and access in **Settings**. Members see the same shared catalog. Each account has its own playtime and supported save profiles. Default names and covers belong to published games.

Connected resources stay on the server. GameShelf opens an account-private network view and holds one writer session for a logical game across devices. When system SMB is enabled, connect your account workspace in Finder or Windows File Explorer. Supported editions can share account progress across devices. Different editions may keep independent saves.

Local use keeps a separate library and the game's ordinary save locations. Connecting a server does not upload or bind existing local games or saves automatically. Owner migration preserves the source and requires exact directory/entry mapping and a committed save-migration receipt.

Passwords are never stored. **Keep signed in** remembers a session token in a private application file on macOS and Windows Credential Vault on Windows. Signing out removes the remembered session.

See [server setup](Server/README.md) and the [unified library contract](Contracts/unified-library-v2.json) for deployment and engine integration.

Run `./test-account-integration.sh` for the AppKit account, scan, registration, and sync acceptance fixtures. All fixture data stays in temporary directories.

## Windows client

The Windows client uses **C# / .NET 10 and WinUI 3**. It follows the same login and library layout with native Windows window chrome, typography, navigation, and controls. Windows executables run natively, without Wine.

Build instructions and acceptance coverage are in [Windows/BUILD.md](Windows/BUILD.md). The Windows installer is available alongside the Mac DMG.

Playtime is recorded while GameShelf is open, including background game time, and pauses when the computer sleeps. Recording starts when this feature is enabled.

Confirmed title aliases and cover art are cached locally and reused on later imports. Publishing a matched game includes its default name and cover for authorized players; personal artwork remains separate.

## Build from source

Install Apple Command Line Tools, Python 3.12+, Ruby, Node.js 20+, and [Zig](https://ziglang.org/download/). Put `zig` on PATH or set `ZIG` to its absolute executable path, then run:

```sh
./build.sh
./test.sh
./package-dmg.sh
```

The app is built at `dist/GameShelf.app` with a local ad-hoc signature. `ARCH=arm64 ./build.sh` explicitly selects Apple Silicon; `ARCH=x86_64` is also available for development builds. Tests use generated fixtures and temporary directories.

For the standalone Ren’Py save migration tool:

```sh
python3 Tools/migrate_renpy_saves.py --help
```

Maintained by [Je1ghtxyuN](https://github.com/Je1ghtxyuN).

## License & credits

[MIT](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md) · [Privacy](PRIVACY.md)

Visitor counter by [Moe Counter](https://github.com/journey-ad/Moe-Counter). Badges by [Shields.io](https://shields.io).
