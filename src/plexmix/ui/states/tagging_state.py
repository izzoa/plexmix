import logging
import reflex as rx
import asyncio
import time
from plexmix.config.constants import TAG_BATCH_SIZE
from plexmix.ui.states.app_state import AppState
from plexmix.ui.job_manager import task_store


from plexmix.ui.utils.helpers import str_dict as _str_dict


logger = logging.getLogger(__name__)


class TaggingState(AppState):
    # Filter criteria
    genre_filter: str = ""
    year_min: str = ""
    year_max: str = ""
    artist_filter: str = ""
    has_no_tags: bool = False
    stale_days: str = ""

    # Preview and progress
    preview_count: int = 0
    is_tagging: bool = False
    tagging_progress: int = 0
    current_batch: int = 0
    total_batches: int = 0
    tags_generated_count: int = 0
    estimated_time_remaining: int = 0
    tagging_message: str = ""

    # Stale tag stats
    stale_track_count: int = 0

    # Confirmation dialog
    show_tag_all_confirm: bool = False
    untagged_track_count: int = 0

    # Recently tagged tracks
    recently_tagged_tracks: list[dict[str, str]] = []

    # For inline editing
    editing_track_id: str = ""
    edit_tags: str = ""
    edit_environments: str = ""
    edit_instruments: str = ""

    def on_load(self):
        if not self.check_auth():
            self.is_page_loading = False
            return
        super().on_load()
        self.load_recently_tagged()
        self._load_stale_count()
        self.poll_task_progress()  # Recover in-progress tagging from TaskStore
        self.is_page_loading = False

    def poll_task_progress(self):
        """Client-initiated poll: read TaskStore -> update state vars."""
        entry = task_store.get("tagging")
        if entry is None:
            return
        if entry.status == "running":
            self.is_tagging = True
            self.tagging_progress = entry.progress
            self.tagging_message = entry.message
            self.current_batch = int(entry.extra.get("current_batch", 0))
            self.total_batches = int(entry.extra.get("total_batches", 0))
            self.tags_generated_count = int(entry.extra.get("tags_generated_count", 0))
            self.estimated_time_remaining = int(entry.extra.get("estimated_time_remaining", 0))
        elif entry.status == "completed":
            self.is_tagging = False
            self.tagging_progress = 100
            self.tagging_message = ""
            self.load_recently_tagged()
            count = entry.extra.get("tags_generated_count", 0)
            task_store.clear("tagging")
            return rx.toast.success(f"Successfully tagged {count} tracks!")
        elif entry.status == "failed":
            self.is_tagging = False
            self.tagging_message = ""
            msg = entry.message or "Tagging failed"
            task_store.clear("tagging")
            return rx.toast.error(f"Error during tagging: {msg}")
        elif entry.status == "cancelled":
            self.is_tagging = False
            self.tagging_message = ""
            task_store.clear("tagging")
            return rx.toast.warning("Tagging cancelled")

    def _load_stale_count(self):
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            if db_path.exists():
                with SQLiteManager(str(db_path)) as db:
                    self.stale_track_count = db.count_stale_tagged_tracks(30)
        except Exception as e:
            logger.error("Error loading stale track count: %s", e)

    def load_recently_tagged(self):
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if db_path.exists():
                db = SQLiteManager(str(db_path))
                db.connect()
                raw_tracks = db.get_recently_tagged_tracks(limit=100)
                self.recently_tagged_tracks = [_str_dict(t) for t in raw_tracks]
                db.close()
        except Exception as e:
            logger.error("Error loading recently tagged tracks: %s", e)

    def set_genre_filter(self, value: str):
        self.genre_filter = value

    def set_year_range(self, year_min: str, year_max: str):
        self.year_min = year_min
        self.year_max = year_max

    def set_year_min(self, value: str):
        self.year_min = value

    def set_year_max(self, value: str):
        self.year_max = value

    def set_artist_filter(self, value: str):
        self.artist_filter = value

    def toggle_has_no_tags(self):
        self.has_no_tags = not self.has_no_tags

    def set_stale_days(self, value: str):
        self.stale_days = value

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

            year_min_int = int(self.year_min) if self.year_min else None
            year_max_int = int(self.year_max) if self.year_max else None
            stale_days_int = int(self.stale_days) if self.stale_days else None

            # Get matching tracks
            tracks = db.get_tracks_by_filter(
                genre=self.genre_filter if self.genre_filter else None,
                year_min=year_min_int,
                year_max=year_max_int,
                artist=self.artist_filter if self.artist_filter else None,
                has_no_tags=self.has_no_tags,
                stale_days=stale_days_int,
            )

            # Also count stale tracks for the UI badge
            stale_count = db.count_stale_tagged_tracks(30)

            db.close()

            async with self:
                self.preview_count = len(tracks)
                self.stale_track_count = stale_count
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

        cancel_event = task_store.start("tagging", message="Starting tag generation...")
        if cancel_event is None:
            # Already running — polling will show existing task's progress
            return

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.ai.tag_generator import TagGenerator

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                task_store.complete("tagging", status="failed", message="Database not found.")
                return

            db = SQLiteManager(str(db_path))
            db.connect()

            year_min_int = int(self.year_min) if self.year_min else None
            year_max_int = int(self.year_max) if self.year_max else None
            stale_days_int = int(self.stale_days) if self.stale_days else None

            # Get tracks to tag
            tracks = db.get_tracks_by_filter(
                genre=self.genre_filter if self.genre_filter else None,
                year_min=year_min_int,
                year_max=year_max_int,
                artist=self.artist_filter if self.artist_filter else None,
                has_no_tags=self.has_no_tags,
                stale_days=stale_days_int,
            )

            if not tracks:
                task_store.complete(
                    "tagging", status="failed", message="No tracks found matching filters."
                )
                db.close()
                return

            # Set up AI provider
            from plexmix.services.providers import build_ai_provider

            ai_provider = build_ai_provider(settings, silent=True)
            if ai_provider is None:
                task_store.complete(
                    "tagging",
                    status="failed",
                    message="Error initializing AI provider — check settings.",
                )
                db.close()
                return

            tag_generator = TagGenerator(ai_provider)

            # Calculate batches
            batch_size = TAG_BATCH_SIZE
            total_batches = (len(tracks) + batch_size - 1) // batch_size

            task_store.update(
                "tagging",
                message=f"Tagging {len(tracks)} tracks in {total_batches} batches...",
                extra={"total_batches": total_batches},
            )

            # Progress callback
            start_time = time.time()

            def progress_callback(batch_num: int, total: int, tracks_tagged: int):
                if cancel_event.is_set():
                    return
                elapsed = time.time() - start_time
                if tracks_tagged > 0:
                    time_per_track = elapsed / tracks_tagged
                    remaining_tracks = len(tracks) - tracks_tagged
                    estimated_remaining = int(time_per_track * remaining_tracks)
                else:
                    estimated_remaining = 0

                task_store.update(
                    "tagging",
                    progress=int((tracks_tagged / len(tracks) * 100)) if tracks else 0,
                    message=f"Processing batch {batch_num}/{total} - {tracks_tagged} tracks tagged",
                    extra={
                        "current_batch": batch_num,
                        "total_batches": total,
                        "tags_generated_count": tracks_tagged,
                        "estimated_time_remaining": estimated_remaining,
                    },
                )

            # Generate tags
            results = tag_generator.generate_tags_batch(
                tracks,
                batch_size=batch_size,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
            )

            # Save tags to database
            saved_count = 0
            for track_id, tag_data in results.items():
                if tag_data["tags"] or tag_data["environments"] or tag_data["instruments"]:
                    db.update_track_tags(
                        track_id,
                        tags=",".join(tag_data["tags"]),
                        environments=",".join(tag_data["environments"]),
                        instruments=",".join(tag_data["instruments"]),
                    )
                    saved_count += 1

            db.close()

            task_store.update("tagging", extra={"tags_generated_count": saved_count})
            task_store.complete("tagging")

        except Exception as e:
            task_store.complete("tagging", status="failed", message=str(e))

    def cancel_tagging(self):
        task_store.cancel("tagging")
        self.tagging_message = "Cancelling tagging..."

    def start_edit_tag(self, track: dict[str, str]):
        self.editing_track_id = track["id"]
        self.edit_tags = track.get("tags", "")
        self.edit_environments = track.get("environments", "")
        self.edit_instruments = track.get("instruments", "")

    def cancel_edit(self):
        self.editing_track_id = ""
        self.edit_tags = ""
        self.edit_environments = ""
        self.edit_instruments = ""

    def set_edit_tags(self, value: str):
        self.edit_tags = value

    def set_edit_environments(self, value: str):
        self.edit_environments = value

    def set_edit_instruments(self, value: str):
        self.edit_instruments = value

    @rx.event(background=True)
    async def save_tag_edit(self):
        async with self:
            if not self.editing_track_id:
                return

            track_id = int(self.editing_track_id)

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
                instruments=self.edit_instruments,
            )

            db.close()

            # Reload recently tagged tracks
            self.load_recently_tagged()

            async with self:
                self.editing_track_id = ""
                self.edit_tags = ""
                self.edit_environments = ""
                self.edit_instruments = ""

            yield rx.toast.success("Tags updated successfully!")

        except Exception as e:
            yield rx.toast.error(f"Error saving tags: {str(e)}")

    def show_tag_all_confirmation(self):
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if db_path.exists():
                with SQLiteManager(str(db_path)) as db:
                    self.untagged_track_count = db.count_untagged_tracks()
            else:
                self.untagged_track_count = 0
        except Exception as e:
            logger.error("Error counting untagged tracks: %s", e)
            self.untagged_track_count = 0

        self.show_tag_all_confirm = True

    def cancel_tag_all_confirm(self):
        self.show_tag_all_confirm = False

    def set_tag_all_confirm_open(self, is_open: bool):
        """Handle dialog open/close via on_open_change."""
        if not is_open:
            self.show_tag_all_confirm = False

    @rx.event(background=True)
    async def tag_all_untagged(self):
        async with self:
            self.show_tag_all_confirm = False
            self.genre_filter = ""
            self.year_min = ""
            self.year_max = ""
            self.artist_filter = ""
            self.has_no_tags = True

        await self.preview_selection()

        # Give UI time to update
        await asyncio.sleep(0.5)

        if self.preview_count > 0:
            await self.start_tagging()
