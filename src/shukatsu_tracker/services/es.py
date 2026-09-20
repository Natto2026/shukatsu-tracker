"""設問と回答のライブラリのユースケース。

保存・検索・文字数の照合をここに集約する。文字数の判定を UI に書くと、
書き出しや評価から呼んだときに同じ判定を書き直すことになるため。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..db import Database, EsAnswerRepository, transaction
from ..models import EsAnswer


@dataclass(frozen=True, slots=True)
class LengthCheck:
    """回答の文字数と、制限に対する状態。"""

    length: int
    limit: int | None

    @property
    def over(self) -> bool:
        return self.limit is not None and self.length > self.limit

    @property
    def short(self) -> bool:
        """制限の8割に満たないか（指定枠を使い切れていない状態）。"""
        return self.limit is not None and self.length < int(self.limit * 0.8)


class EsService:
    """ES 回答の保存と検索。"""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._answers = EsAnswerRepository(db)

    def answers(self) -> list[EsAnswer]:
        return self._answers.list_all()

    def answer(self, answer_id: int) -> EsAnswer | None:
        return self._answers.get(answer_id)

    def add(self, answer: EsAnswer) -> int:
        question = answer.question.strip()
        if not question:
            raise ValueError("設問文は必須です")
        if answer.char_limit is not None and answer.char_limit < 0:
            raise ValueError("文字数制限に負の値は指定できません")
        with transaction(self._db):
            return self._answers.add(
                EsAnswer(
                    question=question,
                    category=answer.category,
                    company_id=answer.company_id,
                    char_limit=answer.char_limit or None,
                    answer=answer.answer,
                )
            )

    def update_text(self, answer_id: int, text: str) -> None:
        with transaction(self._db):
            self._answers.update(answer_id, answer=text)

    def delete(self, answer_id: int) -> None:
        with transaction(self._db):
            self._answers.delete(answer_id)

    def search(self, *, categories: list[str] | None = None, keyword: str = "") -> list[EsAnswer]:
        """カテゴリとキーワードで絞り込む。どちらも空なら全件。"""
        needle = keyword.strip()
        results = []
        for answer in self._answers.list_all():
            if categories and answer.category not in categories:
                continue
            if needle and needle not in answer.question and needle not in answer.answer:
                continue
            results.append(answer)
        return results

    @staticmethod
    def length_check(text: str, limit: int | None) -> LengthCheck:
        return LengthCheck(length=len(text), limit=limit)
