"""Tests for the `plexmix config` CLI commands (init, test, show).

Each test mocks external dependencies (Plex, credentials, settings) so tests
run quickly without network or file-system side-effects.
"""

from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from plexmix.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# config test
# ---------------------------------------------------------------------------


class TestConfigTest:
    """Tests for `plexmix config test`."""

    @patch("plexmix.cli.config_cmd.Settings")
    def test_no_plex_url_configured(self, mock_settings_cls):
        mock_settings = MagicMock()
        mock_settings.plex.url = None
        mock_settings_cls.return_value = mock_settings

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
