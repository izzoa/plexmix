from pydantic import Field
from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Any, Callable, Optional
import os


def _data_dir() -> Path:
    """Return the PlexMix data directory, respecting PLEXMIX_DATA_DIR env var."""
    env_dir = os.getenv("PLEXMIX_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    return Path("~/.plexmix").expanduser()


class PlexSettings(BaseSettings):
    url: Optional[str] = Field(default=None, description="Plex server URL")
    token: Optional[str] = Field(default=None, description="Plex authentication token")
    library_name: Optional[str] = Field(default=None, description="Music library name")

    class Config:
        env_prefix = "PLEX_"


class DatabaseSettings(BaseSettings):
    path: Optional[str] = Field(default=None, description="SQLite database path")
    faiss_index_path: Optional[str] = Field(default=None, description="FAISS index file path")

    class Config:
        env_prefix = "DATABASE_"

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

    def resolve_path(self, file_path: str) -> str:
        """Remap a Plex file path to a local path using configured prefix swap."""
        if self.path_prefix_from and self.path_prefix_to:
            prefix = self.path_prefix_from
            if file_path.startswith(prefix) and (
                len(file_path) == len(prefix)
                or file_path[len(prefix)] == "/"
                or prefix.endswith("/")
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


class UISettings(BaseSettings):
    password: Optional[str] = Field(default=None, description="Password to protect the web UI")

    class Config:
        env_prefix = "PLEXMIX_UI_"


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


class LoggingSettings(BaseSettings):
    level: str = Field(default="INFO", description="Logging level")
    file_path: Optional[str] = Field(default=None, description="Log file path")
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s", description="Log format"
    )

    class Config:
        env_prefix = "LOG_"

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
        ]
        for env_key, yaml_path, cast in _env_maps:
            val = os.getenv(env_key)
            if val is not None:
                parts = yaml_path.split(".")
                d = config_data
                for part in parts[:-1]:
                    d = d.setdefault(part, {})
                d[parts[-1]] = cast(val)
        return config_data

    @classmethod
    def load_from_file(cls, config_path: Optional[str] = None) -> "Settings":
        using_default = config_path is None
        if config_path is None:
            config_path = str(_data_dir() / "config.yaml")

        config = Path(config_path)
        if config.exists():
            import yaml  # type: ignore[import-untyped]

            with open(config, "r") as f:
                config_data = yaml.safe_load(f) or {}
            config_data = cls._apply_env_overrides(config_data)
            return cls(**config_data)

        # Backwards-compat: check legacy ~/.plexmix/ path when using default
        # config path with a non-default data dir (e.g. Docker PLEXMIX_DATA_DIR)
        if using_default:
            legacy = Path("~/.plexmix/config.yaml").expanduser()
            if legacy.exists() and str(legacy) != str(config):
                import yaml  # type: ignore[import-untyped]

                with open(legacy, "r") as f:
                    config_data = yaml.safe_load(f) or {}
                config_data = cls._apply_env_overrides(config_data)
                return cls(**config_data)

        return cls()

    def save_to_file(self, config_path: Optional[str] = None) -> None:
        if config_path is None:
            config_path = str(_data_dir() / "config.yaml")

        Path(config_path).parent.mkdir(parents=True, exist_ok=True)

        import yaml

        with open(config_path, "w") as f:
            yaml.dump(self.model_dump(exclude_none=True), f, default_flow_style=False)


def get_config_dir() -> Path:
    config_dir = _data_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_path() -> Path:
    return get_config_dir() / "config.yaml"
