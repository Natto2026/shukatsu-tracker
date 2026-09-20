"""回答への所見を取るユースケース。

依頼文の組み立て・実行・保存の順序をここで固定する。実行先（通信するか
しないか）は引数で受け取り、この層では選ばない。
"""

from __future__ import annotations

from ..db import (
    CompanyRepository,
    Database,
    EsAnswerRepository,
    ReviewRepository,
    transaction,
)
from ..models import EsAnswer, Review
from ..review import criteria as criteria_module
from ..review import prompt as prompt_module
from ..review.criteria import CriteriaSet
from ..review.prompt import ReviewRequest
from ..review.providers import ExportProvider, ReviewProvider


class ReviewService:
    """設問と回答に対して所見を取り、履歴として残す。"""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._reviews = ReviewRepository(db)
        self._answers = EsAnswerRepository(db)
        self._companies = CompanyRepository(db)

    # --- 観点 ---------------------------------------------------------

    @staticmethod
    def criteria_for(industry: str | None) -> CriteriaSet:
        return criteria_module.for_industry(industry)

    def industry_of(self, answer: EsAnswer) -> str | None:
        """回答の提出先から業界を引く。企業に紐付いていなければ None。"""
        if answer.company_id is None:
            return None
        company = self._companies.get(answer.company_id)
        return None if company is None else company.industry

    # --- 依頼文 -------------------------------------------------------

    def build_request(
        self, answer: EsAnswer, *, industry: str | None = None, note: str = ""
    ) -> ReviewRequest:
        return ReviewRequest(
            question=answer.question,
            answer=answer.answer,
            char_limit=answer.char_limit,
            industry=industry if industry is not None else self.industry_of(answer),
            company_name=answer.company_name,
            note=note,
        )

    def build_prompt(
        self, answer: EsAnswer, *, industry: str | None = None, note: str = ""
    ) -> str:
        """実行せずに、送られる文面だけを組み立てる。

        何を渡すことになるのかを、実行前に必ず確認できるようにするため。
        """
        request = self.build_request(answer, industry=industry, note=note)
        return prompt_module.build(request, self.criteria_for(request.industry))

    # --- 実行と保存 ---------------------------------------------------

    def run(
        self,
        answer: EsAnswer,
        *,
        provider: ReviewProvider | None = None,
        industry: str | None = None,
        note: str = "",
    ) -> Review:
        """所見を取り、依頼文と結果を履歴に残す。

        既定の実行先は通信しない。保存する本文は評価した時点のもので、
        あとから回答を書き換えても所見との対応が追えるようにする。
        """
        if answer.id is None:
            raise ValueError("保存されていない回答は評価できません")

        engine = provider or ExportProvider()
        request = self.build_request(answer, industry=industry, note=note)
        built = prompt_module.build(request, self.criteria_for(request.industry))
        result = engine.review(request, built)

        record = Review(
            es_answer_id=answer.id,
            industry=request.industry or "",
            provider=result.provider,
            model=result.model,
            prompt=result.prompt,
            result=result.text,
            answer_snapshot=answer.answer,
        )
        with transaction(self._db):
            review_id = self._reviews.add(record)
        stored = self._reviews.get(review_id)
        if stored is None:  # pragma: no cover - 直前に書いた行が読めない場合
            raise RuntimeError("所見を保存できませんでした")
        return stored

    # --- 履歴 ---------------------------------------------------------

    def history(self, es_answer_id: int) -> list[Review]:
        return self._reviews.list_for_answer(es_answer_id)

    def latest(self, es_answer_id: int) -> Review | None:
        return self._reviews.latest_for_answer(es_answer_id)

    def delete(self, review_id: int) -> None:
        with transaction(self._db):
            self._reviews.delete(review_id)
