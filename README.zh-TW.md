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
  <sub>Codex · Claude · Cursor · Copilot · Gemini · DeepSeek · MiMo · OpenRouter · Kimi · MiniMax · ElevenLabs · GLM · Antigravity · NayutoAI</sub>
</p>

<p align="center">
  <a href="docs/images/readme-hero.webp"><img src="docs/images/readme-hero.webp" alt="TokenMeter：Codex 額度、DeepSeek 今日分時與餘額、懸浮球及 VPet 桌寵（示範資料）" width="960"></a>
</p>

真實元件截圖，介面為簡體中文，使用示範資料。[查看原圖與來源](docs/images/readme/README.md)。

TokenMeter 是適用於 Windows 10/11 的輕量級 AI 用量監控工具，提供 15 個獨立平台入口，依平台能力顯示訂閱額度、重設時間、API 費用、餘額與歷史趨勢。

目前版本：**v1.15.0** · VPet **v0.1.4** · [GitHub Pages](https://zensoku142.github.io/TokenMeter/)

## 功能

- **額度與餘額**：支援 Codex、Claude Code、Cursor、GitHub Copilot、GLM / Z.ai、Kimi Coding、MiniMax Token Plan、Gemini CLI 和 ElevenLabs 額度；DeepSeek / MiMo / NayutoAI 用量；OpenRouter 金鑰預算與費用；Moonshot / Kimi API 餘額。Antigravity CLI 讀取明確設定的狀態列快照。缺少的欄位顯示不可用，不混合幣別或預算範圍。
- **總覽與多帳戶**：總覽可見時自動更新已設定平台。「設定 → 多帳戶」獨立保存連線、憑據和監控狀態，不覆蓋預設連線或 CLI 登入。兩個已設定平台也可在置頂小看板中常駐。
- **本機統計與 Excel**：讀取 Codex（含封存）與 Claude 本機日誌，先顯示快取，再背景更新。支援日期、專案、模型篩選、每日比較、圖表縮放，並匯出 `.xlsx` 摘要、明細與統計口徑。本機日誌無法確認帳戶歸屬，不代表訂閱額度或實際帳單；可選擇已可存取的 WSL 目錄，不啟動 WSL。
- **平台管理**：離線品牌圖示、搜尋、收藏及移除應用程式保存的連線；保留 CLI 登入與歷史記錄。
- **更新與提醒**：失敗退避、離線快取、可選低額度與恢復提醒、消耗預測及靜默時段。預測需要同週期至少 10 分鐘的有效樣本；恢復提醒需先開啟低額度提醒，並以實際更新確認。提醒狀態可跨重啟保留。
- **桌面顯示**：可拖曳懸浮球、滾輪縮放、貼邊隱藏、系統匣、明暗與系統主題、顏色與透明度、五種語言、自動更新和選用 VPet。主程式 v1.15.0 搭配桌寵 v0.1.4 可同步額度視窗主題。

[平台設定與限制](docs/provider-support-research-2026-09-05.md) · [Kimi / MiniMax / ElevenLabs](docs/codexbar-provider-coverage-2026-09-05.md) · [Gemini / Claude](docs/gemini-quota-2026-09-05.md) · [Antigravity 狀態列](docs/antigravity-statusline-2026-09-07.md)

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

主程式 `1.15.0`，選用桌寵擴充套件 `0.1.4`。更新記錄與校驗檔見 [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases)。

## 授權與致謝

TokenMeter 自有程式碼採用 [MIT License](LICENSE)。

桌寵核心及預設角色、動畫來自 [LorisYounger/VPet](https://github.com/LorisYounger/VPet)，感謝上游作者及貢獻者。核心採用 [Apache-2.0](third_party/VPet/LICENSE)；角色與動畫著作權歸虚拟主播模拟器制作组所有，適用單獨授權，不屬於本專案的 MIT 授權範圍。詳見 [第三方聲明](pet_host/THIRD_PARTY_NOTICES.md)。
