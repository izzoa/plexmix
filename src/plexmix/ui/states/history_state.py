import reflex as rx
import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from plexmix.ui.states.app_state import AppState

logger = logging.getLogger(__name__)


class HistoryState(AppState):
    # Playlist data - List of playlist dictionaries from database
    playlists: List[Dict[str, Any]] = []  # Contains Playlist model data as dicts
    selected_playlist: Optional[Dict[str, Any]] = None
    selected_playlist_tracks: List[Dict[str, Any]] = []

    # Modal visibility
    is_detail_modal_open: bool = False
    is_delete_confirmation_open: bool = False
    playlist_to_delete: Optional[int] = None

    # Sorting/filtering
    sort_by: str = "created_date"  # created_date, name, track_count
    sort_descending: bool = True

    # UI feedback
    loading_playlists: bool = False
    loading_details: bool = False
    deleting_playlist: bool = False
    exporting: bool = False
    action_message: str = ""
    error_message: str = ""

    def on_load(self):
        super().on_load()
        return HistoryState.load_playlists

    @rx.event(background=True)
    async def load_playlists(self):
        async with self:
            self.loading_playlists = True
            self.error_message = ""

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if db_path.exists():
                db = SQLiteManager(str(db_path))
                db.connect()

                # Get all playlists and convert to dicts
                playlist_objs = db.get_playlists()
                playlists = [p.model_dump() for p in playlist_objs]

                # Sort by created date (newest first) by default
                playlists.sort(
                    key=lambda p: p.get('created_at', ''),
                    reverse=True
                )

                db.close()

                async with self:
                    self.playlists = playlists
                    self.loading_playlists = False
            else:
                async with self:
                    self.playlists = []
                    self.loading_playlists = False

        except Exception as e:
            logger.error(f"Error loading playlists: {e}")
            async with self:
                self.playlists = []
                self.loading_playlists = False
                self.error_message = f"Error loading playlists: {str(e)}"

    def select_playlist(self, playlist_id: int):
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if db_path.exists():
                db = SQLiteManager(str(db_path))
                db.connect()

                # Get playlist details
                playlist = db.get_playlist_by_id(playlist_id)
                if playlist:
                    self.selected_playlist = playlist.model_dump()

                    # Get tracks for this playlist
                    tracks = db.get_playlist_tracks(playlist_id)

                    # Format track data for display
                    formatted_tracks = []
                    for i, track in enumerate(tracks):
                        duration_ms = track.get('duration_ms', 0)
                        if duration_ms:
                            minutes = duration_ms // 60000
                            seconds = (duration_ms // 1000) % 60
                            duration_formatted = f"{minutes}:{seconds:02d}"
                        else:
                            duration_formatted = "0:00"

                        formatted_tracks.append({
                            'position': i + 1,
                            'id': track.get('id'),
                            'title': track.get('title', 'Unknown'),
                            'artist': track.get('artist_name', 'Unknown'),
                            'album': track.get('album_title', 'Unknown'),
                            'duration_ms': duration_ms,
                            'duration_formatted': duration_formatted,
                            'genre': track.get('genre', ''),
                            'year': track.get('year', '')
                        })

                    self.selected_playlist_tracks = formatted_tracks

                    # Calculate and format total duration
                    total_duration_ms = sum(t.get('duration_ms', 0) for t in formatted_tracks)
                    total_minutes = total_duration_ms // 60000
                    total_seconds = (total_duration_ms // 1000) % 60
                    self.selected_playlist['total_duration_ms'] = total_duration_ms
                    self.selected_playlist['total_duration_formatted'] = f"{total_minutes}:{total_seconds:02d}"

                    # Open modal
                    self.is_detail_modal_open = True

                db.close()
        except Exception as e:
            logger.error(f"Error selecting playlist: {e}")
            self.action_message = f"Error loading playlist: {str(e)}"

    def close_detail_modal(self):
        self.is_detail_modal_open = False
        self.selected_playlist = None
        self.selected_playlist_tracks = []

    def set_detail_modal_open(self, is_open: bool):
        """Set the detail modal open state and clear data when closing."""
        self.is_detail_modal_open = is_open
        if not is_open:
            self.selected_playlist = None
            self.selected_playlist_tracks = []

    def set_error_message(self, message: str):
        """Set the error message state."""
        self.error_message = message

    def set_action_message(self, message: str):
        """Set the action message state."""
        self.action_message = message

    def show_delete_confirmation(self, playlist_id: int):
        self.playlist_to_delete = playlist_id
        self.is_delete_confirmation_open = True

    def cancel_delete(self):
        self.playlist_to_delete = None
        self.is_delete_confirmation_open = False

    @rx.event(background=True)
    async def confirm_delete(self):
        async with self:
            if self.playlist_to_delete is None:
                return

            playlist_id = self.playlist_to_delete
            self.is_delete_confirmation_open = False

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            db = SQLiteManager(str(db_path))
            db.connect()

            # Delete playlist
            db.delete_playlist(playlist_id)

            db.close()

            # Reload playlists - yield to let Reflex scheduler manage execution
            yield HistoryState.load_playlists()

            async with self:
                self.playlist_to_delete = None
                self.action_message = "Playlist deleted successfully!"

                # Close detail modal if it was open for this playlist
                if self.selected_playlist and self.selected_playlist.get('id') == playlist_id:
                    self.is_detail_modal_open = False
                    self.selected_playlist = None
                    self.selected_playlist_tracks = []

        except Exception as e:
            async with self:
                self.action_message = f"Error deleting playlist: {str(e)}"

    @rx.event(background=True)
    async def export_to_plex(self, playlist_id: int):
        async with self:
            self.action_message = "Exporting to Plex..."

        try:
            from plexmix.config.settings import Settings
            from plexmix.config.credentials import get_plex_token
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.plex.client import PlexClient

            settings = Settings.load_from_file()
            plex_token = get_plex_token()

            if not settings.plex.url or not plex_token:
                async with self:
                    self.action_message = "Plex not configured. Please configure in Settings."
                return

            db_path = settings.database.get_db_path()
            db = SQLiteManager(str(db_path))
            db.connect()

            # Get playlist details
            playlist = db.get_playlist_by_id(playlist_id)
            if not playlist:
                async with self:
                    self.action_message = "Playlist not found"
                db.close()
                return

            # Get playlist tracks
            tracks = db.get_playlist_tracks(playlist_id)
            track_plex_keys = [t['plex_key'] for t in tracks]

            db.close()

            # Connect to Plex and create playlist
            plex_client = PlexClient(settings.plex.url, plex_token)
            plex_client.connect()
            plex_client.select_library(settings.plex.library_name)

            playlist_name = playlist.name or f"PlexMix Playlist {playlist_id}"
            plex_client.create_playlist(playlist_name, track_plex_keys)

            async with self:
                self.action_message = f"Exported '{playlist_name}' to Plex!"

        except Exception as e:
            async with self:
                self.action_message = f"Error exporting to Plex: {str(e)}"

    def export_to_m3u(self, playlist_id: int):
        """Export playlist to M3U format and trigger download.

        Note: This remains synchronous because rx.download must be returned
        directly from an event handler. The database queries are fast enough
        that this shouldn't cause UI blocking in practice.
        """
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            db = SQLiteManager(str(db_path))
            db.connect()

            # Get playlist details
            playlist = db.get_playlist_by_id(playlist_id)
            if not playlist:
                self.action_message = "Playlist not found"
                db.close()
                return None

            # Get playlist tracks
            tracks = db.get_playlist_tracks(playlist_id)

            db.close()

            # Generate M3U content
            m3u_content = "#EXTM3U\n"
            m3u_content += f"#PLAYLIST:{playlist.name or 'PlexMix Playlist'}\n"

            for track in tracks:
                duration_sec = track.get('duration_ms', 0) // 1000
                artist = track.get('artist', 'Unknown')
                title = track.get('title', 'Unknown')
                m3u_content += f"#EXTINF:{duration_sec},{artist} - {title}\n"
                m3u_content += f"track_{track['id']}.mp3\n"

            filename = f"{playlist.name or 'playlist'}_{playlist_id}.m3u"

            # Return download trigger
            return rx.download(data=m3u_content, filename=filename)

        except Exception as e:
            self.action_message = f"Error exporting M3U: {str(e)}"
            return None

    def sort_playlists(self, sort_by: str):
        self.sort_by = sort_by

        if sort_by == "name":
            self.playlists.sort(
                key=lambda p: p.get('name', '').lower(),
                reverse=self.sort_descending
            )
        elif sort_by == "track_count":
            self.playlists.sort(
                key=lambda p: p.get('track_count', 0),
                reverse=self.sort_descending
            )
        else:  # created_date (default)
            self.playlists.sort(
                key=lambda p: p.get('created_at', ''),
                reverse=self.sort_descending
            )

    def toggle_sort_order(self):
        self.sort_descending = not self.sort_descending
        self.sort_playlists(self.sort_by)

    def format_date(self, date_str: str) -> str:
        try:
            # Parse the datetime string
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            # Format it nicely
            return dt.strftime("%B %d, %Y at %I:%M %p")
        except (ValueError, TypeError, AttributeError):
            return date_str

    def format_duration(self, duration_ms: int) -> str:
        if not duration_ms:
            return "0:00"

        total_seconds = duration_ms // 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"