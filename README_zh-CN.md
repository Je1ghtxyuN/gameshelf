<p align="center">
  <img src="https://count.getloli.com/@gameshelf-for-mac?theme=moebooru&amp;padding=7&amp;darkmode=auto" alt="GameShelf visitors" width="460" />
</p>

<h1 align="center">GameShelf</h1>

<p align="center">面向 macOS 和 Windows 的游戏库与启动器。</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="MIT License" /></a>
  <a href="https://github.com/Je1ghtxyuN/gameshelf/releases/latest"><img src="https://img.shields.io/github/v/release/Je1ghtxyuN/gameshelf?style=flat-square" alt="Latest release" /></a>
  <img src="https://img.shields.io/badge/macOS-15%2B-black?style=flat-square&amp;logo=apple" alt="macOS 15+" />
  <img src="https://img.shields.io/badge/Apple_Silicon-arm64-7C3AED?style=flat-square" alt="Apple Silicon" />
  <img src="https://img.shields.io/badge/Swift-F05138?style=flat-square&amp;logo=swift&amp;logoColor=white" alt="Swift" />
</p>

<p align="center"><a href="README.md">English</a> · <strong>简体中文</strong></p>

GameShelf 是一款面向 macOS 和 Windows 的游戏库与启动器。扫描电脑、外置盘或已挂载 NAS 中的目录，选择要添加的游戏，也可登录带账号管理的共享游戏库。

## 下载

**[下载最新版本](https://github.com/Je1ghtxyuN/gameshelf/releases/latest)**

- **Mac（macOS 15+，Apple 芯片）**：打开 DMG，将 **GameShelf.app** 拖到 **Applications（应用程序）** 快捷方式上。
- **Windows x64**：解压 Windows ZIP，运行 `GameShelf.Windows.exe`。

本地使用或连接别人已搭好的 GameShelf 服务器，只需下载对应的客户端。只有自行部署服务器的管理员需要下载 NAS 网关源码包，部署方法见[服务端说明](Server/README.md)。

Mac 上安装 v1.3.0 或更新版本后，可通过 **GameShelf → 检查更新…** 或定期更新提醒升级。安装前验证签名，游戏运行期间等待游戏退出。旧版客户端需先手动升级一次。维护者可参考[更新发布流程](docs/UPDATES.md)。

## 功能

- **统一游戏库**：通过封面和自定义分类，整理 Windows、原生 Mac、视觉小说和 2D 游戏。
- **扫描后选择添加**：设置游戏库目录，按完整游戏发现候选项；自行选择版本，语言启动器归在一起，设置工具不作为游戏卡片。
- **可选的账号共享库**：登录自建服务，浏览获准访问的游戏。资源留在服务器，每个账号使用独立可写空间，Wine 运行环境保存在本机。
- **原位置导入**：游戏可保留在本地磁盘、外置盘或已挂载的 NAS，也可以下载一份本地副本供离线游玩。
- **视觉小说导入**：识别 QLIE、CatSystem2、吉里吉里和 NScripter 特征，过滤设置工具，并优先显示汉化启动入口。
- **绑定启动入口**：存在多个可执行文件时，可选择平时使用的汉化启动器，之后直接启动。
- **多种运行方式**：Windows 客户端直接运行 Windows 游戏。Mac 支持原生游戏、Wine/Wineskin、Ren’Py 引擎适配和 HTML 游戏的 WebKit 内嵌运行。
- **游玩时长**：封面下显示累计时长，在 **属性 → 游玩记录** 中查看最近会话。
- **个性化整理**：支持改名、收藏、分类、本地封面和在线封面搜索。
- **存档管理**：提供按引擎适配的存档迁移、备份，以及重新连接游戏目录后的稳定条目标识。

## 开始使用

首次启动时连接服务器并登录，也可选择无需账号的 **本地使用**。记住登录后直接进入游戏库。服务器、账号、密码和游戏库目录在 **设置** 中管理。

1. 设置游戏库目录，点击 **扫描游戏**，勾选要添加的游戏和启动入口。扫描不会将所有可执行程序自动加入游戏库。
2. 连接服务器时，扫描结果由服务器保存。其他设备直接读取同一批候选和账号游戏库，无需重复扫描 NAS。
3. 保留现有封面网格、分类、收藏、搜索和 **⋯ → 属性**。同一游戏可包含多个平台版本：macOS 优先原生版本，Windows 使用 Windows 可执行程序。
4. 管理员在 **设置** 中管理账号和游戏访问。成员可浏览同一个共享游戏库，游玩时长和受支持的存档按账号分别保存。默认名称与封面属于已发布游戏。

资源保留在服务器。GameShelf 打开账号专属网络视图，同一逻辑游戏在多设备间只允许一个写入会话。服务器启用系统 SMB 后，先完成账号验证。Mac 使用系统的 NAS 登录在后台连接存档工作区，Windows 按客户端提示在文件资源管理器连接账号存档共享。受支持的同一游戏版本可以跨设备读取进度，分别发行的 Mac 版和 Windows 版可能使用独立存档。

本地使用保留独立游戏库和游戏原有的存档位置。连接服务器不会自动上传或绑定已有本地游戏与存档。原有资料迁移保留源文件，核对实际目录和入口，并等待存档迁移回执提交完成后再关联。

客户端不保存密码。**记住登录** 在 macOS 的私有应用文件或 Windows 凭据库中保留会话令牌；退出登录会清除该会话。

部署与引擎接入见[服务端说明](Server/README.md)和[统一游戏库协议](Contracts/unified-library-v2.json)。

运行 `./test-account-integration.sh` 可验证 AppKit 账号、扫描、登记与同步流程，夹具资料只写入临时目录。

## Windows 客户端

Windows 客户端采用 **C# / .NET 10 与 WinUI 3**。登录和游戏库保持相同布局，窗口、字体、导航与控件采用 Windows 原生样式，Windows 游戏直接运行，无需 Wine。

构建与验收说明见 [Windows/BUILD.md](Windows/BUILD.md)。发布页同时提供 Windows 客户端和 Mac DMG。

时长在 GameShelf 运行期间记录，包含游戏切到后台的时间，电脑睡眠时暂停；从启用此功能后开始累计。

已确认的名称别名和封面会保存在本机，后续添加同一游戏时复用。发布匹配的游戏时会附带默认名称与封面，供获授权玩家使用；个人更换的封面单独保留。

## 从源码构建

安装 Apple Command Line Tools、Python 3.12+、Ruby、Node.js 20+ 和 [Zig](https://ziglang.org/download/)。将 `zig` 加入 PATH，或用 `ZIG` 指定其可执行文件绝对路径，然后执行：

```sh
./build.sh
./test.sh
./package-dmg.sh
```

构建产物为 `dist/GameShelf.app`，使用本地临时签名。可通过 `ARCH=arm64 ./build.sh` 指定 Apple Silicon；开发构建也提供 `ARCH=x86_64` 选项。测试使用自动生成的样本和临时目录。

独立的 Ren’Py 存档迁移工具：

```sh
python3 Tools/migrate_renpy_saves.py --help
```

项目维护者：[Je1ghtxyuN](https://github.com/Je1ghtxyuN)。

## 许可与致谢

[MIT 许可证](LICENSE) · [第三方说明](THIRD_PARTY_NOTICES.md) · [隐私说明](PRIVACY.md)

访问计数器来自 [Moe Counter](https://github.com/journey-ad/Moe-Counter)，徽章由 [Shields.io](https://shields.io) 提供。

共享流程使用同一个「游戏扫描」窗口：选中缓存结果后点击「共享所选」，服务器仅核对所选目录。首次共享确认本机目录与服务器目录的对应位置，之后复用。共享游戏库点击「游玩」即记住游戏并启动，无需再次批量添加。账号管理里勾选各账号可玩的游戏，不再提供单独的授权页面。账号可修改自己的密码，管理员可重置其他账号的密码；改密前须退出该账号的共享游戏，改密后重新登录，账号、游戏资料及存档保留。
