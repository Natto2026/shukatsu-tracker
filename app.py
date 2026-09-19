"""shukatsu-tracker の Streamlit UI。

起動: streamlit run app.py
データはローカルの data/shukatsu.db にのみ保存される。

この層は「入力を受け取り、サービスを呼び、結果を並べる」ことだけを行う。
集計も SQL もここには書かない（shukatsu_tracker/services 以下にある）。
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from shukatsu_tracker import ai_export, analytics, constants, db, research
from shukatsu_tracker.models import Company, EsAnswer
from shukatsu_tracker.review import ReviewError
from shukatsu_tracker.review.providers import available_providers
from shukatsu_tracker.services import EsService, ReviewService, SelectionService

# テストや複数プロファイルの切り替え用に環境変数で上書きできる
DB_PATH = Path(os.environ.get("SHUKATSU_DB", Path(__file__).parent / "data" / "shukatsu.db"))

st.set_page_config(page_title="shukatsu-tracker", layout="wide")


@st.cache_resource
def get_conn(db_path: str):
    return db.connect(db_path)


conn = get_conn(str(DB_PATH))
selection = SelectionService(conn)
es = EsService(conn)
reviewer = ReviewService(conn)

page = st.sidebar.radio(
    "メニュー", ["ダッシュボード", "企業管理", "ES管理", "添削", "分析", "書き出し"]
)
st.sidebar.caption("データはローカルの data/shukatsu.db にのみ保存されます。")


# --- ダッシュボード ------------------------------------------------------

if page == "ダッシュボード":
    st.title("ダッシュボード")
    summary = selection.dashboard(date.today())
    companies = selection.companies()
    grouped = selection.steps_by_company()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("エントリー企業", f"{summary.total_companies} 社")
    col2.metric("選考継続中", f"{summary.active_companies} 社")
    col3.metric("7日以内の締切", f"{len(summary.deadlines)} 件")
    col4.metric("期限超過", f"{len(summary.overdue)} 件")

    st.subheader("直近の締切（7日以内）")
    if summary.deadlines:
        for deadline in summary.deadlines:
            step = deadline.step
            label = f"**{step.company_name}** — {step.name}（締切 {step.deadline}）"
            if deadline.overdue:
                st.error(f"{label} : {-deadline.days_left}日超過")
            elif deadline.days_left <= 1:
                st.warning(f"{label} : あと{deadline.days_left}日")
            else:
                st.info(f"{label} : あと{deadline.days_left}日")
    else:
        st.success("7日以内の締切はありません。")

    st.subheader("選考状況一覧")
    if companies:
        rows = [
            {
                "企業名": company.name,
                "志望度": company.priority,
                "業界": company.industry,
                "応募経路": company.route,
                "適性検査": company.test_type,
                "現在の状況": analytics.company_status(grouped.get(company.id or -1, [])),
            }
            for company in companies
        ]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.caption("まだ企業が登録されていません。「企業管理」から追加してください。")


# --- 企業管理 ------------------------------------------------------------

elif page == "企業管理":
    st.title("企業管理")
    companies = selection.companies()

    with st.expander("企業を追加", expanded=not companies), st.form(
        "add_company", clear_on_submit=True
    ):
        name = st.text_input("企業名 *")
        c1, c2, c3 = st.columns(3)
        industry = c1.selectbox("業界", constants.INDUSTRIES)
        priority = c2.selectbox("志望度", constants.PRIORITIES, index=1)
        route = c3.selectbox("応募経路", constants.ROUTES)
        c4, c5 = st.columns(2)
        test_type = c4.selectbox("適性検査", constants.TEST_TYPES, index=len(constants.TEST_TYPES) - 1)
        login_email = c5.text_input("マイページ登録メール")
        mypage_url = st.text_input("マイページURL")
        memo = st.text_area("メモ", height=68)
        add_default = st.checkbox("標準の選考ステップ（ES〜最終面接）をまとめて登録する", value=True)
        if st.form_submit_button("追加") and name.strip():
            selection.add_company(
                Company(
                    name=name,
                    industry=industry,
                    priority=priority,
                    route=route,
                    test_type=test_type,
                    mypage_url=mypage_url,
                    login_email=login_email,
                    memo=memo,
                ),
                with_default_steps=add_default,
            )
            st.success(f"「{name}」を追加しました。")
            st.rerun()

    if not companies:
        st.stop()

    selected = st.selectbox(
        "企業を選択", companies, format_func=lambda c: f"{c.name}（{c.priority}）"
    )
    company_id = selected.id or -1
    steps = selection.steps_of(company_id)
    status = analytics.company_status(selection.steps_by_company().get(company_id, []))
    st.markdown(f"### {selected.name} — {status}")
    if selected.mypage_url:
        email = selected.login_email or "未設定"
        st.markdown(f"[マイページを開く]({selected.mypage_url}) （登録メール: {email}）")
    if selected.memo:
        st.caption(selected.memo)

    with st.expander("企業研究リンク（公式・事業内容・クチコミ・選考体験記）"):
        links = research.research_links(selected.name)
        st.markdown(" / ".join(f"[{link['label']}]({link['url']})" for link in links))

    st.markdown("#### 選考ステップ")
    for step in steps:
        c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
        c1.write(f"**{step.name}**")
        new_deadline = c2.date_input(
            "締切",
            value=analytics.parse_date(step.deadline),
            key=f"dl{step.id}",
            format="YYYY-MM-DD",
            label_visibility="collapsed",
        )
        new_result = c3.selectbox(
            "結果",
            constants.STEP_RESULTS,
            index=constants.STEP_RESULTS.index(step.result),
            key=f"rs{step.id}",
            label_visibility="collapsed",
        )
        if c4.button("削除", key=f"del{step.id}"):
            selection.delete_step(step.id or -1)
            st.rerun()
        deadline_text = new_deadline.isoformat() if new_deadline else None
        if deadline_text != step.deadline or new_result != step.result:
            selection.update_step(step.id or -1, deadline=deadline_text, result=new_result)
            st.rerun()

    with st.form("add_step", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        step_name = c1.text_input("ステップを追加（例: 3次面接、リクルーター面談）")
        if c2.form_submit_button("追加") and step_name.strip():
            selection.add_step(company_id, step_name)
            st.rerun()

    with st.expander("企業情報の編集・削除"):
        with st.form("edit_company"):
            c1, c2, c3 = st.columns(3)
            e_priority = c1.selectbox(
                "志望度", constants.PRIORITIES, index=constants.PRIORITIES.index(selected.priority)
            )
            e_route = c2.selectbox(
                "応募経路", constants.ROUTES, index=constants.ROUTES.index(selected.route)
            )
            e_test = c3.selectbox(
                "適性検査", constants.TEST_TYPES, index=constants.TEST_TYPES.index(selected.test_type)
            )
            e_memo = st.text_area("メモ", value=selected.memo, height=68)
            if st.form_submit_button("更新"):
                selection.update_company(
                    company_id, priority=e_priority, route=e_route, test_type=e_test, memo=e_memo
                )
                st.rerun()
        if st.button(f"「{selected.name}」を削除する", type="secondary"):
            selection.delete_company(company_id)
            st.rerun()


# --- ES管理 --------------------------------------------------------------

elif page == "ES管理":
    st.title("設問と回答のライブラリ")
    st.caption("一度書いた回答をカテゴリで整理し、文字数制限と照らして管理します。")

    companies = selection.companies()
    company_options: dict[str, int | None] = {"（汎用）": None}
    company_options.update({c.name: c.id for c in companies})

    with st.expander("設問・回答を追加"), st.form("add_es", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 2, 1])
        category = c1.selectbox("カテゴリ", constants.ES_CATEGORIES)
        company_name = c2.selectbox("企業", list(company_options))
        char_limit = c3.number_input("文字数制限", min_value=0, value=400, step=50)
        question = st.text_input("設問文")
        answer_text = st.text_area("回答", height=200)
        if st.form_submit_button("保存") and question.strip():
            es.add(
                EsAnswer(
                    question=question,
                    category=category,
                    company_id=company_options[company_name],
                    char_limit=int(char_limit) or None,
                    answer=answer_text,
                )
            )
            st.rerun()

    if not es.answers():
        st.info("まだ回答がありません。")
        st.stop()

    c1, c2 = st.columns(2)
    filter_categories = c1.multiselect("カテゴリで絞り込み", constants.ES_CATEGORIES)
    keyword = c2.text_input("キーワード検索（設問・回答）")

    for answer in es.search(categories=filter_categories, keyword=keyword):
        title = (
            f"[{answer.category}] {answer.question[:40]}"
            f"（{answer.company_name or '汎用'}）"
        )
        with st.expander(title):
            new_text = st.text_area("回答", value=answer.answer, height=200, key=f"es{answer.id}")
            check = es.length_check(new_text, answer.char_limit)
            if check.limit is None:
                st.caption(f"文字数: {check.length}")
            elif check.over:
                st.error(f"文字数: {check.length} / {check.limit} ← 超過")
            elif check.short:
                st.warning(f"文字数: {check.length} / {check.limit}（8割未満）")
            else:
                st.caption(f"文字数: {check.length} / {check.limit}")
            c1, c2 = st.columns([1, 5])
            if c1.button("保存", key=f"save{answer.id}"):
                es.update_text(answer.id or -1, new_text)
                st.rerun()
            if c2.button("削除", key=f"rm{answer.id}"):
                es.delete(answer.id or -1)
                st.rerun()


# --- 添削 ----------------------------------------------------------------

elif page == "添削":
    st.title("回答への所見")
    st.caption(
        "保存した回答を、書き方の観点から点検します。観点は"
        " shukatsu_tracker/review/criteria/ の TOML で定義していて、自由に足せます。"
    )

    answers = [a for a in es.answers() if a.answer.strip()]
    if not answers:
        st.info("本文の入った回答が保存されると点検できます。")
        st.stop()

    target = st.selectbox(
        "対象の回答",
        answers,
        format_func=lambda a: f"[{a.category}] {a.question[:40]}（{a.company_name or '汎用'}）",
    )

    default_industry = reviewer.industry_of(target)
    industry_options = ["指定なし", *constants.INDUSTRIES]
    industry_index = (
        industry_options.index(default_industry)
        if default_industry in industry_options
        else 0
    )
    c1, c2 = st.columns(2)
    industry = c1.selectbox(
        "観点を寄せる業界",
        industry_options,
        index=industry_index,
        help="業界ごとに、読み手が気にしやすい観点を上乗せします。",
    )
    providers = available_providers()
    provider = c2.selectbox(
        "実行先",
        providers,
        format_func=lambda p: p.name,
        help=(
            "既定は書き出しのみで、外部とは通信しません。"
            "Claude API は ANTHROPIC_API_KEY が設定されている場合だけ選べます。"
        ),
    )
    note = st.text_input("補足（任意）", placeholder="例: 文字数を削る方向で見てほしい")

    resolved_industry = None if industry == "指定なし" else industry
    criteria = reviewer.criteria_for(resolved_industry)
    check = es.length_check(target.answer, target.char_limit)

    m1, m2, m3 = st.columns(3)
    m1.metric("文字数", f"{check.length}" + (f" / {check.limit}" if check.limit else ""))
    m2.metric("観点の数", f"{len(criteria)} 件")
    m3.metric("特に重視", f"{sum(1 for c in criteria if c.weight >= 3)} 件")

    with st.expander(f"適用される観点（{criteria.industry}）"):
        st.caption(criteria.reader)
        st.dataframe(
            pd.DataFrame(
                [
                    {"観点": c.title, "重み": c.emphasis_label, "見るところ": c.check}
                    for c in criteria
                ]
            ),
            width="stretch",
            hide_index=True,
        )

    built_prompt = reviewer.build_prompt(target, industry=resolved_industry, note=note)
    with st.expander("送られる文面（実行前に確認できます）"):
        st.code(built_prompt, language="markdown")

    if provider.sends_data_externally:
        st.warning(
            "この実行先を選ぶと、上の文面が外部に送信されます。"
            "含めたくない記述がないか確認してください。"
        )

    if st.button("所見を取る", type="primary"):
        try:
            review = reviewer.run(
                target, provider=provider, industry=resolved_industry, note=note
            )
        except ReviewError as error:
            st.error(str(error))
        else:
            st.success(f"所見を保存しました（実行先: {review.provider}）")
            st.rerun()

    history = reviewer.history(target.id or -1)
    if history:
        st.subheader("履歴")
        for review in history:
            label = f"{review.created_at}  {review.provider}"
            if review.model:
                label += f"（{review.model}）"
            if not review.applies_to(target.answer):
                label += "  ※この所見のあとに本文が変わっています"
            with st.expander(label):
                st.markdown(review.result)
                c1, c2 = st.columns([1, 5])
                c2.download_button(
                    "この所見を保存",
                    review.result,
                    file_name=f"review_{review.id}.md",
                    key=f"dl_review_{review.id}",
                )
                if c1.button("削除", key=f"rm_review_{review.id}"):
                    reviewer.delete(review.id or -1)
                    st.rerun()


# --- 分析 ----------------------------------------------------------------

elif page == "分析":
    st.title("選考の振り返り")

    all_steps = selection.all_step_views()
    judged = [s for s in all_steps if s.result in (analytics.PASSED, analytics.FAILED)]
    if not judged:
        st.info("通過・落選の結果が登録されると集計が表示されます。")
        st.stop()

    step_filter = st.selectbox("対象ステップ", ["すべて", *constants.DEFAULT_STEPS])
    target = None if step_filter == "すべて" else step_filter

    def rate_frame(attribute: str, label: str) -> pd.DataFrame:
        rates = selection.pass_rates(attribute, step_name=target)
        return pd.DataFrame(
            [
                {label: r.group, "通過": r.passed, "落選": r.failed, "通過率": f"{r.rate:.0%}"}
                for r in rates
            ]
        )

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("応募経路別の通過率")
        st.dataframe(rate_frame("route", "応募経路"), width="stretch", hide_index=True)
    with c2:
        st.subheader("適性検査タイプ別の通過率")
        st.dataframe(rate_frame("test_type", "適性検査"), width="stretch", hide_index=True)

    st.subheader("選考ファネル")
    rows = selection.funnel()
    frame = pd.DataFrame(
        [
            {
                "step": r.step,
                "通過": r.passed,
                "落選": r.failed,
                "選考中": r.in_progress,
                "辞退": r.declined,
            }
            for r in rows
        ]
    ).set_index("step")
    # st.bar_chart は軸を辞書順に並べてしまうため、Altair で選考順に固定する
    melted = frame.reset_index().melt("step", var_name="結果", value_name="件数")
    chart = (
        alt.Chart(melted)
        .mark_bar()
        .encode(
            y=alt.Y("step", sort=list(frame.index), title=None),
            x=alt.X("件数", title="件数", axis=alt.Axis(tickMinStep=1, format="d")),
            color=alt.Color(
                "結果",
                scale=alt.Scale(
                    domain=["通過", "落選", "選考中", "辞退"],
                    range=["#2e7d32", "#c62828", "#f9a825", "#9e9e9e"],
                ),
            ),
        )
        .properties(height=60 + 40 * len(frame))
    )
    st.altair_chart(chart, width="stretch")
    st.dataframe(frame, width="stretch")


# --- 書き出し ------------------------------------------------------------

elif page == "書き出し":
    st.title("分析用データの書き出し")
    st.markdown(
        """選考記録と集計を、依頼文つきの Markdown にまとめて書き出します。

**このアプリは外部と通信しません。** 書き出したファイルを誰にどこまで渡すかは、
内容を確認したうえで利用者が決められます。

- **含まれる**: 企業名・業界・志望度・応募経路・適性検査・選考ステップ・メモ欄・（選択時のみ）回答本文
- **含まれない**: マイページURL・ログイン用メールアドレス（認証系情報のため設計上除外）

メモ欄の内容は含まれます。認証情報をメモに書いている場合はプレビューで確認してください。"""
    )

    companies = selection.companies()
    if not companies:
        st.info("企業が登録されると書き出せるようになります。")
        st.stop()

    include_answers = st.checkbox("回答本文も含める", value=False)
    markdown = ai_export.build_analysis_markdown(
        companies,
        selection.steps_by_company(),
        es_answers=es.answers() if include_answers else None,
    )

    export_path = DB_PATH.parent / "ai_analysis.md"
    c1, c2 = st.columns(2)
    if c1.button(f"{export_path.name} に保存"):
        export_path.write_text(markdown, encoding="utf-8")
        st.success(f"保存しました: {export_path}")
    c2.download_button("Markdown をダウンロード", markdown, file_name="ai_analysis.md")

    with st.expander("書き出される内容のプレビュー", expanded=True):
        st.code(markdown, language="markdown")
