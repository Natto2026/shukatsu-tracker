"""画面の振る舞いの検証。

ここで見るのは「例外が出ないこと」ではなく「勝手に書き換えないこと」。
描画しただけで保存が走る作りは例外を出さずにデータを壊すため、
スモークテストでは捕まらない。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from shukatsu_tracker import db
from shukatsu_tracker.db import transaction
from shukatsu_tracker.models import Company, EsAnswer
from shukatsu_tracker.services import EsService, SelectionService

APP_PATH = str(Path(__file__).parent.parent / "app.py")


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """アプリが使う SQLite ファイルを用意し、外から確認できるようにする。"""
    path = tmp_path / "app.db"
    monkeypatch.setenv("SHUKATSU_DB", str(path))
    return path


def open_db(path):
    return db.connect(path)


def seed_company(path, *, deadline: str | None = None) -> tuple[int, int]:
    database = open_db(path)
    try:
        selection = SelectionService(database)
        company_id = selection.add_company(Company(name="テスト株式会社"), with_default_steps=False)
        step_id = selection.add_step(company_id, "ES", deadline=deadline)
    finally:
        database.close()
    return company_id, step_id


def read_step(path, step_id: int) -> tuple[str | None, str]:
    database = open_db(path)
    try:
        row = database.fetchone("SELECT deadline, result FROM steps WHERE id = ?", (step_id,))
        return row["deadline"], row["result"]
    finally:
        database.close()


def open_page(app_db, page: str) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, at.exception
    return at


class TestNoWriteOnRender:
    def test_rendering_does_not_destroy_an_unreadable_deadline(self, app_db):
        """書式を読み取れない締切が、画面を開いただけで消えないこと。"""
        _, step_id = seed_company(app_db, deadline="2026/10/01")
        open_page(app_db, "企業管理")
        assert read_step(app_db, step_id) == ("2026/10/01", "選考中")

    def test_saving_without_edits_keeps_an_unreadable_deadline(self, app_db):
        """何も変えずに保存を押しても、読み取れない締切を空にしないこと。"""
        _, step_id = seed_company(app_db, deadline="2026/10/01")
        at = open_page(app_db, "企業管理")
        submit = [b for b in at.button if b.label == "選考ステップを保存"]
        assert submit, "保存ボタンが見つからない"
        submit[0].click().run()
        assert not at.exception, at.exception
        assert read_step(app_db, step_id) == ("2026/10/01", "選考中")

    def test_rendering_does_not_revert_an_out_of_band_update(self, app_db):
        """別の場所で更新された値を、古い表示のまま書き戻さないこと。"""
        _, step_id = seed_company(app_db, deadline="2026-10-01")
        at = open_page(app_db, "企業管理")

        database = open_db(app_db)
        try:
            SelectionService(database).update_step(step_id, deadline="2026-11-05", result="通過")
        finally:
            database.close()

        at.run()  # 開いたままのタブが再描画されただけ
        assert not at.exception, at.exception
        assert read_step(app_db, step_id) == ("2026-11-05", "通過")

    def test_a_stale_tab_cannot_overwrite_a_newer_change(self, app_db):
        """古い表示のまま保存を押しても、新しい変更を潰さないこと。"""
        _, step_id = seed_company(app_db, deadline="2026-10-01")
        at = open_page(app_db, "企業管理")

        database = open_db(app_db)
        try:
            SelectionService(database).update_step(step_id, result="通過")
        finally:
            database.close()

        submit = [b for b in at.button if b.label == "選考ステップを保存"]
        submit[0].click().run()
        assert not at.exception, at.exception
        assert read_step(app_db, step_id)[1] == "通過"

    def test_a_stale_tab_is_told_that_its_edit_was_not_saved(self, app_db):
        """古い表示のまま編集して保存したら、反映しなかったことを知らせること。

        保存の再実行で入力欄は最新の値で作り直されるため、古いタブの編集は
        届かない。黙って「変更はありませんでした」と出すと、利用者は保存できた
        のか、編集が消えたのかを区別できない。
        """
        _, step_id = seed_company(app_db, deadline="2026-10-01")
        at = open_page(app_db, "企業管理")

        database = open_db(app_db)
        try:
            SelectionService(database).update_step(step_id, result="通過")
        finally:
            database.close()

        stale = [s for s in at.selectbox if s.label == "結果"]
        assert stale, "結果の入力欄が見つからない"
        stale[0].set_value("落選")
        [b for b in at.button if b.label == "選考ステップを保存"][0].click().run()

        assert not at.exception, at.exception
        assert read_step(app_db, step_id) == ("2026-10-01", "通過")
        assert any("他の場所で更新された" in w.value for w in at.warning)
        assert not any("変更はありませんでした" in i.value for i in at.info)

    def test_an_edit_on_an_untouched_row_is_still_saved_from_a_stale_tab(self, app_db):
        """他の場所で更新されていない行の編集は、同じ保存でそのまま反映されること。"""
        company_id, first_id = seed_company(app_db, deadline="2026-10-01")
        database = open_db(app_db)
        try:
            second_id = SelectionService(database).add_step(company_id, "1次面接")
        finally:
            database.close()
        at = open_page(app_db, "企業管理")

        database = open_db(app_db)
        try:
            SelectionService(database).update_step(first_id, result="通過")
        finally:
            database.close()

        results = [s for s in at.selectbox if s.label == "結果"]
        results[0].set_value("落選")
        results[1].set_value("辞退")
        [b for b in at.button if b.label == "選考ステップを保存"][0].click().run()

        assert not at.exception, at.exception
        assert read_step(app_db, first_id)[1] == "通過"
        assert read_step(app_db, second_id)[1] == "辞退"
        assert any("他の場所で更新された" in w.value for w in at.warning)
        assert any("1 件を保存しました" in i.value for i in at.info)

    def test_saving_a_fresh_tab_does_not_warn(self, app_db):
        """最新の表示から保存したときは、警告を出さないこと。"""
        _, step_id = seed_company(app_db, deadline="2026-10-01")
        at = open_page(app_db, "企業管理")
        [s for s in at.selectbox if s.label == "結果"][0].set_value("通過")
        [b for b in at.button if b.label == "選考ステップを保存"][0].click().run()

        assert not at.exception, at.exception
        assert read_step(app_db, step_id)[1] == "通過"
        assert not any("他の場所で更新された" in w.value for w in at.warning)
        assert any("1 件を保存しました" in i.value for i in at.info)


class TestErrorsAreFriendly:
    def test_duplicate_company_name_shows_a_message_not_a_traceback(self, app_db):
        seed_company(app_db)
        at = open_page(app_db, "企業管理")
        name_inputs = [i for i in at.text_input if i.label == "企業名 *"]
        assert name_inputs, "企業名の入力欄が見つからない"
        name_inputs[0].set_value("テスト株式会社")
        [b for b in at.button if b.label == "追加"][0].click().run()

        assert not at.exception, at.exception
        assert any("すでに登録されています" in e.value for e in at.error)

    def test_blank_company_name_is_reported(self, app_db):
        seed_company(app_db)
        at = open_page(app_db, "企業管理")
        [b for b in at.button if b.label == "追加"][0].click().run()
        assert not at.exception, at.exception
        assert any("企業名を入力してください" in e.value for e in at.error)


class TestUserTextIsNotMarkdown:
    """利用者が入れた文字列を、ラベルや通知で Markdown として解釈させないこと。"""

    HOSTILE = "**太字** [罠](https://example.com)"

    def test_delete_confirmation_label_is_escaped(self, app_db):
        database = open_db(app_db)
        try:
            SelectionService(database).add_company(Company(name=self.HOSTILE), with_default_steps=False)
        finally:
            database.close()
        at = open_page(app_db, "企業管理")
        label = [c.label for c in at.checkbox if "削除することを理解しました" in c.label][0]
        assert self.HOSTILE not in label
        assert r"\*\*太字\*\*" in label

    def test_added_company_notice_is_escaped(self, app_db):
        at = open_page(app_db, "企業管理")
        [i for i in at.text_input if i.label == "企業名 *"][0].set_value(self.HOSTILE)
        [b for b in at.button if b.label == "追加"][0].click().run()
        assert not at.exception, at.exception
        notices = [s.value for s in at.success if "追加しました" in s.value]
        assert notices, "追加の通知が見つからない"
        assert self.HOSTILE not in notices[0]

    def test_es_expander_label_is_escaped(self, app_db):
        database = open_db(app_db)
        try:
            company_id = SelectionService(database).add_company(
                Company(name=self.HOSTILE), with_default_steps=False
            )
            EsService(database).add(EsAnswer(question=self.HOSTILE, company_id=company_id, answer="本文"))
        finally:
            database.close()
        at = open_page(app_db, "ES管理")
        labels = [e.label for e in at.expander if "太字" in e.label]
        assert labels, "回答の見出しが見つからない"
        assert self.HOSTILE not in labels[0]
        assert labels[0].count(r"\*\*太字\*\*") == 2


class TestDestructiveActionsNeedConfirmation:
    def test_delete_is_disabled_until_confirmed(self, app_db):
        company_id, _ = seed_company(app_db)
        at = open_page(app_db, "企業管理")
        delete_buttons = [b for b in at.button if b.label == "削除する"]
        assert delete_buttons, "削除ボタンが見つからない"
        assert delete_buttons[0].disabled

        database = open_db(app_db)
        try:
            assert SelectionService(database).company(company_id) is not None
        finally:
            database.close()

    def test_delete_works_once_confirmed(self, app_db):
        company_id, _ = seed_company(app_db)
        at = open_page(app_db, "企業管理")
        confirm = [c for c in at.checkbox if "削除することを理解しました" in c.label]
        assert confirm, "確認のチェックが見つからない"
        confirm[0].set_value(True).run()
        [b for b in at.button if b.label == "削除する"][0].click().run()
        assert not at.exception, at.exception

        database = open_db(app_db)
        try:
            assert SelectionService(database).company(company_id) is None
        finally:
            database.close()


class TestEsLibraryFilter:
    def test_keyword_narrows_the_list(self, app_db):
        """絞り込みはサービス層の検索を通ること（画面に同じ判定を持たない）。"""
        database = open_db(app_db)
        try:
            es = EsService(database)
            es.add(EsAnswer(question="学生時代に力を入れたこと", category="ガクチカ", answer="大会の運営"))
            es.add(EsAnswer(question="志望動機", category="志望動機", answer="事業に関心がある"))
        finally:
            database.close()

        at = open_page(app_db, "ES管理")
        assert any("2 / 2 件" in c.value for c in at.caption)

        [i for i in at.text_input if i.label.startswith("キーワード検索")][0].set_value("運営").run()
        assert not at.exception, at.exception
        assert any("1 / 2 件" in c.value for c in at.caption)
        assert [e.label for e in at.expander if "ガクチカ" in e.label]
        assert not [e.label for e in at.expander if "志望動機" in e.label]


class TestConnectionScope:
    def test_each_session_opens_its_own_connection(self, app_db):
        """セッションごとに別の接続を持つこと。共有すると書き込みが干渉する。"""
        seed_company(app_db)
        first = AppTest.from_file(APP_PATH, default_timeout=60).run()
        second = AppTest.from_file(APP_PATH, default_timeout=60).run()
        assert not first.exception and not second.exception
        assert first.session_state["db"] is not second.session_state["db"]


def test_write_through_the_app_is_visible_to_another_connection(app_db):
    """アプリの書き込みが、別の接続からも読めること（未コミットで止まらない）。"""
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    at.sidebar.radio[0].set_value("企業管理").run()
    [i for i in at.text_input if i.label == "企業名 *"][0].set_value("新規株式会社")
    [b for b in at.button if b.label == "追加"][0].click().run()
    assert not at.exception, at.exception

    database = open_db(app_db)
    try:
        with transaction(database):
            pass
        names = [c.name for c in SelectionService(database).companies()]
    finally:
        database.close()
    assert "新規株式会社" in names


class TestTargetIsNotLeaked:
    """保存先の表示から、利用者名やパスワードが漏れないこと。"""

    def test_connection_string_never_shows_the_password(self):
        described = db.describe("postgresql://someone:s3cret@10.0.0.5:5432/shukatsu")
        assert "s3cret" not in described
        assert "someone" not in described
        assert "10.0.0.5" not in described
        assert "PostgreSQL" in described

    def test_path_outside_the_app_is_reduced_to_a_file_name(self):
        described = db.describe(r"C:\Users\somebody\Desktop\仕事\data\shukatsu.db")
        assert "somebody" not in described
        assert "仕事" not in described
        assert "shukatsu.db" in described

    def test_path_inside_the_app_stays_relative(self, tmp_path):
        described = db.describe(tmp_path / "data" / "demo.db", base=tmp_path)
        assert described == "SQLite（data/demo.db）"

    def test_sidebar_does_not_render_the_absolute_path(self, app_db):
        at = AppTest.from_file(APP_PATH, default_timeout=60).run()
        assert not at.exception, at.exception
        captions = " ".join(c.value for c in at.sidebar.caption)
        assert str(app_db) not in captions
        assert str(app_db.parent) not in captions
        assert "app.db" in captions

    def test_a_windows_path_is_shortened_on_any_platform(self):
        """OS をまたいでも短縮されること。

        pathlib は動作中の OS の区切り文字しか見ない。Windows で作った設定を
        Linux で表示したときに、パスがそのまま出てしまう不具合があった。
        """
        described = db.describe(r"C:\Users\somebody\Desktop\work\data\shukatsu.db")
        assert described == "SQLite（shukatsu.db）"

    def test_a_posix_path_is_shortened_on_any_platform(self):
        described = db.describe("/home/someone/projects/data/shukatsu.db")
        assert described == "SQLite（shukatsu.db）"
