"""テスト共通のフィクスチャ。

既定は SQLite。環境変数 SHUKATSU_TEST_DSN を設定すると、同じテストが
そのまま PostgreSQL に対して走る（テストごとに専用のスキーマを作る）。

    SHUKATSU_TEST_DSN=postgresql://user:pass@127.0.0.1:5432/shukatsu_test pytest
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from shukatsu_tracker import db
from shukatsu_tracker.db import Database
from shukatsu_tracker.services import EsService, ReviewService, SelectionService

POSTGRES_DSN = os.environ.get("SHUKATSU_TEST_DSN")


def _postgres_database() -> tuple[Database, str]:
    import psycopg

    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(POSTGRES_DSN, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    separator = "&" if "?" in str(POSTGRES_DSN) else "?"
    dsn = f"{POSTGRES_DSN}{separator}options=-csearch_path%3D{schema}"
    return db.connect(dsn), schema


def _drop_schema(schema: str) -> None:
    import psycopg

    with psycopg.connect(POSTGRES_DSN, autocommit=True) as admin:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


@contextmanager
def shared_target(tmp_path: Path) -> Iterator[str]:
    """複数の接続から開ける保存先を用意する。セッションの同時実行を再現するときに使う。

    SQLite は1つのファイル、PostgreSQL は専用のスキーマを指す接続文字列を返す。
    """
    if not POSTGRES_DSN:
        yield str(tmp_path / "shared.db")
        return
    import psycopg

    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(POSTGRES_DSN, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    separator = "&" if "?" in str(POSTGRES_DSN) else "?"
    try:
        yield f"{POSTGRES_DSN}{separator}options=-csearch_path%3D{schema}"
    finally:
        _drop_schema(schema)


@pytest.fixture
def conn(tmp_path):
    if POSTGRES_DSN:
        database, schema = _postgres_database()
        yield database
        database.close()
        _drop_schema(schema)
        return
    database = db.connect(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def selection(conn) -> SelectionService:
    return SelectionService(conn)


@pytest.fixture
def es(conn) -> EsService:
    return EsService(conn)


@pytest.fixture
def reviewer(conn) -> ReviewService:
    return ReviewService(conn)


def table_names(database: Database) -> set[str]:
    """現在のスキーマにあるテーブル名。方言ごとに引き方が違う。"""
    if database.dialect.name == "sqlite":
        rows = database.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in rows}
    rows = database.fetchall("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
    return {row["tablename"] for row in rows}
