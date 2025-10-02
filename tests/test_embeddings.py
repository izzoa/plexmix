import pytest
from unittest.mock import Mock, patch

from plexmix.utils.embeddings import create_track_text, EmbeddingGenerator


def test_create_track_text_basic():
    track_data = {
        'title': 'So What',
        'artist': 'Miles Davis',
        'album': 'Kind of Blue'
    }

    text = create_track_text(track_data)

    assert 'So What' in text
    assert 'Miles Davis' in text
    assert 'Kind of Blue' in text


def test_create_track_text_with_metadata():
    track_data = {
        'title': 'So What',
        'artist': 'Miles Davis',
        'album': 'Kind of Blue',
        'genre': 'Jazz',
        'year': 1959,
        'tags': 'mellow, sophisticated, smooth',
        'environments': 'relax, study, focus',
        'instruments': 'piano, bass, drums'
    }

    text = create_track_text(track_data)

    assert 'Jazz' in text
    assert '1959' in text
    assert 'mellow' in text
    assert 'relax' in text
    assert 'piano' in text


def test_create_track_text_missing_fields():
    track_data = {
        'title': 'Track',
        'artist': 'Artist'
    }

    text = create_track_text(track_data)

    assert 'Track' in text
    assert 'Artist' in text
    assert 'Unknown Album' in text


def test_create_track_text_empty_optional_fields():
    track_data = {
        'title': 'Track',
        'artist': 'Artist',
        'album': 'Album',
        'genre': '',
        'year': '',
        'tags': '',
        'environments': '',
        'instruments': ''
    }

    text = create_track_text(track_data)

    assert 'Track' in text
    assert 'Artist' in text
    assert 'Album' in text


@pytest.fixture
def mock_gemini_model():
    with patch('plexmix.utils.embeddings.genai') as mock_genai:
        mock_result = Mock()
        mock_result.embedding = [0.1] * 3072
        mock_genai.embed_content.return_value = {'embedding': [0.1] * 3072}
        yield mock_genai


def test_embedding_generator_local():
    generator = EmbeddingGenerator(provider='local')

    assert generator.provider_name == 'local'
    assert generator.get_dimension() == 384


def test_embedding_generator_gemini_dimension():
    with patch('google.generativeai.configure'):
        generator = EmbeddingGenerator(provider='gemini', api_key='test-key')
        assert generator.get_dimension() == 3072
        assert generator.provider_name == 'gemini'


def test_embedding_generator_openai_dimension():
    with patch('openai.OpenAI'):
        generator = EmbeddingGenerator(provider='openai', api_key='test-key')
        assert generator.get_dimension() == 1536
        assert generator.provider_name == 'openai'


def test_embedding_generator_cohere_dimension():
    with patch('cohere.ClientV2'):
        generator = EmbeddingGenerator(provider='cohere', api_key='test-key')
        assert generator.get_dimension() == 1024
        assert generator.provider_name == 'cohere'


def test_embedding_generator_cohere_custom_dimension():
    with patch('cohere.ClientV2'):
        from plexmix.utils.embeddings import CohereEmbeddingProvider
        provider = CohereEmbeddingProvider(api_key='test-key', output_dimension=512)
        assert provider.get_dimension() == 512


def test_embedding_generator_local_generate():
    generator = EmbeddingGenerator(provider='local')

    embedding = generator.generate_embedding("test text")

    assert isinstance(embedding, list)
    assert len(embedding) == 384
    assert all(isinstance(x, float) for x in embedding)


def test_embedding_generator_local_batch():
    generator = EmbeddingGenerator(provider='local')

    texts = ["text 1", "text 2", "text 3"]
    embeddings = generator.generate_batch_embeddings(texts)

    assert len(embeddings) == 3
    assert all(len(emb) == 384 for emb in embeddings)


def test_embedding_generator_invalid_provider():
    with pytest.raises(ValueError, match="Unknown provider"):
        EmbeddingGenerator(provider='invalid')
