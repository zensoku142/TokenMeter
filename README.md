# TokenSpider

<p align="center">
  <strong>DeepSeek / 小米 MiMo 用量监控桌面悬浮窗</strong><br>
  <sub>在 Windows 桌面实时查看余额、Token 用量、费用趋势与年度活跃记录</sub>
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&amp;logoColor=white">
  <img alt="Windows 10/11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows&amp;logoColor=white">
  <img alt="Version 1.1.9" src="https://img.shields.io/badge/version-1.1.9-2f6fe4">
</p>

TokenSpider 是一个面向 Windows 的 AI 平台用量监控工具。程序常驻系统托盘，以悬浮球展示核心数据；点击后可展开完整面板，查看余额、今日与本周用量、费用趋势、模型统计和过去一年的活跃记录。支持 DeepSeek 与 小米 Mimo 两个平台，可在设置中切换。

## 功能

- 多平台支持：DeepSeek / 小米 MiMo，设置中可自由切换且缓存互不混用
- 悬浮球与系统托盘常驻，支持自由拖动、边缘吸附和位置记忆
- 展示账户余额、余额可用 Token、本月 Token 用量和累计费用
- DeepSeek 统计今日、本周及各模型的 Token 用量与费用
- 小米 MiMo 展示账户余额、本月费用与各模型日 Token 消耗（含 cache hit）
- 绘制每日费用趋势和过去一年的 Token 活跃热力图
- 面板展开时按配置间隔自动刷新，也可手动刷新；悬浮球状态下自动降低刷新频率
- 网络或接口异常时保留最近一次成功数据，并显示明确的错误状态
- 将历史账单缓存在本地 SQLite 数据库，分批补全过去一年的记录
- 将 API Key、Bearer Token 和 Cookie 保存到 Windows 凭据管理器
- 单实例运行，避免重复启动多个悬浮窗
- Token 显示支持「万 / 亿」自适应单位；大型账户数值一目了然

## 运行要求

- Windows 10 或 Windows 11
- Python 3.11 或更高版本
- DeepSeek 账户或小米 MiMo Token Plan 账户
- 对应平台的 Bearer Token / Cookie，用于读取用量数据
- DeepSeek API Key（可选），用于通过官方接口读取账户余额

> [!IMPORTANT]
> DeepSeek 与 MiMo 的用量/余额与日均依赖网页控制台接口；**MiMo 还需在凭据中同时包含 `api-platform_ph` 字段，并读取 `balance / usage` 与 `usage/detail/list` 等接口**。平台接口、鉴权方式或风控策略发生变化时，部分数据可能暂时无法读取。请仅使用自己的账户凭据，并妥善保管相关信息。

## 下载

前往 [GitHub Releases](https://github.com/chenyifei142/TokenScope/releases/tag/v1.1.9) 下载 Windows 便携版：

- `TokenSpider-v1.1.9-windows-x64.exe`

程序无需预装 Python。首次运行若被 Windows SmartScreen 提示，请核对发布页中的 SHA256 后再决定是否运行。

## 快速开始

在 PowerShell 中执行：

```powershell
git clone https://github.com/chenyifei142/TokenScope.git
cd TokenScope

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

如果 PowerShell 不允许激活虚拟环境，可以直接使用虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

## 首次配置

1. 启动程序，点击悬浮球展开面板。
2. 打开「设置」 → 在「数据来源」下拉列表中选择 **DeepSeek** 或 **小米 MiMo**。
3. 填写对应平台的凭据：
   - **DeepSeek**：Bearer Token 或 Cookie；如有官方 API Key 可一并填写
   - **小米 MiMo**：登录 `platform.xiaomimimo.com` 后复制浏览器请求中的**完整 Cookie**（通常包含 `api-platform_serviceToken`、`userId`、`api-platform_slh`、`api-platform_ph`）
4. 保存设置并执行刷新。

默认刷新间隔为 60 秒。切换平台后，面板会立即进入该平台自己的未配置、加载、成功或失败状态，不会短暂展示上一平台数据。

仓库中的 [`config.example.py`](config.example.py) 仅用于展示可配置项。新版程序会自动生成运行时配置，无需将其复制为 `config.py`。如果程序目录中存在旧版 `config.py`，首次启动时会尝试迁移配置并移除其中的明文凭据。

## 本地数据与安全

程序数据默认保存在 `%APPDATA%\TokenSpider`：

| 文件 | 用途 |
| --- | --- |
| `config.json` | 非敏感设置，不包含 API Key、Token 或 Cookie |
| `usage.db` | 本地用量与费用历史缓存 |
| `widget-state.json` | 悬浮球位置 |
| `TokenSpider.log` | 运行日志，单文件最大 2 MB，最多保留 3 个备份 |

敏感凭据保存在 Windows 凭据管理器中，目标名称以 `TokenSpider/` 开头。保存新设置前，程序会备份普通配置；写入失败时会回滚凭据，避免新旧配置混用。

## 性能与能源

程序在空闲时做了多项优化，降低 CPU / 唤醒次数：

- 悬浮球仅在数值变化时重绘；没有变化时不再触发抗锯齿/渐变绘制。
- 移除每 30 秒一次的空刷新循环；只有 API 返回新数据后才更新 UI。
- 贴边隐藏后仅在悬浮球附近检测鼠标，不会因经过屏幕其他边缘而误唤出。
- 刷新间隔根据窗口状态自适应：面板展开使用配置间隔，小球可见时至少 5 分钟，贴边隐藏时至少 10 分钟。
- 粘贴 Cookie 时自动规范化多余空白、换行和大小写无关紧要的标点。

贴边吸附、隐藏和唤出共用一个带缓动的位置动画，展开面板或拖动期间会暂停自动隐藏。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Qt 测试会创建界面组件，建议在可用的 Windows 桌面会话中运行。

## 构建 Windows 可执行文件

项目提供了 PyInstaller 配置：

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\pyinstaller.exe --clean --noconfirm TokenSpider.spec
```

已验证的发布环境为 Python 3.12、PyInstaller 6.21、PySide6 6.11。构建产物位于 `dist\TokenSpider-v1.1.9-windows-x64.exe`。spec 会保留 pyqtgraph 启动所需的 Qt OpenGL 桥接模块及其顶层必需包，仅排除项目未使用的 Qt QML/Quick/PDF/测试组件和非 Windows 平台插件；`build*`、`dist*` 与构建虚拟环境均被 Git 忽略。

`upx=True` 只会在本机已安装 UPX 时生效；UPX 不是必需依赖。发布前必须重新验证启动、托盘、图表和退出流程，因为压缩 Qt DLL 可能增加杀毒软件误报。

## 项目结构

```text
TokenSpider/
├── api/providers/       # 各平台接口适配器（DeepSeek、小米 Mimo）
├── data/                # 数据聚合、SQLite 历史缓存
├── tests/               # 单元测试与 Qt 界面测试
├── ui/                  # PySide6 悬浮球、面板、设置与托盘界面
├── config_manager.py    # 配置、凭据迁移与日志管理
├── config.example.py    # 配置项示例
├── main.py              # 应用入口与单实例控制
├── TokenSpider.spec     # PyInstaller 构建配置
└── requirements.txt     # Python 依赖
```

## 故障排查

- **提示尚未配置**：在设置中选择提供商（DeepSeek / 小米 MiMo）并填写对应凭据。
- **提示凭据失效**：重新获取并保存当前账户的 Token 或 Cookie；**小米 MiMo 需同时更新 `api-platform_ph`，且需从浏览器复制整段 Cookie 粘贴到凭据输入框**。
- **提示请求过于频繁或平台风控拒绝请求**：等待一段时间后再手动刷新；不要持续缩短刷新间隔。
- **数据暂时没有更新**：程序会继续显示上一次成功获取的缓存，可查看 `%APPDATA%\TokenSpider\TokenSpider.log` 获取详细信息。
- **程序没有出现窗口**：检查系统托盘；TokenSpider 只允许一个实例运行。
- **大数字显示过长**：≥ 1 亿的 Token 会以「亿」为单位显示，无需额外处理。

## v1.1.9

更新类型：Bug 修复与功能优化

### 新增

- 小米 MiMo 接入新的平台接口（`/api/v1/balance`、`/api/v1/usage`、`POST /api/v1/usage/detail/list`），可展示账户余额、月度费用与各模型日用量明细。
- 小米 MiMo 支持 `api-platform_ph` 自动注入 Cookie 和 URL query，提升登录态稳定性。
- Token 显示支持「万 / 亿」自适应单位；大型账户数值更易读。
- 设置中的凭据输入框支持多行粘贴，并对粘贴的 Cookie 做空白规范化。

### 修复

- 修复小米 MiMo 平台变更后余额、用量与明细被误判为「鉴权失败」的问题。
- 修复 Qt 重试策略仅接受 GET 导致 POST 明细请求在网络抖动时直接失败的问题。

### 优化

- 优化错误状态、未配置状态和上游不提供数据时的界面表达。
- 精简未使用的 Qt 运行库并补充 Windows 文件版本信息。
- 更新安全、构建、下载和故障排查说明。

## v1.1.8

更新类型：Bug 修复

### 修复

- 提高 Token 热力图第 0 档空格子的填充与描边对比度，避免未使用日期融入背景后看不出网格。

## v1.1.7

更新类型：功能优化

### 优化

- Token 热力图采用低饱和深蓝至冰蓝的视觉方案，在增强档位差异的同时保持整体深色风格统一。

## v1.1.6

更新类型：Bug 修复

### 修复

- 修复可见 Token 用量相近时热力图集中显示为同一高亮颜色的问题，非零数据现在会在当前最小值与最大值之间展开分级。

## v1.1.5

更新类型：功能优化

### 优化

- Token 热力图改为按当前可见最大值动态归一化，0 值独立显示，最大值始终使用最高档；数据跨度达到一个数量级时自动采用对数分级。

## v1.1.4

更新类型：功能优化

### 优化

- 增强 Token 热力图五档蓝色的明暗差异，并按非零用量排名分级，避免极端峰值让多数日期挤在同一颜色档位。

## v1.1.3

更新类型：Bug 修复

### 修复

- 修复近 7 天折线图首尾数据点贴边的问题，并保持日期、数据点和悬浮提示准确对齐。

## v1.1.2

更新类型：Bug 修复

### 修复

- 完整保留 pyqtgraph 0.14 启动时必需的 `imageview`、`multiprocess`、`parametertree` 和 Qt OpenGL 模块，修复 Windows 便携版连续出现模块缺失的问题。
- 增加打包配置回归测试，防止再次排除 pyqtgraph 顶层必需模块或遗漏程序图标。

## v1.1.1

更新类型：Bug 修复

### 修复

- 修复 Windows 便携版遗漏 Qt OpenGL 桥接模块，导致启动时报 `ModuleNotFoundError` 的问题。
- 恢复 Windows 可执行文件的 TokenSpider 自定义图标。

## v1.1.0

### 新增

- 支持小米 MiMo Token Plan 月度已用量和剩余额度查询。
- 支持 DeepSeek 与小米 MiMo 平台切换及独立状态缓存。

### 修复

- 修复 DeepSeek 费用响应被当成 Token、今日金额不显示和部分接口失败清空全部数据的问题。
- 修复 SQLite 首次建库、旧库迁移以及不同平台历史数据互相污染的问题。
- 修复平台切换后短暂显示旧平台数据，以及配置布尔值被错误保存为字符串的问题。
- 修复悬浮球贴边瞬移、全屏边缘误唤出和展开面板时仍自动隐藏的问题。

### 优化

- 优化错误状态、未配置状态和上游不提供数据时的界面表达。
- 精简未使用的 Qt 运行库并补充 Windows 文件版本信息。
- 更新安全、构建、下载和故障排查说明。

## 版本

当前版本：`1.1.9`
