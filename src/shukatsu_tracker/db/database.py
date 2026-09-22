"""接続の薄い包み。

上の層はドライバを直接触らず、この型だけを持ち回る。役割は3つ。

1. SQL は `?` で書き、方言ごとのプレースホルダへ変換する
2. ドライバ固有の例外を、この層の共通例外へ翻訳する
3. 書き込みを直列化する。1つの接続を複数のスレッドが共有しても、
   トランザクションが互いに割り込まないようにする
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from .dialects import Dialect


class Database:
    """1つの接続と、その方言・排他制御をまとめたもの。"""

    def __init__(self, raw: Any, dialect: Dialect) -> None:
        self._raw = raw
        self.dialect = dialect
        # 同じスレッドの入れ子を許しつつ、別スレッドの割り込みを防ぐ。
        # 接続を共有したままトランザクションを開くと、他スレッドの
        # ROLLBACK が自分の書き込みを巻き戻すため。
        self.lock = threading.RLock()
        self._depth = 0
        self._trace: list[str] | None = None

    # --- 問い合わせ ---------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        rendered = self.dialect.render(sql)
        if self._trace is not None:
            self._trace.append(sql)
        try:
            with self.lock:
                return self._raw.execute(rendered, tuple(params or ()))
        except Exception as error:
            translated = self.dialect.translate_error(error)
            if translated is not None:
                raise translated from error
            raise

    def fetchall(self, sql: str, params: Sequence[Any] | None = None) -> list[Any]:
        return list(self.execute(sql, params).fetchall())

    def fetchone(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        return self.execute(sql, params).fetchone()

    def insert(self, table: str, fields: Mapping[str, Any]) -> int:
        """1行挿入し、採番された id を返す。"""
        columns = ", ".join(fields)
        placeholders = ", ".join("?" * len(fields))
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        values = list(fields.values())
        with self.lock:
            if self.dialect.supports_returning:
                row = self.execute(f"{sql} RETURNING id", values).fetchone()
                return int(row["id"] if not isinstance(row, tuple) else row[0])
            cursor = self.execute(sql, values)
            if cursor.lastrowid is None:
                raise RuntimeError(f"{table} への INSERT で id を取得できませんでした")
            return int(cursor.lastrowid)

    # --- トランザクション ---------------------------------------------

    @property
    def depth(self) -> int:
        """開いているトランザクションの入れ子の深さ。"""
        return self._depth

    def begin(self) -> None:
        self._raw.execute(self.dialect.begin_sql)
        self._depth = 1

    def enter(self) -> None:
        self._depth += 1

    def leave(self) -> None:
        self._depth = max(0, self._depth - 1)

    def commit(self) -> None:
        # 失敗しても深さは戻す。戻さないと、次の境界が「入れ子」と誤認されて
        # BEGIN も ROLLBACK も発行されず、以後の書き込みが宙に浮いたままになる。
        try:
            self._raw.execute("COMMIT")
        finally:
            self._depth = 0

    def rollback(self) -> None:
        try:
            self._raw.execute("ROLLBACK")
        finally:
            self._depth = 0

    # --- その他 -------------------------------------------------------

    @contextmanager
    def record(self) -> Iterator[list[str]]:
        """発行した SQL を記録する。問い合わせ回数の検証に使う。"""
        collected: list[str] = []
        previous, self._trace = self._trace, collected
        try:
            yield collected
        finally:
            self._trace = previous

    def close(self) -> None:
        self._raw.close()
