# 見つけた不具合と直し方

動いているように見えるアプリを疑って総点検したときの記録。v0.6.0 の前には、画面をただ開く、
2つのタブで同じ企業を編集する、複数のスレッドから同時に書く、といった操作で見つけたもの。
v0.9.0 の前には、永続化・画面・サービス・CI の4つの観点で監査して見つけたものを足した。

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

上の表のうち「別のセッション」「別のタブ」「他のプロセス」が絡むものは、1人で1つのタブから
使っている限り起きない。複数のセッションやスレッドから使ったときにだけ現れるため、
テストも並行に書く形で再現している。
