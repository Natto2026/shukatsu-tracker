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
from shukatsu_tracker.db import CompanyRepository, DatabaseError, StepRepository, transaction
from shukatsu_tracker.models import Company, EsAnswer
from shukatsu_tracker.services import EsService, ReviewService, SelectionService

APP_PATH = str(Path(__file__).parent.parent / "app.py")

# AppTest はアプリ全体を実行するので遅い。日常は `-m "not ui"` で外せるようにしておく
pytestmark = pytest.mark.ui


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """アプリが使う SQLite ファイルを用意し、外から確認できるようにする。"""
    path = tmp_path / "app.db"
    monkeypatch.setenv("SHUKATSU_DB", str(path))
    return path


def open_db(path):
    return db.connect(path)


def seed_company(path, *, deadline: str | None = None) -> tuple[int, int]:
    """企業とステップを1件ずつ入れる。

    読めない書式の締切は、サービス層が拒否するようになったため、検証が入る前に
    保存された古いデータとしてリポジトリから直接書く。
    """
    database = open_db(path)
    try:
        selection = SelectionService(database)
        company_id = selection.add_company(Company(name="テスト株式会社"), with_default_steps=False)
        step_id = selection.add_step(company_id, "ES")
        if deadline is not None:
            with transaction(database):
                StepRepository(database).update(step_id, deadline=deadline)
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


class TestValuesOutsideTheChoices:
    """定数を変える前に保存した値があっても、画面が落ちず、黙って書き換えないこと。"""

    def test_company_page_renders_a_legacy_value_instead_of_crashing(self, app_db):
        company_id, step_id = seed_company(app_db)
        database = open_db(app_db)
        try:
            with transaction(database):
                CompanyRepository(database).update(company_id, priority="Z")
                StepRepository(database).update(step_id, result="保留")
        finally:
            database.close()

        at = open_page(app_db, "企業管理")
        # 「志望度」は追加フォームにもあるので、編集フォーム側（末尾）を見る
        priority = [s for s in at.selectbox if s.label == "志望度"][-1]
        assert priority.value == "Z"
        assert "Z" in priority.options
        result = [s for s in at.selectbox if s.label == "結果"][0]
        assert result.value == "保留"

    def test_saving_untouched_steps_keeps_a_legacy_result(self, app_db):
        _, step_id = seed_company(app_db)
        database = open_db(app_db)
        try:
            with transaction(database):
                StepRepository(database).update(step_id, result="保留")
        finally:
            database.close()

        at = open_page(app_db, "企業管理")
        [b for b in at.button if b.label == "選考ステップを保存"][0].click().run()
        assert not at.exception, at.exception
        assert read_step(app_db, step_id)[1] == "保留"

    def test_updating_a_company_with_a_legacy_value_is_refused_with_a_message(self, app_db):
        """古い値のまま「更新」を押すと、選択肢から選び直すよう文面で伝えること。"""
        company_id, _ = seed_company(app_db)
        database = open_db(app_db)
        try:
            with transaction(database):
                CompanyRepository(database).update(company_id, priority="Z")
        finally:
            database.close()

        at = open_page(app_db, "企業管理")
        [b for b in at.button if b.label == "更新"][0].click().run()
        assert not at.exception, at.exception
        assert any("志望度「Z」は選択肢にありません" in e.value for e in at.error)


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

    def test_a_failed_step_save_shows_a_message_and_writes_nothing(self, app_db, monkeypatch):
        """保存の途中で失敗したら、生の例外ではなく文面で伝え、どの行も書かれないこと。

        行ごとに別の境界で書いていると、2行目の失敗で1行目だけが残ったうえに
        トレースバックが出る。
        """
        company_id, first_id = seed_company(app_db, deadline="2026-10-01")
        database = open_db(app_db)
        try:
            second_id = SelectionService(database).add_step(company_id, "1次面接")
        finally:
            database.close()
        at = open_page(app_db, "企業管理")

        original = StepRepository.update
        calls = 0

        def flaky(repository, step_id, **fields):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise DatabaseError("2行目で失敗")
            return original(repository, step_id, **fields)

        monkeypatch.setattr(StepRepository, "update", flaky)
        results = [s for s in at.selectbox if s.label == "結果"]
        results[0].set_value("通過")
        results[1].set_value("落選")
        [b for b in at.button if b.label == "選考ステップを保存"][0].click().run()

        assert not at.exception, at.exception
        assert any("保存できませんでした" in e.value for e in at.error)
        assert read_step(app_db, first_id)[1] == "選考中"
        assert read_step(app_db, second_id)[1] == "選考中"

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

    def test_step_name_in_the_status_heading_is_escaped(self, app_db):
        """企業の見出しの状況（「〇〇待ち」）にもステップ名が入るので、同じく解釈させない。"""
        database = open_db(app_db)
        try:
            selection = SelectionService(database)
            company_id = selection.add_company(Company(name="テスト株式会社"), with_default_steps=False)
            selection.add_step(company_id, "![画像](https://example.com/t.png)")
        finally:
            database.close()
        at = open_page(app_db, "企業管理")
        heading = [m.value for m in at.markdown if m.value.startswith("### ")][0]
        assert "![画像](" not in heading
        assert r"\!\[画像\]" in heading

    def test_streamlit_specific_syntax_is_not_interpreted(self, app_db):
        """数式（$）・絵文字や色やアイコン（:…:）も、書いたとおりに出す。"""
        database = open_db(app_db)
        try:
            SelectionService(database).add_company(
                Company(name="テスト株式会社", memo="年収$500万〜$800万 :material/home: 10:00"),
                with_default_steps=False,
            )
        finally:
            database.close()
        at = open_page(app_db, "企業管理")
        memo = [c.value for c in at.caption if "年収" in c.value][0]
        assert r"\$500" in memo
        assert ":material/home:" not in memo
        assert "&#58;material/home&#58;" in memo


class TestDestructiveActionsNeedConfirmation:
    def test_delete_is_disabled_until_confirmed(self, app_db):
        company_id, _ = seed_company(app_db)
        at = open_page(app_db, "企業管理")
        # 「削除する」はステップの確認にもあるので、企業のものはキーで選ぶ
        assert at.button(key="delete_company").disabled

        database = open_db(app_db)
        try:
            assert SelectionService(database).company(company_id) is not None
        finally:
            database.close()

    def test_step_delete_is_behind_a_confirmation(self, app_db):
        """ステップの削除は、何が失われるかを見せた上の「削除する」だけで行えること。"""
        company_id, step_id = seed_company(app_db)
        at = open_page(app_db, "企業管理")

        assert not [b for b in at.button if b.label == "削除"], "1クリックで消える削除ボタンが残っている"
        button = at.button(key=f"delstep{step_id}")
        assert button.label == "削除する"
        assert any("元に戻せません" in w.value for w in at.warning)

        button.click().run()
        assert not at.exception, at.exception
        database = open_db(app_db)
        try:
            assert SelectionService(database).steps_of(company_id) == []
        finally:
            database.close()

    def test_review_delete_is_behind_a_confirmation(self, app_db):
        """所見の削除も同じ扱いであること。"""
        answer_id = seed_answer(app_db)
        database = open_db(app_db)
        try:
            es = EsService(database)
            stored = es.answer(answer_id)
            assert stored is not None
            review_id = ReviewService(database).run(stored).id
        finally:
            database.close()
        at = open_page(app_db, "添削")

        assert not [b for b in at.button if b.label == "削除"]
        button = at.button(key=f"rm_review_{review_id}")
        assert button.label == "削除する"

        button.click().run()
        assert not at.exception, at.exception
        database = open_db(app_db)
        try:
            assert ReviewService(database).history(answer_id) == []
        finally:
            database.close()

    def test_delete_works_once_confirmed(self, app_db):
        company_id, _ = seed_company(app_db)
        at = open_page(app_db, "企業管理")
        confirm = [c for c in at.checkbox if "削除することを理解しました" in c.label]
        assert confirm, "確認のチェックが見つからない"
        confirm[0].set_value(True).run()
        at.button(key="delete_company").click().run()
        assert not at.exception, at.exception

        database = open_db(app_db)
        try:
            assert SelectionService(database).company(company_id) is None
        finally:
            database.close()


def seed_answer(path, text: str = "初稿") -> int:
    database = open_db(path)
    try:
        return EsService(database).add(EsAnswer(question="志望動機", category="志望動機", answer=text))
    finally:
        database.close()


def read_answer(path, answer_id: int) -> str:
    database = open_db(path)
    try:
        stored = EsService(database).answer(answer_id)
        assert stored is not None
        return stored.answer
    finally:
        database.close()


def answer_text_area(at: AppTest):
    """回答一覧の入力欄。「設問・回答を追加」の入力欄も同じラベルなので、末尾を取る。"""
    areas = [t for t in at.text_area if t.label == "回答"]
    assert len(areas) >= 2, "回答の入力欄が見つからない"
    return areas[-1]


class TestEsStaleTab:
    """ES 本文でも、古いタブが新しい変更を潰さないこと（選考ステップと同じ性質）。

    更新日は日付単位なので、同じ日のうちの更新は入力欄のキーで見分けられなかった。
    """

    def test_a_stale_tab_cannot_overwrite_a_newer_answer(self, app_db):
        answer_id = seed_answer(app_db)
        at = open_page(app_db, "ES管理")

        database = open_db(app_db)
        try:
            EsService(database).update_text(answer_id, "第二稿")  # 同じ日のうちの更新
        finally:
            database.close()

        answer_text_area(at).set_value("古いタブの編集")
        at.button(key=f"save{answer_id}").click().run()

        assert not at.exception, at.exception
        assert read_answer(app_db, answer_id) == "第二稿"
        assert any("他の場所で更新された" in w.value for w in at.warning)
        assert not any("変更はありませんでした" in i.value for i in at.info)

    def test_saving_from_a_fresh_tab_still_works(self, app_db):
        answer_id = seed_answer(app_db)
        at = open_page(app_db, "ES管理")
        answer_text_area(at).set_value("推敲した本文")
        at.button(key=f"save{answer_id}").click().run()

        assert not at.exception, at.exception
        assert read_answer(app_db, answer_id) == "推敲した本文"
        assert not any("他の場所で更新された" in w.value for w in at.warning)


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


class TestCsvImport:
    """取り込みは、要約を見せてから、押されたときにだけ書くこと。"""

    CSV = (
        "企業名,業界,ステップ,締切,結果,パスワード\n"
        "アオゾラ電機,メーカー,ES,2026-10-01,通過,hunter2\n"
        "アオゾラ電機,,1次面接,,,\n"
        "ミカヅキ銀行,金融,ES,10月5日,,\n"
    )

    @staticmethod
    def company_names(path) -> list[str]:
        database = open_db(path)
        try:
            return [c.name for c in SelectionService(database).companies()]
        finally:
            database.close()

    def upload(self, app_db, text: str, codec: str = "cp932") -> AppTest:
        at = open_page(app_db, "取り込み")
        at.file_uploader[0].set_value(("export.csv", text.encode(codec), "text/csv")).run()
        assert not at.exception, at.exception
        return at

    def test_summary_is_shown_and_nothing_is_written_until_confirmed(self, app_db):
        at = self.upload(app_db, self.CSV)
        metrics = {m.label: m.value for m in at.metric}
        assert metrics == {"追加される企業": "1 社", "追加されるステップ": "2 件", "取り込まない行": "1 行"}
        tables = [frame.value for frame in at.dataframe]
        skipped = [t for t in tables if "理由" in t.columns and "行" in t.columns][0]
        assert list(skipped["行"]) == [4]
        assert "10月5日" in skipped["理由"].iloc[0]
        ignored = [t for t in tables if list(t.columns) == ["見出し", "理由"]][0]
        assert list(ignored["見出し"]) == ["パスワード"]
        assert "hunter2" not in " ".join(t.to_string() for t in tables)
        assert self.company_names(app_db) == []

    def test_confirming_writes_what_the_summary_showed(self, app_db):
        at = self.upload(app_db, self.CSV)
        [b for b in at.button if b.label == "この内容で取り込む"][0].click().run()
        assert not at.exception, at.exception
        assert self.company_names(app_db) == ["アオゾラ電機"]
        assert any("1 社・2 件のステップを取り込みました" in s.value for s in at.success)
        at.run()
        assert at.file_uploader[0].value is None, "取り込み後は選択済みのファイルを外す"
        assert not [b for b in at.button if b.label == "この内容で取り込む"]

    def test_unreadable_file_shows_a_message_not_a_traceback(self, app_db):
        at = self.upload(app_db, "名前,ステップ\nアオゾラ電機,ES\n")
        assert any("企業名" in e.value for e in at.error)
        assert not [b for b in at.button if b.label == "この内容で取り込む"]

    def test_a_summary_that_went_stale_is_not_applied(self, app_db):
        """要約を出したあとで登録内容が変わったら、押されても書かずに知らせること。

        見せた要約と違う内容を、確認なしに書かない。残りの企業だけを黙って入れることもしない。
        """
        at = self.upload(app_db, "企業名,ステップ\nアオゾラ電機,ES\nコダマ製作所,ES\n")
        database = open_db(app_db)
        try:
            SelectionService(database).add_company(
                Company(name="アオゾラ電機", memo="先に登録"), with_default_steps=False
            )
        finally:
            database.close()

        [b for b in at.button if b.label == "この内容で取り込む"][0].click().run()
        assert not at.exception, at.exception
        assert any("取り込みは行っていません" in w.value for w in at.warning)
        assert self.company_names(app_db) == ["アオゾラ電機"]
        assert {m.label: m.value for m in at.metric}["追加される企業"] == "1 社"

        [b for b in at.button if b.label == "この内容で取り込む"][0].click().run()
        assert not at.exception, at.exception
        database = open_db(app_db)
        try:
            stored = {c.name: c.memo for c in SelectionService(database).companies()}
        finally:
            database.close()
        assert stored == {"アオゾラ電機": "先に登録", "コダマ製作所": ""}


class TestReviewIndustry:
    def test_choosing_no_industry_is_what_gets_sent_and_saved(self, app_db):
        """提出先が金融でも「指定なし」を選べば、金融の観点を足さずに送り、そのとおり残すこと。

        画面の観点表は共通の観点だけなのに、送る文面は提出先の業界に戻っていた。
        """
        database = open_db(app_db)
        try:
            company_id = SelectionService(database).add_company(
                Company(name="テスト株式会社", industry="金融"), with_default_steps=False
            )
            answer_id = EsService(database).add(
                EsAnswer(question="志望動機", answer="本文", company_id=company_id)
            )
        finally:
            database.close()
        at = open_page(app_db, "添削")
        industry = [s for s in at.selectbox if s.label == "観点を寄せる業界"][0]
        assert industry.value == "金融"
        industry.set_value("指定なし").run()
        [b for b in at.button if b.label == "所見を取る"][0].click().run()
        assert not at.exception, at.exception

        database = open_db(app_db)
        try:
            (review,) = ReviewService(database).history(answer_id)
        finally:
            database.close()
        assert review.industry == ""
        assert "数字と根拠の確かさ" not in review.prompt


class TestFormsKeepInputOnError:
    """追加フォームは、弾かれたときに入力を消さず、保存できたときだけ空に戻すこと。

    AppTest は `clear_on_submit` による消去を再現しないため、以前の不具合そのものは
    ここでは再現できない。置き換えた仕組み（成功時だけフォームのキーを変える）が、
    失敗時には作り直さず、成功時には作り直すことを確かめる。
    """

    @staticmethod
    def add_form_answer(at: AppTest):
        """「設問・回答を追加」の回答欄。一覧の入力欄より前に描画される。"""
        return [t for t in at.text_area if t.label == "回答"][0]

    def test_es_answer_survives_a_missing_question(self, app_db):
        at = open_page(app_db, "ES管理")
        self.add_form_answer(at).set_value("書きかけの回答")
        [b for b in at.button if b.label == "保存"][0].click().run()
        assert not at.exception, at.exception

        assert [e.value for e in at.error] == ["設問文を入力してください。"]
        assert self.add_form_answer(at).value == "書きかけの回答"

    def test_es_form_is_cleared_after_saving(self, app_db):
        at = open_page(app_db, "ES管理")
        [t for t in at.text_input if t.label == "設問文"][0].set_value("志望動機")
        self.add_form_answer(at).set_value("保存する回答")
        [b for b in at.button if b.label == "保存"][0].click().run()
        assert not at.exception, at.exception

        assert [t for t in at.text_input if t.label == "設問文"][0].value == ""
        assert self.add_form_answer(at).value == ""

    def test_company_form_survives_a_duplicate_name(self, app_db):
        seed_company(app_db)
        at = open_page(app_db, "企業管理")
        [t for t in at.text_input if t.label == "企業名 *"][0].set_value("テスト株式会社")
        [t for t in at.text_area if t.label == "メモ"][0].set_value("残したいメモ")
        [b for b in at.button if b.label == "追加"][0].click().run()
        assert not at.exception, at.exception

        assert at.error, "重複の知らせが出ていない"
        assert [t for t in at.text_input if t.label == "企業名 *"][0].value == "テスト株式会社"
        assert [t for t in at.text_area if t.label == "メモ"][0].value == "残したいメモ"


class TestCompanySelection:
    def test_updating_the_shown_company_keeps_it_selected(self, app_db):
        """志望度や企業名を変えて更新しても、選択が先頭の企業に戻らないこと。"""
        database = open_db(app_db)
        try:
            selection = SelectionService(database)
            selection.add_company(Company(name="A社", priority="A"), with_default_steps=False)
            b_id = selection.add_company(Company(name="B社", priority="B"), with_default_steps=False)
        finally:
            database.close()
        at = open_page(app_db, "企業管理")
        at.selectbox(key="company_selected").set_value(b_id).run()

        [s for s in at.selectbox if s.label == "志望度"][0].set_value("C").run()
        [b for b in at.button if b.label == "更新"][0].click().run()
        assert not at.exception, at.exception
        assert at.selectbox(key="company_selected").value == b_id
        assert [m.value for m in at.markdown if m.value.startswith("### ")][0].startswith("### B社")

    def test_a_company_named_like_the_generic_choice_does_not_hide_it(self, app_db):
        """「（汎用）」という名前の企業があっても、汎用の回答を登録できること。"""
        database = open_db(app_db)
        try:
            SelectionService(database).add_company(Company(name="（汎用）"), with_default_steps=False)
        finally:
            database.close()
        at = open_page(app_db, "ES管理")
        company = [s for s in at.selectbox if s.label == "企業"][0]
        assert len(company.options) == 2
        [t for t in at.text_input if t.label == "設問文"][0].set_value("志望動機")
        [b for b in at.button if b.label == "保存"][0].click().run()
        assert not at.exception, at.exception

        database = open_db(app_db)
        try:
            (answer,) = EsService(database).answers()
        finally:
            database.close()
        assert answer.company_id is None


class TestLabels:
    def test_review_page_title_matches_the_menu(self, app_db):
        """添削のページの題が、メニューの項目名とずれていないこと。"""
        at = open_page(app_db, "添削")
        assert [t.value for t in at.title] == ["添削"]

    def test_funnel_table_has_no_english_heading(self, app_db):
        """分析ページの表の見出しに、内部の列名が出ていないこと。"""
        _, step_id = seed_company(app_db)
        database = open_db(app_db)
        try:
            SelectionService(database).update_step(step_id, result="通過")
        finally:
            database.close()
        at = open_page(app_db, "分析")
        funnel = at.dataframe[-1].value
        assert funnel.index.name == "選考ステップ"
        assert "step" not in [funnel.index.name, *funnel.columns]


class TestConnectionScope:
    def test_each_session_opens_its_own_connection(self, app_db):
        """セッションごとに別の接続を持つこと。共有すると書き込みが干渉する。"""
        seed_company(app_db)
        first = AppTest.from_file(APP_PATH, default_timeout=60).run()
        second = AppTest.from_file(APP_PATH, default_timeout=60).run()
        assert not first.exception and not second.exception
        assert first.session_state["db"] is not second.session_state["db"]

    def test_a_dead_connection_is_reopened_on_the_next_run(self, app_db):
        """サーバーの再起動などで接続が死んでも、次の再描画で張り直すこと。

        死んだ接続を持ち続けると、以後の操作がすべて生の例外で失敗する。
        """
        seed_company(app_db)
        at = AppTest.from_file(APP_PATH, default_timeout=60).run()
        assert not at.exception, at.exception
        at.session_state["db"].close()

        at.run()
        assert not at.exception, at.exception
        assert at.session_state["db"].ping()
        assert {m.label: m.value for m in at.metric}["エントリー企業"] == "1 社"


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
