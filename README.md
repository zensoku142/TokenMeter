<p align="center">
  <a href="./README.md">简体中文</a> |
  <a href="./README.en.md">English</a> |
  <a href="./README.zh-TW.md">繁體中文</a> |
  <a href="./README.ja.md">日本語</a> |
  <a href="./README.ko.md">한국어</a>
</p>

# TokenMeter — Windows AI Token 用量与订阅额度监控工具

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
  <a href="docs/images/readme-hero.webp"><img src="docs/images/readme-hero.webp" alt="TokenMeter：Codex 额度、DeepSeek 今日分时与余额、悬浮球及 VPet 桌宠（演示数据）" width="960"></a>
</p>

真实组件截图，使用演示数据。[查看原图与来源](docs/images/readme/README.md)。

TokenMeter 是一款适用于 Windows 10/11 的轻量级 AI Token 用量与订阅额度监控工具：可查看 Codex、Cursor 的已用与剩余额度、重置时间，以及 DeepSeek、Xiaomi MiMo、NayutoAI 的 Token 用量、API 费用、账户余额和历史趋势。

## 功能

- **订阅额度**：Codex / Cursor 的已用与剩余比例、重置时间；Codex 另有近 7 天 Token、年度活动和使用统计。
- **API 用量**：DeepSeek / MiMo / NayutoAI 的费用与余额、今日分时图、Token 构成和历史趋势。
- **悬浮展示**：额度水球或余额展示，支持拖动、滚轮缩放、贴边隐藏和系统托盘常驻。
- **外观与语言**：浅色、深色及系统主题，可调整主题色和透明度；支持简中、繁中、英语、日语、韩语。
- **采集与缓存**：默认仅刷新当前平台，可选后台同步；支持离线缓存、DeepSeek 峰谷提示与 MiMo Cookie 获取及续期。
- **桌面集成**：开机自启、自动更新、数据目录迁移，以及可选的 VPet 桌宠扩展。

## 安装与配置

需要 Windows 10 / 11 和至少一个受支持的平台账户。

1. 从 [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases/latest) 下载 `TokenMeter-Setup-vX.Y.Z-x64.exe`，安装后启动；校验文件为 `SHA256SUMS.txt`。
2. 点击悬浮球打开面板，在“设置”中选择平台。Codex / Cursor 可读取本机登录；DeepSeek 填写凭据或可选 API Key，MiMo 可一键获取 Cookie，NayutoAI 使用其平台凭据。
3. 设置自动保存，默认每 60 秒刷新。主题和语言位于“外观”，启动与贴边选项位于“悬浮与启动”。

> 数据依赖平台接口与登录状态，接口变化或风控可能暂时影响获取。请仅使用自己的账户凭据。

## VPet 桌宠（可选）

主安装包不含桌宠。在“设置 → 桌宠”下载扩展，完整安装后再启用；无需另装 .NET。启用后桌宠替代悬浮球，关闭或卸载后恢复球体，不影响账户与面板。

- 支持轻触互动、拖动缩放、自主活动和贴边额度气泡；双击气泡打开用量面板。
- 右键菜单可设置额度气泡展示方式，以及默认关闭的喝水、休息提醒；桌宠菜单目前为中文。
- 精简版不含投喂、工作、养成、Steam 或联机功能。扩展可单独更新，主程序退出时桌宠一同退出。

实现细节和独立构建见 [桌宠开发说明](pet_host/README.md)，使用素材前请阅读 [来源与授权](pet_host/THIRD_PARTY_NOTICES.md)。

## 数据、隐私与更新

- 数据默认保存在 `安装目录\data`，可在设置中迁移。旧版升级采用复制迁移，保留原目录；历史缓存在本地 SQLite。
- API Key、Bearer Token 与 Cookie 存入 Windows 凭据管理器，不写入配置或日志；桌宠只接收展示字段，不接收凭据。
- 更新包通过 SHA256 校验后安装，保留数据与快捷方式。默认卸载也保留 `data`，请确认不再需要后再手动删除。SHA256 校验不等同于发布签名。

## 从源码运行

需要 Python 3.11+。源码版与安装版共用单实例限制，请先退出已运行的 TokenMeter。

```powershell
git clone https://github.com/zensoku142/TokenMeter.git
cd TokenMeter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade "pip>=26.1.2"
python -m pip install -r requirements.txt
python main.py
```

<details>
<summary>开发、测试与构建</summary>

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
pyright
python -m pip install -r requirements-build.txt
python scripts/build_release.py
```

Qt 测试需要可用的 Windows 桌面会话；生成安装器需要 Inno Setup。桌宠构建需要 .NET SDK 8+，详见 [桌宠开发说明](pet_host/README.md)。

[项目结构](docs/PROJECT_STRUCTURE.md) · [配置示例](examples/config.example.py)（无需复制为 `config.py`）

</details>

## 常见问题

- 无窗口：检查系统托盘，并确认未重复启动。
- 凭据失效或请求受限：更新登录 / Cookie，或等待后再刷新。
- 数据异常：查看当前数据目录的 `TokenSpider.log`；提交反馈前请移除敏感信息。

## 版本

主程序 `1.14.2`，可选桌宠扩展 `0.1.3`。更新记录与校验文件见 [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases)。

## 许可与致谢

TokenMeter 自有代码采用 [MIT License](LICENSE)。

桌宠核心及默认角色、动画来源于 [LorisYounger/VPet](https://github.com/LorisYounger/VPet)，感谢上游作者及贡献者。核心采用 [Apache-2.0](third_party/VPet/LICENSE)；角色与动画版权归虚拟主播模拟器制作组所有，适用单独授权，不属于本项目的 MIT 授权范围。详见 [第三方声明](pet_host/THIRD_PARTY_NOTICES.md)。
