from pydantic import Field
from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Optional
import os


class PlexSettings(BaseSettings):
    url: Optional[str] = Field(default=None, description="Plex server URL")
    token: Optional[str] = Field(default=None, description="Plex authentication token")
    library_name: Optional[str] = Field(default=None, description="Music library name")

    class Config:
        env_prefix = "PLEX_"


class DatabaseSettings(BaseSettings):
    path: str = Field(
        default="~/.plexmix/plexmix.db",
        description="SQLite database path"
    )
    faiss_index_path: str = Field(
        default="~/.plexmix/embeddings.index",
        description="FAISS index file path"
    )

    class Config:
        env_prefix = "DATABASE_"

    def get_db_path(self) -> Path:
        return Path(self.path).expanduser()

    def get_index_path(self) -> Path:
        return Path(self.faiss_index_path).expanduser()


class AISettings(BaseSettings):
    default_provider: str = Field(default="gemini", description="Default AI provider")
    model: Optional[str] = Field(default=None, description="Model name")
    temperature: float = Field(default=0.7, description="LLM temperature")

    class Config:
        env_prefix = "AI_"


class EmbeddingSettings(BaseSettings):
    default_provider: str = Field(default="gemini", description="Default embedding provider")
    model: str = Field(default="gemini-embedding-001", description="Embedding model")
    dimension: int = Field(default=3072, description="Embedding dimension")

    class Config:
        env_prefix = "EMBEDDING_"

    def get_dimension_for_provider(self, provider: str) -> int:
        dimension_map = {
            "gemini": 3072,
            "openai": 1536,
            "cohere": 1024,
            "local": 768
        }
        return dimension_map.get(provider, self.dimension)


class PlaylistSettings(BaseSettings):
    default_length: int = Field(default=50, description="Default playlist length")
    candidate_pool_size: int = Field(default=100, description="Candidate pool size for AI selection")

    class Config:
        env_prefix = "PLAYLIST_"


class LoggingSettings(BaseSettings):
    level: str = Field(default="INFO", description="Logging level")
    file_path: str = Field(default="~/.plexmix/plexmix.log", description="Log file path")
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format"
    )

    class Config:
        env_prefix = "LOG_"

    def get_log_path(self) -> Path:
        return Path(self.file_path).expanduser()


class Settings(BaseSettings):
    plex: PlexSettings = Field(default_factory=PlexSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    ai: AISettings = Field(default_factory=AISettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    playlist: PlaylistSettings = Field(default_factory=PlaylistSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    model_config = {
        'env_file': '.env',
        'env_file_encoding': 'utf-8',
        'case_sensitive': False,
        'extra': 'ignore'
    }

    @classmethod
    def load_from_file(cls, config_path: Optional[str] = None) -> "Settings":
        if config_path is None:
            config_path = str(Path("~/.plexmix/config.yaml").expanduser())

        if Path(config_path).exists():
            import yaml
            with open(config_path, 'r') as f:
                config_data = yaml.safe_load(f) or {}
            return cls(**config_data)

        return cls()

    def save_to_file(self, config_path: Optional[str] = None) -> None:
        if config_path is None:
            config_path = str(Path("~/.plexmix/config.yaml").expanduser())

        Path(config_path).parent.mkdir(parents=True, exist_ok=True)

        import yaml
        with open(config_path, 'w') as f:
            yaml.dump(self.model_dump(exclude_none=True), f, default_flow_style=False)


def get_config_dir() -> Path:
    config_dir = Path("~/.plexmix").expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_path() -> Path:
    return get_config_dir() / "config.yaml"
