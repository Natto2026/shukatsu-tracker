"""接続に失敗したときの検証。

理由が何であっても、上の層に届くのは共通の例外 1 つであること。ドライバの
例外や RuntimeError がそのまま上がると、画面に生のトレースバックが出る。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from shukatsu_tracker import db
from shukatsu_tracker.db import ConnectionFailedError, ConnectionLostError, DatabaseError

APP_PATH = str(Path(__file__).parent.parent / "app.py")


def test_connection_failure_is_a_database_error():
    """画面は DatabaseError だけを捕まえる。その範囲に入っていること。"""
    assert issubclass(ConnectionFailedError, DatabaseError)


def test_unsupported_scheme_is_translated():
    with pytest.raises(ConnectionFailedError, match="対応していない接続先"):
        db.connect("mysql://someone:s3cret@127.0.0.1/shukatsu")


def test_missing_postgres_driver_is_translated(monkeypatch):
    """psycopg が入っていない環境。入れ方が文面に含まれること。"""
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(ConnectionFailedError, match=r"pip install -e \"\.\[postgres\]\""):
        db.connect("postgresql://someone:s3cret@127.0.0.1:5432/shukatsu")


def test_unreachable_postgres_is_translated_without_leaking_the_target():
    pytest.importorskip("psycopg")
    # ポート 1 には誰も待ち受けていない。すぐに拒否される
    with pytest.raises(ConnectionFailedError) as caught:
        db.connect("postgresql://someone:s3cret@127.0.0.1:1/shukatsu?connect_timeout=2")
    message = str(caught.value)
    assert "PostgreSQL に接続できませんでした" in message
    assert "s3cret" not in message
    assert "someone" not in message
    assert "127.0.0.1" not in message


def test_ping_tells_a_live_connection_from_a_closed_one(conn):
    assert conn.ping() is True
    conn.close()
    assert conn.ping() is False


def test_operating_on_a_closed_connection_is_reported_as_lost(conn):
    """切れた接続への操作は、ドライバの例外ではなく共通の型で上がること。

    画面はこの型を見て接続を手放し、次の再描画で張り直す。
    """
    conn.close()
    with pytest.raises(ConnectionLostError):
        conn.fetchall("SELECT id FROM companies")


def test_sqlite_file_that_cannot_be_opened_is_translated(tmp_path):
    """保存先にディレクトリを指した場合。sqlite3 の例外を外に出さない。"""
    with pytest.raises(ConnectionFailedError, match="SQLite のファイルを開けませんでした"):
        db.connect(tmp_path)


@pytest.mark.ui
@pytest.mark.parametrize(
    "target",
    [
        "mysql://someone:s3cret@127.0.0.1/shukatsu",
        "postgresql://someone:s3cret@127.0.0.1:1/shukatsu?connect_timeout=2",
    ],
)
def test_app_shows_a_message_not_a_traceback(monkeypatch, target):
    monkeypatch.setenv("SHUKATSU_DB", target)
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert not at.exception, at.exception
    errors = " ".join(e.value for e in at.error)
    assert "データベースに接続できませんでした" in errors
    assert "s3cret" not in errors


class TestPostgresErrorClassification:
    """PostgreSQL の OperationalError のうち、切断だけを「接続が切れた」に分類すること。

    実行時間の打ち切りや容量不足まで切断にすると、生きている接続を捨てて張り直し、
    利用者には事実と違う理由を伝えてしまう。
    """

    @pytest.fixture
    def translate(self):
        psycopg = pytest.importorskip("psycopg")
        from shukatsu_tracker.db.dialects import PostgresDialect

        return psycopg, PostgresDialect().translate_error

    @pytest.mark.parametrize("name", ["QueryCanceled", "DiskFull", "OutOfMemory"])
    def test_a_live_connection_error_is_not_reported_as_lost(self, translate, name):
        psycopg, translate_error = translate
        translated = translate_error(getattr(psycopg.errors, name)("詳細"))
        assert isinstance(translated, DatabaseError)
        assert not isinstance(translated, ConnectionLostError)

    @pytest.mark.parametrize("name", ["ConnectionFailure", "AdminShutdown"])
    def test_a_disconnect_is_reported_as_lost(self, translate, name):
        psycopg, translate_error = translate
        assert isinstance(translate_error(getattr(psycopg.errors, name)("詳細")), ConnectionLostError)

    def test_an_error_without_a_state_code_is_a_lost_connection(self, translate):
        """サーバーの応答が届かなかったときは状態コードがない。"""
        psycopg, translate_error = translate
        assert isinstance(translate_error(psycopg.OperationalError("server closed")), ConnectionLostError)
