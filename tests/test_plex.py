import pytest
from unittest.mock import Mock, MagicMock, patch

from plexmix.plex.client import PlexClient
from plexmix.database.models import Artist, Album, Track


def test_plex_client_initialization():
    with patch('plexmix.plex.client.PlexServer'):
        client = PlexClient(url="http://localhost:32400", token="test-token")
        assert client.url == "http://localhost:32400"
        assert client.token == "test-token"


def test_extract_artist_metadata():
    with patch('plexmix.plex.client.PlexServer'):
        client = PlexClient(url="http://localhost:32400", token="test-token")

    plex_artist = Mock()
    plex_artist.ratingKey = "12345"
    plex_artist.title = "Miles Davis"
    plex_artist.genres = [Mock(tag="Jazz"), Mock(tag="Bebop")]
    plex_artist.summary = "Legendary jazz musician"

    artist = client.extract_artist_metadata(plex_artist)

    assert isinstance(artist, Artist)
    assert artist.plex_key == "12345"
    assert artist.name == "Miles Davis"
    assert artist.genre == "Jazz, Bebop"
    assert artist.bio == "Legendary jazz musician"


def test_extract_album_metadata():
    with patch('plexmix.plex.client.PlexServer'):
        client = PlexClient(url="http://localhost:32400", token="test-token")

    plex_album = Mock()
    plex_album.ratingKey = "67890"
    plex_album.title = "Kind of Blue"
    plex_album.year = 1959
    plex_album.genres = [Mock(tag="Jazz")]
    plex_album.thumb = "http://example.com/cover.jpg"
    plex_album.artist.return_value = None

    album = client.extract_album_metadata(plex_album)

    assert isinstance(album, Album)
    assert album.plex_key == "67890"
    assert album.title == "Kind of Blue"
    assert album.year == 1959
    assert album.genre == "Jazz"
    assert album.cover_art_url == "http://example.com/cover.jpg"


def test_extract_track_metadata():
    with patch('plexmix.plex.client.PlexServer'):
        client = PlexClient(url="http://localhost:32400", token="test-token")

    plex_track = Mock()
    plex_track.ratingKey = "11111"
    plex_track.title = "So What"
    plex_track.duration = 540000
    plex_track.genres = [Mock(tag="Jazz")]
    plex_track.year = 1959
    plex_track.userRating = 4.5
    plex_track.viewCount = 42
    plex_track.lastViewedAt = None
    plex_track.artist.return_value = None
    plex_track.album.return_value = None

    track = client.extract_track_metadata(plex_track)

    assert isinstance(track, Track)
    assert track.plex_key == "11111"
    assert track.title == "So What"
    assert track.duration_ms == 540000
    assert track.genre == "Jazz"
    assert track.year == 1959
    assert track.rating == 4.5
    assert track.play_count == 42


def test_extract_artist_metadata_no_genres():
    with patch('plexmix.plex.client.PlexServer'):
        client = PlexClient(url="http://localhost:32400", token="test-token")

    plex_artist = Mock()
    plex_artist.ratingKey = "1"
    plex_artist.title = "Artist Name"
    plex_artist.genres = []
    plex_artist.summary = None

    artist = client.extract_artist_metadata(plex_artist)

    assert artist.name == "Artist Name"
    assert artist.genre is None
    assert artist.bio is None


def test_extract_track_metadata_no_rating():
    with patch('plexmix.plex.client.PlexServer'):
        client = PlexClient(url="http://localhost:32400", token="test-token")

    plex_track = Mock()
    plex_track.ratingKey = "1"
    plex_track.title = "Track"
    plex_track.duration = 300000
    plex_track.genres = []
    plex_track.year = None
    plex_track.userRating = None
    plex_track.viewCount = 0
    plex_track.lastViewedAt = None
    plex_track.artist.return_value = None
    plex_track.album.return_value = None

    mock_media = Mock()
    mock_part = Mock()
    mock_part.file = "/music/track.mp3"
    mock_media.parts = [mock_part]
    plex_track.media = [mock_media]

    track = client.extract_track_metadata(plex_track)

    assert track.title == "Track"
    assert track.rating is None
    assert track.year is None
