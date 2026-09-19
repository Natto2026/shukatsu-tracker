"""テスト用のデータ生成。集計テストが値の組み立てだけで書けるようにする。"""

from __future__ import annotations

from shukatsu_tracker.models import StepView

_STEP_DEFAULTS = {
    "id": 1,
    "company_id": 1,
    "company_name": "テスト社",
    "name": "ES",
    "deadline": None,
    "result": "選考中",
    "memo": "",
    "sort_order": 0,
    "industry": "SIer・IT",
    "route": "一般公募",
    "test_type": "SPI",
}


def make_step(**overrides) -> StepView:
    """必要な項目だけ指定して StepView を作る。"""
    return StepView(**{**_STEP_DEFAULTS, **overrides})
