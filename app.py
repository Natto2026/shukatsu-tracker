"""shukatsu-tracker の Streamlit UI。

起動: streamlit run app.py
データはローカルの data/shukatsu.db にのみ保存される。
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from shukatsu_tracker import ai_export, analytics, constants, db, research

# テストや複数プロファイルの切り替え用に環境変数で上書きできる
DB_PATH = Path(os.environ.get("SHUKATSU_DB", Path(__file__).parent / "data" / "shukatsu.db"))

st.set_page_config(page_title="shukatsu-tracker", layout="wide")


@st.cache_resource
def get_conn(db_path: str):
    Path(db_path).parent.mkdir(exist_ok=True)
    return db.connect(db_path)


conn = get_conn(str(DB_PATH))
page = st.sidebar.radio(
    "メニュー", ["ダッシュボード", "企業管理", "ES管理", "分析", "AI分析"]
)
st.sidebar.caption("データはローカルの data/shukatsu.db にのみ保存されます。")


# --- ダッシュボード ------------------------------------------------------

if page == "ダッシュボード":
    st.title("ダッシュボード")
    companies = db.list_companies(conn)
    all_steps = db.list_steps(conn)
    today = date.today()

    col1, col2, col3, col4 = st.columns(4)
    active = sum(
        1
        for c in companies
        if analytics.company_status(db.list_steps(conn, c["id"])) not in ("落選", "辞退")
    )
    deadlines = analytics.upcoming_deadlines(all_steps, today, within_days=7)
    overdue = [d for d in deadlines if d["overdue"]]
    col1.metric("エントリー企業", f"{len(companies)} 社")
    col2.metric("選考継続中", f"{active} 社")
    col3.metric("7日以内の締切", f"{len(deadlines)} 件")
    col4.metric("期限超過", f"{len(overdue)} 件")

    st.subheader("直近の締切(7日以内)")
    if deadlines:
        for d in deadlines:
            label = f"**{d['company_name']}** — {d['name']}(締切 {d['deadline']})"
            if d["overdue"]:
                st.error(f"{label} : {-d['days_left']}日超過")
            elif d["days_left"] <= 1:
                st.warning(f"{label} : あと{d['days_left']}日")
            else:
                st.info(f"{label} : あと{d['days_left']}日")
    else:
        st.success("7日以内の締切はありません。")

    st.subheader("選考状況一覧")
    if companies:
        rows = []
        for c in companies:
            steps = db.list_steps(conn, c["id"])
            rows.append(
                {
                    "企業名": c["name"],
                    "志望度": c["priority"],
                    "業界": c["industry"],
                    "応募経路": c["route"],
                    "適性検査": c["test_type"],
                    "現在の状況": analytics.company_status(steps),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("まだ企業が登録されていません。「企業管理」から追加してください。")


# --- 企業管理 ------------------------------------------------------------

elif page == "企業管理":
    st.title("企業管理")

    expand_add = not db.list_companies(conn)
    with st.expander("企業を追加", expanded=expand_add), st.form("add_company", clear_on_submit=True):
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
        add_default = st.checkbox("標準の選考ステップ(ES〜最終面接)をまとめて登録する", value=True)
        if st.form_submit_button("追加") and name.strip():
            cid = db.add_company(
                conn, name.strip(), industry=industry, priority=priority, route=route,
                test_type=test_type, mypage_url=mypage_url, login_email=login_email, memo=memo,
            )
            if add_default:
                for i, step in enumerate(constants.DEFAULT_STEPS):
                    db.add_step(conn, cid, step, sort_order=i)
            st.success(f"「{name}」を追加しました。")
            st.rerun()

    companies = db.list_companies(conn)
    if not companies:
        st.stop()

    selected = st.selectbox("企業を選択", companies, format_func=lambda c: f"{c['name']}({c['priority']})")
    steps = db.list_steps(conn, selected["id"])
    st.markdown(f"### {selected['name']} — {analytics.company_status(steps)}")
    if selected["mypage_url"]:
        email = selected["login_email"] or "未設定"
        st.markdown(f"[マイページを開く]({selected['mypage_url']}) (登録メール: {email})")
    if selected["memo"]:
        st.caption(selected["memo"])

    with st.expander("企業研究リンク(公式・事業内容・クチコミ・選考体験記)"):
        links = research.research_links(selected["name"])
        st.markdown(" / ".join(f"[{link['label']}]({link['url']})" for link in links))

    st.markdown("#### 選考ステップ")
    for s in steps:
        c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
        c1.write(f"**{s['name']}**")
        new_deadline = c2.date_input(
            "締切", value=analytics.parse_date(s["deadline"]), key=f"dl{s['id']}",
            format="YYYY-MM-DD", label_visibility="collapsed",
        )
        new_result = c3.selectbox(
            "結果", constants.STEP_RESULTS, index=constants.STEP_RESULTS.index(s["result"]),
            key=f"rs{s['id']}", label_visibility="collapsed",
        )
        if c4.button("削除", key=f"del{s['id']}"):
            db.delete_step(conn, s["id"])
            st.rerun()
        deadline_str = new_deadline.isoformat() if new_deadline else None
        if deadline_str != s["deadline"] or new_result != s["result"]:
            db.update_step(conn, s["id"], deadline=deadline_str, result=new_result)
            st.rerun()

    with st.form("add_step", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        step_name = c1.text_input("ステップを追加(例: 3次面接、リクルーター面談)")
        if c2.form_submit_button("追加") and step_name.strip():
            db.add_step(conn, selected["id"], step_name.strip(), sort_order=len(steps))
            st.rerun()

    with st.expander("企業情報の編集・削除"):
        with st.form("edit_company"):
            c1, c2, c3 = st.columns(3)
            e_priority = c1.selectbox(
                "志望度", constants.PRIORITIES, index=constants.PRIORITIES.index(selected["priority"])
            )
            e_route = c2.selectbox(
                "応募経路", constants.ROUTES, index=constants.ROUTES.index(selected["route"])
            )
            e_test = c3.selectbox(
                "適性検査", constants.TEST_TYPES, index=constants.TEST_TYPES.index(selected["test_type"])
            )
            e_memo = st.text_area("メモ", value=selected["memo"], height=68)
            if st.form_submit_button("更新"):
                db.update_company(
                    conn, selected["id"], priority=e_priority, route=e_route, test_type=e_test, memo=e_memo
                )
                st.rerun()
        if st.button(f"「{selected['name']}」を削除する", type="secondary"):
            db.delete_company(conn, selected["id"])
            st.rerun()


# --- ES管理 --------------------------------------------------------------

elif page == "ES管理":
    st.title("ES設問ライブラリ")
    st.caption("一度書いた回答をカテゴリで整理し、次のESで使い回せるようにします。")

    companies = db.list_companies(conn)
    company_opts = {"(汎用)": None} | {c["name"]: c["id"] for c in companies}

    with st.expander("設問・回答を追加"), st.form("add_es", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 2, 1])
        category = c1.selectbox("カテゴリ", constants.ES_CATEGORIES)
        company_name = c2.selectbox("企業", list(company_opts))
        char_limit = c3.number_input("文字数制限", min_value=0, value=400, step=50)
        question = st.text_input("設問文")
        answer = st.text_area("回答", height=200)
        if st.form_submit_button("保存") and question.strip():
            db.add_es_answer(
                conn, question.strip(), category=category,
                company_id=company_opts[company_name],
                char_limit=char_limit or None, answer=answer,
            )
            st.rerun()

    answers = db.list_es_answers(conn)
    if not answers:
        st.info("まだ回答がありません。")
        st.stop()

    c1, c2 = st.columns(2)
    f_category = c1.multiselect("カテゴリで絞り込み", constants.ES_CATEGORIES)
    f_keyword = c2.text_input("キーワード検索(設問・回答)")
    for a in answers:
        if f_category and a["category"] not in f_category:
            continue
        if f_keyword and f_keyword not in a["question"] + a["answer"]:
            continue
        title = f"[{a['category']}] {a['question'][:40]}({a['company_name'] or '汎用'})"
        with st.expander(title):
            limit = a["char_limit"]
            new_answer = st.text_area("回答", value=a["answer"], height=200, key=f"es{a['id']}")
            count = len(new_answer)
            if limit:
                over = count > limit
                suffix = " ← 超過!" if over else ""
                (st.error if over else st.caption)(f"文字数: {count} / {limit}{suffix}")
            else:
                st.caption(f"文字数: {count}")
            c1, c2 = st.columns([1, 5])
            if c1.button("保存", key=f"save{a['id']}"):
                db.update_es_answer(conn, a["id"], answer=new_answer)
                st.rerun()
            if c2.button("削除", key=f"rm{a['id']}"):
                db.delete_es_answer(conn, a["id"])
                st.rerun()


# --- 分析 ----------------------------------------------------------------

elif page == "分析":
    st.title("選考の振り返り分析")
    st.caption("落選が続くとき、原因は文面だけとは限りません。応募経路や検査種類ごとの通過率から「どこで落ちているか」を可視化します。")

    all_steps = db.list_steps(conn)
    judged = [s for s in all_steps if s["result"] in ("通過", "落選")]
    if not judged:
        st.info("通過/落選の結果が登録されると分析が表示されます。")
        st.stop()

    step_filter = st.selectbox("対象ステップ", ["すべて", *constants.DEFAULT_STEPS])
    target = None if step_filter == "すべて" else step_filter

    def stats_df(key: str, label: str) -> pd.DataFrame:
        stats = analytics.pass_rate_by(all_steps, key, step_name=target)
        return pd.DataFrame(
            [
                {label: k, "通過": v["passed"], "落選": v["failed"], "通過率": f"{v['rate']:.0%}"}
                for k, v in sorted(stats.items(), key=lambda kv: -kv[1]["rate"])
            ]
        )

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("応募経路別の通過率")
        st.dataframe(stats_df("route", "応募経路"), use_container_width=True, hide_index=True)
    with c2:
        st.subheader("適性検査タイプ別の通過率")
        st.dataframe(stats_df("test_type", "適性検査"), use_container_width=True, hide_index=True)

    st.subheader("選考ファネル(どのステップで落ちているか)")
    fun = analytics.funnel(all_steps, constants.DEFAULT_STEPS)
    fun_df = pd.DataFrame(fun).set_index("step")[["通過", "落選", "選考中", "辞退"]]
    # st.bar_chart は軸を辞書順に並べてしまうため、Altair で選考順に固定する
    melted = fun_df.reset_index().melt("step", var_name="結果", value_name="件数")
    chart = (
        alt.Chart(melted)
        .mark_bar()
        .encode(
            y=alt.Y("step", sort=list(fun_df.index), title=None),
            x=alt.X("件数", title="件数"),
            color=alt.Color(
                "結果",
                scale=alt.Scale(
                    domain=["通過", "落選", "選考中", "辞退"],
                    range=["#2e7d32", "#c62828", "#f9a825", "#9e9e9e"],
                ),
            ),
        )
        .properties(height=60 + 40 * len(fun_df))
    )
    st.altair_chart(chart, use_container_width=True)
    st.dataframe(fun_df, use_container_width=True)


# --- AI分析 --------------------------------------------------------------

elif page == "AI分析":
    st.title("AIによる落選理由の分析")
    st.markdown(
        """選考データを **分析依頼プロンプトつきの Markdown** に書き出します。
それを Claude(claude.ai / Claude Code / Claude Desktop)に貼り付ける(またはファイルごと読ませる)と、
落選パターンの分析と改善アドバイスが受けられます。

**このアプリ自体は AI と通信しません。** データを AI に渡すかどうか・どこまで渡すかは、
書き出された内容を確認した上であなたが決められます。

- **含まれる**: 企業名・業界・志望度・応募経路・適性検査・選考ステップ・メモ欄・(チェック時のみ)ES回答
- **含まれない**: マイページURL・ログイン用メールアドレス(認証系情報のため設計上除外)

メモ欄の内容は含まれるため、認証情報などをメモに書いている場合はプレビューで確認してください。"""
    )

    companies = db.list_companies(conn)
    all_steps = db.list_steps(conn)
    if not companies:
        st.info("企業が登録されると書き出せるようになります。")
        st.stop()

    include_es = st.checkbox("提出したESの回答も含める(文面へのフィードバックが欲しい場合)", value=False)
    steps_by_company = {c["id"]: db.list_steps(conn, c["id"]) for c in companies}
    markdown = ai_export.build_analysis_markdown(
        companies, steps_by_company, all_steps,
        es_answers=db.list_es_answers(conn) if include_es else None,
    )

    export_path = DB_PATH.parent / "ai_analysis.md"
    c1, c2 = st.columns(2)
    if c1.button("data/ai_analysis.md に保存(Claude Codeに読ませる用)"):
        export_path.write_text(markdown, encoding="utf-8")
        st.success(
            f"保存しました: {export_path}\nClaude Code で「このファイルを分析して」と依頼してください。"
        )
    c2.download_button("Markdownをダウンロード(claude.aiに貼る用)", markdown, file_name="ai_analysis.md")

    with st.expander("書き出される内容のプレビュー", expanded=True):
        st.code(markdown, language="markdown")
