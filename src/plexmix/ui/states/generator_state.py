import reflex as rx
import asyncio
from typing import List, Dict, Any, Optional
from plexmix.ui.states.app_state import AppState


class GeneratorState(AppState):
    mood_query: str = ""
    max_tracks: int = 50
    genre_filter: str = ""
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    include_artists: str = ""
    exclude_artists: str = ""

    is_generating: bool = False
    generation_progress: int = 0
    generation_message: str = ""

    generated_playlist: List[Dict[str, Any]] = []
    playlist_name: str = ""
    total_duration_ms: int = 0

    mood_examples: List[str] = [
        "Chill rainy day vibes with acoustic guitar",
        "Energetic workout music to pump me up",
        "Relaxing background music for studying",
        "Upbeat party anthems from the 2000s",
        "Melancholic indie tracks for late night reflection"
    ]

    def on_load(self):
        super().on_load()

    def use_example(self, example: str):
        self.mood_query = example

    def set_max_tracks(self, value: int):
        self.max_tracks = max(10, min(100, value))

    def set_year_range(self, year_min: Optional[int], year_max: Optional[int]):
        self.year_min = year_min
        self.year_max = year_max

    def set_year_min(self, value: str):
        self.year_min = int(value) if value else None

    def set_year_max(self, value: str):
        self.year_max = int(value) if value else None

    @rx.event(background=True)
    async def generate_playlist(self):
        async with self:
            if not self.mood_query.strip():
                return

            self.is_generating = True
            self.generation_progress = 0
            self.generation_message = "Starting playlist generation..."
            self.generated_playlist = []
            self.total_duration_ms = 0

        try:
            from plexmix.config.settings import Settings
            from plexmix.config.credentials import get_google_api_key, get_openai_api_key
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.database.vector_index import VectorIndex
            from plexmix.utils.embeddings import EmbeddingGenerator
            from plexmix.ai.providers.gemini import GeminiProvider
            from plexmix.ai.providers.openai import OpenAIProvider
            from plexmix.playlist.generator import PlaylistGenerator

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                async with self:
                    self.generation_message = "Database not found. Please sync your library first."
                    self.is_generating = False
                return

            db = SQLiteManager(str(db_path))
            db.connect()

            vector_index = VectorIndex(db_path.parent / "vector_index.faiss")
            if not vector_index.index_path.exists():
                async with self:
                    self.generation_message = "Vector index not found. Please generate embeddings first."
                    self.is_generating = False
                db.close()
                return

            vector_index.load_index()

            api_key = None
            ai_provider_name = settings.ai.default_provider
            if ai_provider_name == "gemini":
                api_key = get_google_api_key()
                ai_provider = GeminiProvider(api_key=api_key, model=settings.ai.model)
            elif ai_provider_name == "openai":
                api_key = get_openai_api_key()
                ai_provider = OpenAIProvider(api_key=api_key, model=settings.ai.model)
            else:
                async with self:
                    self.generation_message = f"Unsupported AI provider: {ai_provider_name}"
                    self.is_generating = False
                db.close()
                return

            embedding_api_key = None
            embedding_provider = settings.embedding.default_provider
            if embedding_provider == "gemini":
                embedding_api_key = get_google_api_key()
            elif embedding_provider == "openai":
                embedding_api_key = get_openai_api_key()

            embedding_generator = EmbeddingGenerator(
                provider=embedding_provider,
                api_key=embedding_api_key,
                model=settings.embedding.model
            )

            playlist_generator = PlaylistGenerator(
                db_manager=db,
                vector_index=vector_index,
                ai_provider=ai_provider,
                embedding_generator=embedding_generator
            )

            filters = {}
            if self.genre_filter:
                filters['genre'] = self.genre_filter
            if self.year_min is not None:
                filters['year_min'] = self.year_min
            if self.year_max is not None:
                filters['year_max'] = self.year_max

            def progress_callback(progress: float, message: str):
                async def update_state():
                    async with self:
                        self.generation_progress = int(progress * 100)
                        self.generation_message = message
                asyncio.create_task(update_state())

            mood_query_text = self.mood_query
            max_tracks_val = self.max_tracks

            playlist_tracks = playlist_generator.generate(
                mood_query=mood_query_text,
                max_tracks=max_tracks_val,
                filters=filters if filters else None,
                progress_callback=progress_callback
            )

            total_duration = sum(track.get('duration_ms', 0) for track in playlist_tracks)

            db.close()

            async with self:
                self.generated_playlist = playlist_tracks
                self.total_duration_ms = total_duration
                self.is_generating = False
                self.generation_progress = 100
                self.generation_message = f"Generated {len(playlist_tracks)} tracks!"

        except Exception as e:
            async with self:
                self.is_generating = False
                self.generation_message = f"Generation failed: {str(e)}"

    @rx.event(background=True)
    async def regenerate(self):
        await self.generate_playlist()

    def remove_track(self, track_id: int):
        self.generated_playlist = [t for t in self.generated_playlist if t['id'] != track_id]
        self.total_duration_ms = sum(track.get('duration_ms', 0) for track in self.generated_playlist)

    @rx.event(background=True)
    async def save_to_plex(self):
        async with self:
            if not self.generated_playlist or not self.playlist_name.strip():
                return

            self.is_generating = True
            self.generation_message = "Saving to Plex..."

        try:
            from plexmix.config.settings import Settings
            from plexmix.config.credentials import get_plex_token
            from plexmix.plex.client import PlexClient

            settings = Settings.load_from_file()
            plex_token = get_plex_token()

            if not settings.plex.url or not plex_token:
                async with self:
                    self.generation_message = "Plex not configured"
                    self.is_generating = False
                return

            plex_client = PlexClient(settings.plex.url, plex_token)
            plex_client.connect()

            track_ids = [track['id'] for track in self.generated_playlist]
            plex_key = plex_client.create_playlist(self.playlist_name, track_ids)

            async with self:
                self.is_generating = False
                self.generation_message = f"Saved to Plex: {self.playlist_name}"

        except Exception as e:
            async with self:
                self.is_generating = False
                self.generation_message = f"Failed to save to Plex: {str(e)}"

    @rx.event(background=True)
    async def save_locally(self):
        async with self:
            if not self.generated_playlist or not self.playlist_name.strip():
                return

            self.is_generating = True
            self.generation_message = "Saving locally..."

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.database.models import Playlist

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            db = SQLiteManager(str(db_path))
            db.connect()

            track_ids = [track['id'] for track in self.generated_playlist]

            playlist = Playlist(
                name=self.playlist_name,
                created_by_ai=True,
                mood_query=self.mood_query
            )

            playlist_id = db.insert_playlist(playlist)

            for position, track_id in enumerate(track_ids):
                db.add_track_to_playlist(playlist_id, track_id, position)

            db.close()

            async with self:
                self.is_generating = False
                self.generation_message = f"Saved locally: {self.playlist_name}"

        except Exception as e:
            async with self:
                self.is_generating = False
                self.generation_message = f"Failed to save locally: {str(e)}"

    def export_m3u(self):
        if not self.generated_playlist:
            return

        m3u_content = "#EXTM3U\n"
        for track in self.generated_playlist:
            duration_sec = track.get('duration_ms', 0) // 1000
            artist = track.get('artist', 'Unknown')
            title = track.get('title', 'Unknown')
            m3u_content += f"#EXTINF:{duration_sec},{artist} - {title}\n"
            m3u_content += f"track_{track['id']}.mp3\n"

        return rx.download(data=m3u_content, filename=f"{self.playlist_name or 'playlist'}.m3u")
