"""
Regression tests for AI provider tagging functionality.

Phase G: Tests for provider reliability improvements (Phase D).
"""
import pytest
from unittest.mock import MagicMock, patch


class TestProviderComplete:
    """Tests for the uniform complete() interface across providers."""

    def test_gemini_provider_complete(self):
        """Test GeminiProvider.complete() with mocked API."""
        mock_response = MagicMock()
        mock_response.text = '{"1": {"tags": ["energetic", "upbeat"], "environments": ["workout"], "instruments": ["guitar"]}}'

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("google.genai.Client", return_value=mock_client):
            from plexmix.ai.gemini_provider import GeminiProvider

            provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash")

            result = provider.complete("Test prompt", temperature=0.3, max_tokens=1000)

            assert '{"1":' in result
            mock_client.models.generate_content.assert_called_once()

    def test_openai_provider_complete(self):
        """Test OpenAIProvider.complete() with mocked API."""
        with patch("openai.OpenAI") as mock_client_class:
            # Setup mock
            mock_message = MagicMock()
            mock_message.content = '{"tags": ["chill", "relaxing"]}'
            mock_choice = MagicMock()
            mock_choice.message = mock_message
            mock_response = MagicMock()
            mock_response.choices = [mock_choice]

            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_client_class.return_value = mock_client

            from plexmix.ai.openai_provider import OpenAIProvider

            provider = OpenAIProvider(api_key="test-key", model="gpt-5-mini")

            result = provider.complete("Test prompt", temperature=0.3, max_tokens=1000)

            assert "tags" in result
            mock_client.chat.completions.create.assert_called_once()

    def test_claude_provider_complete(self):
        """Test ClaudeProvider.complete() with mocked API."""
        with patch("anthropic.Anthropic") as mock_client_class:
            # Setup mock
            mock_text = MagicMock()
            mock_text.text = '{"tags": ["melancholic", "slow"]}'
            mock_response = MagicMock()
            mock_response.content = [mock_text]

            mock_client = MagicMock()
            # Mock the with_options().messages.create() chain
            mock_client.with_options.return_value.messages.create.return_value = mock_response
            mock_client_class.return_value = mock_client

            from plexmix.ai.claude_provider import ClaudeProvider

            provider = ClaudeProvider(api_key="test-key", model="claude-sonnet-4-5-20250929")

            result = provider.complete("Test prompt", temperature=0.3, max_tokens=1000)

            assert "tags" in result
            mock_client.with_options.return_value.messages.create.assert_called_once()

    def test_cohere_provider_complete(self):
        """Test CohereProvider.complete() with mocked API."""
        pytest.importorskip("cohere")
        with patch("cohere.ClientV2") as mock_client_class:
            # Setup mock
            mock_text = MagicMock()
            mock_text.text = '{"tags": ["energetic", "happy"]}'
            mock_message = MagicMock()
            mock_message.content = [mock_text]
            mock_response = MagicMock()
            mock_response.message = mock_message

            mock_client = MagicMock()
            mock_client.chat.return_value = mock_response
            mock_client_class.return_value = mock_client

            from plexmix.ai.cohere_provider import CohereProvider

            provider = CohereProvider(api_key="test-key", model="command-r7b-12-2024")

            result = provider.complete("Test prompt", temperature=0.3, max_tokens=1000)

            assert "tags" in result
            mock_client.chat.assert_called_once()


class TestTagGenerator:
    """Tests for TagGenerator using the uniform complete() interface."""

    def test_tag_generator_uses_complete_interface(self):
        """Test that TagGenerator uses provider.complete() instead of provider-specific logic."""
        from plexmix.ai.tag_generator import TagGenerator

        # Create mock provider with complete() method
        mock_provider = MagicMock()
        mock_provider.complete.return_value = """
        {
            "1": {"tags": ["energetic", "upbeat"], "environments": ["workout"], "instruments": ["drums"]},
            "2": {"tags": ["calm", "peaceful"], "environments": ["relax"], "instruments": ["piano"]}
        }
        """

        generator = TagGenerator(mock_provider)

        tracks = [
            {"id": 1, "title": "Track 1", "artist": "Artist 1", "genre": "Rock"},
            {"id": 2, "title": "Track 2", "artist": "Artist 2", "genre": "Classical"},
        ]

        results = generator._generate_batch(tracks)

        # Verify complete() was called
        mock_provider.complete.assert_called_once()

        # Verify results
        assert 1 in results
        assert 2 in results
        assert "energetic" in results[1]["tags"]
        assert "calm" in results[2]["tags"]

    def test_tag_generator_handles_json_errors(self):
        """Test that TagGenerator handles malformed JSON gracefully."""
        from plexmix.ai.tag_generator import TagGenerator

        mock_provider = MagicMock()
        # Return malformed JSON that will fail to parse
        mock_provider.complete.return_value = "This is not valid JSON {{"

        generator = TagGenerator(mock_provider)

        tracks = [{"id": 1, "title": "Track 1", "artist": "Artist 1", "genre": "Rock"}]

        # Should return empty results, not crash
        results = generator._generate_batch(tracks)

        assert 1 in results
        assert results[1]["tags"] == []


class TestOnBatchCallback:
    """generate_tags_batch hands each batch to the caller before it asks for the next."""

    @staticmethod
    def _tracks(count):
        return [
            {"id": n, "title": f"Track {n}", "artist": "Artist", "genre": "Rock"}
            for n in range(1, count + 1)
        ]

    @staticmethod
    def _provider(fail_for=None, order=None):
        """A provider that tags every track of a request. One id can make the request fail."""
        import json
        import re

        provider = MagicMock()

        def complete(prompt, **kwargs):
            listed = prompt.split("Assign tags to the following songs:")[1]
            ids = [int(found) for found in re.findall(r'"id": (\d+)', listed)]
            if order is not None:
                order.append(("request", ids))
            if fail_for is not None and fail_for[0] in ids:
                raise fail_for[1]
            return json.dumps(
                {
                    str(track_id): {
                        "tags": [f"tag-{track_id}"],
                        "environments": ["focus"],
                        "instruments": ["piano"],
                    }
                    for track_id in ids
                }
            )

        provider.complete.side_effect = complete
        return provider

    def test_on_batch_callback_receives_each_batch(self):
        """Five tracks in batches of two: three calls, each with that batch alone."""
        from plexmix.ai.tag_generator import TagGenerator

        order = []
        generator = TagGenerator(self._provider(order=order))
        received = []

        def on_batch(results):
            order.append(("saved", sorted(results)))
            received.append(dict(results))

        results = generator.generate_tags_batch(self._tracks(5), batch_size=2, on_batch=on_batch)

        assert [sorted(batch) for batch in received] == [[1, 2], [3, 4], [5]]
        assert received[0][1] == {
            "tags": ["tag-1"],
            "environments": ["focus"],
            "instruments": ["piano"],
        }
        # Together the batches are the whole result, and nothing arrives twice.
        merged = {}
        for batch in received:
            assert not set(batch) & set(merged)
            merged.update(batch)
        assert merged == results
        # Each batch is handed over before the next request is sent.
        assert order == [
            ("request", [1, 2]),
            ("saved", [1, 2]),
            ("request", [3, 4]),
            ("saved", [3, 4]),
            ("request", [5]),
            ("saved", [5]),
        ]

    def test_on_batch_is_optional(self):
        from plexmix.ai.tag_generator import TagGenerator

        generator = TagGenerator(self._provider())
        results = generator.generate_tags_batch(self._tracks(3), batch_size=2)
        assert sorted(results) == [1, 2, 3]

    def test_failed_batch_arrives_with_empty_results(self):
        """A batch that failed without a fatal error is reported with nothing usable in it."""
        from plexmix.ai.tag_generator import TagGenerator

        generator = TagGenerator(
            self._provider(fail_for=(3, ConnectionError("Connection refused")))
        )
        received = []

        generator.generate_tags_batch(self._tracks(5), batch_size=2, on_batch=received.append)

        assert [sorted(batch) for batch in received] == [[1, 2], [3, 4], [5]]
        empty = {"tags": [], "environments": [], "instruments": []}
        assert received[1] == {3: empty, 4: empty}
        assert received[0][1]["tags"] == ["tag-1"] and received[2][5]["tags"] == ["tag-5"]

    def test_fatal_error_stops_after_the_batches_that_were_handed_over(self):
        """The batches before a fatal error were handed over. The failed one never is."""
        from plexmix.ai.errors import FatalProviderError
        from plexmix.ai.tag_generator import TagGenerator

        generator = TagGenerator(self._provider(fail_for=(3, RuntimeError("401 Unauthorized"))))
        received = []

        with pytest.raises(FatalProviderError) as stopped:
            generator.generate_tags_batch(self._tracks(5), batch_size=2, on_batch=received.append)

        assert [sorted(batch) for batch in received] == [[1, 2]]
        assert sorted(stopped.value.partial_results) == [1, 2]

    def test_error_in_the_callback_stops_the_run(self):
        """A caller that cannot save must not be sent more batches to lose."""
        from plexmix.ai.tag_generator import TagGenerator

        provider = self._provider()
        generator = TagGenerator(provider)

        def on_batch(results):
            raise RuntimeError("the database is locked")

        with pytest.raises(RuntimeError, match="the database is locked"):
            generator.generate_tags_batch(self._tracks(5), batch_size=2, on_batch=on_batch)

        assert provider.complete.call_count == 1


class TestProviderRetry:
    """Tests for provider retry logic."""

    def test_provider_retries_on_rate_limit(self):
        """Test that providers retry on rate limit errors."""
        mock_response = MagicMock()
        mock_response.text = '{"result": "success"}'

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            Exception("429 Too Many Requests"),
            mock_response,
        ]

        with patch("google.genai.Client", return_value=mock_client):
            with patch("time.sleep"):  # Don't actually sleep in tests
                from plexmix.ai.gemini_provider import GeminiProvider

                provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash")

                result = provider.complete("Test prompt")

                assert "success" in result
                # Should have been called twice (first failed, second succeeded)
                assert mock_client.models.generate_content.call_count == 2

    def test_provider_gives_up_after_max_retries(self):
        """Test that providers give up after max retries."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("429 Too Many Requests")

        with patch("google.genai.Client", return_value=mock_client):
            with patch("time.sleep"):
                from plexmix.ai.gemini_provider import GeminiProvider

                provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash")

                with pytest.raises(Exception):
                    provider.complete("Test prompt")

                # Should have tried 3 times (max_retries)
                assert mock_client.models.generate_content.call_count == 3


class TestRepeatedBatchFailure:
    """``max_failed_batches`` stops a run whose provider no longer answers."""

    @staticmethod
    def _tracks(count):
        return [
            {"id": n, "title": f"Track {n}", "artist": "Artist", "genre": "Rock"}
            for n in range(1, count + 1)
        ]

    @staticmethod
    def _provider(failing, error, unreadable=()):
        """Tags every track of a request, unless the request holds a failing id.

        A request that holds an ``unreadable`` id is answered with text that is not JSON.
        """
        import json
        import re

        provider = MagicMock()
        provider.requests = []

        def complete(prompt, **kwargs):
            listed = prompt.split("Assign tags to the following songs:")[1]
            ids = [int(found) for found in re.findall(r'"id": (\d+)', listed)]
            provider.requests.append(ids)
            if set(ids) & set(failing):
                raise error
            if set(ids) & set(unreadable):
                return "I cannot help with that."
            return json.dumps(
                {
                    str(track_id): {
                        "tags": [f"tag-{track_id}"],
                        "environments": ["focus"],
                        "instruments": ["piano"],
                    }
                    for track_id in ids
                }
            )

        provider.complete.side_effect = complete
        return provider

    def test_run_stops_after_the_given_number_of_failed_requests_in_a_row(self):
        from plexmix.ai.errors import FatalProviderError
        from plexmix.ai.tag_generator import RepeatedBatchFailure, TagGenerator

        error = ConnectionError("Connection refused by dummy-api-key-0000")
        provider = self._provider(failing=range(3, 13), error=error)
        generator = TagGenerator(provider)
        received = []

        with pytest.raises(RepeatedBatchFailure) as caught:
            generator.generate_tags_batch(
                self._tracks(12), batch_size=2, on_batch=received.append, max_failed_batches=3
            )

        stopped = caught.value
        # One answered request, three failed ones, and the last two were never sent.
        assert provider.requests == [[1, 2], [3, 4], [5, 6], [7, 8]]
        assert isinstance(stopped, FatalProviderError)
        assert stopped.failed_batches == 3 and stopped.cause is error
        # A fixed sentence: none of the provider's own text, which can hold a key.
        assert stopped.user_message == "The AI provider failed 3 requests in a row."
        assert "dummy-api-key-0000" not in str(stopped)
        # Every batch was handed over before the run stopped, the failed ones empty.
        assert [sorted(batch) for batch in received] == [[1, 2], [3, 4], [5, 6], [7, 8]]
        assert sorted(stopped.partial_results) == [1, 2, 3, 4, 5, 6, 7, 8]
        assert stopped.partial_results[1]["tags"] == ["tag-1"]
        assert stopped.partial_results[3]["tags"] == []

    def test_retryable_error_is_named_by_its_fixed_sentence(self):
        from plexmix.ai.tag_generator import RepeatedBatchFailure, TagGenerator

        provider = self._provider(failing=range(1, 7), error=RuntimeError("Error code: 503"))
        generator = TagGenerator(provider)

        with patch("plexmix.ai.tag_generator.time.sleep"), pytest.raises(
            RepeatedBatchFailure
        ) as caught:
            generator.generate_tags_batch(self._tracks(6), batch_size=2, max_failed_batches=2)

        # Each request was tried three times before its batch was given up.
        assert provider.requests == [[1, 2]] * 3 + [[3, 4]] * 3
        assert caught.value.failed_batches == 2
        assert caught.value.user_message == (
            "The AI provider failed 2 requests in a row. "
            "Temporary AI provider error (timeout, rate limit, or server error)."
        )

    def test_answered_request_ends_the_row(self):
        """Failed, failed, answered, failed, failed: never three in a row."""
        from plexmix.ai.tag_generator import TagGenerator

        error = ConnectionError("Connection refused")
        provider = self._provider(failing=[1, 3, 7, 9], error=error)
        generator = TagGenerator(provider)

        results = generator.generate_tags_batch(
            self._tracks(10), batch_size=2, max_failed_batches=3
        )

        assert len(provider.requests) == 5
        assert results[5]["tags"] == ["tag-5"] and results[1]["tags"] == []

    def test_reply_that_cannot_be_read_is_an_answer(self):
        """The provider answered, so such a batch does not count as a failed request."""
        from plexmix.ai.tag_generator import TagGenerator

        provider = self._provider(failing=(), error=None, unreadable=range(1, 7))
        generator = TagGenerator(provider)

        with patch("plexmix.ai.tag_generator.time.sleep"):
            results = generator.generate_tags_batch(
                self._tracks(6), batch_size=2, max_failed_batches=1
            )

        assert sorted(results) == [1, 2, 3, 4, 5, 6]
        assert all(result["tags"] == [] for result in results.values())

    def test_without_the_argument_every_batch_is_tried(self):
        from plexmix.ai.tag_generator import TagGenerator

        error = ConnectionError("Connection refused")
        provider = self._provider(failing=range(1, 13), error=error)
        generator = TagGenerator(provider)

        results = generator.generate_tags_batch(self._tracks(12), batch_size=2)

        assert len(provider.requests) == 6 and sorted(results) == list(range(1, 13))


def _openai_error(kind):
    """An error of the OpenAI client library, as it raises it for a request that got no answer."""
    import httpx
    import openai

    request = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
    return getattr(openai, kind)(request=request)


NOT_ANSWERED = [
    pytest.param(lambda: RuntimeError("Error code: 500 - internal error"), 3, id="500 text"),
    pytest.param(lambda: RuntimeError("Error code: 429 - rate limit reached"), 3, id="429 text"),
    pytest.param(lambda: _openai_error("APIConnectionError"), 1, id="client connection error"),
    pytest.param(lambda: _openai_error("APITimeoutError"), 3, id="client timeout"),
    pytest.param(lambda: ConnectionError("Connection refused"), 1, id="built-in ConnectionError"),
    pytest.param(lambda: ConnectionResetError("Connection reset by peer"), 1, id="closed"),
    pytest.param(lambda: TimeoutError("no answer"), 1, id="built-in TimeoutError"),
    pytest.param(lambda: RuntimeError("worker gone"), 1, id="any other error"),
    # The type says "a reply that could not be used", and the text says a timeout.
    pytest.param(lambda: ValueError("the request timed out"), 3, id="timeout in a ValueError"),
]

ANSWERED_WITHOUT_TEXT = [
    pytest.param(ValueError("Could not extract text from Gemini response"), id="Gemini"),
    pytest.param(ValueError("Empty response from OpenAI"), id="OpenAI"),
    pytest.param(ValueError("Empty response from custom endpoint"), id="custom endpoint"),
    pytest.param(ValueError("Empty text response from Gemini"), id="Gemini, no text"),
    pytest.param(IndexError("list index out of range"), id="IndexError"),
    pytest.param(KeyError("choices"), id="KeyError"),
    pytest.param(TypeError("'NoneType' object is not subscriptable"), id="TypeError"),
    pytest.param(AttributeError("'NoneType' object has no attribute 'text'"), id="AttributeError"),
]


class TestRequestsThatWereAnswered:
    """Only a request the provider did not answer counts toward the stop.

    A provider wrapper raises a built-in error for a reply without text (a content
    filter answers like that). Such a request was answered, like one whose reply is
    not JSON: its tracks get nothing, the row of failures ends and the run goes on.
    Three of them side by side must not stop a run for good.
    """

    _tracks = staticmethod(TestRepeatedBatchFailure._tracks)
    _provider = staticmethod(TestRepeatedBatchFailure._provider)

    @pytest.mark.parametrize("make_error, attempts", NOT_ANSWERED)
    def test_three_requests_without_an_answer_stop_the_run(self, make_error, attempts):
        from plexmix.ai.tag_generator import (
            RepeatedBatchFailure,
            TagGenerator,
            provider_did_not_answer,
        )

        error = make_error()
        assert provider_did_not_answer(error) is True
        provider = self._provider(failing=range(3, 13), error=error)
        generator = TagGenerator(provider)

        with patch("plexmix.ai.tag_generator.time.sleep"), pytest.raises(
            RepeatedBatchFailure
        ) as caught:
            generator.generate_tags_batch(self._tracks(12), batch_size=2, max_failed_batches=3)

        # One answered request, then three batches that got no answer. The rest was not sent.
        assert provider.requests == [[1, 2]] + [
            batch for batch in ([3, 4], [5, 6], [7, 8]) for _ in range(attempts)
        ]
        assert caught.value.failed_batches == 3 and caught.value.cause is error
        assert caught.value.partial_results[1]["tags"] == ["tag-1"]

    @pytest.mark.parametrize("error", ANSWERED_WITHOUT_TEXT)
    def test_replies_without_text_never_stop_the_run(self, error):
        from plexmix.ai.tag_generator import TagGenerator, provider_did_not_answer

        assert provider_did_not_answer(error) is False
        # Three batches side by side each hold one track the provider gives no text for.
        provider = self._provider(failing=[3, 5, 7], error=error)
        generator = TagGenerator(provider)
        received = []

        results = generator.generate_tags_batch(
            self._tracks(12), batch_size=2, on_batch=received.append, max_failed_batches=3
        )

        # Every batch was sent once, also the ones behind the three.
        assert provider.requests == [[n, n + 1] for n in range(1, 12, 2)]
        assert [sorted(batch) for batch in received] == provider.requests
        assert results[9]["tags"] == ["tag-9"] and results[11]["tags"] == ["tag-11"]
        for track_id in (3, 4, 5, 6, 7, 8):
            assert results[track_id] == {"tags": [], "environments": [], "instruments": []}
        assert generator._last_batch_error is None

    def test_answer_without_text_ends_the_row_of_failed_requests(self):
        """Failed, failed, answered without text, then three failed: stops after the sixth."""
        from plexmix.ai.tag_generator import RepeatedBatchFailure, TagGenerator

        refused = ConnectionError("Connection refused")
        no_text = ValueError("Could not extract text from Gemini response")
        provider = MagicMock()
        provider.requests = []

        def complete(prompt, **kwargs):
            number = len(provider.requests) + 1
            provider.requests.append(number)
            raise no_text if number == 3 else refused

        provider.complete.side_effect = complete
        generator = TagGenerator(provider)

        with pytest.raises(RepeatedBatchFailure) as caught:
            generator.generate_tags_batch(self._tracks(20), batch_size=2, max_failed_batches=3)

        assert provider.requests == [1, 2, 3, 4, 5, 6]
        assert caught.value.failed_batches == 3 and caught.value.cause is refused
        assert sorted(caught.value.partial_results) == list(range(1, 13))

    def test_without_the_argument_nothing_stops_the_run_whatever_fails(self):
        """The other callers (a plain sync, ``tags generate``, the web UI) try every batch."""
        from plexmix.ai.tag_generator import TagGenerator

        for error in (ConnectionError("refused"), ValueError("Empty response from OpenAI")):
            provider = self._provider(failing=range(1, 13), error=error)
            received = []

            results = TagGenerator(provider).generate_tags_batch(
                self._tracks(12), batch_size=2, on_batch=received.append
            )

            assert len(provider.requests) == 6 and len(received) == 6
            assert sorted(results) == list(range(1, 13))
            assert all(result["tags"] == [] for result in results.values())

    def test_fatal_error_still_stops_at_once(self):
        from plexmix.ai.errors import FatalProviderError
        from plexmix.ai.tag_generator import RepeatedBatchFailure, TagGenerator

        provider = self._provider(failing=range(3, 13), error=ValueError("401 Unauthorized"))

        with pytest.raises(FatalProviderError) as caught:
            TagGenerator(provider).generate_tags_batch(
                self._tracks(12), batch_size=2, max_failed_batches=3
            )

        assert not isinstance(caught.value, RepeatedBatchFailure)
        assert provider.requests == [[1, 2], [3, 4]]


class TestCleanTagLists:
    """Only texts with content are tags. A list of blanks is an empty list."""

    @pytest.mark.parametrize(
        "value, cleaned",
        [
            ([""], []),
            ([" "], []),
            (["", ""], []),
            ([None], []),
            ([{"a": 1}], []),
            ([["nested"]], []),
            ([True, False], []),
            ([1, 2], ["1", "2"]),
            ([1.5], ["1.5"]),
            (["x", ""], ["x"]),
            (["  Calm ", "\t", "late night"], ["Calm", "late night"]),
            (["a", None, 3, {"b": 1}, " c "], ["a", "3", "c"]),
            ([], []),
            (None, []),
            ("energetic", []),
            (5, []),
            ({"tags": ["x"]}, []),
            (("x", "y"), []),
        ],
    )
    def test_clean_tag_list(self, value, cleaned):
        from plexmix.ai.tag_generator import clean_tag_list

        assert clean_tag_list(value) == cleaned

    @staticmethod
    def _parsed(entry):
        import json

        from plexmix.ai.tag_generator import TagGenerator

        tracks = [{"id": 1, "title": "Track 1", "artist": "Artist", "genre": "Rock"}]
        return TagGenerator(MagicMock())._parse_tag_response(json.dumps({"1": entry}), tracks)[1]

    @pytest.mark.parametrize("column", ["tags", "environments", "instruments"])
    @pytest.mark.parametrize(
        "items, cleaned",
        [
            ([""], []),
            ([" "], []),
            (["", ""], []),
            ([None], []),
            ([{"a": 1}], []),
            ([1, 2], ["1", "2"]),
            (["x", ""], ["x"]),
            (["  Calm ", " "], ["calm"]),
        ],
    )
    def test_parser_hands_on_clean_lists_for_every_column(self, column, items, cleaned):
        entry = {"tags": ["rock"], "environments": ["focus"], "instruments": ["piano"]}
        entry[column] = items

        parsed = self._parsed(entry)

        assert parsed[column] == cleaned
        # The other two columns are as the reply gave them.
        others = {name: value for name, value in parsed.items() if name != column}
        assert others == {name: value for name, value in entry.items() if name != column}
        # Never the text of something that is no text.
        assert "none" not in parsed[column] and not any("{" in item for item in parsed[column])

    def test_caps_count_the_items_that_are_left(self):
        parsed = self._parsed(
            {
                "tags": ["", "a", " ", "b", None, "c", "d", "e", "f"],
                "environments": ["", "work", "study", "focus", "relax"],
                "instruments": [None, "piano", "", "bass", "drums", "synth"],
            }
        )

        assert parsed["tags"] == ["a", "b", "c", "d", "e"]
        assert parsed["environments"] == ["work", "study", "focus"]
        assert parsed["instruments"] == ["piano", "bass", "drums"]

    def test_one_text_in_place_of_a_list(self):
        """As before: one environment or instrument as a text is a list of one."""
        parsed = self._parsed({"tags": "rock", "environments": " Focus ", "instruments": " "})

        assert parsed == {"tags": [], "environments": ["focus"], "instruments": []}

    def test_bare_list_is_a_list_of_tags(self):
        parsed = self._parsed(["Rock", "", None, " loud "])

        assert parsed == {"tags": ["rock", "loud"], "environments": [], "instruments": []}
