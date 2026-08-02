"""選考データの集計ロジック。

DB に依存しない純粋関数として実装し、単体テストできるようにしている。
入力はすべて db.list_steps(conn) が返す「企業情報付きステップ」の list[dict]。
"""

from __future__ import annotations

from datetime import date, datetime


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def upcoming_deadlines(steps: list[dict], today: date, within_days: int = 7) -> list[dict]:
    """締切が近い(within_days 日以内)未完了ステップを締切順で返す。

    期限超過のものも「超過」として含める(見落としこそ防ぎたいため)。
    """
    result = []
    for s in steps:
        if s.get("result") != "選考中":
            continue
        deadline = parse_date(s.get("deadline"))
        if deadline is None:
            continue
        days_left = (deadline - today).days
        if days_left <= within_days:
            result.append({**s, "days_left": days_left, "overdue": days_left < 0})
    return sorted(result, key=lambda s: s["days_left"])


def pass_rate_by(steps: list[dict], key: str, step_name: str | None = None) -> dict[str, dict]:
    """key(route / test_type など)ごとの通過率を集計する。

    step_name を指定するとそのステップのみ(例: "ES")、省略時は全ステップが対象。
    結果が「通過」「落選」のものだけを分母にする(選考中・辞退は除外)。
    """
    stats: dict[str, dict] = {}
    for s in steps:
        if step_name is not None and s.get("name") != step_name:
            continue
        if s.get("result") not in ("通過", "落選"):
            continue
        group = s.get(key) or "不明"
        entry = stats.setdefault(group, {"passed": 0, "failed": 0})
        entry["passed" if s["result"] == "通過" else "failed"] += 1
    for entry in stats.values():
        total = entry["passed"] + entry["failed"]
        entry["total"] = total
        entry["rate"] = entry["passed"] / total if total else 0.0
    return stats


def funnel(steps: list[dict], step_order: list[str]) -> list[dict]:
    """ステップ名ごとの 通過/落選/選考中 件数を、標準の選考順で返す。

    step_order にないステップ名は末尾にまとめる。
    """
    counts: dict[str, dict] = {}
    for s in steps:
        entry = counts.setdefault(s["name"], {"通過": 0, "落選": 0, "選考中": 0, "辞退": 0})
        result = s.get("result", "選考中")
        if result in entry:
            entry[result] += 1
    ordered = [n for n in step_order if n in counts]
    ordered += [n for n in counts if n not in step_order]
    return [{"step": n, **counts[n]} for n in ordered]


def company_status(steps: list[dict]) -> str:
    """1社分のステップ一覧から現在の状況ラベルを導く。"""
    if not steps:
        return "未エントリー"
    if any(s.get("result") == "落選" for s in steps):
        return "落選"
    if any(s.get("result") == "辞退" for s in steps):
        return "辞退"
    in_progress = [s for s in steps if s.get("result") == "選考中"]
    if not in_progress:
        return f"{steps[-1]['name']}通過"
    return f"{in_progress[0]['name']}待ち"
