"""CLI error reports must never show secrets (Plex token, API keys, web UI password).

Typer shows the values of local variables in its error reports by default. Those
variables hold the Plex token, provider API keys and the web UI password, so every
Typer app in ``plexmix.cli`` turns that off.

The child-process tests run the real command and read both output streams, because
``CliRunner`` catches an exception before Typer formats its report.
"""

import logging
import os
import re
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import MagicMock

import pytest
import typer
import yaml

from plexmix.cli.main import app
from plexmix.database.models import Album, Artist, Playlist, Track
from plexmix.database.sqlite_manager import SQLiteManager

# The only secret values these tests use. None of them is a real credential.
DUMMY_PLEX_TOKEN = "dummy-plex-token-0000"
DUMMY_API_KEY = "dummy-api-key-0000"
DUMMY_UI_PASSWORD = "dummy-ui-password-0000"
DUMMY_SECRETS = (DUMMY_PLEX_TOKEN, DUMMY_API_KEY, DUMMY_UI_PASSWORD)

# The child process imports plexmix from this working tree, installed or not.
SRC_DIR = Path(__file__).resolve().parents[1] / "src"

# A top-level key whose list is never closed: the file stops being valid YAML here.
UNCLOSED_BRACKET = "playlist: [unclosed\n"

# An unquoted value that starts with "*" is read as an alias. The parser's own error
# message quotes the name of an alias it does not know, so here it would quote a secret.
UNDEFINED_ALIAS = f"oops: *{DUMMY_PLEX_TOKEN}\n"

# Colour codes are removed before a stream is searched, so they cannot split a value.
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _walk_typer_apps(root: typer.Typer) -> Iterator[typer.Typer]:
    """Yield ``root`` and every Typer app registered under it, at any depth."""
    yield root
    for group in root.registered_groups:
        sub_app = group.typer_instance
        if sub_app is not None:
            yield from _walk_typer_apps(sub_app)


def test_main_app_hides_local_variables() -> None:
    assert app.pretty_exceptions_show_locals is False


def test_every_cli_app_hides_local_variables() -> None:
    apps: List[typer.Typer] = list(_walk_typer_apps(app))
    # The main app plus the eight command groups registered on it.
    assert len(apps) >= 9
    for typer_app in apps:
        name = typer_app.info.name
        assert (
            typer_app.pretty_exceptions_show_locals is False
        ), f"Typer app {name!r} would print local variables (and so secrets) in error reports"


# ---------------------------------------------------------------------------
# Helpers for the tests that run a command and read what it prints
# ---------------------------------------------------------------------------


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    """A scratch profile: ``home`` (HOME), ``data`` (PLEXMIX_DATA_DIR) and an empty ``cwd``."""
    for name in ("home", "data", "cwd"):
        (tmp_path / name).mkdir()
    return tmp_path


def _write_config(
    data_dir: Path,
    extra: Optional[Dict[str, Dict[str, Any]]] = None,
    broken_tail: str = "",
) -> Path:
    """Write ``config.yaml`` into ``data_dir``.

    Every file holds the dummy API key and the dummy UI password, and keeps the
    database and the index inside ``data_dir``. ``extra`` adds settings per section.
    ``broken_tail`` is appended as raw text, below everything else.
    """
    config: Dict[str, Dict[str, Any]] = {
        "database": {
            "path": str(data_dir / "plexmix.db"),
            "faiss_index_path": str(data_dir / "embeddings.index"),
        },
        "embedding": {"custom_api_key": DUMMY_API_KEY},
        "ui": {"password": DUMMY_UI_PASSWORD},
    }
    for section, values in (extra or {}).items():
        config.setdefault(section, {}).update(values)
    config_path = data_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False) + broken_tail, encoding="utf-8")
    return config_path


def _create_library(db_path: Path, with_playlist: bool = False) -> int:
    """Create the database with one track. Return the saved playlist's ID, or 0 without one."""
    with SQLiteManager(str(db_path)) as db:
        artist_id = db.insert_artist(Artist(plex_key="artist-1", name="Stand-in Artist"))
        album_id = db.insert_album(
            Album(plex_key="album-1", title="Stand-in Album", artist_id=artist_id)
        )
        track_id = db.insert_track(
            Track(
                plex_key="track-1",
                title="Stand-in Track",
                artist_id=artist_id,
                album_id=album_id,
            )
        )
        assert db.count_tracks() == 1
        if not with_playlist:
            return 0
        playlist_id = db.insert_playlist(Playlist(name="Stand-in Playlist"))
        db.add_tracks_to_playlist(playlist_id, [track_id])
        return playlist_id


def _run_cli(
    args: List[str], profile: Path, extra_env: Optional[Dict[str, str]] = None
) -> "subprocess.CompletedProcess[str]":
    """Run the real CLI in a child process and return its exit status and both streams.

    The environment is built by hand. It is not a copy of ``os.environ``, which may
    hold real credentials: ``PATH`` is the only value taken from it.
    """
    data_dir = profile / "data"
    # A data folder without its own config.yaml falls back to the one in the home folder.
    assert (data_dir / "config.yaml").is_file(), "the scratch profile needs its own config.yaml"
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(profile / "home"),
        "PLEXMIX_DATA_DIR": str(data_dir),
        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        "NO_COLOR": "1",
        "COLUMNS": "200",
        "PLEX_TOKEN": DUMMY_PLEX_TOKEN,
        "PYTHONPATH": str(SRC_DIR),
        # The child leaves no bytecode files in the source tree.
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-c", "from plexmix.cli.main import app; app()", *args],
        cwd=profile / "cwd",  # empty, so no ``.env`` file is read
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _assert_no_secret(stdout: str, stderr: str) -> None:
    """Fail if one of the three dummy values is on either stream."""
    for stream, text in (("stdout", stdout), ("stderr", stderr)):
        visible = _ANSI_ESCAPE.sub("", text)
        for secret in DUMMY_SECRETS:
            assert secret not in visible, f"{stream} shows a secret value ({secret})"


def _assert_failed_without_secret(result: "subprocess.CompletedProcess[str]") -> None:
    assert "No module named 'plexmix'" not in result.stderr, "the child could not import plexmix"
    assert result.returncode != 0, f"expected a failure, got exit status 0:\n{result.stdout}"
    _assert_no_secret(result.stdout, result.stderr)


class _PlexStandIn(BaseHTTPRequestHandler):
    """A stand-in Plex server: it connects and lists one music library, then fails.

    ``/``, ``/library`` and ``/library/sections`` answer with a few lines of XML. Every
    other request, so every listing of artists, albums or tracks, gets HTTP 500.
    """

    ANSWERS = {
        "": b'<MediaContainer size="0" friendlyName="standin" machineIdentifier="standin-1" '
        b'version="1.40.0.0" platform="Linux" myPlex="0"/>',
        "/library": b'<MediaContainer size="1" title1="Plex Library"/>',
        "/library/sections": b'<MediaContainer size="1"><Directory key="1" type="artist" '
        b'title="Music" agent="tv.plex.agents.music" scanner="Plex Music" language="en" '
        b'uuid="standin-library"/></MediaContainer>',
    }

    def do_GET(self) -> None:  # noqa: N802 (the name http.server calls)
        body = self.ANSWERS.get(self.path.split("?", 1)[0].rstrip("/"))
        if body is None:
            self._send(500, b"<html><body>stand-in failure</body></html>", "text/html")
        else:
            self._send(200, body, "text/xml")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the test output quiet."""


@contextmanager
def _plex_stand_in() -> Iterator[int]:
    """Run the stand-in on this machine only (127.0.0.1, a free port); yield the port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PlexStandIn)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def _root_logging_restored() -> Iterator[None]:
    """Undo what the CLI's start-up does to the root logger: it replaces every handler."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        yield
    finally:
        for handler in root.handlers:
            if handler not in handlers:
                handler.close()
        root.handlers[:] = handlers
        root.setLevel(level)


# ---------------------------------------------------------------------------
# The five triggers, each run as a child process
# ---------------------------------------------------------------------------


def test_malformed_config_prints_no_secret(profile: Path) -> None:
    # A syntax error stops the load before the environment is read, so the token
    # has to be in the file itself, above the error.
    _write_config(
        profile / "data", {"plex": {"token": DUMMY_PLEX_TOKEN}}, broken_tail=UNCLOSED_BRACKET
    )

    result = _run_cli(["db", "info"], profile)

    _assert_failed_without_secret(result)


def test_malformed_config_whose_error_quotes_a_value_prints_no_secret(profile: Path) -> None:
    # The unclosed bracket above makes a parser error that quotes nothing, so that
    # test passes as long as local variables stay hidden. This file makes one that
    # quotes the token: the settings loader has to drop the parser's message too.
    _write_config(
        profile / "data", {"plex": {"token": DUMMY_PLEX_TOKEN}}, broken_tail=UNDEFINED_ALIAS
    )

    result = _run_cli(["db", "info"], profile)

    _assert_failed_without_secret(result)
    # It failed on the file, and the report says where.
    assert "could not be parsed" in result.stderr
    assert "config.yaml" in result.stderr


def test_wrong_config_value_prints_no_secret(profile: Path) -> None:
    _write_config(profile / "data", {"audio": {"workers": "many"}})

    result = _run_cli(["db", "info"], profile)

    _assert_failed_without_secret(result)


def test_bad_environment_value_prints_no_secret(profile: Path) -> None:
    _write_config(profile / "data")

    result = _run_cli(["db", "info"], profile, extra_env={"AUDIO_WORKERS": "four"})

    _assert_failed_without_secret(result)


def test_unreachable_provider_prints_no_secret(profile: Path) -> None:
    # Nothing listens on port 9 of this machine, so the request is refused at once.
    _write_config(
        profile / "data",
        {
            "embedding": {
                "default_provider": "custom",
                "custom_endpoint": "http://127.0.0.1:9/v1",
                "custom_model": "standin",
                "custom_dimension": 8,
            }
        },
    )
    _create_library(profile / "data" / "plexmix.db")

    result = _run_cli(["embeddings", "generate"], profile)

    _assert_failed_without_secret(result)


def test_wrong_output_path_prints_no_secret(profile: Path) -> None:
    _write_config(profile / "data")
    playlist_id = _create_library(profile / "data" / "plexmix.db", with_playlist=True)
    output_path = profile / "missing" / "out.json"
    assert playlist_id == 1
    assert not output_path.parent.exists()

    result = _run_cli(["playlist", "export", str(playlist_id), "-o", str(output_path)], profile)

    _assert_failed_without_secret(result)
    assert not output_path.exists()


# ---------------------------------------------------------------------------
# Credentials that PlexMix refuses or that Plex repeats, each run as a child process
# ---------------------------------------------------------------------------


def test_failed_plex_listing_prints_no_url_credentials(profile: Path) -> None:
    """Plex connects and then cannot list the library: its error holds the full address."""
    with _plex_stand_in() as port:
        _write_config(
            profile / "data",
            {
                "plex": {
                    "url": f"http://leakuser:{DUMMY_UI_PASSWORD}@127.0.0.1:{port}",
                    "library_name": "Music",
                }
            },
        )

        result = _run_cli(["sync", "incremental", "--no-embeddings"], profile)

    _assert_failed_without_secret(result)
    log = (profile / "data" / "plexmix.log").read_text(encoding="utf-8")
    # The sync got as far as the listing. The user is told so in one plain sentence,
    # which holds no address at all.
    sentence = "Plex could not list the library's artists, so nothing was changed."
    assert sentence in result.stderr
    assert sentence in log
    # The command itself prints the sentence and ends with status 1. It does not
    # leave the error to Typer, whose report would be a traceback.
    assert result.returncode == 1
    assert sentence in result.stdout
    assert "Traceback" not in result.stderr
    # Plex's own error text is in the log only, and its address is shown without credentials.
    shown = f"Failed to retrieve artists: (500) internal_server_error; http://***@127.0.0.1:{port}/"
    assert shown in result.stderr
    assert shown in log
    assert "completed successfully" not in result.stdout
    for text in (result.stdout, result.stderr, log):
        assert "leakuser" not in text
        assert DUMMY_UI_PASSWORD not in text
        assert DUMMY_PLEX_TOKEN not in text


def test_local_token_with_line_break_is_refused_and_not_shown(profile: Path) -> None:
    token = "dummy-api\nkey-0000"  # copied across a line wrap
    _write_config(
        profile / "data",
        {
            "ai": {
                "default_provider": "local",
                "local_mode": "endpoint",
                "local_endpoint": "http://127.0.0.1:9/v1/chat/completions",
                "local_auth_token": token,
            }
        },
    )
    _create_library(profile / "data" / "plexmix.db")

    result = _run_cli(
        ["tags", "generate", "--provider", "local", "--no-regenerate-embeddings"], profile
    )

    _assert_failed_without_secret(result)
    log = (profile / "data" / "plexmix.log").read_text(encoding="utf-8")
    # The user is told why the provider is not ready, on the terminal and in the log file.
    told = "Local endpoint auth token contains invalid characters"
    assert told in result.stderr
    assert told in log
    assert "AI provider 'local' is not ready" in result.stdout
    for text in (result.stdout, result.stderr, log):
        for form in (token, repr(token)[1:-1], "dummy-api", "key-0000"):
            assert form not in text


# ---------------------------------------------------------------------------
# The setup wizard, in this process
# ---------------------------------------------------------------------------


def test_wizard_crash_prints_no_typed_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``config init`` crashes after its hidden prompts; the report must not show the answers."""
    # The wizard loads the existing settings after its prompts, and this file is malformed.
    _write_config(tmp_path, {"plex": {"token": DUMMY_PLEX_TOKEN}}, broken_tail=UNCLOSED_BRACKET)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("TYPER_STANDARD_TRACEBACK", raising=False)
    monkeypatch.delenv("_TYPER_STANDARD_TRACEBACK", raising=False)
    monkeypatch.chdir(tmp_path)  # no ``.env`` file here
    monkeypatch.setattr("plexmix.config.settings._config_path_override", None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # ``app()`` installs Typer's hook

    def fake_prompt(text: str, *args: Any, **kwargs: Any) -> Any:
        if "token" in text.lower():
            return DUMMY_PLEX_TOKEN
        if "api key" in text.lower():
            return DUMMY_API_KEY
        return kwargs.get("default")

    def fake_confirm(text: str, *args: Any, **kwargs: Any) -> bool:
        return "Gemini" in text  # one provider, so one API key is typed

    fake_plex = MagicMock()
    fake_plex.connect.return_value = True
    fake_plex.get_music_libraries.return_value = ["Music"]
    fake_credentials = MagicMock()
    monkeypatch.setattr("plexmix.cli.config_cmd.typer.prompt", fake_prompt)
    monkeypatch.setattr("plexmix.cli.config_cmd.typer.confirm", fake_confirm)
    monkeypatch.setattr("plexmix.cli.config_cmd.PlexClient", MagicMock(return_value=fake_plex))
    monkeypatch.setattr("plexmix.cli.config_cmd.credentials", fake_credentials)

    with _root_logging_restored():
        # A clean exit raises SystemExit, which is not an Exception: it would fail the test.
        with pytest.raises(Exception) as crash:
            app(["config", "init"])
        error = crash.value
        typer.main.except_hook(type(error), error, error.__traceback__)
    captured = capsys.readouterr()

    # Both secrets had been typed when the wizard crashed, and a report was printed.
    fake_credentials.store_plex_token.assert_called_once_with(DUMMY_PLEX_TOKEN)
    fake_credentials.store_google_api_key.assert_called_once_with(DUMMY_API_KEY)
    assert "Traceback" in captured.err
    _assert_no_secret(captured.out, captured.err)
