import reflex as rx
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from plexmix.config.constants import EMBEDDING_BATCH_SIZE, LIBRARY_PAGE_SIZE
from plexmix.ui.states.app_state import AppState
from plexmix.ui.job_manager import jobs, task_store


from plexmix.ui.utils.helpers import str_dict as _str_dict, format_eta as _format_eta

logger = logging.getLogger(__name__)


class LibraryState(AppState):
    tracks: list[dict[str, str]] = []
    total_filtered_tracks: int = 0
    current_page: int = 1
    page_size: int = LIBRARY_PAGE_SIZE
    search_query: str = ""
    genre_filter: str = ""
    year_min: str = ""
    year_max: str = ""
    tag_filter: str = ""
    has_audio: bool = False

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

    is_enriching_musicbrainz: bool = False
    musicbrainz_progress: int = 0
    musicbrainz_message: str = ""

    selected_tracks: list[str] = []
    sort_column: str = "title"
    sort_ascending: bool = True

    # Bulk operations
    show_bulk_tag_dialog: bool = False
    bulk_tag_input: str = ""
    show_delete_confirm: bool = False
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
        self.poll_task_progress()  # Recover in-progress tasks from TaskStore
        self.is_page_loading = False

    def poll_task_progress(self):
        """Client-initiated poll: read TaskStore -> update state vars.

        Called by a hidden button clicked via JavaScript setInterval.
        Because this is a regular (non-background) handler, the response
        travels over the active WebSocket -- no risk of pushing to a dead connection.
        """
        # -- Sync --
        sync_entry = task_store.get("sync")
        if sync_entry:
            if sync_entry.status == "running":
                self.is_syncing = True
                self.sync_progress = sync_entry.progress
                self.sync_message = sync_entry.message
            elif sync_entry.status == "completed":
                self.is_syncing = False
                self.sync_progress = 100
                self.sync_message = ""
                self.load_tracks()
                self.check_configuration_status()
                self.load_library_stats()
                msg = sync_entry.message or "Sync completed!"
                task_store.clear("sync")
                return rx.toast.success(msg)
            elif sync_entry.status == "failed":
                self.is_syncing = False
                self.sync_message = ""
                msg = sync_entry.message or "Sync failed"
                task_store.clear("sync")
                return rx.toast.error(f"Sync failed: {msg}")
            elif sync_entry.status == "cancelled":
                self.is_syncing = False
                self.sync_message = ""
                task_store.clear("sync")
                return rx.toast.warning("Sync cancelled")

        # -- Embedding --
        embed_entry = task_store.get("embedding")
        if embed_entry:
            if embed_entry.status == "running":
                self.is_embedding = True
                self.embedding_progress = embed_entry.progress
                self.embedding_message = embed_entry.message
            elif embed_entry.status == "completed":
                self.is_embedding = False
                self.embedding_progress = 100
                self.embedding_message = ""
                self.clear_selection()
                self.load_tracks()
                self.load_library_stats()
                task_store.clear("embedding")
                return rx.toast.success("Embeddings generated successfully!")
            elif embed_entry.status == "failed":
                self.is_embedding = False
                self.embedding_message = ""
                msg = embed_entry.message or "Embedding generation failed"
                task_store.clear("embedding")
                return rx.toast.error(msg)

        # -- MusicBrainz enrichment --
        mb_entry = task_store.get("musicbrainz")
        if mb_entry:
            if mb_entry.status == "running":
                self.is_enriching_musicbrainz = True
                self.musicbrainz_progress = mb_entry.progress
                self.musicbrainz_message = mb_entry.message
            elif mb_entry.status == "completed":
                self.is_enriching_musicbrainz = False
                self.musicbrainz_progress = 100
                self.musicbrainz_message = ""
                self.load_library_stats()
                msg = mb_entry.message or "MusicBrainz enrichment complete!"
                task_store.clear("musicbrainz")
                return rx.toast.success(msg)
            elif mb_entry.status == "failed":
                self.is_enriching_musicbrainz = False
                self.musicbrainz_message = ""
                msg = mb_entry.message or "MusicBrainz enrichment failed"
                task_store.clear("musicbrainz")
                return rx.toast.error(msg)

        # -- Audio analysis --
        audio_entry = task_store.get("audio")
        if audio_entry:
            if audio_entry.status == "running":
                self.is_analyzing_audio = True
                self.audio_analysis_progress = audio_entry.progress
                self.audio_analysis_message = audio_entry.message
                self.audio_analysis_eta = audio_entry.extra.get("eta", "")
                self.audio_analysis_paused = audio_entry.extra.get("paused", False)
            elif audio_entry.status == "completed":
                self.is_analyzing_audio = False
                self.audio_analysis_progress = 100
                self.audio_analysis_message = ""
                self.audio_analysis_eta = ""
                self.audio_analysis_paused = False
                self.load_library_stats()
                msg = audio_entry.extra.get("result_msg", "Audio analysis complete!")
                task_store.clear("audio")
                return rx.toast.success(msg)
            elif audio_entry.status == "cancelled":
                self.is_analyzing_audio = False
                self.audio_analysis_message = ""
                self.audio_analysis_eta = ""
                self.audio_analysis_paused = False
                msg = audio_entry.extra.get("result_msg", "Audio analysis stopped")
                task_store.clear("audio")
                return rx.toast.warning(msg)
            elif audio_entry.status == "failed":
                self.is_analyzing_audio = False
                self.audio_analysis_message = ""
                self.audio_analysis_eta = ""
                self.audio_analysis_paused = False
                msg = audio_entry.message or "Audio analysis failed"
                task_store.clear("audio")
                return rx.toast.error(msg)

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
                    tag=self.tag_filter if self.tag_filter else None,
                    has_audio=self.has_audio,
                    sort_column=self.sort_column,
                    sort_ascending=self.sort_ascending,
                )
                self.tracks = [_str_dict(t) for t in raw_tracks]

                self.total_filtered_tracks = db.count_tracks(
                    search=self.search_query if self.search_query else None,
                    genre=self.genre_filter if self.genre_filter else None,
                    year_min=year_min_int,
                    year_max=year_max_int,
                    tag=self.tag_filter if self.tag_filter else None,
                    has_audio=self.has_audio,
                )

        except Exception as e:
            logger.error("Error loading tracks: %s", e)
            self.tracks = []
            self.total_filtered_tracks = 0

    @rx.event(background=True)
    async def set_search_query(self, query: str):
        token = self.router.session.client_token
        async with self:
            jobs.cancel_task(token, "search")
            self.search_query = query
            self.current_page = 1

        async def debounced_load():
            await asyncio.sleep(0.5)
            async with self:
                self.load_tracks()

        task = asyncio.create_task(debounced_load())
        jobs.register_task(token, "search", task)

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

    def set_tag_filter(self, value: str):
        self.tag_filter = value
        self.current_page = 1
        self.load_tracks()

    def toggle_has_audio(self, checked: bool):
        self.has_audio = checked
        self.current_page = 1
        self.load_tracks()

    def clear_filters(self):
        self.search_query = ""
        self.genre_filter = ""
        self.year_min = ""
        self.year_max = ""
        self.tag_filter = ""
        self.has_audio = False
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
        page_ids = {track["id"] for track in self.tracks}
        return page_ids.issubset(set(self.selected_tracks))

    def toggle_select_all(self, checked: bool):
        """Toggle selection of all tracks on the current page, preserving off-page selections."""
        page_ids = {track["id"] for track in self.tracks}
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
        self.selected_tracks = [track["id"] for track in self.tracks]

    def clear_selection(self):
        self.selected_tracks = []

    # ── Bulk tag operations ──────────────────────────────────────────

    def open_bulk_tag_dialog(self):
        self.show_bulk_tag_dialog = True

    def close_bulk_tag_dialog(self):
        self.show_bulk_tag_dialog = False
        self.bulk_tag_input = ""

    def set_bulk_tag_dialog_open(self, is_open: bool):
        if not is_open:
            self.close_bulk_tag_dialog()

    def set_bulk_tag_input(self, value: str):
        self.bulk_tag_input = value

    @rx.event(background=True)
    async def apply_bulk_tags(self):
        async with self:
            tags_text = self.bulk_tag_input.strip()
            track_ids = list(self.selected_tracks)
            self.show_bulk_tag_dialog = False
            self.bulk_tag_input = ""

        if not tags_text or not track_ids:
            return

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            with SQLiteManager(str(db_path)) as db:
                for tid_str in track_ids:
                    db.update_track_tags(int(tid_str), tags=tags_text)

            async with self:
                self.selected_tracks = []
            self.load_tracks()

            yield rx.toast.success(f"Applied tags to {len(track_ids)} tracks")

        except Exception as e:
            yield rx.toast.error(f"Error applying tags: {e}")

    # ── Bulk delete operations ───────────────────────────────────────

    def open_delete_confirm(self):
        self.show_delete_confirm = True

    def close_delete_confirm(self):
        self.show_delete_confirm = False

    def set_delete_confirm_open(self, is_open: bool):
        if not is_open:
            self.show_delete_confirm = False

    @rx.event(background=True)
    async def delete_selected_tracks(self):
        async with self:
            track_ids = list(self.selected_tracks)
            self.show_delete_confirm = False

        if not track_ids:
            return

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            with SQLiteManager(str(db_path)) as db:
                for tid_str in track_ids:
                    db.delete_track(int(tid_str))

            async with self:
                self.selected_tracks = []
            self.load_tracks()

            yield rx.toast.success(f"Deleted {len(track_ids)} tracks")

        except Exception as e:
            yield rx.toast.error(f"Error deleting tracks: {e}")

    @rx.event(background=True)
    async def start_sync(self):
        async with self:
            self.is_syncing = True
            self.sync_progress = 0
            self.sync_message = "Starting sync..."
            self.show_regenerate_confirm = False
            sync_mode = self.sync_mode

        cancel_event = task_store.start("sync", message="Starting sync...")
        if cancel_event is None:
            # Already running — polling will show existing task's progress
            return

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.services.sync_service import connect_plex, PlexConnectionError

            settings = Settings.load_from_file()

            try:
                plex_client = connect_plex(settings)
            except PlexConnectionError as e:
                task_store.complete("sync", status="failed", message=str(e))
                return

            if not settings.plex.library_name:
                task_store.complete("sync", status="failed", message="Music library not configured")
                return

            db_path = settings.database.get_db_path()
            db = SQLiteManager(str(db_path))
            db.connect()

            from plexmix.plex.sync import SyncEngine
            from plexmix.services.providers import build_ai_provider

            def progress_callback(progress: float, message: str) -> None:
                if cancel_event.is_set():
                    return
                task_store.update("sync", progress=int(progress * 100), message=message)

            ai_provider = build_ai_provider(settings, silent=True)
            if ai_provider is None:
                task_store.update(
                    "sync", message="AI provider not configured — syncing without tagging..."
                )

            # Pass MusicBrainz settings if enabled and enrichment on sync is on
            mb_settings = None
            if settings.musicbrainz.enabled and settings.musicbrainz.enrich_on_sync:
                mb_settings = settings.musicbrainz

            sync_engine = SyncEngine(
                plex_client,
                db,
                ai_provider=ai_provider,
                musicbrainz_settings=mb_settings,
            )

            loop = asyncio.get_running_loop()

            def run_sync():
                if sync_mode == "regenerate":
                    sync_engine.regenerate_sync(
                        generate_embeddings=False,
                        progress_callback=progress_callback,
                        cancel_event=cancel_event,
                    )
                else:
                    sync_engine.incremental_sync(
                        generate_embeddings=False,
                        progress_callback=progress_callback,
                        cancel_event=cancel_event,
                    )

            await loop.run_in_executor(None, run_sync)

            # Run audio analysis if enabled
            run_audio = settings.audio.analyze_on_sync
            if run_audio and not cancel_event.is_set():
                task_store.update("sync", message="Running audio analysis...")
                try:
                    from plexmix.audio.analyzer import EssentiaAnalyzer

                    analyzer = EssentiaAnalyzer()
                    pending_tracks = [
                        t for t in db.get_tracks_without_audio_features() if t.file_path
                    ]
                    if pending_tracks:
                        loop = asyncio.get_running_loop()
                        duration_limit = settings.audio.duration_limit
                        num_workers = max(1, settings.audio.workers)
                        executor = ThreadPoolExecutor(max_workers=num_workers)
                        analyzed = 0
                        total = len(pending_tracks)
                        track_iter = iter(pending_tracks)
                        in_flight: dict[asyncio.Future, object] = {}

                        def _submit():
                            t = next(track_iter, None)
                            if t is None:
                                return

                            def do_analyze(trk=t):
                                resolved = settings.audio.resolve_path(trk.file_path)
                                return trk, analyzer.analyze(
                                    resolved, duration_limit=duration_limit
                                )

                            in_flight[loop.run_in_executor(executor, do_analyze)] = t

                        for _ in range(min(num_workers, total)):
                            _submit()

                        while in_flight:
                            if cancel_event.is_set():
                                for fut in in_flight:
                                    fut.cancel()
                                break
                            done, _ = await asyncio.wait(
                                in_flight.keys(), return_when=asyncio.FIRST_COMPLETED
                            )
                            for fut in done:
                                in_flight.pop(fut)
                                try:
                                    track_obj, features = fut.result()
                                    db.insert_audio_features(track_obj.id, features)
                                    analyzed += 1
                                except Exception as e:
                                    logger.warning(f"Audio analysis failed: {e}")
                                _submit()
                            if not cancel_event.is_set():
                                task_store.update(
                                    "sync",
                                    progress=int((analyzed / total) * 100) if total else 0,
                                    message=f"Audio analysis: {analyzed}/{total} tracks ({num_workers} workers)",
                                )

                        executor.shutdown(wait=False)
                except ImportError:
                    logger.warning("Essentia not installed, skipping audio analysis")
                except Exception as e:
                    logger.warning(f"Audio analysis error: {e}")

            db.close()

            if cancel_event.is_set():
                task_store.complete("sync", status="cancelled")
            elif ai_provider is None:
                task_store.complete("sync", message="Sync completed (tagging skipped)")
            else:
                task_store.complete("sync")

        except Exception as e:
            task_store.complete("sync", status="failed", message=str(e))

    def request_cancel_sync(self):
        self.show_cancel_confirm = True

    def dismiss_cancel_confirm(self, open: bool = False):
        if not open:
            self.show_cancel_confirm = False

    def cancel_sync(self):
        self.show_cancel_confirm = False
        task_store.cancel("sync")

    @rx.event(background=True)
    async def generate_embeddings(self):
        async with self:
            if not self.selected_tracks:
                return
            self.is_embedding = True
            self.embedding_progress = 0
            self.embedding_message = "Starting embedding generation..."

        cancel_event = task_store.start("embedding", message="Starting embedding generation...")
        if cancel_event is None:
            return

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.services.providers import build_embedding_generator
            from plexmix.services.tagging_service import generate_embeddings_for_tracks

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                task_store.complete("embedding", status="failed", message="Database not found")
                return

            embedding_generator = build_embedding_generator(settings)
            if not embedding_generator:
                task_store.complete(
                    "embedding", status="failed", message="Embedding provider not configured"
                )
                return

            db = SQLiteManager(str(db_path))
            db.connect()

            tracks = []
            for tid_str in self.selected_tracks:
                track = db.get_track_by_id(int(tid_str))
                if track:
                    tracks.append(track)

            def on_progress(generated: int, total: int) -> None:
                if cancel_event.is_set():
                    return
                task_store.update(
                    "embedding",
                    progress=int((generated / total) * 100) if total else 0,
                    message=f"Generated {generated}/{total} embeddings",
                )

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: generate_embeddings_for_tracks(
                    db,
                    embedding_generator,
                    tracks,
                    batch_size=EMBEDDING_BATCH_SIZE,
                    progress_callback=on_progress,
                ),
            )

            db.close()

            task_store.complete("embedding")

        except Exception as e:
            task_store.complete("embedding", status="failed", message=str(e))

    @rx.event(background=True)
    async def enrich_musicbrainz(self):
        async with self:
            self.is_enriching_musicbrainz = True
            self.musicbrainz_progress = 0
            self.musicbrainz_message = "Starting MusicBrainz enrichment..."

        cancel_event = task_store.start("musicbrainz", message="Starting MusicBrainz enrichment...")
        if cancel_event is None:
            return

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            try:
                from plexmix.services.musicbrainz_service import (
                    get_enrichable_tracks,
                    enrich_tracks,
                )
            except ImportError:
                task_store.complete(
                    "musicbrainz",
                    status="failed",
                    message="musicbrainzngs is not installed. Run: pip install musicbrainzngs",
                )
                return

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                task_store.complete("musicbrainz", status="failed", message="Database not found")
                return

            def run_enrichment():
                """Run enrichment in executor thread with its own DB connection."""
                with SQLiteManager(str(db_path)) as db:
                    tracks = get_enrichable_tracks(db)

                    if not tracks:
                        return 0, 0, 0, False  # enriched, cached, errors, had_tracks

                    def on_progress(
                        enriched: int, cached: int, mb_errors: int, total_count: int
                    ) -> None:
                        if cancel_event.is_set():
                            return
                        processed = enriched + cached + mb_errors
                        task_store.update(
                            "musicbrainz",
                            progress=int((processed / total_count) * 100) if total_count else 0,
                            message=f"Enriched {enriched}/{total_count} tracks ({cached} cached)",
                        )

                    e, c, err = enrich_tracks(
                        db,
                        settings.musicbrainz,
                        tracks,
                        progress_callback=on_progress,
                        cancel_event=cancel_event,
                    )
                    return e, c, err, True

            loop = asyncio.get_running_loop()
            enriched, cached, errors, had_tracks = await loop.run_in_executor(
                None,
                run_enrichment,
            )

            if not had_tracks:
                task_store.complete(
                    "musicbrainz",
                    message="All tracks already have MusicBrainz metadata.",
                )
                return

            if cancel_event.is_set():
                task_store.complete("musicbrainz", status="cancelled")
            else:
                task_store.complete(
                    "musicbrainz",
                    message=f"Enriched {enriched} tracks, {cached} cached ({errors} errors)",
                )

        except Exception as e:
            task_store.complete("musicbrainz", status="failed", message=str(e))

    def pause_audio_analysis(self):
        task_store.pause("audio")
        self.audio_analysis_paused = True

    def resume_audio_analysis(self):
        task_store.resume("audio")
        self.audio_analysis_paused = False

    def request_cancel_audio(self):
        self.show_audio_cancel_confirm = True

    def dismiss_audio_cancel_confirm(self, open: bool = False):
        if not open:
            self.show_audio_cancel_confirm = False

    def cancel_audio_analysis(self):
        self.show_audio_cancel_confirm = False
        task_store.cancel("audio")
        # Unblock pause so the loop can exit
        task_store.resume("audio")

    @rx.event(background=True)
    async def analyze_audio(self):
        async with self:
            self.is_analyzing_audio = True
            self.audio_analysis_paused = False
            self.audio_analysis_progress = 0
            self.audio_analysis_message = "Starting audio analysis..."
            self.audio_analysis_eta = ""

        cancel_event = task_store.start("audio", message="Starting audio analysis...")
        if cancel_event is None:
            return

        pause_event = task_store.get_pause_event("audio")

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            try:
                from plexmix.audio.analyzer import EssentiaAnalyzer
            except ImportError:
                task_store.complete(
                    "audio",
                    status="failed",
                    message="Essentia is not installed. Run: poetry install -E audio",
                )
                return

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            duration_limit = settings.audio.duration_limit
            num_workers = max(1, settings.audio.workers)

            if not db_path.exists():
                task_store.complete("audio", status="failed", message="Database not found")
                return

            loop = asyncio.get_running_loop()
            analyzer = EssentiaAnalyzer()
            cancelled = False

            with SQLiteManager(str(db_path)) as db:
                pending_tracks = [t for t in db.get_tracks_without_audio_features() if t.file_path]

                if not pending_tracks:
                    task_store.complete(
                        "audio",
                        message="All tracks already have audio features.",
                    )
                    return

                total = len(pending_tracks)
                analyzed = 0
                eta_base_time = time.monotonic()
                eta_base_count = 0

                executor = ThreadPoolExecutor(max_workers=num_workers)
                track_iter = iter(pending_tracks)

                # Sliding window: keep up to num_workers tasks in flight
                in_flight: dict[asyncio.Future, object] = {}

                def _submit_next() -> bool:
                    """Submit the next track. Returns True if one was submitted."""
                    track = next(track_iter, None)
                    if track is None:
                        return False

                    def do_analyze(t=track):
                        resolved = settings.audio.resolve_path(t.file_path)
                        return (
                            t,
                            analyzer.analyze(resolved, duration_limit=duration_limit).to_dict(),
                        )

                    fut = loop.run_in_executor(executor, do_analyze)
                    in_flight[fut] = track
                    return True

                # Fill initial window
                for _ in range(min(num_workers, total)):
                    _submit_next()

                while in_flight:
                    # Check cancellation
                    if cancel_event.is_set():
                        cancelled = True
                        for fut in in_flight:
                            fut.cancel()
                        break

                    # Check pause — let in-flight work finish, then hold
                    if task_store.is_paused("audio"):
                        task_store.update(
                            "audio",
                            message=f"Paused — {analyzed}/{total} tracks analyzed",
                            extra={"paused": True},
                        )
                        await pause_event.wait()
                        if cancel_event.is_set():
                            cancelled = True
                            for fut in in_flight:
                                fut.cancel()
                            break
                        eta_base_time = time.monotonic()
                        eta_base_count = analyzed

                    # Wait for at least one to complete
                    done, _ = await asyncio.wait(
                        in_flight.keys(), return_when=asyncio.FIRST_COMPLETED
                    )

                    for fut in done:
                        in_flight.pop(fut)
                        try:
                            track_obj, features_dict = fut.result()
                            db.insert_audio_features(track_obj.id, features_dict)
                            analyzed += 1
                        except Exception as exc:
                            logger.warning("Audio analysis failed: %s", exc)

                        # Submit a replacement
                        _submit_next()

                    # Update progress & ETA
                    if not cancel_event.is_set():
                        elapsed = time.monotonic() - eta_base_time
                        since_baseline = analyzed - eta_base_count
                        if since_baseline > 0 and elapsed > 0:
                            rate = since_baseline / elapsed
                            remaining = (total - analyzed) / rate
                            eta_str = _format_eta(remaining)
                        else:
                            eta_str = "calculating..."

                        task_store.update(
                            "audio",
                            progress=int((analyzed / total) * 100) if total else 0,
                            message=f"Analyzed {analyzed}/{total} tracks ({num_workers} workers)",
                            extra={"eta": eta_str, "paused": False},
                        )

                executor.shutdown(wait=False)

            if cancelled:
                task_store.update(
                    "audio",
                    extra={
                        "result_msg": f"Audio analysis stopped — {analyzed}/{total} tracks analyzed"
                    },
                )
                task_store.complete("audio", status="cancelled")
            else:
                task_store.update(
                    "audio",
                    extra={"result_msg": f"Audio analysis complete! Analyzed {analyzed} tracks."},
                )
                task_store.complete("audio")

        except Exception as e:
            task_store.complete("audio", status="failed", message=str(e))
