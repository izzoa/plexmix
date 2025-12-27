import pytest
import json
from unittest.mock import Mock, patch, MagicMock

from plexmix.ai.tag_generator import TagGenerator
from plexmix.ai.base import AIProvider


class MockAIProvider(AIProvider):
    def __init__(self):
        self.model = "mock-model"

    def complete(self, prompt, temperature=None, max_tokens=4096, timeout=30):
        """Mock complete method that returns a simple JSON response."""
        return '{"1": {"tags": ["test"], "environments": ["work"], "instruments": ["piano"]}}'

    def generate_playlist(self, mood_query, candidates, max_tracks):
        return [c['id'] for c in candidates[:max_tracks]]


@pytest.fixture
def mock_ai_provider():
    return MockAIProvider()


@pytest.fixture
def tag_generator(mock_ai_provider):
    return TagGenerator(mock_ai_provider)


def test_tag_generator_initialization(tag_generator, mock_ai_provider):
    assert tag_generator.ai_provider == mock_ai_provider


def test_prepare_tag_prompt(tag_generator):
    tracks = [
        {'id': 1, 'title': 'So What', 'artist': 'Miles Davis', 'genre': 'Jazz'},
        {'id': 2, 'title': 'Blue in Green', 'artist': 'Miles Davis', 'genre': 'Jazz'}
    ]

    prompt = tag_generator._prepare_tag_prompt(tracks)

    assert 'So What' in prompt
    assert 'Miles Davis' in prompt
    assert 'Jazz' in prompt
    assert 'tags' in prompt.lower()
    assert 'environments' in prompt.lower()
    assert 'instruments' in prompt.lower()


def test_parse_tag_response_valid_json(tag_generator):
    tracks = [
        {'id': 1, 'title': 'Track 1', 'artist': 'Artist', 'genre': 'Jazz'},
        {'id': 2, 'title': 'Track 2', 'artist': 'Artist', 'genre': 'Rock'}
    ]

    response = json.dumps({
        "1": {
            "tags": ["energetic", "upbeat", "happy"],
            "environments": ["party", "workout"],
            "instruments": ["guitar", "drums"]
        },
        "2": {
            "tags": ["mellow", "slow", "sad"],
            "environments": ["relax", "sleep"],
            "instruments": ["piano", "strings"]
        }
    })

    result = tag_generator._parse_tag_response(response, tracks)

    assert len(result) == 2
    assert result[1]['tags'] == ["energetic", "upbeat", "happy"]
    assert result[1]['environments'] == ["party", "workout"]
    assert result[1]['instruments'] == ["guitar", "drums"]
    assert result[2]['tags'] == ["mellow", "slow", "sad"]


def test_parse_tag_response_with_code_blocks(tag_generator):
    tracks = [{'id': 1, 'title': 'Track', 'artist': 'Artist', 'genre': 'Jazz'}]

    response = '''```json
{
  "1": {
    "tags": ["jazz", "smooth"],
    "environments": ["relax"],
    "instruments": ["saxophone"]
  }
}
```'''

    result = tag_generator._parse_tag_response(response, tracks)

    assert len(result) == 1
    assert result[1]['tags'] == ["jazz", "smooth"]


def test_parse_tag_response_missing_track(tag_generator):
    tracks = [
        {'id': 1, 'title': 'Track 1', 'artist': 'Artist', 'genre': 'Jazz'},
        {'id': 2, 'title': 'Track 2', 'artist': 'Artist', 'genre': 'Rock'}
    ]

    response = json.dumps({
        "1": {
            "tags": ["energetic"],
            "environments": ["party"],
            "instruments": ["guitar"]
        }
    })

    result = tag_generator._parse_tag_response(response, tracks)

    assert len(result) == 2
    assert result[1]['tags'] == ["energetic"]
    assert result[2]['tags'] == []
    assert result[2]['environments'] == []
    assert result[2]['instruments'] == []


def test_parse_tag_response_legacy_format(tag_generator):
    tracks = [{'id': 1, 'title': 'Track', 'artist': 'Artist', 'genre': 'Jazz'}]

    response = json.dumps({
        "1": ["jazz", "smooth", "mellow"]
    })

    result = tag_generator._parse_tag_response(response, tracks)

    assert len(result) == 1
    assert result[1]['tags'] == ["jazz", "smooth", "mellow"]
    assert result[1]['environments'] == []
    assert result[1]['instruments'] == []


def test_parse_tag_response_limits_to_5_tags(tag_generator):
    tracks = [{'id': 1, 'title': 'Track', 'artist': 'Artist', 'genre': 'Jazz'}]

    response = json.dumps({
        "1": {
            "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7"],
            "environments": ["env1", "env2", "env3", "env4"],
            "instruments": ["inst1", "inst2", "inst3", "inst4"]
        }
    })

    result = tag_generator._parse_tag_response(response, tracks)

    assert len(result[1]['tags']) == 5
    assert len(result[1]['environments']) == 3
    assert len(result[1]['instruments']) == 3


def test_parse_tag_response_handles_string_environments(tag_generator):
    tracks = [{'id': 1, 'title': 'Track', 'artist': 'Artist', 'genre': 'Jazz'}]

    response = json.dumps({
        "1": {
            "tags": ["jazz"],
            "environments": "relax",
            "instruments": "piano"
        }
    })

    result = tag_generator._parse_tag_response(response, tracks)

    assert result[1]['environments'] == ["relax"]
    assert result[1]['instruments'] == ["piano"]


def test_parse_tag_response_invalid_json_raises(tag_generator):
    tracks = [{'id': 1, 'title': 'Track', 'artist': 'Artist', 'genre': 'Jazz'}]

    response = "{ invalid json }"

    with pytest.raises(json.JSONDecodeError):
        tag_generator._parse_tag_response(response, tracks)


def test_parse_tag_response_cleans_trailing_commas(tag_generator):
    tracks = [{'id': 1, 'title': 'Track', 'artist': 'Artist', 'genre': 'Jazz'}]

    response = '''{
  "1": {
    "tags": ["jazz", "smooth",],
    "environments": ["relax",],
    "instruments": ["piano",]
  },
}'''

    result = tag_generator._parse_tag_response(response, tracks)

    assert result[1]['tags'] == ["jazz", "smooth"]


def test_mock_ai_provider_generates_playlist():
    provider = MockAIProvider()

    candidates = [
        {'id': 1, 'title': 'Track 1'},
        {'id': 2, 'title': 'Track 2'},
        {'id': 3, 'title': 'Track 3'}
    ]

    result = provider.generate_playlist("mood", candidates, 2)

    assert len(result) == 2
    assert result == [1, 2]
