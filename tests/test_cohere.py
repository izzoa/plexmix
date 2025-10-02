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


def test_cohere_provider_generate_playlist(mock_cohere_module):
    from plexmix.ai.cohere_provider import CohereProvider

    mock_response = Mock()
    mock_message = Mock()
    mock_content = Mock()
    mock_content.text = '[1, 2, 3]'
    mock_message.content = [mock_content]
    mock_response.message = mock_message
    mock_cohere_module.chat.return_value = mock_response

    provider = CohereProvider(api_key='test-key')

    candidates = [
        {'id': 1, 'title': 'Track 1', 'artist': 'Artist 1'},
        {'id': 2, 'title': 'Track 2', 'artist': 'Artist 2'},
        {'id': 3, 'title': 'Track 3', 'artist': 'Artist 3'}
    ]

    result = provider.generate_playlist('upbeat mood', candidates, 3)

    assert result == [1, 2, 3]
    mock_cohere_module.chat.assert_called_once()
    call_args = mock_cohere_module.chat.call_args
    assert call_args.kwargs['model'] == 'command-r7b-12-2024'
    assert call_args.kwargs['temperature'] == 0.3


def test_cohere_provider_handles_empty_response(mock_cohere_module):
    from plexmix.ai.cohere_provider import CohereProvider

    mock_response = Mock()
    mock_response.message = None
    mock_cohere_module.chat.return_value = mock_response

    provider = CohereProvider(api_key='test-key')

    candidates = [{'id': 1, 'title': 'Track 1', 'artist': 'Artist 1'}]

    result = provider.generate_playlist('mood', candidates, 1)

    assert result == []


def test_cohere_provider_handles_exception(mock_cohere_module):
    from plexmix.ai.cohere_provider import CohereProvider

    mock_cohere_module.chat.side_effect = Exception('API Error')

    provider = CohereProvider(api_key='test-key')

    candidates = [{'id': 1, 'title': 'Track 1', 'artist': 'Artist 1'}]

    result = provider.generate_playlist('mood', candidates, 1)

    assert result == []


def test_cohere_embedding_provider_initialization(mock_cohere_module):
    from plexmix.utils.embeddings import CohereEmbeddingProvider

    provider = CohereEmbeddingProvider(api_key='test-key')

    assert provider.model_name == 'embed-v4'
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
    assert call_args.kwargs['model'] == 'embed-v4'
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


def test_cohere_provider_max_candidates(mock_cohere_module):
    from plexmix.ai.cohere_provider import CohereProvider

    provider = CohereProvider(api_key='test-key', model='command-r7b-12-2024')
    assert provider.get_max_candidates() == 500

    provider = CohereProvider(api_key='test-key', model='command-r-plus-08-2024')
    assert provider.get_max_candidates() == 500

    provider = CohereProvider(api_key='test-key', model='command-r-08-2024')
    assert provider.get_max_candidates() == 400
