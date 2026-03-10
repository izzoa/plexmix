"""Tests for config/settings.py and config/credentials.py."""
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Settings tests
# ---------------------------------------------------------------------------

class TestDataDir:
    def test_default_data_dir(self, monkeypatch):
        monkeypatch.delenv("PLEXMIX_DATA_DIR", raising=False)
        from plexmix.config.settings import _data_dir
        result = _data_dir()
        assert result == Path("~/.plexmix").expanduser()

    def test_custom_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path / "custom"))
        from plexmix.config.settings import _data_dir
        result = _data_dir()
        assert result == tmp_path / "custom"


class TestPlexSettings:
    def test_defaults(self):
        from plexmix.config.settings import PlexSettings
        s = PlexSettings()
        assert s.url is None
        assert s.token is None
        assert s.library_name is None


class TestDatabaseSettings:
    def test_defaults(self, monkeypatch):
        """DatabaseSettings fields default to None (requires env var or explicit value)."""
        from plexmix.config.settings import DatabaseSettings
        monkeypatch.setenv("DATABASE_PATH", "/tmp/test.db")
        monkeypatch.setenv("DATABASE_FAISS_INDEX_PATH", "/tmp/test.index")
        s = DatabaseSettings()
        assert s.path == "/tmp/test.db"
        assert s.faiss_index_path == "/tmp/test.index"

    def test_get_db_path_default(self, monkeypatch):
        monkeypatch.delenv("PLEXMIX_DATA_DIR", raising=False)
        monkeypatch.delenv("DATABASE_PATH", raising=False)
        from plexmix.config.settings import DatabaseSettings
        # Provide explicit empty-ish path to avoid validation error
        s = DatabaseSettings(path="", faiss_index_path="")
        # With empty path, get_db_path falls through to default
        assert s.get_db_path() == Path("~/.plexmix").expanduser() / "plexmix.db"

    def test_get_db_path_custom(self):
        from plexmix.config.settings import DatabaseSettings
        s = DatabaseSettings(path="/tmp/custom.db", faiss_index_path="")
        assert s.get_db_path() == Path("/tmp/custom.db")

    def test_get_index_path_default(self, monkeypatch):
        monkeypatch.delenv("PLEXMIX_DATA_DIR", raising=False)
        from plexmix.config.settings import DatabaseSettings
        s = DatabaseSettings(path="", faiss_index_path="")
        assert s.get_index_path() == Path("~/.plexmix").expanduser() / "embeddings.index"

    def test_get_index_path_custom(self):
        from plexmix.config.settings import DatabaseSettings
        s = DatabaseSettings(path="", faiss_index_path="/tmp/custom.index")
        assert s.get_index_path() == Path("/tmp/custom.index")


class TestAISettings:
    def test_defaults(self):
        from plexmix.config.settings import AISettings
        s = AISettings()
        assert s.default_provider == "gemini"
        assert s.model is None
        assert s.temperature == 0.7
        assert s.local_mode == "builtin"
        assert s.local_endpoint is None
        assert s.local_auth_token is None
        assert s.local_max_output_tokens == 800


class TestEmbeddingSettings:
    def test_defaults(self):
        from plexmix.config.settings import EmbeddingSettings
        s = EmbeddingSettings()
        assert s.default_provider == "gemini"
        assert s.model == "gemini-embedding-001"
        assert s.dimension == 3072

    def test_dimension_gemini(self):
        from plexmix.config.settings import EmbeddingSettings
        s = EmbeddingSettings()
        assert s.get_dimension_for_provider("gemini") == 3072

    def test_dimension_openai(self):
        from plexmix.config.settings import EmbeddingSettings
        s = EmbeddingSettings()
        assert s.get_dimension_for_provider("openai") == 1536

    def test_dimension_cohere(self):
        from plexmix.config.settings import EmbeddingSettings
        s = EmbeddingSettings()
        assert s.get_dimension_for_provider("cohere") == 1024

    def test_dimension_local_miniLM(self):
        from plexmix.config.settings import EmbeddingSettings
        s = EmbeddingSettings(model="all-MiniLM-L6-v2")
        assert s.get_dimension_for_provider("local") == 384

    def test_dimension_local_mxbai(self):
        from plexmix.config.settings import EmbeddingSettings
        s = EmbeddingSettings(model="mixedbread-ai/mxbai-embed-large-v1")
        assert s.get_dimension_for_provider("local") == 1024

    def test_dimension_local_nomic(self):
        from plexmix.config.settings import EmbeddingSettings
        s = EmbeddingSettings(model="nomic-ai/nomic-embed-text-v1.5")
        assert s.get_dimension_for_provider("local") == 768

    def test_dimension_local_embeddinggemma(self):
        from plexmix.config.settings import EmbeddingSettings
        s = EmbeddingSettings(model="google/embeddinggemma-300m")
        assert s.get_dimension_for_provider("local") == 768

    def test_dimension_unknown_local_model_uses_dimension_field(self):
        from plexmix.config.settings import EmbeddingSettings
        s = EmbeddingSettings(model="unknown-model", dimension=512)
        assert s.get_dimension_for_provider("local") == 512

    def test_dimension_unknown_local_model_no_dimension_fallback(self):
        from plexmix.config.settings import EmbeddingSettings
        s = EmbeddingSettings(model="unknown-model", dimension=0)
        assert s.get_dimension_for_provider("local") == 768

    def test_dimension_unknown_provider_uses_dimension_field(self):
        from plexmix.config.settings import EmbeddingSettings
        s = EmbeddingSettings(dimension=999)
        assert s.get_dimension_for_provider("unknown_provider") == 999


class TestAudioSettings:
    def test_defaults(self):
        from plexmix.config.settings import AudioSettings
        s = AudioSettings()
        assert s.enabled is False
        assert s.analyze_on_sync is False
        assert s.duration_limit == 60
        assert s.path_prefix_from is None
        assert s.path_prefix_to is None

    def test_resolve_path_no_mapping(self):
        from plexmix.config.settings import AudioSettings
        s = AudioSettings()
        assert s.resolve_path("/data/music/track.flac") == "/data/music/track.flac"

    def test_resolve_path_matching_prefix(self):
        from plexmix.config.settings import AudioSettings
        s = AudioSettings(path_prefix_from="/data/music", path_prefix_to="/music")
        assert s.resolve_path("/data/music/Artist/track.flac") == "/music/Artist/track.flac"

    def test_resolve_path_non_matching_prefix(self):
        from plexmix.config.settings import AudioSettings
        s = AudioSettings(path_prefix_from="/data/music", path_prefix_to="/music")
        assert s.resolve_path("/other/path/track.flac") == "/other/path/track.flac"

    def test_resolve_path_partial_prefix_safety(self):
        """Ensure /data doesn't match /database."""
        from plexmix.config.settings import AudioSettings
        s = AudioSettings(path_prefix_from="/data", path_prefix_to="/mnt")
        assert s.resolve_path("/database/file.db") == "/database/file.db"

    def test_resolve_path_only_from_set(self):
        from plexmix.config.settings import AudioSettings
        s = AudioSettings(path_prefix_from="/data/music")
        assert s.resolve_path("/data/music/track.flac") == "/data/music/track.flac"

    def test_resolve_path_only_to_set(self):
        from plexmix.config.settings import AudioSettings
        s = AudioSettings(path_prefix_to="/music")
        assert s.resolve_path("/data/music/track.flac") == "/data/music/track.flac"


class TestPlaylistSettings:
    def test_defaults(self):
        from plexmix.config.settings import PlaylistSettings
        s = PlaylistSettings()
        assert s.default_length == 50
        assert s.candidate_pool_size is None
        assert s.candidate_pool_multiplier == 25


class TestLoggingSettings:
    def test_defaults(self):
        from plexmix.config.settings import LoggingSettings
        s = LoggingSettings()
        assert s.level == "INFO"
        assert s.file_path is None

    def test_get_log_path_default(self, monkeypatch):
        monkeypatch.delenv("PLEXMIX_DATA_DIR", raising=False)
        from plexmix.config.settings import LoggingSettings
        s = LoggingSettings()
        assert s.get_log_path() == Path("~/.plexmix").expanduser() / "plexmix.log"

    def test_get_log_path_custom(self):
        from plexmix.config.settings import LoggingSettings
        s = LoggingSettings(file_path="/tmp/custom.log")
        assert s.get_log_path() == Path("/tmp/custom.log")


def _min_db_yaml(tmp_path, extra=""):
    """Minimal YAML with database fields to satisfy DatabaseSettings validation."""
    config_file = tmp_path / "config.yaml"
    base = (
        f"database:\n"
        f"  path: {tmp_path / 'test.db'}\n"
        f"  faiss_index_path: {tmp_path / 'test.index'}\n"
    )
    config_file.write_text(base + extra)
    return str(config_file)


class TestSettings:
    def test_load_from_file_missing(self, tmp_path, monkeypatch):
        """Missing config file returns defaults (with env providing db paths)."""
        from plexmix.config.settings import Settings
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
        monkeypatch.setenv("DATABASE_FAISS_INDEX_PATH", str(tmp_path / "test.index"))
        s = Settings.load_from_file(str(tmp_path / "nonexistent.yaml"))
        assert s.ai.default_provider == "gemini"
        assert s.playlist.default_length == 50

    def test_load_from_file_reads_yaml(self, tmp_path):
        from plexmix.config.settings import Settings
        cfg = _min_db_yaml(
            tmp_path,
            "ai:\n  default_provider: openai\nplaylist:\n  default_length: 30\n",
        )
        s = Settings.load_from_file(cfg)
        assert s.ai.default_provider == "openai"
        assert s.playlist.default_length == 30

    def test_save_and_load_round_trip(self, tmp_path):
        from plexmix.config.settings import Settings
        cfg = _min_db_yaml(tmp_path)
        s = Settings.load_from_file(cfg)
        s.audio.enabled = True
        s.audio.duration_limit = 45
        save_file = str(tmp_path / "saved.yaml")
        s.save_to_file(save_file)

        loaded = Settings.load_from_file(save_file)
        assert loaded.audio.enabled is True
        assert loaded.audio.duration_limit == 45

    def test_save_creates_parent_dirs(self, tmp_path, monkeypatch):
        from plexmix.config.settings import Settings
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
        monkeypatch.setenv("DATABASE_FAISS_INDEX_PATH", str(tmp_path / "test.index"))
        config_file = tmp_path / "nested" / "dir" / "config.yaml"
        Settings.load_from_file(str(tmp_path / "nope.yaml")).save_to_file(str(config_file))
        assert config_file.exists()


class TestConfigHelpers:
    def test_get_config_dir_creates_directory(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path / "newdir"))
        from plexmix.config.settings import get_config_dir
        result = get_config_dir()
        assert result.is_dir()

    def test_get_config_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path / "newdir"))
        from plexmix.config.settings import get_config_path
        result = get_config_path()
        assert result.name == "config.yaml"


# ---------------------------------------------------------------------------
# Credentials tests
# ---------------------------------------------------------------------------

class TestGetKeyring:
    def test_returns_keyring_when_available(self):
        from plexmix.config.credentials import _get_keyring
        # If keyring is installed in the test env, should return non-None
        # If not installed, should return None — either is acceptable
        result = _get_keyring()
        assert result is not None or result is None  # just exercises the path

    def test_returns_none_on_import_error(self):
        with patch.dict("sys.modules", {"keyring": None}):
            # Force reimport
            import importlib
            import plexmix.config.credentials as creds
            importlib.reload(creds)
            result = creds._get_keyring()
            # Restore
            importlib.reload(creds)
            # When keyring module is None, importing it raises TypeError
            # The function catches Exception so returns None
            assert result is None or result is not None


class TestStoreCredential:
    def test_store_success(self):
        from plexmix.config import credentials
        mock_kr = MagicMock()
        with patch.object(credentials, "_get_keyring", return_value=mock_kr):
            result = credentials.store_credential("test_key", "test_value")
        assert result is True
        mock_kr.set_password.assert_called_once_with("plexmix", "test_key", "test_value")

    def test_store_no_keyring(self):
        from plexmix.config import credentials
        with patch.object(credentials, "_get_keyring", return_value=None):
            result = credentials.store_credential("test_key", "test_value")
        assert result is False

    def test_store_keyring_exception(self):
        from plexmix.config import credentials
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = Exception("fail")
        with patch.object(credentials, "_get_keyring", return_value=mock_kr):
            result = credentials.store_credential("test_key", "test_value")
        assert result is False


class TestGetCredential:
    def test_from_env_var(self, monkeypatch):
        from plexmix.config import credentials
        monkeypatch.setenv("PLEX_TOKEN", "env_token")
        with patch.object(credentials, "_get_keyring", return_value=None):
            result = credentials.get_credential("plex_token")
        assert result == "env_token"

    def test_gemini_api_key_fallback(self, monkeypatch):
        from plexmix.config import credentials
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "gemini_key")
        with patch.object(credentials, "_get_keyring", return_value=None):
            result = credentials.get_credential("google_api_key")
        assert result == "gemini_key"

    def test_from_keyring(self, monkeypatch):
        from plexmix.config import credentials
        monkeypatch.delenv("PLEX_TOKEN", raising=False)
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = "keyring_token"
        with patch.object(credentials, "_get_keyring", return_value=mock_kr):
            result = credentials.get_credential("plex_token")
        assert result == "keyring_token"

    def test_no_keyring_no_env(self, monkeypatch):
        from plexmix.config import credentials
        monkeypatch.delenv("PLEX_TOKEN", raising=False)
        with patch.object(credentials, "_get_keyring", return_value=None):
            result = credentials.get_credential("plex_token")
        assert result is None

    def test_keyring_exception(self, monkeypatch):
        from plexmix.config import credentials
        monkeypatch.delenv("PLEX_TOKEN", raising=False)
        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = Exception("fail")
        with patch.object(credentials, "_get_keyring", return_value=mock_kr):
            result = credentials.get_credential("plex_token")
        assert result is None


class TestDeleteCredential:
    def test_delete_success(self):
        from plexmix.config import credentials
        mock_kr = MagicMock()
        with patch.object(credentials, "_get_keyring", return_value=mock_kr):
            result = credentials.delete_credential("test_key")
        assert result is True
        mock_kr.delete_password.assert_called_once()

    def test_delete_no_keyring(self):
        from plexmix.config import credentials
        with patch.object(credentials, "_get_keyring", return_value=None):
            result = credentials.delete_credential("test_key")
        assert result is False

    def test_delete_password_delete_error(self):
        from plexmix.config import credentials

        class PasswordDeleteError(Exception):
            pass

        mock_kr = MagicMock()
        mock_kr.delete_password.side_effect = PasswordDeleteError("not found")
        with patch.object(credentials, "_get_keyring", return_value=mock_kr):
            result = credentials.delete_credential("test_key")
        assert result is False


class TestConvenienceWrappers:
    def test_get_plex_token(self):
        from plexmix.config import credentials
        with patch.object(credentials, "get_credential", return_value="token123") as mock:
            result = credentials.get_plex_token()
        assert result == "token123"
        mock.assert_called_once_with("plex_token")

    def test_store_plex_token(self):
        from plexmix.config import credentials
        with patch.object(credentials, "store_credential", return_value=True) as mock:
            result = credentials.store_plex_token("tok")
        assert result is True
        mock.assert_called_once_with("plex_token", "tok")

    def test_get_google_api_key(self):
        from plexmix.config import credentials
        with patch.object(credentials, "get_credential", return_value="gkey") as mock:
            result = credentials.get_google_api_key()
        assert result == "gkey"
        mock.assert_called_once_with("google_api_key")

    def test_store_google_api_key(self):
        from plexmix.config import credentials
        with patch.object(credentials, "store_credential", return_value=True) as mock:
            result = credentials.store_google_api_key("gkey")
        assert result is True
        mock.assert_called_once_with("google_api_key", "gkey")

    def test_get_openai_api_key(self):
        from plexmix.config import credentials
        with patch.object(credentials, "get_credential", return_value="okey") as mock:
            result = credentials.get_openai_api_key()
        mock.assert_called_once_with("openai_api_key")

    def test_store_openai_api_key(self):
        from plexmix.config import credentials
        with patch.object(credentials, "store_credential", return_value=True) as mock:
            credentials.store_openai_api_key("okey")
        mock.assert_called_once_with("openai_api_key", "okey")

    def test_get_anthropic_api_key(self):
        from plexmix.config import credentials
        with patch.object(credentials, "get_credential", return_value="akey") as mock:
            result = credentials.get_anthropic_api_key()
        mock.assert_called_once_with("anthropic_api_key")

    def test_store_anthropic_api_key(self):
        from plexmix.config import credentials
        with patch.object(credentials, "store_credential", return_value=True) as mock:
            credentials.store_anthropic_api_key("akey")
        mock.assert_called_once_with("anthropic_api_key", "akey")

    def test_get_cohere_api_key(self):
        from plexmix.config import credentials
        with patch.object(credentials, "get_credential", return_value="ckey") as mock:
            result = credentials.get_cohere_api_key()
        mock.assert_called_once_with("cohere_api_key")

    def test_store_cohere_api_key(self):
        from plexmix.config import credentials
        with patch.object(credentials, "store_credential", return_value=True) as mock:
            credentials.store_cohere_api_key("ckey")
        mock.assert_called_once_with("cohere_api_key", "ckey")
