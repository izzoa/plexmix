"""Request-construction compatibility across model families.

Covers the model-family-specific request shaping added in the
``refresh-ai-model-defaults`` change: OpenAI reasoning models,
Gemini 3 thinking models, and Claude Opus 4.7+ sampling-parameter
restrictions, plus factory construction of the new defaults.
"""

import asyncio
from unittest.mock import MagicMock, patch


def _openai_response(text: str = "ok") -> MagicMock:
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    resp.choices = [choice]
    return resp


def _claude_response(text: str = "ok") -> MagicMock:
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _gemini_response(text: str = "ok") -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


class _FakeState:
    """Minimal SettingsState stand-in usable as an async context manager.

    The Settings provider-test helpers (``test_ai_provider_impl``) are
    standalone functions that take a ``state`` and use ``async with state``;
    this lets us drive them with ``asyncio.run`` and inspect the SDK calls.
    """

    def __init__(self, **kwargs):
        self.testing_connection = False
        self.ai_test_status = ""
        self.ai_provider = "openai"
        self.ai_api_key = "sk-test"
        self.ai_model = ""
        self.ai_custom_endpoint = ""
        self.ai_custom_model = ""
        self.ai_custom_api_key = ""
        self.ai_local_mode = "builtin"
        self.ai_local_endpoint = ""
        self.ai_local_auth_token = ""
        for key, value in kwargs.items():
            setattr(self, key, value)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestOpenAIReasoningParams:
    def test_predicate_classifies_models(self):
        from plexmix.ai.openai_provider import is_openai_reasoning_model

        for m in ("gpt-5.4-mini", "gpt-5.5", "gpt-5-nano", "o1", "o3-mini", "o4"):
            assert is_openai_reasoning_model(m), m
        for m in ("gpt-4o", "gpt-4.1", "llama3", "mistral"):
            assert not is_openai_reasoning_model(m), m

    def test_gpt5_uses_max_completion_tokens_and_omits_temperature(self):
        with patch("openai.OpenAI") as mock_openai:
            from plexmix.ai.openai_provider import OpenAIProvider

            client = mock_openai.return_value
            client.chat.completions.create.return_value = _openai_response("hi")

            OpenAIProvider(api_key="sk-test", model="gpt-5.4-mini").complete(
                "hello", max_tokens=1234
            )

            kwargs = client.chat.completions.create.call_args.kwargs
            assert kwargs["max_completion_tokens"] == 1234
            assert kwargs.get("reasoning_effort") == "low"
            assert "temperature" not in kwargs
            assert "max_tokens" not in kwargs

    def test_non_reasoning_model_keeps_max_tokens_and_temperature(self):
        with patch("openai.OpenAI") as mock_openai:
            from plexmix.ai.openai_provider import OpenAIProvider

            client = mock_openai.return_value
            client.chat.completions.create.return_value = _openai_response("hi")

            OpenAIProvider(api_key="sk-test", model="gpt-4o").complete(
                "hello", temperature=0.5, max_tokens=99
            )

            kwargs = client.chat.completions.create.call_args.kwargs
            assert kwargs["max_tokens"] == 99
            assert kwargs["temperature"] == 0.5
            assert "max_completion_tokens" not in kwargs


class TestGeminiThinkingConfig:
    def test_gemini3_sets_thinking_level_low_and_omits_temperature(self):
        with patch("google.genai.Client") as mock_client_cls:
            from google.genai import types

            from plexmix.ai.gemini_provider import GeminiProvider

            client = mock_client_cls.return_value
            client.models.generate_content.return_value = _gemini_response("hi")

            GeminiProvider(api_key="test", model="gemini-3.5-flash").complete(
                "hello", temperature=0.2, max_tokens=2048
            )

            config = client.models.generate_content.call_args.kwargs["config"]
            assert config.thinking_config is not None
            assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
            assert config.temperature is None

    def test_gemini25_keeps_temperature_and_no_thinking_config(self):
        with patch("google.genai.Client") as mock_client_cls:
            from plexmix.ai.gemini_provider import GeminiProvider

            client = mock_client_cls.return_value
            client.models.generate_content.return_value = _gemini_response("hi")

            GeminiProvider(api_key="test", model="gemini-2.5-flash").complete(
                "hello", temperature=0.2, max_tokens=2048
            )

            config = client.models.generate_content.call_args.kwargs["config"]
            assert config.temperature == 0.2
            assert config.thinking_config is None


class TestClaudeSamplingParams:
    def test_predicate_scopes_to_opus_47_plus(self):
        from plexmix.ai.claude_provider import _claude_rejects_sampling_params

        assert _claude_rejects_sampling_params("claude-opus-4-8")
        assert _claude_rejects_sampling_params("claude-opus-4-7")
        assert _claude_rejects_sampling_params("claude-opus-4-9")
        assert _claude_rejects_sampling_params("claude-opus-4-10")  # two-digit minor >= 7
        assert not _claude_rejects_sampling_params("claude-opus-4-6")
        assert not _claude_rejects_sampling_params("claude-opus-4-1-20250414")
        assert not _claude_rejects_sampling_params("claude-sonnet-4-6")
        assert not _claude_rejects_sampling_params("claude-haiku-4-5-20251001")

    def test_opus_omits_sampling_params(self):
        with patch("anthropic.Anthropic") as mock_anthropic:
            from plexmix.ai.claude_provider import ClaudeProvider

            client = mock_anthropic.return_value
            client.with_options.return_value.messages.create.return_value = _claude_response("hi")

            ClaudeProvider(api_key="sk-test", model="claude-opus-4-8").complete(
                "hello", temperature=0.7
            )

            kwargs = client.with_options.return_value.messages.create.call_args.kwargs
            assert "temperature" not in kwargs
            assert "top_p" not in kwargs
            assert "top_k" not in kwargs

    def test_sonnet_and_haiku_keep_temperature(self):
        for model in ("claude-sonnet-4-6", "claude-haiku-4-5-20251001"):
            with patch("anthropic.Anthropic") as mock_anthropic:
                from plexmix.ai.claude_provider import ClaudeProvider

                client = mock_anthropic.return_value
                client.with_options.return_value.messages.create.return_value = _claude_response(
                    "hi"
                )

                ClaudeProvider(api_key="sk-test", model=model).complete("hello", temperature=0.4)

                kwargs = client.with_options.return_value.messages.create.call_args.kwargs
                assert kwargs["temperature"] == 0.4, model


class TestFactoryConstructsNewDefaults:
    def test_openai_default(self):
        with patch("openai.OpenAI"):
            from plexmix.ai import get_ai_provider

            assert get_ai_provider("openai", api_key="sk-test").model == "gpt-5.4-mini"

    def test_gemini_default(self):
        with patch("google.genai.Client"):
            from plexmix.ai import get_ai_provider

            assert get_ai_provider("gemini", api_key="test-key").model == "gemini-3.5-flash"

    def test_claude_default(self):
        with patch("anthropic.Anthropic"):
            from plexmix.ai import get_ai_provider

            assert get_ai_provider("claude", api_key="sk-test").model == "claude-sonnet-4-6"


class TestSettingsTestPaths:
    """The Settings provider-connection test paths (_settings_testing.py)."""

    def test_test_openai_path_uses_reasoning_params(self):
        from plexmix.ui.states._settings_testing import test_ai_provider_impl

        state = _FakeState(ai_provider="openai", ai_model="gpt-5.4-mini", ai_api_key="sk-test")
        with patch("openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _openai_response("test")
            asyncio.run(test_ai_provider_impl(state))

        kwargs = client.chat.completions.create.call_args.kwargs
        assert "max_completion_tokens" in kwargs
        assert "max_tokens" not in kwargs
        assert "✓" in state.ai_test_status  # reached the success path (no 400)

    def test_test_custom_path_keeps_max_tokens(self):
        from plexmix.ui.states._settings_testing import test_ai_provider_impl

        state = _FakeState(
            ai_provider="custom",
            ai_custom_endpoint="http://localhost:11434/v1",
            ai_custom_model="llama3",
        )
        with patch("openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _openai_response("test")
            asyncio.run(test_ai_provider_impl(state))

        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["max_tokens"] == 10
        assert "max_completion_tokens" not in kwargs
