# 起動と保守の手引き

起動、保存先の切り替え、バックアップと復旧、スキーマ更新のときに何が起きるかをまとめる。
利用実績にもとづく手順ではなく、実装とテストから導いた手引きである(現在の利用状況は README を参照)。

## 起動と停止

```bash
streamlit run app.py          # http://localhost:8501 が開く
```

停止は端末で Ctrl+C。サーバーは `127.0.0.1` にしか待ち受けないので、同じ LAN の
他の端末からは見えない（`.streamlit/config.toml`）。

## 保存先

| 保存先 | 指定 | 必要な依存 |
|---|---|---|
| SQLite（既定） | 指定なし → `data/shukatsu.db` | なし |
| SQLite の別ファイル | `SHUKATSU_DB=data/another.db` | なし |
| PostgreSQL | `SHUKATSU_DB=postgresql://user:pass@127.0.0.1:5432/shukatsu` | `pip install -e ".[postgres]"` |

`data/` は `.gitignore` 済みで、個人の選考データがリポジトリに入ることはない。

## 環境変数

| 変数 | 既定 | 用途 |
|---|---|---|
| `SHUKATSU_DB` | `data/shukatsu.db` | 保存先。SQLite のファイルパスか `postgresql://...` |
| `ANTHROPIC_API_KEY` | なし | 設定されている場合だけ、添削の実行先に Claude API が現れる。アプリは値を読まず保存もしない（SDK が解決する） |
| `SHUKATSU_REVIEW_MODEL` | `claude-opus-5` | 添削で Claude API に送るときのモデル名 |
| `SHUKATSU_TEST_DSN` | なし | テストを PostgreSQL に対して走らせる場合の接続先（開発時のみ） |

手元で PostgreSQL を試すときは同梱の `docker-compose.yml` を使う。待ち受けは `127.0.0.1` のみ。

```bash
docker compose up -d
pip install -e ".[dev,postgres]"
SHUKATSU_DB=postgresql://shukatsu:devpass@127.0.0.1:5432/shukatsu streamlit run app.py
```

## バックアップと復旧

### SQLite

アプリを止めてから、`data/shukatsu.db` を丸ごとコピーする。1ファイルなのでこれで完結する。

```bash
cp data/shukatsu.db backup/shukatsu-$(date +%Y%m%d).db
```

復旧は、アプリを止めた状態でコピーを元の場所に戻すだけ。

### PostgreSQL

```bash
pg_dump  -h 127.0.0.1 -U shukatsu shukatsu > backup/shukatsu-$(date +%Y%m%d).sql   # 取得
psql     -h 127.0.0.1 -U shukatsu shukatsu < backup/shukatsu-YYYYMMDD.sql          # 復旧
```

## スキーマの更新で起きること

- スキーマは `src/shukatsu_tracker/db/migrations/NNN_name.sql` に版ごとに置いてある
- 接続時に未適用の版だけを順に流し、適用した版を `schema_migrations` テーブルに記録する。
  起動のたびに自動で行われるので、手で流す操作はない
- 適用は1版ずつトランザクションで囲む。途中で失敗した版は巻き戻り、記録も残らない
- 複数のプロセスが同時に起動しても、記録の衝突で起動に失敗しないようにしてある
- **戻す仕組みは用意していない**。版を上げる前に上のバックアップを取り、戻したければそれを戻す

新しい版を足すときの手順は [CONTRIBUTING.md](../CONTRIBUTING.md) の「スキーマ変更は新しい migration ファイルで行う」を参照。

## 表示用データ

README のスクリーンショットは実データではなく、`scripts/demo_data.py` が作る架空のデータで撮る。

```powershell
python scripts/demo_data.py
$env:SHUKATSU_DB="data/demo.db"; streamlit run app.py
```

画面を変えたら `python scripts/capture_screenshots.py` で撮り直す（`pip install playwright` が必要）。

## 困ったとき

| 症状 | 原因と対処 |
|---|---|
| 企業を追加できず、同じ名前があると言われる | 企業名は一意。企業管理で既存の行を編集する |
| 「データベースに接続できませんでした」と出る | 続けて理由が表示される。「追加の依存が必要」なら `psycopg` が未インストールなので `pip install -e ".[postgres]"` を実行する。「PostgreSQL に接続できませんでした」ならサーバーの起動と `SHUKATSU_DB` の接続文字列を、「SQLite のファイルを開けませんでした」なら保存先のパスと書き込み権限を、「対応していない接続先」なら `SHUKATSU_DB` の書き方を確認する。接続先のホスト名や利用者名は画面に出さない |
| 添削の実行先に Claude API が出ない | 環境変数 `ANTHROPIC_API_KEY` が未設定。設定しても、アプリはキーを保存しない |
| 読み取れない書式の締切がある | 画面上で警告される。そのまま開いても消えることはなく、編集して保存したときだけ書き換わる |
| 保存したら「他の場所で更新されたため反映していません」と出る | 別のタブや端末で先に更新された行。古い表示への入力は反映されず、最新の値が表示される。その値を見てから編集し直す |
