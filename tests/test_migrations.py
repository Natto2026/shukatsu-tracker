"""スキーマのバージョン管理の検証。"""

from __future__ import annotations

import sqlite3

import pytest

from shukatsu_tracker import db
from shukatsu_tracker.db import migrations


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


def test_connect_creates_all_tables(conn):
    assert {"companies", "steps", "es_answers", "schema_migrations"} <= table_names(conn)


def test_every_migration_is_recorded(conn):
    applied = migrations.applied_versions(conn)
    assert applied == {m.version for m in migrations.discover()}


def test_applying_twice_changes_nothing(conn):
    assert migrations.apply_pending(conn) == []


def test_reconnecting_to_an_existing_database_is_safe(tmp_path):
    path = tmp_path / "existing.db"
    first = db.connect(path)
    first.execute("INSERT INTO companies (name) VALUES ('既存社')")
    first.commit()
    first.close()

    second = db.connect(path)
    assert migrations.apply_pending(second) == []
    names = [row[0] for row in second.execute("SELECT name FROM companies").fetchall()]
    assert names == ["既存社"]
    second.close()


def test_migration_filenames_follow_the_convention(tmp_path):
    (tmp_path / "bad-name.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError):
        migrations.discover(tmp_path)


def test_a_failing_migration_is_rolled_back(conn, tmp_path):
    (tmp_path / "900_broken.sql").write_text(
        "CREATE TABLE ok_so_far (id INTEGER); NOT VALID SQL;", encoding="utf-8"
    )
    with pytest.raises(sqlite3.Error):
        migrations.apply_pending(conn, tmp_path)
    assert "ok_so_far" not in table_names(conn)
    assert "900" not in migrations.applied_versions(conn)


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO steps (company_id, name) VALUES (999, 'ES')")
