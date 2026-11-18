"""Tests for the `plexmix config` CLI commands (init, test, show).

Each test mocks external dependencies (Plex, credentials, settings) so tests
run quickly without network or file-system side-effects.
"""

import re
from pathlib import Path
from typing import Any, List, Tuple
from unittest.mock import patch, MagicMock

import pytest
from click.testing import Result
from typer.testing import CliRunner

from plexmix.cli.main import app
from plexmix.database.models import SyncHistory
from plexmix.plex.client import PlexReadInterrupted
from plexmix.plex.sync import REASON_THRESHOLD, SyncOutcome

runner = CliRunner()

# The only "secrets" these tests use are the project's dummy values.
DUMMY_PASSWORD = "dummy-ui-password-0000"
DUMMY_TOKEN = "dummy-plex-token-0000"
DUMMY_API_KEY = "dummy-api-key-0000"


@pytest.fixture
def scratch_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI at an empty data folder so no real profile is read."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path))
    for name in ("PLEX_URL", "PLEX_TOKEN", "PLEX_LIBRARY_NAME"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _write_config(folder: Path, plex_url: str) -> None:
    (folder / "config.yaml").write_text(f'plex:\n  url: "{plex_url}"\n  library_name: Music\n')


# (configured address, what the CLI may show of it)
ODD_ADDRESSES = [
    # a password with spaces: a working address, the HTTP library percent-encodes them
    ("http://leakuser:dummy ui password-0000@plexhost:32400", "http://***@plexhost:32400"),
    # a mistyped scheme
    (f"http:/leakuser:{DUMMY_PASSWORD}@plexhost:32400", "http:/***@plexhost:32400"),
    (f"http//leakuser:{DUMMY_PASSWORD}@plexhost:32400", "http//***@plexhost:32400"),
    (f"//leakuser:{DUMMY_PASSWORD}@plexhost:32400", "//***@plexhost:32400"),
    # nothing after the "@"
    (f"http://leakuser:{DUMMY_PASSWORD}@", "http://***@"),
    # a token pasted into the address, as in Plex's own "View XML" links
    (
        f"http://plexhost:32400/?X-Plex-Token={DUMMY_TOKEN}",
        "http://plexhost:32400/?X-Plex-Token=***",
    ),
]
ODD_ADDRESS_SECRETS = ("leakuser", DUMMY_PASSWORD, "dummy ui", "password-0000", DUMMY_TOKEN)


# ---------------------------------------------------------------------------
# config test
# ---------------------------------------------------------------------------


class TestConfigTest:
    """Tests for `plexmix config test`."""

    @patch("plexmix.cli.config_cmd.Settings")
    def test_no_plex_url_configured(self, mock_settings_cls):
        mock_settings = MagicMock()
        mock_settings.plex.url = None
        mock_settings_cls.load_from_file.return_value = mock_settings

        result = runner.invoke(app, ["config", "test"])
        assert result.exit_code == 1
        assert "No Plex URL" in result.output

    @patch("plexmix.cli.config_cmd.credentials")
    @patch("plexmix.cli.config_cmd.Settings")
    def test_no_plex_token(self, mock_settings_cls, mock_creds):
        mock_settings = MagicMock()
        mock_settings.plex.url = "http://localhost:32400"
        mock_settings_cls.return_value = mock_settings

        mock_creds.get_plex_token.return_value = None

        result = runner.invoke(app, ["config", "test"])
        assert result.exit_code == 1
        assert "No Plex token" in result.output

    @patch("plexmix.cli.config_cmd.PlexClient")
    @patch("plexmix.cli.config_cmd.credentials")
    @patch("plexmix.cli.config_cmd.Settings")
    def test_connection_success(self, mock_settings_cls, mock_creds, mock_plex_cls):
        mock_settings = MagicMock()
        mock_settings.plex.url = "http://localhost:32400"
        mock_settings.plex.library_name = "Music"
        mock_settings_cls.return_value = mock_settings

        mock_creds.get_plex_token.return_value = "valid-token"

        mock_client = MagicMock()
        mock_client.connect.return_value = True
        mock_client.server = MagicMock()
        mock_client.server.friendlyName = "MyPlexServer"
        mock_client.server.version = "1.32.0"
        mock_client.server.platform = "Linux"
        mock_client.select_library.return_value = True
        mock_plex_cls.return_value = mock_client

        result = runner.invoke(app, ["config", "test"])
        assert result.exit_code == 0
        assert "Successfully connected" in result.output

    @patch("plexmix.cli.config_cmd.PlexClient")
    @patch("plexmix.cli.config_cmd.credentials")
    @patch("plexmix.cli.config_cmd.Settings")
    def test_connection_failure(self, mock_settings_cls, mock_creds, mock_plex_cls):
        mock_settings = MagicMock()
        mock_settings.plex.url = "http://localhost:32400"
        mock_settings_cls.return_value = mock_settings

        mock_creds.get_plex_token.return_value = "bad-token"

        mock_client = MagicMock()
        mock_client.connect.return_value = False
        mock_plex_cls.return_value = mock_client

        result = runner.invoke(app, ["config", "test"])
        assert result.exit_code == 1
        assert "Connection failed" in result.output

    @patch("plexmix.cli.config_cmd.PlexClient")
    @patch("plexmix.cli.config_cmd.credentials")
    @patch("plexmix.cli.config_cmd.Settings")
    def test_connection_success_but_library_not_found(
        self, mock_settings_cls, mock_creds, mock_plex_cls
    ):
        mock_settings = MagicMock()
        mock_settings.plex.url = "http://localhost:32400"
        mock_settings.plex.library_name = "NonexistentLib"
        mock_settings_cls.return_value = mock_settings

        mock_creds.get_plex_token.return_value = "valid-token"

        mock_client = MagicMock()
        mock_client.connect.return_value = True
        mock_client.server = MagicMock()
        mock_client.server.friendlyName = "MyPlexServer"
        mock_client.server.version = "1.32.0"
        mock_client.server.platform = "Linux"
        mock_client.select_library.return_value = False
        mock_plex_cls.return_value = mock_client

        result = runner.invoke(app, ["config", "test"])
        assert result.exit_code == 0
        assert "not found" in result.output

    @patch("plexmix.cli.config_cmd.PlexClient")
    @patch("plexmix.cli.config_cmd.credentials")
    @patch("plexmix.cli.config_cmd.Settings")
    def test_config_test_masks_url_and_prints_no_token_length(
        self,
        mock_settings_cls: MagicMock,
        mock_creds: MagicMock,
        mock_plex_cls: MagicMock,
        scratch_profile: Path,
    ) -> None:
        plex_url = f"http://user:{DUMMY_PASSWORD}@plexhost:32400"
        mock_settings = MagicMock()
        mock_settings.plex.url = plex_url
        mock_settings.plex.library_name = None
        mock_settings_cls.load_from_file.return_value = mock_settings

        mock_creds.get_plex_token.return_value = DUMMY_TOKEN

        mock_client = MagicMock()
        mock_client.connect.return_value = True
        mock_client.server.friendlyName = "MyPlexServer"
        mock_client.server.version = "1.32.0"
        mock_client.server.platform = "Linux"
        mock_plex_cls.return_value = mock_client

        result = runner.invoke(app, ["config", "test"])

        assert result.exit_code == 0
        assert "Server URL: http://***@plexhost:32400" in result.output
        assert DUMMY_PASSWORD not in result.output
        assert "user:" not in result.output
        # No token, and nothing about how long it is.
        assert DUMMY_TOKEN not in result.output
        assert "Token length" not in result.output
        assert not re.search(r"\d+ characters", result.output)
        # Only the display is masked: the connection still gets the full address.
        mock_plex_cls.assert_called_once_with(plex_url, DUMMY_TOKEN)

    @pytest.mark.parametrize("plex_url, shown", ODD_ADDRESSES)
    @patch("plexmix.cli.config_cmd.PlexClient")
    @patch("plexmix.cli.config_cmd.credentials")
    @patch("plexmix.cli.config_cmd.Settings")
    def test_config_test_masks_odd_addresses(
        self,
        mock_settings_cls: MagicMock,
        mock_creds: MagicMock,
        mock_plex_cls: MagicMock,
        plex_url: str,
        shown: str,
        scratch_profile: Path,
    ) -> None:
        mock_settings = MagicMock()
        mock_settings.plex.url = plex_url
        mock_settings.plex.library_name = None
        mock_settings_cls.load_from_file.return_value = mock_settings
        mock_creds.get_plex_token.return_value = "another-dummy-token"
        mock_plex_cls.return_value.connect.return_value = False

        result = runner.invoke(app, ["config", "test"])

        assert result.exit_code == 1
        assert f"Server URL: {shown}\n" in result.output
        for secret in ODD_ADDRESS_SECRETS:
            assert secret not in result.output
        # Only the display is masked: the connection still gets the full address.
        mock_plex_cls.assert_called_once_with(plex_url, "another-dummy-token")

    @patch("plexmix.cli.config_cmd.PlexClient")
    @patch("plexmix.cli.config_cmd.credentials")
    @patch("plexmix.cli.config_cmd.Settings")
    def test_config_test_shows_plain_url_unchanged(
        self,
        mock_settings_cls: MagicMock,
        mock_creds: MagicMock,
        mock_plex_cls: MagicMock,
        scratch_profile: Path,
    ) -> None:
        mock_settings = MagicMock()
        mock_settings.plex.url = "http://plexhost:32400"
        mock_settings.plex.library_name = None
        mock_settings_cls.load_from_file.return_value = mock_settings
        mock_creds.get_plex_token.return_value = DUMMY_TOKEN
        mock_client = MagicMock()
        mock_client.connect.return_value = False
        mock_plex_cls.return_value = mock_client

        result = runner.invoke(app, ["config", "test"])

        assert result.exit_code == 1
        assert "Server URL: http://plexhost:32400" in result.output
        assert "Token length" not in result.output


# ---------------------------------------------------------------------------
# config show
# ---------------------------------------------------------------------------


class TestConfigShow:
    """Tests for `plexmix config show`."""

    @patch("plexmix.cli.config_cmd.get_config_path")
    def test_no_config_file_exits_error(self, mock_path, tmp_path):
        mock_path.return_value = tmp_path / "nonexistent.yaml"

        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 1

    def test_config_show_masks_url_credentials(self, scratch_profile: Path) -> None:
        _write_config(scratch_profile, f"https://user:{DUMMY_PASSWORD}@plexhost:32400/web")

        result = runner.invoke(app, ["config", "show"])

        assert result.exit_code == 0
        # Host, port and path are shown as configured; the credentials are not.
        assert "https://***@plexhost:32400/web" in result.output
        assert DUMMY_PASSWORD not in result.output
        assert "user:" not in result.output

    def test_config_show_masks_credentials_without_a_scheme(self, scratch_profile: Path) -> None:
        _write_config(scratch_profile, f"user:{DUMMY_PASSWORD}@plexhost:32400")

        result = runner.invoke(app, ["config", "show"])

        assert result.exit_code == 0
        assert "***@plexhost:32400" in result.output
        assert DUMMY_PASSWORD not in result.output
        assert "user:" not in result.output

    @pytest.mark.parametrize("plex_url, shown", ODD_ADDRESSES)
    def test_config_show_masks_odd_addresses(
        self, plex_url: str, shown: str, scratch_profile: Path
    ) -> None:
        _write_config(scratch_profile, plex_url)

        result = runner.invoke(app, ["config", "show"])

        assert result.exit_code == 0
        assert f" {shown} " in result.output
        for secret in ODD_ADDRESS_SECRETS:
            assert secret not in result.output

    def test_config_show_keeps_url_without_credentials(self, scratch_profile: Path) -> None:
        _write_config(scratch_profile, "http://plexhost:32400")

        result = runner.invoke(app, ["config", "show"])

        assert result.exit_code == 0
        assert "http://plexhost:32400" in result.output
        assert "***" not in result.output

    def test_config_show_does_not_change_the_stored_address(self, scratch_profile: Path) -> None:
        plex_url = f"http://user:{DUMMY_PASSWORD}@plexhost:32400"
        _write_config(scratch_profile, plex_url)
        before = (scratch_profile / "config.yaml").read_text()

        result = runner.invoke(app, ["config", "show"])

        assert result.exit_code == 0
        assert (scratch_profile / "config.yaml").read_text() == before
        assert plex_url in before


# ---------------------------------------------------------------------------
# config init
# ---------------------------------------------------------------------------


class TestConfigInit:
    """Tests for `plexmix config init`."""

    @patch("plexmix.cli.config_cmd.credentials")
    @patch("plexmix.cli.config_cmd.PlexClient")
    def test_connection_fails_during_init(self, mock_plex_cls, mock_creds):
        mock_client = MagicMock()
        mock_client.connect.return_value = False
        mock_plex_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["config", "init"],
            input="http://localhost:32400\nfake-token\n",
        )
        assert result.exit_code == 1
        assert "Failed to connect" in result.output

    @patch("plexmix.cli.config_cmd.credentials")
    @patch("plexmix.cli.config_cmd.PlexClient")
    def test_no_music_libraries_found(self, mock_plex_cls, mock_creds):
        mock_client = MagicMock()
        mock_client.connect.return_value = True
        mock_client.get_music_libraries.return_value = []
        mock_plex_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["config", "init"],
            input="http://localhost:32400\nfake-token\n",
        )
        assert result.exit_code == 1
        assert "No music libraries" in result.output


# ---------------------------------------------------------------------------
# config init: the first sync
# ---------------------------------------------------------------------------


def _flat(text: str) -> str:
    """The output as one line: the console wraps a long sentence at the terminal width."""
    return " ".join(text.split())


def _run_wizard_through_its_sync(
    profile: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: MagicMock,
    terminal: bool,
    remove: bool = False,
) -> Tuple[Result, List[str]]:
    """Run ``config init`` and accept the first sync it offers, with the engine patched.

    Plex, the keyring, the providers and the sync's database are mocks, ``engine``
    stands in for the ``SyncEngine`` object, and ``terminal`` says whether the sync
    has a terminal to ask in. ``remove`` answers the removal question. Returns the
    result and every yes/no question that was asked.
    """
    _write_config(profile, "http://plexhost:32400")
    monkeypatch.chdir(profile)  # no ``.env`` file here
    monkeypatch.setattr("plexmix.config.settings._config_path_override", None)
    asked: List[str] = []

    def answer_prompt(text: str, *args: Any, **kwargs: Any) -> Any:
        if "token" in text.lower():
            return DUMMY_TOKEN
        if "api key" in text.lower():
            return DUMMY_API_KEY
        return kwargs.get("default")

    def answer_confirm(text: str, *args: Any, **kwargs: Any) -> bool:
        question = text.strip()
        asked.append(question)
        if question.startswith("Remove "):
            return remove
        # One provider, and then the first sync.
        return "Gemini" in question or "Run initial sync now" in question

    wizard_plex = MagicMock()
    wizard_plex.connect.return_value = True
    wizard_plex.get_music_libraries.return_value = ["Music"]
    sync_db = MagicMock()
    sync_db.__enter__ = MagicMock(return_value=sync_db)
    sync_db.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr("plexmix.cli.main.setup_logging", MagicMock())
    monkeypatch.setattr("plexmix.cli.config_cmd.typer.prompt", answer_prompt)
    monkeypatch.setattr("plexmix.cli.config_cmd.typer.confirm", answer_confirm)
    monkeypatch.setattr("plexmix.cli.config_cmd.PlexClient", MagicMock(return_value=wizard_plex))
    monkeypatch.setattr("plexmix.cli.config_cmd.credentials", MagicMock())
    monkeypatch.setattr("plexmix.cli.sync_cmd.connect_plex", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(
        "plexmix.cli.sync_cmd._build_ai_provider", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr(
        "plexmix.cli.sync_cmd._build_embedding_generator", MagicMock(return_value=None)
    )
    monkeypatch.setattr("plexmix.cli.sync_cmd.SQLiteManager", MagicMock(return_value=sync_db))
    monkeypatch.setattr("plexmix.cli.sync_cmd.SyncEngine", MagicMock(return_value=engine))
    monkeypatch.setattr("plexmix.cli.sync_cmd.can_prompt", lambda: terminal)

    result = runner.invoke(app, ["config", "init"])
    return result, asked


def _engine_that_held_a_removal() -> MagicMock:
    """A mock for the ``SyncEngine`` object whose sync held 5,443 of 5,943 tracks."""
    engine = MagicMock()
    engine.incremental_sync.return_value = SyncHistory(tracks_added=5, tracks_updated=2)
    engine.outcome = SyncOutcome(
        listed={"artists": 1, "albums": 1, "tracks": 500},
        library_total=5943,
        held=5443,
        reason=REASON_THRESHOLD,
        message=(
            "Plex no longer lists 5,443 of 5,943 tracks. They were kept, because a "
            "removal this large needs confirmation."
        ),
    )
    return engine


class TestConfigInitSync:
    """The first sync that the setup wizard offers follows the sync rules."""

    def test_wizard_sync_with_held_removal_does_not_remove(
        self, scratch_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _engine_that_held_a_removal()

        result, asked = _run_wizard_through_its_sync(
            scratch_profile, monkeypatch, engine, terminal=False
        )

        out = _flat(result.stdout)
        assert "Configuration saved" in out  # the wizard got as far as its sync
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert "5,443 tracks were kept." in out
        assert "--allow-removals" in out
        assert "completed" not in out.lower()
        # The wizard asked for no large removal, and none was applied or offered.
        engine.incremental_sync.assert_called_once()
        assert engine.incremental_sync.call_args.kwargs["allow_large_removals"] is False
        engine.remove_unlisted_tracks.assert_not_called()
        assert not [question for question in asked if question.startswith("Remove ")]

    def test_wizard_sync_in_a_terminal_asks_before_removing(
        self, scratch_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _engine_that_held_a_removal()

        result, asked = _run_wizard_through_its_sync(
            scratch_profile, monkeypatch, engine, terminal=True, remove=False
        )

        out = _flat(result.stdout)
        assert result.exit_code == 0, f"output={result.output!r}"
        assert asked[-1] == "Remove 5,443 of 5,943 tracks?"
        assert "5,443 tracks were kept." in out
        assert "completed" not in out.lower()
        engine.remove_unlisted_tracks.assert_not_called()

    def test_wizard_sync_stopped_exits_nonzero(
        self, scratch_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = MagicMock()
        engine.incremental_sync.side_effect = PlexReadInterrupted("tracks", read=522, listed=5943)
        engine.outcome = SyncOutcome()

        result, _asked = _run_wizard_through_its_sync(
            scratch_profile, monkeypatch, engine, terminal=True
        )

        out = _flat(result.stdout)
        assert "Configuration saved" in out  # the wizard got as far as its sync
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)  # no error report
        assert (
            "Plex stopped answering after 522 of 5,943 tracks were read, "
            "so nothing was changed." in out
        )
        assert "completed" not in out.lower()
        for secret in (DUMMY_TOKEN, DUMMY_API_KEY):
            assert secret not in result.output
