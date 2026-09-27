"""スキーマのバージョン管理の検証。"""

from __future__ import annotations

import threading

import pytest
from conftest import POSTGRES_DSN, shared_target, table_names

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


def test_a_version_that_breaks_a_unique_constraint_is_not_skipped(conn, tmp_path):
    """版の中の文が一意制約に反したら、記録の重複と取り違えて黙って飛ばさないこと。

    飛ばすと、その版だけが抜けたまま次の版が適用され、起動のたびに失敗し続ける。
    """
    (tmp_path / "900_dup.sql").write_text(
        "CREATE TABLE dup_target (code TEXT UNIQUE);\n"
        "INSERT INTO dup_target (code) VALUES ('a');\n"
        "INSERT INTO dup_target (code) VALUES ('a');",
        encoding="utf-8",
    )
    (tmp_path / "901_next.sql").write_text("CREATE TABLE after_dup (id INTEGER);", encoding="utf-8")

    with pytest.raises(MigrationError, match="900_dup.sql"):
        migrations.apply_pending(conn, tmp_path)
    applied = migrations.applied_versions(conn)
    assert "900" not in applied
    assert "901" not in applied


@pytest.mark.skipif(not POSTGRES_DSN, reason="PostgreSQL のときだけ起きる競合")
def test_two_sessions_starting_together_apply_a_version_once(tmp_path):
    """2つのセッションが同時に初回接続しても、二度流せない版で片方が落ちないこと。

    PostgreSQL の BEGIN はロックを取らず、相手の未確定の記録も見えないため、
    両方が未適用と判断して ALTER TABLE を流し、後の方が列の重複で失敗していた。
    """
    with shared_target(tmp_path) as dsn:
        (tmp_path / "900_base.sql").write_text("CREATE TABLE race (id INTEGER);", encoding="utf-8")
        setup = db.connect(dsn)
        migrations.apply_pending(setup, tmp_path)
        setup.close()
        # 相手が判定を終えるまで確定を遅らせ、競合が起きる並びを作る
        (tmp_path / "901_add.sql").write_text(
            "SELECT pg_sleep(1);\nALTER TABLE race ADD COLUMN extra INTEGER;", encoding="utf-8"
        )
        sessions = [db.connect(dsn) for _ in range(2)]
        errors: list[Exception] = []
        applied: list[list[str]] = []
        start = threading.Barrier(2)

        def run(database):
            start.wait()
            try:
                applied.append([m.version for m in migrations.apply_pending(database, tmp_path)])
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=run, args=(s,)) for s in sessions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not [t for t in threads if t.is_alive()], "終わらないセッションがある"
        for session in sessions:
            session.close()

        assert errors == []
        assert sorted(applied) == [[], ["901"]]


@pytest.mark.skipif(not POSTGRES_DSN, reason="PostgreSQL のときだけ起きる競合")
def test_first_connections_arriving_together_both_succeed(tmp_path):
    """空の DB に2つのセッションが同時に初めて接続しても、どちらも失敗しないこと。

    管理表の作成（CREATE TABLE IF NOT EXISTS）も、同時に流すと片方が失敗しうるため、
    版の適用と同じロックの中で流す。競合は起きたり起きなかったりするので、数回試す。
    """
    for attempt in range(5):
        with shared_target(tmp_path / str(attempt)) as dsn:
            assert _connect_together(dsn) == []


def _connect_together(dsn: str) -> list[Exception]:
    """2つのセッションで同時に接続し、起きた例外を返す。"""
    start = threading.Barrier(2)
    errors: list[Exception] = []
    opened = []

    def open_one():
        start.wait()
        try:
            opened.append(db.connect(dsn))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=open_one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not [t for t in threads if t.is_alive()], "終わらないセッションがある"
    for database in opened:
        database.close()
    return errors


def test_reviews_have_usage_columns(conn):
    row = conn.fetchone("SELECT input_tokens, output_tokens FROM reviews WHERE 1 = 0")
    assert row is None


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(ForeignKeyError):
        conn.execute("INSERT INTO steps (company_id, name) VALUES (999, 'ES')")
