"""選考管理のユースケース。UI はこの層だけを呼ぶ。

画面から SQL も集計関数も直接触らせないことで、「企業を1社追加したら
既定の選考ステップも一緒に入る」といった業務ルールの置き場所を1か所にする。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from .. import analytics, constants
from ..db import CompanyRepository, Database, StepRepository, transaction
from ..models import Company, Deadline, FunnelRow, Step, StepView


class _Unset:
    """「引数が渡されなかった」ことを None と区別するための番兵。"""

    __slots__ = ()


UNSET = _Unset()


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    """ダッシュボードが必要とするものを1回の問い合わせでまとめて返す。

    企業一覧とステップも持たせているのは、画面側が同じ問い合わせを
    もう一度出さずに済むようにするため。
    """

    companies: list[Company]
    steps_by_company: dict[int, list[StepView]]
    active_companies: int
    deadlines: list[Deadline]

    @property
    def total_companies(self) -> int:
        return len(self.companies)

    @property
    def overdue(self) -> list[Deadline]:
        """期限を過ぎたもの。"""
        return [deadline for deadline in self.deadlines if deadline.overdue]

    @property
    def upcoming(self) -> list[Deadline]:
        """これから期限を迎えるもの。期限超過は含めない。"""
        return [deadline for deadline in self.deadlines if not deadline.overdue]


class SelectionService:
    """企業と選考ステップの操作。"""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._companies = CompanyRepository(db)
        self._steps = StepRepository(db)

    # --- 企業 ---------------------------------------------------------

    def companies(self) -> list[Company]:
        return self._companies.list_all()

    def company(self, company_id: int) -> Company | None:
        return self._companies.get(company_id)

    def add_company(self, company: Company, *, with_default_steps: bool = True) -> int:
        """企業を登録する。既定の選考ステップもまとめて入れる。

        企業とステップは1つのトランザクションで書く。片方だけ入った状態を
        残さないため（ステップのない企業は一覧で状況を出せなくなる）。
        """
        name = company.name.strip()
        if not name:
            raise ValueError("企業名は必須です")
        with transaction(self._db):
            company_id = self._companies.add(
                Company(
                    name=name,
                    industry=company.industry,
                    priority=company.priority,
                    route=company.route,
                    test_type=company.test_type,
                    mypage_url=company.mypage_url,
                    login_email=company.login_email,
                    memo=company.memo,
                )
            )
            if with_default_steps:
                self._steps.add_many(
                    [
                        Step(company_id=company_id, name=name_, sort_order=order)
                        for order, name_ in enumerate(constants.DEFAULT_STEPS)
                    ]
                )
        return company_id

    def update_company(self, company_id: int, **fields: object) -> None:
        with transaction(self._db):
            self._companies.update(company_id, **fields)

    def delete_company(self, company_id: int) -> None:
        """企業を削除する。ステップは連動削除、ES 回答は残る（スキーマの制約）。"""
        with transaction(self._db):
            self._companies.delete(company_id)

    # --- 選考ステップ -------------------------------------------------

    def steps_of(self, company_id: int) -> list[Step]:
        return self._steps.list_for_company(company_id)

    def all_step_views(self) -> list[StepView]:
        return self._steps.list_views()

    def steps_by_company(self) -> dict[int, list[StepView]]:
        """全ステップを企業ごとにまとめる。企業数ぶんの問い合わせを避けるため。"""
        grouped: dict[int, list[StepView]] = defaultdict(list)
        for step in self._steps.list_views():
            grouped[step.company_id].append(step)
        return dict(grouped)

    def add_step(self, company_id: int, name: str, *, deadline: str | None = None) -> int:
        label = name.strip()
        if not label:
            raise ValueError("ステップ名は必須です")
        existing = self._steps.list_for_company(company_id)
        with transaction(self._db):
            return self._steps.add(
                Step(
                    company_id=company_id,
                    name=label,
                    deadline=deadline,
                    sort_order=len(existing),
                )
            )

    def update_step(
        self,
        step_id: int,
        *,
        deadline: str | None | _Unset = UNSET,
        result: str | _Unset = UNSET,
    ) -> None:
        """指定された項目だけを更新する。

        締切は「未指定」と「空にする（None）」を区別する必要があるため、
        None ではなく専用の番兵で未指定を表す。
        """
        fields: dict[str, object] = {}
        if not isinstance(deadline, _Unset):
            fields["deadline"] = deadline
        if not isinstance(result, _Unset):
            if result not in constants.STEP_RESULTS:
                raise ValueError(f"未定義の選考結果です: {result}")
            fields["result"] = result
        if not fields:
            return
        with transaction(self._db):
            self._steps.update(step_id, **fields)

    def delete_step(self, step_id: int) -> None:
        with transaction(self._db):
            self._steps.delete(step_id)

    # --- 集計 ---------------------------------------------------------

    def dashboard(self, today: date, *, within_days: int = 7) -> DashboardSummary:
        """画面1枚ぶんの情報を、テーブルごとに1回ずつの問い合わせで集める。"""
        grouped = self.steps_by_company()
        companies = self._companies.list_all()
        active = sum(
            1 for company in companies if analytics.is_active(grouped.get(company.id or -1, []))
        )
        all_steps = [step for steps in grouped.values() for step in steps]
        return DashboardSummary(
            companies=companies,
            steps_by_company=grouped,
            active_companies=active,
            deadlines=analytics.upcoming_deadlines(all_steps, today, within_days=within_days),
        )

    def funnel(self) -> list[FunnelRow]:
        return analytics.funnel(self.all_step_views(), constants.DEFAULT_STEPS)
