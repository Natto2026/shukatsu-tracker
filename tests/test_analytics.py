from datetime import date

from shukatsu_tracker import analytics


def make_step(**kwargs):
    base = {
        "name": "ES",
        "result": "選考中",
        "deadline": None,
        "company_name": "テスト社",
        "route": "一般公募",
        "test_type": "SPI",
    }
    return {**base, **kwargs}


class TestUpcomingDeadlines:
    TODAY = date(2026, 8, 1)

    def test_within_days(self):
        steps = [
            make_step(deadline="2026-08-03"),
            make_step(deadline="2026-08-20"),
        ]
        result = analytics.upcoming_deadlines(steps, self.TODAY, within_days=7)
        assert len(result) == 1
        assert result[0]["days_left"] == 2
        assert not result[0]["overdue"]

    def test_overdue_is_included_and_sorted_first(self):
        steps = [
            make_step(deadline="2026-08-05"),
            make_step(deadline="2026-07-30"),
        ]
        result = analytics.upcoming_deadlines(steps, self.TODAY)
        assert [r["overdue"] for r in result] == [True, False]

    def test_finished_and_undated_steps_are_ignored(self):
        steps = [
            make_step(deadline="2026-08-02", result="通過"),
            make_step(deadline=None),
            make_step(deadline="不正な日付"),
        ]
        assert analytics.upcoming_deadlines(steps, self.TODAY) == []


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
        stats = analytics.pass_rate_by(steps, "route")
        assert stats["スカウト・逆求人"]["rate"] == 1.0
        assert stats["一般公募"]["total"] == 3
        assert stats["一般公募"]["rate"] == 1 / 3

    def test_filter_by_step_name(self):
        steps = [
            make_step(name="ES", result="通過"),
            make_step(name="1次面接", result="落選"),
        ]
        stats = analytics.pass_rate_by(steps, "route", step_name="ES")
        assert stats["一般公募"] == {"passed": 1, "failed": 0, "total": 1, "rate": 1.0}


class TestFunnel:
    def test_counts_in_standard_order(self):
        steps = [
            make_step(name="ES", result="通過"),
            make_step(name="ES", result="落選"),
            make_step(name="1次面接", result="選考中"),
            make_step(name="独自ワーク", result="通過"),
        ]
        result = analytics.funnel(steps, ["ES", "Webテスト", "1次面接"])
        assert [r["step"] for r in result] == ["ES", "1次面接", "独自ワーク"]
        assert result[0]["通過"] == 1 and result[0]["落選"] == 1


class TestCompanyStatus:
    def test_no_steps(self):
        assert analytics.company_status([]) == "未エントリー"

    def test_failed_wins(self):
        steps = [make_step(result="通過"), make_step(name="1次面接", result="落選")]
        assert analytics.company_status(steps) == "落選"

    def test_waiting_on_first_in_progress(self):
        steps = [
            make_step(result="通過"),
            make_step(name="Webテスト", result="選考中"),
            make_step(name="1次面接", result="選考中"),
        ]
        assert analytics.company_status(steps) == "Webテスト待ち"

    def test_all_passed(self):
        steps = [make_step(result="通過"), make_step(name="最終面接", result="通過")]
        assert analytics.company_status(steps) == "最終面接通過"
