from typing import Optional, Dict, Any, Callable
import logging
from datetime import datetime
from threading import Event
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

from ..plex.client import PlexClient
from ..database.sqlite_manager import SQLiteManager
from ..database.models import Artist, Album, Track, Genre, SyncHistory
from ..database.vector_index import VectorIndex
from ..utils.embeddings import EmbeddingGenerator, create_track_text
from ..ai.tag_generator import TagGenerator
from ..ai.base import AIProvider

logger = logging.getLogger(__name__)


class SyncEngine:
    def __init__(
        self,
        plex_client: PlexClient,
        db_manager: SQLiteManager,
        embedding_generator: Optional[EmbeddingGenerator] = None,
        vector_index: Optional[VectorIndex] = None,
        ai_provider: Optional[AIProvider] = None
    ):
        self.plex = plex_client
        self.db = db_manager
        self.embedding_generator = embedding_generator
        self.vector_index = vector_index
        self.ai_provider = ai_provider
        self.tag_generator = TagGenerator(ai_provider) if ai_provider else None

    def incremental_sync(
        self,
        generate_embeddings: bool = True,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None
    ) -> SyncHistory:
        logger.info("Starting incremental library sync")
        stats = {
            'tracks_added': 0,
            'tracks_updated': 0,
            'tracks_removed': 0,
            'artists_processed': 0,
            'albums_processed': 0
        }

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
                plex_library = self._build_plex_library_index(progress, task, progress_callback, cancel_event, 0.0, 0.3)

                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                task = progress.add_task("Comparing with database...", total=None)
                changes = self._detect_library_changes(plex_library, progress, task, progress_callback, cancel_event, 0.3, 0.5)
                stats.update(changes)

                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                if self.tag_generator and self.ai_provider:
                    task = progress.add_task("Generating AI tags...", total=None)
                    self._generate_tags_for_untagged_tracks(progress, task, progress_callback, cancel_event, 0.5, 0.7)

                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                if generate_embeddings and self.embedding_generator and self.vector_index:
                    task = progress.add_task("Generating embeddings...", total=None)
                    self._generate_embeddings_for_new_tracks(progress, task, progress_callback, cancel_event, 0.7, 1.0)

            sync_record = SyncHistory(
                tracks_added=stats['tracks_added'],
                tracks_updated=stats['tracks_updated'],
                tracks_removed=stats['tracks_removed'],
                status='success'
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
                tracks_added=stats['tracks_added'],
                tracks_updated=stats['tracks_updated'],
                tracks_removed=stats['tracks_removed'],
                status='interrupted',
                error_message='User interrupted sync'
            )
            self.db.insert_sync_record(sync_record)
            raise

        except Exception as e:
            logger.error(f"Incremental sync failed: {e}")
            sync_record = SyncHistory(
                status='failed',
                error_message=str(e)
            )
            self.db.insert_sync_record(sync_record)
            raise

    def regenerate_sync(
        self,
        generate_embeddings: bool = True,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None
    ) -> SyncHistory:
        logger.info("Starting regenerate sync - will clear and regenerate all tags and embeddings")

        if progress_callback:
            progress_callback(0.0, "Clearing existing tags and embeddings...")

        cursor = self.db.get_connection().cursor()
        cursor.execute('UPDATE tracks SET tags = NULL, environments = NULL, instruments = NULL')
        cursor.execute('DELETE FROM embeddings')
        self.db.get_connection().commit()
        logger.info("Cleared all existing tags and embeddings")

        return self.incremental_sync(
            generate_embeddings=generate_embeddings,
            progress_callback=progress_callback,
            cancel_event=cancel_event
        )

    def full_sync(
        self,
        generate_embeddings: bool = True,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None
    ) -> SyncHistory:
        logger.info("Starting full library sync (alias for incremental_sync)")
        return self.incremental_sync(
            generate_embeddings=generate_embeddings,
            progress_callback=progress_callback,
            cancel_event=cancel_event
        )

    def _build_plex_library_index(
        self,
        progress: Progress,
        task,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0
    ) -> Dict[str, Any]:
        plex_library = {
            'artists': {},
            'albums': {},
            'tracks': {}
        }

        total_items = 0
        for artist_batch in self.plex.get_all_artists(batch_size=100):
            if cancel_event and cancel_event.is_set():
                break

            for artist in artist_batch:
                plex_library['artists'][artist.plex_key] = artist

            total_items += len(artist_batch)
            progress.update(task, advance=len(artist_batch))

        for album_batch in self.plex.get_all_albums(batch_size=100):
            if cancel_event and cancel_event.is_set():
                break

            for album in album_batch:
                plex_library['albums'][album.plex_key] = album

            total_items += len(album_batch)
            progress.update(task, advance=len(album_batch))

        for track_batch in self.plex.get_all_tracks(batch_size=100):
            if cancel_event and cancel_event.is_set():
                break

            for track in track_batch:
                plex_library['tracks'][track.plex_key] = track

            total_items += len(track_batch)
            progress.update(task, advance=len(track_batch))

            if progress_callback and total_items % 100 == 0:
                current_progress = progress_start + (progress_end - progress_start) * 0.5
                progress_callback(current_progress, f"Indexed {total_items} items from Plex...")

        progress.update(task, description=f"Indexed {len(plex_library['artists'])} artists, {len(plex_library['albums'])} albums, {len(plex_library['tracks'])} tracks")
        return plex_library

    def _get_or_create_unknown_entities(self) -> tuple[int, int]:
        """Ensure 'Unknown' artist and album exist and return their IDs."""
        # Get or create Unknown artist
        unknown_artist = self.db.get_artist_by_plex_key("__unknown__")
        if unknown_artist:
            unknown_artist_id = unknown_artist.id
        else:
            unknown_artist = Artist(plex_key="__unknown__", name="Unknown Artist")
            unknown_artist_id = self.db.insert_artist(unknown_artist)
            logger.info("Created 'Unknown Artist' entity for orphaned items")

        # Get or create Unknown album (linked to Unknown artist)
        unknown_album = self.db.get_album_by_plex_key("__unknown__")
        if unknown_album:
            unknown_album_id = unknown_album.id
        else:
            unknown_album = Album(
                plex_key="__unknown__",
                title="Unknown Album",
                artist_id=unknown_artist_id
            )
            unknown_album_id = self.db.insert_album(unknown_album)
            logger.info("Created 'Unknown Album' entity for orphaned items")

        return unknown_artist_id, unknown_album_id

    def _detect_library_changes(
        self,
        plex_library: Dict[str, Any],
        progress: Progress,
        task,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0
    ) -> Dict[str, int]:
        stats = {'tracks_added': 0, 'tracks_updated': 0, 'tracks_removed': 0}

        # Ensure Unknown entities exist for orphaned items
        unknown_artist_id, unknown_album_id = self._get_or_create_unknown_entities()

        db_artists = {a.plex_key: a for a in self.db.get_all_artists()}
        db_albums = {a.plex_key: a for a in self.db.get_all_albums()}
        db_tracks = {t.plex_key: t for t in self.db.get_all_tracks()}

        artist_map = {}
        for plex_key, plex_artist in plex_library['artists'].items():
            if plex_key in db_artists:
                artist_map[plex_key] = db_artists[plex_key].id
            else:
                artist_id = self.db.insert_artist(plex_artist)
                artist_map[plex_key] = artist_id

        album_map = {}
        for plex_key, plex_album in plex_library['albums'].items():
            # Use the artist key from Plex API (stored as _artist_key)
            artist_plex_key = plex_album.__dict__.get('_artist_key')
            if artist_plex_key and artist_plex_key in artist_map:
                plex_album.artist_id = artist_map[artist_plex_key]
            else:
                logger.warning(f"Album '{plex_album.title}' missing artist link; assigning to Unknown Artist")
                plex_album.artist_id = unknown_artist_id

            if plex_key in db_albums:
                album_map[plex_key] = db_albums[plex_key].id
            else:
                album_id = self.db.insert_album(plex_album)
                album_map[plex_key] = album_id

        total_tracks = len(plex_library['tracks'])
        processed = 0

        for plex_key, plex_track in plex_library['tracks'].items():
            if cancel_event and cancel_event.is_set():
                break

            artist_plex_key = plex_track.__dict__.get('_artist_key')
            album_plex_key = plex_track.__dict__.get('_album_key')

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
                    stats['tracks_updated'] += 1
            else:
                self.db.insert_track(plex_track)
                stats['tracks_added'] += 1

            if plex_track.genre:
                for genre_name in plex_track.genre.split(','):
                    genre_name = genre_name.strip().lower()
                    genre = self.db.get_genre_by_name(genre_name)
                    if not genre:
                        genre = Genre(name=genre_name)
                        self.db.insert_genre(genre)

            processed += 1
            if processed % 10 == 0:
                progress.update(task, advance=10)
                if progress_callback:
                    current_progress = progress_start + (progress_end - progress_start) * (processed / total_tracks)
                    progress_callback(current_progress, f"Processing tracks... ({processed}/{total_tracks})")

        for db_plex_key in db_tracks.keys():
            if db_plex_key not in plex_library['tracks']:
                self.db.delete_track(db_tracks[db_plex_key].id)
                stats['tracks_removed'] += 1

        progress.update(task, description=f"Changes: +{stats['tracks_added']} ~{stats['tracks_updated']} -{stats['tracks_removed']}")
        return stats

    def _track_needs_update(self, db_track: Track, plex_track: Track) -> bool:
        return (
            db_track.title != plex_track.title or
            db_track.year != plex_track.year or
            db_track.genre != plex_track.genre or
            db_track.duration_ms != plex_track.duration_ms or
            db_track.rating != plex_track.rating or
            db_track.play_count != plex_track.play_count or
            db_track.last_played != plex_track.last_played
        )

    def _sync_artists(
        self,
        progress: Progress,
        task,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0
    ) -> Dict[str, int]:
        artist_map = {}
        total_artists = 0

        for artist_batch in self.plex.get_all_artists(batch_size=100):
            if cancel_event and cancel_event.is_set():
                break

            for artist in artist_batch:
                existing = self.db.get_artist_by_plex_key(artist.plex_key)
                if existing:
                    artist.id = existing.id
                    artist_id = existing.id
                else:
                    artist_id = self.db.insert_artist(artist)

                artist_map[artist.plex_key] = artist_id

            total_artists += len(artist_batch)
            progress.update(task, advance=len(artist_batch))

            if progress_callback and total_artists % 10 == 0:
                current_progress = progress_start + (progress_end - progress_start) * 0.5
                progress_callback(current_progress, f"Syncing artists... ({total_artists} processed)")

        progress.update(task, description=f"Synced {len(artist_map)} artists")
        return artist_map

    def _sync_albums(
        self,
        progress: Progress,
        task,
        artist_map: Dict[str, int],
        unknown_artist_id: int,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0
    ) -> Dict[str, int]:
        album_map = {}
        total_albums = 0

        for album_batch in self.plex.get_all_albums(batch_size=100):
            if cancel_event and cancel_event.is_set():
                break

            for album in album_batch:
                # Use the artist key from Plex API (stored as _artist_key)
                artist_plex_key = album.__dict__.get('_artist_key')
                if artist_plex_key and artist_plex_key in artist_map:
                    album.artist_id = artist_map[artist_plex_key]
                else:
                    album.artist_id = unknown_artist_id

                existing = self.db.get_album_by_plex_key(album.plex_key)
                if existing:
                    album.id = existing.id
                    album_id = existing.id
                else:
                    album_id = self.db.insert_album(album)

                album_map[album.plex_key] = album_id

            total_albums += len(album_batch)
            progress.update(task, advance=len(album_batch))

            if progress_callback and total_albums % 10 == 0:
                current_progress = progress_start + (progress_end - progress_start) * 0.5
                progress_callback(current_progress, f"Syncing albums... ({total_albums} processed)")

        progress.update(task, description=f"Synced {len(album_map)} albums")
        return album_map

    def _sync_tracks(
        self,
        progress: Progress,
        task,
        artist_map: Dict[str, int],
        album_map: Dict[str, int],
        unknown_artist_id: int,
        unknown_album_id: int,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0
    ) -> Dict[str, int]:
        stats = {'tracks_added': 0, 'tracks_updated': 0, 'tracks_removed': 0}
        plex_track_keys = set()
        total_processed = 0

        for track_batch in self.plex.get_all_tracks(batch_size=100):
            if cancel_event and cancel_event.is_set():
                break

            for track in track_batch:
                plex_track_keys.add(track.plex_key)

                artist_plex_key = track.__dict__.get('_artist_key')
                album_plex_key = track.__dict__.get('_album_key')

                if artist_plex_key and artist_plex_key in artist_map:
                    track.artist_id = artist_map[artist_plex_key]
                else:
                    track.artist_id = unknown_artist_id

                if album_plex_key and album_plex_key in album_map:
                    track.album_id = album_map[album_plex_key]
                else:
                    track.album_id = unknown_album_id

                existing = self.db.get_track_by_plex_key(track.plex_key)
                if existing:
                    track.id = existing.id
                    self.db.insert_track(track)
                    stats['tracks_updated'] += 1
                else:
                    self.db.insert_track(track)
                    stats['tracks_added'] += 1

                if track.genre:
                    for genre_name in track.genre.split(','):
                        genre_name = genre_name.strip().lower()
                        genre = self.db.get_genre_by_name(genre_name)
                        if not genre:
                            genre = Genre(name=genre_name)
                            self.db.insert_genre(genre)

            total_processed += len(track_batch)
            progress.update(task, advance=len(track_batch))

            if progress_callback and total_processed % 10 == 0:
                current_progress = progress_start + (progress_end - progress_start) * 0.6
                progress_callback(current_progress, f"Syncing tracks... ({total_processed} processed)")

        existing_tracks = self.db.get_all_tracks()
        for existing_track in existing_tracks:
            if existing_track.plex_key not in plex_track_keys:
                self.db.delete_track(existing_track.id)
                stats['tracks_removed'] += 1

        progress.update(task, description=f"Synced {stats['tracks_added'] + stats['tracks_updated']} tracks")
        return stats

    def _generate_embeddings_for_new_tracks(
        self,
        progress: Progress,
        task,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0
    ) -> None:
        if not self.embedding_generator or not self.vector_index:
            return

        all_tracks = self.db.get_all_tracks()
        tracks_needing_embeddings = []

        for track in all_tracks:
            existing_embedding = self.db.get_embedding_by_track_id(track.id)
            if not existing_embedding:
                tracks_needing_embeddings.append(track)

        if not tracks_needing_embeddings:
            progress.update(task, description="No new tracks need embeddings")
            return

        progress.update(task, total=len(tracks_needing_embeddings))
        logger.info(f"Generating embeddings for {len(tracks_needing_embeddings)} tracks")

        from ..database.models import Embedding

        batch_size = 50
        embeddings_saved = 0
        total_batches = (len(tracks_needing_embeddings) + batch_size - 1) // batch_size

        try:
            for i in range(0, len(tracks_needing_embeddings), batch_size):
                if cancel_event and cancel_event.is_set():
                    logger.warning(f"Embedding generation cancelled. Saved {embeddings_saved} embeddings.")
                    break

                batch_tracks = tracks_needing_embeddings[i:i + batch_size]
                batch_num = i // batch_size + 1

                # Bulk fetch artists and albums for this batch (eliminates N+1)
                artist_ids = list(set(t.artist_id for t in batch_tracks if t.artist_id))
                album_ids = list(set(t.album_id for t in batch_tracks if t.album_id))
                artists_map = self.db.get_artists_by_ids(artist_ids)
                albums_map = self.db.get_albums_by_ids(album_ids)

                track_data_list = []
                for track in batch_tracks:
                    artist = artists_map.get(track.artist_id)
                    album = albums_map.get(track.album_id)

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
                    track_data_list.append(track_data)

                texts = [create_track_text(td) for td in track_data_list]
                logger.debug(f"Generating embeddings for batch {batch_num}/{total_batches} ({len(texts)} tracks)")

                embeddings = self.embedding_generator.generate_batch_embeddings(texts, batch_size=50)

                for track, embedding_vector in zip(batch_tracks, embeddings):
                    embedding = Embedding(
                        track_id=track.id,
                        embedding_model=self.embedding_generator.provider_name,
                        embedding_dim=self.embedding_generator.get_dimension(),
                        vector=embedding_vector
                    )
                    self.db.insert_embedding(embedding)
                    embeddings_saved += 1
                    progress.update(task, advance=1)

                    if progress_callback and embeddings_saved % 10 == 0:
                        current_progress = progress_start + (progress_end - progress_start) * (embeddings_saved / len(tracks_needing_embeddings))
                        progress_callback(current_progress, f"Generating embeddings... ({embeddings_saved}/{len(tracks_needing_embeddings)})")

                logger.debug(f"Completed batch {batch_num}/{total_batches}")

        except KeyboardInterrupt:
            logger.warning(f"Embedding generation interrupted. Saved {embeddings_saved} embeddings.")
            raise

        # Use incremental FAISS updates if index already exists with correct dimension
        if self.vector_index.index is not None and not self.vector_index.dimension_mismatch:
            # Get only the newly generated embeddings
            new_track_ids = [t.id for t in tracks_needing_embeddings[:embeddings_saved]]
            new_embeddings = []
            for track_id in new_track_ids:
                emb = self.db.get_embedding_by_track_id(track_id)
                if emb:
                    new_embeddings.append(emb.vector)

            if new_embeddings:
                self.vector_index.add_vectors(new_embeddings, new_track_ids)
                logger.info(f"Incrementally added {len(new_embeddings)} vectors to FAISS index")
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
        task,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0
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

            track_data = {
                'id': track.id,
                'title': track.title,
                'artist': artist.name if artist else 'Unknown',
                'album': album.title if album else 'Unknown',
                'genre': track.genre or ''
            }
            track_data_list.append(track_data)

        def tag_progress_callback(batch_num: int, total_batches: int, tracks_tagged: int):
            current_progress = progress_start + (progress_end - progress_start) * (tracks_tagged / len(tracks_needing_tags))
            if progress_callback:
                progress_callback(current_progress, f"Generating AI tags... ({tracks_tagged}/{len(tracks_needing_tags)})")
            progress.update(task, completed=tracks_tagged)

        try:
            tag_results = self.tag_generator.generate_tags_batch(
                track_data_list,
                batch_size=20,
                progress_callback=tag_progress_callback,
                cancel_event=cancel_event
            )

            tags_saved = 0
            for track in tracks_needing_tags:
                if track.id in tag_results:
                    result = tag_results[track.id]
                    track.tags = ','.join(result.get('tags', []))
                    track.environments = ','.join(result.get('environments', []))
                    track.instruments = ','.join(result.get('instruments', []))
                    self.db.insert_track(track)
                    tags_saved += 1

            progress.update(task, description=f"Generated tags for {tags_saved} tracks")
            logger.info(f"Generated AI tags for {tags_saved} tracks")

        except KeyboardInterrupt:
            logger.warning("Tag generation interrupted by user")
            raise
        except Exception as e:
            logger.error(f"Tag generation failed: {e}")
            progress.update(task, description="Tag generation failed")
