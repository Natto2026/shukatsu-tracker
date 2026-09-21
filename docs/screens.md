# 画面一覧

画面はサイドバーのメニューで切り替える1階層の構成。階層的な遷移はない。
どの画面でも、書き込みは利用者がボタンを押したときだけ行う（描画では保存しない）。

| 画面 | できること | 呼ぶ層 | 書き込み |
|---|---|---|---|
| ダッシュボード | 7日以内の締切（期限超過を先頭に強調）、企業ごとの現在の状況 | `SelectionService.dashboard` → `analytics.company_status` | なし |
| 企業管理 | 企業の追加・編集・削除。選考ステップの追加、締切と結果の更新、削除。企業研究リンク（公式・新卒採用・事業内容・IR・クチコミ・選考体験記・ニュース）の生成 | `SelectionService`、`research.research_links`、`analytics.parse_date` | あり。削除は確認のチェックが必須 |
| ES管理 | 設問と回答の登録・編集、カテゴリとキーワードでの検索、文字数制限との照合（超過と8割未満を警告） | `EsService` | あり |
| 添削 | 保存した回答に対する点検の依頼文を組み立てる。実行先を選んで結果と履歴を残す | `ReviewService`（実行先は `review/providers.py`） | あり（所見の保存）。通信は Claude API を選んだときだけ |
| 分析 | 応募経路別・適性検査別の通過率、選考ファネル | `SelectionService.all_step_views` → `analytics.pass_rate_by` / `analytics.funnel` | なし |
| 取り込み | スプレッドシートから書き出した CSV を読み、企業と選考ステップをまとめて登録する。書き込む前に要約（追加される件数、取り込まない行と理由、取り込まない列）を出す | `CsvImportService.preview` / `apply`（解析は `csv_import.parse_csv`） | あり。要約を確認して「この内容で取り込む」を押したときだけ。1つのトランザクションで書く |
| 書き出し | 選考記録と集計を依頼文つき Markdown に書き出す | `SelectionService.dashboard`、`ai_export.build_analysis_markdown` | なし（ブラウザへのダウンロードのみ。DB・ディスクへの書き込みなし） |

## 共通部分

| 部品 | 内容 |
|---|---|
| サイドバー | メニューと、保存先の種別（ファイル名または接続先の種類だけ。パスやパスワードは出さない） |
| 通知 | 保存・削除の結果を、次の描画で1回だけ表示する |
| エラー | 重複した企業名などの入力エラーは、例外ではなく利用者向けの文面で出す |

## 画面と回帰テストの対応

| 画面 | 主なテスト |
|---|---|
| 全画面 | `tests/test_app_smoke.py`（例外なく描画できる） |
| 企業管理 | `tests/test_app_behaviour.py`（描画で書き換えない・古い表示で上書きしない・削除の確認） |
| サイドバー | `tests/test_app_behaviour.py::TestTargetIsNotLeaked` |
| 取り込み | `tests/test_csv_import.py`（解析・認証情報の列を読まない・途中で失敗したら何も残らない）、`tests/test_app_behaviour.py::TestCsvImport`（要約→確認→反映） |
| 書き出し | `tests/test_research_and_export.py`（認証情報を含めない） |

画面ごとの詳しい検証観点は [test_plan.md](test_plan.md) を参照。
