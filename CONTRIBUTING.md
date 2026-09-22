# 開発ルール

このリポジトリで開発するときの規約。

## ブランチ運用

main / develop / 作業ブランチの3層で運用する。

| ブランチ | 役割 | 変更が入る経路 |
|---|---|---|
| `main` | リリース済みの状態だけを置く。タグ `vX.Y.Z` はここに打つ | develop からのリリース PR、または hotfix の PR |
| `develop` | 次のリリースに向けて統合する場所。CI を常に緑に保つ。GitHub の既定ブランチ | 作業ブランチからの PR |
| `feature/<Issue番号>-<内容>` | Issue 1件 = ブランチ1本。1つの PR で読み切れる大きさに収める | develop から切る |
| `fix/…` `docs/…` `chore/…` | 不具合修正・文書・整備。粒度は feature と同じ | develop から切る |
| `release/X.Y.Z` | リリース準備。バージョン番号と CHANGELOG の見出しを確定させるだけ | develop から切り、main へ PR |
| `hotfix/…` | リリース済み main の緊急修正のみ | main から切り、main へ PR したあと develop にも取り込む |

- main と develop へ直接 push しない。必ず PR を経由する
  (private リポジトリではブランチ保護を設定できないため、規約として守る)
- 対応する Issue がある作業ブランチの名前には Issue 番号を入れる(例: `feature/7-ical-export`)。PR 本文に `Closes #7` を書く
- マージ後は作業ブランチを削除する

この運用は 0.7.0 から適用している。それより前の PR は main 向きで、ブランチ名に Issue 番号も入っていない。

開発しているのは作者1人で、hotfix を実際に切ったことはない。1人なら main だけでも回るが、
業務で使われる運用を自分の手で一通り再現しておくために、この形にしている。

## 開発フロー

1. Issue を立てる(または既存の Issue を選ぶ)。小さな修正は Issue なしで始めてよい
2. develop から作業ブランチを切る。依存は `pip install -e ".[dev]" -c constraints.txt` で入れる
   (CI と同じ版にそろえるため。版を上げるときは、全部通してから `constraints.txt` を書き換える)
3. PR 前にローカルで `ruff check .`・`ruff format --check .`・`python -m mypy`・`python -m pytest` をすべて通す
4. 挙動を変える修正には回帰テストを添える
5. CHANGELOG.md の Unreleased に追記してから、develop への PR を出す
6. CI が緑になったらマージし、作業ブランチを削除する

## リリース手順

1. develop から `release/X.Y.Z` を切る
2. CHANGELOG.md の `## [Unreleased]` を `## [X.Y.Z] - YYYY-MM-DD` にし、新しい空の Unreleased と比較リンクを足す
3. `pyproject.toml` と `src/shukatsu_tracker/__init__.py` のバージョンを揃える
4. main へ PR を出し、CI が緑になったらマージする
5. main に `vX.Y.Z` のタグを打ち、CHANGELOG のその版の本文で GitHub Release を作る
6. main を develop に取り込む PR を出してマージし、両方を同じ状態にする

## 絶対に守ること(セキュリティ)

- `data/` 配下・`*.db` をコミットしない(個人の選考データが入っている)。WAL(`*.db-wal`)も同じ
- `.env` や `.streamlit/secrets.toml` に API キーを書いた場合も、コミットしない(.gitignore で除外している)
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
- 選択肢（constants）と締切の書式の検証は services/ に置く。画面や CSV の取り込みだけに置かない
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

デモデータで撮る(実データ禁止)。画面を変えたら、次の2つを流して撮り直す:

```bash
python scripts/demo_data.py
python scripts/capture_screenshots.py    # pip install playwright が必要(開発時のみ)
```

スクリプトがデモデータでアプリを起動し、`docs/screenshots/` の画像を上書きする。
コミット前に画像を開き、実データやローカルのパスが写っていないことを確認する。
