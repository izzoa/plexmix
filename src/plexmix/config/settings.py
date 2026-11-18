from pydantic import Field
from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Any, Callable, Optional, get_args
import os
import re


def _data_dir() -> Path:
    """Return the PlexMix data directory, respecting PLEXMIX_DATA_DIR env var."""
    env_dir = os.getenv("PLEXMIX_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    return Path("~/.plexmix").expanduser()


# Process-wide config path override, set by the CLI's global --config option so
# every subsequent Settings.load_from_file()/save_to_file() uses that file.
_config_path_override: Optional[str] = None


def set_config_path_override(path: Optional[str]) -> None:
    """Set (or clear) the global config-file path used when none is passed."""
    global _config_path_override
    _config_path_override = path


def _read_yaml_config(path: Path) -> Any:
    """Parse a config file. A syntax error keeps the file, line and column only.

    PyYAML's own message can repeat text from the file: an unquoted value that
    starts with ``*`` or ``!`` is read as an alias or a tag and quoted in the
    problem text. The file can hold a token or a password, so that message is
    dropped and only the position is kept. A file that parses but is not a mapping
    of sections is reported the same way, by file and problem.
    """
    import yaml  # type: ignore[import-untyped]

    problem = "could not be parsed"
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, ValueError, KeyError, AttributeError, TypeError) as exc:
        # The last four are not YAML errors, but PyYAML lets them through for a value
        # written after ``!!int``, ``!!float``, ``!!bool`` or ``!!timestamp``, and
        # their text can quote that value as well. They carry no position.
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    else:
        if isinstance(data, dict) and all(isinstance(key, str) for key in data):
            return data
        # A list, a single value, or a key that is not text. ``Settings(**data)``
        # would fail on it with an error that names neither the file nor the problem.
        problem = "must hold a mapping of settings sections at the top level"
        mark = yaml.Mark(str(path), 0, 0, 0, None, None)
    # The errors below are raised after the handler has ended, so the parser's
    # error is neither their cause nor their context and cannot be printed with them.
    line, column = getattr(mark, "line", None), getattr(mark, "column", None)
    if not isinstance(line, int) or not isinstance(column, int):
        raise yaml.YAMLError(f"{path} could not be parsed") from None
    # A new mark without a buffer: the position travels, the source text cannot.
    position = yaml.Mark(str(path), 0, line, column, None, None)
    raise yaml.MarkedYAMLError(problem=problem, problem_mark=position) from None


# The shape of a setting name. An unknown key of another shape is not named in an
# error: it can hold a value (see ``Settings._hide_odd_section_keys``).
_SETTING_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,40}")
_ODD_KEY_TEXT = "unknown key (check for a missing space after a colon)"


class SettingsEnvError(ValueError):
    """An environment variable holds a value of the wrong type.

    The text names the variable and what it must be, never the value, so it is
    safe to print and to log as it is.
    """


def _cast_env_value(env_key: str, raw: str, cast: Callable[[str], Any]) -> Any:
    """Convert an environment override. A failure names the variable, never its value."""
    try:
        return cast(raw)
    except (TypeError, ValueError):
        # The cast's own message quotes the value, so it is dropped: the error
        # below is raised after this handler has ended.
        pass
    expected = "a whole number" if cast is int else "a number" if cast is float else "a valid value"
    raise SettingsEnvError(f"Environment variable {env_key} must be {expected}.") from None


class PlexSettings(BaseSettings):
    url: Optional[str] = Field(default=None, description="Plex server URL")
    token: Optional[str] = Field(default=None, description="Plex authentication token")
    library_name: Optional[str] = Field(default=None, description="Music library name")

    class Config:
        env_prefix = "PLEX_"
        hide_input_in_errors = True


class DatabaseSettings(BaseSettings):
    path: Optional[str] = Field(default=None, description="SQLite database path")
    faiss_index_path: Optional[str] = Field(default=None, description="FAISS index file path")

    class Config:
        env_prefix = "DATABASE_"
        hide_input_in_errors = True

    def get_db_path(self) -> Path:
        if self.path:
            return Path(self.path).expanduser()
        return _data_dir() / "plexmix.db"

    def get_index_path(self) -> Path:
        if self.faiss_index_path:
            return Path(self.faiss_index_path).expanduser()
        return _data_dir() / "embeddings.index"


class AISettings(BaseSettings):
    default_provider: str = Field(default="gemini", description="Default AI provider")
    model: Optional[str] = Field(default=None, description="Model name")
    temperature: float = Field(default=0.7, description="LLM temperature")
    local_mode: str = Field(
        default="builtin",
        description="Local LLM mode: builtin (managed) or endpoint",
    )
    local_endpoint: Optional[str] = Field(
        default=None,
        description="Custom URL for a self-hosted local LLM server",
    )
    local_auth_token: Optional[str] = Field(
        default=None,
        description="Optional auth token for the custom local endpoint",
    )
    local_max_output_tokens: int = Field(
        default=800,
        description="Max new tokens to request from local LLM responses",
    )
    custom_endpoint: Optional[str] = Field(
        default=None,
        description="OpenAI-compatible endpoint URL for custom AI provider",
    )
    custom_model: Optional[str] = Field(
        default=None,
        description="Model name for custom AI endpoint",
    )
    custom_api_key: Optional[str] = Field(
        default=None,
        description="Optional API key for custom AI endpoint",
    )

    class Config:
        env_prefix = "AI_"
        hide_input_in_errors = True


class EmbeddingSettings(BaseSettings):
    default_provider: str = Field(default="gemini", description="Default embedding provider")
    model: str = Field(default="gemini-embedding-001", description="Embedding model")
    dimension: int = Field(default=3072, description="Embedding dimension")
    custom_endpoint: Optional[str] = Field(
        default=None,
        description="OpenAI-compatible endpoint URL for custom embedding provider",
    )
    custom_model: Optional[str] = Field(
        default=None,
        description="Model name for custom embedding endpoint",
    )
    custom_api_key: Optional[str] = Field(
        default=None,
        description="Optional API key for custom embedding endpoint",
    )
    custom_dimension: int = Field(
        default=1536,
        description="Embedding dimension for custom endpoint",
    )

    class Config:
        env_prefix = "EMBEDDING_"
        hide_input_in_errors = True

    def get_dimension_for_provider(self, provider: str) -> int:
        from plexmix.services.registry import get_embedding_dimension

        return get_embedding_dimension(
            provider,
            model=self.model,
            custom_dimension=self.custom_dimension,
            fallback_dimension=self.dimension if self.dimension else None,
        )


class AudioSettings(BaseSettings):
    enabled: bool = Field(default=False, description="Enable audio feature analysis")
    analyze_on_sync: bool = Field(default=False, description="Run analysis during sync")
    duration_limit: int = Field(
        default=60, description="Seconds of audio to analyze (0 = full track)"
    )
    workers: int = Field(default=4, description="Number of parallel audio analysis workers")
    path_prefix_from: Optional[str] = Field(
        default=None, description="Plex file path prefix to replace"
    )
    path_prefix_to: Optional[str] = Field(
        default=None, description="Local file path prefix replacement"
    )

    class Config:
        env_prefix = "AUDIO_"
        hide_input_in_errors = True

    def resolve_path(self, file_path: str) -> str:
        """Remap a Plex file path to a local path using configured prefix swap."""
        if self.path_prefix_from and self.path_prefix_to:
            prefix = self.path_prefix_from
            if file_path.startswith(prefix) and (
                len(file_path) == len(prefix)
                # Accept both POSIX and Windows separators at the boundary so
                # Windows-style Plex paths (C:\Music\...) are remapped too.
                or file_path[len(prefix)] in ("/", "\\")
                or prefix.endswith(("/", "\\"))
            ):
                return self.path_prefix_to + file_path[len(prefix) :]
        return file_path


class MusicBrainzSettings(BaseSettings):
    enabled: bool = Field(default=False, description="Enable MusicBrainz metadata enrichment")
    enrich_on_sync: bool = Field(
        default=False, description="Run MusicBrainz enrichment during sync"
    )
    confidence_threshold: float = Field(
        default=80.0, description="Minimum match confidence score (0-100)"
    )
    rate_limit_delay: float = Field(
        default=1.0, description="Seconds between MusicBrainz API calls"
    )
    contact_email: str = Field(
        default="", description="Contact email (required by MusicBrainz TOS)"
    )

    class Config:
        env_prefix = "MUSICBRAINZ_"
        hide_input_in_errors = True


class UISettings(BaseSettings):
    password: Optional[str] = Field(default=None, description="Password to protect the web UI")

    class Config:
        env_prefix = "PLEXMIX_UI_"
        hide_input_in_errors = True


class PlaylistSettings(BaseSettings):
    default_length: int = Field(default=50, description="Default playlist length")
    candidate_pool_size: Optional[int] = Field(
        default=None, description="Explicit candidate pool size (overrides multiplier)"
    )
    candidate_pool_multiplier: int = Field(
        default=25, description="Multiplier for candidate pool size relative to playlist length"
    )

    class Config:
        env_prefix = "PLAYLIST_"
        hide_input_in_errors = True


class LoggingSettings(BaseSettings):
    level: str = Field(default="INFO", description="Logging level")
    file_path: Optional[str] = Field(default=None, description="Log file path")
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s", description="Log format"
    )

    class Config:
        env_prefix = "LOG_"
        hide_input_in_errors = True

    def get_log_path(self) -> Path:
        if self.file_path:
            return Path(self.file_path).expanduser()
        return _data_dir() / "plexmix.log"


class Settings(BaseSettings):
    plex: PlexSettings = Field(default_factory=PlexSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    ai: AISettings = Field(default_factory=AISettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    playlist: PlaylistSettings = Field(default_factory=PlaylistSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    musicbrainz: MusicBrainzSettings = Field(default_factory=MusicBrainzSettings)
    ui: UISettings = Field(default_factory=UISettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
        # A validation error names the setting and the problem, never the rejected
        # value (it can be a token, a key or a password). This flag is the one that
        # counts when a file is loaded; the same flag on each section class covers
        # a section that is built on its own.
        "hide_input_in_errors": True,
    }

    @classmethod
    def _apply_env_overrides(cls, config_data: dict) -> dict:
        """Overlay environment variables on top of YAML config data.

        pydantic-settings only reads env vars for fields not passed as
        explicit kwargs.  Since load_from_file passes the full YAML dict,
        env vars would be silently ignored.  This method merges env vars
        into the dict so they always win.
        """
        _env_maps: list[tuple[str, str, Callable[[str], Any]]] = [
            # (env_var, dotted_yaml_key, cast)
            ("PLEX_URL", "plex.url", str),
            ("PLEX_TOKEN", "plex.token", str),
            ("PLEX_LIBRARY_NAME", "plex.library_name", str),
            ("AUDIO_ENABLED", "audio.enabled", lambda v: v.lower() in ("1", "true", "yes")),
            (
                "AUDIO_ANALYZE_ON_SYNC",
                "audio.analyze_on_sync",
                lambda v: v.lower() in ("1", "true", "yes"),
            ),
            ("AUDIO_DURATION_LIMIT", "audio.duration_limit", int),
            ("AUDIO_WORKERS", "audio.workers", int),
            ("AUDIO_PATH_PREFIX_FROM", "audio.path_prefix_from", str),
            ("AUDIO_PATH_PREFIX_TO", "audio.path_prefix_to", str),
            (
                "MUSICBRAINZ_ENABLED",
                "musicbrainz.enabled",
                lambda v: v.lower() in ("1", "true", "yes"),
            ),
            (
                "MUSICBRAINZ_ENRICH_ON_SYNC",
                "musicbrainz.enrich_on_sync",
                lambda v: v.lower() in ("1", "true", "yes"),
            ),
            ("MUSICBRAINZ_CONFIDENCE_THRESHOLD", "musicbrainz.confidence_threshold", float),
            ("MUSICBRAINZ_RATE_LIMIT_DELAY", "musicbrainz.rate_limit_delay", float),
            ("MUSICBRAINZ_CONTACT_EMAIL", "musicbrainz.contact_email", str),
            # AI / embedding / database / playlist / logging groups were previously
            # unmapped, so their env vars were silently ignored once config.yaml existed.
            ("AI_DEFAULT_PROVIDER", "ai.default_provider", str),
            ("AI_MODEL", "ai.model", str),
            ("AI_TEMPERATURE", "ai.temperature", float),
            ("AI_LOCAL_MODE", "ai.local_mode", str),
            ("AI_LOCAL_ENDPOINT", "ai.local_endpoint", str),
            ("EMBEDDING_DEFAULT_PROVIDER", "embedding.default_provider", str),
            ("EMBEDDING_MODEL", "embedding.model", str),
            ("EMBEDDING_DIMENSION", "embedding.dimension", int),
            ("DATABASE_PATH", "database.path", str),
            ("PLAYLIST_DEFAULT_LENGTH", "playlist.default_length", int),
            ("LOG_LEVEL", "logging.level", str),
            ("LOG_FILE_PATH", "logging.file_path", str),
        ]
        for env_key, yaml_path, cast in _env_maps:
            val = os.getenv(env_key)
            if val is not None:
                parts = yaml_path.split(".")
                d = config_data
                for part in parts[:-1]:
                    if isinstance(d, dict):
                        d = d.setdefault(part, {})
                if not isinstance(d, dict):
                    # The file wrote this section as something other than a mapping.
                    # Skip the overlay; validation then rejects the section by name.
                    continue
                d[parts[-1]] = _cast_env_value(env_key, val, cast)
        return config_data

    @classmethod
    def _hide_odd_section_keys(cls, config_data: dict) -> dict:
        """Replace section keys that can hold a value, before validation names them.

        A validation error names the key it rejects. In ``ui: {password:secret}`` the
        space after the colon is missing, so YAML reads one key, ``password:secret``,
        and the error would print the password. An unknown key is therefore named
        only when it has the shape of a setting name (a typing mistake such as
        ``tokne``). A key of any other shape is replaced by a fixed text.
        """
        for section, field in cls.model_fields.items():
            values = config_data.get(section)
            # The section class itself, or the class inside an Optional[...].
            candidates = (field.annotation, *get_args(field.annotation))
            known = next((c.model_fields for c in candidates if hasattr(c, "model_fields")), None)
            if not isinstance(values, dict) or known is None:
                continue
            odd_keys = [
                key
                for key in values
                if key not in known
                and not (isinstance(key, str) and _SETTING_NAME_RE.fullmatch(key))
            ]
            for key in odd_keys:
                del values[key]
            if odd_keys:
                values[_ODD_KEY_TEXT] = None
        return config_data

    @classmethod
    def load_from_file(cls, config_path: Optional[str] = None) -> "Settings":
        using_default = config_path is None and _config_path_override is None
        if config_path is None:
            config_path = _config_path_override or str(_data_dir() / "config.yaml")

        config = Path(config_path)
        if config.exists():
            config_data = cls._apply_env_overrides(_read_yaml_config(config))
            return cls(**cls._hide_odd_section_keys(config_data))

        # Backwards-compat: check legacy ~/.plexmix/ path when using default
        # config path with a non-default data dir (e.g. Docker PLEXMIX_DATA_DIR)
        if using_default:
            legacy = Path("~/.plexmix/config.yaml").expanduser()
            if legacy.exists() and str(legacy) != str(config):
                config_data = cls._apply_env_overrides(_read_yaml_config(legacy))
                return cls(**cls._hide_odd_section_keys(config_data))

        # No file: each section reads its own environment variables. Run the cast
        # check first, so a bad value is reported by the name of its variable here
        # as well. The result is not used.
        cls._apply_env_overrides({})
        return cls()

    def save_to_file(self, config_path: Optional[str] = None) -> None:
        if config_path is None:
            config_path = _config_path_override or str(_data_dir() / "config.yaml")

        target = Path(config_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        import yaml

        # Write to a temp file in the same directory and atomically replace, so an
        # interrupted save can't leave a truncated/corrupt config that bricks every
        # subsequent command. Create it 0600 — the file may hold fallback secrets.
        tmp = target.with_name(target.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                yaml.dump(self.model_dump(exclude_none=True), f, default_flow_style=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise


def get_config_dir() -> Path:
    config_dir = _data_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_path() -> Path:
    return get_config_dir() / "config.yaml"
