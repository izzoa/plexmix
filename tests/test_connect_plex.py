"""``connect_plex`` tells an outage from a music library that does not exist.

When Plex stops answering right after the first request, the library selection
fails for a reason that has nothing to do with the library setting. ``connect_plex``
then reports the outage (code ``unreachable``), not a renamed or removed library.

Most tests run the real ``PlexClient`` and PlexAPI's real ``Library`` against a
stand-in server object. No request leaves this process.
"""

import logging
import xml.etree.ElementTree as ElementTree
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Union
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests
import typer
from plexapi.exceptions import BadRequest, NotFound, Unauthorized
from plexapi.library import Library, MusicSection

from plexmix.config.settings import DatabaseSettings, PlexSettings, Settings
from plexmix.plex.client import PlexClient
from plexmix.services.sync_service import PlexConnectionError, connect_plex

# The only "secrets" these tests use are the project's dummy values.
DUMMY_TOKEN = "dummy-plex-token-0000"
DUMMY_PASSWORD = "dummy-ui-password-0000"
PLEX_URL = f"http://user:{DUMMY_PASSWORD}@plexhost:32400"

UNREACHABLE_MESSAGE = (
    "Plex stopped answering while the music library was being selected, "
    "so the library setting was not checked."
)
# Exactly what connect_plex said before it could tell the two cases apart.
LIBRARY_MISSING_MESSAGE = (
    "Failed to select Plex library 'Music'. "
    "It may have been renamed or removed — check your configuration."
)

LIBRARY_REQUEST = "/library/"
SECTIONS_REQUEST = "/library/sections"

Answer = Union[str, BaseException]


def _sections_xml(*music_libraries: str) -> str:
    """The list of libraries as Plex sends it: one film library, then the music ones."""
    rows = ['<Directory key="1" type="movie" title="Films" uuid="films"/>']
    rows += [
        f'<Directory key="{key}" type="artist" title="{title}" uuid="music-{key}"/>'
        for key, title in enumerate(music_libraries, start=2)
    ]
    return f'<MediaContainer size="{len(rows)}">{"".join(rows)}</MediaContainer>'


class _StandInServer:
    """What ``PlexClient`` and PlexAPI's ``Library`` need from a connected server.

    ``answers`` gives, per request, the XML that Plex sends or the error that the
    request raises. A request that is not named gets the answer of a healthy server
    with one music library called "Music".
    """

    friendlyName = "Stand-in"
    _baseurl = "http://plexhost:32400"
    _token = DUMMY_TOKEN

    def __init__(self, answers: Dict[str, Answer]) -> None:
        self.answers: Dict[str, Answer] = {
            LIBRARY_REQUEST: '<MediaContainer size="3" title1="Plex Library"/>',
            SECTIONS_REQUEST: _sections_xml("Music"),
            **answers,
        }
        self.requests: List[str] = []

    def query(self, key: str, *args: Any, **kwargs: Any) -> ElementTree.Element:
        self.requests.append(key)
        answer = self.answers[key]
        if isinstance(answer, BaseException):
            raise answer
        return ElementTree.fromstring(answer)

    def url(self, key: str, includeToken: bool = False) -> str:  # noqa: N803 (PlexAPI's name)
        return self._baseurl + key

    @property
    def library(self) -> Library:
        # As PlexAPI's server does: one request, then its own Library class.
        return Library(self, self.query(LIBRARY_REQUEST))


def _with_address(text: str) -> str:
    """An error text of the kind the HTTP library writes: it names the request."""
    return f"{text}: {PLEX_URL}{SECTIONS_REQUEST}?X-Plex-Token={DUMMY_TOKEN}"


def _reset() -> BaseException:
    # The error of the live outage runs.
    return requests.exceptions.ConnectionError(
        _with_address(
            "('Connection aborted.', RemoteDisconnected('Remote end closed connection "
            "without response'))"
        )
    )


def _timeout() -> BaseException:
    return requests.exceptions.ReadTimeout(_with_address("Read timed out. (read timeout=30)"))


def _refused() -> BaseException:
    return requests.exceptions.ConnectionError(
        _with_address("Failed to establish a new connection: [Errno 61] Connection refused")
    )


@pytest.fixture
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch data folder with its own config.yaml, away from the real profile."""
    (tmp_path / "config.yaml").write_text("", encoding="utf-8")
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    for name in ("PLEX_URL", "PLEX_TOKEN", "PLEX_LIBRARY_NAME"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _settings(profile: Path) -> Settings:
    return Settings(
        plex=PlexSettings(url=PLEX_URL, library_name="Music"),
        database=DatabaseSettings(
            path=str(profile / "plexmix.db"),
            faiss_index_path=str(profile / "embeddings.index"),
        ),
    )


@contextmanager
def _token_from_keyring() -> Iterator[None]:
    """Answer the token lookup of ``connect_plex`` without a keyring."""
    with patch(
        "plexmix.services.sync_service.credentials.get_plex_token", return_value=DUMMY_TOKEN
    ):
        yield


@contextmanager
def _plex(server: _StandInServer) -> Iterator[Mock]:
    """Let the real ``PlexClient`` connect to ``server``; yields the patched server class."""
    with _token_from_keyring():
        with patch("plexmix.plex.client.PlexServer", return_value=server) as server_cls:
            yield server_cls


def _failure(profile: Path, server: _StandInServer) -> PlexConnectionError:
    with _plex(server), pytest.raises(PlexConnectionError) as excinfo:
        connect_plex(_settings(profile))
    return excinfo.value


class TestLostConnection:
    @pytest.mark.parametrize("request_that_fails", [LIBRARY_REQUEST, SECTIONS_REQUEST])
    @pytest.mark.parametrize("make_error", [_reset, _timeout, _refused])
    def test_lost_connection_is_reported_as_unreachable(
        self, profile: Path, make_error: Any, request_that_fails: str
    ) -> None:
        server = _StandInServer({request_that_fails: make_error()})

        error = _failure(profile, server)

        assert error.code == "unreachable"
        assert str(error) == UNREACHABLE_MESSAGE
        assert error.args == (UNREACHABLE_MESSAGE,)
        # The connection was lost at that request, after Plex had answered before.
        assert server.requests[-1] == request_that_fails

    def test_outage_message_is_one_sentence_without_plex_text(self, profile: Path) -> None:
        error = _failure(profile, _StandInServer({LIBRARY_REQUEST: _reset()}))

        message = str(error)
        assert message.startswith("Plex stopped answering while the music library was being")
        assert "library setting was not checked" in message
        assert message.endswith(".") and message.count(".") == 1
        # Not the advice for a wrong library setting.
        assert "renamed or removed" not in message
        assert "Failed to select Plex library" not in message
        # No address, no credentials and nothing of the HTTP library's own text.
        for part in ("http", "plexhost", "user:", DUMMY_PASSWORD, DUMMY_TOKEN, "Connection", "("):
            assert part not in message
        # PlexAPI's error does not travel with it: the CLI prints this error.
        assert error.__cause__ is None
        assert error.__context__ is None

    def test_outage_log_holds_no_credentials(
        self, profile: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="plexmix.plex.client"):
            _failure(profile, _StandInServer({SECTIONS_REQUEST: _refused()}))

        assert (
            "Failed to select library: Failed to establish a new connection: [Errno 61] "
            "Connection refused: http://***@plexhost:32400/library/sections?X-Plex-Token=***"
        ) in caplog.text
        for secret in (DUMMY_PASSWORD, DUMMY_TOKEN, "user:"):
            assert secret not in caplog.text

    def test_sync_command_reports_the_outage(
        self, profile: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """What ``plexmix sync`` prints when Plex answers the first request only."""
        from plexmix.cli import sync_cmd

        settings = _settings(profile)
        with _plex(_StandInServer({LIBRARY_REQUEST: _reset()})):
            with patch.object(Settings, "load_from_file", return_value=settings):
                with pytest.raises(typer.Exit) as stopped:
                    sync_cmd.run_incremental_sync(embeddings=False)

        assert stopped.value.exit_code == 1
        printed = " ".join(capsys.readouterr().out.split())  # the console wraps long lines
        assert UNREACHABLE_MESSAGE in printed
        assert "renamed or removed" not in printed
        assert "check your configuration" not in printed
        # The sync stopped before it opened the database.
        assert not (profile / "plexmix.db").exists()


class TestLibraryMissing:
    @pytest.mark.parametrize(
        "sections",
        [
            pytest.param(_sections_xml("Tunes"), id="renamed"),
            pytest.param(_sections_xml(), id="removed"),
            pytest.param(NotFound("(404) not_found; /library/sections"), id="http-404"),
        ],
    )
    def test_missing_library_keeps_its_message_and_code(
        self, profile: Path, sections: Answer
    ) -> None:
        server = _StandInServer({SECTIONS_REQUEST: sections})

        error = _failure(profile, server)

        assert error.code == "library_missing"
        assert str(error) == LIBRARY_MISSING_MESSAGE
        assert error.args == (LIBRARY_MISSING_MESSAGE,)
        # Plex was asked for its libraries, and it answered.
        assert server.requests == [LIBRARY_REQUEST, SECTIONS_REQUEST]

    @pytest.mark.parametrize(
        "error_from_plex",
        [
            pytest.param(BadRequest("(500) internal_server_error; /library/sections"), id="500"),
            pytest.param(Unauthorized("(401) unauthorized; /library/sections"), id="401"),
            pytest.param(RuntimeError("unexpected"), id="other"),
        ],
    )
    def test_other_failures_keep_the_old_message_and_code(
        self, profile: Path, error_from_plex: BaseException
    ) -> None:
        error = _failure(profile, _StandInServer({SECTIONS_REQUEST: error_from_plex}))

        assert error.code == "library_missing"
        assert str(error) == LIBRARY_MISSING_MESSAGE


class TestMockedClient:
    """Other tests replace ``PlexClient`` by a mock. They keep the path they had."""

    def _failure(self, profile: Path, client: MagicMock) -> PlexConnectionError:
        client.connect.return_value = True
        client.select_library.return_value = False
        with _token_from_keyring():
            with patch("plexmix.services.sync_service.PlexClient", return_value=client):
                with pytest.raises(PlexConnectionError) as excinfo:
                    connect_plex(_settings(profile))
        client.select_library.assert_called_once_with("Music")
        return excinfo.value

    def test_mock_client_that_fails_is_still_library_missing(self, profile: Path) -> None:
        client = MagicMock()

        error = self._failure(profile, client)

        # The mock's attribute is a mock, not the client's own word for an outage.
        assert isinstance(client.last_select_failure, MagicMock)
        assert error.code == "library_missing"
        assert str(error) == LIBRARY_MISSING_MESSAGE

    @pytest.mark.parametrize("reason", ["", "not_found", "error"])
    def test_only_unreachable_takes_the_new_path(self, profile: Path, reason: str) -> None:
        client = MagicMock()
        client.last_select_failure = reason

        error = self._failure(profile, client)

        assert error.code == "library_missing"
        assert str(error) == LIBRARY_MISSING_MESSAGE

    def test_unreachable_reason_alone_decides(self, profile: Path) -> None:
        client = MagicMock()
        client.last_select_failure = "unreachable"

        error = self._failure(profile, client)

        assert error.code == "unreachable"
        assert str(error) == UNREACHABLE_MESSAGE


class TestSelectedLibrary:
    def test_selected_library_is_returned(self, profile: Path) -> None:
        server = _StandInServer({})

        with _plex(server) as server_cls:
            client = connect_plex(_settings(profile))

        assert isinstance(client, PlexClient)
        assert isinstance(client.music_library, MusicSection)
        assert client.music_library.title == "Music"
        assert client.last_select_failure == ""
        server_cls.assert_called_once_with(PLEX_URL, DUMMY_TOKEN)
        assert server.requests == [LIBRARY_REQUEST, SECTIONS_REQUEST]
