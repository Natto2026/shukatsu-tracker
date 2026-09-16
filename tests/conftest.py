"""テスト共通のフィクスチャ。"""

from __future__ import annotations

import pytest

from shukatsu_tracker import db
from shukatsu_tracker.services import EsService, SelectionService


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def selection(conn) -> SelectionService:
    return SelectionService(conn)


@pytest.fixture
def es(conn) -> EsService:
    return EsService(conn)
