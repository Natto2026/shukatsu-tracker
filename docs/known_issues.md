# 見つけた不具合と直し方

動いているように見えるアプリを疑って総点検したときの記録。v0.6.0 の前には、画面をただ開く、
2つのタブで同じ企業を編集する、複数のスレッドから同時に書く、といった操作で見つけたもの。
v0.8.0 のあとには、永続化・画面・サービス・CI の4つの観点で監査して見つけたものを足した。
v0.9.0 のあとには、画面とバックエンドを重点に3回見直して見つけたもののうち、主なものを足した
（すべての修正は CHANGELOG の Unreleased にある）。

いずれも修正と一緒に回帰テストを足し、わざと元に戻してテストが失敗することまで確かめている。
表の「回帰テスト」は `tests/` の関数名と一致していて、観点ごとの一覧は
[test_plan.md](test_plan.md) にある。どの修正がどの版かは [CHANGELOG.md](../CHANGELOG.md) の
Fixed に対応する。

| 症状 | 原因 | 対処 | 回帰テスト |
|---|---|---|---|
| 別のセッションが失敗すると、確定したはずの書き込みが消える | 1つの接続を全セッションで共有し、「いまトランザクション中か」を接続の状態で判定していた。他人のトランザクションを自分のものと誤認し、相手の巻き戻しに巻き込まれた | 接続をセッションごとに持つ。境界の間はロックを握り、入れ子はこの層が数える深さで判定する | `test_a_failed_transaction_does_not_discard_another_threads_write` |
| 別のタブを再描画しただけで、他所の更新が古い値で上書きされる | 入力欄のキーに保存済みの値が含まれず、古い表示がそのまま保存されていた | 保存は明示的な操作のときだけ。キーに保存値を含め、差分だけ書く。表示後に他の場所で更新された行は入力を反映せず、そのことを知らせる | `test_a_stale_tab_cannot_overwrite_a_newer_change`、`test_a_stale_tab_is_told_that_its_edit_was_not_saved` |
| 書式を読み取れない締切が、画面を開いただけで消える | 描画のたびに入力欄の値を書き戻していた | 表示していた値と違うものだけを書く | `test_rendering_does_not_destroy_an_unreadable_deadline` |
| 重複した企業名を入れると、画面に生の例外が出る | ドライバ固有の例外をそのまま上げていた | 共通の型に翻訳し、利用者に伝わる文面にする | `test_duplicate_company_name_shows_a_message_not_a_traceback` |
| ボタン1つで企業と選考ステップがまとめて消える | 確認のない削除ボタン | 確認のチェックを挟み、何が失われるかを明示する | `test_delete_is_disabled_until_confirmed` |
| 保存先の表示に、利用者名やパスワードが出る | 接続文字列とファイルパスをそのまま表示していた | 種別と最小限の識別子だけを返す | `test_connection_string_never_shows_the_password` |
| COMMIT が一度失敗すると、以後の書き込みが確定も巻き戻しもされず、別のセッションの起動が「database is locked」で止まる | COMMIT の失敗時に深さを戻しておらず、次の境界が入れ子と誤認されて BEGIN も ROLLBACK も発行されなかった | COMMIT・ROLLBACK は失敗しても深さを戻す。COMMIT が失敗したら ROLLBACK を試みてから元の例外を上げる | `test_a_failed_commit_resets_the_depth_and_releases_the_lock`、`test_the_next_boundary_still_rolls_back_after_a_failed_commit` |
| ロック待ちの超過や接続の切断で、画面に生のトレースバックが出る。PostgreSQL を再起動すると、開いていたタブは以後の操作がすべて失敗したままになる | 翻訳していたのは一意制約と外部キーの違反だけで、BEGIN / COMMIT はそもそも翻訳を通っていなかった。死んだ接続をセッションが持ち続けていた | ドライバの例外をすべて共通の型に翻訳し、BEGIN / COMMIT / ROLLBACK も同じ経路を通す。再描画のたびに接続の生死を確かめ、死んでいれば張り直す | `test_operating_on_a_closed_connection_is_reported_as_lost`、`test_a_dead_connection_is_reopened_on_the_next_run` |
| 選考ステップの保存が途中で失敗すると、前の行だけが保存されたうえに生のトレースバックが出る | 行ごとに別のトランザクションで書き、失敗を画面で捕まえていなかった | 全行を検証してから1つの境界で書く（`update_steps`）。失敗は他の書き込みと同じ経路で文面にする | `test_a_failure_midway_leaves_no_step_updated`、`test_a_failed_step_save_shows_a_message_and_writes_nothing` |
| ES 本文を別のタブで同じ日に更新すると、古いタブの「保存」で潰される | 入力欄のキーに含めていた更新日が日付単位で、同じ日のうちの更新を見分けられなかった | キーに保存済みの本文のダイジェストを含め、表示していた本文と突き合わせてから書く。サービス層でも、表示していた本文と一致するときだけ書き換える | `test_a_stale_tab_cannot_overwrite_a_newer_answer`、`test_update_refuses_to_overwrite_a_text_that_changed_since_it_was_shown` |
| 出力の上限で途中で切れた所見が、完成品として保存される | 応答の停止理由を「拒否」しか見ていなかった | `max_tokens` で止まった応答は保存せず、理由を伝える。実行にかかったトークン数も所見と一緒に残す | `test_a_truncated_response_is_rejected_not_saved`、`test_usage_is_stored_with_the_review` |
| 画面以外の入口から選択肢にない値や読めない締切を保存でき、一覧や締切から黙って消える。選択肢にない値があると企業管理の画面全体が例外で落ちる | 選択肢と日付の検証が画面と CSV にしかなく、サービス層は素通しだった。画面は `list.index` で位置を引いていた | 業界・志望度・応募経路・適性検査・締切をサービス層で検証する。選択肢にない古い値は末尾に足して表示し、黙って書き換えない | `TestCompanyValidation`、`test_an_unreadable_deadline_is_rejected_not_nulled`、`TestValuesOutsideTheChoices` |
| 途中のステップを消したあとにステップを足すと、並び順が既存の行と重複する。PostgreSQL では2端末から同時に足しても重複する | 並び順を件数で決めていた。PostgreSQL は境界を開いても他の接続を待たせない | 並び順は最大値の次にする。企業の行を `FOR UPDATE`（SQLite では空）でロックしてから読む | `test_a_step_added_after_a_deletion_does_not_collide`、`test_add_step_locks_the_company_row_before_reading_the_order` |
| 選考ステップと所見が、確認なしの1クリックで消える（企業と回答だけが確認付きだった） | 削除ボタンを直に置いていた | 回答の削除と同じく、何が失われるかを見せてから「削除する」を押させる | `test_step_delete_is_behind_a_confirmation`、`test_review_delete_is_behind_a_confirmation` |
| マイグレーションの版の中の文が一意制約に反すると、その版だけを黙って飛ばし、次の版を適用する | 一意制約の違反を、すべて「別のプロセスが先に記録した」とみなしていた | 記録が残っているときだけ飛ばし、なければ失敗として止める | `test_a_version_that_breaks_a_unique_constraint_is_not_skipped` |
| PostgreSQL で2つのセッションが同時に初めて接続すると、片方が起動に失敗する | BEGIN はロックを取らず、相手の未確定の記録も見えない。両方が未適用と判断し、二度流せない `ALTER TABLE` や管理表の作成を流していた | 勧告ロック（`pg_advisory_xact_lock`）を取ってから記録を見直し、管理表の作成と版の適用を1本ずつにする | `test_two_sessions_starting_together_apply_a_version_once`、`test_first_connections_arriving_together_both_succeed` |
| PostgreSQL で、同じ本文を表示していた2つのタブが同時に保存すると、後の保存が先の保存を潰す | 表示していた本文との突き合わせの読み取りが、行をロックしていなかった（SQLite は境界の開始で書き込みロックを取るため起きない） | 行を `FOR UPDATE` で読んでから突き合わせる | `test_two_sessions_saving_from_the_same_text_do_not_both_win` |
| 添削で業界を「指定なし」にしても、提出先の業界の観点で送られ、その業界として保存される | `None` を「未指定（提出先の業界を使う）」と「指定なし」の両方に使っていた | 業界を外すことを表す値 `NO_INDUSTRY` を設けて区別する | `test_choosing_no_industry_is_what_gets_sent_and_saved` |
| 企業情報を更新したり別のページから戻ったりすると、選択が先頭の企業に移り、続けて操作すると別の企業を編集しかねない | 企業そのものを選択肢にしており、企業名や志望度が変わると別の選択欄とみなされた。キーを付けた選択欄の状態は、別のページを開いている間に捨てられる | 選択は企業の ID で持ち、別のキーに控えて戻ったときに復元する | `TestCompanySelection` |
| 落選・辞退した企業に残ったステップが「期限超過」に出続ける。これを直すために企業ごと除外したところ、辞退したインターンのあとに足した本選考の締切まで消えた | 企業の追加時に入る標準のステップが「選考中」のまま残る。残りのステップと、続いている選考とを区別する情報がない | 件数からは外し、締切は消さずに「落選・辞退した企業に残っている締切」として別の欄で見せる | `test_a_deadline_left_in_an_ended_company_is_kept_aside_not_dropped`、`test_a_deadline_left_in_an_ended_company_is_shown_apart` |
| 利用者の入力が、数式・絵文字・アイコンなど Streamlit 独自の記法として描画される。分析用の書き出しでは、メモや設問の改行の続きが文書の見出しとして読まれる | エスケープの対象が Markdown の記号だけだった。書き出しは利用者の文字列をそのまま並べていた | Streamlit 独自の記号もエスケープする（アイコンの `:` は文字参照にする）。書き出しと依頼文では、利用者の文字列を1行に収め、回答の本文は抜け出せない囲みに入れる | `test_streamlit_specific_syntax_is_not_interpreted`、`test_multi_line_user_text_cannot_forge_a_heading` |

上の表のうち「別のセッション」「別のタブ」「他のプロセス」が絡むものは、1人で1つのタブから
使っている限り起きない。複数のセッションやスレッドから使ったときにだけ現れるため、
テストも並行に書く形で再現している。
