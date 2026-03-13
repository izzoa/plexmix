import pytest
from unittest.mock import Mock, patch, MagicMock
import sys


@pytest.fixture
def mock_cohere_module():
    mock_cohere = MagicMock()
    mock_client = MagicMock()
    mock_cohere.ClientV2.return_value = mock_client
    sys.modules['cohere'] = mock_cohere
    yield mock_client
    sys.modules.pop('cohere', None)


def test_cohere_provider_initialization(mock_cohere_module):
    from plexmix.ai.cohere_provider import CohereProvider

    provider = CohereProvider(api_key='test-key')

    assert provider.api_key == 'test-key'
    assert provider.model == 'command-r7b-12-2024'
    assert provider.temperature == 0.3


def test_cohere_provider_custom_model(mock_cohere_module):
    from plexmix.ai.cohere_provider import CohereProvider

    provider = CohereProvider(
        api_key='test-key',
        model='command-r-plus-08-2024',
        temperature=0.5
    )

    assert provider.model == 'command-r-plus-08-2024'
    assert provider.temperature == 0.5


def test_cohere_provider_complete(mock_cohere_module):
    from plexmix.ai.cohere_provider import CohereProvider

    mock_response = Mock()
    mock_message = Mock()
    mock_content = Mock()
    mock_content.text = 'Test response text'
    mock_message.content = [mock_content]
    mock_response.message = mock_message
    mock_cohere_module.chat.return_value = mock_response

    provider = CohereProvider(api_key='test-key')
    result = provider.complete('test prompt')

    assert result == 'Test response text'
    mock_cohere_module.chat.assert_called_once()


def test_cohere_provider_complete_empty_response(mock_cohere_module):
    from plexmix.ai.cohere_provider import CohereProvider

    mock_response = Mock()
    mock_response.message = None
    mock_cohere_module.chat.return_value = mock_response

    provider = CohereProvider(api_key='test-key')

    with pytest.raises(ValueError, match="Empty response"):
        provider.complete('test prompt')


@patch('plexmix.ai.cohere_provider.time.sleep')
def test_cohere_provider_complete_retries_on_rate_limit(mock_sleep, mock_cohere_module):
    from plexmix.ai.cohere_provider import CohereProvider

    mock_response = Mock()
    mock_message = Mock()
    mock_content = Mock()
    mock_content.text = 'Success'
    mock_message.content = [mock_content]
    mock_response.message = mock_message

    mock_cohere_module.chat.side_effect = [
        Exception('429 rate limit'),
        mock_response,
    ]

    provider = CohereProvider(api_key='test-key')
    result = provider.complete('test prompt')

    assert result == 'Success'
    assert mock_cohere_module.chat.call_count == 2
    mock_sleep.assert_called_once()


def test_cohere_embedding_provider_initialization(mock_cohere_module):
    from plexmix.utils.embeddings import CohereEmbeddingProvider

    provider = CohereEmbeddingProvider(api_key='test-key')

    assert provider.model_name == 'embed-v4.0'
    assert provider.dimension == 1024


def test_cohere_embedding_provider_custom_dimensions(mock_cohere_module):
    from plexmix.utils.embeddings import CohereEmbeddingProvider

    for dim in [256, 512, 1024, 1536]:
        provider = CohereEmbeddingProvider(api_key='test-key', output_dimension=dim)
        assert provider.dimension == dim


def test_cohere_embedding_provider_generate_embedding(mock_cohere_module):
    from plexmix.utils.embeddings import CohereEmbeddingProvider

    mock_response = Mock()
    mock_embeddings = Mock()
    mock_embeddings.float_ = [[0.1] * 1024]
    mock_response.embeddings = mock_embeddings
    mock_cohere_module.embed.return_value = mock_response

    provider = CohereEmbeddingProvider(api_key='test-key')

    embedding = provider.generate_embedding('test text')

    assert len(embedding) == 1024
    assert embedding == [0.1] * 1024
    mock_cohere_module.embed.assert_called_once()
    call_args = mock_cohere_module.embed.call_args
    assert call_args.kwargs['model'] == 'embed-v4.0'
    assert call_args.kwargs['texts'] == ['test text']
    assert call_args.kwargs['input_type'] == 'search_document'
    assert call_args.kwargs['output_dimension'] == 1024


def test_cohere_embedding_provider_batch_embeddings(mock_cohere_module):
    from plexmix.utils.embeddings import CohereEmbeddingProvider

    mock_response = Mock()
    mock_embeddings = Mock()
    mock_embeddings.float_ = [[0.1] * 1024, [0.2] * 1024, [0.3] * 1024]
    mock_response.embeddings = mock_embeddings
    mock_cohere_module.embed.return_value = mock_response

    provider = CohereEmbeddingProvider(api_key='test-key')

    texts = ['text 1', 'text 2', 'text 3']
    embeddings = provider.generate_batch_embeddings(texts)

    assert len(embeddings) == 3
    assert embeddings[0] == [0.1] * 1024
    assert embeddings[1] == [0.2] * 1024
    assert embeddings[2] == [0.3] * 1024


def test_cohere_embedding_provider_batch_respects_batch_size(mock_cohere_module):
    from plexmix.utils.embeddings import CohereEmbeddingProvider

    mock_response = Mock()
    mock_embeddings = Mock()
    mock_embeddings.float_ = [[0.1] * 1024] * 50
    mock_response.embeddings = mock_embeddings
    mock_cohere_module.embed.return_value = mock_response

    provider = CohereEmbeddingProvider(api_key='test-key')

    texts = ['text'] * 200
    embeddings = provider.generate_batch_embeddings(texts, batch_size=50)

    assert len(embeddings) == 200
    assert mock_cohere_module.embed.call_count == 4


def test_get_ai_provider_cohere(mock_cohere_module):
    from plexmix.ai import get_ai_provider

    provider = get_ai_provider('cohere', api_key='test-key')

    assert provider.model == 'command-r7b-12-2024'
    assert provider.temperature == 0.7


def test_get_ai_provider_cohere_custom_model(mock_cohere_module):
    from plexmix.ai import get_ai_provider

    provider = get_ai_provider(
        'cohere',
        api_key='test-key',
        model='command-r-08-2024',
        temperature=0.5
    )

    assert provider.model == 'command-r-08-2024'
    assert provider.temperature == 0.5


def test_cohere_provider_complete_raises_on_non_retryable(mock_cohere_module):
    from plexmix.ai.cohere_provider import CohereProvider

    mock_cohere_module.chat.side_effect = Exception('Invalid API key')

    provider = CohereProvider(api_key='test-key')

    with pytest.raises(Exception, match="Invalid API key"):
        provider.complete('test prompt')
