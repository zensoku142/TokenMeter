# TokenSpider

<p align="center">
  <strong>DeepSeek / 小米 MiMo 用量监控桌面悬浮窗</strong><br>
  <sub>在 Windows 桌面实时查看余额、Token 用量、费用趋势与年度活跃记录</sub>
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&amp;logoColor=white">
  <img alt="Windows 10/11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows&amp;logoColor=white">
  <img alt="Version 1.1.0" src="https://img.shields.io/badge/version-1.1.0-2f6fe4">
</p>

TokenSpider 是一个面向 Windows 的 AI 平台用量监控工具。程序常驻系统托盘，以悬浮球展示核心数据；点击后可展开完整面板，查看余额、今日与本周用量、费用趋势、模型统计和过去一年的活跃热力图。支持 DeepSeek 与 小米 Mimo 两个平台，可在设置中切换。

## 功能

- 多平台支持：DeepSeek / 小米 MiMo，设置中可自由切换且缓存互不混用
- 悬浮球与系统托盘常驻，支持自由拖动、边缘吸附和位置记忆
- 展示账户余额、余额可用 Token、本月 Token 用量和累计费用
- DeepSeek 统计今日、本周及各模型的 Token 用量与费用
- MiMo 展示本月套餐已用 Token 和套餐剩余 Token
- 绘制每日费用趋势和过去一年的 Token 活跃热力图
- 面板展开时按配置间隔自动刷新，也可手动刷新；悬浮球状态下自动降低刷新频率
- 网络或接口异常时保留最近一次成功数据，并显示明确的错误状态
- 将历史账单缓存在本地 SQLite 数据库，分批补全过去一年的记录
- 将 API Key、Bearer Token 和 Cookie 保存到 Windows 凭据管理器
- 单实例运行，避免重复启动多个悬浮窗

## 运行要求

- Windows 10 或 Windows 11
- Python 3.11 或更高版本
- DeepSeek 账户或小米 MiMo Token Plan 账户
- 对应平台的 Bearer Token / Cookie，用于读取用量数据
- DeepSeek API Key（可选），用于通过官方接口读取账户余额

> [!IMPORTANT]
> DeepSeek 逐日明细和 MiMo 套餐用量均依赖网页控制台接口。平台接口、鉴权方式或风控策略发生变化时，部分数据可能暂时无法读取。请仅使用自己的账户凭据，并妥善保管相关信息。

## 下载

前往 [GitHub Releases](https://github.com/chenyifei142/TokenScope/releases/tag/v1.1.0) 下载 Windows 便携版：

- `TokenSpider-v1.1.0-windows-x64.exe`

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
2. 打开“设置” → 在“数据来源”下拉列表中选择 **DeepSeek** 或 **小米 MiMo**。
3. 填写对应平台的凭据：
   - **DeepSeek**：Bearer Token 或 Cookie；如有官方 API Key 可一并填写
   - **小米 MiMo**：登录 `platform.xiaomimimo.com` 后复制控制台请求的完整 Cookie；通常应包含 `api-platform_serviceToken`、`userId`、`api-platform_slh` 和 `api-platform_ph`
4. 保存设置并执行刷新。

默认刷新间隔为 60 秒。切换平台后，面板会立即进入该平台自己的未配置、加载、成功或失败状态，不会短暂展示上一平台数据。

MiMo 控制台目前只提供 Token Plan 月度汇总，不提供可靠的逐日 Token 明细和人民币费用。TokenSpider 因此显示“套餐已用 / 套餐剩余”，费用和逐日图表显示为不可用，不会把缺失数据伪装成 `¥0.00`。

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

已验证的发布环境为 Python 3.12、PyInstaller 6.21、PySide6 6.11。构建产物位于 `dist\TokenSpider-v1.1.0-windows-x64.exe`。spec 仅排除项目未使用的 Qt QML/Quick/PDF/OpenGL/测试组件和非 Windows 平台插件；`build*`、`dist*` 与构建虚拟环境均被 Git 忽略。

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
- **提示凭据失效**：重新获取并保存当前账户的 Token 或 Cookie；小米 Mimo 需同时更新 `api-platform_ph`。
- **提示请求过于频繁或平台风控拒绝请求**：等待一段时间后再手动刷新；不要持续缩短刷新间隔。
- **数据暂时没有更新**：程序会继续显示上一次成功获取的缓存，可查看 `%APPDATA%\TokenSpider\TokenSpider.log` 获取详细信息。
- **MiMo 没有今日金额/热力图**：这是上游控制台未提供逐日费用明细，并非金额为零。
- **程序没有出现窗口**：检查系统托盘；TokenSpider 只允许一个实例运行。

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

当前版本：`1.1.0`
