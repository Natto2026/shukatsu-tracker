"""スキーマのバージョン管理。

スキーマをコード中の文字列ではなく連番の .sql ファイルで持ち、適用済みの
バージョンを schema_migrations テーブルに記録する。こうすると既存の DB を
作り直さずに列やテーブルを足せる（ファイルを1枚足せば次の起動時に適用される）。

1ファイル＝1トランザクション。途中で失敗したら、そのファイルの変更は残さない。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .transactions import transaction

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    name: str
    path: Path

    def statements(self) -> list[str]:
        return split_statements(self.path.read_text(encoding="utf-8"))


def split_statements(sql: str) -> list[str]:
    """SQL を文ごとに分ける。

    executescript は暗黙コミットを挟むため使えない。文字列リテラル中の
    セミコロンで誤分割しないよう、区切りの判定は sqlite 自身に任せる。
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if buffer.strip() and sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if _has_code(buffer):
        raise ValueError(f"SQL が文の途中で終わっています: {buffer.strip()[:60]}")
    return statements


def _has_code(sql: str) -> bool:
    """空行とコメントだけでないか。"""
    return any(
        line.strip() and not line.strip().startswith("--") for line in sql.splitlines()
    )


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """NNN_name.sql をバージョン順に返す。命名が規約外のファイルは弾く。"""
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        matched = _FILENAME.match(path.name)
        if matched is None:
            raise ValueError(f"マイグレーション名が規約外です: {path.name}（NNN_name.sql）")
        found.append(Migration(version=matched.group(1), name=matched.group(2), path=path))
    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise ValueError(f"バージョン番号が重複しています: {sorted(versions)}")
    return found


def applied_versions(conn: sqlite3.Connection) -> set[str]:
    with transaction(conn):
        conn.execute(_BOOTSTRAP)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def apply_pending(
    conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR
) -> list[Migration]:
    """未適用のマイグレーションを順に流し、適用したものを返す。"""
    done = applied_versions(conn)
    applied: list[Migration] = []
    for migration in discover(directory):
        if migration.version in done:
            continue
        with transaction(conn):
            for statement in migration.statements():
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
        applied.append(migration)
    return applied
