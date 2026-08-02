"""SQLite への読み書き。

パスワード等の認証情報は保存しない方針(平文保存は漏洩リスクになるため、
マイページの URL とログイン用メールアドレスのみ管理する)。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    industry TEXT NOT NULL DEFAULT 'その他',
    priority TEXT NOT NULL DEFAULT 'B',
    route TEXT NOT NULL DEFAULT '一般公募',
    test_type TEXT NOT NULL DEFAULT '不明',
    mypage_url TEXT NOT NULL DEFAULT '',
    login_email TEXT NOT NULL DEFAULT '',
    memo TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (date('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    deadline TEXT,
    result TEXT NOT NULL DEFAULT '選考中',
    memo TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS es_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    category TEXT NOT NULL DEFAULT 'その他',
    question TEXT NOT NULL,
    char_limit INTEGER,
    answer TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (date('now', 'localtime'))
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """DB に接続し、初回ならスキーマを作成して返す。

    Streamlit は再描画のたびに別スレッドでスクリプトを実行するため、
    check_same_thread=False で接続する(書き込みは各操作で即 commit しており、
    同一セッション内の実行は直列なので問題にならない)。
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# --- companies -----------------------------------------------------------


def add_company(conn: sqlite3.Connection, name: str, **fields) -> int:
    cols = ["name", *fields.keys()]
    sql = f"INSERT INTO companies ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    cur = conn.execute(sql, [name, *fields.values()])
    conn.commit()
    return cur.lastrowid


def update_company(conn: sqlite3.Connection, company_id: int, **fields) -> None:
    if not fields:
        return
    assign = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE companies SET {assign} WHERE id = ?", [*fields.values(), company_id])
    conn.commit()


def delete_company(conn: sqlite3.Connection, company_id: int) -> None:
    conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
    conn.commit()


# 文字列ソートだと S が末尾に来るため、志望度の意味順(S→A→B→C)を明示する
_PRIORITY_ORDER = "CASE priority WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 ELSE 4 END"


def list_companies(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(f"SELECT * FROM companies ORDER BY {_PRIORITY_ORDER}, name").fetchall()
    return [dict(r) for r in rows]


# --- steps ---------------------------------------------------------------


def add_step(
    conn: sqlite3.Connection,
    company_id: int,
    name: str,
    deadline: str | None = None,
    sort_order: int = 0,
) -> int:
    cur = conn.execute(
        "INSERT INTO steps (company_id, name, deadline, sort_order) VALUES (?, ?, ?, ?)",
        (company_id, name, deadline, sort_order),
    )
    conn.commit()
    return cur.lastrowid


def update_step(conn: sqlite3.Connection, step_id: int, **fields) -> None:
    if not fields:
        return
    assign = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE steps SET {assign} WHERE id = ?", [*fields.values(), step_id])
    conn.commit()


def delete_step(conn: sqlite3.Connection, step_id: int) -> None:
    conn.execute("DELETE FROM steps WHERE id = ?", (step_id,))
    conn.commit()


def list_steps(conn: sqlite3.Connection, company_id: int | None = None) -> list[dict]:
    """ステップ一覧。company_id を省略すると企業名付きで全件返す。"""
    if company_id is None:
        rows = conn.execute(
            """SELECT s.*, c.name AS company_name, c.route, c.test_type
               FROM steps s JOIN companies c ON c.id = s.company_id
               ORDER BY c.name, s.sort_order, s.id"""
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM steps WHERE company_id = ? ORDER BY sort_order, id",
            (company_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# --- ES answers ----------------------------------------------------------


def add_es_answer(conn: sqlite3.Connection, question: str, **fields) -> int:
    cols = ["question", *fields.keys()]
    sql = f"INSERT INTO es_answers ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    cur = conn.execute(sql, [question, *fields.values()])
    conn.commit()
    return cur.lastrowid


def update_es_answer(conn: sqlite3.Connection, answer_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = fields.get("updated_at") or _today(conn)
    assign = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE es_answers SET {assign} WHERE id = ?", [*fields.values(), answer_id])
    conn.commit()


def delete_es_answer(conn: sqlite3.Connection, answer_id: int) -> None:
    conn.execute("DELETE FROM es_answers WHERE id = ?", (answer_id,))
    conn.commit()


def list_es_answers(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT e.*, c.name AS company_name
           FROM es_answers e LEFT JOIN companies c ON c.id = e.company_id
           ORDER BY e.updated_at DESC, e.id DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def _today(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT date('now', 'localtime')").fetchone()[0]
