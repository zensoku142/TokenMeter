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
  <a href="https://github.com/zensoku142/TokenMeter/releases/latest"><strong>Download latest</strong></a> ·
  <a href="https://github.com/zensoku142/TokenMeter/stargazers">Star the project</a> ·
  <a href="https://github.com/zensoku142/TokenMeter/discussions">Feedback & discussions</a>
</p>

<p align="center">
  <strong>AI Token Usage, Cost & Balance Monitor for Windows</strong><br>
  <sub>Track Codex, Cursor, DeepSeek, Xiaomi MiMo, and NayutoAI from a lightweight desktop floating widget.</sub>
</p>

<p align="center">
  <img src="docs/images/readme-hero-v1.12.0.webp" alt="TokenMeter product interface overview" width="960">
</p>

TokenMeter is a lightweight Windows desktop monitor for subscription quotas, token usage, API costs, account balances, and historical trends across Codex, Cursor, DeepSeek, Xiaomi MiMo, and NayutoAI. It stays in the system tray and provides a floating widget plus an expandable detail panel.

## Features

- Codex, Cursor, DeepSeek, Xiaomi MiMo, and NayutoAI support with isolated per-provider caches; only the current provider refreshes by default, with optional background providers selected in Settings.
- Floating widget and system tray with dragging, edge docking, position memory, and collapse on focus loss.
- Light, dark, and Windows system themes.
- Balance, token usage, cost trends, model statistics, intraday charts, and an annual activity heatmap.
- DeepSeek peak-pricing hints; MiMo Cookie collection and renewal through a dedicated Chrome profile.
- Last successful data remains visible during network failures; history is cached in local SQLite.
- API keys, Bearer tokens, and Cookies are stored in Windows Credential Manager.
- Data-directory migration, automatic updates, and single-instance operation.

## Requirements

- Windows 10 or Windows 11; Python 3.11+ for running from source.
- At least one supported account; Codex and Cursor can reuse local login data, while DeepSeek, MiMo, and NayutoAI use their platform credentials.
- An optional DeepSeek API key for the official balance endpoint.

> [!IMPORTANT]
> Usage data depends on web-console endpoints; the MiMo Cookie must include `api-platform_ph`. Platform API or risk-control changes may temporarily affect data. Use only your own credentials and keep them secure.

## Installation

1. Download `TokenMeter-Setup-vX.Y.Z-x64.exe` from [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases/latest) and verify `SHA256SUMS.txt` when needed.
2. Run the installer and choose an install directory. The default is `%LOCALAPPDATA%\Programs\TokenMeter`.
3. Start TokenMeter from its desktop or Start menu shortcut.

## Quick start

```powershell
git clone https://github.com/zensoku142/TokenMeter.git
cd TokenMeter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade "pip>=26.1.2"
python -m pip install -r requirements.txt
python main.py
```

## First-time setup

1. Start the app and click the floating widget to open the panel.
2. Open Settings and select DeepSeek or Xiaomi MiMo.
3. Enter a Bearer token, Cookie, or optional DeepSeek API key. For MiMo, “Get MiMo Cookie” also extracts `api-platform_ph` automatically.
4. Save and refresh. The default refresh interval is 60 seconds.

`examples/config.example.py` only documents fields; do not copy it to `config.py`. A legacy `config.py` is migrated on first launch when possible.

## Local data and privacy

New installations store data in `install directory\data`. When upgrading from TokenSpider, the app copies `%APPDATA%\TokenSpider`, validates configuration and SQLite data, and atomically switches only after validation. The old directory is never moved or deleted; a failed migration continues using it without blocking startup.

Windows Credential Manager is read in `TokenMeter/`, `TokenSpider/`, then `TokenScope/` order. Secrets are never written to `config.json` or logs. Settings can also move data to a new empty local directory; network shares are unsupported.

## Automatic updates

Update checks use GitHub Releases from `zensoku142/TokenMeter`. The app downloads only `TokenMeter-Setup-vX.Y.Z-x64.exe` and `SHA256SUMS.txt`, verifies SHA256, and silently upgrades the existing install directory. The fixed AppId preserves `data` and shortcut targets. If installation fails, the previous version remains available from the same shortcut.

## Uninstall

By default, uninstall removes program files and shortcuts but keeps `data`. Delete that directory manually only after confirming its settings, history, and browser sessions are no longer needed.

## Testing

```powershell
python -m pytest -q
```

Run Qt tests in an available Windows desktop session when possible.

## Build

```powershell
python -m pip install pyinstaller
.\.venv\Scripts\pyinstaller.exe --clean --noconfirm packaging\pyinstaller\TokenMeter.spec
python scripts/build_release.py
```

The release script produces the `dist\TokenMeter\` onedir tree. With Inno Setup installed it also creates `dist-installer\TokenMeter-Setup-vX.Y.Z-x64.exe` and `SHA256SUMS.txt`. The verified release stack is Python 3.12, PyInstaller 6.21, and PySide6 6.11; UPX is optional.

## Project structure

```text
TokenMeter/
├── api/                 # Platform APIs, providers, and pricing rules
├── config/              # Configuration, credentials, migration, and runtime state
├── core/                # Application identity and shared metadata
├── data/                # Data directories, aggregation, and SQLite history
├── updater/             # Update client and standalone updater
├── ui/                  # PySide6 interface
├── packaging/           # PyInstaller, installer, and Windows version resources
├── scripts/             # Build and release automation
├── docs/                # Project structure guide and README image
├── examples/            # Example configuration
├── release-notes/       # Version release notes
├── tests/               # Unit and Qt tests
└── main.py              # Application entry point
```

See [Project structure](docs/PROJECT_STRUCTURE.md) for the complete layout.

## Troubleshooting

- Not configured: select a provider and enter credentials in Settings.
- Expired credentials: collect the Cookie again; MiMo first tries its dedicated browser session.
- Rate limit or risk control: wait before refreshing and do not repeatedly shorten the interval.
- Stale data: inspect `TokenSpider.log` in the active data directory, normally `install directory\data` for a new installation.
- No window: check the system tray; only one instance can run.

## Version and releases

Current version: `1.13.1`. See [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases) for change notes and checksums.

## License

TokenMeter is available under the [MIT License](LICENSE). You may use, modify, and distribute it while preserving the copyright and license notice.
