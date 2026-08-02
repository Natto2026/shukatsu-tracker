"""選考データを AI 分析用の Markdown に書き出す。

API は呼ばない。生成した Markdown をユーザー自身が Claude(claude.ai /
Claude Code など)に渡して分析してもらう方式にすることで、
選考データを外部に送るかどうかの判断をユーザーの手に残す。

マイページ URL・ログイン用メールアドレスは分析に不要な認証系情報のため、
書き出しに含めない(tests/test_research_and_export.py で回帰テスト済み)。
"""

from __future__ import annotations

from . import analytics, constants

PROMPT_HEADER = """\
# 依頼: 就活の選考データ分析

あなたは新卒就活に詳しいキャリアメンターです。以下は私の選考記録です。
このデータを分析して、次の4点を教えてください。

1. **落選のパターン**: どの選考ステップ・応募経路・適性検査タイプで落ちる傾向があるか
2. **考えられる原因の仮説**: データから読み取れる範囲で(推測は推測と明示して)
3. **改善アクション**: 優先順位つきで具体的に(例: 特定の適性検査の対策、応募経路の変更)
4. **今後のエントリー戦略**: 選考中の企業への対応と、追加エントリーすべき企業タイプ

データにない情報(面接の受け答え等)が必要なら、私への質問として挙げてください。
"""


def _format_rate_table(stats: dict[str, dict], label: str) -> list[str]:
    lines = [f"| {label} | 通過 | 落選 | 通過率 |", "|---|---|---|---|"]
    for key, v in sorted(stats.items(), key=lambda kv: -kv[1]["rate"]):
        lines.append(f"| {key} | {v['passed']} | {v['failed']} | {v['rate']:.0%} |")
    return lines


def build_analysis_markdown(
    companies: list[dict],
    steps_by_company: dict[int, list[dict]],
    all_steps: list[dict],
    es_answers: list[dict] | None = None,
) -> str:
    """選考記録+集計サマリ+分析依頼プロンプトを1つの Markdown にまとめる。"""
    lines = [PROMPT_HEADER, "---", "", "## 集計サマリ", ""]

    route_stats = analytics.pass_rate_by(all_steps, "route")
    if route_stats:
        lines += ["### 応募経路別の通過率", ""]
        lines += _format_rate_table(route_stats, "応募経路")
        lines.append("")
    test_stats = analytics.pass_rate_by(all_steps, "test_type")
    if test_stats:
        lines += ["### 適性検査タイプ別の通過率", ""]
        lines += _format_rate_table(test_stats, "適性検査")
        lines.append("")

    fun = analytics.funnel(all_steps, constants.DEFAULT_STEPS)
    if fun:
        lines += ["### 選考ファネル", ""]
        lines += ["| ステップ | 通過 | 落選 | 選考中 | 辞退 |", "|---|---|---|---|---|"]
        for f in fun:
            lines.append(f"| {f['step']} | {f['通過']} | {f['落選']} | {f['選考中']} | {f['辞退']} |")
        lines.append("")

    lines += ["## 企業別の選考記録", ""]
    for c in companies:
        steps = steps_by_company.get(c["id"], [])
        status = analytics.company_status(steps)
        lines.append(
            f"### {c['name']}(業界: {c['industry']} / 志望度: {c['priority']} / "
            f"経路: {c['route']} / 適性検査: {c['test_type']} / 現況: {status})"
        )
        for s in steps:
            deadline = f" 締切{s['deadline']}" if s.get("deadline") else ""
            memo = f" — {s['memo']}" if s.get("memo") else ""
            lines.append(f"- {s['name']}:{deadline} 結果: {s['result']}{memo}")
        if c.get("memo"):
            lines.append(f"- メモ: {c['memo']}")
        lines.append("")

    if es_answers:
        lines += ["## 提出した ES の回答(参考)", ""]
        for a in es_answers:
            if not a.get("answer"):
                continue
            company = a.get("company_name") or "汎用"
            lines.append(f"### [{a['category']}] {a['question']}({company})")
            lines.append(a["answer"])
            lines.append("")

    return "\n".join(lines)
