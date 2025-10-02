from plexapi.server import PlexServer
from plexapi.exceptions import Unauthorized, NotFound, BadRequest
from plexapi.library import MusicSection
from typing import List, Optional, Generator
import logging
import time

from ..database.models import Artist, Album, Track

logger = logging.getLogger(__name__)


class PlexClient:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self.server: Optional[PlexServer] = None
        self.music_library: Optional[MusicSection] = None

    def connect(self) -> bool:
        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                self.server = PlexServer(self.url, self.token)
                logger.info(f"Connected to Plex server: {self.server.friendlyName}")
                return True
            except Unauthorized:
                logger.error("Plex authentication failed: Invalid token")
                return False
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Connection attempt {attempt + 1} failed: {e}. Retrying...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error(f"Failed to connect to Plex server after {max_retries} attempts: {e}")
                    return False

        return False

    def test_connection(self) -> bool:
        try:
            if self.server is None:
                return self.connect()
            self.server.library.sections()
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False

    def get_music_libraries(self) -> List[str]:
        if not self.server:
            if not self.connect():
                return []

        try:
            sections = self.server.library.sections()
            music_sections = [section.title for section in sections if section.type == 'artist']
            logger.info(f"Found {len(music_sections)} music libraries")
            return music_sections
        except Exception as e:
            logger.error(f"Failed to retrieve music libraries: {e}")
            return []

    def select_library(self, name_or_index: str | int) -> bool:
        if not self.server:
            if not self.connect():
                return False

        try:
            if isinstance(name_or_index, int):
                libraries = self.get_music_libraries()
                if 0 <= name_or_index < len(libraries):
                    library_name = libraries[name_or_index]
                else:
                    logger.error(f"Invalid library index: {name_or_index}")
                    return False
            else:
                library_name = name_or_index

            self.music_library = self.server.library.section(library_name)
            logger.info(f"Selected music library: {library_name}")
            return True
        except NotFound:
            logger.error(f"Music library not found: {name_or_index}")
            return False
        except Exception as e:
            logger.error(f"Failed to select library: {e}")
            return False

    def get_all_artists(self, batch_size: int = 100) -> Generator[List[Artist], None, None]:
        if not self.music_library:
            logger.error("No music library selected")
            return

        try:
            all_artists = self.music_library.searchArtists()
            logger.info(f"Found {len(all_artists)} artists")

            batch = []
            for plex_artist in all_artists:
                try:
                    artist = self.extract_artist_metadata(plex_artist)
                    batch.append(artist)

                    if len(batch) >= batch_size:
                        yield batch
                        batch = []
                except ValueError as e:
                    logger.debug(f"Skipping artist: {e}")
                    continue
                except Exception as e:
                    logger.warning(f"Failed to extract artist metadata: {e}")
                    continue

            if batch:
                yield batch

        except Exception as e:
            logger.error(f"Failed to retrieve artists: {e}")

    def get_all_albums(self, batch_size: int = 100) -> Generator[List[Album], None, None]:
        if not self.music_library:
            logger.error("No music library selected")
            return

        try:
            all_albums = self.music_library.searchAlbums()
            logger.info(f"Found {len(all_albums)} albums")

            batch = []
            for plex_album in all_albums:
                try:
                    album = self.extract_album_metadata(plex_album)
                    batch.append(album)

                    if len(batch) >= batch_size:
                        yield batch
                        batch = []
                except ValueError as e:
                    logger.debug(f"Skipping album: {e}")
                    continue
                except Exception as e:
                    logger.warning(f"Failed to extract album metadata: {e}")
                    continue

            if batch:
                yield batch

        except Exception as e:
            logger.error(f"Failed to retrieve albums: {e}")

    def get_all_tracks(self, batch_size: int = 100) -> Generator[List[Track], None, None]:
        if not self.music_library:
            logger.error("No music library selected")
            return

        try:
            all_tracks = self.music_library.searchTracks()
            logger.info(f"Found {len(all_tracks)} tracks")

            batch = []
            for plex_track in all_tracks:
                try:
                    track = self.extract_track_metadata(plex_track)
                    batch.append(track)

                    if len(batch) >= batch_size:
                        yield batch
                        batch = []
                except ValueError as e:
                    logger.debug(f"Skipping track: {e}")
                    continue
                except Exception as e:
                    logger.warning(f"Failed to extract track metadata: {e}")
                    continue

            if batch:
                yield batch

        except Exception as e:
            logger.error(f"Failed to retrieve tracks: {e}")

    def extract_artist_metadata(self, plex_artist) -> Artist:
        if not plex_artist.title or not plex_artist.title.strip():
            raise ValueError(f"Artist has empty name (key: {plex_artist.ratingKey})")

        genres = [genre.tag for genre in plex_artist.genres] if hasattr(plex_artist, 'genres') else []
        genre_str = ", ".join(genres) if genres else None

        return Artist(
            plex_key=str(plex_artist.ratingKey),
            name=plex_artist.title,
            genre=genre_str,
            bio=plex_artist.summary if hasattr(plex_artist, 'summary') else None
        )

    def extract_album_metadata(self, plex_album) -> Album:
        if not plex_album.title or not plex_album.title.strip():
            raise ValueError(f"Album has empty title (key: {plex_album.ratingKey})")

        genres = [genre.tag for genre in plex_album.genres] if hasattr(plex_album, 'genres') else []
        genre_str = ", ".join(genres) if genres else None

        artist = plex_album.artist() if hasattr(plex_album, 'artist') else None
        artist_key = str(artist.ratingKey) if artist else "unknown"

        return Album(
            plex_key=str(plex_album.ratingKey),
            title=plex_album.title,
            artist_id=0,
            year=plex_album.year if hasattr(plex_album, 'year') else None,
            genre=genre_str,
            cover_art_url=plex_album.thumb if hasattr(plex_album, 'thumb') else None
        )

    def extract_track_metadata(self, plex_track) -> Track:
        if not plex_track.title or not plex_track.title.strip():
            raise ValueError(f"Track has empty title (key: {plex_track.ratingKey})")

        genres = [genre.tag for genre in plex_track.genres] if hasattr(plex_track, 'genres') else []
        genre_str = ", ".join(genres) if genres else None

        # Get artist and album IDs directly from Plex relationships
        artist = plex_track.artist() if hasattr(plex_track, 'artist') else None
        album = plex_track.album() if hasattr(plex_track, 'album') else None

        track = Track(
            plex_key=str(plex_track.ratingKey),
            title=plex_track.title,
            artist_id=0,  # Will be set in sync based on artist rating key
            album_id=0,  # Will be set in sync based on album rating key
            duration_ms=plex_track.duration if hasattr(plex_track, 'duration') else None,
            genre=genre_str,
            year=plex_track.year if hasattr(plex_track, 'year') else None,
            rating=plex_track.userRating if hasattr(plex_track, 'userRating') else None,
            play_count=plex_track.viewCount if hasattr(plex_track, 'viewCount') else 0,
            last_played=plex_track.lastViewedAt if hasattr(plex_track, 'lastViewedAt') else None,
            file_path=None
        )

        # Store rating keys as temporary attributes (not part of model schema)
        track.__dict__['_artist_key'] = str(artist.ratingKey) if artist else None
        track.__dict__['_album_key'] = str(album.ratingKey) if album else None

        return track

    def create_playlist(self, name: str, track_ids: List[int], description: Optional[str] = None):
        if not self.music_library:
            logger.error("No music library selected")
            return None

        try:
            tracks = []
            for track_id in track_ids:
                try:
                    track = self.music_library.fetchItem(track_id)
                    tracks.append(track)
                except Exception as e:
                    logger.warning(f"Failed to fetch track {track_id}: {e}")

            if not tracks:
                logger.error("No valid tracks found for playlist")
                return None

            playlist = self.server.createPlaylist(title=name, items=tracks)

            if description and hasattr(playlist, 'editSummary'):
                playlist.editSummary(description)

            logger.info(f"Created playlist '{name}' with {len(tracks)} tracks")
            return playlist

        except Exception as e:
            logger.error(f"Failed to create playlist: {e}")
            return None
