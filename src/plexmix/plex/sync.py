from typing import Optional, Dict, Any
import logging
from datetime import datetime
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

from ..plex.client import PlexClient
from ..database.sqlite_manager import SQLiteManager
from ..database.models import Artist, Album, Track, Genre, SyncHistory
from ..database.vector_index import VectorIndex
from ..utils.embeddings import EmbeddingGenerator, create_track_text

logger = logging.getLogger(__name__)


class SyncEngine:
    def __init__(
        self,
        plex_client: PlexClient,
        db_manager: SQLiteManager,
        embedding_generator: Optional[EmbeddingGenerator] = None,
        vector_index: Optional[VectorIndex] = None
    ):
        self.plex = plex_client
        self.db = db_manager
        self.embedding_generator = embedding_generator
        self.vector_index = vector_index

    def full_sync(self, generate_embeddings: bool = True) -> SyncHistory:
        logger.info("Starting full library sync")
        stats = {
            'tracks_added': 0,
            'tracks_updated': 0,
            'tracks_removed': 0,
            'artists_processed': 0,
            'albums_processed': 0
        }

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
            ) as progress:
                task = progress.add_task("Syncing artists...", total=None)
                artist_map = self._sync_artists(progress, task)
                stats['artists_processed'] = len(artist_map)

                task = progress.add_task("Syncing albums...", total=None)
                album_map = self._sync_albums(progress, task, artist_map)
                stats['albums_processed'] = len(album_map)

                task = progress.add_task("Syncing tracks...", total=None)
                track_stats = self._sync_tracks(progress, task, artist_map, album_map)
                stats.update(track_stats)

                if generate_embeddings and self.embedding_generator and self.vector_index:
                    task = progress.add_task("Generating embeddings...", total=None)
                    self._generate_embeddings_for_new_tracks(progress, task)

            sync_record = SyncHistory(
                tracks_added=stats['tracks_added'],
                tracks_updated=stats['tracks_updated'],
                tracks_removed=stats['tracks_removed'],
                status='success'
            )
            self.db.insert_sync_record(sync_record)

            logger.info(
                f"Full sync completed: {stats['tracks_added']} added, "
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
            logger.error(f"Full sync failed: {e}")
            sync_record = SyncHistory(
                status='failed',
                error_message=str(e)
            )
            self.db.insert_sync_record(sync_record)
            raise

    def _sync_artists(self, progress: Progress, task) -> Dict[str, int]:
        artist_map = {}

        for artist_batch in self.plex.get_all_artists(batch_size=100):
            for artist in artist_batch:
                existing = self.db.get_artist_by_plex_key(artist.plex_key)
                if existing:
                    artist.id = existing.id
                    artist_id = existing.id
                else:
                    artist_id = self.db.insert_artist(artist)

                artist_map[artist.plex_key] = artist_id

            progress.update(task, advance=len(artist_batch))

        progress.update(task, description=f"Synced {len(artist_map)} artists")
        return artist_map

    def _sync_albums(self, progress: Progress, task, artist_map: Dict[str, int]) -> Dict[str, int]:
        album_map = {}

        for album_batch in self.plex.get_all_albums(batch_size=100):
            for album in album_batch:
                artist_plex_key = album.plex_key.rsplit('/', 1)[0] if '/' in album.plex_key else None
                if artist_plex_key and artist_plex_key in artist_map:
                    album.artist_id = artist_map[artist_plex_key]
                else:
                    album.artist_id = 1

                existing = self.db.get_album_by_plex_key(album.plex_key)
                if existing:
                    album.id = existing.id
                    album_id = existing.id
                else:
                    album_id = self.db.insert_album(album)

                album_map[album.plex_key] = album_id

            progress.update(task, advance=len(album_batch))

        progress.update(task, description=f"Synced {len(album_map)} albums")
        return album_map

    def _sync_tracks(
        self,
        progress: Progress,
        task,
        artist_map: Dict[str, int],
        album_map: Dict[str, int]
    ) -> Dict[str, int]:
        stats = {'tracks_added': 0, 'tracks_updated': 0, 'tracks_removed': 0}
        plex_track_keys = set()

        for track_batch in self.plex.get_all_tracks(batch_size=100):
            for track in track_batch:
                plex_track_keys.add(track.plex_key)

                # Use the artist/album rating keys stored by extract_track_metadata
                artist_plex_key = track.__dict__.get('_artist_key')
                album_plex_key = track.__dict__.get('_album_key')

                if artist_plex_key and artist_plex_key in artist_map:
                    track.artist_id = artist_map[artist_plex_key]
                else:
                    track.artist_id = 1

                if album_plex_key and album_plex_key in album_map:
                    track.album_id = album_map[album_plex_key]
                else:
                    track.album_id = 1

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

            progress.update(task, advance=len(track_batch))

        existing_tracks = self.db.get_all_tracks()
        for existing_track in existing_tracks:
            if existing_track.plex_key not in plex_track_keys:
                self.db.delete_track(existing_track.id)
                stats['tracks_removed'] += 1

        progress.update(task, description=f"Synced {stats['tracks_added'] + stats['tracks_updated']} tracks")
        return stats

    def _generate_embeddings_for_new_tracks(self, progress: Progress, task) -> None:
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
                batch_tracks = tracks_needing_embeddings[i:i + batch_size]
                batch_num = i // batch_size + 1

                track_data_list = []
                for track in batch_tracks:
                    artist = self.db.get_artist_by_id(track.artist_id)
                    album = self.db.get_album_by_id(track.album_id)

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

                logger.debug(f"Completed batch {batch_num}/{total_batches}")

        except KeyboardInterrupt:
            logger.warning(f"Embedding generation interrupted. Saved {embeddings_saved} embeddings.")
            raise

        all_embeddings = self.db.get_all_embeddings()
        track_ids = [emb[0] for emb in all_embeddings]
        vectors = [emb[1] for emb in all_embeddings]

        self.vector_index.build_index(vectors, track_ids)
        self.vector_index.save_index(str(self.vector_index.index_path))

        progress.update(task, description=f"Generated {embeddings_saved} embeddings")
        logger.info(f"Generated embeddings for {embeddings_saved} tracks")
