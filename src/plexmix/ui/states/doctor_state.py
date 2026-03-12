import reflex as rx
import asyncio
import logging
from typing import List, Dict, Any
from pathlib import Path
from plexmix.ui.states.app_state import AppState

logger = logging.getLogger(__name__)


class DoctorState(AppState):
    doctor_total_tracks: int = 0
    doctor_tracks_with_embeddings: int = 0
    doctor_orphaned_embeddings: int = 0
    doctor_untagged_tracks: int = 0
    doctor_tracks_needing_embeddings: int = 0
    doctor_tracks_without_audio: int = 0
    doctor_audio_features_count: int = 0

    is_healthy: bool = False
    is_checking: bool = False
    check_message: str = ""
    
    is_fixing: bool = False
    fix_message: str = ""
    fix_progress: int = 0
    fix_total: int = 0
    current_fix_target: str = ""
    
    @rx.event
    def on_load(self):
        if not self.check_auth():
            self.is_page_loading = False
            return
        super().on_load()
        return DoctorState.run_health_check
    
    @rx.event(background=True)
    async def run_health_check(self):
        async with self:
            self.is_checking = True
            self.check_message = "Running health check..."
        
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            
            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            
            if not db_path.exists():
                async with self:
                    self.check_message = "Database not found. Please run a sync first."
                    self.is_checking = False
                    self.is_page_loading = False
                return
            
            with SQLiteManager(str(db_path)) as db:
                cursor = db.get_connection().cursor()
                
                # Get total tracks
                cursor.execute('SELECT COUNT(*) FROM tracks')
                total_tracks = cursor.fetchone()[0]
                
                # Get tracks with embeddings
                cursor.execute('SELECT COUNT(DISTINCT track_id) FROM embeddings')
                tracks_with_embeddings = cursor.fetchone()[0]
                
                # Get orphaned embeddings
                cursor.execute('''
                    SELECT COUNT(*) FROM embeddings
                    WHERE track_id NOT IN (SELECT id FROM tracks)
                ''')
                orphaned_count = cursor.fetchone()[0]
                
                # Get untagged tracks
                cursor.execute('SELECT COUNT(*) FROM tracks WHERE tags IS NULL OR tags = ""')
                untagged_count = cursor.fetchone()[0]
                
                # Get tracks needing embeddings
                cursor.execute('SELECT COUNT(*) FROM tracks WHERE id NOT IN (SELECT DISTINCT track_id FROM embeddings)')
                tracks_needing_embeddings = cursor.fetchone()[0]

                # Get audio analysis stats
                audio_features_count = db.get_audio_features_count()
                tracks_without_audio = len(db.get_tracks_without_audio_features())

                async with self:
                    self.doctor_total_tracks = total_tracks
                    self.doctor_tracks_with_embeddings = tracks_with_embeddings
                    self.doctor_orphaned_embeddings = orphaned_count
                    self.doctor_untagged_tracks = untagged_count
                    self.doctor_tracks_needing_embeddings = tracks_needing_embeddings
                    self.doctor_audio_features_count = audio_features_count
                    self.doctor_tracks_without_audio = tracks_without_audio
                    
                    if orphaned_count == 0 and tracks_needing_embeddings == 0:
                        self.is_healthy = True
                        self.check_message = "✓ Database is healthy! All tracks have embeddings and no orphaned data."
                    else:
                        self.is_healthy = False
                        issues = []
                        if orphaned_count > 0:
                            issues.append(f"{orphaned_count} orphaned embeddings")
                        if tracks_needing_embeddings > 0:
                            issues.append(f"{tracks_needing_embeddings} tracks need embeddings")
                        self.check_message = f"⚠️ Issues found: {', '.join(issues)}"

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
        
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            
            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            
            with SQLiteManager(str(db_path)) as db:
                cursor = db.get_connection().cursor()
                cursor.execute('DELETE FROM embeddings WHERE track_id NOT IN (SELECT id FROM tracks)')
                deleted = cursor.rowcount
                db.get_connection().commit()
                
                async with self:
                    self.fix_message = ""
                    self.is_fixing = False
                    self.current_fix_target = ""

            yield rx.toast.success(f"Deleted {deleted} orphaned embeddings")
            yield DoctorState.run_health_check()

        except Exception as e:
            async with self:
                self.fix_message = ""
                self.is_fixing = False
                self.current_fix_target = ""
            yield rx.toast.error(f"Error deleting orphaned embeddings: {str(e)}")
    
    @rx.event(background=True)
    async def generate_missing_embeddings(self):
        async with self:
            self.is_fixing = True
            self.fix_message = "Generating embeddings for missing tracks..."
            self.fix_progress = 0
            self.current_fix_target = "embeddings_incremental"
        
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.utils.embeddings import EmbeddingGenerator
            from plexmix.database.vector_index import VectorIndex
            from plexmix.config.credentials import (
                get_google_api_key, get_openai_api_key, get_cohere_api_key,
                get_custom_embedding_api_key,
            )

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            # Get API key based on provider
            provider = settings.embedding.default_provider
            api_key = None
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

            if not api_key and provider not in ("local", "custom"):
                async with self:
                    self.is_fixing = False
                yield rx.toast.error(f"API key required for {provider} provider")
                return

            embed_model = settings.embedding.model
            if provider == "custom":
                embed_model = settings.embedding.custom_model or embed_model

            embedding_generator = EmbeddingGenerator(
                provider=provider,
                api_key=api_key,
                model=embed_model,
                **embed_kwargs,
            )
            
            index_path = settings.database.get_index_path()
            vector_index = VectorIndex(
                dimension=embedding_generator.get_dimension(),
                index_path=str(index_path)
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
                )

            if count > 0:
                yield rx.toast.success(f"Successfully generated {count} embeddings!")
            else:
                yield rx.toast.info("All tracks already have embeddings.")
            yield DoctorState.run_health_check()

        except Exception as e:
            async with self:
                self.fix_message = ""
                self.is_fixing = False
                self.current_fix_target = ""
            yield rx.toast.error(f"Error generating embeddings: {str(e)}")
    
    @rx.event(background=True)
    async def regenerate_all_embeddings(self):
        async with self:
            self.is_fixing = True
            self.fix_message = "Regenerating all embeddings..."
            self.fix_progress = 0
            self.current_fix_target = "embeddings_full"

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.utils.embeddings import EmbeddingGenerator
            from plexmix.database.vector_index import VectorIndex
            from plexmix.config.credentials import (
                get_google_api_key, get_openai_api_key, get_cohere_api_key,
                get_custom_embedding_api_key,
            )

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            provider = settings.embedding.default_provider
            api_key = None
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

            if not api_key and provider not in ("local", "custom"):
                async with self:
                    self.is_fixing = False
                    self.current_fix_target = ""
                yield rx.toast.error(f"API key required for {provider} provider")
                return

            embed_model = settings.embedding.model
            if provider == "custom":
                embed_model = settings.embedding.custom_model or embed_model

            embedding_generator = EmbeddingGenerator(
                provider=provider,
                api_key=api_key,
                model=embed_model,
                **embed_kwargs,
            )

            index_path = settings.database.get_index_path()
            if index_path.exists():
                index_path.unlink()
            metadata_path = index_path.with_suffix('.metadata')
            if metadata_path.exists():
                metadata_path.unlink()

            vector_index = VectorIndex(
                dimension=embedding_generator.get_dimension(),
                index_path=str(index_path),
            )

            with SQLiteManager(str(db_path)) as db:
                cursor = db.get_connection().cursor()
                cursor.execute('DELETE FROM embeddings')
                db.get_connection().commit()

                tracks_to_embed = db.get_all_tracks()

                count = await self._generate_embeddings_for_tracks(
                    tracks_to_embed=tracks_to_embed,
                    embedding_generator=embedding_generator,
                    vector_index=vector_index,
                    db=db,
                    index_path=index_path,
                    progress_label="Regenerating embeddings...",
                )

            if count > 0:
                yield rx.toast.success(f"Rebuilt embedding index with {count} vectors!")
            else:
                yield rx.toast.info("No tracks available to embed.")
            yield DoctorState.run_health_check()

        except Exception as e:
            async with self:
                self.fix_message = ""
                self.is_fixing = False
                self.current_fix_target = ""
            yield rx.toast.error(f"Error regenerating embeddings: {str(e)}")

    @rx.event(background=True)
    async def regenerate_missing_tags(self):
        async with self:
            self.is_fixing = True
            self.fix_message = "Regenerating tags for untagged tracks..."
            self.fix_progress = 0
            self.current_fix_target = "tags"

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.ai import get_ai_provider
            from plexmix.ai.tag_generator import TagGenerator
            from plexmix.config.credentials import (
                get_google_api_key,
                get_openai_api_key,
                get_anthropic_api_key,
                get_cohere_api_key,
            )

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                async with self:
                    self.is_fixing = False
                    self.current_fix_target = ""
                yield rx.toast.error("Database not found. Please run a sync first.")
                return

            ai_provider_name = settings.ai.default_provider or "gemini"
            provider_alias = "claude" if ai_provider_name == "anthropic" else ai_provider_name
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
                    provider_name=ai_provider_name,
                    api_key=api_key,
                    model=settings.ai.model,
                    temperature=settings.ai.temperature,
                    local_mode=settings.ai.local_mode,
                    local_endpoint=settings.ai.local_endpoint,
                    local_auth_token=settings.ai.local_auth_token,
                    local_max_output_tokens=settings.ai.local_max_output_tokens,
                )
            except ValueError:
                async with self:
                    self.is_fixing = False
                    self.current_fix_target = ""
                yield rx.toast.error(f"AI provider '{ai_provider_name}' is not fully configured.")
                return

            tag_generator = TagGenerator(ai_provider)

            with SQLiteManager(str(db_path)) as db:
                untagged_tracks = db.get_tracks_by_filter(has_no_tags=True)

                if not untagged_tracks:
                    async with self:
                        self.is_fixing = False
                        self.current_fix_target = ""
                    yield rx.toast.info("All tracks already have AI-generated tags.")
                    return

                async with self:
                    self.fix_total = len(untagged_tracks)

                def progress_callback(batch_num: int, total: int, tracks_tagged: int):
                    async def update_progress():
                        async with self:
                            self.fix_progress = tracks_tagged
                            self.fix_message = f"Regenerating tags... {tracks_tagged}/{self.fix_total}"

                    asyncio.create_task(update_progress())

                results = tag_generator.generate_tags_batch(
                    untagged_tracks,
                    batch_size=20,
                    progress_callback=progress_callback,
                )

                updated = 0
                for track_id, tag_data in results.items():
                    tags = ','.join(tag_data.get('tags', []))
                    environments = ','.join(tag_data.get('environments', []))
                    instruments = ','.join(tag_data.get('instruments', []))

                    if tags or environments or instruments:
                        db.update_track_tags(
                            track_id,
                            tags=tags,
                            environments=environments,
                            instruments=instruments,
                        )
                        updated += 1

                async with self:
                    self.fix_message = ""
                    self.is_fixing = False
                    self.fix_progress = 0
                    self.fix_total = 0
                    self.current_fix_target = ""

                yield rx.toast.success(f"Regenerated tags for {updated} tracks")

            yield DoctorState.run_health_check()

        except Exception as e:
            async with self:
                self.fix_message = ""
                self.is_fixing = False
                self.current_fix_target = ""
            yield rx.toast.error(f"Error regenerating tags: {str(e)}")
    
    @rx.event(background=True)
    async def analyze_missing_audio(self):
        async with self:
            self.is_fixing = True
            self.fix_message = "Analyzing audio features for unanalyzed tracks..."
            self.fix_progress = 0
            self.current_fix_target = "audio_analysis"

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            try:
                from plexmix.audio.analyzer import EssentiaAnalyzer
            except ImportError:
                async with self:
                    self.is_fixing = False
                    self.current_fix_target = ""
                yield rx.toast.error("Essentia is not installed. Run: poetry install -E audio")
                return

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            duration_limit = settings.audio.duration_limit

            if not db_path.exists():
                async with self:
                    self.is_fixing = False
                    self.current_fix_target = ""
                yield rx.toast.error("Database not found. Please run a sync first.")
                return

            loop = asyncio.get_running_loop()
            analyzer = EssentiaAnalyzer()

            with SQLiteManager(str(db_path)) as db:
                pending_tracks = db.get_tracks_without_audio_features()

                if not pending_tracks:
                    async with self:
                        self.is_fixing = False
                        self.current_fix_target = ""
                    yield rx.toast.info("All tracks already have audio features.")
                    return

                async with self:
                    self.fix_total = len(pending_tracks)

                analyzed = 0
                for track in pending_tracks:
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

                    async with self:
                        self.fix_progress = analyzed
                        self.fix_message = f"Analyzing audio... {analyzed}/{self.fix_total}"

                async with self:
                    self.fix_message = ""
                    self.is_fixing = False
                    self.fix_progress = 0
                    self.fix_total = 0
                    self.current_fix_target = ""

                yield rx.toast.success(f"Analyzed audio features for {analyzed} tracks")

            yield DoctorState.run_health_check()

        except Exception as e:
            async with self:
                self.fix_message = ""
                self.is_fixing = False
                self.current_fix_target = ""
            yield rx.toast.error(f"Error analyzing audio: {str(e)}")

    async def _generate_embeddings_for_tracks(
        self,
        tracks_to_embed,
        embedding_generator,
        vector_index,
        db,
        index_path: Path,
        progress_label: str,
    ) -> int:
        """Generate embeddings for tracks. Returns count of embeddings saved."""
        from plexmix.database.models import Embedding
        from plexmix.utils.embeddings import create_track_text

        if not tracks_to_embed:
            async with self:
                self.is_fixing = False
                self.current_fix_target = ""
            return 0

        async with self:
            self.fix_total = len(tracks_to_embed)

        batch_size = 50
        embeddings_saved = 0

        for i in range(0, len(tracks_to_embed), batch_size):
            batch_tracks = tracks_to_embed[i:i + batch_size]

            track_data_list: List[Dict[str, Any]] = []
            for track in batch_tracks:
                artist = db.get_artist_by_id(track.artist_id)
                album = db.get_album_by_id(track.album_id)

                track_data = {
                    'id': track.id,
                    'title': track.title,
                    'artist': artist.name if artist else 'Unknown',
                    'album': album.title if album else 'Unknown',
                    'genre': track.genre or '',
                    'year': track.year or '',
                    'tags': track.tags or ''
                }
                track_data_list.append(track_data)

            texts = [create_track_text(td) for td in track_data_list]
            embeddings = embedding_generator.generate_batch_embeddings(texts, batch_size=batch_size)

            for track_data, embedding_vector in zip(track_data_list, embeddings):
                embedding = Embedding(
                    track_id=track_data['id'],
                    embedding_model=embedding_generator.provider_name,
                    embedding_dim=embedding_generator.get_dimension(),
                    vector=embedding_vector
                )
                db.insert_embedding(embedding)
                embeddings_saved += 1

                async with self:
                    self.fix_progress = embeddings_saved
                    self.fix_message = f"{progress_label} {embeddings_saved}/{self.fix_total}"

        all_embeddings = db.get_all_embeddings()
        track_ids = [emb[0] for emb in all_embeddings]
        vectors = [emb[1] for emb in all_embeddings]

        if track_ids:
            vector_index.build_index(vectors, track_ids)
            vector_index.save_index(str(index_path))

        async with self:
            self.fix_message = ""
            self.is_fixing = False
            self.fix_progress = 0
            self.fix_total = 0
            self.current_fix_target = ""

        return embeddings_saved

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
