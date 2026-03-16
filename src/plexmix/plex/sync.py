from typing import Any, Optional, Dict, Callable, List
import logging
from threading import Event
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
)

from ..plex.client import PlexClient
from ..database.sqlite_manager import SQLiteManager
from ..database.models import Artist, Album, Track, Genre, SyncHistory
from ..database.vector_index import VectorIndex
from ..utils.embeddings import EmbeddingGenerator, create_track_text
from ..ai.tag_generator import TagGenerator
from ..ai.base import AIProvider
from ..config.settings import MusicBrainzSettings

logger = logging.getLogger(__name__)


class SyncEngine:
    def __init__(
        self,
        plex_client: PlexClient,
        db_manager: SQLiteManager,
        embedding_generator: Optional[EmbeddingGenerator] = None,
        vector_index: Optional[VectorIndex] = None,
        ai_provider: Optional[AIProvider] = None,
        musicbrainz_settings: Optional[MusicBrainzSettings] = None,
    ):
        self.plex = plex_client
        self.db = db_manager
        self.embedding_generator = embedding_generator
        self.vector_index = vector_index
        self.ai_provider = ai_provider
        self.tag_generator = TagGenerator(ai_provider) if ai_provider else None
        self.musicbrainz_settings = musicbrainz_settings

    def incremental_sync(
        self,
        generate_embeddings: bool = True,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
    ) -> SyncHistory:
        logger.info("Starting incremental library sync")
        stats = {
            "tracks_added": 0,
            "tracks_updated": 0,
            "tracks_removed": 0,
            "artists_processed": 0,
            "albums_processed": 0,
        }

        if progress_callback:
            progress_callback(0.0, "Validating Plex connection...")

        # Pre-flight token validation — fail fast before expensive work
        token_valid, token_msg = self.plex.validate_token()
        if not token_valid:
            logger.error("Plex token validation failed: %s", token_msg)
            raise ConnectionError(token_msg)

        if progress_callback:
            progress_callback(0.0, "Starting incremental sync...")

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
            ) as progress:
                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                task = progress.add_task("Building Plex library index...", total=None)
                plex_library = self._build_plex_library_index(
                    progress, task, progress_callback, cancel_event, 0.0, 0.3
                )

                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                task = progress.add_task("Comparing with database...", total=None)
                changes = self._detect_library_changes(
                    plex_library, progress, task, progress_callback, cancel_event, 0.3, 0.45
                )
                stats.update(changes)

                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                # MusicBrainz enrichment (between DB sync and AI tags)
                if self.musicbrainz_settings and self.musicbrainz_settings.enrich_on_sync:
                    task = progress.add_task("Enriching with MusicBrainz...", total=None)
                    self._enrich_musicbrainz(
                        progress, task, progress_callback, cancel_event, 0.45, 0.55
                    )

                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                if self.tag_generator and self.ai_provider:
                    task = progress.add_task("Generating AI tags...", total=None)
                    self._generate_tags_for_untagged_tracks(
                        progress, task, progress_callback, cancel_event, 0.55, 0.7
                    )

                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                if generate_embeddings and self.embedding_generator and self.vector_index:
                    task = progress.add_task("Generating embeddings...", total=None)
                    self._generate_embeddings_for_new_tracks(
                        progress, task, progress_callback, cancel_event, 0.7, 1.0
                    )

            sync_record = SyncHistory(
                tracks_added=stats["tracks_added"],
                tracks_updated=stats["tracks_updated"],
                tracks_removed=stats["tracks_removed"],
                status="success",
            )
            self.db.insert_sync_record(sync_record)

            if progress_callback:
                progress_callback(1.0, "Incremental sync completed successfully")

            logger.info(
                f"Incremental sync completed: {stats['tracks_added']} added, "
                f"{stats['tracks_updated']} updated, {stats['tracks_removed']} removed"
            )
            return sync_record

        except KeyboardInterrupt:
            logger.warning("Sync interrupted by user")
            sync_record = SyncHistory(
                tracks_added=stats["tracks_added"],
                tracks_updated=stats["tracks_updated"],
                tracks_removed=stats["tracks_removed"],
                status="interrupted",
                error_message="User interrupted sync",
            )
            self.db.insert_sync_record(sync_record)
            raise

        except Exception as e:
            logger.error(f"Incremental sync failed: {e}")
            sync_record = SyncHistory(status="failed", error_message=str(e))
            self.db.insert_sync_record(sync_record)
            raise

    def regenerate_sync(
        self,
        generate_embeddings: bool = True,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
    ) -> SyncHistory:
        logger.info("Starting regenerate sync - will clear and regenerate all tags and embeddings")

        if progress_callback:
            progress_callback(0.0, "Clearing existing tags and embeddings...")

        cursor = self.db.get_connection().cursor()
        cursor.execute("UPDATE tracks SET tags = NULL, environments = NULL, instruments = NULL")
        cursor.execute("DELETE FROM embeddings")
        self.db._commit()
        logger.info("Cleared all existing tags and embeddings")

        return self.incremental_sync(
            generate_embeddings=generate_embeddings,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )

    def full_sync(
        self,
        generate_embeddings: bool = True,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
    ) -> SyncHistory:
        logger.info("Starting full library sync (alias for incremental_sync)")
        return self.incremental_sync(
            generate_embeddings=generate_embeddings,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )

    def _build_plex_library_index(
        self,
        progress: Progress,
        task: Any,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
    ) -> Dict[str, Any]:
        plex_library: Dict[str, Any] = {"artists": {}, "albums": {}, "tracks": {}}

        total_items = 0
        for artist_batch in self.plex.get_all_artists(batch_size=100):
            if cancel_event and cancel_event.is_set():
                break

            for artist in artist_batch:
                plex_library["artists"][artist.plex_key] = artist

            total_items += len(artist_batch)
            progress.update(task, advance=len(artist_batch))

        for album_batch in self.plex.get_all_albums(batch_size=100):
            if cancel_event and cancel_event.is_set():
                break

            for album in album_batch:
                plex_library["albums"][album.plex_key] = album

            total_items += len(album_batch)
            progress.update(task, advance=len(album_batch))

        for track_batch in self.plex.get_all_tracks(batch_size=100):
            if cancel_event and cancel_event.is_set():
                break

            for track in track_batch:
                plex_library["tracks"][track.plex_key] = track

            total_items += len(track_batch)
            progress.update(task, advance=len(track_batch))

            if progress_callback and total_items % 100 == 0:
                current_progress = progress_start + (progress_end - progress_start) * 0.5
                progress_callback(current_progress, f"Indexed {total_items} items from Plex...")

        progress.update(
            task,
            description=f"Indexed {len(plex_library['artists'])} artists, {len(plex_library['albums'])} albums, {len(plex_library['tracks'])} tracks",
        )
        return plex_library

    def _get_or_create_unknown_entities(self) -> tuple[int, int]:
        """Ensure 'Unknown' artist and album exist and return their IDs."""
        # Get or create Unknown artist
        unknown_artist = self.db.get_artist_by_plex_key("__unknown__")
        if unknown_artist and unknown_artist.id is not None:
            unknown_artist_id = unknown_artist.id
        else:
            unknown_artist_obj = Artist(plex_key="__unknown__", name="Unknown Artist")
            unknown_artist_id = self.db.insert_artist(unknown_artist_obj)
            logger.info("Created 'Unknown Artist' entity for orphaned items")

        # Get or create Unknown album (linked to Unknown artist)
        unknown_album = self.db.get_album_by_plex_key("__unknown__")
        if unknown_album and unknown_album.id is not None:
            unknown_album_id = unknown_album.id
        else:
            unknown_album_obj = Album(
                plex_key="__unknown__", title="Unknown Album", artist_id=unknown_artist_id
            )
            unknown_album_id = self.db.insert_album(unknown_album_obj)
            logger.info("Created 'Unknown Album' entity for orphaned items")

        return unknown_artist_id, unknown_album_id

    def _detect_library_changes(
        self,
        plex_library: Dict[str, Any],
        progress: Progress,
        task: Any,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
    ) -> Dict[str, int]:
        stats = {"tracks_added": 0, "tracks_updated": 0, "tracks_removed": 0}

        # Ensure Unknown entities exist for orphaned items
        unknown_artist_id, unknown_album_id = self._get_or_create_unknown_entities()

        db_artists = {a.plex_key: a for a in self.db.get_all_artists()}
        db_albums = {a.plex_key: a for a in self.db.get_all_albums()}
        db_tracks = {t.plex_key: t for t in self.db.get_all_tracks()}

        # Pre-cache known genres to avoid per-track DB lookups
        known_genres: set = set()
        for genre_obj in self.db.get_all_genres():
            known_genres.add(genre_obj.name)

        with self.db.deferred_commits():
            artist_map = {}
            for plex_key, plex_artist in plex_library["artists"].items():
                if plex_key in db_artists:
                    artist_map[plex_key] = db_artists[plex_key].id
                else:
                    artist_id = self.db.insert_artist(plex_artist)
                    artist_map[plex_key] = artist_id

            album_map = {}
            for plex_key, plex_album in plex_library["albums"].items():
                artist_plex_key = plex_album.__dict__.get("_artist_key")
                if artist_plex_key and artist_plex_key in artist_map:
                    plex_album.artist_id = artist_map[artist_plex_key]
                else:
                    logger.warning(
                        f"Album '{plex_album.title}' missing artist link; assigning to Unknown Artist"
                    )
                    plex_album.artist_id = unknown_artist_id

                if plex_key in db_albums:
                    album_map[plex_key] = db_albums[plex_key].id
                else:
                    album_id = self.db.insert_album(plex_album)
                    album_map[plex_key] = album_id

            total_tracks = len(plex_library["tracks"])
            processed = 0

            for plex_key, plex_track in plex_library["tracks"].items():
                if cancel_event and cancel_event.is_set():
                    break

                artist_plex_key = plex_track.__dict__.get("_artist_key")
                album_plex_key = plex_track.__dict__.get("_album_key")

                if artist_plex_key and artist_plex_key in artist_map:
                    plex_track.artist_id = artist_map[artist_plex_key]
                else:
                    plex_track.artist_id = unknown_artist_id

                if album_plex_key and album_plex_key in album_map:
                    plex_track.album_id = album_map[album_plex_key]
                else:
                    plex_track.album_id = unknown_album_id

                if plex_key in db_tracks:
                    existing = db_tracks[plex_key]
                    if self._track_needs_update(existing, plex_track):
                        plex_track.id = existing.id
                        plex_track.tags = existing.tags
                        plex_track.environments = existing.environments
                        plex_track.instruments = existing.instruments
                        self.db.insert_track(plex_track)
                        stats["tracks_updated"] += 1
                else:
                    self.db.insert_track(plex_track)
                    stats["tracks_added"] += 1

                if plex_track.genre:
                    for genre_name in plex_track.genre.split(","):
                        genre_name = genre_name.strip().lower()
                        if genre_name not in known_genres:
                            self.db.insert_genre(Genre(name=genre_name))
                            known_genres.add(genre_name)

                processed += 1
                if processed % 10 == 0:
                    progress.update(task, advance=10)
                    if progress_callback:
                        current_progress = progress_start + (progress_end - progress_start) * (
                            processed / total_tracks
                        )
                        progress_callback(
                            current_progress, f"Processing tracks... ({processed}/{total_tracks})"
                        )

            for db_plex_key in db_tracks.keys():
                if db_plex_key not in plex_library["tracks"]:
                    track_id = db_tracks[db_plex_key].id
                    if track_id is not None:
                        self.db.delete_track(track_id)
                    stats["tracks_removed"] += 1

        progress.update(
            task,
            description=f"Changes: +{stats['tracks_added']} ~{stats['tracks_updated']} -{stats['tracks_removed']}",
        )
        return stats

    def _track_needs_update(self, db_track: Track, plex_track: Track) -> bool:
        return (
            db_track.title != plex_track.title
            or db_track.year != plex_track.year
            or db_track.genre != plex_track.genre
            or db_track.duration_ms != plex_track.duration_ms
            or db_track.rating != plex_track.rating
            or db_track.play_count != plex_track.play_count
            or db_track.last_played != plex_track.last_played
        )

    def _generate_embeddings_for_new_tracks(
        self,
        progress: Progress,
        task: Any,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
    ) -> None:
        if not self.embedding_generator or not self.vector_index:
            return

        all_tracks = self.db.get_all_tracks()
        existing_track_ids = self.db.get_track_ids_with_embeddings()
        tracks_needing_embeddings = [t for t in all_tracks if t.id not in existing_track_ids]

        if not tracks_needing_embeddings:
            progress.update(task, description="No new tracks need embeddings")
            return

        progress.update(task, total=len(tracks_needing_embeddings))
        logger.info(f"Generating embeddings for {len(tracks_needing_embeddings)} tracks")

        from ..database.models import Embedding

        batch_size = 50
        embeddings_saved = 0
        total_batches = (len(tracks_needing_embeddings) + batch_size - 1) // batch_size
        saved_vectors: List[
            tuple[int, List[float]]
        ] = []  # Collect (track_id, vector) in-memory for FAISS update

        try:
            for i in range(0, len(tracks_needing_embeddings), batch_size):
                if cancel_event and cancel_event.is_set():
                    logger.warning(
                        f"Embedding generation cancelled. Saved {embeddings_saved} embeddings."
                    )
                    break

                batch_tracks = tracks_needing_embeddings[i : i + batch_size]
                batch_num = i // batch_size + 1

                # Bulk fetch artists and albums for this batch (eliminates N+1)
                artist_ids = list(set(t.artist_id for t in batch_tracks if t.artist_id))
                album_ids = list(set(t.album_id for t in batch_tracks if t.album_id))
                artists_map = self.db.get_artists_by_ids(artist_ids)
                albums_map = self.db.get_albums_by_ids(album_ids)

                track_data_list: List[Dict[str, Any]] = []
                for track in batch_tracks:
                    artist = artists_map.get(track.artist_id)
                    album = albums_map.get(track.album_id)

                    track_data: Dict[str, Any] = {
                        "id": track.id,
                        "title": track.title,
                        "artist": artist.name if artist else "Unknown",
                        "album": album.title if album else "Unknown",
                        "genre": track.genre or "",
                        "year": track.year or "",
                        "tags": track.tags or "",
                        "environments": track.environments or "",
                        "instruments": track.instruments or "",
                        "musicbrainz_genres": track.musicbrainz_genres or "",
                        "recording_type": track.recording_type or "",
                    }
                    track_data_list.append(track_data)

                # Fetch audio features for this batch if available
                batch_track_ids: List[int] = [
                    td["id"] for td in track_data_list if td["id"] is not None
                ]
                audio_features_map = self.db.get_audio_features_by_track_ids(batch_track_ids)

                texts = [
                    create_track_text(td, audio_features_map.get(td["id"]))
                    for td in track_data_list
                ]
                logger.debug(
                    f"Generating embeddings for batch {batch_num}/{total_batches} ({len(texts)} tracks)"
                )

                embeddings = self.embedding_generator.generate_batch_embeddings(
                    texts, batch_size=50
                )

                batch_embedding_objects = []
                for track, embedding_vector in zip(batch_tracks, embeddings):
                    tid = track.id
                    if tid is None:
                        continue
                    batch_embedding_objects.append(
                        Embedding(
                            track_id=tid,
                            embedding_model=self.embedding_generator.provider_name,
                            embedding_dim=self.embedding_generator.get_dimension(),
                            vector=embedding_vector,
                        )
                    )
                    saved_vectors.append((tid, embedding_vector))
                    embeddings_saved += 1
                    progress.update(task, advance=1)

                    if progress_callback and embeddings_saved % 10 == 0:
                        current_progress = progress_start + (progress_end - progress_start) * (
                            embeddings_saved / len(tracks_needing_embeddings)
                        )
                        progress_callback(
                            current_progress,
                            f"Generating embeddings... ({embeddings_saved}/{len(tracks_needing_embeddings)})",
                        )

                self.db.insert_embeddings_batch(batch_embedding_objects)

                logger.debug(f"Completed batch {batch_num}/{total_batches}")

        except KeyboardInterrupt:
            logger.warning(
                f"Embedding generation interrupted. Saved {embeddings_saved} embeddings."
            )
            raise

        # Use incremental FAISS updates if index already exists with correct dimension
        if self.vector_index.index is not None and not self.vector_index.dimension_mismatch:
            if saved_vectors:
                new_track_ids = [tv[0] for tv in saved_vectors]
                new_embeddings = [tv[1] for tv in saved_vectors]
                # update_vectors removes old entries first, avoiding duplicates
                self.vector_index.update_vectors(new_embeddings, new_track_ids)
                logger.info(f"Incrementally updated {len(new_embeddings)} vectors in FAISS index")
        else:
            # Full rebuild needed (new index or dimension mismatch)
            all_embeddings = self.db.get_all_embeddings()
            track_ids = [emb[0] for emb in all_embeddings]
            vectors = [emb[1] for emb in all_embeddings]
            self.vector_index.build_index(vectors, track_ids)
            logger.info(f"Rebuilt FAISS index with {len(track_ids)} vectors")

        self.vector_index.save_index(str(self.vector_index.index_path))

        progress.update(task, description=f"Generated {embeddings_saved} embeddings")
        logger.info(f"Generated embeddings for {embeddings_saved} tracks")

    def _generate_tags_for_untagged_tracks(
        self,
        progress: Progress,
        task: Any,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
    ) -> None:
        if not self.tag_generator:
            return

        all_tracks = self.db.get_all_tracks()
        tracks_needing_tags = []

        for track in all_tracks:
            if not track.tags and not track.environments and not track.instruments:
                tracks_needing_tags.append(track)

        if not tracks_needing_tags:
            progress.update(task, description="No tracks need AI tags")
            return

        progress.update(task, total=len(tracks_needing_tags))
        logger.info(f"Generating AI tags for {len(tracks_needing_tags)} tracks")

        # Bulk fetch all artists and albums (eliminates N+1)
        artist_ids = list(set(t.artist_id for t in tracks_needing_tags if t.artist_id))
        album_ids = list(set(t.album_id for t in tracks_needing_tags if t.album_id))
        artists_map = self.db.get_artists_by_ids(artist_ids)
        albums_map = self.db.get_albums_by_ids(album_ids)

        track_data_list = []
        for track in tracks_needing_tags:
            artist = artists_map.get(track.artist_id)
            album = albums_map.get(track.album_id)

            track_data: Dict[str, Any] = {
                "id": track.id,
                "title": track.title,
                "artist": artist.name if artist else "Unknown",
                "album": album.title if album else "Unknown",
                "genre": track.genre or "",
            }
            if track.musicbrainz_genres:
                track_data["musicbrainz_genres"] = track.musicbrainz_genres
            if track.recording_type:
                track_data["recording_type"] = track.recording_type
            track_data_list.append(track_data)

        def tag_progress_callback(batch_num: int, total_batches: int, tracks_tagged: int) -> None:
            current_progress = progress_start + (progress_end - progress_start) * (
                tracks_tagged / len(tracks_needing_tags)
            )
            if progress_callback:
                progress_callback(
                    current_progress,
                    f"Generating AI tags... ({tracks_tagged}/{len(tracks_needing_tags)})",
                )
            progress.update(task, completed=tracks_tagged)

        try:
            tag_results = self.tag_generator.generate_tags_batch(
                track_data_list,
                batch_size=20,
                progress_callback=tag_progress_callback,
                cancel_event=cancel_event,
            )

            tags_saved = 0
            for track in tracks_needing_tags:
                if track.id in tag_results:
                    result = tag_results[track.id]
                    self.db.update_track_tags(
                        track.id,
                        tags=",".join(result.get("tags", [])),
                        environments=",".join(result.get("environments", [])),
                        instruments=",".join(result.get("instruments", [])),
                    )
                    tags_saved += 1

            progress.update(task, description=f"Generated tags for {tags_saved} tracks")
            logger.info(f"Generated AI tags for {tags_saved} tracks")

        except KeyboardInterrupt:
            logger.warning("Tag generation interrupted by user")
            raise
        except Exception as e:
            logger.error(f"Tag generation failed: {e}")
            progress.update(task, description="Tag generation failed")

    def _enrich_musicbrainz(
        self,
        progress: Progress,
        task: Any,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
    ) -> None:
        if not self.musicbrainz_settings:
            return

        try:
            from ..services.musicbrainz_service import get_enrichable_tracks, enrich_tracks

            tracks = get_enrichable_tracks(self.db)
            if not tracks:
                progress.update(task, description="No tracks need MusicBrainz enrichment")
                return

            progress.update(task, total=len(tracks))
            logger.info(f"Enriching {len(tracks)} tracks with MusicBrainz data")

            def mb_progress_callback(
                enriched: int, cached: int, mb_errors: int, total: int
            ) -> None:
                processed = enriched + cached + mb_errors
                current_progress = progress_start + (progress_end - progress_start) * (
                    processed / total if total else 0
                )
                if progress_callback:
                    progress_callback(
                        current_progress,
                        f"MusicBrainz enrichment... ({processed}/{total})",
                    )
                progress.update(task, completed=processed)

            enriched, cached, errors = enrich_tracks(
                self.db,
                self.musicbrainz_settings,
                tracks,
                progress_callback=mb_progress_callback,
                cancel_event=cancel_event,
            )

            progress.update(
                task,
                description=f"MusicBrainz: {enriched} enriched, {cached} cached, {errors} errors",
            )
            logger.info(
                f"MusicBrainz enrichment: {enriched} enriched, {cached} cached, {errors} errors"
            )

        except ImportError:
            logger.warning("musicbrainzngs not available, skipping MusicBrainz enrichment")
            progress.update(task, description="MusicBrainz: library not available")
        except Exception as e:
            logger.error(f"MusicBrainz enrichment failed: {e}")
            progress.update(task, description="MusicBrainz enrichment failed")
