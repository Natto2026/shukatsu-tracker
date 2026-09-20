# 開発ルール

このリポジトリで開発するときの規約。

## 開発フロー

- main への直接コミット禁止。必ず `feature/xxx` か `fix/xxx` ブランチ → Pull Request
- PR 前にローカルで `ruff check .`・`ruff format --check .`・`python -m mypy`・`python -m pytest` をすべて通すこと
- 挙動を変える修正には回帰テストを添える
- マージ後は CHANGELOG.md の Unreleased に追記し、ブランチを削除する

## 絶対に守ること(セキュリティ)

- `data/` 配下・`*.db` をコミットしない(個人の選考データが入っている)
- パスワードを保存する機能を追加しない
- `ai_export.py` の書き出しにマイページ URL・ログイン用メールを含めない
  (tests/test_research_and_export.py の回帰テストが監視している)
- 外部への通信を行う機能を追加する場合は、README のセキュリティ方針の更新とセットで行う
- API キーなどの認証情報をアプリで受け取らない・保存しない。環境変数から SDK に解決させる
- 外部に送る文面は、送信前に必ず画面で確認できるようにする
- 評価の観点(review/criteria/*.toml)を、特定の組織の基準として提示しない
  (tests/test_review_criteria.py が文面を検証している)

## コーディング規約

- ruff の設定(pyproject.toml)に従う。行長 110
- 書式は `ruff format` に任せる。手で折り返さない(CI が `ruff format --check .` で検査する)
- **層を越えない**: app.py → services/ → db/ の一方向。UI から repositories を直接呼ばない
- UI(app.py)にロジックを書かない。集計は analytics.py(純粋関数)へ
- 層をまたぐ受け渡しは models.py の dataclass で行う。dict を持ち回さない
- SQL は db/repositories.py と db/migrations/*.sql だけに書き、値は必ずパラメータ化する
  - 例外はスキーマ管理そのものだけ。db/migrations.py が schema_migrations を作る DDL と
    適用済みバージョンを記録する INSERT、db/database.py が組み立てる INSERT / UPDATE の
    ひな形がこれにあたる。テーブルごとの読み書きをここに足さない
- 列名を動的に組み立てる場合は、リポジトリの `writable` に列挙した名前だけを通す
- **スキーマ変更は新しい migration ファイルで行う**。適用済みの .sql は編集しない
- 書き込みは services/ 側で `transaction()` に包む。repositories を裸で呼ばない
- SQL は `?` で書く。方言差は db/dialects.py に足す。SQL ファイルを方言ごとに分けない
- 画面から書き込むのは、利用者が押したときだけ。描画の途中で保存しない
- 入力欄の key には保存済みの値を含める。古い表示で新しい変更を上書きしないため
- 破壊的な操作には確認を挟み、何が失われるかを書く
- スキーマを足したら、SQLite と PostgreSQL の両方でテストを通す
- コメント・docstring・UI 文言は日本語
- 文書にテスト件数などの数字をハードコードしない(CI ログを正とする)

## スクリーンショット更新

デモデータで撮る(実データ禁止):

```powershell
python scripts/demo_data.py
$env:SHUKATSU_DB="data/demo.db"; streamlit run app.py --server.port 8502 --server.headless true
```
