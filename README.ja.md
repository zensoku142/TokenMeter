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
  <sub>Codex · Claude · Cursor · DeepSeek · MiMo · NayutoAI</sub>
</p>

TokenMeter は Windows 10/11 向けの軽量な AI 使用量モニターです。6 サービスの契約利用枠、リセット時刻、API コスト、残高と履歴を、フローティングウィジェット、ライトリング、ペットで確認できます。

<table>
  <tr>
    <td align="center" width="46%"><a href="docs/images/readme-hero.webp"><img src="docs/images/readme-hero.webp" alt="製品の全体像" width="440"></a><br>製品の全体像</td>
    <td align="center" width="46%"><a href="site/assets/panel-token-speed.png"><img src="site/assets/panel-token-speed.png" alt="Token 速度" width="440"></a><br>Token 速度</td>
    <td align="center" width="8%"><a href="site/assets/light-ring.png"><img src="site/assets/light-ring.png" alt="ライトリング" width="42"></a><br>ライトリング</td>
  </tr>
</table>

実際のコンポーネント画面です。中国語 UI とデモデータを使用。クリックで拡大できます。[画像と出典](docs/images/readme/README.md)。

現在のバージョン：**v1.17.3** · ペット **v0.3.2** · [GitHub Pages](https://zensoku142.github.io/TokenMeter/)

## 機能

- **利用枠と残高**：Codex / Claude Code / Cursor の契約利用枠、WorkBuddy のクレジット、DeepSeek / MiMo / NayutoAI の費用・残高・履歴。通貨・クレジット・Token は分けて表示します。
- **統計と速度**：WorkBuddy の当月日別クレジットとモデル別クレジット、Codex の時間別表示、Codex / Claude / ZCode のローカル統計・絞り込み・Excel 出力。Codex / NayutoAI はモデルの出力速度も表示します。
- **一覧と複数アカウント**：複数サービスの一覧と最前面の 2 サービス表示。複数アカウントは既定でオフ。個別監視は既定の接続や CLI ログインを上書きしません。
- **CC Switch**：複数アカウントと CC 連携を有効にすると、管理下の Codex OAuth アカウントを検出・自動取得。トークン更新・照会は CC が担当し、切断時はキャッシュを保持します。
- **フローティング表示**：従来の利用枠・残高ウィジェットとライトリング。ドラッグ、サイズ変更、画面端への移動に対応し、リングは複数サービス・アカウント、順序・色・幅を設定できます。
- **取得と通知**：バックグラウンド同期、失敗時の間隔延長、キャッシュ、時間帯別料金ヒント、Cookie 取得。残量低下・回復、消費予測、Codex リセット情報の通知と通知休止時間も選べます。
- **外観と連携**：明暗・システムテーマ、色、透明度、5 言語、サービス検索・お気に入り、自動起動・更新、データ移行。

ローカル統計のアカウント帰属は不明で、利用枠や請求額ではありません。速度は最初の応答待ちを含む推定値で、データ不足時は不明です。コミュニティ情報は個人の利用枠回復を保証しません。

<details>
<summary>操作と接続の詳細</summary>

- リングをドラッグして移動、アイコンを長押しして上下にドラッグすると並べ替え、Ctrl＋ホイールで幅を変更。表示元は「取得と統計 → バックグラウンド取得」で選択します。
- 複数アカウントと CC 連携は個別に停止でき、設定は保持します。CC 連携は内部コマンドに依存し、通常起動済みの場合は一度トレイから完全終了してから更新してください。CC 未設定では独立した複数アカウントのログイン・更新はできません。
- 予測には同じ周期の有効なサンプルが 10 分以上必要です。回復通知には残量低下通知の有効化と取得結果での確認が必要です。通知状態は再起動後も保持し、接続を削除しても CLI ログインと履歴は残ります。

</details>

## インストールと設定

Windows 10 / 11 と、対応サービスのアカウントが 1 つ以上必要です。

1. [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases/latest) から `TokenMeter-Setup-vX.Y.Z-x64.exe` をダウンロードしてインストールします。チェックサムは `SHA256SUMS.txt` にあります。
2. フローティングウィジェットをクリックし、「設定」でサービスを選択します。Codex / Cursor はローカルのログイン情報を利用できます。DeepSeek は資格情報または任意の API Key、MiMo は Cookie 取得機能、NayutoAI は専用の資格情報を使用します。
3. 設定は自動保存され、既定の更新間隔は 60 秒です。テーマと言語は「外観」、起動と画面端の動作はフローティング・起動設定で変更できます。

> データはサービスの API とログイン状態に依存します。API やアクセス制限の変更で取得できなくなる場合があります。自分の資格情報のみを使用してください。

## デスクトップペット（任意）

<table>
  <tr>
    <td align="center" width="33%"><a href="docs/images/readme/pet-vpet-current.png"><img src="docs/images/readme/pet-vpet-current.png" alt="Loris（VPet）" width="160"></a><br>Loris（VPet）</td>
    <td align="center" width="33%"><a href="site/assets/pet-whale-transparent.png"><img src="site/assets/pet-whale-transparent.png" alt="鯨の少女" width="160"></a><br>鯨の少女</td>
    <td align="center" width="34%"><a href="site/assets/pet-bailong.png"><img src="site/assets/pet-bailong.png" alt="小龍娘" width="160"></a><br>小龍娘</td>
  </tr>
</table>

「設定 → ペット」でキャラクターをダウンロードして有効にします。.NET は別途不要です。ウィジェットの代わりに表示するか併用でき、無効にすると元に戻ります。

- **キャラクター**：Loris（VPet）は待機・歩行・インタラクション、[鯨の少女](https://github.com/PC2005-cloud/dsh-pet)は利用枠減少の演出と残高カード、[小龍娘](pet_host/bailong/README.md)は 35 アニメーションと自律移動。
- **操作**：ドラッグ、Ctrl＋ホイールのサイズ変更、テーマ連動の利用枠バブル、既定でオフの水分補給・休憩通知。バブルをダブルクリックしてパネルを開き、複数起動時は位置と表示アカウントを個別保存します。
- **管理**：個別インストール・削除、既定の選択、安定版・プレビュー版の独立更新。最後のキャラクターを削除するとホストも削除し、本体終了時はペットも終了します。

サイズは小龍娘 80–320、ほかは 160–320。メニューは中国語です。育成・給餌・仕事・Steam・オンライン・AI チャットは含みません。鯨の少女の素材は非商用限定。[開発](pet_host/README.md) · [出典とライセンス](pet_host/THIRD_PARTY_NOTICES.md)。

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

本体 `1.17.3`、任意のペット拡張 `0.3.2`。変更履歴とチェックサムは [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases) にあります。

## ライセンスと謝辞

TokenMeter 独自のコードは [MIT License](LICENSE) で提供します。

ペットのコア、既定のキャラクター、アニメーションは [LorisYounger/VPet](https://github.com/LorisYounger/VPet) に由来します。作者と貢献者の皆様に感謝します。コアは [Apache-2.0](third_party/VPet/LICENSE) です。キャラクターとアニメーションの著作権は虚拟主播模拟器制作组に帰属し、別の利用条件が適用されるため、本プロジェクトの MIT ライセンスには含まれません。[第三者の権利に関する声明](pet_host/THIRD_PARTY_NOTICES.md) を参照してください。
