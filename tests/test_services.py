"""サービス層の検証。業務ルールがここに集約されていることを見る。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from shukatsu_tracker import constants
from shukatsu_tracker.db import DatabaseError, DuplicateKeyError, StepRepository
from shukatsu_tracker.models import Company, EsAnswer
from shukatsu_tracker.services import StepChange


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
        original = StepRepository.list_for_company

        def spy(repository, target_id):
            depths.append(conn.depth)
            return original(repository, target_id)

        monkeypatch.setattr(StepRepository, "list_for_company", spy)
        selection.add_step(company_id, "リクルーター面談")
        assert depths == [1]


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
