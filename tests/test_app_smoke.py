"""Streamlit AppTest による画面のスモークテスト。

各ページが例外なく描画されることと、データ登録後のダッシュボード表示を確認する。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from shukatsu_tracker import db
from shukatsu_tracker.models import Company
from shukatsu_tracker.services import SelectionService

APP_PATH = str(Path(__file__).parent.parent / "app.py")
PAGES = ["ダッシュボード", "企業管理", "ES管理", "添削", "分析", "書き出し"]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SHUKATSU_DB", str(tmp_path / "smoke.db"))
    return AppTest.from_file(APP_PATH, default_timeout=30)


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_error(app, page):
    at = app.run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception


def test_dashboard_shows_registered_company(app, tmp_path):
    conn = db.connect(tmp_path / "smoke.db")
    SelectionService(conn).add_company(
        Company(name="サンプル株式会社", route="スカウト・逆求人")
    )
    conn.close()

    at = app.run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["エントリー企業"] == "1 社"
    assert metrics["選考継続中"] == "1 社"
