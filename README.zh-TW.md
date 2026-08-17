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
  <a href="https://github.com/zensoku142/TokenMeter/releases/latest"><strong>下載最新版</strong></a> ·
  <a href="https://github.com/zensoku142/TokenMeter/stargazers">如果有幫助，請點 Star</a> ·
  <a href="https://github.com/zensoku142/TokenMeter/discussions">意見與討論</a>
</p>

<p align="center">
  <strong>Windows AI Token 用量、費用與餘額監控工具</strong><br>
  <sub>適用於 Codex、Cursor、DeepSeek、Xiaomi MiMo 與 NayutoAI 的用量監控工具。</sub>
</p>

<p align="center">
  <img src="docs/images/readme-hero-v1.12.0.webp" alt="TokenMeter 產品介面概覽" width="960">
</p>

TokenMeter 是輕量級 Windows 桌面 AI 用量監控工具，用於查看 Codex、Cursor、DeepSeek、Xiaomi MiMo 與 NayutoAI 的訂閱額度、Token 消耗、呼叫費用、帳戶餘額與歷史趨勢。程式常駐系統匣，提供浮動小工具與可展開的詳細面板。

## 功能

- 支援 Codex、Cursor、DeepSeek、Xiaomi MiMo 與 NayutoAI，各平台快取互不混用；預設只更新目前平台，僅在設定中勾選的平台會於背景同步取得。
- 浮動小工具與系統匣常駐，支援拖曳、邊緣吸附、位置記憶及失焦收合。
- 提供淺色、深色及跟隨 Windows 的主題。
- 顯示餘額、Token 用量、費用趨勢、模型統計、分時圖與年度活躍熱力圖。
- DeepSeek 峰谷計價提示；MiMo Cookie 可透過專用 Chrome 工作階段取得與續期。
- 網路異常時保留最近成功資料；歷史資料快取於本機 SQLite。
- API Key、Bearer Token 與 Cookie 儲存於 Windows 認證管理員。
- 支援資料目錄遷移、自動更新及單一執行個體。

## 系統需求

- Windows 10 或 Windows 11；從原始碼執行需要 Python 3.11+。
- 至少一個支援的平台帳戶；Codex 與 Cursor 可使用本機登入資料，DeepSeek、MiMo 與 NayutoAI 使用各平台憑據。
- DeepSeek API Key 為選用，用於官方餘額端點。

> [!IMPORTANT]
> 用量資料依賴平台網頁控制台端點；MiMo Cookie 必須包含 `api-platform_ph`。平台 API 或風控變更可能暫時影響資料。請只使用自己的憑據並妥善保管。

## 下載

從 [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases/latest) 下載 `TokenMeter-Setup-vX.Y.Z-x64.exe`，需要時核對 `SHA256SUMS.txt`。執行安裝程式並選擇安裝目錄，再從桌面或開始功能表捷徑啟動；預設安裝到 `%LOCALAPPDATA%\Programs\TokenMeter`。

## 快速開始

```powershell
git clone https://github.com/zensoku142/TokenMeter.git
cd TokenMeter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade "pip>=26.1.2"
python -m pip install -r requirements.txt
python main.py
```

## 首次設定

1. 啟動程式並點擊浮動小工具展開面板。
2. 開啟「設定」，選擇 DeepSeek 或 Xiaomi MiMo。
3. 填寫 Bearer Token、Cookie 或選用的 DeepSeek API Key。MiMo 的「一鍵取得 MiMo Cookie」會自動擷取 `api-platform_ph`。
4. 儲存並重新整理。預設重新整理間隔為 60 秒。

`examples/config.example.py` 僅展示欄位，不必複製為 `config.py`。舊版 `config.py` 會在首次啟動時嘗試遷移。

## 本機資料與隱私

全新安裝把資料存於 `安裝目錄\data`。從舊 TokenSpider 升級時，程式會複製 `%APPDATA%\TokenSpider`，驗證設定與 SQLite 後才原子切換；舊目錄不會被移動或刪除，失敗時仍使用舊資料啟動。Windows 認證管理員依序相容 `TokenMeter/`、`TokenSpider/`、`TokenScope/`。

## 自動更新

更新只下載 `TokenMeter-Setup-vX.Y.Z-x64.exe` 與 `SHA256SUMS.txt`，驗證 SHA256 後靜默覆蓋原安裝目錄。固定 AppId 會保留 `data` 與捷徑；失敗時舊版本仍可從相同捷徑啟動。預設解除安裝只刪除程式與捷徑，保留 `data`。

## 測試

```powershell
python -m pytest -q
```

Qt 測試建議在可用的 Windows 桌面工作階段中執行。

## 建置

```powershell
python -m pip install pyinstaller
.\.venv\Scripts\pyinstaller.exe --clean --noconfirm packaging\pyinstaller\TokenMeter.spec
python scripts/build_release.py
```

發布腳本會產生 `dist\TokenMeter\` onedir 結構；安裝 Inno Setup 後也會產生 `dist-installer\TokenMeter-Setup-vX.Y.Z-x64.exe` 與 `SHA256SUMS.txt`。

## 專案結構

```text
TokenMeter/
├── api/                 # 平台 API、Provider 與計價規則
├── config/              # 設定、憑據、遷移與執行階段狀態
├── core/                # 應用程式身分與共用中繼資料
├── data/                # 資料目錄、聚合與 SQLite 歷史
├── updater/             # 更新用戶端與獨立更新器
├── ui/                  # PySide6 介面
├── packaging/           # PyInstaller、安裝器與 Windows 資源
├── scripts/             # 建置與發布自動化
├── docs/                # 文件、任務封存與圖片
├── examples/            # 設定範例
├── release-notes/       # 發布說明
├── tests/               # 單元與 Qt 測試
└── main.py              # 程式進入點
```

完整內容請見 [專案結構](docs/PROJECT_STRUCTURE.md)。

## 疑難排解

- 尚未設定：在設定中選擇平台並填入憑據。
- 憑據失效：重新取得 Cookie；MiMo 會先嘗試專用瀏覽器工作階段。
- 請求頻繁或風控：等待後再重新整理，不要持續縮短間隔。
- 資料未更新：檢查目前資料目錄中的 `TokenSpider.log`；全新安裝通常位於 `安裝目錄\data`。
- 未出現視窗：檢查系統匣；程式只允許一個執行個體。

## 版本與 Release

目前版本：`1.13.0`。更新說明與校驗檔請見 [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases)。

## License

本專案採用 [MIT License](LICENSE)，保留著作權與授權聲明後即可使用、修改及散布。
