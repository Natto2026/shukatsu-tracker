"""DB 接続。接続を作るのはこのモジュールだけ。

isolation_level を None にして sqlite3 の暗黙トランザクションを切り、
開始と終了は transactions.transaction() が明示的に発行する。既定のままだと
DDL が暗黙コミットされ、複数文をまとめて巻き戻せないため。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import migrations
from .transactions import transaction

__all__ = ["connect", "transaction"]


def connect(db_path: str | Path) -> sqlite3.Connection:
    """DB に接続し、未適用のマイグレーションを流して返す。

    Streamlit は再描画のたびに別スレッドでスクリプトを実行するため
    check_same_thread=False で接続する（書き込みは transaction() で直列に閉じる）。
    """
    path = Path(db_path)
    if str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrations.apply_pending(conn)
    return conn
