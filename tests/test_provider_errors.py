"""Tests for AI provider error classification and fail-fast tag generation.

Covers the containment + diagnosis side of harden-ai-provider-errors: the
retryable/fatal classifier, the HTML-edge-rejection message, the
FatalProviderError carrier, and the tag generator aborting the run on a fatal
error while preserving partial results.
"""

import json
import logging
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

import plexmix.ai as ai_factory
from plexmix.ai.errors import FatalProviderError, classify_provider_error
from plexmix.ai.tag_generator import TagGenerator

# Reference credentials helpers through the live module: test_config reloads it,
# rebinding the InvalidAPIKeyError class, so a directly-imported name could drift.
from plexmix.config import credentials

# The exact body the reporting user saw — an HTML page from Google's edge, not JSON.
GFE_HTML_400 = (
    "400 Bad Request {'message': '<html><title>Error 400 (Bad Request)!!1</title>"
    "</html>', 'status': 'Bad Request'}"
)


class TestClassifyProviderError:
    @pytest.mark.parametrize(
        "msg",
        [
            "429 quota exceeded",
            "Rate limit reached",
            "Read timed out",
            "408 Request Timeout",
            "500 internal error",
            "503 service unavailable",
        ],
    )
    def test_retryable(self, msg):
        is_retryable, is_fatal, _ = classify_provider_error(Exception(msg))
        assert is_retryable and not is_fatal

    @pytest.mark.parametrize(
        "msg",
        [
            "401 Unauthorized",
            "403 Forbidden",
            "400 INVALID_ARGUMENT",
            "404 model not found",
            "422 Unprocessable Entity",
            "409 Conflict",
        ],
    )
    def test_fatal(self, msg):
        is_retryable, is_fatal, _ = classify_provider_error(Exception(msg))
        assert is_fatal and not is_retryable

    def test_html_400_is_fatal_with_edge_message(self):
        is_retryable, is_fatal, user_message = classify_provider_error(Exception(GFE_HTML_400))
        assert is_fatal and not is_retryable
        assert "before it reached the model" in user_message.lower()
        assert "api key" in user_message.lower()

    def test_unknown_is_neither(self):
        assert classify_provider_error(Exception("some weird glitch"))[:2] == (False, False)


class TestFailureModeDistinction:
    """A transport-unsafe key fails pre-flight; a server HTML 400 is an edge rejection."""

    def test_transport_unsafe_key_reported_as_invalid_characters(self):
        with pytest.raises(credentials.InvalidAPIKeyError) as ei:
            credentials.validate_api_key("AIza\nbadkey", "gemini")
        assert "invalid characters" in str(ei.value).lower()

    def test_server_html_400_reported_as_edge_rejection(self):
        _, is_fatal, user_message = classify_provider_error(Exception(GFE_HTML_400))
        assert is_fatal
        assert "before it reached the model" in user_message.lower()


class TestLocalAuthToken:
    """The local endpoint token is sent as a header, so it gets the API-key check."""

    ENDPOINT = "http://127.0.0.1:9/v1/chat/completions"
    REFUSAL = "Local endpoint auth token contains invalid characters"

    @pytest.mark.parametrize(
        "token",
        ["dummy-api\nkey-0000", "dummy-api key-0000", "dummy-\u00e4pi-key-0000"],
    )
    def test_local_auth_token_with_line_break_is_refused(
        self, token: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="plexmix.ai"):
            with patch.object(ai_factory, "LocalLLMProvider") as provider_cls:
                # ValueError: the concrete class can be rebound by a module reload (see
                # the import note above), and InvalidAPIKeyError subclasses it.
                with pytest.raises(ValueError) as ei:
                    ai_factory.get_ai_provider(
                        "local",
                        local_mode="endpoint",
                        local_endpoint=self.ENDPOINT,
                        local_auth_token=token,
                    )

        # The provider was never built, so no request could carry the token.
        provider_cls.assert_not_called()
        assert type(ei.value).__name__ == "InvalidAPIKeyError"
        # The validator's own error (about an "API key") is not shown with it.
        assert ei.value.__cause__ is None and ei.value.__suppress_context__ is True
        message = str(ei.value)
        # The message names the token, not an "API key".
        assert message.startswith(self.REFUSAL)
        assert "api key" not in message.lower()
        # The refusal is logged once, where it happens: callers build quietly.
        assert [record.getMessage() for record in caplog.records] == [message]
        assert caplog.records[0].levelno == logging.ERROR
        for text in (message, caplog.text):
            assert token not in text
            assert repr(token)[1:-1] not in text
            assert "key-0000" not in text

    @pytest.mark.parametrize("silent", [True, False])
    def test_refused_local_token_is_not_logged(
        self, silent: bool, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The user is told why, and the token is not shown.

        Every caller in the CLI and the web UI builds with ``silent=True``, so the
        reason has to reach the log in that mode too.
        """
        from plexmix.services import providers

        token = "dummy-api\nkey-0000"
        settings = SimpleNamespace(
            ai=SimpleNamespace(
                default_provider="local",
                model=None,
                temperature=0.7,
                local_mode="endpoint",
                local_endpoint=self.ENDPOINT,
                local_auth_token=token,
                local_max_output_tokens=None,
                custom_endpoint=None,
                custom_api_key=None,
                custom_model=None,
            )
        )

        with caplog.at_level(logging.DEBUG):
            with patch.object(providers, "resolve_ai_api_key", return_value=None):
                assert providers.build_ai_provider(settings, silent=silent) is None

        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1 and errors[0].startswith(self.REFUSAL)
        assert token not in caplog.text
        assert repr(token)[1:-1] not in caplog.text
        assert "key-0000" not in caplog.text

    @pytest.mark.parametrize("mode", ["builtin", None])
    @pytest.mark.parametrize("token", ["dummy-api\nkey-0000", "dummy-api key-0000"])
    def test_leftover_token_does_not_stop_the_builtin_model(
        self, token: str, mode: Optional[str], caplog: pytest.LogCaptureFixture
    ) -> None:
        # Only endpoint mode sends the token. In builtin mode a token left over from
        # an earlier endpoint setup is never sent, so it is not a reason to refuse.
        with caplog.at_level(logging.DEBUG, logger="plexmix.ai"):
            with patch.object(ai_factory, "LocalLLMProvider") as provider_cls:
                provider = ai_factory.get_ai_provider(
                    "local", local_mode=mode, local_auth_token=token
                )

        assert provider is provider_cls.return_value
        provider_cls.assert_called_once()
        assert provider_cls.call_args.kwargs["mode"] == "builtin"
        assert caplog.records == []

    @pytest.mark.parametrize("token", [None, ""])
    def test_local_endpoint_without_a_token_still_builds(self, token: Optional[str]) -> None:
        # No auth token is a normal setup for a local endpoint.
        provider = ai_factory.get_ai_provider(
            "local", local_mode="endpoint", local_endpoint=self.ENDPOINT, local_auth_token=token
        )

        assert isinstance(provider, ai_factory.LocalLLMProvider)
        assert not provider.auth_token

    def test_clean_local_token_is_passed_on_without_its_surrounding_whitespace(self) -> None:
        provider = ai_factory.get_ai_provider(
            "local",
            local_mode="endpoint",
            local_endpoint=self.ENDPOINT,
            local_auth_token="  dummy-api-key-0000\n",
        )

        assert provider.auth_token == "dummy-api-key-0000"


class TestFatalProviderError:
    def test_carries_fields(self):
        cause = ValueError("x")
        e = FatalProviderError("boom", user_message="hi", cause=cause, partial_results={1: {}})
        assert e.user_message == "hi"
        assert e.cause is cause
        assert e.partial_results == {1: {}}

    def test_defaults(self):
        e = FatalProviderError("boom")
        assert e.user_message == "boom"
        assert e.partial_results == {}


def _tracks(ids):
    return [{"id": i, "title": f"t{i}", "artist": "a", "genre": "g"} for i in ids]


def _valid_json(ids):
    return json.dumps(
        {str(i): {"tags": ["x"], "environments": ["work"], "instruments": ["piano"]} for i in ids}
    )


class TestTagGeneratorFailFast:
    def test_fatal_first_batch_raises_and_stops(self):
        provider = MagicMock()
        provider.complete.side_effect = Exception("401 Unauthorized invalid api key")
        tg = TagGenerator(provider)
        with pytest.raises(FatalProviderError):
            tg.generate_tags_batch(_tracks([1, 2, 3]), batch_size=1)
        # Aborts after the first failing batch — no further provider calls.
        assert provider.complete.call_count == 1

    def test_no_empty_tag_map_on_fatal(self):
        provider = MagicMock()
        provider.complete.side_effect = Exception(GFE_HTML_400)
        tg = TagGenerator(provider)
        with pytest.raises(FatalProviderError):
            tg.generate_tags_batch(_tracks([1, 2]), batch_size=1)

    def test_partial_results_preserved_on_later_fatal(self):
        provider = MagicMock()
        provider.complete.side_effect = [_valid_json([1]), Exception("403 Forbidden")]
        tg = TagGenerator(provider)
        with pytest.raises(FatalProviderError) as ei:
            tg.generate_tags_batch(_tracks([1, 2]), batch_size=1)
        assert 1 in ei.value.partial_results
        assert 2 not in ei.value.partial_results

    def test_retryable_then_success(self, monkeypatch):
        monkeypatch.setattr("plexmix.ai.tag_generator.time.sleep", lambda s: None)
        provider = MagicMock()
        provider.complete.side_effect = [Exception("429 rate limit"), _valid_json([1])]
        tg = TagGenerator(provider)
        res = tg.generate_tags_batch(_tracks([1]), batch_size=1)
        assert res[1]["tags"] == ["x"]
        assert provider.complete.call_count == 2

    def test_unknown_error_degrades_to_empty(self):
        provider = MagicMock()
        provider.complete.side_effect = Exception("some weird glitch")
        tg = TagGenerator(provider)
        res = tg.generate_tags_batch(_tracks([1]), batch_size=1)
        assert res[1] == {"tags": [], "environments": [], "instruments": []}
