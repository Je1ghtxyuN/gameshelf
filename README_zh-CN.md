# GameShelf

[English](README.md) · [下载最新版](https://github.com/Je1ghtxyuN/gameshelf/releases/latest) · [MIT 许可](LICENSE)

GameShelf 是面向 macOS 和 Windows 的游戏库与启动器。你可以扫描本机、外接硬盘或 NAS 上的游戏，用封面、分类、收藏和游玩时长整理游戏库。共享库成员可以看到同一套游戏目录，存档和时长按 GameShelf 账号分别保存。

## 下载哪个文件？

| 你的设备 | 下载 |
|---|---|
| Apple 芯片 Mac，macOS 15 或更新版本 | 下载 Mac DMG，打开后将 GameShelf 拖入「应用程序」。 |
| Windows x64 | 下载 Windows ZIP，解压后运行 `GameShelf.Windows.exe`。 |

只用本地游戏库，或连接别人已经搭好的 GameShelf 服务器，都只需要客户端。只有自行部署 GameShelf 服务的管理员需要下载 NAS 网关源码包，并参考 [服务端部署说明](Server/README.md)。

## 开始使用

- **本地游戏库。** 选择本地使用，添加游戏所在文件夹，扫描后勾选要加入的游戏。Windows 直接运行兼容的 Windows 游戏。Mac 优先使用原生入口，也可用 Wine 或受支持的引擎运行游戏。兼容性取决于游戏及版本。
- **NAS 共享库。** 用管理员提供的邀请或服务器地址登录。若服务器启用了系统 SMB，按提示用自己的 NAS 账号在 Finder 或文件资源管理器连接存档工作区。游戏文件放在 NAS，由服务端管理账号访问、时长和存档隔离。
- **跨设备存档。** 同一发行版使用受支持的存档方式时，可以跨设备读取账号进度。分别发行的 Windows 和 Mac 版可能采用不同的存档格式或位置。

## 构建和部署

- **Mac。** 安装 Apple 命令行工具、Python 3.12+、Ruby、Node.js 和 [Zig](https://ziglang.org/download/)，运行 `./build.sh`、`./test.sh` 和 `./package-dmg.sh`。发布流程见 [Mac 更新说明](docs/UPDATES.md)。
- **Windows。** C#／WinUI 3 客户端的构建方法见 [Windows 构建说明](Windows/BUILD.md)。
- **NAS 网关。** 安装、邀请、账号和 SMB 配置见 [服务端部署说明](Server/README.md)。

[隐私说明](PRIVACY.md) · [第三方组件](THIRD_PARTY_NOTICES.md)
