from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator
import numpy as np


class Artist(BaseModel):
    id: Optional[int] = None
    plex_key: str
    name: str
    genre: Optional[str] = None
    bio: Optional[str] = None

    @field_validator('name')
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('Artist name cannot be empty')
        return v.strip()


class Album(BaseModel):
    id: Optional[int] = None
    plex_key: str
    title: str
    artist_id: int
    year: Optional[int] = None
    genre: Optional[str] = None
    cover_art_url: Optional[str] = None

    @field_validator('title')
    @classmethod
    def title_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('Album title cannot be empty')
        return v.strip()

    @field_validator('year')
    @classmethod
    def year_must_be_valid(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 1900 or v > 2100):
            raise ValueError('Year must be between 1900 and 2100')
        return v


class Track(BaseModel):
    id: Optional[int] = None
    plex_key: str
    title: str
    artist_id: int
    album_id: int
    duration_ms: Optional[int] = None
    genre: Optional[str] = None
    year: Optional[int] = None
    rating: Optional[float] = None
    play_count: Optional[int] = None
    last_played: Optional[datetime] = None
    file_path: Optional[str] = None
    tags: Optional[str] = None
    environments: Optional[str] = None
    instruments: Optional[str] = None

    @field_validator('title')
    @classmethod
    def title_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('Track title cannot be empty')
        return v.strip()

    @field_validator('rating')
    @classmethod
    def rating_must_be_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and (v < 0.0 or v > 5.0):
            raise ValueError('Rating must be between 0.0 and 5.0')
        return v

    @field_validator('duration_ms')
    @classmethod
    def duration_must_be_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError('Duration cannot be negative')
        return v

    def get_tags_list(self) -> List[str]:
        if not self.tags:
            return []
        return [tag.strip() for tag in self.tags.split(',') if tag.strip()]

    def set_tags_list(self, tags: List[str]) -> None:
        if len(tags) > 5:
            tags = tags[:5]
        self.tags = ', '.join(tags) if tags else None


class Genre(BaseModel):
    id: Optional[int] = None
    name: str

    @field_validator('name')
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('Genre name cannot be empty')
        return v.strip().lower()


class Embedding(BaseModel):
    id: Optional[int] = None
    track_id: int
    embedding_model: str
    embedding_dim: int
    vector: List[float]
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator('embedding_dim')
    @classmethod
    def embedding_dim_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError('Embedding dimension must be positive')
        return v

    @field_validator('vector')
    @classmethod
    def vector_must_match_dim(cls, v: List[float]) -> List[float]:
        if not v:
            raise ValueError('Vector cannot be empty')
        return v

    def to_numpy(self) -> np.ndarray:
        return np.array(self.vector, dtype=np.float32)


class SyncHistory(BaseModel):
    id: Optional[int] = None
    sync_date: datetime = Field(default_factory=datetime.utcnow)
    tracks_added: int = 0
    tracks_updated: int = 0
    tracks_removed: int = 0
    status: str = "success"
    error_message: Optional[str] = None

    @field_validator('status')
    @classmethod
    def status_must_be_valid(cls, v: str) -> str:
        valid_statuses = ['success', 'failed', 'partial']
        if v not in valid_statuses:
            raise ValueError(f'Status must be one of {valid_statuses}')
        return v


class Playlist(BaseModel):
    id: Optional[int] = None
    plex_key: Optional[str] = None
    name: str
    description: Optional[str] = None
    created_by_ai: bool = False
    mood_query: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator('name')
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('Playlist name cannot be empty')
        return v.strip()
