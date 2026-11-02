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
  <sub>Codex · Claude · Cursor · DeepSeek · MiMo · NayutoAI</sub>
</p>

<p align="center">
  <a href="docs/images/readme-hero.webp"><img src="docs/images/readme-hero.webp" alt="TokenMeter：Codex 額度、DeepSeek 今日分時與餘額、懸浮球、VPet 與鯨魚娘桌寵（示範資料）" width="960"></a>
</p>

產品總覽展示用量面板、懸浮球、VPet 與鯨魚娘；Token 速度、輕底細環和小龍娘截圖見下方對應介紹。圖片均來自真實元件，介面為簡體中文，使用示範資料。[查看原圖與來源](docs/images/readme/README.md)。

TokenMeter 是適用於 Windows 10/11 的輕量級 AI 用量監控工具，提供 6 個獨立平台入口，依平台能力顯示訂閱額度、重設時間、API 費用、餘額與歷史趨勢。可透過懸浮球、輕底細環或桌寵常駐查看用量，也可開啟面板查看分時統計、Token 速度與多帳號額度。

目前版本：**v1.17.1** · 桌寵 **v0.3.2** · [GitHub Pages](https://zensoku142.github.io/TokenMeter/)



## 功能

- **CC Switch Codex 多帳號**：在「設定 → 多帳戶」啟用多帳戶與 CC Switch 連線後，程式啟動即自動發現並更新 CC Switch 管理的 Codex OAuth 帳號，續期與查詢由 CC Switch 處理；暫時斷線時保留快取額度。本機橋接依賴 CC Switch 內部命令，版本變動可能需要適配；若 CC Switch 已在未啟用橋接的情況下執行，請先從系統匣完全結束一次，再重新整理。
- **輕底細環與 Codex 重設動態**：除懸浮球外，也可選擇細環，支援單平台、多平台與多帳號常駐顯示、貼邊吸附及自訂環色。直接拖曳圖示或留白可移動視窗，長按圖示後上下拖曳可排序，順序自動儲存；懸停時按住 Ctrl 滾動滾輪可調整寬度。懸停詳情可顯示社群公開重設消息並選擇開啟提醒；公開消息不代表個人帳號已重設。顯示來源統一在「擷取與統計 → 同時取得」選擇。
- **Codex 今日分時**：依本機工作階段日誌的記錄時間顯示快取命中、新增輸入和輸出 Token，支援模型提示、歷史日期與增量更新。僅包含所選目錄的本機記錄，帳號歸屬未知，不代表訂閱額度或實際帳單。
- **Token 速度**：在 Codex 和 NayutoAI 面板依模型與時間查看輸出速度。速度根據有效請求的輸出 Token 與耗時估算，包含首字等待；資料不足時顯示未知。
- **額度與餘額**：支援 Codex、Claude Code、Cursor 額度；DeepSeek / MiMo / NayutoAI 用量與餘額。缺少的欄位顯示不可用，不混合積分、幣別與 Token。
- **總覽與多帳戶**：總覽可見時自動更新已設定平台。多帳戶預設關閉，可在「設定 → 多帳戶」啟用後獨立保存連線、憑據和監控狀態，不覆蓋預設連線或 CLI 登入。關閉多帳戶可暫停全部帳戶檔案採集與顯示，或單獨關閉 CC Switch 連線以停止連線與自動啟動；重新開啟沿用原設定。兩個已設定平台也可在置頂小看板中常駐。
- **本機統計與 Excel**：讀取 Codex（含封存）與 Claude 本機日誌，先顯示快取，再背景更新。支援日期、專案、模型篩選、每日比較、圖表縮放，並匯出 `.xlsx` 摘要、明細與統計口徑。本機日誌無法確認帳戶歸屬，不代表訂閱額度或實際帳單；可選擇已可存取的 WSL 目錄，不啟動 WSL。
- **平台管理**：離線品牌圖示、搜尋、收藏及移除應用程式保存的連線；保留 CLI 登入與歷史記錄。
- **更新與提醒**：失敗退避、離線快取、可選低額度與恢復提醒、消耗預測及靜默時段。預測需要同週期至少 10 分鐘的有效樣本；恢復提醒需先開啟低額度提醒，並以實際更新確認。提醒狀態可跨重啟保留。
- **桌面顯示**：懸浮球以額度水球或餘額顯示用量，支援拖曳、滾輪縮放、貼邊隱藏和系統匣常駐；另有明暗與系統主題、顏色與透明度、五種語言、自動更新和選用多角色桌寵。主程式 v1.17.0 搭配桌寵 v0.3.1 可同步額度視窗主題。

<p align="center">
  <a href="site/assets/panel-token-speed.png"><img src="site/assets/panel-token-speed.png" alt="Token 速度 K 線面板（簡體中文介面，示範資料）" width="960"></a>
</p>

Token 速度面板展示模型輸出速度隨時間的變化，可搭配原有額度、餘額與分時統計使用。

<p align="center">
  <a href="site/assets/light-ring.png"><img src="site/assets/light-ring.png" alt="輕底細環同時顯示三個平台額度（示範資料）" width="58"></a>
</p>

輕底細環提供精簡的多平台常駐顯示；原有懸浮球仍可在設定中選擇。

## 安裝與設定

需要 Windows 10 / 11 和至少一個支援的平台帳戶。

1. 從 [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases/latest) 下載並安裝 `TokenMeter-Setup-vX.Y.Z-x64.exe`；校驗檔為 `SHA256SUMS.txt`。
2. 點擊懸浮球，在「設定」選擇平台。Codex / Cursor 可讀取本機登入；DeepSeek 填寫憑據或選用的 API Key，MiMo 可一鍵取得 Cookie，NayutoAI 使用其平台憑據。
3. 設定自動儲存，預設每 60 秒更新。主題和語言位於「外觀」，啟動與貼邊選項位於「懸浮與啟動」。

> 資料依賴平台介面與登入狀態，介面變更或風控可能暫時影響取得。請只使用自己的帳戶憑據。

## 桌寵（選用）

主安裝程式不含桌寵。在「設定 → 桌寵」下載擴充套件，完整安裝後再啟用；無需另裝 .NET。啟用後預設由桌寵取代懸浮球；開啟「同時顯示懸浮球」可讓兩者一起顯示，設定自動儲存並立即生效。停用或解除安裝桌寵後恢復球體，不影響帳戶與面板。

- 支援輕觸互動、拖曳縮放、自主活動和貼邊額度氣泡；按兩下氣泡可開啟用量面板。
- 啟動前可選擇蘿莉斯（VPet）、[鯨魚娘](https://github.com/PC2005-cloud/dsh-pet)或[小龍娘](pet_host/bailong/README.md)，已安裝角色可設為預設。蘿莉斯提供待機、走動和互動；鯨魚娘保留動作播放、額度扣減回饋與餘額卡片，素材僅限非商業用途；小龍娘完整接入 35 段動畫，共用現有宿主、用量氣泡、顯示來源與多開，支援自主動作和移動，不包含 AI 對話或歷史會話。設定頁提供角色縮圖與可安裝的相容版本。
- 支援只下載所選角色，之後可單獨新增或移除 VPet、鯨魚娘和小龍娘資源；移除最後一個角色時同時解除安裝共用宿主。桌寵更新可獨立選擇正式版或體驗版通道。
- 可從桌寵右鍵選單即時增開已安裝角色，或單獨關閉某隻桌寵。額外執行個體各自記住位置；每隻桌寵可分別跟隨主程式目前平台、已設定平台的預設帳戶，或已儲存的帳戶檔案。
- 懸停桌寵時按住 Ctrl 滾動滾輪即可縮放；小龍娘範圍為 80–320，其餘角色為 160–320。
- 右鍵選單可設定氣泡顯示方式，以及預設關閉的喝水、休息提醒；桌寵選單目前為簡體中文。
- 精簡版不含餵食、工作、養成、Steam 或連線功能。擴充套件可獨立更新，主程式結束時桌寵一同結束。

實作細節與獨立建置見 [桌寵開發說明](pet_host/README.md)，使用素材前請閱讀 [來源與授權](pet_host/THIRD_PARTY_NOTICES.md)。

![鯨魚娘桌寵角色展示](site/assets/pet-whale-transparent.png)

鯨魚娘素材來源：[PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)，僅限非商業用途。

<p align="center">
  <a href="site/assets/pet-bailong.png"><img src="site/assets/pet-bailong.png" alt="小龍娘桌寵角色展示" width="220"></a>
</p>

小龍娘與蘿莉斯、鯨魚娘使用相同的安裝與切換入口，可依需求單獨下載。動作與素材說明見[小龍娘角色介紹](pet_host/bailong/README.md)。

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

主程式 `1.17.1`，選用桌寵擴充套件 `0.3.2`。更新記錄與校驗檔見 [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases)。

## 授權與致謝

TokenMeter 自有程式碼採用 [MIT License](LICENSE)。

桌寵核心及預設角色、動畫來自 [LorisYounger/VPet](https://github.com/LorisYounger/VPet)，感謝上游作者及貢獻者。核心採用 [Apache-2.0](third_party/VPet/LICENSE)；角色與動畫著作權歸虚拟主播模拟器制作组所有，適用單獨授權，不屬於本專案的 MIT 授權範圍。詳見 [第三方聲明](pet_host/THIRD_PARTY_NOTICES.md)。
