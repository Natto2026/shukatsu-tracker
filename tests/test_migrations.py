"""スキーマのバージョン管理の検証。"""

from __future__ import annotations

import pytest
from conftest import table_names

from shukatsu_tracker import db
from shukatsu_tracker.db import ForeignKeyError, MigrationError, migrations, transaction


def test_connect_creates_all_tables(conn):
    assert {"companies", "steps", "es_answers", "schema_migrations"} <= table_names(conn)


def test_every_migration_is_recorded(conn):
    applied = migrations.applied_versions(conn)
    assert applied == {m.version for m in migrations.discover()}


def test_applying_twice_changes_nothing(conn):
    assert migrations.apply_pending(conn) == []


def test_reconnecting_to_an_existing_sqlite_file_is_safe(tmp_path):
    path = tmp_path / "existing.db"
    first = db.connect(path)
    with transaction(first):
        first.execute("INSERT INTO companies (name) VALUES ('既存社')")
    first.close()

    second = db.connect(path)
    assert migrations.apply_pending(second) == []
    names = [row["name"] for row in second.fetchall("SELECT name FROM companies")]
    assert names == ["既存社"]
    second.close()


def test_migration_filenames_follow_the_convention(tmp_path):
    (tmp_path / "bad-name.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError):
        migrations.discover(tmp_path)


def test_a_failing_migration_is_rolled_back(conn, tmp_path):
    (tmp_path / "900_broken.sql").write_text(
        "CREATE TABLE ok_so_far (id INTEGER);\nNOT VALID SQL;", encoding="utf-8"
    )
    with pytest.raises(MigrationError, match="900_broken.sql"):
        migrations.apply_pending(conn, tmp_path)
    assert "ok_so_far" not in table_names(conn)
    assert "900" not in migrations.applied_versions(conn)


def test_a_version_applied_meanwhile_is_skipped_inside_the_boundary(conn, tmp_path, monkeypatch):
    """適用済みの一覧を読んだあとに別のプロセスが同じ版を流していても、二度流さないこと。

    ALTER TABLE ... ADD COLUMN のように二度流せない文を含む版があるため、
    境界を開いてから記録を見直す。
    """
    (tmp_path / "900_once.sql").write_text("CREATE TABLE only_once (id INTEGER);", encoding="utf-8")
    assert [m.version for m in migrations.apply_pending(conn, tmp_path)] == ["900"]

    # 一覧を読んだ時点では未適用に見えた、という状況を作る
    monkeypatch.setattr(migrations, "applied_versions", lambda db: set())
    assert migrations.apply_pending(conn, tmp_path) == []
    assert "only_once" in table_names(conn)


def test_reviews_have_usage_columns(conn):
    row = conn.fetchone("SELECT input_tokens, output_tokens FROM reviews WHERE 1 = 0")
    assert row is None


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(ForeignKeyError):
        conn.execute("INSERT INTO steps (company_id, name) VALUES (999, 'ES')")
