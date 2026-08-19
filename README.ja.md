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
  <a href="https://github.com/zensoku142/TokenMeter/releases/latest"><strong>最新版をダウンロード</strong></a> ·
  <a href="https://github.com/zensoku142/TokenMeter/stargazers">Star で応援</a> ·
  <a href="https://github.com/zensoku142/TokenMeter/discussions">フィードバックと議論</a>
</p>

<p align="center">
  <strong>Windows 向け AI Token 使用量・コスト・残高モニター</strong><br>
  <sub>Codex、Cursor、DeepSeek、Xiaomi MiMo、NayutoAI の使用量モニター。</sub>
</p>

<p align="center">
  <img src="docs/images/readme-hero-v1.12.0.webp" alt="TokenMeter 製品画面の概要" width="960">
</p>

TokenMeter は、Codex、Cursor、DeepSeek、Xiaomi MiMo、NayutoAI のサブスクリプション枠、Token 消費量、API コスト、残高、履歴傾向を確認する軽量な Windows デスクトップツールです。システムトレイに常駐し、フローティングウィジェットと展開可能な詳細パネルを提供します。

## 機能

- Codex、Cursor、DeepSeek、Xiaomi MiMo、NayutoAI に対応し、プロバイダーごとにキャッシュを分離。既定では現在のプロバイダーだけを更新し、設定で選択したプロバイダーのみバックグラウンド取得します。
- ドラッグ、画面端への吸着、位置記憶、フォーカス喪失時の折りたたみに対応。
- ライト、ダーク、Windows システム連動テーマ。
- 残高、Token 使用量、コスト推移、モデル統計、時間帯グラフ、年間アクティビティヒートマップ。
- DeepSeek のピーク料金通知、専用 Chrome セッションによる MiMo Cookie の取得と更新。
- 通信障害時も最後の成功データを表示し、履歴をローカル SQLite に保存。
- API Key、Bearer Token、Cookie は Windows 資格情報マネージャーに保存。
- データディレクトリ移行、自動更新、単一インスタンス実行。

## 動作要件

- Windows 10 または Windows 11。ソース実行には Python 3.11+。
- 対応サービスのアカウントが 1 つ以上必要です。Codex と Cursor はローカルのログイン情報を利用でき、DeepSeek、MiMo、NayutoAI は各サービスの資格情報を使用します。
- 公式残高 API 用の DeepSeek API Key は任意。

> [!IMPORTANT]
> 使用量データは Web コンソールのエンドポイントに依存し、MiMo Cookie には `api-platform_ph` が必要です。API やリスク制御の変更で一時的に取得できない場合があります。必ず自分の資格情報だけを安全に使用してください。

## ダウンロード

[GitHub Releases](https://github.com/zensoku142/TokenMeter/releases/latest) から `TokenMeter-Setup-vX.Y.Z-x64.exe` をダウンロードし、必要に応じて `SHA256SUMS.txt` を照合します。インストーラーで保存先を選択し、デスクトップまたはスタートメニューのショートカットから起動してください。既定の保存先は `%LOCALAPPDATA%\Programs\TokenMeter` です。

## クイックスタート

```powershell
git clone https://github.com/zensoku142/TokenMeter.git
cd TokenMeter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade "pip>=26.1.2"
python -m pip install -r requirements.txt
python main.py
```

## 初回設定

1. アプリを起動し、フローティングウィジェットをクリックします。
2. 「設定」で DeepSeek または Xiaomi MiMo を選択します。
3. Bearer Token、Cookie、または任意の DeepSeek API Key を入力します。MiMo の Cookie 取得機能は `api-platform_ph` も自動抽出します。
4. 保存して更新します。既定の更新間隔は 60 秒です。

`examples/config.example.py` は項目説明専用で、`config.py` へコピーする必要はありません。旧 `config.py` は初回起動時に可能な範囲で移行されます。

## ローカルデータとプライバシー

新規インストールでは `インストール先\data` にデータを保存します。旧 TokenSpider からの更新時は `%APPDATA%\TokenSpider` をコピーして設定と SQLite を検証し、成功後にのみ切り替えます。旧ディレクトリは移動・削除されず、失敗時もそのまま使用して起動します。資格情報は `TokenMeter/`、`TokenSpider/`、`TokenScope/` の順で Windows 資格情報マネージャーから読み取ります。

## 自動更新

更新時は `TokenMeter-Setup-vX.Y.Z-x64.exe` と `SHA256SUMS.txt` のみをダウンロードし、SHA256 検証後に元のインストール先へサイレント上書きします。固定 AppId により `data` とショートカットは維持され、失敗時は旧バージョンを同じショートカットから起動できます。アンインストールでは既定でプログラムとショートカットだけを削除し、`data` は残します。

## テスト

```powershell
python -m pytest -q
```

Qt テストは利用可能な Windows デスクトップセッションでの実行を推奨します。

## ビルド

```powershell
python -m pip install pyinstaller
.\.venv\Scripts\pyinstaller.exe --clean --noconfirm packaging\pyinstaller\TokenMeter.spec
python scripts/build_release.py
```

スクリプトは `dist\TokenMeter\` の onedir 構成を生成します。Inno Setup がある環境では `dist-installer\TokenMeter-Setup-vX.Y.Z-x64.exe` と `SHA256SUMS.txt` も生成します。

## プロジェクト構成

```text
TokenMeter/
├── api/                 # API、Provider、料金ルール
├── config/              # 設定、資格情報、移行、実行時状態
├── core/                # アプリ ID と共有メタデータ
├── data/                # データディレクトリ、集約、SQLite 履歴
├── updater/             # 更新クライアントと単独更新ツール
├── ui/                  # PySide6 UI
├── packaging/           # PyInstaller、インストーラー、Windows リソース
├── scripts/             # ビルド / リリース自動化
├── docs/                # プロジェクト構成と README 画像
├── examples/            # 設定例
├── release-notes/       # リリースノート
├── tests/               # 単体 / Qt テスト
└── main.py              # エントリーポイント
```

詳細は [プロジェクト構成](docs/PROJECT_STRUCTURE.md) を参照してください。

## トラブルシューティング

- 未設定：設定でプロバイダーと資格情報を入力してください。
- 資格情報の期限切れ：Cookie を再取得してください。MiMo は最初に専用ブラウザーセッションを試します。
- レート制限：しばらく待ってから更新し、間隔を繰り返し短縮しないでください。
- データが古い：現在のデータディレクトリ（新規インストールでは通常 `インストール先\data`）にある `TokenSpider.log` を確認してください。
- ウィンドウがない：システムトレイを確認してください。実行できるのは 1 インスタンスだけです。

## バージョンと Release

現在のバージョン：`1.13.1`。変更履歴とチェックサムは [GitHub Releases](https://github.com/zensoku142/TokenMeter/releases) を参照してください。

## License

本プロジェクトは [MIT License](LICENSE) で提供されます。著作権表示とライセンス表示を保持すれば、利用・変更・再配布できます。
