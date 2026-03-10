"""Tests for cli/main.py helper functions and CLI commands."""
import pytest
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

from plexmix.cli.main import (
    app,
    _canonical_ai_provider,
    _resolve_ai_api_key,
    _local_provider_kwargs,
    _build_ai_provider,
)
from plexmix.config.settings import Settings

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helper function tests (pure logic, no mocks needed)
# ---------------------------------------------------------------------------

class TestCanonicalAiProvider:
    def test_none_returns_gemini(self):
        assert _canonical_ai_provider(None) == "gemini"

    def test_empty_string_returns_gemini(self):
        assert _canonical_ai_provider("") == "gemini"

    def test_anthropic_maps_to_claude(self):
        assert _canonical_ai_provider("anthropic") == "claude"

    def test_anthropic_case_insensitive(self):
        assert _canonical_ai_provider("Anthropic") == "claude"
        assert _canonical_ai_provider("ANTHROPIC") == "claude"

    def test_openai_passthrough(self):
        assert _canonical_ai_provider("openai") == "openai"

    def test_gemini_passthrough(self):
        assert _canonical_ai_provider("gemini") == "gemini"

    def test_cohere_passthrough(self):
        assert _canonical_ai_provider("cohere") == "cohere"

    def test_local_passthrough(self):
        assert _canonical_ai_provider("local") == "local"


class TestResolveAiApiKey:
    @patch("plexmix.cli.main.credentials")
    def test_gemini_delegates(self, mock_creds):
        mock_creds.get_google_api_key.return_value = "gkey"
        assert _resolve_ai_api_key("gemini") == "gkey"

    @patch("plexmix.cli.main.credentials")
    def test_openai_delegates(self, mock_creds):
        mock_creds.get_openai_api_key.return_value = "okey"
        assert _resolve_ai_api_key("openai") == "okey"

    @patch("plexmix.cli.main.credentials")
    def test_anthropic_maps_to_claude(self, mock_creds):
        mock_creds.get_anthropic_api_key.return_value = "akey"
        assert _resolve_ai_api_key("anthropic") == "akey"

    @patch("plexmix.cli.main.credentials")
    def test_cohere_delegates(self, mock_creds):
        mock_creds.get_cohere_api_key.return_value = "ckey"
        assert _resolve_ai_api_key("cohere") == "ckey"

    @patch("plexmix.cli.main.credentials")
    def test_unknown_returns_none(self, mock_creds):
        assert _resolve_ai_api_key("unknown_provider") is None


def _make_settings_yaml(tmp_path, extra=""):
    """Create a minimal config YAML with required database fields."""
    config_file = tmp_path / "config.yaml"
    base = (
        "database:\n"
        f"  path: {tmp_path / 'test.db'}\n"
        f"  faiss_index_path: {tmp_path / 'test.index'}\n"
    )
    config_file.write_text(base + extra)
    return str(config_file)


class TestLocalProviderKwargs:
    def test_extracts_fields(self, tmp_path):
        cfg = _make_settings_yaml(
            tmp_path,
            "ai:\n"
            "  local_mode: endpoint\n"
            "  local_endpoint: http://localhost:8080\n"
            "  local_auth_token: tok123\n"
            "  local_max_output_tokens: 500\n",
        )
        settings = Settings.load_from_file(cfg)
        result = _local_provider_kwargs(settings)
        assert result == {
            "local_mode": "endpoint",
            "local_endpoint": "http://localhost:8080",
            "local_auth_token": "tok123",
            "local_max_output_tokens": 500,
        }


class TestBuildAiProvider:
    @patch("plexmix.cli.main.get_ai_provider")
    @patch("plexmix.cli.main._resolve_ai_api_key", return_value="key")
    def test_success_returns_provider(self, mock_resolve, mock_get, tmp_path):
        mock_provider = MagicMock()
        mock_get.return_value = mock_provider
        settings = Settings.load_from_file(_make_settings_yaml(tmp_path))
        result = _build_ai_provider(settings)
        assert result is mock_provider

    @patch("plexmix.cli.main.get_ai_provider", side_effect=ValueError("bad"))
    @patch("plexmix.cli.main._resolve_ai_api_key", return_value=None)
    def test_value_error_returns_none(self, mock_resolve, mock_get, tmp_path):
        settings = Settings.load_from_file(_make_settings_yaml(tmp_path))
        result = _build_ai_provider(settings)
        assert result is None

    @patch("plexmix.cli.main.get_ai_provider", side_effect=ValueError("bad"))
    @patch("plexmix.cli.main._resolve_ai_api_key", return_value=None)
    def test_silent_suppresses_output(self, mock_resolve, mock_get, tmp_path):
        settings = Settings.load_from_file(_make_settings_yaml(tmp_path))
        result = _build_ai_provider(settings, silent=True)
        assert result is None


# ---------------------------------------------------------------------------
# CLI help output tests (exercises Typer registration)
# ---------------------------------------------------------------------------

class TestHelpOutput:
    def test_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "plexmix" in result.output.lower() or "AI" in result.output

    def test_config_help(self):
        result = runner.invoke(app, ["config", "--help"])
        assert result.exit_code == 0
        assert "config" in result.output.lower()

    def test_db_help(self):
        result = runner.invoke(app, ["db", "--help"])
        assert result.exit_code == 0

    def test_sync_help(self):
        result = runner.invoke(app, ["sync", "--help"])
        assert result.exit_code == 0

    def test_tags_help(self):
        result = runner.invoke(app, ["tags", "--help"])
        assert result.exit_code == 0

    def test_embeddings_help(self):
        result = runner.invoke(app, ["embeddings", "--help"])
        assert result.exit_code == 0

    def test_audio_help(self):
        result = runner.invoke(app, ["audio", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Mocked command tests
# ---------------------------------------------------------------------------

class TestConfigShow:
    @patch("plexmix.cli.main.get_config_path")
    def test_no_config_exits_error(self, mock_path, tmp_path):
        mock_path.return_value = tmp_path / "nonexistent.yaml"
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code != 0

    @patch("plexmix.cli.main.get_config_path")
    @patch("plexmix.cli.main.Settings.load_from_file")
    def test_with_config_shows_table(self, mock_load, mock_path, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("ai:\n  default_provider: openai\n")
        mock_path.return_value = config_file

        # Create a mock Settings object to avoid pydantic validation issues
        mock_settings = MagicMock()
        mock_settings.plex.url = "http://localhost:32400"
        mock_settings.plex.library_name = "Music"
        mock_settings.database.path = str(tmp_path / "test.db")
        mock_settings.ai.default_provider = "openai"
        mock_settings.embedding.default_provider = "gemini"
        mock_settings.playlist.default_length = 50
        mock_load.return_value = mock_settings

        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "PlexMix Configuration" in result.output


class TestDbInfo:
    @patch("plexmix.cli.main.Settings")
    def test_no_db_shows_message(self, mock_settings_cls, tmp_path):
        mock_settings = Settings.load_from_file(_make_settings_yaml(tmp_path))
        mock_settings.database.path = str(tmp_path / "nonexistent.db")
        mock_settings_cls.return_value = mock_settings
        result = runner.invoke(app, ["db", "info"])
        assert "does not exist" in result.output or "not initialized" in result.output.lower()
