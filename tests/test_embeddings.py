import pytest
import sys
from unittest.mock import Mock, patch, MagicMock

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
    mock_client = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.values = [0.1] * 3072
    mock_response = MagicMock()
    mock_response.embeddings = [mock_embedding]
    mock_client.models.embed_content.return_value = mock_response
    with patch('google.genai.Client', return_value=mock_client):
        yield mock_client


def test_embedding_generator_local():
    generator = EmbeddingGenerator(provider='local')

    assert generator.provider_name == 'local'
    assert generator.get_dimension() == 384


def test_embedding_generator_gemini_dimension():
    mock_client = MagicMock()
    with patch('google.genai.Client', return_value=mock_client):
        generator = EmbeddingGenerator(provider='gemini', api_key='test-key')
        assert generator.get_dimension() == 3072
        assert generator.provider_name == 'gemini'


def test_embedding_generator_openai_dimension():
    with patch('openai.OpenAI'):
        generator = EmbeddingGenerator(provider='openai', api_key='test-key')
        assert generator.get_dimension() == 1536
        assert generator.provider_name == 'openai'


def test_embedding_generator_cohere_dimension():
    mock_cohere = MagicMock()
    mock_client = MagicMock()
    mock_cohere.ClientV2.return_value = mock_client
    sys.modules['cohere'] = mock_cohere

    try:
        generator = EmbeddingGenerator(provider='cohere', api_key='test-key')
        assert generator.get_dimension() == 1024
        assert generator.provider_name == 'cohere'
    finally:
        sys.modules.pop('cohere', None)


def test_embedding_generator_cohere_custom_dimension():
    mock_cohere = MagicMock()
    mock_client = MagicMock()
    mock_cohere.ClientV2.return_value = mock_client
    sys.modules['cohere'] = mock_cohere

    try:
        from plexmix.utils.embeddings import CohereEmbeddingProvider
        provider = CohereEmbeddingProvider(api_key='test-key', output_dimension=512)
        assert provider.get_dimension() == 512
    finally:
        sys.modules.pop('cohere', None)


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


def test_create_track_text_with_audio_features():
    track_data = {
        'title': 'Uptown Funk',
        'artist': 'Bruno Mars',
        'album': 'Uptown Special',
        'genre': 'Pop',
    }
    audio_features = {
        'tempo': 115.0,
        'key': 'D',
        'scale': 'minor',
        'energy_level': 'high',
        'danceability': 0.85,
    }

    text = create_track_text(track_data, audio_features)

    assert '115 bpm' in text
    assert 'medium' in text  # 115 bpm -> medium pace
    assert 'D minor' in text
    assert 'high energy' in text
    assert 'very danceable' in text


def test_create_track_text_audio_features_none():
    track_data = {
        'title': 'Track',
        'artist': 'Artist',
        'album': 'Album',
    }

    text_without = create_track_text(track_data, None)
    text_plain = create_track_text(track_data)
    assert text_without == text_plain
    assert 'audio:' not in text_without


def test_create_track_text_partial_audio_features():
    track_data = {
        'title': 'Track',
        'artist': 'Artist',
        'album': 'Album',
    }
    audio_features = {'tempo': 90.0}

    text = create_track_text(track_data, audio_features)

    assert '90 bpm' in text
    assert 'audio:' in text
