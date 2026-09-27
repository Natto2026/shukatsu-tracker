"""利用者の文字列を、生成する Markdown の構造を壊さない形にする。

添削の依頼文（review/prompt.py）と分析用の書き出し（ai_export.py）が使う。どちらも
AI に読ませる文書なので、利用者の文字列の中の改行が見出しや指示として読まれないよう、
1行に収めるか、囲みの中に入れる。
"""

from __future__ import annotations


def one_line(text: str) -> str:
    """1行に収める。

    str.splitlines は \\n と \\r のほか、\\v・\\f・U+0085・U+2028・U+2029 なども改行とみなす。
    これらで区切られた続きの行が、見出しや指示として読まれないようにする。
    """
    return " ".join(text.splitlines()).strip()


def table_cell(text: str) -> str:
    """表のセルに入れる。改行と縦棒は列の区切りを壊すため置き換える。"""
    return one_line(text).replace("|", "／")


def fence_for(text: str) -> str:
    """本文を囲む記号。本文中の連続バッククォートより1つ長くし、囲みから抜け出せないようにする。"""
    longest = 0
    run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)
