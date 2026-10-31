<p align="center">
  <a href="./README.md">简体中文</a> |
  <a href="./README.en.md">English</a> |
  <a href="./README.zh-TW.md">繁體中文</a> |
  <a href="./README.ja.md">日本語</a> |
  <a href="./README.ko.md">한국어</a>
</p>

# TokenMeter — Windows AI Token Usage & Subscription Quota Monitor

<p align="center">
  <a href="https://github.com/zensoku142/TokenMeter/stargazers"><img alt="GitHub Stars" src="https://img.shields.io/github/stars/zensoku142/TokenMeter?style=flat-square"></a>
  <a href="https://github.com/zensoku142/TokenMeter/releases/latest"><img alt="Latest Release" src="https://img.shields.io/github/v/release/zensoku142/TokenMeter?style=flat-square"></a>
  <a href="https://github.com/zensoku142/TokenMeter/releases"><img alt="Release Downloads" src="https://img.shields.io/github/downloads/zensoku142/TokenMeter/total?style=flat-square"></a>
  <a href="https://github.com/zensoku142/TokenMeter/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/zensoku142/TokenMeter/ci.yml?branch=master&style=flat-square&label=CI"></a>
</p>

<p align="center">
  <a href="https://github.com/zensoku142/TokenMeter/releases/latest"><strong>Download latest</strong></a> ·
  <a href="https://github.com/zensoku142/TokenMeter/stargazers">Star the project</a> ·
  <a href="https://github.com/zensoku142/TokenMeter/discussions">Feedback & discussions</a>
</p>

<p align="center">
  <strong>AI Token Usage, Cost & Balance Monitor for Windows</strong><br>
  <sub>Codex · Claude · Cursor · DeepSeek · MiMo · NayutoAI</sub>
</p>

<p align="center">
  <a href="site/assets/panel-light.png"><img src="site/assets/panel-light.png" alt="TokenMeter Codex panel (demo data)" width="960"></a><br>
  <a href="site/assets/panel-token-speed.png"><img src="site/assets/panel-token-speed.png" alt="Token speed candlestick chart (Chinese UI, demo data)" width="960"></a><br>
  <img src="site/assets/light-ring.png" alt="Light ring displaying three providers (demo data)" width="58">
  <img src="site/assets/pet-bailong.png" alt="Xiaolong desktop pet" width="220">
</p>

Actual component screenshots in Chinese, using demo data. [Original images and sources](docs/images/readme/README.md).

TokenMeter is a lightweight AI usage monitor for Windows 10/11 with 6 separate provider connections. It displays subscription quotas, reset times, API costs, balances and history according to each provider's capabilities.

Current version：**v1.17.0** · Pet **v0.3.1** · [GitHub Pages](https://zensoku142.github.io/TokenMeter/)



## Features

- **CC Switch Codex accounts**: discover and refresh Codex OAuth accounts managed by CC Switch in Settings → Multi-account. CC Switch handles renewal and queries; cached quotas remain visible during a disconnect. The local bridge relies on CC Switch internals and may need updates when CC Switch changes. If CC Switch is already running without the bridge, exit it fully from the tray once before refreshing.
- **Light ring and Codex reset news**: the optional light ring displays multiple providers and accounts, supports drag ordering and customizable ring color. Its hover details can show community reset announcements and optional notifications; these do not confirm an individual account reset.
- **Codex intraday usage**: view cached input, uncached input and output tokens grouped by local session log timestamps, with model tooltips, retained dates and incremental refresh. Counts cover the selected local directory only; account attribution is unknown and they are not subscription quota or actual billing.
- **Token speed**: view output speed by model and time in the Codex and NayutoAI panels. Speed is estimated from eligible requests' output tokens and duration, including the first-token wait; unavailable data remains unknown.
- **Quotas and balances**: Codex, Claude Code and Cursor quotas; DeepSeek, MiMo and NayutoAI usage and balances. Missing fields remain unavailable; credits, currencies and tokens stay separate.
- **Overview and accounts**: automatically refresh configured providers while the overview is visible. Settings → Multi-account stores independent connections, credentials and monitoring state without replacing default connections or CLI logins. Two configured providers can also stay visible in a compact, always-on-top dashboard.
- **Local analytics and Excel**: read local Codex (including archived sessions) and Claude logs, show cached results, then update in the background. Filter by date, project and model, compare daily usage, zoom charts and export `.xlsx` summaries, details and methodology. Local logs have unknown account attribution and do not represent subscription quotas or actual bills; accessible WSL directories can be selected without starting WSL.
- **Provider management**: offline brand icons, search, favorites and removal of app-managed connections. Removing a provider preserves CLI logins and history.
- **Refresh and alerts**: retry backoff, offline cache, optional low-quota and recovery alerts, forecasts and quiet hours. Forecasts require at least 10 minutes of valid samples from the same quota cycle; recovery alerts require low-quota alerts and a confirmed refresh. Alert state survives restarts.
- **Desktop display**: draggable floating widget, wheel resizing, edge hiding, tray, light/dark/system themes, colors and opacity, five languages, automatic updates and an optional multi-character pet. Pet quota windows follow the app theme with pet v0.3.1 and app v1.17.0.


## Installation and setup

Requires Windows 10 / 11 and at least one supported account.

1. Download and run `TokenMeter-Setup-vX.Y.Z-x64.exe` from [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases/latest). Checksums are in `SHA256SUMS.txt`.
2. Click the floating widget and select a provider in Settings. Codex / Cursor can read local login data; enter DeepSeek credentials or an optional API key, use MiMo's Cookie collection, or enter NayutoAI credentials.
3. Settings save automatically; the default refresh interval is 60 seconds. Appearance contains theme and language options; Floating & Startup contains launch and edge behavior.

> Data depends on platform endpoints and login state. API or risk-control changes may interrupt collection. Use only your own credentials.

## Desktop pet (optional)

The main installer does not include the pet. Download it in Settings → Pet, then enable it after installation completes; no separate .NET installation is needed. The pet replaces the floating widget by default. Enable “Also show the floating ball” to display both; the setting saves automatically and takes effect immediately. Disabling or uninstalling it restores the widget without affecting accounts or the panel.

- Touch interactions, dragging, resizing, autonomous activity, and an edge quota bubble. Double-click the bubble to open the usage panel.
- Choose VPet, [Whale Girl](https://github.com/PC2005-cloud/dsh-pet), or [Xiaolong](pet_host/bailong/README.md). Xiaolong includes all 35 animations in the same pet host, without AI chat or conversation history. The settings page shows character thumbnails and compatible versions available for installation or update.
- The context menu controls bubble visibility and optional water / break reminders, which are off by default. The pet's menu is currently in Chinese.
- The lite extension omits feeding, work, progression, Steam, and online features. It updates independently and exits with the main app.

See [pet development](pet_host/README.md) for implementation and build details, and read the [source and licensing notices](pet_host/THIRD_PARTY_NOTICES.md) before reusing assets.

![Whale Girl desktop pet preview](site/assets/pet-whale-transparent.png)

Whale Girl assets: [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet); non-commercial use only.

## Data, privacy, and updates

- Data defaults to `install directory\data` and can be moved in Settings. Legacy migration copies data and preserves the original directory; history is cached in local SQLite.
- API keys, Bearer tokens, and Cookies are stored in Windows Credential Manager, not configuration files or logs. The pet receives display fields only, never credentials.
- Updates verify SHA256 and preserve data and shortcuts. Uninstall also keeps `data`; remove it manually only when no longer needed. A checksum is not a release signature.

## Run from source

Requires Python 3.11+. Source and installed builds share a single-instance limit; exit any running TokenMeter first.

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
<summary>Development, tests, and builds</summary>

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
pyright
python -m pip install -r requirements-build.txt
python scripts/build_release.py
```

Qt tests need an available Windows desktop session; installer builds require Inno Setup. Pet builds require .NET SDK 8+; see [pet development](pet_host/README.md).

[Project structure](docs/PROJECT_STRUCTURE.md) · [Example configuration](examples/config.example.py) (do not copy it to `config.py`)

</details>

## Troubleshooting

- No window: check the system tray and avoid starting a second instance.
- Expired credentials or rate limits: renew the login / Cookie, or wait before refreshing.
- Data problems: inspect `TokenSpider.log` in the active data directory; remove sensitive information before reporting an issue.

## Versions

Main app: `1.17.0`; optional pet extension: `0.3.1`. See [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases) for changes and checksums.

## License and acknowledgments

TokenMeter's own code uses the [MIT License](LICENSE).

The pet core, default character, and animations come from [LorisYounger/VPet](https://github.com/LorisYounger/VPet). Thanks to its authors and contributors. The core uses [Apache-2.0](third_party/VPet/LICENSE); the character and animations are copyrighted by 虚拟主播模拟器制作组 and have separate terms, outside TokenMeter's MIT license. See the [third-party notices](pet_host/THIRD_PARTY_NOTICES.md).
