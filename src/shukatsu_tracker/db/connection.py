"""接続の入口。接続を開くのはこのモジュールだけ。

接続先は文字列で受け取る。スキームのないものは SQLite のファイルパス、
`postgresql://...` は PostgreSQL として扱う。どちらの場合も返すのは
Database なので、上の層は接続先の種類を意識しない。
"""

from __future__ import annotations

from pathlib import Path

from . import migrations
from .database import Database
from .dialects import resolve
from .errors import ConnectionFailedError
from .transactions import transaction

__all__ = ["connect", "transaction"]


def connect(target: str | Path) -> Database:
    """接続し、未適用のマイグレーションを流して返す。

    接続できない理由（未対応の接続先・依存の不足・届かないサーバー）は、どれも
    ConnectionFailedError にして上げる。画面が DatabaseError だけを見れば済むように。

    接続は呼び出し側が所有する。画面はブラウザのセッションごとに1つ作る
    （1つの接続を全セッションで共有すると、書き込みが互いに干渉するため）。
    """
    try:
        dialect, dsn = resolve(target)
    except ValueError as error:
        raise ConnectionFailedError(str(error)) from error
    db = Database(dialect.connect(dsn), dialect)
    migrations.apply_pending(db)
    return db
