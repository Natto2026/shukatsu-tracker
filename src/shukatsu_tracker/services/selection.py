"""選考管理のユースケース。UI はこの層だけを呼ぶ。

画面から SQL も集計関数も直接触らせないことで、「企業を1社追加したら
既定の選考ステップも一緒に入る」といった業務ルールの置き場所を1か所にする。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from .. import analytics, constants
from ..db import CompanyRepository, Database, StepRepository, transaction
from ..models import Company, Deadline, Step, StepView


class _Unset:
    """「引数が渡されなかった」ことを None と区別するための番兵。"""

    __slots__ = ()


UNSET = _Unset()

# 企業の列のうち、選択肢が決まっているもの。画面の選択肢と CSV の取り込みが
# 同じ定数を見ているが、サービス層でも検証する。別の入口（スクリプトなど）から
# 選択肢にない値が入ると、一覧の並びや通過率の集計から黙って外れるため。
_COMPANY_CHOICES: dict[str, list[str]] = {
    "industry": constants.INDUSTRIES,
    "priority": constants.PRIORITIES,
    "route": constants.ROUTES,
    "test_type": constants.TEST_TYPES,
}
_COMPANY_LABELS = {"industry": "業界", "priority": "志望度", "route": "応募経路", "test_type": "適性検査"}
# 前後の空白を除く列。URL は先頭の空白で「リンクとして開ける」判定に落ちる
_TRIMMED = ("name", "mypage_url", "login_email")


def _validated_company_fields(fields: Mapping[str, object]) -> dict[str, Any]:
    """企業の列の値を検証し、整えて返す。選択肢にない値・空の企業名は ValueError。"""
    cleaned: dict[str, Any] = {}
    for key, value in fields.items():
        if key in _TRIMMED and isinstance(value, str):
            value = value.strip()
        if key == "name" and not value:
            raise ValueError("企業名は必須です")
        choices = _COMPANY_CHOICES.get(key)
        if choices is not None and value not in choices:
            raise ValueError(
                f"{_COMPANY_LABELS[key]}「{value}」は選択肢にありません（{' / '.join(choices)}）"
            )
        cleaned[key] = value
    return cleaned


def _validated_deadline(deadline: str | None) -> str | None:
    """締切は空か YYYY-MM-DD。読めない書式は、空にせず ValueError にする。

    黙って空にすると、締切一覧からその行が消えて気づけない（CSV の取り込みと同じ方針）。
    """
    if not deadline:
        return None
    parsed = analytics.parse_date(deadline)
    if parsed is None:
        raise ValueError(f"締切「{deadline}」を YYYY-MM-DD の日付として読めません")
    return parsed.isoformat()


@dataclass(frozen=True, slots=True)
class StepChange:
    """選考ステップ1件に対する変更。未指定の項目は触らない。

    締切は「未指定」と「空にする（None）」を区別する必要があるため、
    None ではなく専用の番兵で未指定を表す。
    """

    step_id: int
    deadline: str | None | _Unset = UNSET
    result: str | _Unset = UNSET

    def fields(self) -> dict[str, object]:
        """書き込む列と値。選択肢にない結果は ValueError。"""
        fields: dict[str, object] = {}
        if not isinstance(self.deadline, _Unset):
            fields["deadline"] = _validated_deadline(self.deadline)
        if not isinstance(self.result, _Unset):
            if self.result not in constants.STEP_RESULTS:
                raise ValueError(f"未定義の選考結果です: {self.result}")
            fields["result"] = self.result
        return fields


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
        cleaned = _validated_company_fields(
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
        with transaction(self._db):
            company_id = self._companies.add(replace(company, id=None, **cleaned))
            if with_default_steps:
                self._steps.add_many(
                    [
                        Step(company_id=company_id, name=name_, sort_order=order)
                        for order, name_ in enumerate(constants.DEFAULT_STEPS)
                    ]
                )
        return company_id

    def update_company(self, company_id: int, **fields: object) -> None:
        """指定された列だけを更新する。値の検証は追加と同じ。"""
        cleaned = _validated_company_fields(fields)
        if not cleaned:
            return
        with transaction(self._db):
            self._companies.update(company_id, **cleaned)

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
        checked_deadline = _validated_deadline(deadline)
        # 並び順を決める読み取りも境界の中で行う。外で読むと、読んでから書くまでの
        # 間に別の追加が割り込み、同じ並び順が2つできる。
        with transaction(self._db):
            existing = self._steps.list_for_company(company_id)
            return self._steps.add(
                Step(
                    company_id=company_id,
                    name=label,
                    deadline=checked_deadline,
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
        """指定された項目だけを更新する。1件版。複数は update_steps。"""
        self.update_steps([StepChange(step_id, deadline=deadline, result=result)])

    def update_steps(self, changes: Sequence[StepChange]) -> int:
        """複数のステップをまとめて更新し、書いた行数を返す。

        全行を先に検証してから、1つの境界で書く。3行目で失敗して1〜2行目だけが
        残ると、利用者は何が保存されたのか分からない。まとめて成功するか、
        まとめて失敗するかのどちらかにする。
        """
        planned = [(change.step_id, fields) for change in changes if (fields := change.fields())]
        if not planned:
            return 0
        with transaction(self._db):
            for step_id, fields in planned:
                self._steps.update(step_id, **fields)
        return len(planned)

    def delete_step(self, step_id: int) -> None:
        with transaction(self._db):
            self._steps.delete(step_id)

    # --- 集計 ---------------------------------------------------------

    def dashboard(self, today: date, *, within_days: int = 7) -> DashboardSummary:
        """画面1枚ぶんの情報を、テーブルごとに1回ずつの問い合わせで集める。"""
        grouped = self.steps_by_company()
        companies = self._companies.list_all()
        active = sum(1 for company in companies if analytics.is_active(grouped.get(company.id or -1, [])))
        all_steps = [step for steps in grouped.values() for step in steps]
        return DashboardSummary(
            companies=companies,
            steps_by_company=grouped,
            active_companies=active,
            deadlines=analytics.upcoming_deadlines(all_steps, today, within_days=within_days),
        )
