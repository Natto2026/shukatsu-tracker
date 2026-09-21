"""評価の実行先。

既定は「書き出すだけ」で、外部との通信は行わない。通信する実行先は
利用者が明示的に選んだときだけ使われる。API キーはこのアプリでは
保存も受け取りもせず、SDK が環境変数から解決したものをそのまま使う
（アプリが認証情報を持たない方針を、この機能でも崩さないため）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

from .prompt import ReviewRequest

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 16000
# 画面を止めたまま待たせないため、既定（10分×再試行）より短く切る
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_RETRIES = 1
API_KEY_ENV = "ANTHROPIC_API_KEY"
MODEL_ENV = "SHUKATSU_REVIEW_MODEL"


def configured_model() -> str:
    """使うモデル名。環境変数で上書きでき、未設定なら既定値。

    モデルは API キーと違って秘密ではないが、新しいモデルが出るたびに
    コードを直すのは筋が悪いので、実行環境から差し替えられるようにする。
    読むのは呼ばれた時点で、取り込み時に固定しない。
    """
    return os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL


class ReviewError(RuntimeError):
    """評価を実行できなかった。利用者にそのまま見せられる文面を持つ。"""


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """評価の実行結果。何を送って何が返ったかを組で残す。"""

    provider: str
    prompt: str
    text: str
    model: str | None = None


class ReviewProvider(Protocol):
    """評価の実行先の共通の形。"""

    name: str
    sends_data_externally: bool

    def review(self, request: ReviewRequest, prompt: str) -> ReviewResult: ...


class ExportProvider:
    """通信せず、組み立てたプロンプトをそのまま返す（既定）。

    利用者が内容を確認したうえで、好きな場所に貼って使う。
    """

    name = "書き出しのみ（通信しない）"
    sends_data_externally = False

    def review(self, request: ReviewRequest, prompt: str) -> ReviewResult:
        return ReviewResult(provider=self.name, prompt=prompt, text=prompt)


class AnthropicProvider:
    """Claude API に送って所見を受け取る。

    利用者が明示的に選んだときだけ使う。クライアントを外から渡せるように
    してあるのは、通信せずに組み立てと後処理をテストするため。
    """

    name = "Claude API"
    sends_data_externally = True

    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        use_fallbacks: bool = True,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._client = client
        # 明示的に渡された値 > 環境変数 > 既定値。既定引数で解決すると
        # 取り込み時の環境変数で固定されてしまうため、ここで決める。
        self.model = model or configured_model()
        self.max_tokens = max_tokens
        self.use_fallbacks = use_fallbacks
        self.timeout = timeout
        self.max_retries = max_retries

    @staticmethod
    def is_configured() -> bool:
        """環境変数に API キーがあるか。値そのものは読まない。"""
        return bool(os.environ.get(API_KEY_ENV))

    def _build_client(self) -> Any:
        try:
            import anthropic
        except ImportError as error:
            raise ReviewError('Claude API を使うには追加の依存が必要です: pip install -e ".[llm]"') from error
        if not self.is_configured():
            raise ReviewError(
                f"環境変数 {API_KEY_ENV} が設定されていません。"
                "キーはアプリに保存せず、実行環境の環境変数から読み込みます。"
            )
        return anthropic.Anthropic(timeout=self.timeout, max_retries=self.max_retries)

    def review(self, request: ReviewRequest, prompt: str) -> ReviewResult:
        client = self._client or self._build_client()
        from .prompt import SYSTEM_PROMPT

        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.use_fallbacks:
            # 方針上の理由で応答が断られた場合に、同じ呼び出しの中で
            # 別のモデルへ引き継ぐ。断られたまま何も返らない状態を避ける。
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["fallbacks"] = "default"

        response = self._call(client, params)
        if getattr(response, "stop_reason", None) == "refusal":
            raise ReviewError("評価の実行が安全上の理由で見送られました。回答本文の内容を確認してください。")

        text = extract_text(response)
        if not text:
            raise ReviewError("応答に本文が含まれていませんでした。")
        return ReviewResult(
            provider=self.name,
            prompt=prompt,
            text=text,
            model=getattr(response, "model", self.model),
        )

    def _call(self, client: Any, params: dict[str, Any]) -> Any:
        try:
            import anthropic
        except ImportError:  # pragma: no cover - テスト用の差し込みクライアント向け
            return client.beta.messages.create(**params)

        try:
            return client.beta.messages.create(**params)
        except anthropic.AuthenticationError as error:
            raise ReviewError(f"{API_KEY_ENV} の値が受け付けられませんでした。") from error
        except anthropic.RateLimitError as error:
            raise ReviewError("送信の上限に達しました。時間をおいて試してください。") from error
        except anthropic.APIStatusError as error:
            raise ReviewError(f"API がエラーを返しました（{error.status_code}）。") from error
        except anthropic.APITimeoutError as error:
            raise ReviewError(
                f"{self.timeout:.0f} 秒以内に応答がありませんでした。時間をおいて試してください。"
            ) from error
        except anthropic.APIConnectionError as error:
            raise ReviewError("API に接続できませんでした。通信環境を確認してください。") from error
        except TypeError as error:
            raise ReviewError(
                f'SDK がこの呼び出し方に対応していません: pip install -U "anthropic>=1.0"（{error}）'
            ) from error


def extract_text(response: Any) -> str:
    """応答から本文のブロックだけを取り出して連結する。"""
    parts = [
        block.text for block in getattr(response, "content", []) if getattr(block, "type", None) == "text"
    ]
    return "\n".join(part for part in parts if part).strip()


def available_providers() -> list[ReviewProvider]:
    """選べる実行先。通信しないものを先頭にする。"""
    providers: list[ReviewProvider] = [ExportProvider()]
    if AnthropicProvider.is_configured():
        providers.append(AnthropicProvider())
    return providers
