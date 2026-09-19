"""観点の定義（TOML）の検証。"""

from __future__ import annotations

import tomllib

import pytest

from shukatsu_tracker.review import criteria as criteria_module
from shukatsu_tracker.review import prompt as prompt_module
from shukatsu_tracker.review.prompt import ReviewRequest

# 観点は「一般的な書き方の整理」として持つ。特定の組織の基準として
# 提示しないことを、文面そのもので担保する。
FORBIDDEN_PHRASES = ["株式会社", "選考基準", "評価基準", "社の基準", "人事によると"]


def all_criteria_files():
    return [criteria_module.BASE_FILE, *criteria_module.INDUSTRY_DIR.glob("*.toml")]


def test_base_criteria_load():
    base = criteria_module.base_criteria()
    assert len(base) >= 10
    assert len({c.id for c in base}) == len(base)


@pytest.mark.parametrize("path", all_criteria_files(), ids=lambda p: p.name)
def test_every_file_is_valid_toml(path):
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    assert "meta" in raw
    for entry in raw.get("criteria", []):
        assert {"id", "title", "check", "weak"} <= set(entry)
        assert 1 <= int(entry.get("weight", 1)) <= 4


@pytest.mark.parametrize(
    "industry", [None, *criteria_module.available_industries()], ids=str
)
def test_sent_text_never_claims_a_specific_organisation_standard(industry):
    """送られる文面に「どこかの組織の基準だ」と読める記述を混ぜない。

    ファイル冒頭の注記（「特定の企業の評価基準ではない」）は文面には出ないため、
    検査対象はコメントを含む生ファイルではなく、組み上がった依頼文にする。
    """
    built = prompt_module.build(
        ReviewRequest(question="設問", answer="回答本文", industry=industry)
    )
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in built, f"{industry} の文面に「{phrase}」が含まれています"


def test_base_file_states_it_is_not_a_specific_standard():
    header = criteria_module.BASE_FILE.read_text(encoding="utf-8")[:300]
    assert "特定の企業の評価基準ではない" in header


@pytest.mark.parametrize("industry", criteria_module.available_industries())
def test_industry_overlay_extends_the_base(industry):
    base = criteria_module.base_criteria()
    merged = criteria_module.for_industry(industry)
    assert len(merged) >= len(base)
    assert set(c.id for c in base) <= set(merged.ids)
    assert merged.industry == industry


def test_emphasis_raises_the_weight():
    base = {c.id: c for c in criteria_module.base_criteria()}
    merged = {c.id: c for c in criteria_module.for_industry("SIer・IT")}
    assert merged["plain_language"].weight > base["plain_language"].weight


def test_weight_is_capped():
    for industry in criteria_module.available_industries():
        for criterion in criteria_module.for_industry(industry):
            assert criterion.weight <= 4


def test_unknown_industry_falls_back_to_base_only():
    merged = criteria_module.for_industry("その他")
    assert merged.ids == tuple(
        c.id for c in sorted(criteria_module.base_criteria(), key=lambda c: (-c.weight, c.id))
    )


def test_none_industry_is_accepted():
    merged = criteria_module.for_industry(None)
    assert merged.industry == "指定なし"
    assert len(merged) == len(criteria_module.base_criteria())


def test_criteria_are_ordered_by_weight():
    merged = criteria_module.for_industry("金融")
    weights = [c.weight for c in merged]
    assert weights == sorted(weights, reverse=True)


def test_emphasis_must_reference_a_defined_criterion(tmp_path):
    (tmp_path / "broken.toml").write_text(
        '[meta]\nname = "壊れた例"\nmatches = "架空業界"\nemphasis = ["存在しない観点"]\n',
        encoding="utf-8",
    )
    criteria_module._industry_files.cache_clear()
    try:
        original = criteria_module.INDUSTRY_DIR
        criteria_module.INDUSTRY_DIR = tmp_path
        criteria_module._industry_files.cache_clear()
        with pytest.raises(ValueError, match="emphasis"):
            criteria_module.for_industry("架空業界")
    finally:
        criteria_module.INDUSTRY_DIR = original
        criteria_module._industry_files.cache_clear()
