# テスト観点表

何をどの観点で検証しているかの一覧。テスト名は `tests/` の関数名と一致させてあるので、
表から該当のテストへ辿れる。件数は書かない（CI のログを正とする）。

## 方針

- 層ごとに単体テストを置く。集計は DB を使わず、リポジトリとサービスは実際の DB で、画面は Streamlit の AppTest で検証する
- 通信するテストは置かない。API を呼ぶ実行先は、差し込んだ偽のクライアントに対して「何を送るか」「応答をどう解釈するか」だけを見る
- 同じテスト一式を SQLite（Python 3.11 / 3.13）と PostgreSQL の両方に対して CI で走らせる
- 挙動を変える修正には回帰テストを添える。追加したテストは、修正をわざと戻して失敗することまで確かめる

## 観点と対応するテスト

### 集計（`tests/test_analytics.py`）

| 観点 | テスト |
|---|---|
| 指定日数以内の締切だけを抽出する | `test_within_days` |
| 期限超過を含め、先頭に並べる | `test_overdue_is_included_and_sorted_first` |
| 結果の出たステップと日付のないステップは対象外 | `test_finished_and_undated_steps_are_ignored` |
| 経路別の通過率を出す | `test_rate_by_route` |
| 通過率の高い順に並ぶ | `test_sorted_by_rate_descending` |
| ステップ名で絞り込める | `test_filter_by_step_name` |
| 想定外の属性名は受け付けない | `test_unknown_attribute_is_rejected` |
| ファネルは既定の選考順で数える | `test_counts_in_standard_order` |
| 企業の状況判定（ステップなし・落選優先・最初の選考中・全通過） | `TestCompanyStatus` |

### リポジトリ（`tests/test_repositories.py`）

| 観点 | テスト |
|---|---|
| 追加した企業を同じ内容で読み戻せる | `test_add_and_get_roundtrip` |
| 志望度は文字コード順ではなく意味の順で並ぶ | `test_sorted_by_priority_meaning_not_alphabet` |
| 企業名の重複は共通の例外になる | `test_duplicate_name_is_rejected` |
| 存在しない ID は None | `test_get_returns_none_for_missing_row` |
| 許可した列以外は書けない | `test_unknown_column_is_rejected` |
| id 列は上書きできない | `test_id_column_cannot_be_overwritten` |
| ステップ一覧には企業の属性が結合される | `test_views_are_joined_with_company_fields` |
| ステップは表示順で並ぶ | `test_ordered_by_sort_order` |
| 企業を消すとステップも消える | `test_deleting_company_cascades_steps` |
| 企業を消しても回答は残る | `test_answer_survives_company_deletion` |
| 回答の更新で更新日時が進む | `test_update_refreshes_the_timestamp` |
| 回答一覧に企業名が結合される | `test_company_name_is_joined` |

### サービス（`tests/test_services.py`）

| 観点 | テスト |
|---|---|
| 企業を追加すると既定の選考ステップも入る。省略もできる | `test_default_steps_are_created`、`test_default_steps_can_be_skipped` |
| 企業名は前後の空白を除き、空なら拒否 | `test_name_is_trimmed_and_required` |
| 重複で失敗したとき、ステップだけが残らない | `test_duplicate_name_leaves_no_orphan_steps` |
| 追加したステップは末尾に付く | `test_added_step_goes_to_the_end` |
| 空のステップ名・未定義の結果は拒否 | `test_blank_step_name_is_rejected`、`test_undefined_result_is_rejected` |
| 締切を消しても結果には触れない | `test_deadline_can_be_cleared_without_touching_the_result` |
| 変更なしの更新は何もしない | `test_updating_nothing_is_a_no_op` |
| ダッシュボードの件数・締切・期限超過の分離 | `test_counts_and_deadlines`、`test_overdue_is_separated` |
| 企業ごとに問い合わせを繰り返さない（N+1 なし） | `test_steps_are_fetched_once_for_every_company` |
| 設問は必須。文字数制限 0 は「なし」、負数は拒否 | `test_question_is_required`、`test_zero_char_limit_is_stored_as_none`、`test_negative_char_limit_is_rejected` |
| カテゴリとキーワードで検索できる | `test_search_by_category_and_keyword` |
| 文字数制限との照合 | `test_length_check` |

### スキーマ適用（`tests/test_migrations.py`）

| 観点 | テスト |
|---|---|
| 接続するとすべてのテーブルができる | `test_connect_creates_all_tables` |
| 適用した版が記録される | `test_every_migration_is_recorded` |
| 2回適用しても変わらない | `test_applying_twice_changes_nothing` |
| 既存の SQLite ファイルへ再接続しても壊れない | `test_reconnecting_to_an_existing_sqlite_file_is_safe` |
| ファイル名が NNN_name.sql の規約に従う | `test_migration_filenames_follow_the_convention` |
| 途中で失敗した適用は巻き戻る | `test_a_failing_migration_is_rolled_back` |
| 外部キー制約が効いている | `test_foreign_keys_are_enforced` |

### トランザクション（`tests/test_transactions.py`）

| 観点 | テスト |
|---|---|
| 成功でコミット、失敗でロールバック | `test_commits_on_success`、`test_rolls_back_on_failure` |
| 入れ子はいちばん外側で1回だけコミット | `test_nested_blocks_commit_once` |
| 入れ子の内側で失敗すると全体が巻き戻る | `test_failure_inside_a_nested_block_rolls_back_everything` |
| 別スレッドの失敗が自分の確定済みの書き込みを消さない | `test_a_failed_transaction_does_not_discard_another_threads_write` |
| 同時に書いた行がすべて残る | `test_concurrent_writers_all_persist` |
| サービス経由の書き込みは直列化される | `test_service_level_writes_are_serialised` |
| 重複で失敗しても先に入れた行は残る | `test_a_duplicate_failure_leaves_earlier_rows_intact` |

### 画面（`tests/test_app_behaviour.py`、`tests/test_app_smoke.py`）

| 観点 | テスト |
|---|---|
| どの画面も例外なく描画できる | `test_page_renders_without_error` |
| 登録した企業がダッシュボードに出る | `test_dashboard_shows_registered_company` |
| 描画しただけでは書き換えない（読めない締切を壊さない） | `test_rendering_does_not_destroy_an_unreadable_deadline`、`test_saving_without_edits_keeps_an_unreadable_deadline` |
| 別の場所の更新を古い表示で戻さない | `test_rendering_does_not_revert_an_out_of_band_update`、`test_a_stale_tab_cannot_overwrite_a_newer_change` |
| 入力エラーは例外ではなく文面で出る | `test_duplicate_company_name_shows_a_message_not_a_traceback`、`test_blank_company_name_is_reported` |
| 削除は確認しないと押せない。確認すれば消える | `test_delete_is_disabled_until_confirmed`、`test_delete_works_once_confirmed` |
| セッションごとに接続を持ち、別接続から書き込みが見える | `test_each_session_opens_its_own_connection`、`test_write_through_the_app_is_visible_to_another_connection` |
| 保存先の表示にパスワードや絶対パスが出ない | `TestTargetIsNotLeaked` |

### 企業研究リンク・書き出し（`tests/test_research_and_export.py`）

| 観点 | テスト |
|---|---|
| リンクは https でエンコード済み、主要な調査ページを網羅 | `test_links_are_https_and_encoded`、`test_covers_key_research_pages` |
| 書き出しに依頼文・記録・集計が含まれる | `test_contains_request_and_records`、`test_contains_aggregates` |
| 書き出しにマイページ URL とログイン用メールを含めない | `test_export_never_contains_credentials` |
| 回答本文は求めたときだけ含め、空の回答は飛ばす | `test_answers_only_when_requested`、`test_empty_answers_are_skipped` |

### 回答の点検（`tests/test_review_*.py`）

| 観点 | テスト |
|---|---|
| 観点ファイル（TOML）がすべて読める | `test_base_criteria_load`、`test_every_file_is_valid_toml` |
| 送る文面が特定の組織の基準を名乗らない | `test_sent_text_never_claims_a_specific_organisation_standard`、`test_base_file_states_it_is_not_a_specific_standard` |
| 業界ファイルは共通観点を上書きせず足す。emphasis は重みを上げ、上限がある | `test_industry_overlay_extends_the_base`、`test_emphasis_raises_the_weight`、`test_weight_is_capped`、`test_emphasis_must_reference_a_defined_criterion` |
| 未知の業界・業界なしでも共通観点で動く | `test_unknown_industry_falls_back_to_base_only`、`test_none_industry_is_accepted` |
| 観点は重みの順に並ぶ | `test_criteria_are_ordered_by_weight` |
| 依頼文に本文・文字数・全観点が入り、業界で変わる | `test_contains_the_target`、`test_reports_length_without_a_limit`、`test_lists_every_criterion`、`test_industry_changes_the_criteria_shown` |
| 補足は与えたときだけ入る。複数行の観点で表が崩れない。空入力は拒否。事実の捏造を禁じる指示が入る | `test_note_is_included_only_when_given`、`test_multiline_criteria_do_not_break_the_table`、`test_empty_input_is_rejected`、`test_system_prompt_forbids_inventing_facts` |
| 既定の実行先は依頼文をそのまま返し、通信しないと宣言する | `test_returns_the_prompt_unchanged`、`test_declares_that_it_does_not_send_data` |
| API の実行先は送信内容・拒否・空応答・依存やキーの欠如を扱える | `TestAnthropicProvider`、`TestExtractText` |
| モデルは環境変数で差し替えられ、未設定・空白なら既定に戻る。明示指定が環境変数より優先される | `test_model_defaults_when_the_environment_is_unset`、`test_model_can_be_overridden_by_the_environment`、`test_a_blank_environment_value_falls_back_to_the_default`、`test_an_explicit_model_wins_over_the_environment`、`test_the_environment_is_read_at_call_time_not_at_import` |
| 実行先の一覧は通信しないものが先頭、API はキーがあるときだけ | `test_offline_provider_is_always_first`、`test_api_provider_appears_when_the_key_is_set` |
| 所見は依頼文と本文の写しごと保存され、後の書き換えを検出する | `test_run_saves_the_review`、`test_prompt_is_stored_with_the_result`、`test_snapshot_detects_a_later_edit` |
| 履歴は新しい順。未保存の回答は点検できない | `test_history_is_newest_first`、`test_unsaved_answer_is_rejected` |
| 業界は企業から引き、上書きもできる | `test_industry_comes_from_the_company`、`test_industry_can_be_overridden`、`test_answer_without_a_company_has_no_industry` |
| 既定の実行先では通信しない。回答を消すと所見も消える。所見は1件ずつ消せる | `test_default_provider_does_not_send_data`、`test_review_is_removed_with_its_answer`、`test_delete_removes_one_review` |

## 実行

```bash
python -m pytest                       # SQLite
SHUKATSU_TEST_DSN=postgresql://user:pass@127.0.0.1:5432/shukatsu_test python -m pytest   # PostgreSQL
```

CI の構成は [.github/workflows/ci.yml](../.github/workflows/ci.yml) を参照。
