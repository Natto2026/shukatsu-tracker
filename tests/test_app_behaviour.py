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
from shukatsu_tracker.models import Company
from shukatsu_tracker.services import SelectionService

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
        company_id = selection.add_company(
            Company(name="テスト株式会社"), with_default_steps=False
        )
        step_id = selection.add_step(company_id, "ES", deadline=deadline)
    finally:
        database.close()
    return company_id, step_id


def read_step(path, step_id: int) -> tuple[str | None, str]:
    database = open_db(path)
    try:
        row = database.fetchone(
            "SELECT deadline, result FROM steps WHERE id = ?", (step_id,)
        )
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
            SelectionService(database).update_step(
                step_id, deadline="2026-11-05", result="通過"
            )
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
