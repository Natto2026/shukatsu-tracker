"""デモデータを生成する（企業名はすべて架空）。

スクリーンショットの撮影と動作確認に使う。実データは絶対に使わない。

使い方:
    python scripts/demo_data.py          # data/demo.db を作成
    SHUKATSU_DB=data/demo.db streamlit run app.py
PowerShell の場合:
    $env:SHUKATSU_DB="data/demo.db"; streamlit run app.py
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from shukatsu_tracker import db
from shukatsu_tracker.models import Company, EsAnswer
from shukatsu_tracker.services import EsService, SelectionService

DEMO_DB = Path(__file__).parent.parent / "data" / "demo.db"


def days(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


# (企業名, 業界, 志望度, 経路, 検査, [(ステップ, 締切までの日数, 結果), ...])
# 1件=1行の表として読めるよう、手で揃えた並びを保つ
# fmt: off
DEMO_COMPANIES = [
    ("アオゾラ電機", "メーカー", "S", "一般公募", "SPI",
     [("ES", -30, "通過"), ("Webテスト", -20, "通過"), ("1次面接", 3, "選考中")]),
    ("ミカヅキ銀行", "金融", "A", "一般公募", "玉手箱",
     [("ES", -25, "通過"), ("Webテスト", -15, "落選")]),
    ("ツバメ情報システム", "SIer・IT", "A", "スカウト・逆求人", "SPI",
     [("ES", -18, "通過"), ("Webテスト", -10, "通過"), ("1次面接", -2, "通過"),
      ("2次面接", 6, "選考中")]),
    ("ハルカゼ商事", "商社", "B", "一般公募", "TG-WEB",
     [("ES", -12, "落選")]),
    ("コダマ製作所", "メーカー", "B", "ハッカソン・イベント", "独自テスト",
     [("ES", -8, "通過"), ("1次面接", 1, "選考中")]),
    ("ヒナタ損害保険", "金融", "A", "一般公募", "玉手箱",
     [("ES", 0, "選考中")]),
    ("シラカバソリューションズ", "SIer・IT", "C", "スカウト・逆求人", "SPI",
     [("ES", -5, "通過"), ("Webテスト", 5, "選考中")]),
    ("ヤマビコ重工", "メーカー", "S", "一般公募", "SPI",
     [("ES", -40, "通過"), ("Webテスト", -30, "通過"),
      ("グループディスカッション", -20, "落選")]),
]
# fmt: on

# 1件=1行の表として読めるよう、手で揃えた並びを保つ
# fmt: off
DEMO_ANSWERS = [
    ("研究内容", "研究内容を分かりやすく説明してください。", 500,
     "河川の水位計の記録から、増水の兆しを早い段階で見つける手法の研究に取り組んでいる。"
     "…（サンプル文）"),
    ("志望動機", "当社を志望する理由を教えてください。", 300,
     "社会基盤を支える事業に関心があり…（サンプル文）"),
    # 添削ページの見本になるよう、この1本だけ実際の分量で書いてある（内容は架空）
    ("ガクチカ", "学生時代に最も力を入れたことを教えてください。", 400,
     "所属する技術サークルで、部内の出欠管理が紙の名簿で滞っていた課題に取り組んだことである。"
     "毎週の集計に担当者が30分を取られ、記入漏れの問い合わせも続いていた。"
     "原因は、記入する側に手間の自覚がなく、困っているのが集計側だけに閉じていた点にあると考えた。"
     "そこで、まず1か月ぶんの集計時間と問い合わせ件数を記録し、部会で数字として共有した。"
     "そのうえでメンバー3名とWebアプリを作り、既存の名簿と並行して運用する期間を設けて移行した。"
     "結果として集計は数分で終わるようになり、問い合わせもほぼなくなった。"
     "困りごとを自分の感覚ではなく数字で示すことが、人を動かすうえで有効だと学んだ。"),
]
# fmt: on


def main() -> None:
    DEMO_DB.parent.mkdir(parents=True, exist_ok=True)
    if DEMO_DB.exists():
        DEMO_DB.unlink()

    conn = db.connect(DEMO_DB)
    selection = SelectionService(conn)
    es = EsService(conn)

    for name, industry, priority, route, test_type, steps in DEMO_COMPANIES:
        company_id = selection.add_company(
            Company(
                name=name,
                industry=industry,
                priority=priority,
                route=route,
                test_type=test_type,
                memo="※デモデータ（架空の企業）",
            ),
            with_default_steps=False,
        )
        for step_name, offset, result in steps:
            step_id = selection.add_step(company_id, step_name, deadline=days(offset))
            selection.update_step(step_id, result=result)

    for category, question, limit, answer in DEMO_ANSWERS:
        es.add(EsAnswer(question=question, category=category, char_limit=limit, answer=answer))

    conn.close()
    print(f"デモデータを作成しました: {DEMO_DB}")


if __name__ == "__main__":
    main()
