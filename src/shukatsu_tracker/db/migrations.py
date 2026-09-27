"""スキーマのバージョン管理。

スキーマをコード中の文字列ではなく連番の .sql ファイルで持ち、適用済みの
バージョンを schema_migrations テーブルに記録する。既存の DB を作り直さずに
列やテーブルを足せる（ファイルを1枚足せば次の接続時に適用される）。

方言差は差し込み記号（{{PK}} など）で吸収するため、SQL ファイルは1組で足りる。
1ファイル＝1トランザクション。途中で失敗したら、そのファイルの変更は残さない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .database import Database
from .dialects import NOW
from .errors import DuplicateKeyError, MigrationError
from .transactions import transaction

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    name: str
    path: Path

    def statements(self) -> list[str]:
        return split_statements(self.path.read_text(encoding="utf-8"))


def strip_comments(sql: str) -> str:
    """行コメントとブロックコメントを取り除く。文字列リテラルの中は残す。"""
    out: list[str] = []
    index = 0
    length = len(sql)
    quote: str | None = None
    while index < length:
        char = sql[index]
        if quote is not None:
            out.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            out.append(char)
            index += 1
            continue
        if sql.startswith("--", index):
            end = sql.find("\n", index)
            index = length if end == -1 else end
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end == -1:
                raise ValueError("ブロックコメントが閉じられていません")
            index = end + 2
            continue
        out.append(char)
        index += 1
    if quote is not None:
        raise ValueError("文字列リテラルが閉じられていません")
    return "".join(out)


def split_statements(sql: str) -> list[str]:
    """SQL を文ごとに分ける。

    ドライバの executescript は暗黙コミットを挟むため使えず、多くの
    ドライバは1回の execute で複数文を受け付けない。コメントを外してから
    文字列リテラルの外のセミコロンで区切る。
    """
    body = strip_comments(sql)
    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    for char in body:
        if quote is None and char in ("'", '"'):
            quote = char
        elif quote is not None and char == quote:
            quote = None
        if char == ";" and quote is None:
            text = "".join(buffer).strip()
            if text:
                statements.append(text)
            buffer = []
            continue
        buffer.append(char)
    trailing = "".join(buffer).strip()
    if trailing:
        raise ValueError(f"SQL がセミコロンで終わっていません: {trailing[:60]}")
    return statements


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


def applied_versions(db: Database) -> set[str]:
    with transaction(db):
        db.execute(_BOOTSTRAP)
    rows = db.fetchall("SELECT version FROM schema_migrations")
    return {_first(row) for row in rows}


def apply_pending(db: Database, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """未適用のマイグレーションを順に流し、適用したものを返す。"""
    done = applied_versions(db)
    applied: list[Migration] = []
    for migration in discover(directory):
        if migration.version in done:
            continue
        try:
            now_expression = db.dialect.substitutions()[NOW]
            record_sql = (
                f"INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, {now_expression})"
            )
            with transaction(db):
                # 一覧を読んでから境界を開くまでの間に、別のプロセスが同じ版を
                # 適用し終えていることがある。ALTER TABLE のように二度流せない文を
                # 含む版もあるため、ロックを取ってから記録を見直し、済んでいれば流さない
                # （SQLite は BEGIN IMMEDIATE、PostgreSQL は勧告ロックで直列化する）。
                if db.dialect.migration_lock_sql is not None:
                    db.execute(db.dialect.migration_lock_sql)
                if _is_recorded(db, migration.version):
                    continue
                for statement in migration.statements():
                    db.execute(statement)
                db.execute(record_sql, (migration.version, migration.name))
        except DuplicateKeyError as error:
            # 記録の重複（別のプロセスが先に同じ版を記録した）なら飛ばしてよい。
            # 版の中の文が一意制約に反した場合は、記録がないので失敗として扱う。
            # 黙って飛ばすと、その版だけが抜けたまま次の版が適用されてしまう。
            if _is_recorded(db, migration.version):
                continue
            raise MigrationError(f"{migration.path.name} の適用に失敗しました: {error}") from error
        except Exception as error:
            raise MigrationError(f"{migration.path.name} の適用に失敗しました: {error}") from error
        applied.append(migration)
    return applied


def _is_recorded(db: Database, version: str) -> bool:
    row = db.fetchone("SELECT version FROM schema_migrations WHERE version = ?", (version,))
    return row is not None


def _first(row: object) -> str:
    """ドライバによって行が dict にも tuple にもなるため、先頭列を取り出す。"""
    if isinstance(row, dict):
        return str(next(iter(row.values())))
    return str(row[0])  # type: ignore[index]
