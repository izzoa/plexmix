import reflex as rx
import asyncio
import threading
import time
from typing import List, Dict, Any, Optional
from plexmix.ui.states.app_state import AppState


class TaggingState(AppState):
    # Filter criteria
    genre_filter: str = ""
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    artist_filter: str = ""
    has_no_tags: bool = False

    # Preview and progress
    preview_count: int = 0
    is_tagging: bool = False
    tagging_progress: int = 0
    current_batch: int = 0
    total_batches: int = 0
    tags_generated_count: int = 0
    estimated_time_remaining: int = 0
    tagging_message: str = ""

    # Recently tagged tracks
    recently_tagged_tracks: List[Dict[str, Any]] = []

    # For inline editing
    editing_track_id: Optional[int] = None
    edit_tags: str = ""
    edit_environments: str = ""
    edit_instruments: str = ""

    # Cancel event
    _cancel_event: Optional[threading.Event] = None

    def on_load(self):
        super().on_load()
        self.load_recently_tagged()

    def load_recently_tagged(self):
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if db_path.exists():
                db = SQLiteManager(str(db_path))
                db.connect()
                self.recently_tagged_tracks = db.get_recently_tagged_tracks(limit=100)
                db.close()
        except Exception as e:
            print(f"Error loading recently tagged tracks: {e}")

    def set_genre_filter(self, value: str):
        self.genre_filter = value

    def set_year_range(self, year_min: Optional[int], year_max: Optional[int]):
        self.year_min = year_min
        self.year_max = year_max

    def set_year_min(self, value: str):
        self.year_min = int(value) if value else None

    def set_year_max(self, value: str):
        self.year_max = int(value) if value else None

    def set_artist_filter(self, value: str):
        self.artist_filter = value

    def toggle_has_no_tags(self):
        self.has_no_tags = not self.has_no_tags

    @rx.event(background=True)
    async def preview_selection(self):
        async with self:
            self.preview_count = 0
            self.tagging_message = "Counting matching tracks..."

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                async with self:
                    self.tagging_message = "Database not found. Please sync your library first."
                return

            db = SQLiteManager(str(db_path))
            db.connect()

            # Get matching tracks
            tracks = db.get_tracks_by_filter(
                genre=self.genre_filter if self.genre_filter else None,
                year_min=self.year_min,
                year_max=self.year_max,
                artist=self.artist_filter if self.artist_filter else None,
                has_no_tags=self.has_no_tags
            )

            db.close()

            async with self:
                self.preview_count = len(tracks)
                self.tagging_message = f"{len(tracks)} tracks match your filters"

        except Exception as e:
            async with self:
                self.tagging_message = f"Error: {str(e)}"

    @rx.event(background=True)
    async def start_tagging(self):
        async with self:
            if self.preview_count == 0:
                self.tagging_message = "No tracks to tag. Preview selection first."
                return

            self.is_tagging = True
            self.tagging_progress = 0
            self.current_batch = 0
            self.tags_generated_count = 0
            self.tagging_message = "Starting tag generation..."
            self._cancel_event = threading.Event()

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.ai.tag_generator import TagGenerator

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                async with self:
                    self.tagging_message = "Database not found."
                    self.is_tagging = False
                return

            db = SQLiteManager(str(db_path))
            db.connect()

            # Get tracks to tag
            tracks = db.get_tracks_by_filter(
                genre=self.genre_filter if self.genre_filter else None,
                year_min=self.year_min,
                year_max=self.year_max,
                artist=self.artist_filter if self.artist_filter else None,
                has_no_tags=self.has_no_tags
            )

            if not tracks:
                async with self:
                    self.tagging_message = "No tracks found matching filters."
                    self.is_tagging = False
                db.close()
                return

            # Set up AI provider
            from plexmix.ai import get_ai_provider
            from plexmix.config.credentials import get_google_api_key, get_openai_api_key, get_anthropic_api_key, get_cohere_api_key
            
            ai_provider_name = settings.ai.default_provider or "gemini"
            api_key = None

            provider_alias = "claude" if ai_provider_name == "anthropic" else ai_provider_name

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
                    provider_name=ai_provider_name,
                    api_key=api_key,
                    model=settings.ai.model,
                    temperature=settings.ai.temperature,
                    local_mode=settings.ai.local_mode,
                    local_endpoint=settings.ai.local_endpoint,
                    local_auth_token=settings.ai.local_auth_token,
                    local_max_output_tokens=settings.ai.local_max_output_tokens,
                )
            except Exception as e:
                async with self:
                    self.tagging_message = f"Error initializing AI provider: {str(e)}"
                    self.is_tagging = False
                db.close()
                return

            tag_generator = TagGenerator(ai_provider)

            # Calculate batches
            batch_size = 20
            total_batches = (len(tracks) + batch_size - 1) // batch_size

            async with self:
                self.total_batches = total_batches
                self.tagging_message = f"Tagging {len(tracks)} tracks in {total_batches} batches..."

            # Progress callback
            start_time = time.time()

            def progress_callback(batch_num: int, total: int, tracks_tagged: int):
                elapsed = time.time() - start_time
                if tracks_tagged > 0:
                    time_per_track = elapsed / tracks_tagged
                    remaining_tracks = len(tracks) - tracks_tagged
                    estimated_remaining = int(time_per_track * remaining_tracks)
                else:
                    estimated_remaining = 0

                async def update_progress():
                    async with self:
                        self.current_batch = batch_num
                        self.tags_generated_count = tracks_tagged
                        self.tagging_progress = int((tracks_tagged / len(tracks) * 100)) if tracks else 0
                        self.estimated_time_remaining = estimated_remaining
                        self.tagging_message = f"Processing batch {batch_num}/{total} - {tracks_tagged} tracks tagged"

                asyncio.create_task(update_progress())

            # Generate tags
            results = tag_generator.generate_tags_batch(
                tracks,
                batch_size=batch_size,
                progress_callback=progress_callback,
                cancel_event=self._cancel_event
            )

            # Save tags to database
            for track_id, tag_data in results.items():
                if tag_data['tags'] or tag_data['environments'] or tag_data['instruments']:
                    db.update_track_tags(
                        track_id,
                        tags=','.join(tag_data['tags']),
                        environments=','.join(tag_data['environments']),
                        instruments=','.join(tag_data['instruments'])
                    )

            db.close()

            # Reload recently tagged tracks
            self.load_recently_tagged()

            async with self:
                self.is_tagging = False
                self.tagging_progress = 100
                self.tagging_message = f"Successfully tagged {self.tags_generated_count} tracks!"

        except Exception as e:
            async with self:
                self.is_tagging = False
                self.tagging_message = f"Error during tagging: {str(e)}"

    def cancel_tagging(self):
        if self._cancel_event:
            self._cancel_event.set()
            self.tagging_message = "Cancelling tagging..."

    def start_edit_tag(self, track: Dict[str, Any]):
        self.editing_track_id = track['id']
        self.edit_tags = track.get('tags', '')
        self.edit_environments = track.get('environments', '')
        self.edit_instruments = track.get('instruments', '')

    def cancel_edit(self):
        self.editing_track_id = None
        self.edit_tags = ""
        self.edit_environments = ""
        self.edit_instruments = ""

    @rx.event(background=True)
    async def save_tag_edit(self):
        async with self:
            if self.editing_track_id is None:
                return

            track_id = self.editing_track_id

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            db = SQLiteManager(str(db_path))
            db.connect()

            db.update_track_tags(
                track_id,
                tags=self.edit_tags,
                environments=self.edit_environments,
                instruments=self.edit_instruments
            )

            db.close()

            # Reload recently tagged tracks
            self.load_recently_tagged()

            async with self:
                self.editing_track_id = None
                self.edit_tags = ""
                self.edit_environments = ""
                self.edit_instruments = ""
                self.tagging_message = "Tags updated successfully!"

        except Exception as e:
            async with self:
                self.tagging_message = f"Error saving tags: {str(e)}"

    @rx.event(background=True)
    async def tag_all_untagged(self):
        async with self:
            self.genre_filter = ""
            self.year_min = None
            self.year_max = None
            self.artist_filter = ""
            self.has_no_tags = True

        await self.preview_selection()

        # Give UI time to update
        await asyncio.sleep(0.5)

        if self.preview_count > 0:
            await self.start_tagging()
