"""サービス層の検証。業務ルールがここに集約されていることを見る。"""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta

import pytest
from conftest import shared_target

from shukatsu_tracker import constants, db
from shukatsu_tracker.db import DatabaseError, DuplicateKeyError, EsAnswerRepository, StepRepository
from shukatsu_tracker.models import Company, EsAnswer
from shukatsu_tracker.services import EsService, StaleAnswerError, StepChange


def days_from_today(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


class TestAddCompany:
    def test_default_steps_are_created(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"))
        names = [s.name for s in selection.steps_of(company_id)]
        assert names == list(constants.DEFAULT_STEPS)

    def test_default_steps_can_be_skipped(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"), with_default_steps=False)
        assert selection.steps_of(company_id) == []

    def test_name_is_trimmed_and_required(self, selection):
        company_id = selection.add_company(Company(name="  テスト株式会社  "))
        company = selection.company(company_id)
        assert company is not None
        assert company.name == "テスト株式会社"
        with pytest.raises(ValueError):
            selection.add_company(Company(name="   "))

    def test_duplicate_name_leaves_no_orphan_steps(self, selection):
        """企業の登録に失敗したら、既定ステップも書かれていないこと。"""
        selection.add_company(Company(name="テスト株式会社"))
        with pytest.raises(DuplicateKeyError):
            selection.add_company(Company(name="テスト株式会社"))
        assert len(selection.all_step_views()) == len(constants.DEFAULT_STEPS)


class TestCompanyValidation:
    """選択肢と書式の検証がサービス層にあること（画面や CSV だけに置かない）。"""

    @pytest.mark.parametrize("name", ["A社\n# 見出し", "A社\tB", "A\r社"])
    def test_a_company_name_with_control_characters_is_rejected(self, selection, name):
        with pytest.raises(ValueError, match="制御文字"):
            selection.add_company(Company(name=name), with_default_steps=False)

    def test_renaming_to_a_multi_line_name_is_rejected(self, selection):
        company_id = selection.add_company(Company(name="A社"), with_default_steps=False)
        with pytest.raises(ValueError, match="制御文字"):
            selection.update_company(company_id, name="A社\n2行目")

    def test_a_step_name_with_a_line_break_is_rejected(self, selection):
        company_id = selection.add_company(Company(name="A社"), with_default_steps=False)
        with pytest.raises(ValueError, match="制御文字"):
            selection.add_step(company_id, "ES\n2行目")

    @pytest.mark.parametrize(
        ("field", "value", "label"),
        [
            ("industry", "宇宙", "業界"),
            ("priority", "Z", "志望度"),
            ("route", "縁故", "応募経路"),
            ("test_type", "口頭試問", "適性検査"),
        ],
    )
    def test_a_value_outside_the_choices_is_rejected_on_add(self, selection, field, value, label):
        with pytest.raises(ValueError, match=f"{label}「{value}」は選択肢にありません"):
            selection.add_company(Company(name="テスト株式会社", **{field: value}))
        assert selection.companies() == []

    def test_a_value_outside_the_choices_is_rejected_on_update(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"))
        with pytest.raises(ValueError, match="志望度「Z」は選択肢にありません"):
            selection.update_company(company_id, priority="Z")
        company = selection.company(company_id)
        assert company is not None
        assert company.priority == "B"

    def test_update_trims_the_name_and_rejects_a_blank_one(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"))
        selection.update_company(company_id, name="  新社名  ")
        company = selection.company(company_id)
        assert company is not None
        assert company.name == "新社名"
        with pytest.raises(ValueError, match="企業名は必須"):
            selection.update_company(company_id, name="   ")

    def test_url_and_email_are_trimmed(self, selection):
        """先頭の空白が付いた URL は「リンクとして開ける」判定に落ちるので、除いて保存する。"""
        company_id = selection.add_company(
            Company(name="テスト株式会社", mypage_url=" https://example.com/ ", login_email=" a@example.com ")
        )
        company = selection.company(company_id)
        assert company is not None
        assert (company.mypage_url, company.login_email) == ("https://example.com/", "a@example.com")
        selection.update_company(company_id, mypage_url="  https://example.com/mypage ")
        company = selection.company(company_id)
        assert company is not None
        assert company.mypage_url == "https://example.com/mypage"

    def test_updating_no_fields_writes_nothing(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"))
        selection.update_company(company_id)
        assert selection.company(company_id) is not None


class TestSteps:
    def test_added_step_goes_to_the_end(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"))
        selection.add_step(company_id, "リクルーター面談")
        assert selection.steps_of(company_id)[-1].name == "リクルーター面談"

    def test_blank_step_name_is_rejected(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"))
        with pytest.raises(ValueError):
            selection.add_step(company_id, "  ")

    def test_undefined_result_is_rejected(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"))
        step_id = selection.steps_of(company_id)[0].id or -1
        with pytest.raises(ValueError, match="未定義の選考結果"):
            selection.update_step(step_id, result="なんとなく通過")

    @pytest.mark.parametrize("value", ["来週", "2026/10/01", "2026-13-01", "10月1日"])
    def test_an_unreadable_deadline_is_rejected_not_nulled(self, selection, value):
        """読めない締切を黙って空にしない。空にすると締切一覧から消えて気づけない。"""
        company_id = selection.add_company(Company(name="テスト株式会社"))
        step_id = selection.steps_of(company_id)[0].id or -1
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            selection.add_step(company_id, "リクルーター面談", deadline=value)
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            selection.update_step(step_id, deadline=value)
        assert len(selection.steps_of(company_id)) == len(constants.DEFAULT_STEPS)
        assert selection.steps_of(company_id)[0].deadline is None

    def test_a_readable_deadline_is_stored_in_iso_form(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"))
        step_id = selection.add_step(company_id, "リクルーター面談", deadline="2026-10-01")
        selection.update_step(step_id, deadline="2026-11-05")
        assert selection.steps_of(company_id)[-1].deadline == "2026-11-05"

    def test_deadline_can_be_cleared_without_touching_the_result(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"))
        step_id = selection.steps_of(company_id)[0].id or -1
        selection.update_step(step_id, deadline="2026-08-10", result="通過")
        selection.update_step(step_id, deadline=None)
        step = selection.steps_of(company_id)[0]
        assert step.deadline is None
        assert step.result == "通過"

    def test_updating_nothing_is_a_no_op(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"))
        step_id = selection.steps_of(company_id)[0].id or -1
        selection.update_step(step_id)
        assert selection.steps_of(company_id)[0].result == "選考中"

    def test_updating_many_steps_validates_every_row_before_writing(self, selection):
        """2行目が不正なら、1行目も書かれないこと。"""
        company_id = selection.add_company(Company(name="テスト株式会社"))
        first, second = [s.id or -1 for s in selection.steps_of(company_id)[:2]]
        with pytest.raises(ValueError, match="未定義の選考結果"):
            selection.update_steps(
                [StepChange(first, result="通過"), StepChange(second, result="なんとなく")]
            )
        assert [s.result for s in selection.steps_of(company_id)[:2]] == ["選考中", "選考中"]

    def test_a_failure_midway_leaves_no_step_updated(self, selection, monkeypatch):
        """3行目の書き込みで失敗したら、1〜2行目も残らないこと。"""
        company_id = selection.add_company(Company(name="テスト株式会社"))
        ids = [s.id or -1 for s in selection.steps_of(company_id)[:3]]
        original = StepRepository.update
        calls = 0

        def flaky(repository, step_id, **fields):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise DatabaseError("3行目で失敗")
            return original(repository, step_id, **fields)

        monkeypatch.setattr(StepRepository, "update", flaky)
        with pytest.raises(DatabaseError, match="3行目で失敗"):
            selection.update_steps([StepChange(step_id, result="通過") for step_id in ids])
        assert {s.result for s in selection.steps_of(company_id)[:3]} == {"選考中"}

    def test_updating_many_steps_counts_only_rows_with_a_change(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"))
        first, second = [s.id or -1 for s in selection.steps_of(company_id)[:2]]
        assert selection.update_steps([StepChange(first, result="通過"), StepChange(second)]) == 1
        assert selection.update_steps([]) == 0
        assert [s.result for s in selection.steps_of(company_id)[:2]] == ["通過", "選考中"]

    def test_add_step_reads_the_order_inside_the_transaction(self, conn, selection, monkeypatch):
        """並び順を決める読み取りが、書き込みと同じ境界の中で行われること。"""
        company_id = selection.add_company(Company(name="テスト株式会社"), with_default_steps=False)
        depths: list[int] = []
        original = StepRepository.next_sort_order

        def spy(repository, target_id):
            depths.append(conn.depth)
            return original(repository, target_id)

        monkeypatch.setattr(StepRepository, "next_sort_order", spy)
        selection.add_step(company_id, "リクルーター面談")
        assert depths == [1]

    def test_add_step_locks_the_company_row_before_reading_the_order(self, conn, selection):
        """同じ企業への同時の追加を直列化するため、並び順を読む前に企業の行をロックすること。"""
        company_id = selection.add_company(Company(name="テスト株式会社"), with_default_steps=False)
        with conn.record() as executed:
            selection.add_step(company_id, "リクルーター面談")
        lock = next(i for i, sql in enumerate(executed) if "FROM companies WHERE id = ?" in sql)
        order = next(i for i, sql in enumerate(executed) if "MAX(sort_order)" in sql)
        assert "{{FOR_UPDATE}}" in executed[lock]
        assert lock < order

    def test_a_step_added_after_a_deletion_does_not_collide(self, selection):
        """途中のステップを消したあとの追加が、既存の並び順と衝突しないこと。

        件数を並び順にしていると、0..5 から 2 を消して足したときに 5 が2つになる。
        """
        company_id = selection.add_company(Company(name="テスト株式会社"))
        steps = selection.steps_of(company_id)
        selection.delete_step(steps[2].id or -1)
        selection.add_step(company_id, "リクルーター面談")
        orders = [s.sort_order for s in selection.steps_of(company_id)]
        assert len(orders) == len(set(orders))
        assert selection.steps_of(company_id)[-1].name == "リクルーター面談"

    def test_adding_a_step_to_a_missing_company_is_reported(self, selection):
        with pytest.raises(ValueError, match="企業が見つかりません"):
            selection.add_step(999, "リクルーター面談")


class TestDashboard:
    def test_counts_and_deadlines(self, selection):
        alive = selection.add_company(Company(name="継続中株式会社"), with_default_steps=False)
        selection.add_step(alive, "ES", deadline=days_from_today(3))
        rejected = selection.add_company(Company(name="落選株式会社"), with_default_steps=False)
        step_id = selection.add_step(rejected, "ES")
        selection.update_step(step_id, result="落選")

        summary = selection.dashboard(date.today())
        assert summary.total_companies == 2
        assert summary.active_companies == 1
        assert [d.step.company_name for d in summary.deadlines] == ["継続中株式会社"]
        assert summary.overdue == []

    def test_a_deadline_left_in_an_ended_company_is_kept_aside_not_dropped(self, selection):
        """落選・辞退した企業に残った締切は、件数に数えず、消さずに別に返す。

        辞退したインターンのあとに足した本選考の締切が、黙って消えないように。
        """
        company_id = selection.add_company(Company(name="テスト株式会社"), with_default_steps=False)
        intern = selection.add_step(company_id, "夏インターン")
        selection.update_step(intern, result="辞退")
        selection.add_step(company_id, "本選考ES", deadline=days_from_today(-1))

        summary = selection.dashboard(date.today())
        assert summary.overdue == []
        assert summary.upcoming == []
        assert [d.step.name for d in summary.left_behind] == ["本選考ES"]

    def test_overdue_is_separated(self, selection):
        company_id = selection.add_company(Company(name="テスト株式会社"), with_default_steps=False)
        selection.add_step(company_id, "ES", deadline=days_from_today(-2))
        summary = selection.dashboard(date.today())
        assert len(summary.overdue) == 1
        assert summary.overdue[0].days_left == -2

    def test_steps_are_fetched_once_for_every_company(self, selection, conn):
        """企業数ぶんの問い合わせを出していないこと（N+1 の防止）。"""
        for name in ["A社", "B社", "C社"]:
            selection.add_company(Company(name=name))

        with conn.record() as executed:
            selection.dashboard(date.today())

        step_queries = [sql for sql in executed if "FROM steps" in sql]
        assert len(step_queries) == 1


class TestEsService:
    def test_question_is_required(self, es):
        with pytest.raises(ValueError):
            es.add(EsAnswer(question="   "))

    def test_zero_char_limit_is_stored_as_none(self, es):
        answer_id = es.add(EsAnswer(question="設問", char_limit=0))
        stored = es.answer(answer_id)
        assert stored is not None
        assert stored.char_limit is None

    def test_negative_char_limit_is_rejected(self, es):
        with pytest.raises(ValueError):
            es.add(EsAnswer(question="設問", char_limit=-1))

    def test_update_refuses_to_overwrite_a_text_that_changed_since_it_was_shown(self, es):
        """表示していた本文と違えば書かない。古い表示からの保存で新しい本文を潰さないため。"""
        answer_id = es.add(EsAnswer(question="志望動機", answer="初稿"))
        es.update_text(answer_id, "第二稿")
        with pytest.raises(StaleAnswerError):
            es.update_text(answer_id, "古いタブの編集", expected="初稿")
        stored = es.answer(answer_id)
        assert stored is not None
        assert stored.answer == "第二稿"

    def test_two_sessions_saving_from_the_same_text_do_not_both_win(self, tmp_path, monkeypatch):
        """同じ本文を表示していた2つのセッションが同時に保存しても、両方は通らないこと。

        PostgreSQL では判定の読み取りが行をロックしておらず、両方が「表示どおり」と
        判定して、後から書いた方が先の保存を黙って潰していた。
        """
        with shared_target(tmp_path) as target:
            setup = db.connect(target)
            answer_id = EsService(setup).add(EsAnswer(question="志望動機", answer="初稿"))
            setup.close()

            # 判定から書き込みまでの間を広げ、相手の判定が割り込める並びを作る
            original = EsAnswerRepository.update

            def slow_update(repository, *args, **kwargs):
                time.sleep(0.5)
                original(repository, *args, **kwargs)

            monkeypatch.setattr(EsAnswerRepository, "update", slow_update)
            sessions = [db.connect(target) for _ in range(2)]
            start = threading.Barrier(2)
            saved: list[str] = []
            refused: list[str] = []
            errors: list[BaseException] = []

            def save(database, text: str) -> None:
                start.wait()
                try:
                    EsService(database).update_text(answer_id, text, expected="初稿")
                    saved.append(text)
                except StaleAnswerError:
                    refused.append(text)
                except BaseException as error:  # pragma: no cover - 失敗時の診断用
                    errors.append(error)

            threads = [
                threading.Thread(target=save, args=(session, text))
                for session, text in zip(sessions, ["タブAの編集", "タブBの編集"], strict=True)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            assert not any(thread.is_alive() for thread in threads)
            for session in sessions:
                session.close()

            assert errors == []
            assert len(saved) == 1
            assert len(refused) == 1
            check = db.connect(target)
            stored = EsService(check).answer(answer_id)
            check.close()
            assert stored is not None
            assert stored.answer == saved[0]

    def test_update_writes_when_the_shown_text_is_still_current(self, es):
        answer_id = es.add(EsAnswer(question="志望動機", answer="初稿"))
        es.update_text(answer_id, "第二稿", expected="初稿")
        stored = es.answer(answer_id)
        assert stored is not None
        assert stored.answer == "第二稿"

    def test_update_of_a_deleted_answer_is_reported(self, es):
        answer_id = es.add(EsAnswer(question="志望動機", answer="初稿"))
        es.delete(answer_id)
        with pytest.raises(ValueError, match="見つかりません"):
            es.update_text(answer_id, "第二稿", expected="初稿")

    def test_search_by_category_and_keyword(self, es):
        es.add(EsAnswer(question="学生時代", category="ガクチカ", answer="体育会の活動"))
        es.add(EsAnswer(question="志望動機", category="志望動機", answer="貴社の事業"))
        assert len(es.search(categories=["ガクチカ"])) == 1
        assert len(es.search(keyword="体育会")) == 1
        assert len(es.search(keyword="存在しない語")) == 0
        assert len(es.search()) == 2

    @pytest.mark.parametrize(
        ("text", "limit", "over", "short"),
        [
            ("あ" * 401, 400, True, False),
            ("あ" * 400, 400, False, False),
            ("あ" * 319, 400, False, True),
            ("あ" * 10, None, False, False),
        ],
    )
    def test_length_check(self, es, text, limit, over, short):
        check = es.length_check(text, limit)
        assert check.over is over
        assert check.short is short
