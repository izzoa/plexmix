from unittest.mock import Mock, patch

import pytest
from plexapi.exceptions import BadRequest, NotFound, Unauthorized

from plexmix.plex.client import PlexClient
from plexmix.database.models import Artist, Album, Track


def test_plex_client_initialization():
    with patch("plexmix.plex.client.PlexServer"):
        client = PlexClient(url="http://localhost:32400", token="test-token")
        assert client.url == "http://localhost:32400"
        assert client.token == "test-token"


def test_extract_artist_metadata():
    with patch("plexmix.plex.client.PlexServer"):
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
    with patch("plexmix.plex.client.PlexServer"):
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
    with patch("plexmix.plex.client.PlexServer"):
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
    with patch("plexmix.plex.client.PlexServer"):
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
    with patch("plexmix.plex.client.PlexServer"):
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


# ---------------------------------------------------------------------------
# Helper: create a PlexClient without triggering a real PlexServer connection
# ---------------------------------------------------------------------------


def _make_client(url: str = "http://localhost:32400", token: str = "test-token") -> PlexClient:
    """Return a PlexClient whose __init__ is patched so PlexServer is never called."""
    with patch("plexmix.plex.client.PlexServer"):
        return PlexClient(url=url, token=token)


# ===================================================================
# 1. Connection logic (connect method)
# ===================================================================


class TestConnect:
    @patch("plexmix.plex.client.PlexServer")
    def test_connect_success(self, mock_plex_server_cls: Mock) -> None:
        mock_server = Mock()
        mock_server.friendlyName = "MyPlex"
        mock_plex_server_cls.return_value = mock_server

        client = PlexClient(url="http://localhost:32400", token="valid-token")
        # __init__ already created a server, reset to test connect() in isolation
        client.server = None

        result = client.connect()

        assert result is True
        assert client.server is mock_server
        mock_plex_server_cls.assert_called_with("http://localhost:32400", "valid-token")

    @patch("plexmix.plex.client.PlexServer")
    def test_connect_unauthorized_returns_false_immediately(
        self, mock_plex_server_cls: Mock
    ) -> None:
        mock_plex_server_cls.side_effect = Unauthorized("bad token")

        client = PlexClient.__new__(PlexClient)
        client.url = "http://localhost:32400"
        client.token = "bad-token"
        client.server = None
        client.music_library = None

        result = client.connect()

        assert result is False
        # Should NOT retry – only one call
        assert mock_plex_server_cls.call_count == 1

    @patch("plexmix.plex.client.PlexServer")
    def test_connect_bad_request_returns_false_immediately(
        self, mock_plex_server_cls: Mock
    ) -> None:
        mock_plex_server_cls.side_effect = BadRequest("bad request")

        client = PlexClient.__new__(PlexClient)
        client.url = "http://localhost:32400"
        client.token = "some-token"
        client.server = None
        client.music_library = None

        result = client.connect()

        assert result is False
        assert mock_plex_server_cls.call_count == 1

    @patch("plexmix.plex.client.time.sleep", return_value=None)
    @patch("plexmix.plex.client.PlexServer")
    def test_connect_generic_exception_retries_three_times(
        self, mock_plex_server_cls: Mock, mock_sleep: Mock
    ) -> None:
        mock_plex_server_cls.side_effect = ConnectionError("network down")

        client = PlexClient.__new__(PlexClient)
        client.url = "http://localhost:32400"
        client.token = "valid-token"
        client.server = None
        client.music_library = None

        result = client.connect()

        assert result is False
        assert mock_plex_server_cls.call_count == 3
        # sleep called between attempts (2 sleeps for 3 attempts)
        assert mock_sleep.call_count == 2

    @patch("plexmix.plex.client.PlexServer")
    def test_connect_cleans_token_whitespace(self, mock_plex_server_cls: Mock) -> None:
        mock_server = Mock()
        mock_server.friendlyName = "MyPlex"
        mock_plex_server_cls.return_value = mock_server

        client = PlexClient.__new__(PlexClient)
        client.url = "http://localhost:32400"
        client.token = "  abc123  "
        client.server = None
        client.music_library = None

        client.connect()

        mock_plex_server_cls.assert_called_with("http://localhost:32400", "abc123")


# ===================================================================
# 2. test_connection method
# ===================================================================


class TestTestConnection:
    def test_server_is_none_calls_connect(self) -> None:
        client = _make_client()
        client.server = None

        with patch.object(client, "connect", return_value=True) as mock_connect:
            result = client.test_connection()

        assert result is True
        mock_connect.assert_called_once()

    def test_server_exists_sections_works(self) -> None:
        client = _make_client()
        mock_server = Mock()
        mock_server.library.sections.return_value = []
        client.server = mock_server

        result = client.test_connection()

        assert result is True
        mock_server.library.sections.assert_called_once()

    def test_server_exists_sections_raises(self) -> None:
        client = _make_client()
        mock_server = Mock()
        mock_server.library.sections.side_effect = Exception("timeout")
        client.server = mock_server

        result = client.test_connection()

        assert result is False


# ===================================================================
# 3. get_music_libraries
# ===================================================================


class TestGetMusicLibraries:
    def test_no_server_and_connect_fails(self) -> None:
        client = _make_client()
        client.server = None

        with patch.object(client, "connect", return_value=False):
            result = client.get_music_libraries()

        assert result == []

    def test_success_returns_only_artist_sections(self) -> None:
        client = _make_client()
        mock_server = Mock()

        music_section = Mock()
        music_section.title = "Music"
        music_section.type = "artist"

        movie_section = Mock()
        movie_section.title = "Movies"
        movie_section.type = "movie"

        photo_section = Mock()
        photo_section.title = "Photos"
        photo_section.type = "photo"

        mock_server.library.sections.return_value = [
            music_section,
            movie_section,
            photo_section,
        ]
        client.server = mock_server

        result = client.get_music_libraries()

        assert result == ["Music"]

    def test_exception_returns_empty_list(self) -> None:
        client = _make_client()
        mock_server = Mock()
        mock_server.library.sections.side_effect = Exception("API error")
        client.server = mock_server

        result = client.get_music_libraries()

        assert result == []


# ===================================================================
# 4. select_library
# ===================================================================


class TestSelectLibrary:
    def test_select_by_name_success(self) -> None:
        client = _make_client()
        mock_server = Mock()
        mock_section = Mock()
        mock_server.library.section.return_value = mock_section
        client.server = mock_server

        result = client.select_library("Music")

        assert result is True
        assert client.music_library is mock_section
        mock_server.library.section.assert_called_with("Music")

    def test_select_by_name_not_found(self) -> None:
        client = _make_client()
        mock_server = Mock()
        mock_server.library.section.side_effect = NotFound("not found")
        client.server = mock_server

        result = client.select_library("NonExistent")

        assert result is False

    def test_select_by_index_valid(self) -> None:
        client = _make_client()
        mock_server = Mock()
        mock_section = Mock()
        mock_server.library.section.return_value = mock_section

        # get_music_libraries will be called inside select_library for int index
        with patch.object(client, "get_music_libraries", return_value=["Music", "Jazz"]):
            client.server = mock_server
            result = client.select_library(1)

        assert result is True
        mock_server.library.section.assert_called_with("Jazz")

    def test_select_by_index_out_of_range(self) -> None:
        client = _make_client()
        mock_server = Mock()
        client.server = mock_server

        with patch.object(client, "get_music_libraries", return_value=["Music"]):
            result = client.select_library(5)

        assert result is False

    def test_no_server_and_connect_fails(self) -> None:
        client = _make_client()
        client.server = None

        with patch.object(client, "connect", return_value=False):
            result = client.select_library("Music")

        assert result is False


# ===================================================================
# 5. Metadata extraction edge cases
# ===================================================================


class TestMetadataEdgeCases:
    def test_artist_with_empty_name_raises_value_error(self) -> None:
        client = _make_client()

        plex_artist = Mock()
        plex_artist.ratingKey = "999"
        plex_artist.title = "   "

        with pytest.raises(ValueError, match="Artist has empty name"):
            client.extract_artist_metadata(plex_artist)

    def test_album_no_genres_attribute(self) -> None:
        """When the plex object has no 'genres' attr, genre should be None."""
        client = _make_client()

        plex_album = Mock(spec=[])  # spec=[] removes all default attributes
        plex_album.ratingKey = "100"
        plex_album.title = "No Genres Album"
        # Do NOT set plex_album.genres — hasattr should be False

        album = client.extract_album_metadata(plex_album)

        assert album.genre is None
        assert album.year is None
        assert album.cover_art_url is None

    def test_track_with_grandparent_and_parent_rating_keys(self) -> None:
        client = _make_client()

        plex_track = Mock()
        plex_track.ratingKey = "500"
        plex_track.title = "Test Track"
        plex_track.duration = 200000
        plex_track.genres = []
        plex_track.year = 2020
        plex_track.userRating = 3.0
        plex_track.viewCount = 10
        plex_track.lastViewedAt = None
        plex_track.grandparentRatingKey = "100"
        plex_track.parentRatingKey = "200"
        plex_track.media = []

        track = client.extract_track_metadata(plex_track)

        assert track.__dict__["_artist_key"] == "100"
        assert track.__dict__["_album_key"] == "200"

    def test_album_with_parent_rating_key(self) -> None:
        client = _make_client()

        plex_album = Mock()
        plex_album.ratingKey = "300"
        plex_album.title = "Album With Parent"
        plex_album.year = 2021
        plex_album.genres = [Mock(tag="Rock")]
        plex_album.thumb = None
        plex_album.parentRatingKey = "50"

        album = client.extract_album_metadata(plex_album)

        assert album.__dict__["_artist_key"] == "50"


# ===================================================================
# 6. _extract_file_path
# ===================================================================


class TestExtractFilePath:
    def test_track_with_media_and_parts(self) -> None:
        plex_track = Mock()
        mock_part = Mock()
        mock_part.file = "/data/music/song.flac"
        mock_media = Mock()
        mock_media.parts = [mock_part]
        plex_track.media = [mock_media]

        result = PlexClient._extract_file_path(plex_track)

        assert result == "/data/music/song.flac"

    def test_track_with_no_media(self) -> None:
        plex_track = Mock()
        plex_track.media = []

        result = PlexClient._extract_file_path(plex_track)

        assert result is None

    def test_track_with_empty_parts(self) -> None:
        plex_track = Mock()
        mock_media = Mock()
        mock_media.parts = []
        plex_track.media = [mock_media]

        result = PlexClient._extract_file_path(plex_track)

        assert result is None


# ===================================================================
# 7. create_playlist
# ===================================================================


class TestCreatePlaylist:
    def test_successful_creation(self) -> None:
        client = _make_client()
        mock_server = Mock()
        mock_library = Mock()
        mock_playlist = Mock()

        mock_track_1 = Mock()
        mock_track_2 = Mock()
        mock_library.fetchItem.side_effect = [mock_track_1, mock_track_2]
        mock_server.createPlaylist.return_value = mock_playlist

        client.server = mock_server
        client.music_library = mock_library

        result = client.create_playlist("My Playlist", ["111", "222"], description="Great tunes")

        assert result is mock_playlist
        mock_server.createPlaylist.assert_called_once_with(
            title="My Playlist", items=[mock_track_1, mock_track_2]
        )
        mock_playlist.editSummary.assert_called_once_with("Great tunes")

    def test_no_library_returns_none(self) -> None:
        client = _make_client()
        client.music_library = None

        result = client.create_playlist("Playlist", ["111"])

        assert result is None

    def test_all_tracks_fail_to_fetch_returns_none(self) -> None:
        client = _make_client()
        mock_server = Mock()
        mock_library = Mock()
        mock_library.fetchItem.side_effect = Exception("not found")

        client.server = mock_server
        client.music_library = mock_library

        result = client.create_playlist("Playlist", ["111", "222"])

        assert result is None

    def test_no_server_returns_none(self) -> None:
        """When tracks are fetched but server is None, playlist creation fails."""
        client = _make_client()
        mock_library = Mock()
        mock_library.fetchItem.return_value = Mock()

        client.server = None
        client.music_library = mock_library

        result = client.create_playlist("Playlist", ["111"])

        assert result is None
