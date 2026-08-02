"""企業名から企業研究用のリンク集を生成する。

外部 API は使わず検索 URL を組み立てるだけなので、通信も認証も不要。
サイト個別の検索 URL 仕様に依存しないよう、site: 指定の Google 検索に統一している。
"""

from __future__ import annotations

from urllib.parse import quote


def research_links(company_name: str) -> list[dict]:
    """企業研究でまず見るべきページへの検索リンクを返す。"""
    q = quote(company_name)
    google = "https://www.google.com/search?q="
    return [
        {"label": "公式サイト", "url": f"{google}{q}"},
        {"label": "新卒採用ページ", "url": f"{google}{q}+{quote('新卒採用')}"},
        {"label": "事業内容", "url": f"{google}{q}+{quote('事業内容')}"},
        {"label": "IR・有価証券報告書", "url": f"{google}{q}+{quote('IR 有価証券報告書')}"},
        {"label": "社員クチコミ(OpenWork)", "url": f"{google}site:openwork.jp+{q}"},
        {"label": "選考体験記(ONE CAREER)", "url": f"{google}site:onecareer.jp+{q}"},
        {"label": "最新ニュース", "url": f"https://news.google.com/search?q={q}&hl=ja&gl=JP"},
    ]
