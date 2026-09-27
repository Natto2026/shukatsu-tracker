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


class StaleAnswerError(ValueError):
    """表示していた本文が、保存するまでの間に他の場所で更新されていた。"""


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

    def update_text(self, answer_id: int, text: str, *, expected: str | None = None) -> None:
        """本文を書き換える。`expected` を渡すと、いまの本文がそれと一致するときだけ書く。

        表示してから保存するまでの間に別のタブや端末が更新していた場合に、古い表示
        からの入力で新しい本文を潰さないため。判定は書き込みと同じ境界の中で、行を
        ロックしてから行う（SQLite は開始時の書き込みロック、PostgreSQL は FOR UPDATE）。
        """
        with transaction(self._db):
            if expected is not None:
                current = self._answers.lock_text(answer_id)
                if current is None:
                    raise ValueError("回答が見つかりません。別の場所で削除された可能性があります")
                if current != expected:
                    raise StaleAnswerError(
                        "表示後に他の場所で更新されたため、この入力は反映していません。最新の本文を表示しています"
                    )
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
