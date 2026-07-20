<p align="center">
  <a href="./README.md">简体中文</a> |
  <a href="./README.en.md">English</a> |
  <a href="./README.zh-TW.md">繁體中文</a> |
  <a href="./README.ja.md">日本語</a> |
  <a href="./README.ko.md">한국어</a>
</p>

# TokenMeter

<p align="center">
  <strong>AI Token Usage, Cost & Balance Monitor for Windows</strong><br>
  <sub>Track DeepSeek and Xiaomi MiMo usage from a lightweight desktop floating widget.</sub>
</p>

TokenMeter is a lightweight Windows desktop monitor for token usage, API costs, account balances, and historical trends on DeepSeek and Xiaomi MiMo. It stays in the system tray and provides a floating widget plus an expandable detail panel.

## Features

- DeepSeek and Xiaomi MiMo support with isolated per-provider caches.
- Floating widget and system tray with dragging, edge docking, position memory, and collapse on focus loss.
- Light, dark, and Windows system themes.
- Balance, token usage, cost trends, model statistics, intraday charts, and an annual activity heatmap.
- DeepSeek peak-pricing hints; MiMo Cookie collection and renewal through a dedicated Chrome profile.
- Last successful data remains visible during network failures; history is cached in local SQLite.
- API keys, Bearer tokens, and Cookies are stored in Windows Credential Manager.
- Data-directory migration, automatic updates, and single-instance operation.

## Screenshots

| Light theme | Dark theme |
| --- | --- |
| ![TokenMeter light theme](docs/images/token-spider-ui-v3-light.png) | ![TokenMeter dark theme](docs/images/token-spider-ui-v3-dark.png) |

## Requirements

- Windows 10 or Windows 11; Python 3.11+ for running from source.
- A DeepSeek or Xiaomi MiMo Token Plan account and the corresponding Cookie / Token.
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
python -m pip install -r requirements.txt
python main.py
```

## First-time setup

1. Start the app and click the floating widget to open the panel.
2. Open Settings and select DeepSeek or Xiaomi MiMo.
3. Enter a Bearer token, Cookie, or optional DeepSeek API key. For MiMo, “Get MiMo Cookie” also extracts `api-platform_ph` automatically.
4. Save and refresh. The default refresh interval is 60 seconds.

`config.example.py` only documents fields; do not copy it to `config.py`. A legacy `config.py` is migrated on first launch when possible.

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
.\.venv\Scripts\pyinstaller.exe --clean --noconfirm TokenMeter.spec
python scripts/build_release.py
```

The release script produces the `dist\TokenMeter\` onedir tree. With Inno Setup installed it also creates `dist-installer\TokenMeter-Setup-vX.Y.Z-x64.exe` and `SHA256SUMS.txt`. The verified release stack is Python 3.12, PyInstaller 6.21, and PySide6 6.11; UPX is optional.

## Project structure

```text
TokenMeter/
├── api/providers/       # DeepSeek and Xiaomi MiMo adapters
├── data/                # Aggregation and SQLite history
├── tests/               # Unit and Qt tests
├── ui/                  # PySide6 interface
├── app_identity.py      # Display and compatibility identities
├── config_manager.py    # Configuration, credentials, and logging
├── main.py              # Application entry point
└── TokenMeter.spec      # PyInstaller configuration
```

## Troubleshooting

- Not configured: select a provider and enter credentials in Settings.
- Expired credentials: collect the Cookie again; MiMo first tries its dedicated browser session.
- Rate limit or risk control: wait before refreshing and do not repeatedly shorten the interval.
- Stale data: inspect `TokenSpider.log` in the active data directory, normally `install directory\data` for a new installation.
- No window: check the system tray; only one instance can run.

## Version and releases

Current version: `1.10.4`. See [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases) for change notes and checksums.

## License

The repository currently has no separate license file. Contact the maintainer before use or redistribution.
