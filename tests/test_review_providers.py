"""実行先の検証。通信せずに、送る内容と後処理だけを確かめる。"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

from shukatsu_tracker.review.prompt import ReviewRequest
from shukatsu_tracker.review.providers import (
    API_KEY_ENV,
    DEFAULT_MODEL,
    MODEL_ENV,
    AnthropicProvider,
    ExportProvider,
    ReviewError,
    available_providers,
    configured_model,
    extract_text,
)

REQUEST = ReviewRequest(question="設問", answer="回答本文", industry="金融")
PROMPT = "組み立て済みの依頼文"


@dataclass
class FakeBlock:
    type: str
    text: str = ""


class FakeResponse:
    def __init__(self, blocks, stop_reason="end_turn", model="fake-model"):
        self.content = blocks
        self.stop_reason = stop_reason
        self.model = model


class FakeClient:
    """呼び出しの引数を記録するだけのクライアント。"""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict] = []
        self.beta = self

    @property
    def messages(self):
        return self

    def create(self, **params):
        self.calls.append(params)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class TestExportProvider:
    def test_returns_the_prompt_unchanged(self):
        result = ExportProvider().review(REQUEST, PROMPT)
        assert result.text == PROMPT
        assert result.prompt == PROMPT
        assert result.model is None

    def test_declares_that_it_does_not_send_data(self):
        assert ExportProvider().sends_data_externally is False


class TestAnthropicProvider:
    def test_sends_the_prompt_and_system_instruction(self):
        client = FakeClient(FakeResponse([FakeBlock("text", "所見の本文")]))
        result = AnthropicProvider(client).review(REQUEST, PROMPT)

        assert result.text == "所見の本文"
        assert result.model == "fake-model"
        sent = client.calls[0]
        assert sent["messages"] == [{"role": "user", "content": PROMPT}]
        assert "補って書かないこと" in sent["system"]
        assert sent["max_tokens"] > 0

    def test_requests_a_fallback_so_a_decline_still_returns_something(self):
        client = FakeClient(FakeResponse([FakeBlock("text", "所見")]))
        AnthropicProvider(client).review(REQUEST, PROMPT)
        assert client.calls[0]["fallbacks"] == "default"
        assert client.calls[0]["betas"] == ["server-side-fallback-2026-07-01"]

    def test_model_defaults_when_the_environment_is_unset(self, monkeypatch):
        monkeypatch.delenv(MODEL_ENV, raising=False)
        assert configured_model() == DEFAULT_MODEL
        assert AnthropicProvider().model == DEFAULT_MODEL

    def test_model_can_be_overridden_by_the_environment(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV, "claude-sonnet-5")
        assert configured_model() == "claude-sonnet-5"
        client = FakeClient(FakeResponse([FakeBlock("text", "所見")]))
        AnthropicProvider(client).review(REQUEST, PROMPT)
        assert client.calls[0]["model"] == "claude-sonnet-5"

    def test_a_blank_environment_value_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV, "   ")
        assert configured_model() == DEFAULT_MODEL

    def test_an_explicit_model_wins_over_the_environment(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV, "claude-sonnet-5")
        assert AnthropicProvider(model="claude-opus-4-8").model == "claude-opus-4-8"

    def test_the_environment_is_read_at_call_time_not_at_import(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV, "claude-haiku-4-5")
        assert AnthropicProvider().model == "claude-haiku-4-5"
        monkeypatch.delenv(MODEL_ENV, raising=False)
        assert AnthropicProvider().model == DEFAULT_MODEL

    def test_fallback_can_be_switched_off(self):
        client = FakeClient(FakeResponse([FakeBlock("text", "所見")]))
        AnthropicProvider(client, use_fallbacks=False).review(REQUEST, PROMPT)
        assert "fallbacks" not in client.calls[0]
        assert "betas" not in client.calls[0]

    def test_refusal_is_reported_as_a_readable_error(self):
        client = FakeClient(FakeResponse([], stop_reason="refusal"))
        with pytest.raises(ReviewError, match="見送られました"):
            AnthropicProvider(client).review(REQUEST, PROMPT)

    def test_empty_response_is_an_error(self):
        client = FakeClient(FakeResponse([FakeBlock("thinking")]))
        with pytest.raises(ReviewError, match="本文が含まれていません"):
            AnthropicProvider(client).review(REQUEST, PROMPT)

    def test_missing_dependency_is_reported(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "anthropic", None)
        with pytest.raises(ReviewError, match="追加の依存"):
            AnthropicProvider().review(REQUEST, PROMPT)

    def test_missing_api_key_is_reported(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=lambda: None))
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        with pytest.raises(ReviewError, match=API_KEY_ENV):
            AnthropicProvider().review(REQUEST, PROMPT)

    def test_is_configured_reads_only_the_presence_of_the_key(self, monkeypatch):
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        assert AnthropicProvider.is_configured() is False
        monkeypatch.setenv(API_KEY_ENV, "dummy")
        assert AnthropicProvider.is_configured() is True


class TestExtractText:
    def test_joins_text_blocks_only(self):
        response = FakeResponse([FakeBlock("thinking"), FakeBlock("text", "前半"), FakeBlock("text", "後半")])
        assert extract_text(response) == "前半\n後半"

    def test_missing_content_is_empty(self):
        assert extract_text(object()) == ""


class TestAvailableProviders:
    def test_offline_provider_is_always_first(self, monkeypatch):
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        providers = available_providers()
        assert len(providers) == 1
        assert providers[0].sends_data_externally is False

    def test_api_provider_appears_when_the_key_is_set(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV, "dummy")
        providers = available_providers()
        assert [p.sends_data_externally for p in providers] == [False, True]
