<p align="center">
  <a href="./README.md">简体中文</a> |
  <a href="./README.en.md">English</a> |
  <a href="./README.zh-TW.md">繁體中文</a> |
  <a href="./README.ja.md">日本語</a> |
  <a href="./README.ko.md">한국어</a>
</p>

# TokenMeter — Windows 向け AI Token 使用量・サブスクリプション枠モニター

<p align="center">
  <a href="https://github.com/zensoku142/TokenMeter/stargazers"><img alt="GitHub Stars" src="https://img.shields.io/github/stars/zensoku142/TokenMeter?style=flat-square"></a>
  <a href="https://github.com/zensoku142/TokenMeter/releases/latest"><img alt="Latest Release" src="https://img.shields.io/github/v/release/zensoku142/TokenMeter?style=flat-square"></a>
  <a href="https://github.com/zensoku142/TokenMeter/releases"><img alt="Release Downloads" src="https://img.shields.io/github/downloads/zensoku142/TokenMeter/total?style=flat-square"></a>
  <a href="https://github.com/zensoku142/TokenMeter/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/zensoku142/TokenMeter/ci.yml?branch=master&style=flat-square&label=CI"></a>
</p>

<p align="center">
  <a href="https://github.com/zensoku142/TokenMeter/releases/latest"><strong>最新版をダウンロード</strong></a> ·
  <a href="https://github.com/zensoku142/TokenMeter/stargazers">Star で応援</a> ·
  <a href="https://github.com/zensoku142/TokenMeter/discussions">フィードバックと議論</a>
</p>

<p align="center">
  <strong>Windows 向け AI Token 使用量・コスト・残高モニター</strong><br>
  <sub>Codex · Claude · Cursor · Copilot · Gemini · DeepSeek · MiMo · OpenRouter · Kimi · MiniMax · ElevenLabs · GLM · Antigravity · NayutoAI</sub>
</p>

<p align="center">
  <a href="docs/images/readme-hero.webp"><img src="docs/images/readme-hero.webp" alt="TokenMeter：Codex の利用枠、DeepSeek の本日の時間帯別使用量と残高、フローティング表示、VPet（デモデータ）" width="960"></a>
</p>

実際のコンポーネントを撮影した画面です。表示は中国語、数値はデモデータです。[元画像と出典](docs/images/readme/README.md)。

TokenMeter は Windows 10/11 向けの軽量な AI 使用量モニターです。15 の独立した接続先を備え、各サービスの対応範囲に応じて契約の利用枠、リセット時刻、API コスト、残高と履歴を表示します。

現在のバージョン：**v1.15.0** · VPet **v0.1.4** · [GitHub Pages](https://zensoku142.github.io/TokenMeter/)

## 機能

- **利用枠と残高**：Codex、Claude Code、Cursor、GitHub Copilot、GLM / Z.ai、Kimi Coding、MiniMax Token Plan、Gemini CLI、ElevenLabs の利用枠、DeepSeek / MiMo / NayutoAI の使用量、OpenRouter のキー予算とコスト、Moonshot / Kimi API の残高に対応。Antigravity CLI は明示的に設定したステータスラインのスナップショットを読み取ります。未提供の値は利用不可とし、通貨や予算の範囲を混ぜません。
- **一覧と複数アカウント**：一覧の表示中は設定済みサービスを自動更新。「設定 → 複数アカウント」で接続・認証情報・監視状態を個別管理し、既定の接続や CLI ログインを上書きしません。設定済みの 2 サービスを最前面の小型ダッシュボードにも表示できます。
- **ローカル統計と Excel**：Codex（アーカイブを含む）と Claude のローカルログを読み取り、キャッシュを先に表示してバックグラウンド更新。日付・プロジェクト・モデルの絞り込み、日別比較、グラフのズーム、集計・明細・集計基準の `.xlsx` 出力に対応。ログのアカウント帰属は不明で、契約の残量や実際の請求額ではありません。アクセス可能な WSL ディレクトリも選択できますが WSL は起動しません。
- **接続先管理**：オフラインのブランドアイコン、検索、お気に入り、アプリが保存した接続の削除。CLI ログインと履歴は保持します。
- **更新と通知**：失敗時の更新間隔延長、オフラインキャッシュ、任意の残量低下・回復通知、消費予測と通知休止時間。予測には同じ周期の有効なサンプルが 10 分以上必要です。回復通知は残量低下通知が有効で、取得結果で回復を確認した場合に送ります。通知状態は再起動後も保持します。
- **デスクトップ表示**：ドラッグ、ホイールによるサイズ変更、画面端での非表示、トレイ、明暗・システムテーマ、色と透明度、5 言語、自動更新、任意の VPet。アプリ v1.15.0 とペット v0.1.4 の組み合わせで利用枠ウィンドウのテーマを同期します。

[接続設定と制限（中国語）](docs/provider-support-research-2026-09-05.md) · [Kimi / MiniMax / ElevenLabs](docs/codexbar-provider-coverage-2026-09-05.md) · [Gemini / Claude](docs/gemini-quota-2026-09-05.md) · [Antigravity](docs/antigravity-statusline-2026-09-07.md)

## インストールと設定

Windows 10 / 11 と、対応サービスのアカウントが 1 つ以上必要です。

1. [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases/latest) から `TokenMeter-Setup-vX.Y.Z-x64.exe` をダウンロードしてインストールします。チェックサムは `SHA256SUMS.txt` にあります。
2. フローティングウィジェットをクリックし、「設定」でサービスを選択します。Codex / Cursor はローカルのログイン情報を利用できます。DeepSeek は資格情報または任意の API Key、MiMo は Cookie 取得機能、NayutoAI は専用の資格情報を使用します。
3. 設定は自動保存され、既定の更新間隔は 60 秒です。テーマと言語は「外観」、起動と画面端の動作はフローティング・起動設定で変更できます。

> データはサービスの API とログイン状態に依存します。API やアクセス制限の変更で取得できなくなる場合があります。自分の資格情報のみを使用してください。

## VPet デスクトップペット（任意）

本体のインストーラーにペットは含まれません。「設定 → ペット」で拡張をダウンロードし、インストール完了後に有効にします。.NET の別途インストールは不要です。ペットはフローティングウィジェットを置き換え、無効化・削除すると元に戻ります。アカウントやパネルには影響しません。

- タッチ操作、ドラッグ、サイズ変更、自律動作、画面端の残量バブルに対応。バブルをダブルクリックすると使用量パネルが開きます。
- 右クリックメニューでバブルの表示方法や、水分補給・休憩のリマインダーを設定できます。リマインダーは既定でオフ、ペットのメニューは現在中国語のみです。
- 軽量版には給餌、仕事、育成、Steam、オンライン機能はありません。拡張は単独で更新でき、本体と同時に終了します。

実装とビルドは [ペット開発ガイド](pet_host/README.md)、素材の利用条件は [出典とライセンス](pet_host/THIRD_PARTY_NOTICES.md) を参照してください。

## データ、プライバシー、更新

- データは既定で `インストール先\data` に保存され、設定から移行できます。旧版からの移行はコピー方式で元のディレクトリを保持し、履歴はローカル SQLite に保存します。
- API Key、Bearer Token、Cookie は Windows 資格情報マネージャーに保存し、設定ファイルやログには記録しません。ペットには表示用データのみを渡し、資格情報は渡しません。
- 更新は SHA256 検証後に適用し、データとショートカットを保持します。アンインストールでも `data` は残るため、不要と確認してから手動で削除してください。チェックサムはリリース署名とは異なります。

## ソースから実行

Python 3.11+ が必要です。ソース版とインストール版は同時に起動できないため、実行中の TokenMeter を先に終了してください。

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
<summary>開発・テスト・ビルド</summary>

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
pyright
python -m pip install -r requirements-build.txt
python scripts/build_release.py
```

Qt テストには利用可能な Windows デスクトップセッション、インストーラー生成には Inno Setup が必要です。ペットのビルドには .NET SDK 8+ が必要です。[ペット開発ガイド](pet_host/README.md) を参照してください。

[プロジェクト構成](docs/PROJECT_STRUCTURE.md) · [設定例](examples/config.example.py)（`config.py` へのコピーは不要）

</details>

## トラブルシューティング

- ウィンドウがない：システムトレイと二重起動を確認してください。
- 資格情報の期限切れ・アクセス制限：ログインや Cookie を更新するか、時間を置いて再試行してください。
- データの問題：現在のデータディレクトリにある `TokenSpider.log` を確認し、報告前に機密情報を除去してください。

## バージョン

本体 `1.15.0`、任意のペット拡張 `0.1.4`。変更履歴とチェックサムは [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases) にあります。

## ライセンスと謝辞

TokenMeter 独自のコードは [MIT License](LICENSE) で提供します。

ペットのコア、既定のキャラクター、アニメーションは [LorisYounger/VPet](https://github.com/LorisYounger/VPet) に由来します。作者と貢献者の皆様に感謝します。コアは [Apache-2.0](third_party/VPet/LICENSE) です。キャラクターとアニメーションの著作権は虚拟主播模拟器制作组に帰属し、別の利用条件が適用されるため、本プロジェクトの MIT ライセンスには含まれません。[第三者の権利に関する声明](pet_host/THIRD_PARTY_NOTICES.md) を参照してください。
