"""依頼文の組み立ての検証。"""

from __future__ import annotations

import pytest

from shukatsu_tracker.review import criteria as criteria_module
from shukatsu_tracker.review import prompt as prompt_module
from shukatsu_tracker.review.prompt import ReviewRequest

REQUEST = ReviewRequest(
    question="学生時代に力を入れたことを教えてください。",
    answer="所属する団体で、出欠管理が紙運用で滞っていた課題に取り組んだ。",
    char_limit=400,
    industry="SIer・IT",
    company_name="テスト株式会社",
)


def test_contains_the_target():
    built = prompt_module.build(REQUEST)
    assert REQUEST.question in built
    assert REQUEST.answer in built
    assert "テスト株式会社" in built
    assert "SIer・IT" in built
    assert f"{REQUEST.length} / 400" in built


def test_reports_length_without_a_limit():
    built = prompt_module.build(
        ReviewRequest(question="設問", answer="回答本文", char_limit=None)
    )
    assert "制限の指定なし" in built


def test_lists_every_criterion():
    criteria = criteria_module.for_industry("SIer・IT")
    built = prompt_module.build(REQUEST, criteria)
    for criterion in criteria:
        assert criterion.title in built


def test_industry_changes_the_criteria_shown():
    finance = prompt_module.build(
        ReviewRequest(question="設問", answer="回答本文", industry="金融")
    )
    web = prompt_module.build(
        ReviewRequest(question="設問", answer="回答本文", industry="Web・ネット")
    )
    assert finance != web
    assert "数字と根拠の確かさ" in finance
    assert "数字と根拠の確かさ" not in web
    assert "使う人の視点" in web


def test_note_is_included_only_when_given():
    assert "書き手からの補足" not in prompt_module.build(REQUEST)
    with_note = prompt_module.build(
        ReviewRequest(question="設問", answer="回答本文", note="文字数を削りたい")
    )
    assert "文字数を削りたい" in with_note


def test_multiline_criteria_do_not_break_the_table():
    built = prompt_module.build(REQUEST)
    table = [line for line in built.splitlines() if line.startswith("| ")]
    assert len(table) >= 10
    for row in table:
        assert row.count("|") == 5


@pytest.mark.parametrize(
    ("question", "answer"),
    [("", "回答本文"), ("設問", ""), ("   ", "回答本文"), ("設問", "   ")],
)
def test_empty_input_is_rejected(question, answer):
    with pytest.raises(ValueError):
        prompt_module.build(ReviewRequest(question=question, answer=answer))


def test_system_prompt_forbids_inventing_facts():
    assert "補って書かないこと" in prompt_module.SYSTEM_PROMPT
    assert "点数や合否の判定はしないこと" in prompt_module.SYSTEM_PROMPT
