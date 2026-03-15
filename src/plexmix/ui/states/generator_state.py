import reflex as rx
import asyncio
import logging
from plexmix.config.constants import GENERATION_LOG_MAX
from plexmix.ui.states.app_state import AppState


from plexmix.ui.utils.helpers import str_dict as _str_dict

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
    shuffle_mode: str = "similarity"
    avoid_recent: int = 0

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

    # Templates
    templates: list[dict[str, str]] = []
    show_save_template_dialog: bool = False
    template_name_input: str = ""

    mood_examples: list[str] = [
        "Chill rainy day vibes with acoustic guitar",
        "Energetic workout music to pump me up",
        "Relaxing background music for studying",
        "Upbeat party anthems from the 2000s",
        "Melancholic indie tracks for late night reflection",
    ]

    def on_load(self):
        if not self.check_auth():
            self.is_page_loading = False
            return
        super().on_load()
        self._load_audio_stats()
        self._load_templates()

        # Check for rerun config from History page
        if self._rerun_generation_config:
            self.load_generation_config(self._rerun_generation_config)
            self._rerun_generation_config = ""

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

    def _load_templates(self):
        """Load saved templates from database."""
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            if db_path.exists():
                db = SQLiteManager(str(db_path))
                db.connect()
                templates = db.get_templates()
                db.close()
                self.templates = [
                    _str_dict(
                        {
                            "id": str(t.id or ""),
                            "name": t.name,
                            "mood_query": t.mood_query,
                            "max_tracks": str(t.max_tracks),
                            "genre_filter": t.genre_filter,
                            "year_min": str(t.year_min or ""),
                            "year_max": str(t.year_max or ""),
                            "tempo_min": str(t.tempo_min or ""),
                            "tempo_max": str(t.tempo_max or ""),
                            "energy_level": t.energy_level,
                            "key_filter": t.key_filter,
                            "danceability_min": str(t.danceability_min or ""),
                            "shuffle_mode": t.shuffle_mode,
                            "candidate_pool_multiplier": str(t.candidate_pool_multiplier),
                            "is_preset": "1" if t.is_preset else "0",
                        }
                    )
                    for t in templates
                ]
            else:
                self.templates = []

            # Seed built-in presets if none exist
            if not any(t.get("is_preset") == "1" for t in self.templates):
                self._seed_presets()

        except Exception:
            self.templates = []

    def _seed_presets(self):
        """Insert built-in template presets into the database."""
        from plexmix.config.settings import Settings
        from plexmix.database.sqlite_manager import SQLiteManager
        from plexmix.database.models import PlaylistTemplate

        presets = [
            PlaylistTemplate(
                name="Morning Commute",
                mood_query="Upbeat feel-good songs to start the day",
                max_tracks=30,
                energy_level="medium",
                shuffle_mode="alternating_artists",
                is_preset=True,
            ),
            PlaylistTemplate(
                name="Workout",
                mood_query="High energy pump-up workout music",
                max_tracks=40,
                energy_level="high",
                shuffle_mode="energy_curve",
                is_preset=True,
            ),
            PlaylistTemplate(
                name="Study Session",
                mood_query="Calm instrumental focus music for studying",
                max_tracks=50,
                energy_level="low",
                shuffle_mode="similarity",
                is_preset=True,
            ),
            PlaylistTemplate(
                name="Dinner Party",
                mood_query="Sophisticated jazz and soul for an evening gathering",
                max_tracks=40,
                genre_filter="jazz",
                shuffle_mode="alternating_artists",
                is_preset=True,
            ),
            PlaylistTemplate(
                name="Late Night",
                mood_query="Mellow downtempo and ambient tracks for winding down",
                max_tracks=30,
                energy_level="low",
                shuffle_mode="similarity",
                is_preset=True,
            ),
        ]

        try:
            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            if db_path.exists():
                db = SQLiteManager(str(db_path))
                db.connect()
                for preset in presets:
                    db.insert_template(preset)
                db.close()
                self._load_templates()
        except Exception:
            pass

    def apply_template(self, template_id: str):
        """Load a template's settings into the generator form."""
        template = next((t for t in self.templates if t.get("id") == template_id), None)
        if not template:
            return

        self.mood_query = template.get("mood_query", "")
        self.max_tracks = int(template.get("max_tracks", "50") or "50")
        self.genre_filter = template.get("genre_filter", "")
        self.year_min = template.get("year_min", "")
        self.year_max = template.get("year_max", "")
        self.tempo_min = template.get("tempo_min", "")
        self.tempo_max = template.get("tempo_max", "")
        self.energy_level = template.get("energy_level", "")
        self.key_filter = template.get("key_filter", "")
        self.danceability_min = template.get("danceability_min", "")
        self.shuffle_mode = template.get("shuffle_mode", "similarity")
        self.candidate_pool_multiplier = int(
            template.get("candidate_pool_multiplier", "25") or "25"
        )

    def open_save_template_dialog(self):
        self.template_name_input = ""
        self.show_save_template_dialog = True

    def close_save_template_dialog(self):
        self.show_save_template_dialog = False

    def set_template_name_input(self, value: str):
        self.template_name_input = value

    def save_current_as_template(self):
        """Save the current generator config as a named template."""
        name = self.template_name_input.strip()
        if not name:
            return

        from plexmix.config.settings import Settings
        from plexmix.database.sqlite_manager import SQLiteManager
        from plexmix.database.models import PlaylistTemplate

        try:
            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            db = SQLiteManager(str(db_path))
            db.connect()

            template = PlaylistTemplate(
                name=name,
                mood_query=self.mood_query,
                max_tracks=self.max_tracks,
                genre_filter=self.genre_filter,
                year_min=int(self.year_min) if self.year_min else None,
                year_max=int(self.year_max) if self.year_max else None,
                tempo_min=float(self.tempo_min) if self.tempo_min else None,
                tempo_max=float(self.tempo_max) if self.tempo_max else None,
                energy_level=self.energy_level,
                key_filter=self.key_filter,
                danceability_min=float(self.danceability_min) if self.danceability_min else None,
                shuffle_mode=self.shuffle_mode,
                candidate_pool_multiplier=self.candidate_pool_multiplier,
                is_preset=False,
            )

            db.insert_template(template)
            db.close()
            self._load_templates()
            self.show_save_template_dialog = False
        except Exception as e:
            logger.error("Failed to save template: %s", e)

    def delete_template(self, template_id: str):
        """Delete a user-created template (presets cannot be deleted)."""
        template = next((t for t in self.templates if t.get("id") == template_id), None)
        if not template or template.get("is_preset") == "1":
            return

        from plexmix.config.settings import Settings
        from plexmix.database.sqlite_manager import SQLiteManager

        try:
            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            db = SQLiteManager(str(db_path))
            db.connect()
            db.delete_template(int(template_id))
            db.close()
            self._load_templates()
        except Exception as e:
            logger.error("Failed to delete template: %s", e)

    def load_generation_config(self, config_json: str):
        """Load generation parameters from a JSON config string (used by rerun)."""
        import json as _json

        try:
            config = _json.loads(config_json)
        except (ValueError, TypeError):
            return

        self.mood_query = config.get("mood_query", "")
        self.max_tracks = int(config.get("max_tracks", 50))
        self.genre_filter = config.get("genre_filter", "")
        self.year_min = str(config.get("year_min", "") or "")
        self.year_max = str(config.get("year_max", "") or "")
        self.tempo_min = str(config.get("tempo_min", "") or "")
        self.tempo_max = str(config.get("tempo_max", "") or "")
        self.energy_level = config.get("energy_level", "")
        self.key_filter = config.get("key_filter", "")
        self.danceability_min = str(config.get("danceability_min", "") or "")
        self.shuffle_mode = config.get("shuffle_mode", "similarity")
        self.candidate_pool_multiplier = int(config.get("candidate_pool_multiplier", 25))
        self.avoid_recent = int(config.get("avoid_recent", 0))

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

    def set_shuffle_mode(self, value: str):
        self.shuffle_mode = value

    def set_avoid_recent(self, value: str):
        try:
            self.avoid_recent = max(0, int(value)) if value else 0
        except ValueError:
            self.avoid_recent = 0

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
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.database.vector_index import VectorIndex
            from plexmix.playlist.generator import PlaylistGenerator
            from plexmix.services.providers import build_embedding_generator

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                async with self:
                    self.generation_message = "Database not found. Please sync your library first."
                    self.is_generating = False
                return

            # Resolve embedding settings now (but don't instantiate generator yet - it blocks!)
            embedding_provider = settings.embedding.default_provider
            index_path = str(settings.database.get_index_path())

            from plexmix.services.playlist_service import build_generation_filters, safe_int, safe_float

            filters = build_generation_filters(
                genre=self.genre_filter or None,
                year_min=safe_int(self.year_min),
                year_max=safe_int(self.year_max),
                tempo_min=safe_float(self.tempo_min),
                tempo_max=safe_float(self.tempo_max),
                energy_level=self.energy_level or None,
                key=self.key_filter or None,
                danceability_min=safe_float(self.danceability_min),
            )

            loop = asyncio.get_running_loop()

            def progress_callback(progress: float, message: str):
                async def update():
                    async with self:
                        progress_value = max(0, min(100, int(progress * 100)))
                        self.generation_progress = progress_value
                        self.generation_message = message
                        self.generation_log = (self.generation_log + [message])[-GENERATION_LOG_MAX:]

                asyncio.run_coroutine_threadsafe(update(), loop)

            mood_query_text = self.mood_query
            max_tracks_val = self.max_tracks
            pool_multiplier = self.candidate_pool_multiplier
            shuffle = self.shuffle_mode
            avoid_recent_val = self.avoid_recent

            logger.info(
                "Playlist generation started | mood='%s' max_tracks=%s multiplier=%s shuffle=%s",
                mood_query_text,
                max_tracks_val,
                pool_multiplier,
                shuffle,
            )

            def run_generation():
                # Create EmbeddingGenerator inside executor to avoid blocking UI
                # (LocalEmbeddingProvider spawns subprocess and waits for model load)
                embedding_generator = build_embedding_generator(settings)
                if embedding_generator is None:
                    return {
                        "tracks": [],
                        "total_duration": 0,
                        "error": "Embedding provider not configured. Check your API keys.",
                    }
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
                        shuffle_mode=shuffle,
                        avoid_recent=avoid_recent_val,
                    )

                    total_duration = sum(track.get("duration_ms", 0) for track in tracks)

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
                    self.generation_log = (self.generation_log + [error_message])[-GENERATION_LOG_MAX:]
                return

            playlist_tracks = generation_result["tracks"]
            total_duration = generation_result["total_duration"]

            logger.info("Playlist generation finished with %s tracks", len(playlist_tracks))

            # Format durations for display
            for track in playlist_tracks:
                duration_ms = track.get("duration_ms", 0)
                if duration_ms:
                    minutes = duration_ms // 60000
                    seconds = (duration_ms // 1000) % 60
                    track["duration_formatted"] = f"{minutes}:{seconds:02d}"
                else:
                    track["duration_formatted"] = "0:00"

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
                self.generation_log = (self.generation_log + [final_msg])[-GENERATION_LOG_MAX:]

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
                self.generation_log = (self.generation_log + [error_msg, traceback.format_exc()])[
                    -GENERATION_LOG_MAX:
                ]

    @rx.event(background=True)
    async def regenerate(self):
        await self.generate_playlist()

    def remove_track(self, track_id: str):
        self.generated_playlist = [t for t in self.generated_playlist if t["id"] != track_id]
        self.total_duration_ms = sum(
            int(track.get("duration_ms", "0") or "0") for track in self.generated_playlist
        )

    @rx.event(background=True)
    async def save_to_plex(self):
        async with self:
            if not self.generated_playlist or not self.playlist_name.strip():
                return

            self.is_generating = True
            self.generation_message = "Saving to Plex..."

        try:
            from plexmix.config.settings import Settings
            from plexmix.services.sync_service import connect_plex, PlexConnectionError

            settings = Settings.load_from_file()

            try:
                plex_client = connect_plex(settings)
            except PlexConnectionError as e:
                async with self:
                    self.is_generating = False
                    self.generation_message = ""
                yield rx.toast.error(str(e))
                return

            if not settings.plex.library_name:
                async with self:
                    self.is_generating = False
                    self.generation_message = ""
                yield rx.toast.error("Music library not configured")
                return

            track_plex_keys = [int(track["plex_key"]) for track in self.generated_playlist]
            plex_client.create_playlist(self.playlist_name, track_plex_keys)

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

            import json as _json

            track_ids = [int(track["id"]) for track in self.generated_playlist]

            # Store full generation config for rerun capability
            gen_config = _json.dumps(
                {
                    "mood_query": self.mood_query,
                    "max_tracks": self.max_tracks,
                    "genre_filter": self.genre_filter,
                    "year_min": self.year_min,
                    "year_max": self.year_max,
                    "tempo_min": self.tempo_min,
                    "tempo_max": self.tempo_max,
                    "energy_level": self.energy_level,
                    "key_filter": self.key_filter,
                    "danceability_min": self.danceability_min,
                    "shuffle_mode": self.shuffle_mode,
                    "candidate_pool_multiplier": self.candidate_pool_multiplier,
                    "avoid_recent": self.avoid_recent,
                }
            )

            playlist = Playlist(
                name=self.playlist_name,
                created_by_ai=True,
                mood_query=self.mood_query,
                generation_config=gen_config,
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
            duration_sec = int(track.get("duration_ms", "0") or "0") // 1000
            artist = track.get("artist", "Unknown")
            title = track.get("title", "Unknown")
            m3u_content += f"#EXTINF:{duration_sec},{artist} - {title}\n"
            file_path = track.get("file_path") or ""
            if file_path:
                file_path = settings.audio.resolve_path(file_path)
            m3u_content += f"{file_path}\n" if file_path else f"track_{track['id']}.mp3\n"

        return rx.download(data=m3u_content, filename=f"{self.playlist_name or 'playlist'}.m3u")
