"""DBMS ごとの差分。

SQL の本体は1つに保ち、方言による違い（プレースホルダ・主キーの書き方・
日付関数・例外の型）だけをここに閉じ込める。新しい DBMS を足す場合も
このファイルに1クラス増やすだけで済むようにしている。
"""

from __future__ import annotations

import re
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import (
    BusyError,
    ConnectionFailedError,
    ConnectionLostError,
    DatabaseError,
    DuplicateKeyError,
    ForeignKeyError,
)

_BUSY_MESSAGE = (
    "別のセッションが書き込み中のため、待ち時間内に保存できませんでした。少し待ってからやり直してください"
)
_LOST_MESSAGE = "データベースとの接続が切れました。画面を再読み込みすると接続し直します"


def _generic(error: Exception) -> DatabaseError:
    """それ以外のドライバの例外。先頭行だけを文面に残す。"""
    first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
    return DatabaseError(f"データベースの操作に失敗しました: {first_line}")


# SQL 中で方言差を吸収するための差し込み記号
PK = "{{PK}}"
TODAY = "{{TODAY}}"
NOW = "{{NOW}}"
# SELECT の末尾に置くと、その行を境界の終わりまでロックする。SQLite は境界の開始で
# ファイル全体の書き込みロックを取るので何も置かない
FOR_UPDATE = "{{FOR_UPDATE}}"


class Dialect(ABC):
    """1つの DBMS に対する差分の定義。"""

    name: str
    placeholder: str
    supports_returning: bool
    begin_sql: str = "BEGIN"
    # マイグレーションの版を1つずつ適用するために、境界の先頭で流す文。
    # None なら begin_sql の時点で書き込みが直列化されるので要らない
    migration_lock_sql: str | None = None

    @abstractmethod
    def connect(self, target: str) -> Any:
        """ドライバの接続を開いて返す。開けなければ ConnectionFailedError。"""

    @abstractmethod
    def substitutions(self) -> dict[str, str]:
        """マイグレーション SQL の差し込み記号に対する置換。"""

    @abstractmethod
    def translate_error(self, error: Exception) -> Exception | None:
        """ドライバ固有の例外を共通の型に翻訳する。対象外なら None。"""

    def render(self, sql: str) -> str:
        """差し込み記号を置換し、プレースホルダを方言に合わせる。"""
        for token, replacement in self.substitutions().items():
            sql = sql.replace(token, replacement)
        if self.placeholder != "?":
            sql = _swap_placeholders(sql, self.placeholder)
        return sql


class SqliteDialect(Dialect):
    """既定。1ファイルで完結し、サーバーを持たない。"""

    name = "sqlite"
    placeholder = "?"
    supports_returning = False
    # 境界は書き込みにしか使わないので、開始時に書き込みロックを取る。既定の
    # BEGIN（DEFERRED）だと、境界の中で読んでから書く間に別の接続が書き込めてしまい、
    # そのあとの自分の書き込みは待たされずに「database is locked」で失敗する。
    begin_sql = "BEGIN IMMEDIATE"

    def connect(self, target: str) -> sqlite3.Connection:
        path = Path(target)
        try:
            if str(path.parent) not in ("", "."):
                path.parent.mkdir(parents=True, exist_ok=True)
            # Streamlit はセッションごとに別スレッドで動くため、接続は
            # セッション単位で作る前提で check_same_thread を外す。
            # isolation_level=None で暗黙トランザクションを切り、開始と終了は
            # transactions.transaction() だけが発行する。
            raw = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            raw.row_factory = sqlite3.Row
            raw.execute("PRAGMA foreign_keys = ON")
            raw.execute("PRAGMA journal_mode = WAL")
            raw.execute("PRAGMA busy_timeout = 5000")
        except (sqlite3.Error, OSError) as error:
            raise ConnectionFailedError(
                "SQLite のファイルを開けませんでした。保存先のパスと書き込み権限を確認してください"
            ) from error
        return raw

    def substitutions(self) -> dict[str, str]:
        return {
            PK: "INTEGER PRIMARY KEY AUTOINCREMENT",
            TODAY: "date('now', 'localtime')",
            NOW: "datetime('now', 'localtime')",
            FOR_UPDATE: "",
        }

    def translate_error(self, error: Exception) -> Exception | None:
        if not isinstance(error, sqlite3.Error):
            return None
        message = str(error)
        upper = message.upper()
        if isinstance(error, sqlite3.IntegrityError):
            if "UNIQUE" in upper:
                return DuplicateKeyError(message)
            if "FOREIGN KEY" in upper:
                return ForeignKeyError(message)
        elif isinstance(error, sqlite3.OperationalError) and ("LOCKED" in upper or "BUSY" in upper):
            return BusyError(_BUSY_MESSAGE)
        elif isinstance(error, sqlite3.ProgrammingError) and "CLOSED" in upper:
            return ConnectionLostError(_LOST_MESSAGE)
        return _generic(error)


class PostgresDialect(Dialect):
    """サーバー型。複数端末から使う場合や、本番に近い構成で動かす場合に選ぶ。"""

    name = "postgresql"
    placeholder = "%s"
    supports_returning = True
    # BEGIN はロックを取らないため、同時に初回接続した2つのセッションが同じ版を
    # 流しうる。境界が終わるまで保持される勧告ロックで、版の適用を1本ずつにする
    # （数値は、このアプリのマイグレーション用と分かれば何でもよい）
    migration_lock_sql = "SELECT pg_advisory_xact_lock(724001)"

    def connect(self, target: str) -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise ConnectionFailedError(
                'PostgreSQL を使うには追加の依存が必要です: pip install -e ".[postgres]"'
            ) from error
        # autocommit=True にして、開始と終了を transaction() だけが発行する形に
        # そろえる（SQLite 側と同じ扱いにするため）。
        try:
            raw = psycopg.connect(target, autocommit=True, row_factory=dict_row)
            raw.execute("SET client_encoding TO 'UTF8'")
        except psycopg.Error as error:
            raise ConnectionFailedError(
                "PostgreSQL に接続できませんでした。サーバーが起動しているか、"
                "接続文字列（ホスト・ポート・利用者名・パスワード・DB 名）を確認してください"
            ) from error
        return raw

    def substitutions(self) -> dict[str, str]:
        return {
            PK: "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
            TODAY: "to_char(CURRENT_DATE, 'YYYY-MM-DD')",
            NOW: "to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')",
            FOR_UPDATE: "FOR UPDATE",
        }

    def translate_error(self, error: Exception) -> Exception | None:
        try:
            import psycopg
        except ImportError:  # pragma: no cover - 依存未導入なら翻訳対象がない
            return None
        if not isinstance(error, psycopg.Error):
            return None
        if isinstance(error, psycopg.errors.UniqueViolation):
            return DuplicateKeyError(str(error))
        if isinstance(error, psycopg.errors.ForeignKeyViolation):
            return ForeignKeyError(str(error))
        # ロック待ちの超過・競合は OperationalError の下位型なので、先に見る
        contention = (
            psycopg.errors.LockNotAvailable,
            psycopg.errors.DeadlockDetected,
            psycopg.errors.SerializationFailure,
        )
        if isinstance(error, contention):
            return BusyError(_BUSY_MESSAGE)
        # 開いていた接続への操作で起きる OperationalError は切断（接続時の失敗は
        # connect() が先に ConnectionFailedError にしている）
        if isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError)):
            return ConnectionLostError(_LOST_MESSAGE)
        return _generic(error)


def _swap_placeholders(sql: str, placeholder: str) -> str:
    """文字列リテラルの外にある ? だけを置き換える。"""
    out: list[str] = []
    quote: str | None = None
    for char in sql:
        if quote is None and char in ("'", '"'):
            quote = char
        elif quote is not None and char == quote:
            quote = None
        if char == "?" and quote is None:
            out.append(placeholder)
        else:
            out.append(char)
    return "".join(out)


def resolve(target: str | Path) -> tuple[Dialect, str]:
    """接続先の指定から、方言と接続文字列を決める。

    スキームのない文字列は SQLite のファイルパスとして扱う
    （従来の SHUKATSU_DB の指定をそのまま使えるようにするため）。
    """
    text = str(target)
    parsed = urlparse(text)
    if parsed.scheme in ("postgresql", "postgres"):
        return PostgresDialect(), text
    if parsed.scheme == "sqlite":
        # sqlite:///relative/path.db と sqlite:////abs/path.db の両方を許す
        return SqliteDialect(), parsed.path.lstrip("/") or parsed.netloc
    if parsed.scheme and len(parsed.scheme) > 1:
        raise ValueError(f"対応していない接続先です: {parsed.scheme}")
    return SqliteDialect(), text


def describe(target: str | Path, base: Path | None = None) -> str:
    """接続先を、画面に出してよい形に要約する。

    接続文字列にはパスワードが、ファイルパスには利用者名やフォルダ構成が
    含まれる。画面共有やスクリーンショットでそのまま漏れるため、
    種別と最小限の識別子だけを返す。
    """
    text = str(target)
    if text.startswith(("postgresql://", "postgres://")):
        return "PostgreSQL（サーバー型）"
    if base is not None:
        try:
            return f"SQLite（{Path(text).relative_to(base).as_posix()}）"
        except ValueError:
            pass
    # Path に任せると、動作中の OS の区切り文字しか見ない。Windows で作った
    # パスを Linux で表示すると分解されず、そのまま出てしまう。
    return f"SQLite（{_last_segment(text)}）"


_SEPARATORS = re.compile(r"[/\\]")


def _last_segment(text: str) -> str:
    """OS に関係なく、パスの最後の要素だけを取り出す。"""
    segments = [part for part in _SEPARATORS.split(text) if part]
    return segments[-1] if segments else text
