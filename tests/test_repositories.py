"""リポジトリ層の検証。SQL とモデルの往復が壊れていないことを見る。"""

from __future__ import annotations

import pytest

from shukatsu_tracker.db import (
    CompanyRepository,
    DuplicateKeyError,
    EsAnswerRepository,
    StepRepository,
    transaction,
)
from shukatsu_tracker.db.dialects import PostgresDialect, SqliteDialect
from shukatsu_tracker.models import Company, EsAnswer, Step


@pytest.fixture
def companies(conn) -> CompanyRepository:
    return CompanyRepository(conn)


@pytest.fixture
def steps(conn) -> StepRepository:
    return StepRepository(conn)


@pytest.fixture
def answers(conn) -> EsAnswerRepository:
    return EsAnswerRepository(conn)


class TestCompanyRepository:
    def test_add_and_get_roundtrip(self, companies):
        company_id = companies.add(Company(name="テスト株式会社", route="スカウト・逆求人", priority="A"))
        stored = companies.get(company_id)
        assert stored is not None
        assert stored.name == "テスト株式会社"
        assert stored.route == "スカウト・逆求人"
        assert stored.created_at is not None

    def test_sorted_by_priority_meaning_not_alphabet(self, companies):
        for name, priority in [("B社", "B"), ("S社", "S"), ("C社", "C"), ("A社", "A")]:
            companies.add(Company(name=name, priority=priority))
        assert [c.name for c in companies.list_all()] == ["S社", "A社", "B社", "C社"]

    def test_duplicate_name_is_rejected(self, companies):
        companies.add(Company(name="テスト株式会社"))
        with pytest.raises(DuplicateKeyError):
            companies.add(Company(name="テスト株式会社"))

    def test_get_returns_none_for_missing_row(self, companies):
        assert companies.get(999) is None

    def test_unknown_column_is_rejected(self, companies):
        """呼び出し側の打ち間違いを SQL に届かせない。"""
        company_id = companies.add(Company(name="テスト株式会社"))
        with pytest.raises(ValueError, match="書き込めない列"):
            companies.update(company_id, priorityy="S")

    def test_id_column_cannot_be_overwritten(self, companies):
        company_id = companies.add(Company(name="テスト株式会社"))
        with pytest.raises(ValueError):
            companies.update(company_id, id=42)


class TestStepRepository:
    def test_views_are_joined_with_company_fields(self, companies, steps):
        company_id = companies.add(Company(name="テスト株式会社", route="ハッカソン・イベント"))
        steps.add(Step(company_id=company_id, name="ES", deadline="2026-08-10"))
        view = steps.list_views()[0]
        assert view.company_name == "テスト株式会社"
        assert view.route == "ハッカソン・イベント"

    def test_ordered_by_sort_order(self, companies, steps):
        company_id = companies.add(Company(name="テスト株式会社"))
        steps.add(Step(company_id=company_id, name="2次面接", sort_order=1))
        steps.add(Step(company_id=company_id, name="ES", sort_order=0))
        assert [s.name for s in steps.list_for_company(company_id)] == ["ES", "2次面接"]

    def test_deleting_company_cascades_steps(self, companies, steps):
        company_id = companies.add(Company(name="テスト株式会社"))
        steps.add(Step(company_id=company_id, name="ES"))
        companies.delete(company_id)
        assert steps.list_views() == []


class TestRowLock:
    """行ロックの差し込み記号が方言ごとに正しく展開されること。"""

    def test_postgres_appends_for_update(self):
        rendered = PostgresDialect().render("SELECT id FROM companies WHERE id = ? {{FOR_UPDATE}}")
        assert rendered.rstrip().endswith("FOR UPDATE")

    def test_sqlite_appends_nothing(self):
        rendered = SqliteDialect().render("SELECT id FROM companies WHERE id = ? {{FOR_UPDATE}}")
        assert "FOR UPDATE" not in rendered
        assert "{{" not in rendered

    def test_lock_reports_whether_the_row_exists(self, conn, companies):
        company_id = companies.add(Company(name="テスト株式会社"))
        with transaction(conn):
            assert companies.lock(company_id) is True
            assert companies.lock(999) is False


class TestEsAnswerRepository:
    def test_answer_survives_company_deletion(self, companies, answers):
        company_id = companies.add(Company(name="テスト株式会社"))
        answers.add(EsAnswer(question="学生時代に力を入れたこと", company_id=company_id, char_limit=400))
        companies.delete(company_id)
        stored = answers.list_all()
        assert len(stored) == 1
        assert stored[0].company_id is None

    def test_update_refreshes_the_timestamp(self, answers):
        answer_id = answers.add(EsAnswer(question="設問", answer="初版"))
        answers.update(answer_id, answer="改訂版")
        stored = answers.get(answer_id)
        assert stored is not None
        assert stored.answer == "改訂版"
        assert stored.updated_at is not None

    def test_company_name_is_joined(self, companies, answers):
        company_id = companies.add(Company(name="テスト株式会社"))
        answer_id = answers.add(EsAnswer(question="設問", company_id=company_id))
        stored = answers.get(answer_id)
        assert stored is not None
        assert stored.company_name == "テスト株式会社"
