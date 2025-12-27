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
        with patch('google.generativeai.GenerativeModel') as mock_model_class:
            # Setup mock
            mock_response = MagicMock()
            mock_response.text = '{"1": {"tags": ["energetic", "upbeat"], "environments": ["workout"], "instruments": ["guitar"]}}'
            mock_model = MagicMock()
            mock_model.generate_content.return_value = mock_response
            mock_model_class.return_value = mock_model

            with patch('google.generativeai.configure'):
                from plexmix.ai.gemini_provider import GeminiProvider
                provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash")

                result = provider.complete("Test prompt", temperature=0.3, max_tokens=1000)

                assert '{"1":' in result
                mock_model.generate_content.assert_called_once()

    def test_openai_provider_complete(self):
        """Test OpenAIProvider.complete() with mocked API."""
        with patch('openai.OpenAI') as mock_client_class:
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
        with patch('anthropic.Anthropic') as mock_client_class:
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
        with patch('cohere.ClientV2') as mock_client_class:
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
        mock_provider.complete.return_value = '''
        {
            "1": {"tags": ["energetic", "upbeat"], "environments": ["workout"], "instruments": ["drums"]},
            "2": {"tags": ["calm", "peaceful"], "environments": ["relax"], "instruments": ["piano"]}
        }
        '''

        generator = TagGenerator(mock_provider)

        tracks = [
            {'id': 1, 'title': 'Track 1', 'artist': 'Artist 1', 'genre': 'Rock'},
            {'id': 2, 'title': 'Track 2', 'artist': 'Artist 2', 'genre': 'Classical'}
        ]

        results = generator._generate_batch(tracks)

        # Verify complete() was called
        mock_provider.complete.assert_called_once()

        # Verify results
        assert 1 in results
        assert 2 in results
        assert 'energetic' in results[1]['tags']
        assert 'calm' in results[2]['tags']

    def test_tag_generator_handles_json_errors(self):
        """Test that TagGenerator handles malformed JSON gracefully."""
        from plexmix.ai.tag_generator import TagGenerator

        mock_provider = MagicMock()
        # Return malformed JSON that will fail to parse
        mock_provider.complete.return_value = "This is not valid JSON {{"

        generator = TagGenerator(mock_provider)

        tracks = [
            {'id': 1, 'title': 'Track 1', 'artist': 'Artist 1', 'genre': 'Rock'}
        ]

        # Should return empty results, not crash
        results = generator._generate_batch(tracks)

        assert 1 in results
        assert results[1]['tags'] == []


class TestProviderRetry:
    """Tests for provider retry logic."""

    def test_provider_retries_on_rate_limit(self):
        """Test that providers retry on rate limit errors."""
        with patch('google.generativeai.GenerativeModel') as mock_model_class:
            # First call raises rate limit error, second succeeds
            mock_response = MagicMock()
            mock_response.text = '{"result": "success"}'

            mock_model = MagicMock()
            mock_model.generate_content.side_effect = [
                Exception("429 Too Many Requests"),
                mock_response
            ]
            mock_model_class.return_value = mock_model

            with patch('google.generativeai.configure'):
                with patch('time.sleep'):  # Don't actually sleep in tests
                    from plexmix.ai.gemini_provider import GeminiProvider
                    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash")

                    result = provider.complete("Test prompt")

                    assert "success" in result
                    # Should have been called twice (first failed, second succeeded)
                    assert mock_model.generate_content.call_count == 2

    def test_provider_gives_up_after_max_retries(self):
        """Test that providers give up after max retries."""
        with patch('google.generativeai.GenerativeModel') as mock_model_class:
            mock_model = MagicMock()
            mock_model.generate_content.side_effect = Exception("429 Too Many Requests")
            mock_model_class.return_value = mock_model

            with patch('google.generativeai.configure'):
                with patch('time.sleep'):
                    from plexmix.ai.gemini_provider import GeminiProvider
                    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash")

                    with pytest.raises(Exception):
                        provider.complete("Test prompt")

                    # Should have tried 3 times (max_retries)
                    assert mock_model.generate_content.call_count == 3
