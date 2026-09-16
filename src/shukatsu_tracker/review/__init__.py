"""回答への所見をまとめる機能。

観点の定義（criteria）、依頼文の組み立て（prompt）、実行先（providers）に
分かれている。既定の実行先は通信せず、依頼文を書き出すだけ。
"""

from .criteria import CriteriaSet, Criterion, available_industries, for_industry
from .prompt import SYSTEM_PROMPT, ReviewRequest, build
from .providers import (
    AnthropicProvider,
    ExportProvider,
    ReviewError,
    ReviewProvider,
    ReviewResult,
    available_providers,
)

__all__ = [
    "SYSTEM_PROMPT",
    "AnthropicProvider",
    "CriteriaSet",
    "Criterion",
    "ExportProvider",
    "ReviewError",
    "ReviewProvider",
    "ReviewRequest",
    "ReviewResult",
    "available_industries",
    "available_providers",
    "build",
    "for_industry",
]
