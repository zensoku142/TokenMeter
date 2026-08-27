<p align="center">
  <a href="./README.md">简体中文</a> |
  <a href="./README.en.md">English</a> |
  <a href="./README.zh-TW.md">繁體中文</a> |
  <a href="./README.ja.md">日本語</a> |
  <a href="./README.ko.md">한국어</a>
</p>

# TokenMeter

<p align="center">
  <a href="https://github.com/zensoku142/TokenMeter/stargazers"><img alt="GitHub Stars" src="https://img.shields.io/github/stars/zensoku142/TokenMeter?style=flat-square"></a>
  <a href="https://github.com/zensoku142/TokenMeter/releases/latest"><img alt="Latest Release" src="https://img.shields.io/github/v/release/zensoku142/TokenMeter?style=flat-square"></a>
  <a href="https://github.com/zensoku142/TokenMeter/releases"><img alt="Release Downloads" src="https://img.shields.io/github/downloads/zensoku142/TokenMeter/total?style=flat-square"></a>
  <a href="https://github.com/zensoku142/TokenMeter/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/zensoku142/TokenMeter/ci.yml?branch=master&style=flat-square&label=CI"></a>
</p>

<p align="center">
  <a href="https://github.com/zensoku142/TokenMeter/releases/latest"><strong>下载最新版</strong></a> ·
  <a href="https://github.com/zensoku142/TokenMeter/stargazers">如果有帮助，请点 Star</a> ·
  <a href="https://github.com/zensoku142/TokenMeter/discussions">反馈与讨论</a>
</p>

<p align="center">
  <strong>Windows AI 编程订阅额度、Token 用量与余额监控工具</strong><br>
  <sub>Codex & Cursor Subscription Quota, DeepSeek, MiMo & NayutoAI Token Usage Monitor.</sub>
</p>

<p align="center">
  <img src="docs/images/readme-hero-v1.12.0.webp" alt="TokenMeter 产品界面概览" width="960">
</p>

TokenMeter 是一款轻量级 Windows 桌面 AI 用量监控工具。它显示 Codex 与 Cursor 订阅额度、剩余比例和重置倒计时，并为 DeepSeek、Xiaomi MiMo 与 NayutoAI 展示 Token、费用、余额和历史趋势。

## 功能

- 支持 Codex、Cursor、DeepSeek、Xiaomi MiMo 与 NayutoAI，平台缓存互不混用；默认只刷新当前数据源，可在设置中勾选需要同时后台获取的其他平台。
- 主面板通过与深浅主题一致的紧凑下拉菜单切换 Provider，当前平台有明确选中标记；订阅平台展示已用/剩余比例和重置倒计时，API 平台动态展示原生币种金额。
- 悬浮球和系统托盘常驻；Codex 以深浅主题水球显示周额度水位、剩余百分比和重置倒计时，DeepSeek/MiMo 保留金额视图；鼠标悬停在悬浮球上时可通过滚轮调整大小，贴边后仍保留清晰的唤出区域。
- 提供浅色、深色及跟随 Windows 的主题；浅色和深色主题可分别设置主色与 70%–100% 面板透明度，文字和控件保持清晰。
- Codex 按接口返回的窗口时长展示当前周额度与重置时间，并显示订阅套餐和到期日期，不在面板展示账户邮箱；右侧显示近 7 天 Token 使用量，同时保留年度活动、累计/峰值 Token、按单个任务计算的最长聊天和连续使用天数。
- Codex 额度按“刷新间隔”设置更新；底部使用统计、近 7 天图和活动热力图复用 1 小时缓存。底部统计与热力图始终采用官方数据，只有近 7 天图会在接口缺少当天记录时使用本机会话日志估算，次日同步后由官方数据替换。
- Codex 默认读取本机 CLI 目录；非默认位置通过只读目录选择器设置，并兼容旧版已保存的 `auth.json` 文件路径。
- DeepSeek 支持峰谷计价提示；MiMo Cookie 可通过专用 Chrome 会话获取和续期。
- 面板状态栏和悬浮提示会区分接口、缓存与近 7 天当天估算数据；断网、超时、限流及服务异常时按匿名账号指纹恢复最后成功的额度、统计和活动数据，完全退出后离线重启也不会清空。
- 历史数据缓存在本地 SQLite；自动更新前备份 `usage.db`，分时数据按设置天数的 2 倍保护期清理并记录清理明细。
- API Key、Bearer Token 和 Cookie 保存到 Windows 凭据管理器。
- 支持迁移应用数据目录、当前 Windows 用户开机自启、自动更新及单实例运行。

## 系统要求

- Windows 10 或 Windows 11；源码运行需要 Python 3.11+。
- 至少一个受支持平台账户；Codex 与 Cursor 可读取本机登录数据，DeepSeek、MiMo 与 NayutoAI 使用各自平台凭据。
- DeepSeek API Key 可选，用于官方余额接口。

> [!IMPORTANT]
> 订阅额度依赖本机 CLI OAuth 会话或产品额度接口，DeepSeek/MiMo 用量依赖平台控制台接口。平台接口、套餐或风控变化可能暂时影响数据。请仅使用自己的账户凭据并妥善保管。

## 安装

1. 从 [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases/latest) 下载 `TokenMeter-Setup-vX.Y.Z-x64.exe`，并按需核对 `SHA256SUMS.txt`。
2. 双击安装包并选择安装目录；默认目录为 `%LOCALAPPDATA%\Programs\TokenMeter`。
3. 安装完成后通过桌面或开始菜单中的 TokenMeter 快捷方式启动。

## 快速开始

```powershell
git clone https://github.com/zensoku142/TokenMeter.git
cd TokenMeter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade "pip>=26.1.2"
python -m pip install -r requirements.txt
python main.py
```

开发与发布依赖分别维护，避免运行环境安装测试或打包工具：

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
pyright
python -m pip install -r requirements-build.txt
```

## 首次配置

1. 启动程序并点击悬浮球展开面板。
2. 打开“设置”，选择 Codex、Cursor、DeepSeek、Xiaomi MiMo 或 NayutoAI。
3. Codex 默认读取本机 CLI 登录，仅在使用非默认位置时点击“选择…”指定 Codex 目录；DeepSeek 填写 API Key/控制台凭据；MiMo 可点击“一键获取 MiMo Cookie”。
4. 保存设置并刷新。默认刷新间隔为 60 秒。
5. 可在“设置 → 运行行为”调整主题主色、面板透明度、贴边隐藏、面板自动收起及开机自启。

`examples/config.example.py` 仅展示配置项；无需复制为 `config.py`。旧版 `config.py` 会在首次启动时尝试迁移。

## 本地数据与隐私

新安装默认把数据保存在 `安装目录\data`。从旧版 TokenSpider 升级时，程序会将 `%APPDATA%\TokenSpider` 复制到新目录，验证配置和 SQLite 数据库后再原子切换；旧目录不会被移动或删除。迁移失败时继续使用旧目录，不影响启动。

Windows 凭据管理器按 `TokenMeter/`、`TokenSpider/`、`TokenScope/` 顺序兼容读取，敏感凭据不会写入 `config.json` 或日志。也可在“设置 → 运行行为 → 应用数据目录”选择新的本地空目录；不支持网络共享路径。

## 自动更新

更新检查访问 `zensoku142/TokenMeter` 的 GitHub Releases。发现新版本后，程序只下载 `TokenMeter-Setup-vX.Y.Z-x64.exe` 和 `SHA256SUMS.txt`，校验 SHA256 后静默覆盖原安装目录。安装包使用固定 AppId，并保留 `data` 与现有快捷方式；失败时原版本文件仍可从原快捷方式启动。

SHA256 用于确认下载文件的完整性，不等同于由独立密钥提供的发布签名；后续可在不降低现有校验的前提下增加 Authenticode 或 Ed25519 验证。

## 卸载

默认卸载只删除程序文件和快捷方式，保留安装目录中的 `data`。如需清理配置、历史记录或浏览器会话，请在确认不再需要后手动删除该目录。

## 测试

```powershell
python -m pytest -q
```

Qt 测试建议在可用的 Windows 桌面会话中运行。

## 构建

```powershell
python -m pip install -r requirements-build.txt
.\.venv\Scripts\pyinstaller.exe --clean --noconfirm packaging\pyinstaller\TokenMeter.spec
python scripts/build_release.py
```

发布脚本生成 `dist\TokenMeter\` onedir 目录；安装 Inno Setup 后还会生成 `dist-installer\TokenMeter-Setup-vX.Y.Z-x64.exe` 和对应的 `SHA256SUMS.txt`。已验证发布环境为 Python 3.12、PyInstaller 6.21、PySide6 6.11；UPX 可选。

## 项目结构

```text
TokenMeter/
├── api/                 # 平台 API、Provider 与计价规则
├── config/              # 配置、凭据、迁移与运行时状态
├── core/                # 应用身份等基础元数据
├── data/                # 数据目录、聚合与 SQLite 历史缓存
├── updater/             # 更新客户端与独立更新器
├── ui/                  # PySide6 界面
├── packaging/           # PyInstaller、安装器与 Windows 版本资源
├── scripts/             # 构建与发布脚本
├── docs/                # 项目结构说明与 README 图片
├── examples/            # 示例配置
├── release-notes/       # 版本发布说明
├── tests/               # 单元与 Qt 测试
└── main.py              # 应用入口
```

完整说明见 [项目目录结构](docs/PROJECT_STRUCTURE.md)。

## 故障排查

- 未配置：在设置中选择平台并填写凭据。
- 凭据失效：重新获取 Cookie；MiMo 会先尝试复用专用浏览器会话。
- 请求频繁或风控：等待后再刷新，不要持续缩短间隔。
- 数据未更新：查看当前数据目录中的 `TokenSpider.log`；新安装通常位于 `安装目录\data`。
- 未出现窗口：检查系统托盘；程序只允许一个实例。

## 版本与 Release

当前版本：`1.13.2`。更新记录及校验文件见 [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases)。

## License

本项目采用 [MIT License](LICENSE)，可在保留版权和许可声明的前提下使用、修改和分发。
