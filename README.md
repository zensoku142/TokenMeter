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
  <a href="docs/images/readme-hero.webp"><img src="docs/images/readme-hero.webp" alt="TokenMeter 当前源码真实界面：Codex 订阅额度与年度活动、DeepSeek 余额与今日分时消耗，浅色、深色和自定义主题面板，额度与金额悬浮球，以及 VPet 桌宠和额度气泡（演示数据）" width="960"></a>
</p>

上图由当前源码运行的真实组件截图排版，额度与用量为演示数据；桌宠为可选扩展。[查看原始截图与来源说明](docs/images/readme/README.md)。

TokenMeter 是一款轻量级 Windows 桌面 AI 用量监控工具。它显示 Codex 与 Cursor 订阅额度、剩余比例和重置倒计时，并为 DeepSeek、Xiaomi MiMo 与 NayutoAI 展示 Token、费用、余额和历史趋势。

当前体验版：[v1.14.0-beta.1](https://github.com/zensoku142/TokenMeter/releases/tag/v1.14.0-beta.1)，配套桌宠扩展为 [pet-v0.1.0-beta.1](https://github.com/zensoku142/TokenMeter/releases/tag/pet-v0.1.0-beta.1)。两者均为预发布版，稳定版下载入口保持不变。

## 功能

- 支持 Codex、Cursor、DeepSeek、Xiaomi MiMo 与 NayutoAI，平台缓存互不混用；默认只刷新当前数据源，可在设置中勾选需要同时后台获取的其他平台。
- 主面板通过与深浅主题一致的紧凑下拉菜单切换 Provider，当前平台有明确选中标记；订阅平台展示已用/剩余比例和重置倒计时，API 平台动态展示原生币种金额。
- 悬浮球和系统托盘常驻；Codex 以深浅主题水球显示周额度水位、剩余百分比和重置倒计时，DeepSeek/MiMo 保留金额视图；鼠标悬停在悬浮球上时可通过滚轮调整大小，贴边后仍保留清晰的唤出区域。
- 提供浅色、深色及跟随 Windows 的主题；默认在深浅模式间同步主色，取消“深浅模式使用相同主题色”选项后可分别设置。两种模式的 70%–100% 面板透明度始终独立，文字和控件保持清晰。
- 界面默认跟随电脑语言，支持简体中文、繁體中文、English、日本語、한국어；在“设置 → 外观 → Language / 语言”即时切换并记住选择，未支持的系统语言回退到英文。
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
4. 修改后自动保存，顶部显示保存状态；默认刷新间隔为 60 秒。
5. 设置在原面板内打开，可从标题栏“返回面板”。主题与透明度位于“外观”，贴边隐藏、自动收起与开机自启位于“悬浮与启动”，刷新和分时选项位于“采集与统计”。

`examples/config.example.py` 仅展示配置项；无需复制为 `config.py`。旧版 `config.py` 会在首次启动时尝试迁移。

## VPet 精简桌宠（可选扩展）

桌宠源码来源：[LorisYounger/VPet](https://github.com/LorisYounger/VPet)。TokenMeter 在其开源核心上增加用量展示与独立扩展管理；“设置 → 桌宠”页也提供此来源链接。源码、默认角色和动画的授权说明见 [来源与授权](pet_host/THIRD_PARTY_NOTICES.md)。

主安装包不包含桌宠资源或 .NET 运行时，默认保持原有悬浮球和面板。有需要时在独立的“设置 → 桌宠”页点击“下载桌宠扩展包”，下载安装并校验完整后才能打开“启用 VPet 精简桌宠”；未安装或安装不完整时开关禁用，本地开发构建不会被视为已安装扩展。该页常驻显示桌宠版本或安装状态，下载、卸载和检查更新按钮在同一行。扩展使用 [VPet](https://github.com/LorisYounger/VPet) 原版默认角色与核心互动，加载成功后替代悬浮球，关闭后恢复原球体，不影响已有账户、面板和用量采集。

扩展从独立的 `pet-v版本号` GitHub Release 下载，经 SHA256 和通信协议校验后安装到当前应用数据目录的 `extensions/vpet/`，不需要另外安装 .NET。桌宠有自己的版本号；设置页显示已安装版本，点击“检查桌宠更新”后可单独更新到与当前主程序兼容的最新版本：正式版仅选择稳定包，体验版也可选择预发布包，无需重装面板或主题。不安装桌宠时不会自动下载扩展。首次体验请先安装 `v1.14.0-beta.1` 或更新的兼容主程序，`v1.13.2` 尚无扩展管理入口。

更新期间只暂停桌宠，主程序和原面板继续运行；新包完整下载、校验、解压后才替换旧包，取消或替换失败保留旧包并按原开关状态恢复桌宠。可随时点击“卸载桌宠扩展包”：先关闭桌宠、恢复悬浮球，再删除宿主、资源和动画缓存，保留账户、用量数据、主题与 `vpet/` 中的偏好。尚未发布兼容扩展时会显示提示，不改变原有面板。

- 轻触头部/身体互动，按住移动即可拖动，长按也可提起；无底部工具栏。原生右键菜单提供用量查看、额度气泡开关、缩放、设置和退出。保留自主走动、爬边和待机小动作，可在右键菜单暂停自主活动。
- 不包含投喂、睡觉、学习、打游戏、工作、金币、经验、等级、心情或体力等养成玩法；相关计算、属性面板和无用动画资源均不启用。
- 自主活动包含完整待机资源：自娱自乐、侧身互动、无聊、吹泡泡、比心、喵喵、探看、下蹲、网球、哈欠，以及坐下/躺下序列。每轮随机遍历动作与状态版本，补齐只有循环段的动作入口；不改变养成数值。贴边时也可自行回到屏幕内活动，移动仍遵守原有边界规则。拖动、菜单、隐藏或关闭自主活动会取消旧动作，提醒优先显示。
- 拖放吸附范围已收窄（默认桌宠大小约 5 像素），可以停在墙边而不直接隐藏；靠墙时的自主移动优先触发爬墙。
- 贴边时自动显示额度气泡，可在右键菜单手动关闭；非贴边时默认隐藏，也可手动开启并跟随头顶。手动选择在当前状态内保持，重新贴边恢复显示、离开贴边恢复隐藏。气泡用百分比和缓慢波动的液体水位显示剩余额度，余额账户仍显示金额。水位跟随当前主题色；开启 DeepSeek 分时提示后，峰时描边使用与悬浮球一致的主题提醒色、平时描边使用主题强调色，切换主题或自定义颜色会同步更新。刚显示或有鼠标交互时清晰显示，空闲 3 秒后淡化至 65% 不透明度；隐藏时停止波浪。右键“显示额度气泡”只控制气泡，不再弹出独立额度窗口。双击气泡仍打开原有用量面板，单击不打开、不抢焦点。
- 桌宠位置、大小、自主活动开关和缓存放在当前应用数据目录的 `vpet/` 子目录，不再读写旧养成存档。仅通过本机父子进程管道传递展示字段，不传递 API Key、Cookie 或登录信息；主程序退出时桌宠同步退出。
- 右键“额度气泡展示”可选择贴边自动（默认）、悬停、随机或悬停＋随机。悬停约 300 毫秒显示，移到气泡上保持显示，离开角色和气泡 500 毫秒后隐藏；随机间隔可选 3–5、5–10（默认）、10–20 分钟，每次显示当前账户的真实额度 8 秒，开启悬停时可停留延长。手动气泡开关优先，切换模式或真正进出贴边后恢复自动规则。
- 右键可独立启用“喝水提醒”和“休息提醒”，默认关闭；喝水间隔可选 15/30/45/60 分钟（默认 30），休息可选 30/45/60/90 分钟（默认 60）。使用原有文字对话框，两项同时到期合并显示，不发声音或系统通知。随机额度和生活提醒统一使用原有常规姿态，不播放轻拍动作，也不增加素材。提示期间暂停自主走动；贴边时暂时移入屏幕，结束后归位，用户拖动或缩放后不自动归位。隐藏、休眠后恢复重新计时，不补发积压提醒；关闭“自主活动”不影响提醒。上述偏好自动保存在 `vpet/layout.json`，不改变主程序设置或通信协议。
- 精简包不包含 Steam、联机、创意工坊、照片图库、音乐识别或额外代码插件。桌宠自身菜单目前为中文，默认动画有单独授权，详见 [来源与授权](pet_host/THIRD_PARTY_NOTICES.md)。

需要 .NET SDK 8 或更新版本构建宿主。构建与独立预览（演示数据，不读取真实账户）：

```powershell
python scripts/build_vpet.py
python examples/vpet_preview.py
```

独立预览使用隔离的数据目录，也需先在其“设置 → 桌宠”页下载安装扩展；不会因存在本地构建而自动显示桌宠。

默认构建内置 .NET 8 运行时；仅在已装 .NET 8 Desktop Runtime 的开发机试验时可用 `--framework-dependent`。VPet 核心源码已纳入本仓库的 `third_party/VPet/VPet-Simulator.Core/`，无需额外克隆核心仓库。动画仍按固定版本下载到 `build/vpet-upstream/`，依赖缓存也位于 `build/`，首次构建需要联网。

日常开发无需重装主程序：用 `.venv\Scripts\python.exe main.py` 运行最新 Python 界面。正常启动只使用已安装的完整桌宠扩展，即使旧配置保留启用状态，也不会自动调用本地构建。修改 `pet_host/` 或 `third_party/VPet/VPet-Simulator.Core/` 后仍需编译宿主；成功构建会更新 `build/vpet-active.json`，该标记仅供宿主开发调试使用，不作为主程序的安装凭据。

贡献者可只修改、提交 `pet_host/` 和仓库内的 VPet 核心，并单独构建和发布桌宠。`ui/vpet_host.py` 属于主程序通信桥，仅在协议能力需要变化时随主程序更新。不要修改 `build/vpet-upstream/` 中的核心副本，该目录仅用于资源缓存，不参与核心编译。来源版本见 [VPet 源码维护说明](third_party/VPet/UPSTREAM.md)，独立版本与发布步骤见 [桌宠开发说明](pet_host/README.md)。

源码版与安装版共用单实例限制，启动前请先从托盘退出已经运行的 TokenMeter。若启动后仍是悬浮球，请在当前配置的“设置 → 桌宠”下载安装扩展包，再开启“启用 VPet 精简桌宠”。

普通打包命令保持不变，主安装包始终不带桌宠。运行 `python scripts/build_release.py --stage pet` 单独生成 `dist-pet/TokenMeter-Pet-v<桌宠版本>-x64.zip`、`extension.json` 和 `SHA256SUMS.txt`，不需要构建主安装器。现有 `--with-vpet` 参数保留，仅额外生成上述独立产物。主程序 `v*` 发布流程只发布面板安装器；桌宠 `pet-v*` 发布流程只发布扩展，不占用仓库的主程序 Latest 标记。

## 本地数据与隐私

新安装默认把数据保存在 `安装目录\data`。从旧版 TokenSpider 升级时，程序会将 `%APPDATA%\TokenSpider` 复制到新目录，验证配置和 SQLite 数据库后再原子切换；旧目录不会被移动或删除。迁移失败时继续使用旧目录，不影响启动。

Windows 凭据管理器按 `TokenMeter/`、`TokenSpider/`、`TokenScope/` 顺序兼容读取，敏感凭据不会写入 `config.json` 或日志。也可在“设置 → 数据存储 → 应用数据目录”选择新的本地空目录；不支持网络共享路径。

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
├── core/                # 应用身份、自启与桌宠扩展安装管理
├── data/                # 数据目录、聚合与 SQLite 历史缓存
├── updater/             # 更新客户端与独立更新器
├── ui/                  # PySide6 界面
├── pet_host/            # 独立 .NET 桌宠宿主与版本清单
├── third_party/VPet/    # 随仓库维护的桌宠核心源码与授权
├── packaging/           # PyInstaller、安装器与 Windows 版本资源
├── scripts/             # 构建与发布脚本
├── assets/              # 应用图标与不同尺寸的图标导出
├── docs/                # 项目结构说明与 README 图片
├── examples/            # 示例配置与桌宠预览
├── release-notes/       # 版本发布说明
├── tests/               # 单元与 Qt 测试
└── main.py              # 应用入口
```

模块职责、主调用链、导入入口和精简顺序见 [项目目录结构](docs/PROJECT_STRUCTURE.md)。

## 故障排查

- 未配置：在设置中选择平台并填写凭据。
- 凭据失效：重新获取 Cookie；MiMo 会先尝试复用专用浏览器会话。
- 请求频繁或风控：等待后再刷新，不要持续缩短间隔。
- 数据未更新：查看当前数据目录中的 `TokenSpider.log`；新安装通常位于 `安装目录\data`。
- 未出现窗口：检查系统托盘；程序只允许一个实例。

## 版本与 Release

当前版本：`1.13.2`。更新记录及校验文件见 [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases)。

## 第三方声明与致谢

可选桌宠扩展基于 [LorisYounger/VPet（虚拟桌宠模拟器）](https://github.com/LorisYounger/VPet) 的开源核心，并使用其原版默认角色与动画，感谢上游作者及贡献者。默认角色与动画版权归虚拟主播模拟器制作组所有；产品介绍图中的桌宠也来自该项目。

VPet 核心源码采用 [Apache License 2.0](third_party/VPet/LICENSE)，角色、动画与图片适用上游单独的授权声明，不属于 TokenMeter 的 MIT 授权范围。具体来源、修改与授权要求见 [桌宠来源与授权](pet_host/THIRD_PARTY_NOTICES.md) 和 [上游完整声明](third_party/VPet/README.md)。

## License

TokenMeter 自有代码采用 [MIT License](LICENSE)，可在保留版权和许可声明的前提下使用、修改和分发；第三方组件与素材遵循各自的许可和版权声明。
