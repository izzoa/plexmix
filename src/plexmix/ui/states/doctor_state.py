import reflex as rx
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any
from pathlib import Path
from plexmix.ui.states.app_state import AppState
from plexmix.ui.job_manager import task_store

logger = logging.getLogger(__name__)


class DoctorState(AppState):
    doctor_total_tracks: int = 0
    doctor_tracks_with_embeddings: int = 0
    doctor_orphaned_embeddings: int = 0
    doctor_untagged_tracks: int = 0
    doctor_tracks_needing_embeddings: int = 0
    doctor_tracks_without_audio: int = 0
    doctor_audio_features_count: int = 0
    doctor_tracks_without_musicbrainz: int = 0
    doctor_musicbrainz_enriched: int = 0

    is_healthy: bool = False
    is_checking: bool = False
    check_message: str = ""

    is_fixing: bool = False
    fix_message: str = ""
    fix_progress: int = 0
    fix_total: int = 0
    current_fix_target: str = ""

    show_retag_confirm: bool = False

    @rx.event
    def on_load(self):
        if not self.check_auth():
            self.is_page_loading = False
            return
        super().on_load()
        self.poll_task_progress()
        return DoctorState.run_health_check

    def _refresh_health_stats(self) -> None:
        """Synchronous health check -- updates state vars from DB."""
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                return

            with SQLiteManager(str(db_path)) as db:
                cursor = db.get_connection().cursor()

                # Get total tracks
                cursor.execute("SELECT COUNT(*) FROM tracks")
                total_tracks = cursor.fetchone()[0]

                # Get tracks with embeddings
                cursor.execute("SELECT COUNT(DISTINCT track_id) FROM embeddings")
                tracks_with_embeddings = cursor.fetchone()[0]

                # Get orphaned embeddings
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM embeddings
                    WHERE track_id NOT IN (SELECT id FROM tracks)
                """
                )
                orphaned_count = cursor.fetchone()[0]

                # Get untagged tracks
                cursor.execute('SELECT COUNT(*) FROM tracks WHERE tags IS NULL OR tags = ""')
                untagged_count = cursor.fetchone()[0]

                # Get tracks needing embeddings
                cursor.execute(
                    "SELECT COUNT(*) FROM tracks WHERE id NOT IN (SELECT DISTINCT track_id FROM embeddings)"
                )
                tracks_needing_embeddings = cursor.fetchone()[0]

                # Get audio analysis stats
                audio_features_count = db.get_audio_features_count()
                tracks_without_audio = len(db.get_tracks_without_audio_features())

                # Get MusicBrainz enrichment stats
                mb_enriched = db.get_musicbrainz_enrichment_count()
                mb_without = len(db.get_tracks_without_musicbrainz())

                self.doctor_total_tracks = total_tracks
                self.doctor_tracks_with_embeddings = tracks_with_embeddings
                self.doctor_orphaned_embeddings = orphaned_count
                self.doctor_untagged_tracks = untagged_count
                self.doctor_tracks_needing_embeddings = tracks_needing_embeddings
                self.doctor_audio_features_count = audio_features_count
                self.doctor_tracks_without_audio = tracks_without_audio
                self.doctor_musicbrainz_enriched = mb_enriched
                self.doctor_tracks_without_musicbrainz = mb_without

                if orphaned_count == 0 and tracks_needing_embeddings == 0:
                    self.is_healthy = True
                    self.check_message = (
                        "✓ Database is healthy! All tracks have embeddings and no orphaned data."
                    )
                else:
                    self.is_healthy = False
                    issues = []
                    if orphaned_count > 0:
                        issues.append(f"{orphaned_count} orphaned embeddings")
                    if tracks_needing_embeddings > 0:
                        issues.append(f"{tracks_needing_embeddings} tracks need embeddings")
                    self.check_message = f"⚠️ Issues found: {', '.join(issues)}"

        except Exception as e:
            logger.error("Error during health check: %s", e)

    @rx.event(background=True)
    async def run_health_check(self):
        async with self:
            self.is_checking = True
            self.check_message = "Running health check..."

        try:
            from plexmix.config.settings import Settings

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                async with self:
                    self.check_message = "Database not found. Please run a sync first."
                    self.is_checking = False
                    self.is_page_loading = False
                return

            async with self:
                self._refresh_health_stats()
                self.is_checking = False
                self.is_page_loading = False

        except Exception as e:
            async with self:
                self.check_message = f"Error during health check: {str(e)}"
                self.is_checking = False
                self.is_page_loading = False

    @rx.event(background=True)
    async def delete_orphaned_embeddings(self):
        async with self:
            self.is_fixing = True
            self.fix_message = "Deleting orphaned embeddings..."
            self.current_fix_target = "cleanup"

        cancel_event = task_store.start("doctor_fix", message="Deleting orphaned embeddings...")
        if cancel_event is None:
            return  # already running

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            with SQLiteManager(str(db_path)) as db:
                cursor = db.get_connection().cursor()
                cursor.execute(
                    "DELETE FROM embeddings WHERE track_id NOT IN (SELECT id FROM tracks)"
                )
                deleted = cursor.rowcount
                db._commit()

            task_store.update(
                "doctor_fix",
                extra={
                    "fix_target": "cleanup",
                    "result_msg": f"Deleted {deleted} orphaned embeddings",
                },
            )
            task_store.complete("doctor_fix")

        except Exception as e:
            task_store.complete("doctor_fix", status="failed", message=str(e))

    @rx.event(background=True)
    async def generate_missing_embeddings(self):
        async with self:
            self.is_fixing = True
            self.fix_message = "Generating embeddings for missing tracks..."
            self.fix_progress = 0
            self.current_fix_target = "embeddings_incremental"

        cancel_event = task_store.start(
            "doctor_fix", message="Generating embeddings for missing tracks..."
        )
        if cancel_event is None:
            return  # already running

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.database.vector_index import VectorIndex
            from plexmix.services.providers import build_embedding_generator

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            embedding_generator = build_embedding_generator(settings)
            if embedding_generator is None:
                provider = settings.embedding.default_provider
                task_store.complete(
                    "doctor_fix",
                    status="failed",
                    message=f"API key required for {provider} provider",
                )
                return

            index_path = settings.database.get_index_path()
            vector_index = VectorIndex(
                dimension=embedding_generator.get_dimension(), index_path=str(index_path)
            )

            with SQLiteManager(str(db_path)) as db:
                all_tracks = db.get_all_tracks()
                tracks_to_embed = [t for t in all_tracks if not db.get_embedding_by_track_id(t.id)]
                count = await self._generate_embeddings_for_tracks(
                    tracks_to_embed=tracks_to_embed,
                    embedding_generator=embedding_generator,
                    vector_index=vector_index,
                    db=db,
                    index_path=index_path,
                    progress_label="Generating embeddings...",
                    cancel_event=cancel_event,
                )

            result_msg = (
                f"Successfully generated {count} embeddings!"
                if count > 0
                else "All tracks already have embeddings."
            )
            task_store.update(
                "doctor_fix",
                extra={"fix_target": "embeddings_incremental", "result_msg": result_msg},
            )
            task_store.complete("doctor_fix")

        except Exception as e:
            task_store.complete("doctor_fix", status="failed", message=str(e))

    @rx.event(background=True)
    async def regenerate_all_embeddings(self):
        async with self:
            self.is_fixing = True
            self.fix_message = "Regenerating all embeddings..."
            self.fix_progress = 0
            self.current_fix_target = "embeddings_full"

        cancel_event = task_store.start("doctor_fix", message="Regenerating all embeddings...")
        if cancel_event is None:
            return  # already running

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.database.vector_index import VectorIndex
            from plexmix.services.providers import build_embedding_generator

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            embedding_generator = build_embedding_generator(settings)
            if embedding_generator is None:
                provider = settings.embedding.default_provider
                task_store.complete(
                    "doctor_fix",
                    status="failed",
                    message=f"API key required for {provider} provider",
                )
                return

            index_path = settings.database.get_index_path()
            if index_path.exists():
                index_path.unlink()
            metadata_path = index_path.with_suffix(".metadata")
            if metadata_path.exists():
                metadata_path.unlink()

            vector_index = VectorIndex(
                dimension=embedding_generator.get_dimension(),
                index_path=str(index_path),
            )

            with SQLiteManager(str(db_path)) as db:
                cursor = db.get_connection().cursor()
                cursor.execute("DELETE FROM embeddings")
                db._commit()

                tracks_to_embed = db.get_all_tracks()

                count = await self._generate_embeddings_for_tracks(
                    tracks_to_embed=tracks_to_embed,
                    embedding_generator=embedding_generator,
                    vector_index=vector_index,
                    db=db,
                    index_path=index_path,
                    progress_label="Regenerating embeddings...",
                    cancel_event=cancel_event,
                )

            result_msg = (
                f"Rebuilt embedding index with {count} vectors!"
                if count > 0
                else "No tracks available to embed."
            )
            task_store.update(
                "doctor_fix",
                extra={"fix_target": "embeddings_full", "result_msg": result_msg},
            )
            task_store.complete("doctor_fix")

        except Exception as e:
            task_store.complete("doctor_fix", status="failed", message=str(e))

    @rx.event
    def open_retag_confirm(self):
        self.show_retag_confirm = True

    @rx.event
    def close_retag_confirm(self):
        self.show_retag_confirm = False

    @rx.event
    def confirm_retag(self):
        self.show_retag_confirm = False
        return DoctorState.regenerate_all_tags

    @rx.event(background=True)
    async def regenerate_all_tags(self):
        """Regenerate tags for ALL tracks (force retag)."""
        async with self:
            self.is_fixing = True
            self.fix_message = "Regenerating tags for all tracks..."
            self.fix_progress = 0
            self.current_fix_target = "tags"

        cancel_event = task_store.start("doctor_fix", message="Regenerating tags for all tracks...")
        if cancel_event is None:
            return

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.ai.tag_generator import TagGenerator
            from plexmix.services.providers import build_ai_provider

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                task_store.complete(
                    "doctor_fix",
                    status="failed",
                    message="Database not found. Please run a sync first.",
                )
                return

            ai_provider = build_ai_provider(settings, silent=True)
            if ai_provider is None:
                ai_provider_name = settings.ai.default_provider or "gemini"
                task_store.complete(
                    "doctor_fix",
                    status="failed",
                    message=f"AI provider '{ai_provider_name}' is not fully configured.",
                )
                return

            tag_generator = TagGenerator(ai_provider)

            with SQLiteManager(str(db_path)) as db:
                all_tracks = db.get_all_tracks()

                if not all_tracks:
                    task_store.update(
                        "doctor_fix",
                        extra={
                            "fix_target": "tags",
                            "result_msg": "No tracks in database.",
                        },
                    )
                    task_store.complete("doctor_fix")
                    return

                total_tracks = len(all_tracks)
                task_store.update(
                    "doctor_fix",
                    extra={"fix_target": "tags", "fix_total": total_tracks},
                )

                def progress_callback(batch_num: int, total: int, tracks_tagged: int):
                    task_store.update(
                        "doctor_fix",
                        progress=tracks_tagged,
                        message=f"Regenerating all tags... {tracks_tagged}/{total_tracks}",
                        extra={"fix_target": "tags", "fix_total": total_tracks},
                    )

                def run_tag_regen():
                    results = tag_generator.generate_tags_batch(
                        all_tracks,
                        batch_size=20,
                        progress_callback=progress_callback,
                    )

                    count = 0
                    for track_id, tag_data in results.items():
                        tags = ",".join(tag_data.get("tags", []))
                        environments = ",".join(tag_data.get("environments", []))
                        instruments = ",".join(tag_data.get("instruments", []))

                        if tags or environments or instruments:
                            db.update_track_tags(
                                track_id,
                                tags=tags,
                                environments=environments,
                                instruments=instruments,
                            )
                            count += 1
                    return count

                loop = asyncio.get_running_loop()
                updated = await loop.run_in_executor(None, run_tag_regen)

            task_store.update(
                "doctor_fix",
                extra={
                    "fix_target": "tags",
                    "result_msg": f"Regenerated tags for {updated} tracks",
                },
            )
            task_store.complete("doctor_fix")

        except Exception as e:
            task_store.complete("doctor_fix", status="failed", message=str(e))

    @rx.event(background=True)
    async def regenerate_missing_tags(self):
        async with self:
            self.is_fixing = True
            self.fix_message = "Regenerating tags for untagged tracks..."
            self.fix_progress = 0
            self.current_fix_target = "tags"

        cancel_event = task_store.start(
            "doctor_fix", message="Regenerating tags for untagged tracks..."
        )
        if cancel_event is None:
            return  # already running

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.ai.tag_generator import TagGenerator
            from plexmix.services.providers import build_ai_provider

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                task_store.complete(
                    "doctor_fix",
                    status="failed",
                    message="Database not found. Please run a sync first.",
                )
                return

            ai_provider = build_ai_provider(settings, silent=True)
            if ai_provider is None:
                ai_provider_name = settings.ai.default_provider or "gemini"
                task_store.complete(
                    "doctor_fix",
                    status="failed",
                    message=f"AI provider '{ai_provider_name}' is not fully configured.",
                )
                return

            tag_generator = TagGenerator(ai_provider)

            with SQLiteManager(str(db_path)) as db:
                untagged_tracks = db.get_tracks_by_filter(has_no_tags=True)

                if not untagged_tracks:
                    task_store.update(
                        "doctor_fix",
                        extra={
                            "fix_target": "tags",
                            "result_msg": "All tracks already have AI-generated tags.",
                        },
                    )
                    task_store.complete("doctor_fix")
                    return

                total_untagged = len(untagged_tracks)
                task_store.update(
                    "doctor_fix",
                    extra={"fix_target": "tags", "fix_total": total_untagged},
                )

                def progress_callback(batch_num: int, total: int, tracks_tagged: int):
                    task_store.update(
                        "doctor_fix",
                        progress=tracks_tagged,
                        message=f"Regenerating tags... {tracks_tagged}/{total_untagged}",
                        extra={"fix_target": "tags", "fix_total": total_untagged},
                    )

                def run_tag_regen():
                    results = tag_generator.generate_tags_batch(
                        untagged_tracks,
                        batch_size=20,
                        progress_callback=progress_callback,
                    )

                    count = 0
                    for track_id, tag_data in results.items():
                        tags = ",".join(tag_data.get("tags", []))
                        environments = ",".join(tag_data.get("environments", []))
                        instruments = ",".join(tag_data.get("instruments", []))

                        if tags or environments or instruments:
                            db.update_track_tags(
                                track_id,
                                tags=tags,
                                environments=environments,
                                instruments=instruments,
                            )
                            count += 1
                    return count

                loop = asyncio.get_running_loop()
                updated = await loop.run_in_executor(None, run_tag_regen)

            task_store.update(
                "doctor_fix",
                extra={
                    "fix_target": "tags",
                    "result_msg": f"Regenerated tags for {updated} tracks",
                },
            )
            task_store.complete("doctor_fix")

        except Exception as e:
            task_store.complete("doctor_fix", status="failed", message=str(e))

    @rx.event(background=True)
    async def analyze_missing_audio(self):
        async with self:
            self.is_fixing = True
            self.fix_message = "Analyzing audio features for unanalyzed tracks..."
            self.fix_progress = 0
            self.current_fix_target = "audio_analysis"

        cancel_event = task_store.start(
            "doctor_fix", message="Analyzing audio features for unanalyzed tracks..."
        )
        if cancel_event is None:
            return  # already running

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            try:
                from plexmix.audio.analyzer import EssentiaAnalyzer
            except ImportError:
                task_store.complete(
                    "doctor_fix",
                    status="failed",
                    message="Essentia is not installed. Run: poetry install -E audio",
                )
                return

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            duration_limit = settings.audio.duration_limit

            if not db_path.exists():
                task_store.complete(
                    "doctor_fix",
                    status="failed",
                    message="Database not found. Please run a sync first.",
                )
                return

            loop = asyncio.get_running_loop()
            analyzer = EssentiaAnalyzer()
            num_workers = max(1, settings.audio.workers)

            with SQLiteManager(str(db_path)) as db:
                pending_tracks = [t for t in db.get_tracks_without_audio_features() if t.file_path]

                if not pending_tracks:
                    task_store.update(
                        "doctor_fix",
                        extra={
                            "fix_target": "audio_analysis",
                            "result_msg": "All tracks already have audio features.",
                        },
                    )
                    task_store.complete("doctor_fix")
                    return

                total = len(pending_tracks)
                task_store.update(
                    "doctor_fix",
                    extra={"fix_target": "audio_analysis", "fix_total": total},
                )

                analyzed = 0
                executor = ThreadPoolExecutor(max_workers=num_workers)
                track_iter = iter(pending_tracks)
                in_flight: dict[asyncio.Future, object] = {}

                def _submit():
                    t = next(track_iter, None)
                    if t is None:
                        return

                    def do_analyze(trk=t):
                        resolved = settings.audio.resolve_path(trk.file_path)
                        features = analyzer.analyze(resolved, duration_limit=duration_limit)
                        return trk, features.to_dict()

                    in_flight[loop.run_in_executor(executor, do_analyze)] = t

                for _ in range(min(num_workers, total)):
                    _submit()

                while in_flight:
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
                            logger.warning(
                                "Audio analysis failed for track %s: %s",
                                getattr(exc, "track_id", "?"),
                                exc,
                            )
                        _submit()

                    task_store.update(
                        "doctor_fix",
                        progress=analyzed,
                        message=f"Analyzing audio... {analyzed}/{total} ({num_workers} workers)",
                        extra={"fix_target": "audio_analysis", "fix_total": total},
                    )

                executor.shutdown(wait=False)

            task_store.update(
                "doctor_fix",
                extra={
                    "fix_target": "audio_analysis",
                    "result_msg": f"Analyzed audio features for {analyzed} tracks",
                },
            )
            task_store.complete("doctor_fix")

        except Exception as e:
            task_store.complete("doctor_fix", status="failed", message=str(e))

    async def _generate_embeddings_for_tracks(
        self,
        tracks_to_embed,
        embedding_generator,
        vector_index,
        db,
        index_path: Path,
        progress_label: str,
        cancel_event=None,
    ) -> int:
        """Generate embeddings for tracks. Returns count of embeddings saved."""
        from plexmix.database.models import Embedding
        from plexmix.utils.embeddings import create_track_text

        if not tracks_to_embed:
            return 0

        total = len(tracks_to_embed)
        batch_size = 50

        def run_embedding_generation() -> int:
            embeddings_saved = 0

            for i in range(0, len(tracks_to_embed), batch_size):
                if cancel_event is not None and cancel_event.is_set():
                    break

                batch_tracks = tracks_to_embed[i : i + batch_size]

                track_data_list: List[Dict[str, Any]] = []
                for track in batch_tracks:
                    artist = db.get_artist_by_id(track.artist_id)
                    album = db.get_album_by_id(track.album_id)

                    track_data = {
                        "id": track.id,
                        "title": track.title,
                        "artist": artist.name if artist else "Unknown",
                        "album": album.title if album else "Unknown",
                        "genre": track.genre or "",
                        "year": track.year or "",
                        "tags": track.tags or "",
                    }
                    track_data_list.append(track_data)

                texts = [create_track_text(td) for td in track_data_list]
                embeddings = embedding_generator.generate_batch_embeddings(
                    texts, batch_size=batch_size
                )

                for track_data, embedding_vector in zip(track_data_list, embeddings):
                    embedding = Embedding(
                        track_id=track_data["id"],
                        embedding_model=embedding_generator.provider_name,
                        embedding_dim=embedding_generator.get_dimension(),
                        vector=embedding_vector,
                    )
                    db.insert_embedding(embedding)
                    embeddings_saved += 1

                    task_store.update(
                        "doctor_fix",
                        progress=embeddings_saved,
                        message=f"{progress_label} {embeddings_saved}/{total}",
                        extra={"fix_total": total},
                    )

            all_embeddings = db.get_all_embeddings()
            track_ids = [emb[0] for emb in all_embeddings]
            vectors = [emb[1] for emb in all_embeddings]

            if track_ids:
                vector_index.build_index(vectors, track_ids)
                vector_index.save_index(str(index_path))

            return embeddings_saved

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, run_embedding_generation)

    @rx.event
    def poll_task_progress(self):
        """Client-initiated poll for doctor fix progress."""
        entry = task_store.get("doctor_fix")
        if entry is None:
            return

        if entry.status == "running":
            self.is_fixing = True
            self.fix_progress = entry.progress
            self.fix_message = entry.message
            self.fix_total = int(entry.extra.get("fix_total", 0))
            self.current_fix_target = entry.extra.get("fix_target", "")
        elif entry.status == "completed":
            self.is_fixing = False
            self.fix_message = ""
            self.fix_progress = 0
            self.fix_total = 0
            self.current_fix_target = ""
            self._refresh_health_stats()
            msg = entry.extra.get("result_msg", "Fix completed!")
            task_store.clear("doctor_fix")
            return rx.toast.success(msg)
        elif entry.status == "failed":
            self.is_fixing = False
            self.fix_message = ""
            self.fix_progress = 0
            self.fix_total = 0
            self.current_fix_target = ""
            msg = entry.message or "Fix failed"
            task_store.clear("doctor_fix")
            return rx.toast.error(msg)

    @rx.var(cache=True)
    def orphaned_embeddings_label(self) -> str:
        return f"{self.doctor_orphaned_embeddings} Orphaned Embeddings"

    @rx.var(cache=True)
    def missing_embeddings_label(self) -> str:
        return f"{self.doctor_tracks_needing_embeddings} Tracks Need Embeddings"

    @rx.var(cache=True)
    def fix_progress_label(self) -> str:
        if self.fix_total <= 0:
            return "0 / 0"
        return f"{self.fix_progress} / {self.fix_total}"

    @rx.var(cache=True)
    def embedding_job_running(self) -> bool:
        return self.current_fix_target in ("embeddings_incremental", "embeddings_full")

    @rx.var(cache=True)
    def incremental_embedding_running(self) -> bool:
        return self.current_fix_target == "embeddings_incremental"

    @rx.var(cache=True)
    def full_embedding_running(self) -> bool:
        return self.current_fix_target == "embeddings_full"

    @rx.var(cache=True)
    def tag_job_running(self) -> bool:
        return self.current_fix_target == "tags"

    @rx.var(cache=True)
    def missing_audio_label(self) -> str:
        return f"{self.doctor_tracks_without_audio} Tracks Need Audio Analysis"

    @rx.var(cache=True)
    def audio_job_running(self) -> bool:
        return self.current_fix_target == "audio_analysis"

    @rx.var(cache=True)
    def untagged_tracks_message(self) -> str:
        if self.doctor_untagged_tracks > 0:
            return (
                f"{self.doctor_untagged_tracks} tracks don't have AI-generated tags. "
                "Use the controls below or visit the Tagging page to generate them."
            )
        return (
            "All tracks currently have AI-generated tags. "
            "You can regenerate them if you want to refresh metadata."
        )

    @rx.var(cache=True)
    def missing_musicbrainz_label(self) -> str:
        return f"{self.doctor_tracks_without_musicbrainz} Tracks Need MusicBrainz Enrichment"

    @rx.var(cache=True)
    def musicbrainz_job_running(self) -> bool:
        return self.current_fix_target == "musicbrainz"

    @rx.event(background=True)
    async def enrich_missing_musicbrainz(self):
        async with self:
            self.is_fixing = True
            self.fix_message = "Enriching tracks with MusicBrainz metadata..."
            self.fix_progress = 0
            self.current_fix_target = "musicbrainz"

        cancel_event = task_store.start(
            "doctor_fix", message="Enriching tracks with MusicBrainz metadata..."
        )
        if cancel_event is None:
            return

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.services.musicbrainz_service import (
                get_enrichable_tracks,
                enrich_tracks,
            )

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                task_store.complete(
                    "doctor_fix",
                    status="failed",
                    message="Database not found. Please run a sync first.",
                )
                return

            def run_enrichment():
                with SQLiteManager(str(db_path)) as db:
                    tracks = get_enrichable_tracks(db)
                    if not tracks:
                        return 0, 0, 0

                    total = len(tracks)
                    task_store.update(
                        "doctor_fix",
                        extra={"fix_target": "musicbrainz", "fix_total": total},
                    )

                    def on_progress(
                        enriched: int, cached: int, mb_errors: int, total_count: int
                    ) -> None:
                        processed = enriched + cached + mb_errors
                        task_store.update(
                            "doctor_fix",
                            progress=processed,
                            message=f"MusicBrainz enrichment... {processed}/{total_count}",
                            extra={"fix_target": "musicbrainz", "fix_total": total_count},
                        )

                    return enrich_tracks(
                        db,
                        settings.musicbrainz,
                        tracks,
                        progress_callback=on_progress,
                        cancel_event=cancel_event,
                    )

            loop = asyncio.get_running_loop()
            enriched, cached, errors = await loop.run_in_executor(None, run_enrichment)

            task_store.update(
                "doctor_fix",
                extra={
                    "fix_target": "musicbrainz",
                    "result_msg": f"Enriched {enriched} tracks, {cached} cached ({errors} errors)",
                },
            )
            task_store.complete("doctor_fix")

        except Exception as e:
            task_store.complete("doctor_fix", status="failed", message=str(e))
