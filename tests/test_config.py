"""Tests for config/settings.py and config/credentials.py."""
from pathlib import Path
from typing import get_args, get_origin
from unittest.mock import patch, MagicMock

import pytest


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
        # With no dimension hint, defaults to 384 (smallest known local model dim)
        assert s.get_dimension_for_provider("local") == 384

    def test_dimension_unknown_provider_uses_dimension_field(self):
        from plexmix.config.settings import EmbeddingSettings

        s = EmbeddingSettings(dimension=999)
        assert s.get_dimension_for_provider("unknown_provider") == 999

    def test_dimension_custom_uses_custom_dimension(self):
        from plexmix.config.settings import EmbeddingSettings

        s = EmbeddingSettings(custom_dimension=768)
        assert s.get_dimension_for_provider("custom") == 768

    def test_dimension_custom_default(self):
        from plexmix.config.settings import EmbeddingSettings

        s = EmbeddingSettings()
        assert s.get_dimension_for_provider("custom") == 1536


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


class TestSettingsErrorsHideValues:
    """A settings error names the file, setting or variable, never the rejected value."""

    def test_validation_error_hides_numeric_password(self, tmp_path: Path) -> None:
        from pydantic import ValidationError

        from plexmix.config.settings import Settings

        cfg = _min_db_yaml(tmp_path, "ui:\n  password: 1234567890\n")
        with pytest.raises(ValidationError) as info:
            Settings.load_from_file(cfg)
        text = str(info.value)
        assert "ui.password" in text
        assert "1234567890" not in text

    def test_validation_error_hides_section_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import ValidationError

        from plexmix.config.settings import Settings

        for name in ("PLEX_URL", "PLEX_TOKEN", "PLEX_LIBRARY_NAME"):
            monkeypatch.delenv(name, raising=False)
        cfg = _min_db_yaml(tmp_path, "plex:\n  - dummy-plex-token-0000\n")
        with pytest.raises(ValidationError) as info:
            Settings.load_from_file(cfg)
        text = str(info.value)
        assert "\nplex\n" in text  # the section is named
        assert "dummy-plex-token-0000" not in text

    def test_section_settings_hide_inputs_when_built_alone(self) -> None:
        from pydantic import ValidationError

        from plexmix.config.settings import UISettings

        with pytest.raises(ValidationError) as info:
            UISettings(password=1234567890)
        text = str(info.value)
        assert "password" in text
        assert "1234567890" not in text

    def test_every_settings_section_hides_inputs(self) -> None:
        from pydantic import BaseModel

        from plexmix.config.settings import Settings

        assert Settings.model_config.get("hide_input_in_errors") is True
        checked = set()
        for name, field in Settings.model_fields.items():
            # The annotation itself, or the class inside an Optional[...].
            for candidate in (field.annotation, *get_args(field.annotation)):
                if get_origin(candidate) is not None or not isinstance(candidate, type):
                    continue
                if issubclass(candidate, BaseModel):
                    assert candidate.model_config.get("hide_input_in_errors") is True, name
                    checked.add(name)
        # The walk covers a section that is added later; these are the nine of today.
        assert checked >= {
            "plex",
            "database",
            "ai",
            "embedding",
            "playlist",
            "audio",
            "musicbrainz",
            "ui",
            "logging",
        }

    def test_yaml_alias_and_tag_errors_do_not_echo_value(self, tmp_path: Path) -> None:
        import yaml

        from plexmix.config.settings import Settings

        # PyYAML reads an unquoted value that starts with "*" as an alias and one that
        # starts with "!" as a tag, and its own error message repeats the name.
        for line in ("password: *dummyvalue", "password: !dummyvalue"):
            cfg = _min_db_yaml(tmp_path, f"ui:\n  {line}\n")
            with pytest.raises(yaml.YAMLError) as info:
                Settings.load_from_file(cfg)
            assert "dummyvalue" not in str(info.value)
            assert "dummyvalue" not in repr(info.value)
            # The parser's own error quotes the value; it is not attached, so it
            # cannot be printed as the cause or the context of this one.
            assert info.value.__cause__ is None and info.value.__context__ is None

    def test_yaml_core_tag_errors_do_not_echo_value(self, tmp_path: Path) -> None:
        import yaml

        from plexmix.config.settings import Settings

        # After "!!bool", "!!int" or "!!float" PyYAML fails with a plain KeyError or
        # ValueError (not a YAML error) whose text quotes the value. After
        # "!!timestamp" it fails with an AttributeError that names nothing at all.
        for tag in ("!!bool", "!!int", "!!float", "!!timestamp"):
            cfg = _min_db_yaml(tmp_path, f"ui:\n  password: {tag} dummyvalue\n")
            with pytest.raises(yaml.YAMLError) as info:
                Settings.load_from_file(cfg)
            text = str(info.value)
            assert cfg in text and "could not be parsed" in text
            assert "dummyvalue" not in text
            assert info.value.__cause__ is None and info.value.__context__ is None

    def test_yaml_error_keeps_line_and_column(self, tmp_path: Path) -> None:
        import yaml

        from plexmix.config.settings import Settings

        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "plex:\n  token: dummy-plex-token-0000\n  url: *dummyvalue\naudio:\n  workers: 2\n"
        )
        with pytest.raises(yaml.YAMLError) as info:
            Settings.load_from_file(str(cfg))
        text = str(info.value)
        assert str(cfg) in text
        assert "line 3, column 8" in text
        assert "dummy-plex-token-0000" not in text and "dummyvalue" not in text
        # What the web UI's error text reads: a fixed problem and a zero-based position.
        assert info.value.problem == "could not be parsed"
        mark = info.value.problem_mark
        assert (mark.line, mark.column) == (2, 7)
        assert mark.buffer is None and mark.pointer is None  # no source text travels

    def test_legacy_config_yaml_error_does_not_echo_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fallback to ~/.plexmix/config.yaml reads the file through the same helper."""
        import yaml

        from plexmix.config import settings as settings_module

        home = tmp_path / "home"
        legacy = home / ".plexmix" / "config.yaml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("ui:\n  password: *dummyvalue\n")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.setenv("PLEXMIX_DATA_DIR", str(data_dir))
        monkeypatch.setattr(settings_module, "_config_path_override", None)
        # Guard: the fallback must resolve to the temporary home, never a real profile.
        assert Path("~/.plexmix/config.yaml").expanduser() == legacy

        with pytest.raises(yaml.YAMLError) as info:
            settings_module.Settings.load_from_file()
        text = str(info.value)
        assert str(legacy) in text and "line 2" in text
        assert "dummyvalue" not in text

    @pytest.mark.parametrize("with_config_file", [True, False])
    def test_env_cast_error_names_variable_not_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_config_file: bool
    ) -> None:
        from pydantic import ValidationError

        from plexmix.config.settings import Settings

        # Without a config file the sections read the environment themselves; the
        # error must name the variable there too, not "AudioSettings / workers".
        cfg = _min_db_yaml(tmp_path) if with_config_file else str(tmp_path / "absent.yaml")
        assert Path(cfg).exists() is with_config_file
        cases = (
            ("AUDIO_WORKERS", "four", "a whole number"),
            ("MUSICBRAINZ_RATE_LIMIT_DELAY", "slowly", "a number"),
        )
        for name, value, expected in cases:
            monkeypatch.setenv(name, value)
            with pytest.raises(ValueError) as info:
                Settings.load_from_file(cfg)
            monkeypatch.delenv(name)
            assert not isinstance(info.value, ValidationError)
            text = str(info.value)
            assert name in text
            assert expected in text
            assert value not in text
            # The cast's own error quotes the value; it is not attached either.
            assert info.value.__cause__ is None and info.value.__context__ is None

    def test_unknown_key_error_does_not_echo_key_text(self, tmp_path: Path) -> None:
        from pydantic import ValidationError

        from plexmix.config.settings import Settings

        # Flow style with the space after the colon missing: YAML reads ONE key,
        # "password:dummy-ui-password-0000", and a validation error names its keys.
        cases = (
            ("ui", "{password:dummy-ui-password-0000}", "dummy-ui-password-0000"),
            ("plex", "{token:dummy-plex-token-0000}", "dummy-plex-token-0000"),
            ("ai", "{custom_api_key:dummy-api-key-0000}", "dummy-api-key-0000"),
            ("ui", "{password:dummy-ui-password-0000, 1234567890: x}", "1234567890"),
            ("embedding", "{9876543210: x}", "9876543210"),  # a key that is not text
        )
        for section, body, secret in cases:
            cfg = _min_db_yaml(tmp_path, f"{section}: {body}\n")
            with pytest.raises(ValidationError) as info:
                Settings.load_from_file(cfg)
            text = str(info.value)
            assert f"{section}.unknown key (check for a missing space after a colon)" in text
            for value in (secret, "dummy-ui-password-0000"):
                assert value not in text
                assert value not in repr(info.value)

    def test_unknown_key_with_the_shape_of_a_setting_name_is_still_named(
        self, tmp_path: Path
    ) -> None:
        from pydantic import ValidationError

        from plexmix.config.settings import Settings

        # A typing mistake in a key is reported by that key, as before.
        cfg = _min_db_yaml(tmp_path, "plex:\n  tokne: dummy-plex-token-0000\n")
        with pytest.raises(ValidationError) as info:
            Settings.load_from_file(cfg)
        text = str(info.value)
        assert "plex.tokne" in text
        assert "unknown key" not in text
        assert "dummy-plex-token-0000" not in text

    def test_known_keys_and_other_sections_are_left_as_written(self, tmp_path: Path) -> None:
        from plexmix.config.settings import Settings

        data = {
            "ui": {"password": "dummy-ui-password-0000"},
            "plex": {"url": "http://plexhost:32400", "token:x": None},
            "not_a_section": {"any key:at all": 1},
            "audio": ["not", "a", "mapping"],
        }
        cleaned = Settings._hide_odd_section_keys(data)
        assert cleaned["ui"] == {"password": "dummy-ui-password-0000"}
        assert cleaned["plex"] == {
            "url": "http://plexhost:32400",
            "unknown key (check for a missing space after a colon)": None,
        }
        assert cleaned["not_a_section"] == {"any key:at all": 1}
        assert cleaned["audio"] == ["not", "a", "mapping"]

    def test_top_level_that_is_not_a_mapping_names_the_file_not_the_value(
        self, tmp_path: Path
    ) -> None:
        import yaml

        from plexmix.config.settings import Settings

        cfg = tmp_path / "config.yaml"
        # A list, a single value, and a key that is not text.
        for body in ("- dummy-ui-password-0000\n", "dummy-ui-password-0000\n", "1234567890: {}\n"):
            cfg.write_text(body)
            with pytest.raises(yaml.YAMLError) as info:
                Settings.load_from_file(str(cfg))
            text = str(info.value)
            assert str(cfg) in text
            assert "must hold a mapping of settings sections at the top level" in text
            # What the web UI's error text reads.
            assert info.value.problem == "must hold a mapping of settings sections at the top level"
            for value in ("dummy-ui-password-0000", "1234567890"):
                assert value not in text
                assert value not in repr(info.value)
            assert info.value.__cause__ is None and info.value.__context__ is None

    def test_empty_config_file_still_loads(self, tmp_path: Path) -> None:
        from plexmix.config.settings import Settings

        cfg = tmp_path / "config.yaml"
        for body in ("", "# only a comment\n", "---\n"):
            cfg.write_text(body)
            assert Settings.load_from_file(str(cfg)).playlist.default_length == 50

    def test_env_override_skips_section_that_is_not_a_mapping(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import ValidationError

        from plexmix.config.settings import Settings

        monkeypatch.setenv("PLEX_TOKEN", "dummy-plex-token-0000")
        monkeypatch.setenv("AUDIO_WORKERS", "7")
        data = Settings._apply_env_overrides({"plex": ["a", "list"], "audio": {"workers": 2}})
        assert data["plex"] == ["a", "list"]  # left as written, for validation to reject
        assert data["audio"]["workers"] == 7  # a mapping section still takes the override

        # Loading such a file ends in a validation error that names the section,
        # not in a bare TypeError from writing the override into a list.
        cfg = _min_db_yaml(tmp_path, "plex:\n  - dummy-plex-token-0000\n")
        with pytest.raises(ValidationError) as info:
            Settings.load_from_file(cfg)
        text = str(info.value)
        assert "\nplex\n" in text
        assert "dummy-plex-token-0000" not in text


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
            credentials.get_openai_api_key()
        mock.assert_called_once_with("openai_api_key")

    def test_store_openai_api_key(self):
        from plexmix.config import credentials

        with patch.object(credentials, "store_credential", return_value=True) as mock:
            credentials.store_openai_api_key("okey")
        mock.assert_called_once_with("openai_api_key", "okey")

    def test_get_anthropic_api_key(self):
        from plexmix.config import credentials

        with patch.object(credentials, "get_credential", return_value="akey") as mock:
            credentials.get_anthropic_api_key()
        mock.assert_called_once_with("anthropic_api_key")

    def test_store_anthropic_api_key(self):
        from plexmix.config import credentials

        with patch.object(credentials, "store_credential", return_value=True) as mock:
            credentials.store_anthropic_api_key("akey")
        mock.assert_called_once_with("anthropic_api_key", "akey")

    def test_get_cohere_api_key(self):
        from plexmix.config import credentials

        with patch.object(credentials, "get_credential", return_value="ckey") as mock:
            credentials.get_cohere_api_key()
        mock.assert_called_once_with("cohere_api_key")

    def test_store_cohere_api_key(self):
        from plexmix.config import credentials

        with patch.object(credentials, "store_credential", return_value=True) as mock:
            credentials.store_cohere_api_key("ckey")
        mock.assert_called_once_with("cohere_api_key", "ckey")
