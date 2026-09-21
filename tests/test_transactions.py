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
from shukatsu_tracker.db import DuplicateKeyError, transaction
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
        """
        first = db.connect(tmp_path / "shared.db")
        second = db.connect(tmp_path / "shared.db")
        second.execute("PRAGMA busy_timeout = 100")
        try:
            with transaction(first):
                first.fetchall("SELECT id FROM companies")
                with pytest.raises(sqlite3.OperationalError, match="locked"), transaction(second):
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
