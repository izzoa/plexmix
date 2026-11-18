import ast
import logging
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from unittest.mock import Mock, patch

import pytest
import requests
from plexapi.audio import Track as PlexApiTrack
from plexapi.exceptions import BadRequest, NotFound, Unauthorized

import plexmix.plex.client as client_module
from plexmix.plex.client import (
    EmptyTitleError,
    PlexClient,
    PlexFetchError,
    PlexReadInterrupted,
    PlexReadReport,
)
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
    plex_track.userRating = 9.0  # Plex 0-10 scale (== 4.5 stars)
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
    # Plex's 0-10 userRating is converted to PlexMix's 0-5 scale.
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


# ===================================================================
# 8. Secrets in log lines and messages
# ===================================================================

# The only "secrets" these tests use are the project's dummy values.
DUMMY_TOKEN = "dummy-plex-token-0000"
DUMMY_PASSWORD = "dummy-ui-password-0000"
URL_WITH_CREDENTIALS = f"http://user:{DUMMY_PASSWORD}@plexhost:32400"
CLIENT_LOGGER = "plexmix.plex.client"


def _bare_exception_uses(source: str) -> List[str]:
    """Places where an ``except`` block logs, returns or passes on its exception as it is.

    A caught exception may only reach a log call, an f-string or a returned value
    through ``_safe_error_text``. ``logger.exception`` and ``exc_info`` count too:
    they make the logging module format the exception itself. Raising the caught
    exception again, or chaining it to a new one, counts as well: the CLI's error
    report prints its text. Only the client's own ``PlexFetchError`` may pass.
    """
    tree = ast.parse(source)
    parents: Dict[ast.AST, ast.AST] = {
        child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
    }

    def is_log_call(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        )

    def is_safe_text_call(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_safe_error_text"
        )

    problems = set()
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        for node in ast.walk(handler):
            if isinstance(node, ast.Call) and is_log_call(node):
                assert isinstance(node.func, ast.Attribute)
                if node.func.attr == "exception" or any(
                    keyword.arg == "exc_info" for keyword in node.keywords
                ):
                    problems.add(f"line {node.lineno}: the log call formats the exception itself")
            if isinstance(node, ast.Raise):
                names = [part.id for part in (node.exc, node.cause) if isinstance(part, ast.Name)]
                own_error = (
                    isinstance(handler.type, ast.Name) and handler.type.id == "PlexFetchError"
                )
                if (node.exc is None and not own_error) or handler.name in names:
                    problems.add(f"line {node.lineno}: the caught exception is raised again")
            if not (isinstance(node, ast.Name) and node.id == handler.name):
                continue
            ancestor = parents[node]
            while ancestor is not handler:
                if is_safe_text_call(ancestor):
                    break
                if isinstance(ancestor, (ast.JoinedStr, ast.Return)) or is_log_call(ancestor):
                    problems.add(f"line {node.lineno}: bare exception {node.id!r}")
                    break
                ancestor = parents[ancestor]
    return sorted(problems)


class TestSecretsInErrorText:
    @patch("plexmix.plex.client.PlexServer")
    def test_bad_request_log_has_no_token_length_or_url_credentials(
        self, mock_plex_server_cls: Mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_plex_server_cls.side_effect = BadRequest(
            f"(400) bad_request; {URL_WITH_CREDENTIALS}/ <html><body>Bad Request</body></html>"
        )
        client = _make_client(url=URL_WITH_CREDENTIALS, token=DUMMY_TOKEN)

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert client.connect() is False

        assert "Bad request to Plex server: (400) bad_request; http://***@plexhost:32400/" in (
            caplog.text
        )
        assert "Server URL used: http://***@plexhost:32400" in caplog.text
        # Nothing about the token, not even how long it is.
        assert "Token length" not in caplog.text
        assert f"{len(DUMMY_TOKEN)} characters" not in caplog.text
        assert DUMMY_TOKEN not in caplog.text
        assert DUMMY_PASSWORD not in caplog.text
        assert "user:" not in caplog.text
        # Only the log is masked: the connection still used the full address.
        mock_plex_server_cls.assert_called_once_with(URL_WITH_CREDENTIALS, DUMMY_TOKEN)

    @patch("plexmix.plex.client.time.sleep", return_value=None)
    @patch("plexmix.plex.client.PlexServer")
    def test_connect_failure_text_masks_url_credentials(
        self, mock_plex_server_cls: Mock, mock_sleep: Mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_plex_server_cls.side_effect = ConnectionError(
            f"(502) bad_gateway; http://user:pass@host:32400/ and {URL_WITH_CREDENTIALS}/identity"
        )
        client = _make_client(url=URL_WITH_CREDENTIALS, token=DUMMY_TOKEN)

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert client.connect() is False
            valid, message = client.validate_token()

        assert valid is False
        assert message == (
            "Cannot reach Plex server: (502) bad_gateway; http://***@host:32400/ and "
            "http://***@plexhost:32400/identity"
        )
        # Every attempt logged the failure, and every line is masked.
        assert caplog.text.count("http://***@plexhost:32400/identity") == 3
        for text in (caplog.text, message):
            assert "user:pass" not in text
            assert DUMMY_PASSWORD not in text
            assert "user:" not in text

    @patch("plexmix.plex.client.PlexServer")
    def test_bad_request_message_masks_url_credentials(self, mock_plex_server_cls: Mock) -> None:
        mock_plex_server_cls.side_effect = BadRequest(f"(400) bad_request; {URL_WITH_CREDENTIALS}/")
        client = _make_client(url=URL_WITH_CREDENTIALS, token=DUMMY_TOKEN)

        valid, message = client.validate_token()

        assert valid is False
        assert message == (
            "Bad request: (400) bad_request; http://***@plexhost:32400/. "
            "Check server URL and token format."
        )

    def test_errors_after_connecting_are_masked_in_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        error = ConnectionError(
            f"(500) internal_server_error; {URL_WITH_CREDENTIALS}/library/sections"
            f"?X-Plex-Token={DUMMY_TOKEN}"
        )
        client = _make_client(url=URL_WITH_CREDENTIALS, token=DUMMY_TOKEN)
        client.server = Mock()
        client.server.library.sections.side_effect = error
        client.server.library.section.side_effect = error
        client.server.createPlaylist.side_effect = error
        client.music_library = Mock()
        client.music_library.searchArtists.side_effect = error
        client.music_library.searchAlbums.side_effect = error
        client.music_library.searchTracks.side_effect = error
        client.music_library.fetchItem.side_effect = [error, Mock()]

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert client.test_connection() is False
            assert client.get_music_libraries() == []
            assert client.select_library("Music") is False
            for fetch in (client.get_all_artists, client.get_all_albums, client.get_all_tracks):
                # Not Plex's own error: its text holds the full address.
                with pytest.raises(PlexFetchError):
                    list(fetch())
            assert client.create_playlist("Mix", ["1", "2"]) is None

        for line in (
            "Connection test failed",
            "Failed to retrieve music libraries",
            "Failed to select library",
            "Failed to retrieve artists",
            "Failed to retrieve albums",
            "Failed to retrieve tracks",
            "Failed to fetch track 1",
            "Failed to create playlist",
        ):
            assert (
                f"{line}: (500) internal_server_error; http://***@plexhost:32400/library/sections"
                "?X-Plex-Token=***" in caplog.text
            )
        assert DUMMY_PASSWORD not in caplog.text
        assert DUMMY_TOKEN not in caplog.text
        assert "user:" not in caplog.text

    @pytest.mark.parametrize("kind", ["artists", "albums", "tracks"])
    def test_listing_failure_raises_safe_text_without_the_original_error(
        self, kind: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """What a failed listing raises ends up on the user's screen, in full."""
        error = BadRequest(
            f"(500) internal_server_error; {URL_WITH_CREDENTIALS}/library/sections/1/all"
            f"?includeGuids=1&type=8&X-Plex-Token={DUMMY_TOKEN} <html><body>no</body></html>"
        )
        client = _make_client(url=URL_WITH_CREDENTIALS, token=DUMMY_TOKEN)
        client.music_library = Mock()
        client.music_library.searchArtists.side_effect = error
        client.music_library.searchAlbums.side_effect = error
        client.music_library.searchTracks.side_effect = error
        fetch = getattr(client, f"get_all_{kind}")

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            with pytest.raises(PlexFetchError) as info:
                list(fetch())

        raised = info.value
        # One plain sentence. Nothing of Plex's own text is in it, masked or not.
        assert isinstance(raised, PlexReadInterrupted)
        assert str(raised) == f"Plex could not list the library's {kind}, so nothing was changed."
        # Plex's error is not shown with it: not as the cause, not as the context.
        assert not isinstance(raised, BadRequest)
        assert raised.__cause__ is None
        assert raised.__suppress_context__ is True
        for text in (str(raised), repr(raised)):
            assert DUMMY_PASSWORD not in text
            assert DUMMY_TOKEN not in text
            assert "user:" not in text
            assert "plexhost" not in text
            assert "http" not in text
        # The log keeps Plex's text, with the credentials and the token masked.
        assert (
            f"Failed to retrieve {kind}: (500) internal_server_error; "
            "http://***@plexhost:32400/library/sections/1/all"
            "?includeGuids=1&type=8&X-Plex-Token=*** <html><body>no</body></html>"
        ) in caplog.text
        assert DUMMY_PASSWORD not in caplog.text
        assert DUMMY_TOKEN not in caplog.text
        assert "user:" not in caplog.text

    def test_item_errors_are_masked_in_the_log(self, caplog: pytest.LogCaptureFixture) -> None:
        client = _make_client(url=URL_WITH_CREDENTIALS, token=DUMMY_TOKEN)
        client.music_library = Mock()
        client.music_library.searchArtists.return_value = [Mock()]
        client.music_library.searchAlbums.return_value = [Mock()]
        client.music_library.searchTracks.return_value = [Mock()]
        skipped = EmptyTitleError(f"empty title; see {URL_WITH_CREDENTIALS}/library/metadata/1")
        failed = RuntimeError(f"(404) not_found; {URL_WITH_CREDENTIALS}/library/metadata/2")

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            for error in (skipped, failed):
                with (
                    patch.object(client, "extract_artist_metadata", side_effect=error),
                    patch.object(client, "extract_album_metadata", side_effect=error),
                    patch.object(client, "extract_track_metadata", side_effect=error),
                ):
                    assert list(client.get_all_artists()) == []
                    assert list(client.get_all_albums()) == []
                    assert list(client.get_all_tracks()) == []

        for kind in ("artist", "album", "track"):
            assert f"Skipping {kind}: empty title; see http://***@plexhost:32400/" in caplog.text
            assert (
                f"Failed to extract {kind} metadata: (404) not_found; http://***@plexhost:32400/"
                in caplog.text
            )
        assert DUMMY_PASSWORD not in caplog.text
        assert "user:" not in caplog.text

    def test_playlist_description_error_is_masked_in_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _make_client(url=URL_WITH_CREDENTIALS, token=DUMMY_TOKEN)
        client.server = Mock()
        client.music_library = Mock()
        playlist = client.server.createPlaylist.return_value
        playlist.editSummary.side_effect = RuntimeError(
            f"(400) bad_request; {URL_WITH_CREDENTIALS}/playlists/9"
        )

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert client.create_playlist("Mix", ["1"], description="notes") is playlist

        assert (
            "failed to set its description: (400) bad_request; http://***@plexhost:32400/"
            in caplog.text
        )
        assert DUMMY_PASSWORD not in caplog.text

    @pytest.mark.parametrize(
        "password, shown_by_the_library",
        [
            ("dummy-ui/password-0000", "leakuser:dummy-ui"),
            ("dummy-ui?password-0000", "leakuser:dummy-ui"),
            ("dummy-ui#password-0000", "leakuser:dummy-ui"),
            ("dummy\\ui-password-0000", "leakuser:dummy"),
        ],
    )
    @patch("plexmix.plex.client.time.sleep", return_value=None)
    @patch("plexmix.plex.client.PlexServer")
    def test_address_the_http_library_cannot_read_is_not_quoted(
        self,
        mock_plex_server_cls: Mock,
        mock_sleep: Mock,
        password: str,
        shown_by_the_library: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A raw "/", "?", "#" or backslash in the password cuts the address in two.

        The HTTP library then quotes the first part, user name and password start,
        with no "@" in it for the masking to find.
        """
        url = f"http://leakuser:{password}@127.0.0.1:9"
        # The library's own error for this address. Preparing a request sends nothing.
        with pytest.raises(requests.exceptions.InvalidURL) as library_error:
            requests.Request("GET", url).prepare()
        assert shown_by_the_library in str(library_error.value)
        mock_plex_server_cls.side_effect = library_error.value
        client = _make_client(url=url, token=DUMMY_TOKEN)

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert client.connect() is False
            valid, message = client.validate_token()

        assert valid is False
        assert "the Plex server address is not a valid URL" in message
        assert caplog.text.count("the Plex server address is not a valid URL") == 3
        for text in (message, caplog.text):
            assert "leakuser" not in text
            for piece in ("dummy-ui", "dummy", "password-0000", "ui-password"):
                assert piece not in text

    @pytest.mark.parametrize(
        "error",
        [
            requests.exceptions.InvalidURL(
                f"Invalid URL 'http:/leakuser:{DUMMY_PASSWORD}@plexhost:32400/': No host supplied"
            ),
            requests.exceptions.MissingSchema(
                f"Invalid URL 'http//leakuser:{DUMMY_PASSWORD}@plexhost:32400/': No scheme "
                f"supplied. Perhaps you meant https://http//leakuser:{DUMMY_PASSWORD}@plexhost/?"
            ),
            requests.exceptions.InvalidSchema(
                f"No connection adapters were found for '//leakuser:{DUMMY_PASSWORD}@plexhost/'"
            ),
        ],
    )
    def test_address_errors_are_reported_by_a_fixed_sentence(self, error: Exception) -> None:
        client = _make_client(url=f"http:/leakuser:{DUMMY_PASSWORD}@plexhost:32400")

        text = client._safe_error_text(error)

        assert text.startswith("the Plex server address is not a valid URL")
        assert "leakuser" not in text
        assert DUMMY_PASSWORD not in text

    def test_other_errors_of_the_http_library_keep_their_text(self) -> None:
        client = _make_client(url=URL_WITH_CREDENTIALS)
        error = requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='plexhost', port=32400): Max retries exceeded with url: / "
            "(Caused by NewConnectionError('[Errno 61] Connection refused'))"
        )

        assert client._safe_error_text(error) == str(error)

    def test_own_credentials_are_replaced_where_no_at_sign_marks_them(self) -> None:
        """An address that was read the wrong way is quoted in pieces, without the "@"."""
        client = _make_client(
            url="http://leakuser:dummy-ui/password-0000@plexhost:32400", token=DUMMY_TOKEN
        )
        # Not one of the address errors above: any other error could quote the same.
        error = RuntimeError(
            "HTTPConnectionPool(host='leakuser', port=80): cannot use 'leakuser:dummy-ui' "
            "with url: /password-0000@plexhost:32400/"
        )

        text = client._safe_error_text(error)

        assert text == (
            "HTTPConnectionPool(host='***', port=80): cannot use '***' "
            "with url: /***@plexhost:32400/"
        )

    @pytest.mark.parametrize(
        "url, shown",
        [
            # A password with a space, a tab or a line break is a working address.
            ("http://leakuser:dummy ui password-0000@plexhost:32400", "http://***@plexhost:32400"),
            (
                "http://leakuser:dummy\tui\npassword-0000@plexhost:32400",
                "http://***@plexhost:32400",
            ),
            # Mistyped schemes, and nothing after the "@".
            (f"http:/leakuser:{DUMMY_PASSWORD}@plexhost:32400", "http:/***@plexhost:32400"),
            (f"http//leakuser:{DUMMY_PASSWORD}@plexhost:32400", "http//***@plexhost:32400"),
            (f"http://leakuser:{DUMMY_PASSWORD}@", "http://***@"),
            # A token pasted into the address, as in Plex's own "View XML" links.
            (
                f"http://plexhost:32400/?X-Plex-Token={DUMMY_TOKEN}",
                "http://plexhost:32400/?X-Plex-Token=***",
            ),
        ],
    )
    @patch("plexmix.plex.client.PlexServer")
    def test_bad_request_log_masks_odd_addresses(
        self, mock_plex_server_cls: Mock, url: str, shown: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_plex_server_cls.side_effect = BadRequest("(400) bad_request")
        client = _make_client(url=url, token="another-dummy-token")

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert client.connect() is False

        assert f"Server URL used: {shown}" in caplog.text
        for secret in ("leakuser", "dummy", "password-0000", DUMMY_TOKEN):
            assert secret not in caplog.text

    def test_client_never_formats_a_bare_exception(self) -> None:
        """Every exception text in client.py goes through ``_safe_error_text``."""
        # The check itself must catch each bad form, or passing means nothing.
        bad_forms = {
            'logger.error(f"failed: {e}")': 1,
            'logger.warning("failed: %s", e)': 1,
            'logger.error("failed: " + str(e))': 1,
            'return False, f"Cannot reach Plex server: {e}"': 1,
            "return str(e)": 1,
            'logger.exception("failed")': 1,
            'logger.error("failed", exc_info=True)': 1,
            "raise": 1,
            "raise e": 1,
            "raise RuntimeError('wrapped') from e": 1,
            'logger.error(f"failed: {self._safe_error_text(e)}")': 0,
            'return False, f"failed: {self._safe_error_text(e)}"': 0,
            'raise PlexFetchError(f"failed: {self._safe_error_text(e)}") from None': 0,
            'raise self._listing_error("artists", e) from None': 0,
        }
        for line, expected in bad_forms.items():
            sample = (
                "def run(self):\n"
                "    try:\n"
                "        pass\n"
                "    except Exception as e:\n"
                f"        {line}\n"
            )
            assert len(_bare_exception_uses(sample)) == expected, line

        # The client's own error may pass through an ``except PlexFetchError`` unchanged.
        own_error = (
            "def run(self):\n    try:\n        pass\n    except PlexFetchError:\n        raise\n"
        )
        assert _bare_exception_uses(own_error) == []
        assert len(_bare_exception_uses(own_error.replace("PlexFetchError", "BadRequest"))) == 1

        source = Path(client_module.__file__).read_text(encoding="utf-8")
        assert _bare_exception_uses(source) == []
        assert "{e}" not in source
        # The helper is really in use: the file has many except blocks.
        assert "self._safe_error_text(e)" in source


class TestTokenThatCannotBeSent:
    @pytest.mark.parametrize(
        "token",
        [
            "dummy-plex\ntoken-0000",  # a line break inside
            "dummy-plex\r\ntoken-0000",
            "dummy-plex token-0000",  # a space inside
            "dummy-plex\ttoken-0000",
            "dummy-plex\x00token-0000",  # another control character
            "dummy-pl\u00e9x-token-0000",  # a non-ASCII character
            "  dummy-plex\ntoken-0000  ",
        ],
    )
    @patch("plexmix.plex.client.PlexServer")
    def test_token_with_line_break_is_refused_before_connecting(
        self, mock_plex_server_cls: Mock, token: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _make_client(token=token)
        mock_plex_server_cls.reset_mock()

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            connected = client.connect()
            valid, message = client.validate_token()

        assert connected is False
        assert valid is False
        # No server object was built, so no request carried the token.
        mock_plex_server_cls.assert_not_called()
        assert client.server is None
        assert "Plex token contains invalid characters" in message
        assert "Plex token contains invalid characters" in caplog.text
        # connect() logs one line; validate_token() only returns its message.
        assert len([r for r in caplog.records if r.name == CLIENT_LOGGER]) == 1
        # The value is never quoted: not as it is, not escaped, not in part.
        cleaned = token.strip()
        for text in (message, caplog.text):
            assert cleaned not in text
            assert repr(cleaned)[1:-1] not in text
            assert "dummy-pl" not in text
            assert "token-0000" not in text

    @patch("plexmix.plex.client.PlexServer")
    def test_clean_token_still_connects(self, mock_plex_server_cls: Mock) -> None:
        mock_plex_server_cls.return_value.friendlyName = "MyPlex"
        # Every printable ASCII character except the space can be sent.
        token = "".join(chr(code) for code in range(0x21, 0x7F))
        client = _make_client(token=f"  {token}\n")
        mock_plex_server_cls.reset_mock()

        assert client.connect() is True
        assert client.validate_token() == (True, "Connected to MyPlex")
        assert mock_plex_server_cls.call_count == 2
        mock_plex_server_cls.assert_called_with("http://localhost:32400", token)

    @patch("plexmix.plex.client.time.sleep", return_value=None)
    @patch("plexmix.plex.client.PlexServer")
    def test_connect_error_text_never_contains_the_token(
        self, mock_plex_server_cls: Mock, mock_sleep: Mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_plex_server_cls.side_effect = ValueError(
            f"Invalid header value {DUMMY_TOKEN!r} for X-Plex-Token ({DUMMY_TOKEN})"
        )
        client = _make_client(token=f" {DUMMY_TOKEN} ")

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert client.connect() is False
            valid, message = client.validate_token()

        assert valid is False
        assert (
            message == "Cannot reach Plex server: Invalid header value '***' for X-Plex-Token (***)"
        )
        assert "Invalid header value '***' for X-Plex-Token (***)" in caplog.text
        assert DUMMY_TOKEN not in caplog.text
        assert DUMMY_TOKEN not in message

    def test_safe_error_text_replaces_the_escaped_token(self) -> None:
        token = "dummy-plex\ntoken-0000"
        client = _make_client(token=token)
        # An HTTP library quotes a refused header value with repr().
        error = ValueError(f"Invalid return character in header value: {token!r} (raw: {token})")

        text = client._safe_error_text(error)

        assert text == "Invalid return character in header value: '***' (raw: ***)"
        assert "dummy-plex" not in text
        assert "token-0000" not in text

    def test_safe_error_text_keeps_ordinary_text(self) -> None:
        client = _make_client(token=DUMMY_TOKEN)

        assert client._safe_error_text(TimeoutError("Read timed out")) == "Read timed out"
        # An empty or very short token must not turn the text into a row of masks.
        for token in ("", "a"):
            client.token = token
            assert client._safe_error_text(ValueError("a bad day")) == "a bad day"


# ===================================================================
# 9. What Plex listed, and reads that fail
# ===================================================================

KINDS = ["artists", "albums", "tracks"]


class _FakeItem:
    """One listed item. Its rating key is always there; reading its details can fail.

    ``errors`` are raised one per read attempt, in order, before the item answers:
    every attempt starts by reading ``title``, as the real client does.
    """

    genres: List[Any] = []

    def __init__(
        self,
        key: Any,
        title: str = "Title",
        errors: Iterable[BaseException] = (),
        on_read: Optional[Callable[[], None]] = None,
    ) -> None:
        self.ratingKey = key
        self._title = title
        self._errors = list(errors)
        self._on_read = on_read
        self.detail_reads = 0

    @property
    def title(self) -> str:
        self.detail_reads += 1
        if self._on_read is not None:
            self._on_read()
        if self._errors:
            raise self._errors.pop(0)
        return self._title


class _FakeLibrary:
    """A music library that lists the items it was given, of every kind."""

    def __init__(self, items: Iterable[_FakeItem], error: Optional[BaseException] = None) -> None:
        self._items = list(items)
        self._error = error
        self.listings = 0

    def _list(self) -> List[_FakeItem]:
        self.listings += 1
        if self._error is not None:
            raise self._error
        return list(self._items)

    searchArtists = searchAlbums = searchTracks = _list


def _client_with(items: Iterable[_FakeItem], error: Optional[BaseException] = None) -> PlexClient:
    client = _make_client(url=URL_WITH_CREDENTIALS, token=DUMMY_TOKEN)
    client.music_library = _FakeLibrary(items, error)
    return client


def _read(client: PlexClient, kind: str) -> List[str]:
    """Read every item of ``kind`` and return the keys of the ones that were read."""
    batches = getattr(client, f"get_all_{kind}")()
    return [entry.plex_key for batch in batches for entry in batch]


class TestReadReport:
    def test_listed_keys_include_tracks_whose_read_failed(self) -> None:
        keys_at_first_read: List[frozenset] = []
        client = _client_with([])
        items = [
            _FakeItem(
                1, on_read=lambda: keys_at_first_read.append(client.read_report.listed_track_keys)
            ),
            _FakeItem(2, errors=[RuntimeError("(404) not_found")]),
            _FakeItem(3),
            _FakeItem(4),
        ]
        client.music_library = _FakeLibrary(items)

        read = _read(client, "tracks")

        report = client.read_report
        assert isinstance(report, PlexReadReport)
        assert read == ["1", "3", "4"]
        # The track whose read failed is still listed: only the listing says what is gone.
        assert report.listed_track_keys == frozenset({"1", "2", "3", "4"})
        assert report.listed["tracks"] == 4
        assert report.failed["tracks"] == 1
        assert report.skipped["tracks"] == 0
        # The whole set was there before the first item was read.
        assert keys_at_first_read[0] == frozenset({"1", "2", "3", "4"})

    def test_empty_title_is_skipped_but_listed(self) -> None:
        client = _client_with([_FakeItem(1), _FakeItem(2, title="  "), _FakeItem(3)])

        read = _read(client, "tracks")

        report = client.read_report
        assert read == ["1", "3"]
        assert "2" in report.listed_track_keys
        assert report.listed["tracks"] == 3
        assert report.skipped["tracks"] == 1
        assert report.failed["tracks"] == 0

    @pytest.mark.parametrize("kind", KINDS)
    @pytest.mark.parametrize(
        "error_type", [ConnectionResetError, TimeoutError, requests.exceptions.ConnectionError]
    )
    @patch("plexmix.plex.client.time.sleep", return_value=None)
    def test_connection_error_retries_once_then_raises_interrupted(
        self, mock_sleep: Mock, error_type: type, kind: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        lost = f"connection lost; {URL_WITH_CREDENTIALS}/library/metadata/3"
        items = [
            _FakeItem(1),
            _FakeItem(2),
            _FakeItem(3, errors=[error_type(lost), error_type(lost)]),
            _FakeItem(4),
            _FakeItem(5),
        ]
        client = _client_with(items)

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            with pytest.raises(PlexReadInterrupted) as info:
                _read(client, kind)

        raised = info.value
        assert (raised.kind, raised.read, raised.listed) == (kind, 2, 5)
        assert str(raised) == (
            f"Plex stopped answering after 2 of 5 {kind} were read, so nothing was changed."
        )
        # One retry, one second later, and then the reads stop: item 4 was never read.
        mock_sleep.assert_called_once_with(1.0)
        assert items[2].detail_reads == 2
        assert items[3].detail_reads == 0
        # The listing is on record, and the lost item is not counted as a failed read.
        assert client.read_report.listed[kind] == 5
        assert client.read_report.failed[kind] == 0
        # The error is not wrapped again and does not carry the library's own error.
        assert type(raised) is PlexReadInterrupted
        assert raised.__cause__ is None
        assert raised.__suppress_context__ is True
        assert "plexhost" not in str(raised)
        assert "Plex stopped answering after 2 of 5" in caplog.text
        assert "http://***@plexhost:32400/library/metadata/3" in caplog.text
        assert DUMMY_PASSWORD not in caplog.text

    @pytest.mark.parametrize("kind", KINDS)
    @patch("plexmix.plex.client.time.sleep", return_value=None)
    def test_connection_error_recovered_by_retry_continues(
        self, mock_sleep: Mock, kind: str
    ) -> None:
        items = [
            _FakeItem(1),
            _FakeItem(2, errors=[ConnectionResetError("connection reset by peer")]),
            _FakeItem(3),
        ]
        client = _client_with(items)

        read = _read(client, kind)

        assert read == ["1", "2", "3"]
        mock_sleep.assert_called_once_with(1.0)
        assert client.read_report.listed[kind] == 3
        assert client.read_report.failed[kind] == 0
        assert client.read_report.skipped[kind] == 0

    @pytest.mark.parametrize("kind", KINDS)
    @patch("plexmix.plex.client.time.sleep", return_value=None)
    def test_other_failures_are_counted_not_raised(self, mock_sleep: Mock, kind: str) -> None:
        items = [
            _FakeItem(1, errors=[RuntimeError("unexpected")]),
            _FakeItem(2),
            # PlexAPI's own HTTP errors are not connection errors.
            _FakeItem(3, errors=[NotFound("(404) not_found")]),
            _FakeItem(4, errors=[BadRequest("(500) internal_server_error")]),
            _FakeItem(5, errors=[KeyError("ratingKey")]),
            _FakeItem(6),
        ]
        client = _client_with(items)

        read = _read(client, kind)

        assert read == ["2", "6"]
        assert client.read_report.listed[kind] == 6
        assert client.read_report.failed[kind] == 4
        assert client.read_report.skipped[kind] == 0
        # No retry and no pause: these are not lost connections.
        mock_sleep.assert_not_called()
        assert [item.detail_reads for item in items if item.ratingKey in (1, 3, 4, 5)] == [1] * 4

    @patch("plexmix.plex.client.time.sleep", return_value=None)
    def test_retry_that_fails_another_way_is_counted(self, mock_sleep: Mock) -> None:
        items = [
            _FakeItem(1, errors=[ConnectionResetError("reset"), RuntimeError("(404) not_found")]),
            _FakeItem(2),
        ]
        client = _client_with(items)

        assert _read(client, "tracks") == ["2"]
        assert client.read_report.failed["tracks"] == 1
        mock_sleep.assert_called_once_with(1.0)

    @pytest.mark.parametrize("kind", KINDS)
    def test_only_an_empty_title_is_skipped(self, kind: str) -> None:
        """Another ``ValueError`` is a failed read: it must count, and be reported."""
        items = [
            _FakeItem(1, errors=[ValueError("could not convert string to float: 'n/a'")]),
            _FakeItem(2, title=""),
            _FakeItem(3),
        ]
        client = _client_with(items)

        assert _read(client, kind) == ["3"]
        assert client.read_report.failed[kind] == 1
        assert client.read_report.skipped[kind] == 1

    def test_detail_that_the_track_model_refuses_is_a_failed_read(self) -> None:
        # A rating of 12 is 6.0 on PlexMix's scale of 0 to 5. The model's own error
        # for it is a ValueError too, and it is not an empty title.
        refused = _FakeItem(1)
        refused.userRating = 12  # type: ignore[attr-defined]
        client = _client_with([refused, _FakeItem(2)])

        with pytest.raises(ValueError) as info:
            client.extract_track_metadata(refused)
        assert not isinstance(info.value, EmptyTitleError)

        assert _read(client, "tracks") == ["2"]
        assert client.read_report.failed["tracks"] == 1
        assert client.read_report.skipped["tracks"] == 0
        assert "1" in client.read_report.listed_track_keys

    def test_empty_title_error_is_a_value_error(self) -> None:
        # Code that catches ValueError around an extract call keeps working.
        client = _client_with([])
        with pytest.raises(EmptyTitleError) as info:
            client.extract_track_metadata(_FakeItem(9, title=" "))
        assert isinstance(info.value, ValueError)

    def test_each_read_starts_its_own_counts(self) -> None:
        client = _client_with([_FakeItem(1, errors=[RuntimeError("no")]), _FakeItem(2, title="")])
        assert _read(client, "tracks") == []
        assert client.read_report.failed["tracks"] == 1
        assert client.read_report.skipped["tracks"] == 1

        client.music_library = _FakeLibrary([_FakeItem(7)])
        assert _read(client, "tracks") == ["7"]

        assert client.read_report.listed_track_keys == frozenset({"7"})
        assert client.read_report.listed["tracks"] == 1
        assert client.read_report.failed["tracks"] == 0
        assert client.read_report.skipped["tracks"] == 0

    @pytest.mark.parametrize("kind", KINDS)
    @pytest.mark.parametrize(
        "error",
        [
            requests.exceptions.ConnectionError(
                f"HTTPConnectionPool(host='plexhost', port=32400): {URL_WITH_CREDENTIALS}/library"
                f"/sections/1/all?X-Plex-Token={DUMMY_TOKEN}"
            ),
            BadRequest(f"(500) internal_server_error; {URL_WITH_CREDENTIALS}/library/sections/1"),
        ],
    )
    def test_listing_failure_raises_interrupted(
        self, error: Exception, kind: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _client_with([_FakeItem(1)], error=error)
        client.read_report.listed_track_keys = frozenset({"stale"})
        client.read_report.listed[kind] = 99

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            with pytest.raises(PlexReadInterrupted) as info:
                _read(client, kind)

        raised = info.value
        assert (raised.kind, raised.read, raised.listed) == (kind, 0, None)
        sentence = str(raised)
        assert sentence == f"Plex could not list the library's {kind}, so nothing was changed."
        # The sentence has no address, and the original error is not chained to it.
        for piece in ("http", "plexhost", "32400", "user:", DUMMY_PASSWORD, DUMMY_TOKEN):
            assert piece not in sentence
        assert raised.__cause__ is None
        assert raised.__suppress_context__ is True
        # The log has the reason, masked. Nothing of an earlier listing is left behind.
        assert f"Failed to retrieve {kind}: " in caplog.text
        assert "http://***@plexhost:32400/library" in caplog.text
        assert DUMMY_PASSWORD not in caplog.text
        assert DUMMY_TOKEN not in caplog.text
        assert client.read_report.listed[kind] == 0
        if kind == "tracks":
            assert client.read_report.listed_track_keys == frozenset()

    @pytest.mark.parametrize("missing_key", [None, "", "  "])
    def test_listed_track_without_a_key_is_a_failed_listing(self, missing_key: Any) -> None:
        items = [_FakeItem(1), _FakeItem(missing_key), _FakeItem(3)]
        client = _client_with(items)

        with pytest.raises(PlexReadInterrupted) as info:
            _read(client, "tracks")
        with pytest.raises(PlexReadInterrupted):
            client.list_track_keys()

        assert info.value.listed is None
        assert client.read_report.listed_track_keys == frozenset()
        # No item was read: a listing that cannot be trusted stops the sync at once.
        assert [item.detail_reads for item in items] == [0, 0, 0]

    def test_list_track_keys_reads_no_details(self) -> None:
        items = [_FakeItem(11), _FakeItem(12, errors=[RuntimeError("never read")]), _FakeItem(13)]
        client = _client_with(items)

        keys = client.list_track_keys()

        assert keys == frozenset({"11", "12", "13"})
        assert isinstance(keys, frozenset)
        assert [item.detail_reads for item in items] == [0, 0, 0]
        assert client.music_library.listings == 1
        # The report of the last full read is left alone.
        assert client.read_report == PlexReadReport()

    def test_list_track_keys_failure_raises_interrupted(self) -> None:
        client = _client_with(
            [], error=ConnectionError(f"refused; {URL_WITH_CREDENTIALS}/library/sections/1/all")
        )

        with pytest.raises(PlexReadInterrupted) as info:
            client.list_track_keys()

        assert (
            str(info.value) == "Plex could not list the library's tracks, so nothing was changed."
        )
        assert info.value.__cause__ is None

        client.music_library = None
        with pytest.raises(PlexFetchError, match="No music library selected"):
            client.list_track_keys()

    def test_item_failure_logging_is_capped(self, caplog: pytest.LogCaptureFixture) -> None:
        items = [_FakeItem(n, errors=[RuntimeError(f"(404) not_found {n}")]) for n in range(8)]
        items += [_FakeItem(100), _FakeItem(101)]
        client = _client_with(items)

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert _read(client, "tracks") == ["100", "101"]

        failures = [r for r in caplog.records if "Failed to extract track metadata" in r.message]
        assert [r.levelno for r in failures] == [logging.WARNING] * 5 + [logging.DEBUG] * 3
        # One summary line says how many, whatever the level of the single lines.
        summaries = [r for r in caplog.records if r.message.startswith("Read ")]
        assert len(summaries) == 1
        assert summaries[0].levelno == logging.WARNING
        assert summaries[0].message == (
            "Read 2 of 10 tracks from Plex: 8 could not be read, 0 skipped (empty title)"
        )
        assert client.read_report.failed["tracks"] == 8

    def test_read_without_failures_logs_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        client = _client_with([_FakeItem(1), _FakeItem(2, title="")])

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert _read(client, "tracks") == ["1"]

        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
        assert "Read 1 of 2 tracks from Plex: 0 could not be read, 1 skipped" in caplog.text


class _DetailsServer:
    """What a PlexAPI item needs from its server: ``query`` answers a request for details.

    ``failures`` holds, per rating key, the errors that the next requests for that
    item raise. No request leaves this process.
    """

    _baseurl = "http://plexhost:32400"
    _token = DUMMY_TOKEN

    def __init__(self, failures: Dict[str, List[BaseException]]) -> None:
        self.failures = failures
        self.requests: List[str] = []

    def query(self, key: str, *args: Any, **kwargs: Any) -> ElementTree.Element:
        rating_key = key.split("?")[0].rsplit("/", 1)[-1]
        self.requests.append(rating_key)
        if self.failures.get(rating_key):
            raise self.failures[rating_key].pop(0)
        return ElementTree.fromstring(
            f'<MediaContainer size="1">{_track_xml(rating_key, details=True)}</MediaContainer>'
        )

    def url(self, key: str, includeToken: bool = False) -> str:  # noqa: N803 (PlexAPI's name)
        return self._baseurl + key


def _track_xml(rating_key: str, details: bool = False) -> str:
    """One track as Plex sends it: in a listing, or with ``details`` when asked for it alone."""
    more = 'year="2001" userRating="8.0" viewCount="3"' if details else ""
    return (
        f'<Track ratingKey="{rating_key}" key="/library/metadata/{rating_key}" type="track" '
        f'title="Song {rating_key}" parentRatingKey="11" grandparentRatingKey="1" {more}/>'
    )


def _listed_plexapi_tracks(server: _DetailsServer, rating_keys: List[str]) -> List[PlexApiTrack]:
    """Real PlexAPI track objects, built the way a listing builds them."""
    return [
        PlexApiTrack(
            server, ElementTree.fromstring(_track_xml(key)), initpath="/library/sections/1/all"
        )
        for key in rating_keys
    ]


class TestRetryWithPlexApiItems:
    """The retry has to reach Plex again, with PlexAPI's own item class.

    PlexAPI marks an item as loaded before the request for its details has answered.
    After a failed request the item answers every later read from the listing and
    sends nothing, so reading it a second time proves nothing about the connection.
    """

    def _client(self, server: _DetailsServer, rating_keys: List[str]) -> PlexClient:
        client = _make_client(url=URL_WITH_CREDENTIALS, token=DUMMY_TOKEN)
        client.music_library = Mock()
        client.music_library.searchTracks.return_value = _listed_plexapi_tracks(server, rating_keys)
        return client

    def test_listing_alone_sends_no_request_per_item(self) -> None:
        server = _DetailsServer({})
        client = self._client(server, ["101", "102", "103"])

        assert client.list_track_keys() == frozenset({"101", "102", "103"})
        assert server.requests == []

    @patch("plexmix.plex.client.time.sleep", return_value=None)
    def test_lasting_outage_is_noticed_at_the_retry(self, mock_sleep: Mock) -> None:
        refused = requests.exceptions.ConnectionError("connection refused")
        server = _DetailsServer({"102": [refused, refused]})
        client = self._client(server, ["101", "102", "103"])

        with pytest.raises(PlexReadInterrupted) as info:
            list(client.get_all_tracks())

        assert (info.value.kind, info.value.read, info.value.listed) == ("tracks", 1, 3)
        mock_sleep.assert_called_once_with(1.0)
        # Track 102 was asked for twice, and the reads stopped before track 103.
        assert server.requests == ["101", "102", "102"]
        assert client.read_report.listed_track_keys == frozenset({"101", "102", "103"})

    @patch("plexmix.plex.client.time.sleep", return_value=None)
    def test_recovered_retry_reads_the_real_details(self, mock_sleep: Mock) -> None:
        server = _DetailsServer({"102": [requests.exceptions.ConnectionError("reset by peer")]})
        client = self._client(server, ["101", "102", "103"])

        tracks = [track for batch in client.get_all_tracks() for track in batch]

        assert [track.plex_key for track in tracks] == ["101", "102", "103"]
        mock_sleep.assert_called_once_with(1.0)
        assert server.requests == ["101", "102", "102", "103"]
        # Track 102 holds what Plex answered on the second request, not the bare listing.
        assert [track.year for track in tracks] == [2001, 2001, 2001]
        assert [track.rating for track in tracks] == [4.0, 4.0, 4.0]
        assert client.read_report.failed["tracks"] == 0

    @patch("plexmix.plex.client.time.sleep", return_value=None)
    def test_http_error_for_one_item_is_a_failed_read(self, mock_sleep: Mock) -> None:
        server = _DetailsServer({"102": [NotFound("(404) not_found; /library/metadata/102")]})
        client = self._client(server, ["101", "102", "103"])

        tracks = [track for batch in client.get_all_tracks() for track in batch]

        assert [track.plex_key for track in tracks] == ["101", "103"]
        mock_sleep.assert_not_called()
        assert client.read_report.failed["tracks"] == 1
        assert "102" in client.read_report.listed_track_keys


# ===================================================================
# 10. select_library says why it failed (last_select_failure)
# ===================================================================

# What the HTTP library and Python raise when the connection is lost: a connection
# that was reset, a timeout and a refused connection. Every one is an OSError.
_LOST_CONNECTIONS = [
    pytest.param(
        requests.exceptions.ConnectionError(
            "('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))"
        ),
        id="reset",
    ),
    pytest.param(ConnectionResetError(54, "Connection reset by peer"), id="reset-builtin"),
    pytest.param(
        requests.exceptions.ConnectionError(
            "('Connection aborted.', RemoteDisconnected('Remote end closed connection "
            "without response'))"
        ),
        id="closed-without-an-answer",
    ),
    pytest.param(
        requests.exceptions.ReadTimeout("Read timed out. (read timeout=30)"), id="timeout"
    ),
    pytest.param(requests.exceptions.ConnectTimeout("timed out"), id="connect-timeout"),
    pytest.param(TimeoutError("timed out"), id="timeout-builtin"),
    pytest.param(
        requests.exceptions.ConnectionError(
            "Max retries exceeded with url: /library/ (Caused by NewConnectionError("
            "'Failed to establish a new connection: [Errno 61] Connection refused'))"
        ),
        id="refused",
    ),
    pytest.param(ConnectionRefusedError(61, "Connection refused"), id="refused-builtin"),
]

# Failures that say nothing about the connection and are not a missing library.
_OTHER_FAILURES = [
    pytest.param(BadRequest("(500) internal_server_error; /library/sections"), id="http-500"),
    pytest.param(Unauthorized("(401) unauthorized; /library/sections"), id="http-401"),
    pytest.param(RuntimeError("unexpected"), id="runtime-error"),
    pytest.param(ElementTree.ParseError("not well-formed"), id="reply-that-is-not-xml"),
    # An OSError of the HTTP library, but about the address and not the connection.
    pytest.param(requests.exceptions.InvalidURL("Failed to parse: plexhost:port"), id="address"),
]


def _client_whose_selection_raises(error: BaseException, **kwargs: Any) -> PlexClient:
    client = _make_client(**kwargs)
    client.server = Mock()
    client.server.library.section.side_effect = error
    return client


class _ServerWithoutLibrary:
    """A connected server whose library cannot be asked for: that request itself fails."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    @property
    def library(self) -> Any:
        raise self._error


class TestSelectLibraryFailureReason:
    def test_new_client_has_no_failure(self) -> None:
        assert _make_client().last_select_failure == ""

    def test_missing_library_is_not_found(self, caplog: pytest.LogCaptureFixture) -> None:
        client = _client_whose_selection_raises(NotFound("Invalid library section: NonExistent"))

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert client.select_library("NonExistent") is False

        assert client.last_select_failure == "not_found"
        assert client.music_library is None
        assert "Music library not found: NonExistent" in caplog.text

    @pytest.mark.parametrize("index", [1, 5, -1])
    def test_index_out_of_range_is_not_found(
        self, index: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _make_client()
        client.server = Mock()

        with patch.object(client, "get_music_libraries", return_value=["Music"]):
            with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
                assert client.select_library(index) is False

        assert client.last_select_failure == "not_found"
        assert f"Invalid library index: {index}" in caplog.text
        client.server.library.section.assert_not_called()

    @pytest.mark.parametrize("error", _LOST_CONNECTIONS)
    def test_lost_connection_is_unreachable(
        self, error: BaseException, caplog: pytest.LogCaptureFixture
    ) -> None:
        assert isinstance(error, OSError)
        client = _client_whose_selection_raises(error)

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert client.select_library("Music") is False

        assert client.last_select_failure == "unreachable"
        assert client.music_library is None
        # The log line is the one a failed selection always wrote.
        assert f"Failed to select library: {error}" in caplog.text
        assert "Music library not found" not in caplog.text

    def test_connection_lost_before_the_library_can_be_asked_for(self) -> None:
        """The outage of the live runs: Plex answers the first request and no other."""
        client = _make_client()
        client.server = _ServerWithoutLibrary(
            requests.exceptions.ConnectionError("Remote end closed connection without response")
        )

        assert client.select_library("Music") is False

        assert client.last_select_failure == "unreachable"

    @pytest.mark.parametrize("error", _OTHER_FAILURES)
    def test_other_failures_are_errors(
        self, error: BaseException, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _client_whose_selection_raises(error)

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert client.select_library("Music") is False

        assert client.last_select_failure == "error"
        assert "Failed to select library: " in caplog.text
        assert "Music library not found" not in caplog.text

    def test_failed_connect_is_an_error(self) -> None:
        client = _make_client()
        client.server = None

        with patch.object(client, "connect", return_value=False):
            assert client.select_library("Music") is False

        # Why the connection failed is not known here, so it is not called an outage.
        assert client.last_select_failure == "error"

    def test_success_clears_an_earlier_failure(self) -> None:
        client = _make_client()
        client.server = Mock()
        section = Mock()
        client.server.library.section.side_effect = [
            requests.exceptions.ConnectionError("connection reset"),
            section,
            NotFound("Invalid library section: Music"),
            RuntimeError("unexpected"),
            section,
        ]

        results = []
        for _attempt in range(5):
            results.append((client.select_library("Music"), client.last_select_failure))

        assert results == [
            (False, "unreachable"),
            (True, ""),
            (False, "not_found"),
            (False, "error"),
            (True, ""),
        ]
        assert client.music_library is section

    def test_success_by_index_clears_an_earlier_failure(self) -> None:
        client = _make_client()
        client.server = Mock()

        with patch.object(client, "get_music_libraries", return_value=["Music", "Jazz"]):
            assert client.select_library(7) is False
            assert client.last_select_failure == "not_found"
            assert client.select_library(1) is True

        assert client.last_select_failure == ""
        client.server.library.section.assert_called_once_with("Jazz")

    def test_every_call_starts_without_a_failure(self) -> None:
        """The reason of an earlier call is gone before Plex is asked again."""
        client = _make_client()
        client.server = Mock()
        seen_while_asking: List[str] = []

        def refuse(name: str) -> Any:
            seen_while_asking.append(client.last_select_failure)
            raise ConnectionRefusedError(61, "Connection refused")

        client.server.library.section.side_effect = refuse

        assert client.select_library("Music") is False
        assert client.select_library("Music") is False

        assert seen_while_asking == ["", ""]
        assert client.last_select_failure == "unreachable"

    @pytest.mark.parametrize(
        "make_error, reason",
        [
            (requests.exceptions.ConnectionError, "unreachable"),
            (requests.exceptions.ReadTimeout, "unreachable"),
            (ConnectionResetError, "unreachable"),
            (BadRequest, "error"),
            (RuntimeError, "error"),
            (NotFound, "not_found"),
        ],
    )
    def test_failure_log_never_holds_url_credentials(
        self,
        make_error: Callable[[str], BaseException],
        reason: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        error = make_error(
            f"(500) internal_server_error; {URL_WITH_CREDENTIALS}/library/sections"
            f"?X-Plex-Token={DUMMY_TOKEN}"
        )
        client = _client_whose_selection_raises(error, url=URL_WITH_CREDENTIALS, token=DUMMY_TOKEN)

        with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
            assert client.select_library("Music") is False

        assert client.last_select_failure == reason
        if reason == "not_found":
            assert "Music library not found: Music" in caplog.text
        else:
            assert (
                "Failed to select library: (500) internal_server_error; "
                "http://***@plexhost:32400/library/sections?X-Plex-Token=***" in caplog.text
            )
        assert DUMMY_PASSWORD not in caplog.text
        assert DUMMY_TOKEN not in caplog.text
        assert "user:" not in caplog.text
