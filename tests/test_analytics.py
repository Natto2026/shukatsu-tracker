from datetime import date

import pytest
from factories import make_step

from shukatsu_tracker import analytics


class TestUpcomingDeadlines:
    TODAY = date(2026, 8, 1)

    def test_within_days(self):
        steps = [make_step(deadline="2026-08-03"), make_step(deadline="2026-08-20")]
        result = analytics.upcoming_deadlines(steps, self.TODAY, within_days=7)
        assert len(result) == 1
        assert result[0].days_left == 2
        assert not result[0].overdue

    def test_overdue_is_included_and_sorted_first(self):
        steps = [make_step(deadline="2026-08-05"), make_step(deadline="2026-07-30")]
        result = analytics.upcoming_deadlines(steps, self.TODAY)
        assert [d.overdue for d in result] == [True, False]

    def test_finished_and_undated_steps_are_ignored(self):
        steps = [
            make_step(deadline="2026-08-02", result="通過"),
            make_step(deadline=None),
            make_step(deadline="不正な日付"),
        ]
        assert analytics.upcoming_deadlines(steps, self.TODAY) == []

    def test_remaining_steps_of_an_ended_company_are_marked_not_dropped(self):
        """落選した企業に残った「選考中」の締切は、消さずに印を付ける。ほかの企業には付けない。

        企業の追加時に入った標準のステップの残りか、続いている選考かは区別できない。
        黙って消すと、辞退したインターンのあとの本選考の締切まで見えなくなる。
        """
        steps = [
            make_step(company_id=1, name="ES", result="落選"),
            make_step(company_id=1, name="Webテスト", deadline="2026-07-30"),
            make_step(company_id=2, name="Webテスト", deadline="2026-07-30"),
        ]
        result = analytics.upcoming_deadlines(steps, self.TODAY)
        assert sorted((d.step.company_id, d.company_ended) for d in result) == [(1, True), (2, False)]

    def test_a_declined_company_is_also_ended(self):
        steps = [
            make_step(company_id=1, name="1次面接", result="辞退"),
            make_step(company_id=1, name="2次面接", deadline="2026-08-02"),
        ]
        (deadline,) = analytics.upcoming_deadlines(steps, self.TODAY)
        assert deadline.company_ended


class TestPassRateBy:
    def test_rate_by_route(self):
        steps = [
            make_step(route="スカウト・逆求人", result="通過"),
            make_step(route="スカウト・逆求人", result="通過"),
            make_step(route="一般公募", result="通過"),
            make_step(route="一般公募", result="落選"),
            make_step(route="一般公募", result="落選"),
            make_step(route="一般公募", result="選考中"),  # 分母に入らない
        ]
        rates = {r.group: r for r in analytics.pass_rate_by(steps, "route")}
        assert rates["スカウト・逆求人"].rate == 1.0
        assert rates["一般公募"].total == 3
        assert rates["一般公募"].rate == 1 / 3

    def test_sorted_by_rate_descending(self):
        steps = [
            make_step(route="一般公募", result="落選"),
            make_step(route="学校推薦", result="通過"),
        ]
        assert [r.group for r in analytics.pass_rate_by(steps, "route")] == ["学校推薦", "一般公募"]

    def test_filter_by_step_name(self):
        steps = [
            make_step(name="ES", result="通過"),
            make_step(name="1次面接", result="落選"),
        ]
        rates = analytics.pass_rate_by(steps, "route", step_name="ES")
        assert len(rates) == 1
        assert (rates[0].passed, rates[0].failed, rates[0].rate) == (1, 0, 1.0)

    def test_unknown_attribute_is_rejected(self):
        with pytest.raises(ValueError):
            analytics.pass_rate_by([], "memo")


class TestFunnel:
    def test_counts_in_standard_order(self):
        steps = [
            make_step(name="ES", result="通過"),
            make_step(name="ES", result="落選"),
            make_step(company_id=2, name="1次面接", result="選考中"),
            make_step(name="独自ワーク", result="通過"),
        ]
        rows = analytics.funnel(steps, ["ES", "Webテスト", "1次面接"])
        assert [r.step for r in rows] == ["ES", "1次面接", "独自ワーク"]
        assert rows[0].passed == 1
        assert rows[0].failed == 1

    def test_a_step_only_an_ended_company_has_left_gets_no_empty_row(self):
        """終わった企業にしか残っていないステップを、全部 0 の行として出さない。"""
        steps = [
            make_step(company_id=1, name="ES", result="落選"),
            make_step(company_id=1, name="Webテスト"),
        ]
        assert [r.step for r in analytics.funnel(steps, ["ES", "Webテスト"])] == ["ES"]

    def test_remaining_steps_of_an_ended_company_are_not_in_progress(self):
        """落ちた企業の後続ステップは、進んでいないので「選考中」に数えない。"""
        steps = [
            make_step(company_id=1, name="ES", result="落選"),
            make_step(company_id=1, name="Webテスト"),
            make_step(company_id=2, name="ES", result="通過"),
            make_step(company_id=2, name="Webテスト"),
        ]
        rows = {r.step: r for r in analytics.funnel(steps, ["ES", "Webテスト"])}
        assert rows["Webテスト"].in_progress == 1
        assert (rows["ES"].passed, rows["ES"].failed) == (1, 1)


class TestCompanyStatus:
    def test_no_steps(self):
        assert analytics.company_status([]) == "未エントリー"

    def test_failed_wins(self):
        steps = [make_step(result="通過"), make_step(name="1次面接", result="落選")]
        assert analytics.company_status(steps) == "落選"
        assert not analytics.is_active(steps)

    def test_waiting_on_first_in_progress(self):
        steps = [
            make_step(result="通過"),
            make_step(name="Webテスト", result="選考中"),
            make_step(name="1次面接", result="選考中"),
        ]
        assert analytics.company_status(steps) == "Webテスト待ち"
        assert analytics.is_active(steps)

    def test_all_passed(self):
        steps = [make_step(result="通過"), make_step(name="最終面接", result="通過")]
        assert analytics.company_status(steps) == "最終面接通過"
