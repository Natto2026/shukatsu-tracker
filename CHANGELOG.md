# Changelog

このプロジェクトの変更履歴。[Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) 形式、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に従う。
0.1.0 と 0.2.0 は履歴を整理する前の版で、対応するタグがないため見出しをリンクにしていない。

## [Unreleased]

### Fixed
- **COMMIT が一度失敗すると、以後の書き込みが確定も巻き戻しもされない問題を修正**。失敗時に境界の深さを戻しておらず、次の境界が入れ子と誤認されて BEGIN も ROLLBACK も発行されなかった。開いたままのトランザクションが書き込みロックを握り続け、別のセッションの起動も「database is locked」で止まっていた。COMMIT・ROLLBACK は失敗しても深さを戻し、COMMIT が失敗したら ROLLBACK を試みてから元の例外を上げる

## [0.8.0] - 2026-09-21

### Added
- スプレッドシートから書き出した CSV の取り込み（#8）。「取り込み」の画面で CSV を選ぶと、企業と選考ステップをまとめて登録できる
  - 1行 = 選考ステップ1件の縦持ち。列は見出し名で対応づけ、日本語の別名（会社名・締切日・選考結果など）を受ける。文字コードは UTF-8（BOM あり・なし）と Shift-JIS
  - 書き込む前に要約（追加される企業とステップの数、取り込まない行と理由、取り込まない列）を出し、押されたときに1つのトランザクションで書く。途中で失敗したら何も残らない。要約のあとで登録内容が変わっていたら、書かずに要約を出し直す
  - 読めない締切や選択肢にない値を、空欄や既定値に置き換えて取り込むことはしない。行番号と理由を出す。問題のある行を含む企業は丸ごと取り込まない（登録済みの企業は上書きしないため、半分だけ入れると直した CSV で残りを入れられなくなる）
  - 認証情報に当たる列（マイページURL・ログインID・メール・パスワードなど）は値を読まない。読まなかった列は画面に出す
  - 解析は `services/csv_import.py` の `parse_csv`（DB にも画面にも触れない）。見本は `docs/sample_import.csv`（架空の企業）

### Changed
- README の「現在の利用状況」を更新。CSV の取り込みは入ったが、作者自身のデータの移行はまだ行っていない
- メニューに「取り込み」が増えたため、スクリーンショットを撮り直した
- scripts/demo_data.py の研究内容のサンプル文を、実在の研究を連想させない架空の題材に差し替えた（デモデータは内容もすべて架空にする）
- `pyproject.toml` に `readme`・`license`(MIT)・`license-files`・`authors` を追加。SPDX 形式の `license` に合わせて、ビルドに使う setuptools の下限を 77 に引き上げた
- `# pragma: no cover` を付けていながら網羅率を計測する設定がなかったため、`pytest-cov` を dev 依存に、coverage の設定を pyproject.toml に追加。CI の SQLite のテストで網羅率を出力する（記録のみで、しきい値では落とさない）
- CONTRIBUTING.md のブランチ運用に、適用範囲(0.7.0 から。それ以前の PR は main 向き)と、1人の開発でこの運用を置いている理由を追記。Issue 番号をブランチ名に入れるのは対応する Issue がある場合、と明記した
- 文書の取り残しを実装と規約に合わせた。docs/architecture.md の開発フロー（develop／release の流れ）と構成ツリー（`scripts/capture_screenshots.py`）、CONTRIBUTING.md のスクリーンショット更新手順（自動撮影スクリプト）、README の finance.toml 抜粋の `emphasis`・SQL を書く場所の説明・`SHUKATSU_REVIEW_MODEL` の既定値、docs/data_flow.md の書き出しの説明
- ES管理の絞り込みを `EsService.search` に戻した。画面が同じ判定（カテゴリとキーワード）を自前で持っており、「UI にロジックを書かない」という規約とサービス層の検索の両方から外れていた
- `streamlit` の下限を 1.36 から 1.51 に引き上げ。`width="stretch"` を st.dataframe(1.49 から対応)と st.altair_chart(1.51 から対応)に渡しており、宣言していた下限では動かなかった
- README に現在の利用状況を明記。作者自身の選考管理は今もスプレッドシートで、このアプリは移行経路(#8)ができるまで使っていない。スプレッドシートの課題の話がアプリの運用実績に読めていた。docs/operations.md も題を「起動と保守の手引き」に改め、運用実績を前提にしない書き方にした

### Fixed
- 画面の表記を揃えた。メニュー「添削」とページの題「回答への所見」の不一致、分析ページの表の見出しが `step` のままだった点、選考ファネルの Y 軸で長いステップ名が「…」で切れていた点。スクリーンショット（添削・分析）も撮り直した
- `SelectionService.add_step` が、並び順を決める読み取りをトランザクションの外で行っていた。読み取りを境界の中へ移し、SQLite では境界の開始を `BEGIN IMMEDIATE` にした。境界は書き込みにしか使っておらず、既定の `BEGIN` のままだと、中で読んでから書くまでの間に別の接続が書き込めてしまう
- 利用者が入れた文字列が Markdown として解釈される箇所が残っていた。企業の削除確認のチェック、ES管理の回答の見出し、追加・削除の通知でエスケープし、依頼文では設問と提出先を1行に収める（改行を含む入力が見出しや指示として割り込めないように）
- 接続に失敗すると画面に生のトレースバックが出ていた。psycopg が未導入（RuntimeError）、未対応の接続先（ValueError）、届かない PostgreSQL や開けない SQLite ファイル（ドライバの例外）を、永続化層で共通の `ConnectionFailedError` に翻訳する。文面には接続先のホスト名や利用者名を含めない
- 古いタブから選考ステップを保存すると、入力が黙って捨てられ「変更はありませんでした。」と表示されていた。保存時の再実行で入力欄が最新の値で作り直されるため、衝突を知らせる分岐には到達していなかった。表示した値を入力欄とは別に控え、表示後に他の場所で更新された行は入力を反映しなかったことを警告する。新しい値を上書きしない点は従来どおり。README・docs の説明も実際の挙動に合わせた
- README と docs/architecture.md が「外部送信ゼロ」「アプリは通信しない」と言い切っていたのを、実装に合わせて「既定では通信しない」に修正。データが端末の外に出る2つの場合（添削の実行先に Claude API を選んだとき／`SHUKATSU_DB` を別ホストの PostgreSQL に向けたとき）をセキュリティ方針に明記した

### Removed
- 呼び出し元のないコードを削除: `SelectionService.funnel`（画面と書き出しは `analytics.funnel` を直接呼ぶ）、`StepRepository.get`、`PostgresDialect` が保持するだけで使わない `dsn`
- テストからしか呼ばれていなかったものを削除: `ReviewService.latest` と `ReviewRepository.latest_for_answer`（履歴の先頭と同じ）、`ReviewResult.sent_externally`（実行先の `sends_data_externally` と重複）、`CriteriaSet.ids`。テストは残した入口で同じことを確かめる

## [0.7.0] - 2026-09-21

### Fixed
- 保存先をサイドバーにそのまま表示していたため、ファイルパスに含まれる利用者名やフォルダ構成、PostgreSQL の接続文字列に含まれるパスワードが画面に出ていた。種別と最小限の識別子だけを出す形に変更し、漏れないことをテストで保証した
- 企業研究リンクの説明が README・画面ラベル・docs/screens.md で三者三様に実装と食い違っていたのを、実際に生成する7本に統一（スクリーンショットも撮り直し）
- 書き出しの保存先を文書が `data/ai_analysis.md` としていたのを修正。実際はブラウザへのダウンロードのみで、DB にもディスクにも書かない
- docs/data_flow.md の ER 図で `schema_migrations.version` を int としていたのを text に修正（実装は TEXT）

### Added
- 添削で Claude API に送るモデルを環境変数 `SHUKATSU_REVIEW_MODEL` で差し替えられるようにした（未設定なら従来どおり既定のモデル）。モデルを変えるためにコードを直さずに済む
- `pyproject.toml` の依存に `altair` を明記（app.py が直接 import しているが、streamlit の依存として入ってくるのに頼っていた）
- スクリーンショットをデモデータから自動で撮り直すスクリプト(`scripts/capture_screenshots.py`)
- README のスクリーンショットを現在の画面に更新(添削・企業管理を追加)

### Changed
- ブランチ運用を main / develop / 作業ブランチの3層に変更。CONTRIBUTING.md にブランチの役割・開発フロー・リリース手順を明記し、CI の push 対象に develop を追加。既定ブランチは develop
- scripts/demo_data.py のデモデータ配列を `# fmt: off` で囲み、1件=1行の表として読める並びを保つようにした
- README に、総点検で見つけた不具合と直し方の一覧を追加(症状・原因・対処・回帰テストの対応表)
- docs/data_flow.md に ER 図と索引の一覧、docs/screens.md に画面一覧（できること・呼ぶ層・書き込みの有無）を追加
- docs/test_plan.md にテスト観点表（観点とテスト名の対応）を追加
- docs/operations.md に運用手順（起動・保存先・バックアップと復旧・スキーマ更新時の挙動・困ったとき）を追加
- mypy を dev 依存と CI に追加し、`src/` と `app.py` を型検査する（#5）。ファネルの行を入れる変数が経路別集計の変数と衝突していたのを直した
- CI の lint ジョブが ruff をバージョン指定なしで入れていたのを `pip install -e ".[dev]"` に変更。pyproject の `ruff>=0.6` に揃え、ruff の新版が出た日にコード無変更で CI が落ちないようにした
- 企業研究リンクの受け渡しを dict から dataclass（`ResearchLink`）に変更。層をまたぐ受け渡しに dict を使わない方針の例外になっており、キー名の打ち間違いを型で検出できなかった
- 書式を `ruff format` に統一し、CI の lint ジョブと PR テンプレートに `ruff format --check .` を追加。`line-length = 110` と宣言しながら実際は手で約88桁に折り返しており、設定と実コードが食い違っていた
- docs/architecture.md のレイヤー図に、画面から集計（analytics）への依存を追記（実装は直接呼んでいるが図になかった）
- docs/architecture.md の開発フローと PR テンプレートの確認項目に mypy を追記
- CONTRIBUTING.md の「SQL を書く場所」の規約に、スキーマ管理自身（migrations.py・database.py）を例外として明記
- CHANGELOG の各版にリンク定義を追加。タグのない 0.1.0 / 0.2.0 は見出しの角括弧を外した
- `.gitignore` に `.mypy_cache/` と `.ruff_cache/` を追加（`.pytest_cache/` と揃えた）
- docs/test_plan.md に残っていたテスト件数の表記を外し、「件数は書かない」という自身の宣言に揃えた

### Removed
- `Dialect.in_transaction`（抽象メソッドと SQLite / PostgreSQL の実装）を削除。接続の状態でトランザクションの入れ子を判定する方式は 0.6.0 で取りやめており、呼び出し元のないまま残っていた
- `EsAnswer.length` / `EsAnswer.over_limit` と `LengthCheck.remaining` を削除。文字数の判定は `services/es.py` の `LengthCheck` に集約してあり、モデル側の同じ判定は呼ばれていなかった
- `SelectionService.status_of` / `SelectionService.pass_rates` を削除。呼び出し元がなく、`pass_rates` は内部でステップ一覧を取り直すため、画面が取得済みの結果を使い回す現在の呼び方より問い合わせが増える
- `Database.raw` を削除。docstring は「方言固有の確認が必要なテストだけが使う」としていたが、使っているテストはなく、ドライバの接続を外へ出す入口だけが残っていた
- `ReviewService.industries_with_overlay` を削除。画面は業界の選択肢を `constants.INDUSTRIES` から出しており、このラッパーは呼ばれていなかった

## [0.6.0] - 2026-09-17

### Fixed
- **1つの接続を複数のセッションで共有すると、書き込みが失われる問題を修正**。接続の状態で入れ子を判定していたため、あるセッションの失敗が別のセッションの確定済みの書き込みを巻き戻していた。境界の間は接続のロックを握り、入れ子はこの層が数える深さで判定する。接続もブラウザのセッションごとに持つ
- **画面を開いただけで選考ステップが書き換わる問題を修正**。保存は明示的な操作のときだけ行い、入力欄のキーに保存済みの値を含めて、別の場所での更新を古い表示で上書きしないようにした。保存時にも表示時点の値と突き合わせ、ずれていればその行を書かずに知らせる
- **書式を読み取れない締切が、画面を開いただけで消える問題を修正**。表示していた値と違うものだけを書くようにした
- 重複した企業名などの入力エラーが、画面に例外として出ていた問題を修正。ドライバ固有の例外を共通の型に翻訳し、利用者に伝わる文面にした
- 空欄のまま送信したときに何も起きない（保存されたのか分からない）問題を修正
- 保存・削除の通知が再描画で消えていた問題を修正
- マイページURL・メモ・企業名が Markdown として解釈され、リンクや見出しを差し込めた問題を修正
- 回答本文に見出しや表が含まれると、評価の依頼文の指示を上書きできた問題を修正（本文を囲って渡す）
- ブロックコメントや1行に複数文を含む SQL ファイルで、起動時にマイグレーションが失敗する問題を修正
- 複数プロセスが同時に起動したときに、マイグレーションの記録が衝突して起動に失敗する問題を修正
- 「7日以内の締切」に期限超過が二重に数えられていた問題を修正

### Added
- **PostgreSQL に対応**。`SHUKATSU_DB=postgresql://...` で保存先を切り替えられる。SQL は1組のまま、方言の差は `db/dialects.py` に閉じた
- 同じテスト一式を PostgreSQL に対して走らせる CI ジョブと、手元で試すための `docker-compose.yml`
- トランザクションの回帰テスト（並行書き込み・入れ子・他スレッドの失敗との独立性）
- 画面の回帰テスト（描画で書き換えない・古い表示で上書きしない・削除の確認）
- 企業名・業界・マイページURL・登録メールを後から編集できるようにした
- Claude API の呼び出しに待ち時間の上限と進行表示を追加

### Changed
- 永続化層に Database の層を足し、上の層がドライバを直接触らない形にした
- 省略可能な依存を追加（`postgres`）。`llm` の下限を `anthropic>=1.0` に引き上げ

## [0.5.0] - 2026-09-16

### Added
- **添削ページ**。保存した回答を書き方の観点から点検し、依頼文を組み立てる
- 観点を TOML で定義（`review/criteria/`）。共通の観点13件に加え、応募業界ごとの上乗せを7業界分用意。`emphasis` で共通観点の重みを引き上げられる
- 実行先を差し替え可能にした。既定は通信しない書き出しのみ。Claude API は `ANTHROPIC_API_KEY` がある場合だけ選択肢に出る（キーはアプリに保存しない）
- 所見の履歴を保存（`reviews` テーブル）。評価時点の本文を控え、後から書き換えた場合は画面で分かるようにした
- 通信する実行先を使う場合の省略可能な依存 `llm` を追加

### Changed
- 対応 Python を 3.11 以上に変更（観点の定義に標準ライブラリの tomllib を使うため）

## [0.4.0] - 2026-09-16

### Changed
- **バックエンドを層に分割**。UI（app.py）→ services（業務ルール・入力検証）→ db（接続・スキーマ・読み書き）の一方向依存にし、UI から SQL と集計を分離
- 層をまたぐ受け渡しを dict から dataclass（`models.py`）に変更。キー名の誤りを実行時ではなく型で検出できるようにした
- スキーマを `db/migrations/NNN_name.sql` に切り出し、適用済みバージョンを `schema_migrations` に記録する方式へ変更。既存 DB を作り直さずに列を追加できる
- トランザクションを明示制御に変更（`isolation_level=None` + 明示 BEGIN/COMMIT）。sqlite3 の既定では DDL が暗黙コミットされ、まとめて巻き戻せないため
- リポジトリごとに更新可能な列を列挙し、範囲外の列名は SQL に届く前に `ValueError` にした
- ダッシュボードの集計を1クエリに統合（企業数ぶんの問い合わせを解消）
- 非推奨の `use_container_width` を `width="stretch"` に置換
- 選考ファネルの件数軸を整数目盛に固定
- README の主題を「選考プロセスをローカルで安全に管理するツール」に整理し、設計の意図を明記
- UI テーマを定義（config.toml）。アクセント1色・境界線・見出しサイズ・チャート系列色を統一し、開発用ツールバーを非表示に
- 「AI分析」ページを「書き出し」に改称し、記述を書き出しの仕様に絞った

### Added
- ES 回答の文字数チェックに「制限の8割未満」の警告を追加
- `pyproject.toml` に `[build-system]` を明記
- 層ごとのテスト（リポジトリ・サービス・スキーマ適用）を追加

### Fixed
- マイグレーション失敗時に変更が残る問題を修正（`executescript` の暗黙コミットを廃止し、文単位で明示トランザクションに載せた）

## [0.3.0] - 2026-08-02

### Added
- ruff による静的解析を導入し、CI に lint ジョブを追加
- PR / Issue テンプレートを追加
- 設計文書を追加(docs/architecture.md・docs/data_flow.md)、開発規約を CONTRIBUTING.md に明文化
- AI 書き出しに認証情報が含まれないことを保証する回帰テストと画面表記を追加

### Changed
- UI・文書の絵文字を撤去し、表記をプレーンに統一

### Fixed
- 企業一覧の志望度ソートが文字列順(A→B→C→S)になっており、志望度Sが末尾に表示される問題を修正
- 選考ファネルのステップが辞書順で描画される問題を修正(Altair で選考順に固定)
- README のテスト件数の記載が古くなる問題(件数を書かず CI を参照する形に変更)

## 0.2.0 - 2026-08-01（履歴整理前・タグなし）

### Added
- 企業研究リンクの自動生成(公式・採用・事業内容・IR・クチコミ・選考体験記・ニュース)
- AI分析ページ: 選考データを分析依頼プロンプトつき Markdown に書き出し(アプリ自体は AI と通信しない)
- デモデータ生成スクリプト `scripts/demo_data.py`(架空企業8社)
- README に実画面スクリーンショット4枚を掲載

### Fixed
- 選考ファネルの配色を直感に合わせ修正(通過=緑 / 落選=赤)
- サーバーを 127.0.0.1 のみにバインドし、同一LAN内への意図しない公開を防止

## 0.1.0 - 2026-08-01（履歴整理前・タグなし）

### Added
- 初版: ダッシュボード(締切アラート)・企業管理・ES設問ライブラリ・通過率分析
- SQLite ローカル保存(パスワード非保存方針)、pytest によるテスト、GitHub Actions CI

[Unreleased]: https://github.com/Natto2026/shukatsu-tracker/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/Natto2026/shukatsu-tracker/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Natto2026/shukatsu-tracker/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Natto2026/shukatsu-tracker/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Natto2026/shukatsu-tracker/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Natto2026/shukatsu-tracker/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Natto2026/shukatsu-tracker/releases/tag/v0.3.0
