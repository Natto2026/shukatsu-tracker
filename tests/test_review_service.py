"""所見のユースケースの検証。"""

from __future__ import annotations

import pytest

from shukatsu_tracker.models import Company, EsAnswer
from shukatsu_tracker.review.providers import ReviewResult
from shukatsu_tracker.services import EsService, SelectionService


class StubProvider:
    """所見を返したことにする実行先。"""

    name = "スタブ"
    sends_data_externally = False

    def __init__(self, text: str = "所見の本文") -> None:
        self.text = text
        self.seen: list[str] = []

    def review(self, request, prompt):
        self.seen.append(prompt)
        return ReviewResult(provider=self.name, prompt=prompt, text=self.text, model=None)


@pytest.fixture
def saved_answer(conn) -> EsAnswer:
    es = EsService(conn)
    answer_id = es.add(
        EsAnswer(question="学生時代に力を入れたこと", answer="団体での活動に取り組んだ。", char_limit=400)
    )
    stored = es.answer(answer_id)
    assert stored is not None
    return stored


def test_run_saves_the_review(reviewer, saved_answer):
    review = reviewer.run(saved_answer, provider=StubProvider())
    assert review.id is not None
    assert review.result == "所見の本文"
    assert review.provider == "スタブ"
    assert review.created_at is not None
    assert reviewer.history(saved_answer.id or -1) == [review]


def test_prompt_is_stored_with_the_result(reviewer, saved_answer):
    review = reviewer.run(saved_answer, provider=StubProvider())
    assert saved_answer.question in review.prompt
    assert saved_answer.answer in review.prompt


def test_snapshot_detects_a_later_edit(reviewer, saved_answer, conn):
    review = reviewer.run(saved_answer, provider=StubProvider())
    assert review.applies_to(saved_answer.answer)

    es = EsService(conn)
    es.update_text(saved_answer.id or -1, "書き直した本文")
    updated = es.answer(saved_answer.id or -1)
    assert updated is not None
    assert not review.applies_to(updated.answer)


def test_history_is_newest_first(reviewer, saved_answer):
    first = reviewer.run(saved_answer, provider=StubProvider("1回目"))
    second = reviewer.run(saved_answer, provider=StubProvider("2回目"))
    assert [r.id for r in reviewer.history(saved_answer.id or -1)] == [second.id, first.id]
    latest = reviewer.latest(saved_answer.id or -1)
    assert latest is not None
    assert latest.result == "2回目"


def test_unsaved_answer_is_rejected(reviewer):
    with pytest.raises(ValueError, match="保存されていない"):
        reviewer.run(EsAnswer(question="設問", answer="本文"), provider=StubProvider())


def test_industry_comes_from_the_company(conn, reviewer):
    selection = SelectionService(conn)
    company_id = selection.add_company(
        Company(name="テスト株式会社", industry="金融"), with_default_steps=False
    )
    es = EsService(conn)
    answer_id = es.add(EsAnswer(question="設問", answer="本文", company_id=company_id))
    stored = es.answer(answer_id)
    assert stored is not None

    assert reviewer.industry_of(stored) == "金融"
    built = reviewer.build_prompt(stored)
    assert "数字と根拠の確かさ" in built


def test_industry_can_be_overridden(conn, reviewer, saved_answer):
    built = reviewer.build_prompt(saved_answer, industry="Web・ネット")
    assert "使う人の視点" in built


def test_answer_without_a_company_has_no_industry(reviewer, saved_answer):
    assert reviewer.industry_of(saved_answer) is None


def test_default_provider_does_not_send_data(reviewer, saved_answer):
    review = reviewer.run(saved_answer)
    assert review.model is None
    assert review.result == review.prompt


def test_review_is_removed_with_its_answer(conn, reviewer, saved_answer):
    reviewer.run(saved_answer, provider=StubProvider())
    EsService(conn).delete(saved_answer.id or -1)
    assert reviewer.history(saved_answer.id or -1) == []


def test_delete_removes_one_review(reviewer, saved_answer):
    review = reviewer.run(saved_answer, provider=StubProvider())
    reviewer.delete(review.id or -1)
    assert reviewer.history(saved_answer.id or -1) == []
