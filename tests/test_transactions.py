"""トランザクション境界の検証。

1つの接続を複数のスレッドが使う状況を再現する。ここが壊れると、
成功したはずの書き込みが他スレッドの失敗に巻き込まれて消えるため、
この設計でいちばん守りたい性質になる。
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from shukatsu_tracker import db
from shukatsu_tracker.db import (
    BusyError,
    Database,
    DatabaseError,
    DuplicateKeyError,
    migrations,
    transaction,
)
from shukatsu_tracker.db.dialects import SqliteDialect
from shukatsu_tracker.models import Company
from shukatsu_tracker.services import SelectionService


def company_names(conn) -> list[str]:
    return [row["name"] for row in conn.fetchall("SELECT name FROM companies ORDER BY id")]


class TestSingleThread:
    def test_commits_on_success(self, conn):
        with transaction(conn):
            conn.execute("INSERT INTO companies (name) VALUES (?)", ("成功社",))
        assert company_names(conn) == ["成功社"]
        assert conn.depth == 0

    def test_rolls_back_on_failure(self, conn):
        with pytest.raises(RuntimeError), transaction(conn):
            conn.execute("INSERT INTO companies (name) VALUES (?)", ("失敗社",))
            raise RuntimeError("途中で失敗")
        assert company_names(conn) == []
        assert conn.depth == 0

    def test_nested_blocks_commit_once(self, conn):
        with transaction(conn):
            conn.execute("INSERT INTO companies (name) VALUES (?)", ("外側",))
            with transaction(conn):
                conn.execute("INSERT INTO companies (name) VALUES (?)", ("内側",))
            assert conn.depth == 1
        assert sorted(company_names(conn)) == ["内側", "外側"]
        assert conn.depth == 0

    def test_failure_inside_a_nested_block_rolls_back_everything(self, conn):
        with pytest.raises(RuntimeError), transaction(conn):
            conn.execute("INSERT INTO companies (name) VALUES (?)", ("外側",))
            with transaction(conn):
                conn.execute("INSERT INTO companies (name) VALUES (?)", ("内側",))
                raise RuntimeError("内側で失敗")
        assert company_names(conn) == []


class TestSeparateConnections:
    def test_sqlite_boundary_takes_the_write_lock_at_the_start(self, tmp_path):
        """境界を開いた時点で、別の接続の書き込みを待たせること（SQLite）。

        開始時にロックを取らないと、境界の中で読んでから書くまでの間に別の接続が
        書き込めてしまい、読んだ値（並び順の算出など）が古くなる。
        待ちきれなかった側には、ドライバの例外ではなく共通の BusyError が届くこと。
        """
        first = db.connect(tmp_path / "shared.db")
        second = db.connect(tmp_path / "shared.db")
        second.execute("PRAGMA busy_timeout = 100")
        try:
            with transaction(first):
                first.fetchall("SELECT id FROM companies")
                with pytest.raises(BusyError), transaction(second):
                    second.execute("INSERT INTO companies (name) VALUES (?)", ("割り込み社",))
            with transaction(second):
                second.execute("INSERT INTO companies (name) VALUES (?)", ("後続社",))
            assert company_names(first) == ["後続社"]
            assert second.depth == 0
        finally:
            first.close()
            second.close()


class TestSharedConnection:
    """接続を共有したままでも、境界が互いに干渉しないこと。"""

    def test_a_failed_transaction_does_not_discard_another_threads_write(self, conn):
        """片方の失敗で、もう片方の確定済みの書き込みが消えないこと。"""
        b_committed = threading.Event()
        a_may_fail = threading.Event()
        errors: list[BaseException] = []

        def writer_a() -> None:
            try:
                with transaction(conn):
                    conn.execute("INSERT INTO companies (name) VALUES (?)", ("A社",))
                    b_committed.set()
                    a_may_fail.wait(timeout=5)
                    raise RuntimeError("A は失敗する")
            except RuntimeError:
                pass
            except BaseException as error:  # pragma: no cover - 失敗時の診断用
                errors.append(error)

        def writer_b() -> None:
            try:
                b_committed.wait(timeout=5)
                with transaction(conn):
                    conn.execute("INSERT INTO companies (name) VALUES (?)", ("B社",))
            except BaseException as error:  # pragma: no cover - 失敗時の診断用
                errors.append(error)
            finally:
                a_may_fail.set()

        threads = [threading.Thread(target=writer_a), threading.Thread(target=writer_b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        # join は時間切れでも黙って返る。終わっていないスレッドがあれば、止まったとみなして落とす
        assert not any(thread.is_alive() for thread in threads)

        assert errors == []
        # A は失敗したので残らない。B は確定しているので必ず残る。
        assert company_names(conn) == ["B社"]

    def test_concurrent_writers_all_persist(self, conn):
        """同時に書いても、取りこぼしも例外も出ないこと。"""
        errors: list[BaseException] = []
        per_thread = 15
        thread_count = 6

        def writer(index: int) -> None:
            try:
                for number in range(per_thread):
                    with transaction(conn):
                        conn.execute(
                            "INSERT INTO companies (name) VALUES (?)",
                            (f"社{index}-{number}",),
                        )
            except BaseException as error:  # pragma: no cover - 失敗時の診断用
                errors.append(error)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        # join は時間切れでも黙って返る。終わっていないスレッドがあれば、止まったとみなして落とす
        assert not any(thread.is_alive() for thread in threads)

        assert errors == []
        assert len(company_names(conn)) == per_thread * thread_count
        assert conn.depth == 0

    def test_service_level_writes_are_serialised(self, conn):
        """サービス経由の複数文の書き込みが、互いに割り込まれないこと。"""
        errors: list[BaseException] = []

        def writer(index: int) -> None:
            service = SelectionService(conn)
            try:
                service.add_company(Company(name=f"会社{index}"))
            except BaseException as error:  # pragma: no cover - 失敗時の診断用
                errors.append(error)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        # join は時間切れでも黙って返る。終わっていないスレッドがあれば、止まったとみなして落とす
        assert not any(thread.is_alive() for thread in threads)

        assert errors == []
        service = SelectionService(conn)
        # 8社それぞれに既定ステップが漏れなく入っていること
        grouped = service.steps_by_company()
        assert len(grouped) == 8
        assert {len(steps) for steps in grouped.values()} == {6}

    def test_a_duplicate_failure_leaves_earlier_rows_intact(self, conn):
        service = SelectionService(conn)
        service.add_company(Company(name="既存社"), with_default_steps=False)
        with pytest.raises(DuplicateKeyError):
            service.add_company(Company(name="既存社"))
        assert company_names(conn) == ["既存社"]
        assert conn.depth == 0


class _CommitFails:
    """COMMIT だけを失敗させるドライバの包み。ディスクの I/O エラーや切断を模す。

    `already_rolled_back` を立てると、失敗の前に自分で ROLLBACK を発行する。
    I/O エラー時の SQLite のように、ドライバ側で巻き戻し済みの状態を再現するため。
    """

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw
        self.fail_next = False
        self.already_rolled_back = False

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        if sql == "COMMIT" and self.fail_next:
            self.fail_next = False
            if self.already_rolled_back:
                self._raw.execute("ROLLBACK")
            raise sqlite3.OperationalError("disk I/O error")
        return self._raw.execute(sql, params)

    def close(self) -> None:
        self._raw.close()


class TestCommitFailure:
    """COMMIT が失敗しても、境界が壊れたまま残らないこと。

    修正前は COMMIT の失敗で深さが 1 のまま固着し、次の境界が入れ子と誤認されて
    BEGIN も ROLLBACK も発行されなかった。開いたままのトランザクションが
    書き込みロックを握り続け、別のセッションの起動も止まっていた。
    """

    @pytest.fixture
    def flaky(self, tmp_path):
        dialect = SqliteDialect()
        raw = dialect.connect(str(tmp_path / "flaky.db"))
        migrations.apply_pending(Database(raw, dialect))
        wrapper = _CommitFails(raw)
        database = Database(wrapper, dialect)
        yield database, wrapper
        database.close()

    def test_a_failed_commit_resets_the_depth_and_releases_the_lock(self, tmp_path, flaky):
        database, wrapper = flaky
        wrapper.fail_next = True
        with pytest.raises(DatabaseError, match="disk I/O error"), transaction(database):
            database.execute("INSERT INTO companies (name) VALUES (?)", ("消える社",))
        assert database.depth == 0
        assert company_names(database) == []

        # 書き込みロックが解放され、別の接続が書けること
        other = db.connect(tmp_path / "flaky.db")
        other.execute("PRAGMA busy_timeout = 100")
        try:
            with transaction(other):
                other.execute("INSERT INTO companies (name) VALUES (?)", ("後続社",))
        finally:
            other.close()
        assert company_names(database) == ["後続社"]

    def test_the_next_boundary_still_rolls_back_after_a_failed_commit(self, flaky):
        database, wrapper = flaky
        wrapper.fail_next = True
        with pytest.raises(DatabaseError, match="disk I/O error"), transaction(database):
            database.execute("INSERT INTO companies (name) VALUES (?)", ("消える社",))

        with pytest.raises(RuntimeError), transaction(database):
            database.execute("INSERT INTO companies (name) VALUES (?)", ("失敗社",))
            raise RuntimeError("途中で失敗")
        assert company_names(database) == []

        with transaction(database):
            database.execute("INSERT INTO companies (name) VALUES (?)", ("成功社",))
        assert company_names(database) == ["成功社"]
        assert database.depth == 0

    def test_a_commit_the_driver_already_rolled_back_does_not_mask_the_error(self, flaky):
        database, wrapper = flaky
        wrapper.fail_next = True
        wrapper.already_rolled_back = True
        with pytest.raises(DatabaseError, match="disk I/O error"), transaction(database):
            database.execute("INSERT INTO companies (name) VALUES (?)", ("消える社",))
        assert database.depth == 0

        with transaction(database):
            database.execute("INSERT INTO companies (name) VALUES (?)", ("成功社",))
        assert company_names(database) == ["成功社"]
