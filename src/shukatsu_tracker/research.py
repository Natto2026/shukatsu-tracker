"""企業名から企業研究用のリンク集を生成する。

外部 API は使わず検索 URL を組み立てるだけなので、通信も認証も不要。
サイト個別の検索 URL 仕様に依存しないよう、site: 指定の Google 検索に統一している。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True, slots=True)
class ResearchLink:
    """企業研究用のリンク1本。表示名とURLの組。"""

    label: str
    url: str


def research_links(company_name: str) -> list[ResearchLink]:
    """企業研究でまず見るべきページへの検索リンクを返す。"""
    q = quote(company_name)
    google = "https://www.google.com/search?q="
    return [
        ResearchLink("公式サイト", f"{google}{q}"),
        ResearchLink("新卒採用ページ", f"{google}{q}+{quote('新卒採用')}"),
        ResearchLink("事業内容", f"{google}{q}+{quote('事業内容')}"),
        ResearchLink("IR・有価証券報告書", f"{google}{q}+{quote('IR 有価証券報告書')}"),
        ResearchLink("社員クチコミ(OpenWork)", f"{google}site:openwork.jp+{q}"),
        ResearchLink("選考体験記(ONE CAREER)", f"{google}site:onecareer.jp+{q}"),
        ResearchLink("最新ニュース", f"https://news.google.com/search?q={q}&hl=ja&gl=JP"),
    ]
