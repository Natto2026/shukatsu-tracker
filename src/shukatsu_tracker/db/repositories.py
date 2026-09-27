"""テーブルごとの読み書き。SQL を書いてよいのはこの層だけ。

値は必ずプレースホルダで渡す。列名はプレースホルダに置けないため、更新できる列を
リポジトリごとの writable に列挙し、それ以外は ValueError にする
（呼び出し側の打ち間違いが SQL に混ざらないようにするため）。

接続そのものではなく Database を受け取る。方言差とドライバ固有の例外は
Database が吸収するので、この層の SQL は1組で両方の DBMS に通る。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..models import Company, EsAnswer, Review, Step, StepView
from .database import Database
from .dialects import FOR_UPDATE, TODAY

# 志望度は文字列順だと S が末尾に来るため、意味の順（S→A→B→C）を明示する
_PRIORITY_ORDER = "CASE priority WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 ELSE 4 END"


class _Table:
    """更新列の検証と、INSERT / UPDATE 文の組み立てを共通化する。"""

    table: str
    writable: frozenset[str]

    def __init__(self, db: Database) -> None:
        self._db = db

    def _checked(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(fields) - self.writable
        if unknown:
            raise ValueError(f"{self.table} に書き込めない列が指定されました: {sorted(unknown)}")
        return dict(fields)

    def _update(self, row_id: int, fields: Mapping[str, Any]) -> None:
        values = self._checked(fields)
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        self._db.execute(
            f"UPDATE {self.table} SET {assignments} WHERE id = ?",
            [*values.values(), row_id],
        )

    def _insert(self, fields: Mapping[str, Any]) -> int:
        return self._db.insert(self.table, self._checked(fields))

    def delete(self, row_id: int) -> None:
        self._db.execute(f"DELETE FROM {self.table} WHERE id = ?", (row_id,))


class CompanyRepository(_Table):
    table = "companies"
    writable = frozenset(
        {
            "name",
            "industry",
            "priority",
            "route",
            "test_type",
            "mypage_url",
            "login_email",
            "memo",
        }
    )

    @staticmethod
    def _to_model(row: Any) -> Company:
        return Company(
            id=row["id"],
            name=row["name"],
            industry=row["industry"],
            priority=row["priority"],
            route=row["route"],
            test_type=row["test_type"],
            mypage_url=row["mypage_url"],
            login_email=row["login_email"],
            memo=row["memo"],
            created_at=row["created_at"],
        )

    def add(self, company: Company) -> int:
        return self._insert(
            {
                "name": company.name,
                "industry": company.industry,
                "priority": company.priority,
                "route": company.route,
                "test_type": company.test_type,
                "mypage_url": company.mypage_url,
                "login_email": company.login_email,
                "memo": company.memo,
            }
        )

    def update(self, company_id: int, **fields: Any) -> None:
        self._update(company_id, fields)

    def get(self, company_id: int) -> Company | None:
        row = self._db.fetchone("SELECT * FROM companies WHERE id = ?", (company_id,))
        return None if row is None else self._to_model(row)

    def lock(self, company_id: int) -> bool:
        """企業の行を境界の終わりまでロックする。行がなければ False。

        同じ企業への同時の追加を直列化するために使う（PostgreSQL の既定の分離レベルでは
        境界を開いても他の接続を待たせないため）。境界の外で呼んでも意味がない。
        """
        row = self._db.fetchone(f"SELECT id FROM companies WHERE id = ? {FOR_UPDATE}", (company_id,))
        return row is not None

    def list_all(self) -> list[Company]:
        rows = self._db.fetchall(f"SELECT * FROM companies ORDER BY {_PRIORITY_ORDER}, name")
        return [self._to_model(row) for row in rows]


class StepRepository(_Table):
    table = "steps"
    writable = frozenset({"company_id", "name", "deadline", "result", "memo", "sort_order"})

    @staticmethod
    def _to_model(row: Any) -> Step:
        return Step(
            id=row["id"],
            company_id=row["company_id"],
            name=row["name"],
            deadline=row["deadline"],
            result=row["result"],
            memo=row["memo"],
            sort_order=row["sort_order"],
        )

    @staticmethod
    def _to_view(row: Any) -> StepView:
        return StepView(
            id=row["id"],
            company_id=row["company_id"],
            company_name=row["company_name"],
            name=row["name"],
            deadline=row["deadline"],
            result=row["result"],
            memo=row["memo"],
            sort_order=row["sort_order"],
            industry=row["industry"],
            route=row["route"],
            test_type=row["test_type"],
        )

    def add(self, step: Step) -> int:
        return self._insert(
            {
                "company_id": step.company_id,
                "name": step.name,
                "deadline": step.deadline,
                "result": step.result,
                "memo": step.memo,
                "sort_order": step.sort_order,
            }
        )

    def add_many(self, steps: Sequence[Step]) -> list[int]:
        return [self.add(step) for step in steps]

    def update(self, step_id: int, **fields: Any) -> None:
        self._update(step_id, fields)

    def list_for_company(self, company_id: int) -> list[Step]:
        rows = self._db.fetchall(
            "SELECT * FROM steps WHERE company_id = ? ORDER BY sort_order, id",
            (company_id,),
        )
        return [self._to_model(row) for row in rows]

    def next_sort_order(self, company_id: int) -> int:
        """末尾に足すときの並び順。件数ではなく最大値の次にする。

        件数だと、途中のステップを消したあとの追加が既存の並び順と衝突する
        （0..5 から 2 を消して足すと 5 が2つになる）。
        """
        row = self._db.fetchone(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM steps WHERE company_id = ?",
            (company_id,),
        )
        return int(row["next_order"])

    def list_views(self) -> list[StepView]:
        """全ステップに企業情報を結合して返す。集計の唯一の入力。"""
        rows = self._db.fetchall(
            "SELECT s.*, c.name AS company_name, c.industry, c.route, c.test_type "
            "FROM steps s JOIN companies c ON c.id = s.company_id "
            "ORDER BY c.name, s.sort_order, s.id"
        )
        return [self._to_view(row) for row in rows]


class EsAnswerRepository(_Table):
    table = "es_answers"
    writable = frozenset({"company_id", "category", "question", "char_limit", "answer", "updated_at"})

    _SELECT_WITH_COMPANY = (
        "SELECT e.*, c.name AS company_name FROM es_answers e LEFT JOIN companies c ON c.id = e.company_id"
    )

    @staticmethod
    def _to_model(row: Any) -> EsAnswer:
        return EsAnswer(
            id=row["id"],
            company_id=row["company_id"],
            company_name=row["company_name"],
            category=row["category"],
            question=row["question"],
            char_limit=row["char_limit"],
            answer=row["answer"],
            updated_at=row["updated_at"],
        )

    def add(self, answer: EsAnswer) -> int:
        return self._insert(
            {
                "company_id": answer.company_id,
                "category": answer.category,
                "question": answer.question,
                "char_limit": answer.char_limit,
                "answer": answer.answer,
            }
        )

    def update(self, answer_id: int, **fields: Any) -> None:
        values = dict(fields)
        values.setdefault("updated_at", self._today())
        self._update(answer_id, values)

    def get(self, answer_id: int) -> EsAnswer | None:
        row = self._db.fetchone(f"{self._SELECT_WITH_COMPANY} WHERE e.id = ?", (answer_id,))
        return None if row is None else self._to_model(row)

    def lock_text(self, answer_id: int) -> str | None:
        """本文の行を境界の終わりまでロックして、いまの本文を返す。行がなければ None。

        表示していた本文との突き合わせと書き込みの間に、別の接続が割り込まないようにする
        （PostgreSQL の既定の分離レベルでは、境界を開いても他の接続を待たせないため）。
        企業との結合を含めると FOR UPDATE を付けられないので、本文の表だけを読む。
        """
        row = self._db.fetchone(f"SELECT answer FROM es_answers WHERE id = ? {FOR_UPDATE}", (answer_id,))
        return None if row is None else row["answer"]

    def list_all(self) -> list[EsAnswer]:
        rows = self._db.fetchall(f"{self._SELECT_WITH_COMPANY} ORDER BY e.updated_at DESC, e.id DESC")
        return [self._to_model(row) for row in rows]

    def _today(self) -> str:
        expression = self._db.dialect.substitutions()[TODAY]
        row = self._db.fetchone(f"SELECT {expression} AS today")
        return str(row["today"])


class ReviewRepository(_Table):
    table = "reviews"
    writable = frozenset(
        {
            "es_answer_id",
            "industry",
            "provider",
            "model",
            "prompt",
            "result",
            "answer_snapshot",
            "input_tokens",
            "output_tokens",
        }
    )

    @staticmethod
    def _to_model(row: Any) -> Review:
        return Review(
            id=row["id"],
            es_answer_id=row["es_answer_id"],
            industry=row["industry"],
            provider=row["provider"],
            model=row["model"],
            prompt=row["prompt"],
            result=row["result"],
            answer_snapshot=row["answer_snapshot"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            created_at=row["created_at"],
        )

    def add(self, review: Review) -> int:
        return self._insert(
            {
                "es_answer_id": review.es_answer_id,
                "industry": review.industry,
                "provider": review.provider,
                "model": review.model,
                "prompt": review.prompt,
                "result": review.result,
                "answer_snapshot": review.answer_snapshot,
                "input_tokens": review.input_tokens,
                "output_tokens": review.output_tokens,
            }
        )

    def get(self, review_id: int) -> Review | None:
        row = self._db.fetchone("SELECT * FROM reviews WHERE id = ?", (review_id,))
        return None if row is None else self._to_model(row)

    def list_for_answer(self, es_answer_id: int) -> list[Review]:
        """新しいものから順に返す。"""
        rows = self._db.fetchall(
            "SELECT * FROM reviews WHERE es_answer_id = ? ORDER BY id DESC",
            (es_answer_id,),
        )
        return [self._to_model(row) for row in rows]
