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
    candidate_pool_multiplier: int = 25

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

    def set_mood_query(self, value: str):
        self.mood_query = value

    def set_genre_filter(self, value: str):
        self.genre_filter = value

    def set_playlist_name(self, value: str):
        self.playlist_name = value

    def set_max_tracks(self, value: int):
        self.max_tracks = max(10, min(100, value))

    def set_candidate_pool_multiplier(self, value: int):
        self.candidate_pool_multiplier = max(1, min(100, value))

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

            # Get the embedding dimension from settings
            embedding_provider = settings.embedding.default_provider
            if embedding_provider == "gemini":
                dimension = 3072
            elif embedding_provider == "openai":
                dimension = 1536
            elif embedding_provider == "cohere":
                dimension = 1024
            else:  # local
                dimension = 384

            index_path = settings.database.faiss_index_path
            vector_index = VectorIndex(dimension=dimension, index_path=index_path)

            if not vector_index.index or vector_index.index.ntotal == 0:
                async with self:
                    self.generation_message = "Vector index not found or empty. Please generate embeddings first."
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
                import asyncio
                loop = asyncio.get_event_loop()

                async def update():
                    async with self:
                        self.generation_progress = int(progress * 100)
                        self.generation_message = message

                asyncio.run_coroutine_threadsafe(update(), loop)

            mood_query_text = self.mood_query
            max_tracks_val = self.max_tracks
            pool_multiplier = self.candidate_pool_multiplier

            print(f"Generating playlist with mood: {mood_query_text}, max_tracks: {max_tracks_val}, pool_multiplier: {pool_multiplier}")

            playlist_tracks = playlist_generator.generate(
                mood_query=mood_query_text,
                max_tracks=max_tracks_val,
                candidate_pool_multiplier=pool_multiplier,
                filters=filters if filters else None,
                progress_callback=progress_callback
            )

            print(f"Generated {len(playlist_tracks)} tracks")

            # Format durations for display
            for track in playlist_tracks:
                duration_ms = track.get('duration_ms', 0)
                if duration_ms:
                    minutes = duration_ms // 60000
                    seconds = (duration_ms // 1000) % 60
                    track['duration_formatted'] = f"{minutes}:{seconds:02d}"
                else:
                    track['duration_formatted'] = "0:00"

            total_duration = sum(track.get('duration_ms', 0) for track in playlist_tracks)

            db.close()

            async with self:
                self.generated_playlist = playlist_tracks
                self.total_duration_ms = total_duration
                self.is_generating = False
                self.generation_progress = 100
                if len(playlist_tracks) > 0:
                    self.generation_message = f"Generated {len(playlist_tracks)} tracks!"
                else:
                    self.generation_message = "No tracks generated. Check console for errors."

        except Exception as e:
            import traceback
            print(f"Error generating playlist: {e}")
            print(traceback.format_exc())
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
            plex_client.select_library(settings.plex.library_name)

            track_plex_keys = [track['plex_key'] for track in self.generated_playlist]
            plex_key = plex_client.create_playlist(self.playlist_name, track_plex_keys)

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

    def format_duration(self, duration_ms: int) -> str:
        """Format duration from milliseconds to mm:ss"""
        if not duration_ms:
            return "0:00"

        total_seconds = duration_ms // 1000
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}:{seconds:02d}"

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
