"""評価依頼のプロンプトを組み立てる。

観点そのものは criteria.py が TOML から読み、ここは並べ方だけを担う。
組み立てた文面は必ず利用者が確認できる場所に出す（何を渡したかが
見えないまま外部に送らないため）。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import criteria as criteria_module
from .criteria import CriteriaSet

SYSTEM_PROMPT = """\
あなたは、新卒採用の応募書類を数多く読んできた第三者です。
提出前の回答を読み、書き方の観点から所見を述べてください。

守ること:
- 書き手が書いていない事実・数値・固有名詞を、補って書かないこと。
  情報が足りない箇所は「ここは書かれていないため判断できない」と述べる。
- 点数や合否の判定はしないこと。観点ごとに、満たしているかどうかと理由を述べる。
- 改善案を示す場合も、書き手の事実の範囲内で言い換えるにとどめること。
  新しいエピソードや数字を提案しない。
- 断定できないことは推測と明示すること。
"""

_OUTPUT_FORMAT = """\
## 出力の形式

1. **総合所見** — このまま提出できる状態か。最大の懸念を1〜2文で。
2. **観点ごとの所見** — 下の表で、観点ごとに次を書く。
   - 判定: 満たしている / 一部 / 満たしていない / 判断できない
   - 根拠: 回答中のどの記述からそう判断したか（該当箇所を短く引用する）
3. **改善の提案** — 優先度の高い順に3つまで。各項目に「今どうなっているか」
   「どう直すか」「直すと何が伝わるようになるか」を書く。
4. **書き足りない情報** — 書き手に確認しないと埋められない点を箇条書きで。
5. **想定される深掘り質問** — この回答から聞かれそうな質問を3つ。
"""


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """評価の対象。"""

    question: str
    answer: str
    char_limit: int | None = None
    industry: str | None = None
    company_name: str | None = None
    note: str = ""

    @property
    def length(self) -> int:
        return len(self.answer)


def _inline(text: str) -> str:
    """1行に収める。改行を含む入力が、続きの行で見出しや指示として読まれないようにする。"""
    return text.replace("\r", " ").replace("\n", " ").strip()


def _cell(text: str) -> str:
    """表のセルに入れる。改行と縦棒は列の区切りを壊すため置き換える。"""
    return _inline(text).replace("|", "／")


def _fence_for(text: str) -> str:
    """本文を囲む記号。本文中の連続バッククォートより1つ長くする。"""
    longest = 0
    run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _criteria_table(criteria: CriteriaSet) -> str:
    lines = ["| 観点 | 重み | 見るところ | 弱いときの見え方 |", "|---|---|---|---|"]
    for criterion in criteria:
        check = _cell(criterion.check)
        weak = _cell(criterion.weak)
        lines.append(f"| {criterion.title} | {criterion.emphasis_label} | {check} | {weak} |")
    return "\n".join(lines)


def _target_block(request: ReviewRequest) -> str:
    lines = ["## 評価の対象", ""]
    if request.company_name:
        lines.append(f"- 提出先: {_inline(request.company_name)}")
    lines.append(f"- 応募業界: {request.industry or '指定なし'}")
    lines.append(f"- 設問: {_inline(request.question)}")
    if request.char_limit:
        lines.append(f"- 文字数: {request.length} / {request.char_limit}（制限あり）")
    else:
        lines.append(f"- 文字数: {request.length}（制限の指定なし）")
    if request.note:
        lines.append(f"- 書き手からの補足: {_cell(request.note)}")
    # 本文は囲って渡す。見出しや表を含む回答が、下の指示や観点の表を
    # 上書きして読まれないようにするため。
    fence = _fence_for(request.answer)
    lines += [
        "",
        "### 回答本文（囲みの内側だけが評価の対象）",
        "",
        fence,
        request.answer.strip(),
        fence,
    ]
    return "\n".join(lines)


def build(request: ReviewRequest, criteria: CriteriaSet | None = None) -> str:
    """評価依頼の本文を組み立てる。"""
    if not request.question.strip():
        raise ValueError("設問文が空です")
    if not request.answer.strip():
        raise ValueError("回答本文が空です")

    resolved = criteria or criteria_module.for_industry(request.industry)
    return "\n\n".join(
        [
            "# 依頼: 応募書類の回答へのフィードバック",
            _target_block(request),
            "## 読み方",
            resolved.reader,
            "## 評価の観点",
            "下の観点で読んでください。「重み」が「特に重視」の行から先に見ます。",
            _criteria_table(resolved),
            _OUTPUT_FORMAT,
        ]
    )
