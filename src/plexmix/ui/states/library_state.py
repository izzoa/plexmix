import reflex as rx
import asyncio
import logging
import atexit
import time
from typing import Dict, Optional
from threading import Event
from plexmix.ui.states.app_state import AppState


def _str_dict(d: dict) -> dict[str, str]:
    """Convert a dict with mixed-type values to all-string values for Reflex."""
    return {k: ("" if v is None else str(v)) for k, v in d.items()}

logger = logging.getLogger(__name__)


def _format_eta(seconds: float) -> str:
    """Format remaining seconds as a human-readable ETA string."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s remaining"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs}s remaining"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m remaining"


# Per-client globals for background tasks
_sync_cancel_events: Dict[str, Event] = {}
_search_tasks: Dict[str, asyncio.Task] = {}
_audio_cancel_events: Dict[str, Event] = {}
_audio_pause_events: Dict[str, asyncio.Event] = {}


def _cleanup_client_state(client_token: str) -> None:
    """Clean up any state associated with a disconnected client."""
    if client_token in _sync_cancel_events:
        _sync_cancel_events[client_token].set()  # Signal cancellation
        del _sync_cancel_events[client_token]
    if client_token in _search_tasks:
        task = _search_tasks[client_token]
        if not task.done():
            task.cancel()
        del _search_tasks[client_token]
    if client_token in _audio_cancel_events:
        _audio_cancel_events[client_token].set()
        del _audio_cancel_events[client_token]
    _audio_pause_events.pop(client_token, None)


def _cleanup_all_state() -> None:
    """Clean up all global state on process exit."""
    for token in list(_sync_cancel_events.keys()):
        _sync_cancel_events[token].set()
    _sync_cancel_events.clear()
    for task in list(_search_tasks.values()):
        if not task.done():
            task.cancel()
    _search_tasks.clear()
    for token in list(_audio_cancel_events.keys()):
        _audio_cancel_events[token].set()
    _audio_cancel_events.clear()
    _audio_pause_events.clear()


# Register cleanup on process exit
atexit.register(_cleanup_all_state)


class LibraryState(AppState):
    tracks: list[dict[str, str]] = []
    total_filtered_tracks: int = 0
    current_page: int = 1
    page_size: int = 50
    search_query: str = ""
    genre_filter: str = ""
    year_min: str = ""
    year_max: str = ""

    is_syncing: bool = False
    sync_progress: int = 0
    sync_message: str = ""
    sync_mode: str = "incremental"
    show_regenerate_confirm: bool = False

    is_embedding: bool = False
    embedding_progress: int = 0
    embedding_message: str = ""

    is_analyzing_audio: bool = False
    audio_analysis_paused: bool = False
    audio_analysis_progress: int = 0
    audio_analysis_message: str = ""
    audio_analysis_eta: str = ""
    show_audio_cancel_confirm: bool = False

    selected_tracks: list[str] = []
    sort_column: str = "title"
    sort_ascending: bool = True
    show_cancel_confirm: bool = False

    def set_sort(self, column: str):
        """Toggle sort direction if same column, otherwise sort ascending by new column."""
        if self.sort_column == column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column
            self.sort_ascending = True
        self.current_page = 1
        self.load_tracks()
        return rx.call_script("window.scrollTo({top: 0, behavior: 'smooth'})")

    def set_sync_mode(self, mode: str):
        self.sync_mode = mode

    def confirm_regenerate_sync(self):
        self.show_regenerate_confirm = True

    def cancel_regenerate_confirm(self):
        self.show_regenerate_confirm = False

    def on_load(self):
        if not self.check_auth():
            self.is_page_loading = False
            return
        super().on_load()
        self.load_tracks()
        self.is_page_loading = False

    def load_tracks(self):
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                self.tracks = []
                self.total_filtered_tracks = 0
                return

            with SQLiteManager(str(db_path)) as db:
                offset = (self.current_page - 1) * self.page_size

                year_min_int = int(self.year_min) if self.year_min else None
                year_max_int = int(self.year_max) if self.year_max else None

                raw_tracks = db.get_tracks(
                    limit=self.page_size,
                    offset=offset,
                    search=self.search_query if self.search_query else None,
                    genre=self.genre_filter if self.genre_filter else None,
                    year_min=year_min_int,
                    year_max=year_max_int,
                    sort_column=self.sort_column,
                    sort_ascending=self.sort_ascending,
                )
                self.tracks = [_str_dict(t) for t in raw_tracks]

                self.total_filtered_tracks = db.count_tracks(
                    search=self.search_query if self.search_query else None,
                    genre=self.genre_filter if self.genre_filter else None,
                    year_min=year_min_int,
                    year_max=year_max_int,
                )

        except Exception as e:
            logger.error("Error loading tracks: %s", e)
            self.tracks = []
            self.total_filtered_tracks = 0

    @rx.event(background=True)
    async def set_search_query(self, query: str):
        token = self.router.session.client_token
        async with self:
            if token in _search_tasks and not _search_tasks[token].done():
                _search_tasks[token].cancel()
            self.search_query = query
            self.current_page = 1

        async def debounced_load():
            await asyncio.sleep(0.5)
            async with self:
                self.load_tracks()

        _search_tasks[token] = asyncio.create_task(debounced_load())

    def set_genre_filter(self, genre: str):
        self.genre_filter = genre
        self.current_page = 1
        self.load_tracks()

    def set_year_range(self, year_min: str, year_max: str):
        self.year_min = year_min
        self.year_max = year_max
        self.current_page = 1
        self.load_tracks()

    def set_year_min(self, value: str):
        self.year_min = value
        self.current_page = 1
        self.load_tracks()

    def set_year_max(self, value: str):
        self.year_max = value
        self.current_page = 1
        self.load_tracks()

    def clear_filters(self):
        self.search_query = ""
        self.genre_filter = ""
        self.year_min = ""
        self.year_max = ""
        self.current_page = 1
        self.load_tracks()

    def next_page(self):
        total_pages = (self.total_filtered_tracks + self.page_size - 1) // self.page_size
        if self.current_page < total_pages:
            self.current_page += 1
            self.load_tracks()
            return rx.call_script("window.scrollTo({top: 0, behavior: 'smooth'})")

    def previous_page(self):
        if self.current_page > 1:
            self.current_page -= 1
            self.load_tracks()
            return rx.call_script("window.scrollTo({top: 0, behavior: 'smooth'})")

    def go_to_page(self, page: int):
        total_pages = (self.total_filtered_tracks + self.page_size - 1) // self.page_size
        if 1 <= page <= total_pages:
            self.current_page = page
            self.load_tracks()
            return rx.call_script("window.scrollTo({top: 0, behavior: 'smooth'})")

    @rx.var(cache=True)
    def all_page_selected(self) -> bool:
        """Returns True when all current-page track IDs are in selected_tracks."""
        if not self.tracks:
            return False
        page_ids = {track['id'] for track in self.tracks}
        return page_ids.issubset(set(self.selected_tracks))

    def toggle_select_all(self, checked: bool):
        """Toggle selection of all tracks on the current page, preserving off-page selections."""
        page_ids = {track['id'] for track in self.tracks}
        if checked:
            existing = set(self.selected_tracks)
            existing.update(page_ids)
            self.selected_tracks = list(existing)
        else:
            self.selected_tracks = [tid for tid in self.selected_tracks if tid not in page_ids]

    def toggle_track_selection(self, track_id: str):
        if track_id in self.selected_tracks:
            self.selected_tracks.remove(track_id)
        else:
            self.selected_tracks.append(track_id)

    def select_all_tracks(self):
        self.selected_tracks = [track['id'] for track in self.tracks]

    def clear_selection(self):
        self.selected_tracks = []

    @rx.event(background=True)
    async def start_sync(self):
        token = self.router.session.client_token
        async with self:
            self.is_syncing = True
            self.sync_progress = 0
            self.sync_message = "Starting sync..."
            self.show_regenerate_confirm = False

        _sync_cancel_events[token] = Event()

        try:
            from plexmix.config.settings import Settings
            from plexmix.config.credentials import get_plex_token
            from plexmix.plex.client import PlexClient
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            plex_token = get_plex_token()

            if not settings.plex.url or not plex_token:
                async with self:
                    self.sync_message = "Plex not configured"
                    self.is_syncing = False
                return

            plex_client = PlexClient(settings.plex.url, plex_token)
            if not plex_client.connect():
                async with self:
                    self.sync_message = "Failed to connect to Plex server"
                    self.is_syncing = False
                return

            if not settings.plex.library_name or not plex_client.select_library(settings.plex.library_name):
                async with self:
                    self.sync_message = f"Music library not found: {settings.plex.library_name or '(not configured)'}"
                    self.is_syncing = False
                return

            db_path = settings.database.get_db_path()
            db = SQLiteManager(str(db_path))
            db.connect()

            from plexmix.plex.sync import SyncEngine
            from plexmix.ai import get_ai_provider
            from plexmix.config.credentials import (
                get_google_api_key,
                get_openai_api_key,
                get_anthropic_api_key,
                get_cohere_api_key,
            )

            def progress_callback(progress: float, message: str):
                async def update_state():
                    async with self:
                        self.sync_progress = int(progress * 100)
                        self.sync_message = message
                asyncio.create_task(update_state())

            ai_provider = None
            provider_name = settings.ai.default_provider or "gemini"
            provider_alias = "claude" if provider_name == "anthropic" else provider_name

            api_key = None
            if provider_alias == "gemini":
                api_key = get_google_api_key()
            elif provider_alias == "openai":
                api_key = get_openai_api_key()
            elif provider_alias == "claude":
                api_key = get_anthropic_api_key()
            elif provider_alias == "cohere":
                api_key = get_cohere_api_key()
            elif provider_alias == "custom":
                from plexmix.config.credentials import get_custom_ai_api_key
                api_key = settings.ai.custom_api_key or get_custom_ai_api_key()

            try:
                ai_provider = get_ai_provider(
                    provider_name=provider_name,
                    api_key=api_key,
                    model=settings.ai.custom_model if provider_name == "custom" else settings.ai.model,
                    temperature=settings.ai.temperature,
                    local_mode=settings.ai.local_mode,
                    local_endpoint=settings.ai.local_endpoint,
                    local_auth_token=settings.ai.local_auth_token,
                    local_max_output_tokens=settings.ai.local_max_output_tokens,
                    custom_endpoint=settings.ai.custom_endpoint,
                    custom_api_key=api_key if provider_name == "custom" else None,
                )
            except ValueError as exc:
                logger.warning(f"AI provider unavailable: {exc}")
                ai_provider = None
                async with self:
                    self.sync_message = "AI provider not configured — syncing without tagging..."
            sync_engine = SyncEngine(plex_client, db, ai_provider=ai_provider)

            sync_mode = None
            async with self:
                sync_mode = self.sync_mode

            if sync_mode == "regenerate":
                sync_engine.regenerate_sync(
                    generate_embeddings=False,
                    progress_callback=progress_callback,
                    cancel_event=_sync_cancel_events.get(token)
                )
            else:
                sync_engine.incremental_sync(
                    generate_embeddings=False,
                    progress_callback=progress_callback,
                    cancel_event=_sync_cancel_events.get(token)
                )

            # Run audio analysis if enabled
            run_audio = settings.audio.analyze_on_sync
            if run_audio:
                async with self:
                    self.sync_message = "Running audio analysis..."
                try:
                    from plexmix.audio.analyzer import EssentiaAnalyzer
                    analyzer = EssentiaAnalyzer()
                    pending_tracks = db.get_tracks_without_audio_features()
                    if pending_tracks:
                        loop = asyncio.get_event_loop()
                        duration_limit = settings.audio.duration_limit
                        analyzed = 0
                        total = len(pending_tracks)
                        for track in pending_tracks:
                            if _sync_cancel_events.get(token, Event()).is_set():
                                break
                            if not track.file_path:
                                continue
                            try:
                                def analyze_track(t=track):
                                    resolved = settings.audio.resolve_path(t.file_path)
                                    return analyzer.analyze(resolved, duration_limit=duration_limit)
                                features_dict = await loop.run_in_executor(None, analyze_track)
                                db.insert_audio_features(track.id, features_dict)
                                analyzed += 1
                                async with self:
                                    self.sync_progress = int((analyzed / total) * 100) if total else 0
                                    self.sync_message = f"Audio analysis: {analyzed}/{total} tracks"
                            except Exception as e:
                                logger.warning(f"Audio analysis failed for track {track.id}: {e}")
                except ImportError:
                    logger.warning("Essentia not installed, skipping audio analysis")
                except Exception as e:
                    logger.warning(f"Audio analysis error: {e}")

            db.close()

            async with self:
                self.is_syncing = False
                self.sync_progress = 100
                self.sync_message = ""
                self.load_tracks()
                self.check_configuration_status()
                self.load_library_stats()

            if ai_provider is None:
                yield rx.toast.warning("Sync completed (tagging skipped — AI provider not configured)")
            else:
                yield rx.toast.success("Sync completed!")

            if token in _sync_cancel_events:
                del _sync_cancel_events[token]

        except KeyboardInterrupt:
            async with self:
                self.is_syncing = False
                self.sync_message = "Sync cancelled"
                self.load_tracks()

            if token in _sync_cancel_events:
                del _sync_cancel_events[token]

        except Exception as e:
            async with self:
                self.is_syncing = False
                self.sync_message = ""

            yield rx.toast.error(f"Sync failed: {str(e)}")

            if token in _sync_cancel_events:
                del _sync_cancel_events[token]

    def request_cancel_sync(self):
        self.show_cancel_confirm = True

    def dismiss_cancel_confirm(self, open: bool = False):
        if not open:
            self.show_cancel_confirm = False

    def cancel_sync(self):
        self.show_cancel_confirm = False
        token = self.router.session.client_token
        if token in _sync_cancel_events:
            _sync_cancel_events[token].set()

    @rx.event(background=True)
    async def generate_embeddings(self):
        async with self:
            if not self.selected_tracks:
                return
            self.is_embedding = True
            self.embedding_progress = 0
            self.embedding_message = "Starting embedding generation..."

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.utils.embeddings import EmbeddingGenerator, create_track_text
            from plexmix.database.models import Embedding
            from plexmix.config.credentials import (
                get_google_api_key, get_openai_api_key, get_cohere_api_key,
                get_custom_embedding_api_key,
            )

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                async with self:
                    self.embedding_message = "Database not found"
                    self.is_embedding = False
                return

            api_key = None
            provider = settings.embedding.default_provider
            embed_kwargs = {}
            if provider == "gemini":
                api_key = get_google_api_key()
            elif provider == "openai":
                api_key = get_openai_api_key()
            elif provider == "cohere":
                api_key = get_cohere_api_key()
            elif provider == "custom":
                embed_kwargs = {
                    "custom_endpoint": settings.embedding.custom_endpoint,
                    "custom_api_key": (
                        settings.embedding.custom_api_key or get_custom_embedding_api_key()
                    ),
                    "custom_dimension": settings.embedding.custom_dimension,
                }

            embed_model = settings.embedding.model
            if provider == "custom":
                embed_model = settings.embedding.custom_model or embed_model

            embedding_generator = EmbeddingGenerator(
                provider=provider,
                api_key=api_key,
                model=embed_model,
                **embed_kwargs,
            )

            db = SQLiteManager(str(db_path))
            db.connect()

            selected_ids = [int(tid) for tid in self.selected_tracks]
            total_tracks = len(selected_ids)
            embeddings_generated = 0

            batch_size = 50
            for i in range(0, len(selected_ids), batch_size):
                batch_ids = selected_ids[i:i + batch_size]
                batch_tracks = []

                for track_id in batch_ids:
                    track = db.get_track_by_id(track_id)
                    if track:
                        artist = db.get_artist_by_id(track.artist_id)
                        album = db.get_album_by_id(track.album_id)

                        track_data = {
                            'id': track.id,
                            'title': track.title,
                            'artist': artist.name if artist else 'Unknown',
                            'album': album.title if album else 'Unknown',
                            'genre': track.genre or '',
                            'year': track.year or '',
                            'tags': track.tags or '',
                            'environments': track.environments or '',
                            'instruments': track.instruments or ''
                        }
                        batch_tracks.append((track, track_data))

                texts = [create_track_text(td[1]) for td in batch_tracks]
                embeddings = embedding_generator.generate_batch_embeddings(texts, batch_size=50)

                for (track, _), embedding_vector in zip(batch_tracks, embeddings):
                    embedding = Embedding(
                        track_id=track.id,
                        embedding_model=embedding_generator.provider_name,
                        embedding_dim=embedding_generator.get_dimension(),
                        vector=embedding_vector
                    )
                    db.insert_embedding(embedding)
                    embeddings_generated += 1

                    async with self:
                        self.embedding_progress = int((embeddings_generated / total_tracks) * 100)
                        self.embedding_message = f"Generated {embeddings_generated}/{total_tracks} embeddings"

            db.close()

            async with self:
                self.is_embedding = False
                self.embedding_progress = 100
                self.embedding_message = ""
                self.clear_selection()
                self.load_tracks()
                self.load_library_stats()

            yield rx.toast.success("Embeddings generated successfully!")

        except Exception as e:
            async with self:
                self.is_embedding = False
                self.embedding_message = ""

            yield rx.toast.error(f"Embedding generation failed: {str(e)}")

    def pause_audio_analysis(self):
        token = self.router.session.client_token
        if token in _audio_pause_events:
            _audio_pause_events[token].clear()  # Block the loop
            self.audio_analysis_paused = True

    def resume_audio_analysis(self):
        token = self.router.session.client_token
        if token in _audio_pause_events:
            _audio_pause_events[token].set()  # Unblock the loop
            self.audio_analysis_paused = False

    def request_cancel_audio(self):
        self.show_audio_cancel_confirm = True

    def dismiss_audio_cancel_confirm(self, open: bool = False):
        if not open:
            self.show_audio_cancel_confirm = False

    def cancel_audio_analysis(self):
        self.show_audio_cancel_confirm = False
        token = self.router.session.client_token
        if token in _audio_cancel_events:
            _audio_cancel_events[token].set()
        # Unblock pause so the loop can exit
        if token in _audio_pause_events:
            _audio_pause_events[token].set()

    @rx.event(background=True)
    async def analyze_audio(self):
        token = self.router.session.client_token

        # Set up cancel/pause events
        _audio_cancel_events[token] = Event()
        pause_event = asyncio.Event()
        pause_event.set()  # Start unpaused
        _audio_pause_events[token] = pause_event

        async with self:
            self.is_analyzing_audio = True
            self.audio_analysis_paused = False
            self.audio_analysis_progress = 0
            self.audio_analysis_message = "Starting audio analysis..."
            self.audio_analysis_eta = ""

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            try:
                from plexmix.audio.analyzer import EssentiaAnalyzer
            except ImportError:
                async with self:
                    self.audio_analysis_message = (
                        "Essentia is not installed. Run: poetry install -E audio"
                    )
                    self.is_analyzing_audio = False
                return

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            duration_limit = settings.audio.duration_limit

            if not db_path.exists():
                async with self:
                    self.audio_analysis_message = "Database not found"
                    self.is_analyzing_audio = False
                return

            loop = asyncio.get_running_loop()
            analyzer = EssentiaAnalyzer()
            cancelled = False

            with SQLiteManager(str(db_path)) as db:
                pending_tracks = db.get_tracks_without_audio_features()

                if not pending_tracks:
                    async with self:
                        self.audio_analysis_message = "All tracks already have audio features."
                        self.is_analyzing_audio = False
                    return

                total = len(pending_tracks)
                analyzed = 0
                eta_base_time = time.monotonic()
                eta_base_count = 0

                for track in pending_tracks:
                    # Check cancellation
                    if _audio_cancel_events.get(token, Event()).is_set():
                        cancelled = True
                        break

                    # Wait while paused (non-blocking check via asyncio)
                    pe = _audio_pause_events.get(token)
                    if pe and not pe.is_set():
                        async with self:
                            self.audio_analysis_message = f"Paused — {analyzed}/{total} tracks analyzed"
                        await pe.wait()
                        # Re-check cancel after resume
                        if _audio_cancel_events.get(token, Event()).is_set():
                            cancelled = True
                            break
                        # Reset ETA baseline after unpause
                        eta_base_time = time.monotonic()
                        eta_base_count = analyzed

                    def analyze_track(t=track):
                        resolved = settings.audio.resolve_path(t.file_path)
                        features = analyzer.analyze(resolved, duration_limit=duration_limit)
                        return features.to_dict()

                    try:
                        features_dict = await loop.run_in_executor(None, analyze_track)
                        db.insert_audio_features(track.id, features_dict)
                        analyzed += 1
                    except Exception as exc:
                        logger.warning("Audio analysis failed for track %s: %s", track.id, exc)

                    # Calculate ETA
                    elapsed = time.monotonic() - eta_base_time
                    since_baseline = analyzed - eta_base_count
                    if since_baseline > 0 and elapsed > 0:
                        rate = since_baseline / elapsed
                        remaining = (total - analyzed) / rate
                        eta_str = _format_eta(remaining)
                    else:
                        eta_str = "calculating..."

                    async with self:
                        self.audio_analysis_progress = int((analyzed / total) * 100) if total else 0
                        self.audio_analysis_message = f"Analyzed {analyzed}/{total} tracks"
                        self.audio_analysis_eta = eta_str

            async with self:
                self.is_analyzing_audio = False
                self.audio_analysis_paused = False
                self.audio_analysis_progress = 100 if not cancelled else self.audio_analysis_progress
                self.audio_analysis_message = ""
                self.audio_analysis_eta = ""
                self.load_library_stats()

            if cancelled:
                yield rx.toast.warning(f"Audio analysis stopped — {analyzed}/{total} tracks analyzed")
            else:
                yield rx.toast.success(f"Audio analysis complete! Analyzed {analyzed} tracks.")

        except Exception as e:
            async with self:
                self.is_analyzing_audio = False
                self.audio_analysis_paused = False
                self.audio_analysis_message = ""
                self.audio_analysis_eta = ""

            yield rx.toast.error(f"Audio analysis failed: {str(e)}")

        finally:
            _audio_cancel_events.pop(token, None)
            _audio_pause_events.pop(token, None)
