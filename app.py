"""shukatsu-tracker の Streamlit UI。

起動: streamlit run app.py
保存先は既定でローカルの data/shukatsu.db。SHUKATSU_DB で切り替えられる。

この層は「入力を受け取り、サービスを呼び、結果を並べる」ことだけを行う。
集計も SQL もここには書かない（shukatsu_tracker/services 以下にある）。

書き込みは、利用者が明示的にボタンを押したときだけ行う。画面を開いた
だけで保存が走る作りにすると、別のタブや別の端末で更新した内容を、
古い表示のまま上書きしてしまうため。
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from shukatsu_tracker import ai_export, analytics, constants, db, research
from shukatsu_tracker.db import DatabaseError, DuplicateKeyError
from shukatsu_tracker.models import Company, EsAnswer
from shukatsu_tracker.review import ReviewError
from shukatsu_tracker.review.providers import available_providers
from shukatsu_tracker.services import EsService, ReviewService, SelectionService

DEFAULT_DB = Path(__file__).parent / "data" / "shukatsu.db"
DB_TARGET = os.environ.get("SHUKATSU_DB", str(DEFAULT_DB))

st.set_page_config(page_title="shukatsu-tracker", layout="wide")

_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~])")


def as_text(value: str | None) -> str:
    """利用者が入れた文字列を、Markdown として解釈されない形にする。"""
    return "" if not value else _MARKDOWN_SPECIALS.sub(r"\\\1", value)


def get_db():
    """接続をブラウザのセッションごとに1つ持つ。

    全セッションで1つの接続を共有すると、あるセッションの失敗が別の
    セッションの確定済みの書き込みを巻き戻すため。
    """
    if "db" not in st.session_state:
        st.session_state.db = db.connect(DB_TARGET)
    return st.session_state.db


def flash(message: str, kind: str = "success") -> None:
    """再描画をまたいで残る通知を積む。"""
    st.session_state.setdefault("flash", []).append((kind, message))


def show_flash() -> None:
    for kind, message in st.session_state.pop("flash", []):
        getattr(st, kind)(message)


def run_write(action, success: str | None = None) -> bool:
    """書き込みを実行し、失敗したら利用者に伝わる文面にして返す。"""
    try:
        action()
    except DuplicateKeyError:
        st.error("同じ名前がすでに登録されています。別の名前にしてください。")
    except DatabaseError as error:
        st.error(f"保存できませんでした: {error}")
    except ValueError as error:
        st.error(str(error))
    else:
        if success:
            flash(success)
        return True
    return False


try:
    database = get_db()
except DatabaseError as error:
    st.error(f"データベースに接続できませんでした: {error}")
    st.stop()

selection = SelectionService(database)
es = EsService(database)
reviewer = ReviewService(database)

page = st.sidebar.radio("メニュー", ["ダッシュボード", "企業管理", "ES管理", "添削", "分析", "書き出し"])
st.sidebar.caption(f"保存先: {db.describe(DB_TARGET, base=Path(__file__).parent)}")
show_flash()


# --- ダッシュボード ------------------------------------------------------

if page == "ダッシュボード":
    st.title("ダッシュボード")
    summary = selection.dashboard(date.today())

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("エントリー企業", f"{summary.total_companies} 社")
    col2.metric("選考継続中", f"{summary.active_companies} 社")
    col3.metric("7日以内の締切", f"{len(summary.upcoming)} 件")
    col4.metric("期限超過", f"{len(summary.overdue)} 件")

    if summary.overdue:
        st.subheader("期限超過")
        for deadline in summary.overdue:
            step = deadline.step
            st.error(
                f"**{as_text(step.company_name)}** — {as_text(step.name)}"
                f"（締切 {step.deadline}）: {-deadline.days_left}日超過"
            )

    st.subheader("直近の締切（7日以内）")
    if summary.upcoming:
        for deadline in summary.upcoming:
            step = deadline.step
            label = (
                f"**{as_text(step.company_name)}** — {as_text(step.name)}"
                f"（締切 {step.deadline}）: あと{deadline.days_left}日"
            )
            (st.warning if deadline.days_left <= 1 else st.info)(label)
    else:
        st.success("7日以内の締切はありません。")

    st.subheader("選考状況一覧")
    if summary.companies:
        rows = [
            {
                "企業名": company.name,
                "志望度": company.priority,
                "業界": company.industry,
                "応募経路": company.route,
                "適性検査": company.test_type,
                "現在の状況": analytics.company_status(summary.steps_by_company.get(company.id or -1, [])),
            }
            for company in summary.companies
        ]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.caption("まだ企業が登録されていません。「企業管理」から追加してください。")


# --- 企業管理 ------------------------------------------------------------

elif page == "企業管理":
    st.title("企業管理")
    companies = selection.companies()

    with st.expander("企業を追加", expanded=not companies), st.form("add_company", clear_on_submit=True):
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
        if st.form_submit_button("追加"):
            if not name.strip():
                st.error("企業名を入力してください。")
            elif run_write(
                lambda: selection.add_company(
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
                ),
                success=f"「{as_text(name.strip())}」を追加しました。",
            ):
                st.rerun()

    if not companies:
        st.stop()

    selected = st.selectbox("企業を選択", companies, format_func=lambda c: f"{c.name}（{c.priority}）")
    company_id = selected.id or -1
    # 一覧と状況の両方をこの1回の問い合わせで賄う
    steps = selection.steps_by_company().get(company_id, [])
    status = analytics.company_status(steps)
    st.markdown(f"### {as_text(selected.name)} — {status}")

    if selected.mypage_url:
        if selected.mypage_url.startswith(("http://", "https://")):
            st.link_button("マイページを開く", selected.mypage_url)
        else:
            st.caption(f"マイページURL: {as_text(selected.mypage_url)}")
        st.caption(f"登録メール: {as_text(selected.login_email) or '未設定'}")
    if selected.memo:
        st.caption(as_text(selected.memo))

    with st.expander("企業研究リンク（公式・新卒採用・事業内容・IR・クチコミ・選考体験記・ニュース）"):
        links = research.research_links(selected.name)
        st.markdown(" / ".join(f"[{link.label}]({link.url})" for link in links))

    st.markdown("#### 選考ステップ")
    st.caption("変更したら「選考ステップを保存」を押してください。押すまで保存されません。")

    unreadable = [s for s in steps if s.deadline and analytics.parse_date(s.deadline) is None]
    if unreadable:
        names = "、".join(as_text(s.name) for s in unreadable)
        st.warning(
            f"締切の書式を読み取れないステップがあります（{names}）。"
            "日付を選び直すまで、その値はそのまま保存されます。"
        )

    # 前回この画面に出した値を控えておく。保存を押した再実行では入力欄が最新の値で
    # 作り直される（下の key）ため、古い表示への入力は届かない。届かなかったことを
    # 利用者に知らせるには、入力欄とは別に「何を見せていたか」を持つ必要がある。
    previously_shown: dict[int, tuple[str | None, str]] = st.session_state.get("steps_shown", {})
    now_shown = {s.id or -1: (s.deadline, s.result) for s in steps}
    st.session_state["steps_shown"] = now_shown

    with st.form("edit_steps"):
        edited: list[tuple[int, date | None, str, str | None, str]] = []
        for step in steps:
            rendered_deadline = analytics.parse_date(step.deadline)
            # DB の値をキーに含める。別のタブや端末で更新されたとき、
            # 古い入力欄の値が残って上書きするのを防ぐため。
            token = f"{step.id}:{step.deadline}:{step.result}"
            c1, c2, c3 = st.columns([3, 2, 2])
            c1.write(f"**{as_text(step.name)}**")
            new_deadline = c2.date_input(
                "締切",
                value=rendered_deadline,
                key=f"dl:{token}",
                format="YYYY-MM-DD",
                label_visibility="collapsed",
            )
            new_result = c3.selectbox(
                "結果",
                constants.STEP_RESULTS,
                index=constants.STEP_RESULTS.index(step.result),
                key=f"rs:{token}",
                label_visibility="collapsed",
            )
            edited.append((step.id or -1, new_deadline, new_result, step.deadline, step.result))

        if st.form_submit_button("選考ステップを保存", type="primary"):
            # 押した時点の表示と、いま読み直した値を突き合わせる。ずれている行は
            # 入力欄が作り直されており、下の edited には最新の値しか入っていない
            # （＝新しい値を上書きすることはない）。消えた行も同じ扱いにする。
            outdated = sum(
                1 for step_id, shown in previously_shown.items() if now_shown.get(step_id) != shown
            )
            changed = 0
            for step_id, new_deadline, new_result, old_deadline, old_result in edited:
                fields: dict[str, object] = {}
                rendered = analytics.parse_date(old_deadline)
                # 表示していた値と違うものだけを書く。読めない締切に
                # 触っていない場合は、空欄に見えていても書き換えない。
                if new_deadline != rendered:
                    fields["deadline"] = new_deadline.isoformat() if new_deadline else None
                if new_result != old_result:
                    fields["result"] = new_result
                if fields:
                    selection.update_step(step_id, **fields)  # type: ignore[arg-type]
                    changed += 1
            if outdated:
                flash(
                    f"{outdated} 件は表示後に他の場所で更新されたため、その行への入力は反映していません。"
                    "最新の値を表示しています。",
                    "warning",
                )
            if changed:
                flash(f"{changed} 件を保存しました。", "info")
            elif not outdated:
                flash("変更はありませんでした。", "info")
            st.rerun()

    with st.form("add_step", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        step_name = c1.text_input("ステップを追加（例: 3次面接、リクルーター面談）")
        if c2.form_submit_button("追加"):
            if not step_name.strip():
                st.error("ステップ名を入力してください。")
            elif run_write(
                lambda: selection.add_step(company_id, step_name),
                success=f"「{as_text(step_name.strip())}」を追加しました。",
            ):
                st.rerun()

    with st.expander("不要なステップを削除"):
        for step in steps:
            c1, c2 = st.columns([4, 1])
            c1.write(as_text(step.name))
            if c2.button("削除", key=f"delstep{step.id}") and run_write(
                lambda sid=step.id: selection.delete_step(sid or -1),
                success="ステップを削除しました。",
            ):
                st.rerun()

    with st.expander("企業情報の編集"), st.form("edit_company"):
        e_name = st.text_input("企業名", value=selected.name)
        c1, c2, c3 = st.columns(3)
        e_industry = c1.selectbox(
            "業界", constants.INDUSTRIES, index=constants.INDUSTRIES.index(selected.industry)
        )
        e_priority = c2.selectbox(
            "志望度", constants.PRIORITIES, index=constants.PRIORITIES.index(selected.priority)
        )
        e_route = c3.selectbox("応募経路", constants.ROUTES, index=constants.ROUTES.index(selected.route))
        c4, c5 = st.columns(2)
        e_test = c4.selectbox(
            "適性検査",
            constants.TEST_TYPES,
            index=constants.TEST_TYPES.index(selected.test_type),
        )
        e_email = c5.text_input("マイページ登録メール", value=selected.login_email)
        e_url = st.text_input("マイページURL", value=selected.mypage_url)
        e_memo = st.text_area("メモ", value=selected.memo, height=68)
        if st.form_submit_button("更新"):
            if not e_name.strip():
                st.error("企業名を入力してください。")
            elif run_write(
                lambda: selection.update_company(
                    company_id,
                    name=e_name.strip(),
                    industry=e_industry,
                    priority=e_priority,
                    route=e_route,
                    test_type=e_test,
                    login_email=e_email,
                    mypage_url=e_url,
                    memo=e_memo,
                ),
                success="企業情報を更新しました。",
            ):
                st.rerun()

    with st.expander("企業を削除"):
        st.warning(
            "削除すると、この企業の選考ステップもすべて消えます。"
            "保存した回答は残りますが、企業との結びつきは失われます。元に戻せません。"
        )
        confirmed = st.checkbox(
            f"「{as_text(selected.name)}」を削除することを理解しました", key=f"confirm_del{company_id}"
        )
        if st.button("削除する", disabled=not confirmed, type="secondary") and run_write(
            lambda: selection.delete_company(company_id),
            success=f"「{as_text(selected.name)}」を削除しました。",
        ):
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
        if st.form_submit_button("保存"):
            if not question.strip():
                st.error("設問文を入力してください。")
            elif run_write(
                lambda: es.add(
                    EsAnswer(
                        question=question,
                        category=category,
                        company_id=company_options[company_name],
                        char_limit=int(char_limit) or None,
                        answer=answer_text,
                    )
                ),
                success="保存しました。",
            ):
                st.rerun()

    all_answers = es.answers()
    if not all_answers:
        st.info("まだ回答がありません。")
        st.stop()

    c1, c2 = st.columns(2)
    filter_categories = c1.multiselect("カテゴリで絞り込み", constants.ES_CATEGORIES)
    keyword = c2.text_input("キーワード検索（設問・回答）")
    shown = es.search(categories=filter_categories, keyword=keyword)
    st.caption(f"{len(shown)} / {len(all_answers)} 件")

    for answer in shown:
        # expander の見出しは Markdown として描画される
        title = (
            f"[{answer.category}] {as_text(answer.question[:40])}（{as_text(answer.company_name) or '汎用'}）"
        )
        with st.expander(title):
            new_text = st.text_area(
                "回答",
                value=answer.answer,
                height=200,
                key=f"es:{answer.id}:{answer.updated_at}",
            )
            check = es.length_check(new_text, answer.char_limit)
            if check.limit is None:
                st.caption(f"文字数: {check.length}")
            elif check.over:
                st.error(f"文字数: {check.length} / {check.limit} ← 超過")
            elif check.short:
                st.warning(f"文字数: {check.length} / {check.limit}（8割未満）")
            else:
                st.caption(f"文字数: {check.length} / {check.limit}")

            if st.button("保存", key=f"save{answer.id}"):
                if new_text == answer.answer:
                    st.info("変更はありませんでした。")
                elif run_write(
                    lambda aid=answer.id, text=new_text: es.update_text(aid or -1, text),
                    success="回答を保存しました。",
                ):
                    st.rerun()

            with st.popover("削除"):
                st.warning("この回答と、ひもづく所見の履歴もすべて消えます。元に戻せません。")
                if st.button("削除する", key=f"rm{answer.id}", type="secondary") and run_write(
                    lambda aid=answer.id: es.delete(aid or -1),
                    success="回答を削除しました。",
                ):
                    st.rerun()


# --- 添削 ----------------------------------------------------------------

elif page == "添削":
    st.title("添削")
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
    industry_index = industry_options.index(default_industry) if default_industry in industry_options else 0
    c1, c2 = st.columns(2)
    industry = c1.selectbox(
        "観点を寄せる業界",
        industry_options,
        index=industry_index,
        help="業界ごとに、読み手が気にしやすい観点を上乗せします。",
    )
    providers = available_providers()
    provider_names = [p.name for p in providers]
    provider_name = c2.selectbox(
        "実行先",
        provider_names,
        help=(
            "既定は書き出しのみで、外部とは通信しません。"
            "Claude API は ANTHROPIC_API_KEY が設定されている場合だけ選べます。"
        ),
    )
    provider = providers[provider_names.index(provider_name)]
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
                [{"観点": c.title, "重み": c.emphasis_label, "見るところ": c.check} for c in criteria]
            ),
            width="stretch",
            hide_index=True,
        )

    built_prompt = reviewer.build_prompt(target, industry=resolved_industry, note=note)
    with st.expander("送られる文面（実行前に確認できます）"):
        st.code(built_prompt, language="markdown")

    if provider.sends_data_externally:
        st.warning(
            "この実行先を選ぶと、上の文面が外部に送信されます。含めたくない記述がないか確認してください。"
        )

    if st.button("所見を取る", type="primary"):
        try:
            with st.spinner("所見を作成しています。しばらくお待ちください。"):
                review = reviewer.run(target, provider=provider, industry=resolved_industry, note=note)
        except ReviewError as error:
            st.error(str(error))
        except DatabaseError as error:
            st.error(f"保存できませんでした: {error}")
        else:
            flash(f"所見を保存しました（実行先: {review.provider}）")
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
                if c1.button("削除", key=f"rm_review_{review.id}") and run_write(
                    lambda rid=review.id: reviewer.delete(rid or -1),
                    success="所見を削除しました。",
                ):
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
    target_step = None if step_filter == "すべて" else step_filter

    def rate_frame(attribute: str, label: str) -> pd.DataFrame:
        rates = analytics.pass_rate_by(all_steps, attribute, step_name=target_step)
        return pd.DataFrame(
            [{label: r.group, "通過": r.passed, "落選": r.failed, "通過率": f"{r.rate:.0%}"} for r in rates]
        )

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("応募経路別の通過率")
        st.dataframe(rate_frame("route", "応募経路"), width="stretch", hide_index=True)
    with c2:
        st.subheader("適性検査タイプ別の通過率")
        st.dataframe(rate_frame("test_type", "適性検査"), width="stretch", hide_index=True)

    st.subheader("選考ファネル")
    funnel_rows = analytics.funnel(all_steps, constants.DEFAULT_STEPS)
    frame = pd.DataFrame(
        [
            {
                "選考ステップ": r.step,
                "通過": r.passed,
                "落選": r.failed,
                "選考中": r.in_progress,
                "辞退": r.declined,
            }
            for r in funnel_rows
        ]
    ).set_index("選考ステップ")
    # st.bar_chart は軸を辞書順に並べてしまうため、Altair で選考順に固定する
    melted = frame.reset_index().melt("選考ステップ", var_name="結果", value_name="件数")
    chart = (
        alt.Chart(melted)
        .mark_bar()
        .encode(
            # labelLimit=0 で上限を外す。既定の幅では長いステップ名が「…」で切れる
            y=alt.Y("選考ステップ", sort=list(frame.index), title=None, axis=alt.Axis(labelLimit=0)),
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

**この画面は外部と通信しません。** 書き出したファイルを誰にどこまで渡すかは、
内容を確認したうえで利用者が決められます。

- **含まれる**: 企業名・業界・志望度・応募経路・適性検査・選考ステップ・メモ欄・（選択時のみ）回答本文
- **含まれない**: マイページURL・ログイン用メールアドレス（認証系情報のため設計上除外）

メモ欄の内容は含まれます。認証情報をメモに書いている場合はプレビューで確認してください。"""
    )

    summary = selection.dashboard(date.today())
    if not summary.companies:
        st.info("企業が登録されると書き出せるようになります。")
        st.stop()

    include_answers = st.checkbox("回答本文も含める", value=False)
    markdown = ai_export.build_analysis_markdown(
        summary.companies,
        summary.steps_by_company,
        es_answers=es.answers() if include_answers else None,
    )

    st.download_button("Markdown をダウンロード", markdown, file_name="ai_analysis.md")
    with st.expander("書き出される内容のプレビュー", expanded=True):
        st.code(markdown, language="markdown")
