import reflex as rx
import asyncio
import logging
from typing import Optional
from plexmix.ui.states.app_state import AppState


def _str_dict(d: dict) -> dict[str, str]:
    """Convert a dict with mixed-type values to all-string values for Reflex."""
    return {k: ("" if v is None else str(v)) for k, v in d.items()}


logger = logging.getLogger(__name__)


class GeneratorState(AppState):
    mood_query: str = ""
    max_tracks: int = 50
    genre_filter: str = ""
    year_min: str = ""
    year_max: str = ""
    include_artists: str = ""
    exclude_artists: str = ""
    candidate_pool_multiplier: int = 25

    tempo_min: str = ""
    tempo_max: str = ""
    energy_level: str = ""
    key_filter: str = ""
    danceability_min: str = ""

    audio_analyzed_count: int = 0

    is_generating: bool = False
    generation_progress: int = 0
    generation_message: str = ""
    generation_log: list[str] = []

    generated_playlist: list[dict[str, str]] = []
    playlist_name: str = ""
    total_duration_ms: int = 0

    _generation_id: int = 0

    mood_examples: list[str] = [
        "Chill rainy day vibes with acoustic guitar",
        "Energetic workout music to pump me up",
        "Relaxing background music for studying",
        "Upbeat party anthems from the 2000s",
        "Melancholic indie tracks for late night reflection"
    ]

    def on_load(self):
        if not self.check_auth():
            self.is_page_loading = False
            return
        super().on_load()
        self._load_audio_stats()
        self.is_page_loading = False

    def _load_audio_stats(self):
        """Check how many tracks have audio analysis data."""
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            if db_path.exists():
                db = SQLiteManager(str(db_path))
                db.connect()
                row = db.conn.execute("SELECT COUNT(*) FROM audio_features").fetchone()
                self.audio_analyzed_count = row[0] if row else 0
                db.close()
            else:
                self.audio_analyzed_count = 0
        except Exception:
            self.audio_analyzed_count = 0

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

    def set_year_range(self, year_min: str, year_max: str):
        self.year_min = year_min
        self.year_max = year_max

    def set_year_min(self, value: str):
        self.year_min = value

    def set_year_max(self, value: str):
        self.year_max = value

    def set_tempo_min(self, value: str):
        self.tempo_min = value

    def set_tempo_max(self, value: str):
        self.tempo_max = value

    def set_energy_level(self, value: str):
        self.energy_level = "" if value == "Any" else value

    def set_key_filter(self, value: str):
        self.key_filter = "" if value == "Any" else value

    def set_danceability_min(self, value: str):
        self.danceability_min = value

    @rx.event(background=True)
    async def generate_playlist(self):
        async with self:
            if not self.mood_query.strip():
                return

            self._generation_id += 1
            self.is_generating = True
            self.generation_progress = 0
            self.generation_message = "Starting playlist generation..."
            self.generation_log = ["Starting playlist generation..."]
            self.generated_playlist = []
            self.total_duration_ms = 0

        current_gen = self._generation_id

        try:
            from plexmix.config.settings import Settings
            from plexmix.config.credentials import (
                get_google_api_key, get_openai_api_key, get_cohere_api_key,
                get_custom_embedding_api_key,
            )
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

            # Get the embedding settings (but don't create generator yet - it blocks!)
            embedding_provider = settings.embedding.default_provider
            embedding_model = settings.embedding.model
            index_path = str(settings.database.get_index_path())

            embedding_api_key = None
            embedding_kwargs = {}
            if embedding_provider == "gemini":
                embedding_api_key = get_google_api_key()
            elif embedding_provider == "openai":
                embedding_api_key = get_openai_api_key()
            elif embedding_provider == "cohere":
                embedding_api_key = get_cohere_api_key()
            elif embedding_provider == "custom":
                embedding_model = settings.embedding.custom_model or embedding_model
                embedding_kwargs = {
                    "custom_endpoint": settings.embedding.custom_endpoint,
                    "custom_api_key": (
                        settings.embedding.custom_api_key or get_custom_embedding_api_key()
                    ),
                    "custom_dimension": settings.embedding.custom_dimension,
                }

            filters = {}
            if self.genre_filter:
                filters['genre'] = self.genre_filter
            if self.year_min:
                try:
                    filters['year_min'] = int(self.year_min)
                except ValueError:
                    pass
            if self.year_max:
                try:
                    filters['year_max'] = int(self.year_max)
                except ValueError:
                    pass
            if self.tempo_min:
                try:
                    filters['tempo_min'] = float(self.tempo_min)
                except ValueError:
                    pass
            if self.tempo_max:
                try:
                    filters['tempo_max'] = float(self.tempo_max)
                except ValueError:
                    pass
            if self.energy_level:
                filters['energy_level'] = self.energy_level
            if self.key_filter:
                filters['key'] = self.key_filter
            if self.danceability_min:
                try:
                    filters['danceability_min'] = float(self.danceability_min)
                except ValueError:
                    pass

            loop = asyncio.get_running_loop()

            def progress_callback(progress: float, message: str):
                async def update():
                    async with self:
                        progress_value = max(0, min(100, int(progress * 100)))
                        self.generation_progress = progress_value
                        self.generation_message = message
                        self.generation_log = (self.generation_log + [message])[-25:]

                asyncio.run_coroutine_threadsafe(update(), loop)

            mood_query_text = self.mood_query
            max_tracks_val = self.max_tracks
            pool_multiplier = self.candidate_pool_multiplier

            logger.info(
                "Playlist generation started | mood='%s' max_tracks=%s multiplier=%s",
                mood_query_text,
                max_tracks_val,
                pool_multiplier,
            )

            def run_generation():
                # Create EmbeddingGenerator inside executor to avoid blocking UI
                # (LocalEmbeddingProvider spawns subprocess and waits for model load)
                embedding_generator = EmbeddingGenerator(
                    provider=embedding_provider,
                    api_key=embedding_api_key,
                    model=embedding_model,
                    **embedding_kwargs,
                )
                dimension = embedding_generator.get_dimension()

                local_db = SQLiteManager(str(db_path))
                local_db.connect()
                try:
                    local_vector_index = VectorIndex(dimension=dimension, index_path=index_path)

                    if not local_vector_index.index or local_vector_index.index.ntotal == 0:
                        return {
                            "tracks": [],
                            "total_duration": 0,
                            "error": "Vector index not found or empty. Please generate embeddings first.",
                        }

                    if local_vector_index.dimension_mismatch:
                        mismatch_msg = (
                            f"⚠️ Embedding dimension mismatch! Existing embeddings are {local_vector_index.loaded_dimension}D "
                            f"but current provider '{embedding_provider}' uses {dimension}D. Please regenerate embeddings."
                        )
                        return {
                            "tracks": [],
                            "total_duration": 0,
                            "error": mismatch_msg,
                        }

                    playlist_generator = PlaylistGenerator(
                        db_manager=local_db,
                        vector_index=local_vector_index,
                        embedding_generator=embedding_generator,
                    )

                    tracks = playlist_generator.generate(
                        mood_query=mood_query_text,
                        max_tracks=max_tracks_val,
                        candidate_pool_multiplier=pool_multiplier,
                        filters=filters if filters else None,
                        progress_callback=progress_callback,
                    )

                    total_duration = sum(track.get('duration_ms', 0) for track in tracks)

                    return {
                        "tracks": tracks,
                        "total_duration": total_duration,
                        "error": None,
                    }
                finally:
                    local_db.close()

            generation_result = await loop.run_in_executor(None, run_generation)

            if generation_result.get("error"):
                error_message = generation_result["error"]
                logger.warning("Playlist generation aborted: %s", error_message)
                async with self:
                    self.is_generating = False
                    self.generation_message = error_message
                    self.generation_log = (self.generation_log + [error_message])[-25:]
                return

            playlist_tracks = generation_result["tracks"]
            total_duration = generation_result["total_duration"]

            logger.info("Playlist generation finished with %s tracks", len(playlist_tracks))

            # Format durations for display
            for track in playlist_tracks:
                duration_ms = track.get('duration_ms', 0)
                if duration_ms:
                    minutes = duration_ms // 60000
                    seconds = (duration_ms // 1000) % 60
                    track['duration_formatted'] = f"{minutes}:{seconds:02d}"
                else:
                    track['duration_formatted'] = "0:00"

            async with self:
                self.generated_playlist = [_str_dict(t) for t in playlist_tracks]
                self.total_duration_ms = total_duration
                self.is_generating = False
                self.generation_progress = 100
                if len(playlist_tracks) > 0:
                    final_msg = f"Generated {len(playlist_tracks)} tracks!"
                else:
                    final_msg = "No tracks generated. Check logs for details."
                self.generation_message = final_msg
                self.generation_log = (self.generation_log + [final_msg])[-25:]

            # Auto-dismiss success message after 5 seconds (keep errors/warnings visible)
            if len(playlist_tracks) > 0:
                await asyncio.sleep(5)
                async with self:
                    if self._generation_id == current_gen and not self.is_generating:
                        self.generation_message = ""
                        self.generation_log = []

        except Exception as e:
            import traceback
            logger.error("Playlist generation failed: %s", e, exc_info=True)
            async with self:
                self.is_generating = False
                error_msg = f"Generation failed: {str(e)}"
                self.generation_message = error_msg
                self.generation_log = (self.generation_log + [error_msg, traceback.format_exc()])[-25:]

    @rx.event(background=True)
    async def regenerate(self):
        await self.generate_playlist()

    def remove_track(self, track_id: str):
        self.generated_playlist = [t for t in self.generated_playlist if t['id'] != track_id]
        self.total_duration_ms = sum(int(track.get('duration_ms', '0') or '0') for track in self.generated_playlist)

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
                    self.generation_message = ""
                    self.is_generating = False
                yield rx.toast.error("Plex not configured")
                return

            plex_client = PlexClient(settings.plex.url, plex_token)
            plex_client.connect()
            plex_client.select_library(settings.plex.library_name)

            track_plex_keys = [int(track['plex_key']) for track in self.generated_playlist]
            plex_key = plex_client.create_playlist(self.playlist_name, track_plex_keys)

            async with self:
                self.is_generating = False
                self.generation_message = ""

            yield rx.toast.success(f"Saved to Plex: {self.playlist_name}")

        except Exception as e:
            async with self:
                self.is_generating = False
                self.generation_message = ""

            yield rx.toast.error(f"Failed to save to Plex: {str(e)}")

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

            track_ids = [int(track['id']) for track in self.generated_playlist]

            playlist = Playlist(
                name=self.playlist_name,
                created_by_ai=True,
                mood_query=self.mood_query
            )

            playlist_id = db.insert_playlist(playlist)
            db.add_tracks_to_playlist(playlist_id, track_ids)

            db.close()

            async with self:
                self.is_generating = False
                self.generation_message = ""

            yield rx.toast.success(f"Saved locally: {self.playlist_name}")

        except Exception as e:
            async with self:
                self.is_generating = False
                self.generation_message = ""

            yield rx.toast.error(f"Failed to save locally: {str(e)}")

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

        from plexmix.config.settings import Settings
        settings = Settings.load_from_file()

        m3u_content = "#EXTM3U\n"
        for track in self.generated_playlist:
            duration_sec = int(track.get('duration_ms', '0') or '0') // 1000
            artist = track.get('artist', 'Unknown')
            title = track.get('title', 'Unknown')
            m3u_content += f"#EXTINF:{duration_sec},{artist} - {title}\n"
            file_path = track.get('file_path') or ""
            if file_path:
                file_path = settings.audio.resolve_path(file_path)
            m3u_content += f"{file_path}\n" if file_path else f"track_{track['id']}.mp3\n"

        return rx.download(data=m3u_content, filename=f"{self.playlist_name or 'playlist'}.m3u")
