"""選考データを分析用の Markdown に書き出す。

ここは書き出しだけを行い、外部への送信はしない。生成したファイルを
どこまで誰に渡すかの判断を利用者の手元に残すため。

マイページ URL・ログイン用メールアドレスは分析に不要な認証系情報のため、
書き出しに含めない（tests/test_research_and_export.py の回帰テストで保証）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from . import analytics, constants
from .models import Company, EsAnswer, PassRate, StepView

PROMPT_HEADER = """\
# 依頼: 選考データの分析

以下は私の選考記録です。このデータを読み、次の4点を教えてください。

1. **結果の傾向**: どの選考ステップ・応募経路・適性検査タイプで結果が分かれているか
2. **考えられる要因の仮説**: データから読み取れる範囲で（推測は推測と明示して）
3. **次にとる行動**: 優先順位つきで具体的に（例: 特定の適性検査の対策、応募経路の見直し）
4. **今後の応募方針**: 選考中の企業への対応と、追加で検討すべき企業タイプ

データにない情報（面接の受け答え等）が必要なら、私への質問として挙げてください。
"""


def _rate_table(rates: Sequence[PassRate], label: str) -> list[str]:
    lines = [f"| {label} | 通過 | 落選 | 通過率 |", "|---|---|---|---|"]
    lines += [f"| {r.group} | {r.passed} | {r.failed} | {r.rate:.0%} |" for r in rates]
    return lines


def build_analysis_markdown(
    companies: Sequence[Company],
    steps_by_company: Mapping[int, Sequence[StepView]],
    es_answers: Sequence[EsAnswer] | None = None,
) -> str:
    """選考記録・集計サマリ・依頼文を1つの Markdown にまとめる。"""
    all_steps = [step for steps in steps_by_company.values() for step in steps]
    lines = [PROMPT_HEADER, "---", "", "## 集計サマリ", ""]

    route_rates = analytics.pass_rate_by(all_steps, "route")
    if route_rates:
        lines += [
            "### 応募経路別のステップ通過率",
            "",
            f"（{analytics.PASS_RATE_UNIT}）",
            "",
        ]
        lines += _rate_table(route_rates, "応募経路")
        lines.append("")

    test_rates = analytics.pass_rate_by(all_steps, "test_type")
    if test_rates:
        lines += ["### 適性検査タイプ別のステップ通過率", ""]
        lines += _rate_table(test_rates, "適性検査")
        lines.append("")

    rows = analytics.funnel(all_steps, constants.DEFAULT_STEPS)
    if rows:
        lines += ["### 選考ファネル", ""]
        lines += ["| ステップ | 通過 | 落選 | 選考中 | 辞退 |", "|---|---|---|---|---|"]
        lines += [f"| {r.step} | {r.passed} | {r.failed} | {r.in_progress} | {r.declined} |" for r in rows]
        lines.append("")

    lines += ["## 企業別の選考記録", ""]
    for company in companies:
        steps = list(steps_by_company.get(company.id or -1, []))
        status = analytics.company_status(steps)
        lines.append(
            f"### {company.name}（業界: {company.industry} / 志望度: {company.priority} / "
            f"経路: {company.route} / 適性検査: {company.test_type} / 現況: {status}）"
        )
        for step in steps:
            deadline = f" 締切{step.deadline}" if step.deadline else ""
            memo = f" — {step.memo}" if step.memo else ""
            lines.append(f"- {step.name}:{deadline} 結果: {step.result}{memo}")
        if company.memo:
            lines.append(f"- メモ: {company.memo}")
        lines.append("")

    if es_answers:
        lines += ["## 提出した回答（参考）", ""]
        for answer in es_answers:
            if not answer.answer:
                continue
            company_name = answer.company_name or "汎用"
            lines.append(f"### [{answer.category}] {answer.question}（{company_name}）")
            lines.append(answer.answer)
            lines.append("")

    return "\n".join(lines)
