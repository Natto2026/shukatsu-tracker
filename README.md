# shukatsu-tracker

[![CI](https://github.com/Natto2026/shukatsu-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/Natto2026/shukatsu-tracker/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

選考プロセスをローカルで安全に管理するツールです。締切・選考ステップ・提出した回答を
一元管理し、認証情報を持たない設計と外部送信ゼロを前提に作っています。

![ダッシュボード](docs/screenshots/dashboard.png)

## なぜ作ったか

選考管理はスプレッドシートが定番ですが、実際に使っていて次の課題がありました。

- 締切の見落としが怖い。企業ごとにマイページへ情報が分散し、一覧性がない
- 一度書いた回答が探せない。設問・文字数制限・提出先が結びついていない
- 記録が増えても、どのステップで止まっているのかが見えない

一方で、就活の管理データはマイページのURLや登録メールを含みます。クラウドの
サービスに預けるのも、スプレッドシートに認証情報を並べるのも避けたい。
そこで**データが端末から出ない前提**で、締切管理・回答管理・振り返りをまとめました。

## 機能

| ページ | 内容 |
|---|---|
| ダッシュボード | 7日以内の締切（期限超過は強調表示）、全社の選考状況一覧 |
| 企業管理 | 選考ステップ（ES〜最終面接＋自由追加）、締切・結果の記録、マイページURL管理、企業研究リンクの自動生成（公式・事業内容・IR・クチコミ・選考体験記） |
| ES管理 | 設問と回答のライブラリ。カテゴリ・キーワード検索、文字数制限との照合（超過と8割未満を警告） |
| 分析 | 応募経路別・適性検査タイプ別の通過率、選考ファネル |
| 書き出し | 選考記録と集計を依頼文つき Markdown に書き出す。**アプリは外部と通信せず**、何をどこまで渡すかは書き出し内容を確認して利用者が決める |

### 振り返りの集計

応募経路別・適性検査タイプ別の通過率と、選考ファネルを可視化します。

![分析](docs/screenshots/analytics.png)

## データの扱い（セキュリティ方針）

- データはローカルの `data/shukatsu.db`（SQLite）にのみ保存され、外部送信は一切ありません
- サーバーは `127.0.0.1` のみにバインドし、同一LAN内の他端末からもアクセスできません（`.streamlit/config.toml`）
- `data/` は `.gitignore` 済みで、個人の選考データがリポジトリに混入しない構成です
- **パスワードは保存しない設計**です。平文保存は漏洩リスクになるため、マイページURLと登録メールアドレスのみ管理します
- 書き出し機能にマイページURL・ログイン用メールを含めないことを、回帰テストで保証しています

## セットアップ

```bash
git clone https://github.com/Natto2026/shukatsu-tracker.git
cd shukatsu-tracker
pip install -e ".[dev]"
streamlit run app.py
```

ブラウザで http://localhost:8501 が開きます。

**デモデータで試す**（スクリーンショットと同じ架空データ）:

```powershell
python scripts/demo_data.py
$env:SHUKATSU_DB="data/demo.db"; streamlit run app.py
```

## 設計

UI・ユースケース・永続化を層として分け、上の層が下の層だけを呼ぶ構成にしています。

```
app.py（画面）
   └─ services/（業務ルール・入力検証・トランザクションの単位）
        └─ db/（接続・スキーマ適用・テーブルごとの読み書き）
             └─ SQLite
analytics.py（集計）は DB にも UI にも依存しない純粋関数
```

この分け方にしている理由は3つあります。

- **集計を DB から切り離す**ため。`analytics.py` は `StepView` の列だけを受け取るので、
  テストがデータを組み立てるだけで書け、画面を変えてもロジックのテストが壊れません
- **業務ルールの置き場所を1か所にする**ため。「企業を追加したら既定の選考ステップも
  一緒に入れる」といった規則を UI に書くと、別の入口を作るたびに書き直しになります
- **スキーマを追跡可能にする**ため。テーブル定義は `db/migrations/NNN_name.sql` に置き、
  適用済みのバージョンを DB 側に記録します。既存のデータを作り直さずに列を追加できます

トランザクションは明示的に開きます。sqlite3 の既定は DDL を暗黙コミットしてしまい、
複数テーブルにまたがる書き込みをまとめて巻き戻せないためです。

詳細は [docs/architecture.md](docs/architecture.md)（レイヤー設計・永続化の方針）と
[docs/data_flow.md](docs/data_flow.md)（データの流れ・テーブル設計）を参照してください。
開発規約は [CONTRIBUTING.md](CONTRIBUTING.md) にまとめています。

## 開発

```bash
ruff check .
python -m pytest
```

テストは層ごとの単体テスト（集計・リポジトリ・サービス・スキーマ適用）と、
Streamlit AppTest による画面のスモークテストで構成し、CI（lint + テスト）で
Python 3.11 / 3.13 の両方に対して常時実行しています。
