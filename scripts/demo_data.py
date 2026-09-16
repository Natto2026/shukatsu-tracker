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

DEMO_ANSWERS = [
    ("ガクチカ", "学生時代に最も力を入れたことを教えてください。", 400,
     "所属する技術サークルで、部内の出欠管理が紙運用で滞っていた課題に対し、"
     "メンバー3名とWebアプリを開発して電子化を主導しました。…（サンプル文）"),
    ("志望動機", "当社を志望する理由を教えてください。", 300,
     "貴社の社会インフラを支える事業に魅力を感じ…（サンプル文）"),
    ("研究内容", "研究内容を分かりやすく説明してください。", 500,
     "動画データからの特徴抽出をテーマに…（サンプル文）"),
]


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
        es.add(
            EsAnswer(question=question, category=category, char_limit=limit, answer=answer)
        )

    conn.close()
    print(f"デモデータを作成しました: {DEMO_DB}")


if __name__ == "__main__":
    main()
