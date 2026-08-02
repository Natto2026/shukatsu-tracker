import sqlite3

import pytest

from shukatsu_tracker import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


def test_add_and_list_company(conn):
    cid = db.add_company(conn, "テスト株式会社", route="スカウト・逆求人", priority="A")
    companies = db.list_companies(conn)
    assert len(companies) == 1
    assert companies[0]["id"] == cid
    assert companies[0]["route"] == "スカウト・逆求人"


def test_companies_sorted_by_priority_meaning_not_alphabet(conn):
    db.add_company(conn, "B社", priority="B")
    db.add_company(conn, "S社", priority="S")
    db.add_company(conn, "C社", priority="C")
    db.add_company(conn, "A社", priority="A")
    names = [c["name"] for c in db.list_companies(conn)]
    assert names == ["S社", "A社", "B社", "C社"]


def test_duplicate_company_name_rejected(conn):
    db.add_company(conn, "テスト株式会社")
    with pytest.raises(sqlite3.IntegrityError):
        db.add_company(conn, "テスト株式会社")


def test_steps_are_joined_with_company_fields(conn):
    cid = db.add_company(conn, "テスト株式会社", route="ハッカソン・イベント")
    db.add_step(conn, cid, "ES", deadline="2026-08-10")
    steps = db.list_steps(conn)
    assert steps[0]["company_name"] == "テスト株式会社"
    assert steps[0]["route"] == "ハッカソン・イベント"


def test_deleting_company_cascades_steps(conn):
    cid = db.add_company(conn, "テスト株式会社")
    db.add_step(conn, cid, "ES")
    db.delete_company(conn, cid)
    assert db.list_steps(conn) == []


def test_es_answer_survives_company_deletion(conn):
    cid = db.add_company(conn, "テスト株式会社")
    db.add_es_answer(conn, "学生時代に力を入れたこと", company_id=cid, char_limit=400)
    db.delete_company(conn, cid)
    answers = db.list_es_answers(conn)
    assert len(answers) == 1
    assert answers[0]["company_id"] is None


def test_update_step_result(conn):
    cid = db.add_company(conn, "テスト株式会社")
    sid = db.add_step(conn, cid, "ES")
    db.update_step(conn, sid, result="通過")
    assert db.list_steps(conn, cid)[0]["result"] == "通過"
