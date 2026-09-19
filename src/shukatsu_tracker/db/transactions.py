"""トランザクション境界。

sqlite3 の既定（isolation_level=""）は DDL を暗黙コミットしてしまい、
「まとめて成功するか、まとめて失敗するか」を保証できない。そのため接続側で
自動トランザクションを切り、開始と終了をこのモジュールだけが発行する。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """まとめて成功するか、まとめて失敗するかのどちらかにする。

    すでにトランザクションの中なら何も発行せず、外側の境界に委ねる
    （入れ子で BEGIN すると sqlite がエラーにするため）。
    """
    if conn.in_transaction:
        yield conn
        return

    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
