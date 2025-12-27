import reflex as rx
import asyncio
import logging
import atexit
from typing import List, Dict, Any, Optional
from threading import Event
from plexmix.ui.states.app_state import AppState

logger = logging.getLogger(__name__)

# Per-client globals for background tasks
_sync_cancel_events: Dict[str, Event] = {}
_search_tasks: Dict[str, asyncio.Task] = {}


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


def _cleanup_all_state() -> None:
    """Clean up all global state on process exit."""
    for token in list(_sync_cancel_events.keys()):
        _sync_cancel_events[token].set()
    _sync_cancel_events.clear()
    for task in list(_search_tasks.values()):
        if not task.done():
            task.cancel()
    _search_tasks.clear()


# Register cleanup on process exit
atexit.register(_cleanup_all_state)


class LibraryState(AppState):
    tracks: List[Dict[str, Any]] = []
    total_filtered_tracks: int = 0
    current_page: int = 1
    page_size: int = 50
    search_query: str = ""
    genre_filter: str = ""
    year_min: Optional[int] = None
    year_max: Optional[int] = None

    is_syncing: bool = False
    sync_progress: int = 0
    sync_message: str = ""
    sync_mode: str = "incremental"
    show_regenerate_confirm: bool = False

    is_embedding: bool = False
    embedding_progress: int = 0
    embedding_message: str = ""

    selected_tracks: List[int] = []
    sort_column: str = "title"
    sort_ascending: bool = True

    def set_sync_mode(self, mode: str):
        self.sync_mode = mode

    def confirm_regenerate_sync(self):
        self.show_regenerate_confirm = True

    def cancel_regenerate_confirm(self):
        self.show_regenerate_confirm = False

    def on_load(self):
        super().on_load()
        self.load_tracks()

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

                self.tracks = db.get_tracks(
                    limit=self.page_size,
                    offset=offset,
                    search=self.search_query if self.search_query else None,
                    genre=self.genre_filter if self.genre_filter else None,
                    year_min=self.year_min,
                    year_max=self.year_max
                )

                self.total_filtered_tracks = db.count_tracks(
                    search=self.search_query if self.search_query else None,
                    genre=self.genre_filter if self.genre_filter else None,
                    year_min=self.year_min,
                    year_max=self.year_max
                )

        except Exception as e:
            print(f"Error loading tracks: {e}")
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

    def set_year_range(self, year_min: Optional[int], year_max: Optional[int]):
        self.year_min = year_min
        self.year_max = year_max
        self.current_page = 1
        self.load_tracks()

    def set_year_min(self, value: str):
        self.year_min = int(value) if value else None
        self.current_page = 1
        self.load_tracks()

    def set_year_max(self, value: str):
        self.year_max = int(value) if value else None
        self.current_page = 1
        self.load_tracks()

    def clear_filters(self):
        self.search_query = ""
        self.genre_filter = ""
        self.year_min = None
        self.year_max = None
        self.current_page = 1
        self.load_tracks()

    def next_page(self):
        total_pages = (self.total_filtered_tracks + self.page_size - 1) // self.page_size
        if self.current_page < total_pages:
            self.current_page += 1
            self.load_tracks()

    def previous_page(self):
        if self.current_page > 1:
            self.current_page -= 1
            self.load_tracks()

    def go_to_page(self, page: int):
        total_pages = (self.total_filtered_tracks + self.page_size - 1) // self.page_size
        if 1 <= page <= total_pages:
            self.current_page = page
            self.load_tracks()

    def toggle_track_selection(self, track_id: int):
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
            plex_client.connect()

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

            try:
                ai_provider = get_ai_provider(
                    provider_name=provider_name,
                    api_key=api_key,
                    model=settings.ai.model,
                    temperature=settings.ai.temperature,
                    local_mode=settings.ai.local_mode,
                    local_endpoint=settings.ai.local_endpoint,
                    local_auth_token=settings.ai.local_auth_token,
                    local_max_output_tokens=settings.ai.local_max_output_tokens,
                )
            except ValueError as exc:
                logger.warning(f"AI provider unavailable: {exc}")
                ai_provider = None
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

            db.close()

            async with self:
                self.is_syncing = False
                self.sync_progress = 100
                self.sync_message = "Sync completed!"
                self.load_tracks()
                self.check_configuration_status()
                self.load_library_stats()

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
                self.sync_message = f"Sync failed: {str(e)}"

            if token in _sync_cancel_events:
                del _sync_cancel_events[token]

    def cancel_sync(self):
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
            from plexmix.config.credentials import get_google_api_key, get_openai_api_key, get_cohere_api_key

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                async with self:
                    self.embedding_message = "Database not found"
                    self.is_embedding = False
                return

            api_key = None
            provider = settings.embedding.default_provider
            if provider == "gemini":
                api_key = get_google_api_key()
            elif provider == "openai":
                api_key = get_openai_api_key()
            elif provider == "cohere":
                api_key = get_cohere_api_key()

            embedding_generator = EmbeddingGenerator(
                provider=provider,
                api_key=api_key,
                model=settings.embedding.model
            )

            db = SQLiteManager(str(db_path))
            db.connect()

            selected_ids = list(self.selected_tracks)
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
                self.embedding_message = "Embeddings generated successfully!"
                self.clear_selection()
                self.load_tracks()
                self.load_library_stats()

        except Exception as e:
            async with self:
                self.is_embedding = False
                self.embedding_message = f"Embedding generation failed: {str(e)}"
