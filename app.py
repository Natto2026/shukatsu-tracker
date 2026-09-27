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

import hashlib
import os
import re
from contextlib import suppress
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from shukatsu_tracker import ai_export, analytics, constants, db, research
from shukatsu_tracker.db import ConnectionLostError, DatabaseError, DuplicateKeyError
from shukatsu_tracker.models import Company, EsAnswer
from shukatsu_tracker.review import ReviewError
from shukatsu_tracker.review.providers import available_providers
from shukatsu_tracker.services import (
    NO_INDUSTRY,
    UNSET,
    CsvFormatError,
    CsvImportService,
    EsService,
    ReviewService,
    SelectionService,
    StepChange,
    csv_import,
)

DEFAULT_DB = Path(__file__).parent / "data" / "shukatsu.db"
DB_TARGET = os.environ.get("SHUKATSU_DB", str(DEFAULT_DB))

st.set_page_config(page_title="shukatsu-tracker", layout="wide")

# Markdown の記号に加え、Streamlit が独自に解釈する記号も対象にする。
# $ は数式、< は自動リンク、& は文字参照。: は絵文字（:smile:）・色（:red[…]）・
# アイコン（:material/…:）の記法で、アイコンはバックスラッシュでは止まらないため
# 文字参照 &#58; に置き換える（表示は : のまま）
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~$<&])")


def as_text(value: str | None, *, keep_lines: bool = False) -> str:
    """利用者が入れた文字列を、Markdown として解釈されない形にする。

    keep_lines を指定すると、改行を Markdown の改行として残す（メモなど複数行の欄）。
    """
    if not value:
        return ""
    text = _MARKDOWN_SPECIALS.sub(r"\\\1", value).replace(":", "&#58;")
    return text.replace("\n", "  \n") if keep_lines else text


# 直前のバックスラッシュの並びも一緒に取る。数を見ないと、`\![` のようにすでに
# 無害な記法へ `\` を足して `\\![`（文字の \ ＋画像）に変えてしまう
_IMAGE_SYNTAX = re.compile(r"(\\*)!\[")


def _escape_image(match: re.Match[str]) -> str:
    backslashes = match.group(1)
    if len(backslashes) % 2:  # 奇数個なら `!` はすでにエスケープされている
        return match.group(0)
    return backslashes + "\\!["


def without_images(text: str) -> str:
    """AI の出力を描画する前に、画像の記法だけを無効にする。

    見出しや箇条書きは読みやすさのため Markdown のまま描画したい。ただし画像の記法は
    描画した時点で外部の URL を読みに行くため、`!` をエスケープして文字として出す。
    """
    return _IMAGE_SYNTAX.sub(_escape_image, text)


def with_saved(options: list[str], value: str) -> tuple[list[str], int]:
    """選択肢と、保存済みの値の位置。

    選択肢にない値（定数を変える前に保存した古いデータ）は末尾に足して表示する。
    `list.index` で落とすと、その企業を選んだ瞬間に画面全体が例外になって編集も
    削除もできなくなる。先頭に倒すと、保存で黙って書き換わる。
    """
    if value in options:
        return options, options.index(value)
    return [*options, value], len(options)


def saved_token(text: str) -> str:
    """保存済みの本文を入力欄のキーに含めるための短い識別子。

    本文そのものをキーにすると長すぎるため、ダイジェストにする。
    """
    return hashlib.blake2s(text.encode("utf-8"), digest_size=8).hexdigest()


def get_db():
    """接続をブラウザのセッションごとに1つ持つ。

    全セッションで1つの接続を共有すると、あるセッションの失敗が別の
    セッションの確定済みの書き込みを巻き戻すため。
    """
    database = st.session_state.get("db")
    if database is not None and not database.ping():
        # サーバーの再起動などで接続が死んでいる。持ち続けると、以後の操作が
        # すべて失敗したままになるので、閉じて作り直す。
        with suppress(Exception):
            database.close()
        database = None
    if database is None:
        database = st.session_state.db = db.connect(DB_TARGET)
    return database


def flash(message: str, kind: str = "success") -> None:
    """再描画をまたいで残る通知を積む。"""
    st.session_state.setdefault("flash", []).append((kind, message))


def show_flash() -> None:
    for kind, message in st.session_state.pop("flash", []):
        getattr(st, kind)(message)


def form_key(name: str) -> str:
    """追加フォームのキー。保存に成功したときだけ変えて、入力欄を空で作り直す。

    `clear_on_submit` は検証や保存に失敗したときも入力を消してしまうため使わない。
    """
    return f"{name}:{st.session_state.get(f'{name}_round', 0)}"


def reset_form(name: str) -> None:
    st.session_state[f"{name}_round"] = st.session_state.get(f"{name}_round", 0) + 1


def run_write(action, success: str | None = None) -> bool:
    """書き込みを実行し、失敗したら利用者に伝わる文面にして返す。"""
    try:
        action()
    except DuplicateKeyError:
        st.error("同じ名前がすでに登録されています。別の名前にしてください。")
    except ConnectionLostError as error:
        # 次の再描画で接続を張り直せるように、死んだ接続は手放す
        st.session_state.pop("db", None)
        st.error(f"保存できませんでした: {error}")
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
importer = CsvImportService(database)

page = st.sidebar.radio(
    "メニュー", ["ダッシュボード", "企業管理", "ES管理", "添削", "分析", "取り込み", "書き出し"]
)
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

    if summary.left_behind:
        with st.expander(f"落選・辞退した企業に残っている締切（{len(summary.left_behind)} 件）"):
            st.caption(
                "落選・辞退のあとに、選考中のまま残っているステップです。上の件数には数えていません。"
                "続いている選考（辞退したインターンのあとの本選考など）なら、企業管理で結果を見直してください。"
                "不要なら、ステップを削除するか結果を「辞退」にすると消えます。"
            )
            for deadline in summary.left_behind:
                step = deadline.step
                st.write(
                    f"**{as_text(step.company_name)}** — {as_text(step.name)}"
                    f"（締切 {step.deadline}）: "
                    + (f"{-deadline.days_left}日超過" if deadline.overdue else f"あと{deadline.days_left}日")
                )

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

    with st.expander("企業を追加", expanded=not companies), st.form(form_key("add_company")):
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
                reset_form("add_company")
                st.rerun()

    if not companies:
        st.stop()

    # 選択は企業の ID で持つ。企業そのものを選択肢にすると、表示文字列（企業名・志望度）を
    # 更新したときに別の欄とみなされ、先頭の企業に戻ってしまう
    company_by_id = {c.id or -1: c for c in companies}
    company_id = st.selectbox(
        "企業を選択",
        list(company_by_id),
        format_func=lambda cid: f"{company_by_id[cid].name}（{company_by_id[cid].priority}）",
        key="company_selected",
    )
    selected = company_by_id[company_id]
    # 一覧と状況の両方をこの1回の問い合わせで賄う
    steps = selection.steps_by_company().get(company_id, [])
    status = analytics.company_status(steps)
    # 状況の文言にはステップ名が入る（「1次面接待ち」など）ので、これもエスケープする
    st.markdown(f"### {as_text(selected.name)} — {as_text(status)}")

    if selected.mypage_url:
        if selected.mypage_url.startswith(("http://", "https://")):
            st.link_button("マイページを開く", selected.mypage_url)
        else:
            st.caption(f"マイページURL: {as_text(selected.mypage_url)}")
        st.caption(f"登録メール: {as_text(selected.login_email) or '未設定'}")
    if selected.memo:
        st.caption(as_text(selected.memo, keep_lines=True))

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
        edited: list[tuple[int, date | None, bool, str, str | None, str]] = []
        for step in steps:
            rendered_deadline = analytics.parse_date(step.deadline)
            # DB の値をキーに含める。別のタブや端末で更新されたとき、
            # 古い入力欄の値が残って上書きするのを防ぐため。
            token = f"{step.id}:{step.deadline}:{step.result}"
            c1, c2, c3, c4 = st.columns([3, 2, 1, 2])
            c1.write(f"**{as_text(step.name)}**")
            new_deadline = c2.date_input(
                "締切",
                value=rendered_deadline,
                key=f"dl:{token}",
                format="YYYY-MM-DD",
                label_visibility="collapsed",
            )
            # 日付の入力欄は、初期値が空のときしか空に戻せない（Streamlit の仕様）。
            # 保存済みの締切を消す手段として、締切がある行にだけ別のチェックを置く
            clear_deadline = bool(step.deadline) and c3.checkbox("締切を消す", key=f"dlclear:{token}")
            result_options, result_index = with_saved(constants.STEP_RESULTS, step.result)
            new_result = c4.selectbox(
                "結果",
                result_options,
                index=result_index,
                key=f"rs:{token}",
                label_visibility="collapsed",
            )
            edited.append(
                (step.id or -1, new_deadline, clear_deadline, new_result, step.deadline, step.result)
            )

        if st.form_submit_button("選考ステップを保存", type="primary"):
            # 押した時点の表示と、いま読み直した値を突き合わせる。ずれている行は
            # 入力欄が作り直されており、下の edited には最新の値しか入っていない
            # （＝新しい値を上書きすることはない）。消えた行も同じ扱いにする。
            outdated = sum(
                1 for step_id, shown in previously_shown.items() if now_shown.get(step_id) != shown
            )
            changes: list[StepChange] = []
            for step_id, new_deadline, clear_deadline, new_result, old_deadline, old_result in edited:
                rendered = analytics.parse_date(old_deadline)
                # 表示していた値と違うものだけを書く。読めない締切に
                # 触っていない場合は、空欄に見えていても書き換えない。
                # 「締切を消す」を付けた行は、日付の入力欄の値にかかわらず空にする
                if clear_deadline:
                    deadline_change = None
                else:
                    deadline_change = (
                        (new_deadline.isoformat() if new_deadline else None)
                        if new_deadline != rendered
                        else UNSET
                    )
                result_change = new_result if new_result != old_result else UNSET
                if deadline_change is not UNSET or result_change is not UNSET:
                    changes.append(StepChange(step_id, deadline=deadline_change, result=result_change))
            # 何行あっても1つの境界で書く。途中で失敗したら何も残らず、失敗の文面は
            # run_write が出す。再描画すると消えるので、失敗したときは止まる。
            saved = not changes or run_write(lambda: selection.update_steps(changes))
            if saved:
                if outdated:
                    flash(
                        f"{outdated} 件は表示後に他の場所で更新されたため、その行への入力は反映していません。"
                        "最新の値を表示しています。",
                        "warning",
                    )
                if changes:
                    flash(f"{len(changes)} 件を保存しました。", "info")
                elif not outdated:
                    flash("変更はありませんでした。", "info")
                st.rerun()

    with st.form(form_key("add_step")):
        c1, c2 = st.columns([3, 1])
        step_name = c1.text_input("ステップを追加（例: 3次面接、リクルーター面談）")
        if c2.form_submit_button("追加"):
            if not step_name.strip():
                st.error("ステップ名を入力してください。")
            elif run_write(
                lambda: selection.add_step(company_id, step_name),
                success=f"「{as_text(step_name.strip())}」を追加しました。",
            ):
                reset_form("add_step")
                st.rerun()

    with st.expander("不要なステップを削除"):
        for step in steps:
            c1, c2 = st.columns([4, 1])
            c1.write(as_text(step.name))
            # 1クリックで消さない。回答の削除と同じく、何が失われるかを見せてから押させる
            with c2.popover("削除"):
                st.warning(f"「{as_text(step.name)}」を消します。締切・結果・メモも消え、元に戻せません。")
                if st.button("削除する", key=f"delstep{step.id}", type="secondary") and run_write(
                    lambda sid=step.id: selection.delete_step(sid or -1),
                    success="ステップを削除しました。",
                ):
                    st.rerun()

    with st.expander("企業情報の編集"), st.form("edit_company"):
        e_name = st.text_input("企業名", value=selected.name)
        c1, c2, c3 = st.columns(3)
        industry_options, industry_index = with_saved(constants.INDUSTRIES, selected.industry)
        e_industry = c1.selectbox("業界", industry_options, index=industry_index)
        priority_options, priority_index = with_saved(constants.PRIORITIES, selected.priority)
        e_priority = c2.selectbox("志望度", priority_options, index=priority_index)
        route_options, route_index = with_saved(constants.ROUTES, selected.route)
        e_route = c3.selectbox("応募経路", route_options, index=route_index)
        c4, c5 = st.columns(2)
        test_options, test_index = with_saved(constants.TEST_TYPES, selected.test_type)
        e_test = c4.selectbox("適性検査", test_options, index=test_index)
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
        if st.button(
            "削除する", key="delete_company", disabled=not confirmed, type="secondary"
        ) and run_write(
            lambda: selection.delete_company(company_id),
            success=f"「{as_text(selected.name)}」を削除しました。",
        ):
            st.rerun()


# --- ES管理 --------------------------------------------------------------

elif page == "ES管理":
    st.title("設問と回答のライブラリ")
    st.caption("一度書いた回答をカテゴリで整理し、文字数制限と照らして管理します。")

    companies = selection.companies()
    # 選択肢は企業の ID（汎用は None）。名前を鍵にすると、「（汎用）」という名前の企業や
    # 同名の企業で選択肢が上書きされる
    company_names: dict[int | None, str] = {None: "（汎用）"}
    company_names.update({c.id: c.name for c in companies})

    with st.expander("設問・回答を追加"), st.form(form_key("add_es")):
        c1, c2, c3 = st.columns([2, 2, 1])
        category = c1.selectbox("カテゴリ", constants.ES_CATEGORIES)
        target_company = c2.selectbox("企業", list(company_names), format_func=company_names.__getitem__)
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
                        company_id=target_company,
                        char_limit=int(char_limit) or None,
                        answer=answer_text,
                    )
                ),
                success="保存しました。",
            ):
                reset_form("add_es")
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

    # 前回この画面に出した本文を控えておく（選考ステップと同じ仕組み）。保存を押した
    # 再実行では入力欄が最新の本文で作り直されるため、古い表示への入力は届かない。
    # 届かなかったことを知らせるには、入力欄とは別に「何を見せていたか」が要る。
    previously_shown_text: dict[int, str] = st.session_state.get("es_shown", {})
    st.session_state["es_shown"] = {a.id or -1: a.answer for a in shown}

    for answer in shown:
        answer_id = answer.id or -1
        # expander の見出しは Markdown として描画される
        title = (
            f"[{answer.category}] {as_text(answer.question[:40])}（{as_text(answer.company_name) or '汎用'}）"
        )
        with st.expander(title):
            # 保存済みの本文をキーに含める。別のタブや端末で更新されたとき、古い入力欄の
            # 値が残って上書きするのを防ぐため。更新日は日付単位なので、同じ日のうちの
            # 更新を見分けられず、キーに使えない。
            new_text = st.text_area(
                "回答",
                value=answer.answer,
                height=200,
                key=f"es:{answer_id}:{saved_token(answer.answer)}",
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

            if st.button("保存", key=f"save{answer_id}"):
                # 押した時点の表示と、いま読み直した本文を突き合わせる。ずれていれば
                # 入力欄は作り直されており、new_text には最新の本文しか入っていない。
                if previously_shown_text.get(answer_id, answer.answer) != answer.answer:
                    st.warning(
                        "表示後に他の場所で更新されたため、この入力は反映していません。最新の本文を表示しています。"
                    )
                elif new_text == answer.answer:
                    st.info("変更はありませんでした。")
                elif run_write(
                    lambda aid=answer_id, text=new_text, seen=answer.answer: es.update_text(
                        aid, text, expected=seen
                    ),
                    success="回答を保存しました。",
                ):
                    st.rerun()

            with st.popover("削除"):
                st.warning("この回答と、ひもづく所見の履歴もすべて消えます。元に戻せません。")
                if st.button("削除する", key=f"rm{answer_id}", type="secondary") and run_write(
                    lambda aid=answer_id: es.delete(aid),
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

    resolved_industry = NO_INDUSTRY if industry == "指定なし" else industry
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
                label += f"（{as_text(review.model)}）"
            if review.input_tokens is not None or review.output_tokens is not None:
                label += f"  入力 {review.input_tokens or 0:,} / 出力 {review.output_tokens or 0:,} トークン"
            if not review.applies_to(target.answer):
                label += "  ※この所見のあとに本文が変わっています"
            with st.expander(label):
                if review.model is None:
                    # 通信しない実行先の結果は依頼文そのもの。提出先・設問・補足が入るので
                    # Markdown として描画せず、書き出したとおりに見せる
                    st.code(review.result, language="markdown")
                else:
                    st.markdown(without_images(review.result))
                c1, c2 = st.columns([1, 5])
                c2.download_button(
                    "この所見を保存",
                    review.result,
                    file_name=f"review_{review.id}.md",
                    key=f"dl_review_{review.id}",
                )
                with c1.popover("削除"):
                    st.warning("この所見を消します。元に戻せません。")
                    if st.button("削除する", key=f"rm_review_{review.id}", type="secondary") and run_write(
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
            [{label: r.group, "通過": r.passed, "落選": r.failed, "通過率": r.rate} for r in rates]
        )

    # 通過率は数値のまま渡し、表示だけを百分率にする。文字列にすると、見出しで
    # 並べ替えたときに "100%" < "33%" < "7%" の辞書順になる
    rate_columns = {"通過率": st.column_config.NumberColumn("通過率", format="percent")}
    st.caption(f"{analytics.PASS_RATE_UNIT}。対象ステップを選ぶと、そのステップだけの通過率になる。")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("応募経路別のステップ通過率")
        st.dataframe(
            rate_frame("route", "応募経路"), width="stretch", hide_index=True, column_config=rate_columns
        )
    with c2:
        st.subheader("適性検査タイプ別のステップ通過率")
        st.dataframe(
            rate_frame("test_type", "適性検査"), width="stretch", hide_index=True, column_config=rate_columns
        )

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


# --- 取り込み ------------------------------------------------------------

elif page == "取り込み":
    st.title("CSV の取り込み")
    st.markdown(
        """スプレッドシートから書き出した CSV を読み込み、企業と選考ステップをまとめて登録します。

- 1行目は見出し、2行目以降は **1行 = 選考ステップ1件**。同じ企業名の行は1社にまとまります
- ファイルを選ぶと、何が取り込まれるかの要約を表示します。**「この内容で取り込む」を押すまで保存されません**
- 登録済みの企業は上書きしません。問題のある行は、行番号と理由をつけて表示します
- 企業のどれかの行に問題があれば、その企業は丸ごと取り込みません（CSV を直して、もう一度取り込めます）
- マイページURL・ログインID・パスワードなど、認証情報に当たる列は取り込みません"""
    )
    with st.expander("受け付ける列"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "見出し": guide.header,
                        "必須": "必須" if guide.required else "",
                        "別名": "、".join(guide.aliases),
                        "内容": guide.note,
                    }
                    for guide in csv_import.column_guide()
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "文字コードは UTF-8 と Shift-JIS（Excel の「CSV (コンマ区切り)」）のどちらでも読めます。"
            "業界などの選択肢の列は、空欄なら企業の追加フォームと同じ既定値になります。"
        )

    # 取り込みが済んだら key を変えて、選択済みのファイルを外す
    uploaded = st.file_uploader(
        "CSV ファイル", type=["csv"], key=f"import_file:{st.session_state.get('import_round', 0)}"
    )
    if uploaded is None:
        st.session_state.pop("import_shown", None)
        st.stop()

    try:
        plan = importer.preview(uploaded.getvalue())
    except CsvFormatError as error:
        st.session_state.pop("import_shown", None)
        st.error(as_text(str(error)))
        st.stop()
    except DatabaseError as error:
        st.session_state.pop("import_shown", None)
        st.error(f"登録済みの企業を確認できませんでした: {error}")
        st.stop()

    # 前回この画面に出した要約を控えておく。押した時点で見えていた内容と、
    # いま作り直した内容がずれていたら書き込まない（企業管理の保存と同じ考え方）。
    summary_shown = st.session_state.get("import_shown")
    st.session_state["import_shown"] = plan

    st.subheader("取り込みの要約")
    m1, m2, m3 = st.columns(3)
    m1.metric("追加される企業", f"{len(plan.companies)} 社")
    m2.metric("追加されるステップ", f"{plan.step_count} 件")
    m3.metric("取り込まない行", f"{len(plan.skipped)} 行")
    notes = [f"文字コード: {plan.encoding}"]
    if plan.blank_rows:
        notes.append(f"空行 {plan.blank_rows} 行は読み飛ばしました")
    st.caption(" / ".join(notes))

    if plan.skipped:
        st.markdown("#### 取り込まない行と理由")
        st.dataframe(
            pd.DataFrame(
                [{"行": row.line, "企業名": row.company_name, "理由": row.reason} for row in plan.skipped]
            ),
            width="stretch",
            hide_index=True,
        )
    if plan.ignored_columns:
        st.markdown("#### 取り込まない列")
        st.dataframe(
            pd.DataFrame([{"見出し": c.header, "理由": c.reason} for c in plan.ignored_columns]),
            width="stretch",
            hide_index=True,
        )

    if not plan.companies:
        st.info("取り込める企業がありません。")
        st.stop()

    st.markdown("#### 追加される企業")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "行": planned.line,
                    "企業名": planned.company.name,
                    "業界": planned.company.industry,
                    "志望度": planned.company.priority,
                    "応募経路": planned.company.route,
                    "適性検査": planned.company.test_type,
                    "ステップ数": len(planned.steps),
                }
                for planned in plan.companies
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    with st.expander("追加されるステップ"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "行": step.line,
                        "企業名": planned.company.name,
                        "ステップ": step.name,
                        "締切": step.deadline or "",
                        "結果": step.result,
                    }
                    for planned in plan.companies
                    for step in planned.steps
                ]
            ),
            width="stretch",
            hide_index=True,
        )

    if st.button("この内容で取り込む", type="primary"):
        if summary_shown != plan:
            st.warning(
                "表示後に登録内容が変わったため、取り込みは行っていません。"
                "上の要約を確認してから、もう一度押してください。"
            )
        else:
            try:
                result = importer.apply(plan)
            except DuplicateKeyError as error:
                st.error(f"取り込みは行っていません（何も保存されていません）。{as_text(str(error))}")
            except DatabaseError as error:
                st.error(f"取り込めませんでした（何も保存されていません）: {error}")
            else:
                flash(f"{result.companies} 社・{result.steps} 件のステップを取り込みました。")
                st.session_state["import_round"] = st.session_state.get("import_round", 0) + 1
                st.session_state.pop("import_shown", None)
                st.rerun()


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
