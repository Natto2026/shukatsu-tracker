"""選考データの集計ロジック。

DB にも UI にも依存しない純粋関数として実装する。入力は StepView の列だけで、
コネクションも Streamlit も受け取らない。こうすると集計の単体テストが
データを組み立てるだけで書け、画面を変えてもテストが壊れない。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime

from .models import Deadline, FunnelRow, PassRate, StepView

IN_PROGRESS = "選考中"
PASSED = "通過"
FAILED = "落選"
DECLINED = "辞退"

_JUDGED = (PASSED, FAILED)


def parse_date(value: str | None) -> date | None:
    """ISO 形式の日付文字列を date にする。読めなければ None。"""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def upcoming_deadlines(
    steps: Iterable[StepView], today: date, within_days: int = 7
) -> list[Deadline]:
    """締切が within_days 日以内の未完了ステップを、締切が近い順に返す。

    期限超過のものも含める（見落としこそ防ぎたいため）。
    """
    found: list[Deadline] = []
    for step in steps:
        if step.result != IN_PROGRESS:
            continue
        deadline = parse_date(step.deadline)
        if deadline is None:
            continue
        days_left = (deadline - today).days
        if days_left <= within_days:
            found.append(Deadline(step=step, days_left=days_left))
    return sorted(found, key=lambda d: d.days_left)


def pass_rate_by(
    steps: Iterable[StepView], attribute: str, step_name: str | None = None
) -> list[PassRate]:
    """属性（route / test_type / industry）ごとの通過率を、高い順に返す。

    step_name を指定するとそのステップだけを対象にする（例: "ES"）。
    分母は結果が確定したものだけで、選考中・辞退は数えない。
    """
    if attribute not in {"route", "test_type", "industry"}:
        raise ValueError(f"集計できない属性です: {attribute}")

    tally: dict[str, list[int]] = {}
    for step in steps:
        if step_name is not None and step.name != step_name:
            continue
        if step.result not in _JUDGED:
            continue
        group = getattr(step, attribute) or "不明"
        counts = tally.setdefault(group, [0, 0])
        counts[0 if step.result == PASSED else 1] += 1

    rates = [PassRate(group=group, passed=p, failed=f) for group, (p, f) in tally.items()]
    return sorted(rates, key=lambda r: (-r.rate, -r.total, r.group))


def funnel(steps: Iterable[StepView], step_order: Sequence[str]) -> list[FunnelRow]:
    """ステップ名ごとの件数を、標準の選考順で返す。

    step_order にないステップ名（企業独自のワークなど）は末尾にまとめる。
    """
    counts: dict[str, dict[str, int]] = {}
    for step in steps:
        row = counts.setdefault(
            step.name, {PASSED: 0, FAILED: 0, IN_PROGRESS: 0, DECLINED: 0}
        )
        if step.result in row:
            row[step.result] += 1

    ordered = [name for name in step_order if name in counts]
    ordered += [name for name in counts if name not in step_order]
    return [
        FunnelRow(
            step=name,
            passed=counts[name][PASSED],
            failed=counts[name][FAILED],
            in_progress=counts[name][IN_PROGRESS],
            declined=counts[name][DECLINED],
        )
        for name in ordered
    ]


def company_status(steps: Sequence[StepView]) -> str:
    """1社分のステップ一覧から、現在の状況ラベルを導く。"""
    if not steps:
        return "未エントリー"
    if any(step.result == FAILED for step in steps):
        return FAILED
    if any(step.result == DECLINED for step in steps):
        return DECLINED
    in_progress = [step for step in steps if step.result == IN_PROGRESS]
    if not in_progress:
        return f"{steps[-1].name}通過"
    return f"{in_progress[0].name}待ち"


def is_active(steps: Sequence[StepView]) -> bool:
    """選考が継続中か（落選・辞退で終わっていないか）。"""
    return company_status(steps) not in (FAILED, DECLINED)
