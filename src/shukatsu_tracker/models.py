"""アプリ全体で共有する型付きモデル。

UI・サービス・リポジトリはすべてこの型でやり取りする。dict を層をまたいで
持ち回すと、キー名の打ち間違いが実行時まで分からないため型で固定する。
永続化の都合（id・created_at）は Optional にして、未保存の値も同じ型で扱う。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Company:
    """応募先企業。"""

    name: str
    industry: str = "その他"
    priority: str = "B"
    route: str = "一般公募"
    test_type: str = "不明"
    mypage_url: str = ""
    login_email: str = ""
    memo: str = ""
    id: int | None = None
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class Step:
    """1社の選考ステップ（ES・Webテスト・面接など）。"""

    company_id: int
    name: str
    deadline: str | None = None
    result: str = "選考中"
    memo: str = ""
    sort_order: int = 0
    id: int | None = None


@dataclass(frozen=True, slots=True)
class StepView:
    """企業情報を結合したステップ。集計はこの型だけを入力にする。"""

    id: int
    company_id: int
    company_name: str
    name: str
    deadline: str | None
    result: str
    memo: str
    sort_order: int
    industry: str
    route: str
    test_type: str


@dataclass(frozen=True, slots=True)
class EsAnswer:
    """設問と回答。企業に紐付く場合と、汎用ストックの場合がある。"""

    question: str
    category: str = "その他"
    company_id: int | None = None
    company_name: str | None = None
    char_limit: int | None = None
    answer: str = ""
    updated_at: str | None = None
    id: int | None = None

    @property
    def length(self) -> int:
        return len(self.answer)

    @property
    def over_limit(self) -> bool:
        return self.char_limit is not None and self.length > self.char_limit


@dataclass(frozen=True, slots=True)
class Deadline:
    """締切が近いステップと、残り日数。"""

    step: StepView
    days_left: int

    @property
    def overdue(self) -> bool:
        return self.days_left < 0


@dataclass(frozen=True, slots=True)
class PassRate:
    """ある区分（応募経路・適性検査など）の通過実績。"""

    group: str
    passed: int
    failed: int

    @property
    def total(self) -> int:
        return self.passed + self.failed

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class FunnelRow:
    """選考ステップごとの件数。表示名は UI 側で与える。"""

    step: str
    passed: int = 0
    failed: int = 0
    in_progress: int = 0
    declined: int = 0
