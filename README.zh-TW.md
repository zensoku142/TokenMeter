<p align="center">
  <a href="./README.md">简体中文</a> |
  <a href="./README.en.md">English</a> |
  <a href="./README.zh-TW.md">繁體中文</a> |
  <a href="./README.ja.md">日本語</a> |
  <a href="./README.ko.md">한국어</a>
</p>

# TokenMeter — Windows AI Token 用量與訂閱額度監控工具

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
  <a href="docs/images/readme-hero.webp"><img src="docs/images/readme-hero.webp" alt="TokenMeter：Codex 額度、DeepSeek 今日分時與餘額、懸浮球及 VPet 桌寵（示範資料）" width="960"></a>
</p>

真實元件截圖，介面為簡體中文，使用示範資料。[查看原圖與來源](docs/images/readme/README.md)。

TokenMeter 是一款適用於 Windows 10/11 的輕量級 AI Token 用量與訂閱額度監控工具：可查看 Codex、Cursor 的已用與剩餘額度、重設時間，以及 DeepSeek、Xiaomi MiMo、NayutoAI 的 Token 用量、API 費用、帳戶餘額和歷史趨勢。

## 功能

- **訂閱額度**：Codex / Cursor 的已用與剩餘比例、重設時間；Codex 另有近 7 天 Token、年度活動和使用統計。
- **API 用量**：DeepSeek / MiMo / NayutoAI 的費用與餘額、今日分時圖、Token 組成和歷史趨勢。
- **懸浮顯示**：額度水球或餘額顯示，支援拖曳、滾輪縮放、貼邊隱藏和系統匣常駐。
- **外觀與語言**：淺色、深色及系統主題，可調整主題色和透明度；支援簡中、繁中、英語、日語、韓語。
- **擷取與快取**：預設只更新目前平台，可選背景同步；支援離線快取、DeepSeek 峰谷提示及 MiMo Cookie 取得與續期。
- **桌面整合**：開機自動啟動、自動更新、資料目錄遷移，以及選用的 VPet 桌寵擴充套件。

## 安裝與設定

需要 Windows 10 / 11 和至少一個支援的平台帳戶。

1. 從 [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases/latest) 下載並安裝 `TokenMeter-Setup-vX.Y.Z-x64.exe`；校驗檔為 `SHA256SUMS.txt`。
2. 點擊懸浮球，在「設定」選擇平台。Codex / Cursor 可讀取本機登入；DeepSeek 填寫憑據或選用的 API Key，MiMo 可一鍵取得 Cookie，NayutoAI 使用其平台憑據。
3. 設定自動儲存，預設每 60 秒更新。主題和語言位於「外觀」，啟動與貼邊選項位於「懸浮與啟動」。

> 資料依賴平台介面與登入狀態，介面變更或風控可能暫時影響取得。請只使用自己的帳戶憑據。

## VPet 桌寵（選用）

主安裝程式不含桌寵。在「設定 → 桌寵」下載擴充套件，完整安裝後再啟用；無需另裝 .NET。啟用後桌寵取代懸浮球，停用或解除安裝後恢復球體，不影響帳戶與面板。

- 支援輕觸互動、拖曳縮放、自主活動和貼邊額度氣泡；按兩下氣泡可開啟用量面板。
- 右鍵選單可設定氣泡顯示方式，以及預設關閉的喝水、休息提醒；桌寵選單目前為簡體中文。
- 精簡版不含餵食、工作、養成、Steam 或連線功能。擴充套件可獨立更新，主程式結束時桌寵一同結束。

實作細節與獨立建置見 [桌寵開發說明](pet_host/README.md)，使用素材前請閱讀 [來源與授權](pet_host/THIRD_PARTY_NOTICES.md)。

## 資料、隱私與更新

- 資料預設存於 `安裝目錄\data`，可在設定中遷移。舊版升級採複製遷移，保留原目錄；歷史快取於本機 SQLite。
- API Key、Bearer Token 與 Cookie 存入 Windows 認證管理員，不寫入設定或記錄檔；桌寵只接收顯示欄位，不接收憑據。
- 更新套件通過 SHA256 驗證後安裝，保留資料與捷徑。預設解除安裝也保留 `data`，請確認不再需要後再手動刪除。SHA256 驗證不等同於發布簽章。

## 從原始碼執行

需要 Python 3.11+。原始碼版與安裝版共用單一執行個體限制，請先結束已執行的 TokenMeter。

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
<summary>開發、測試與建置</summary>

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
pyright
python -m pip install -r requirements-build.txt
python scripts/build_release.py
```

Qt 測試需要可用的 Windows 桌面工作階段；產生安裝程式需要 Inno Setup。桌寵建置需要 .NET SDK 8+，詳見 [桌寵開發說明](pet_host/README.md)。

[專案結構](docs/PROJECT_STRUCTURE.md) · [設定範例](examples/config.example.py)（無需複製為 `config.py`）

</details>

## 常見問題

- 無視窗：檢查系統匣，並確認未重複啟動。
- 憑據失效或請求受限：更新登入 / Cookie，或稍後再更新資料。
- 資料異常：查看目前資料目錄的 `TokenSpider.log`；回報問題前請移除敏感資訊。

## 版本

主程式 `1.15.1`，選用桌寵擴充套件 `0.2.0`。更新記錄與校驗檔見 [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases)。

## 授權與致謝

TokenMeter 自有程式碼採用 [MIT License](LICENSE)。

桌寵核心及預設角色、動畫來自 [LorisYounger/VPet](https://github.com/LorisYounger/VPet)，感謝上游作者及貢獻者。核心採用 [Apache-2.0](third_party/VPet/LICENSE)；角色與動畫著作權歸虚拟主播模拟器制作组所有，適用單獨授權，不屬於本專案的 MIT 授權範圍。詳見 [第三方聲明](pet_host/THIRD_PARTY_NOTICES.md)。
