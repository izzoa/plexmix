import reflex as rx
import asyncio
import logging
from typing import Optional
from datetime import datetime
from plexmix.ui.states.app_state import AppState


def _str_dict(d: dict) -> dict[str, str]:
    """Convert a dict with mixed-type values to all-string values for Reflex."""
    return {k: ("" if v is None else str(v)) for k, v in d.items()}

logger = logging.getLogger(__name__)


class HistoryState(AppState):
    # Playlist data - List of playlist dictionaries from database
    playlists: list[dict[str, str]] = []
    selected_playlist: dict[str, str] = {}
    selected_playlist_tracks: list[dict[str, str]] = []

    # Modal visibility
    is_detail_modal_open: bool = False
    is_delete_confirmation_open: bool = False
    playlist_to_delete: str = ""

    # Sorting/filtering
    sort_by: str = "created_date"  # created_date, name, track_count
    sort_descending: bool = True
    search_query: str = ""

    # UI feedback
    loading_playlists: bool = False
    loading_details: bool = False
    deleting_playlist: bool = False
    exporting: bool = False
    action_message: str = ""
    error_message: str = ""

    def on_load(self):
        if not self.check_auth():
            self.is_page_loading = False
            return
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

                # Get all playlists and convert to string dicts
                playlist_objs = db.get_playlists()
                playlists = [_str_dict(p.model_dump()) for p in playlist_objs]

                # Sort by created date (newest first) by default
                playlists.sort(
                    key=lambda p: p.get('created_at', ''),
                    reverse=True
                )

                db.close()

                async with self:
                    self.playlists = playlists
                    self.loading_playlists = False
                    self.is_page_loading = False
            else:
                async with self:
                    self.playlists = []
                    self.loading_playlists = False
                    self.is_page_loading = False

        except Exception as e:
            logger.error(f"Error loading playlists: {e}")
            async with self:
                self.playlists = []
                self.loading_playlists = False
                self.is_page_loading = False
                self.error_message = f"Error loading playlists: {str(e)}"

    def select_playlist(self, playlist_id: str):
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            pid = int(playlist_id)

            if db_path.exists():
                db = SQLiteManager(str(db_path))
                db.connect()

                # Get playlist details
                playlist = db.get_playlist_by_id(pid)
                if playlist:
                    playlist_dict = _str_dict(playlist.model_dump())

                    # Get tracks for this playlist
                    tracks = db.get_playlist_tracks(pid)

                    # Format track data for display
                    formatted_tracks = []
                    total_duration_ms = 0
                    for i, track in enumerate(tracks):
                        duration_ms = track.get('duration_ms', 0) or 0
                        total_duration_ms += duration_ms
                        if duration_ms:
                            minutes = duration_ms // 60000
                            seconds = (duration_ms // 1000) % 60
                            duration_formatted = f"{minutes}:{seconds:02d}"
                        else:
                            duration_formatted = "0:00"

                        formatted_tracks.append({
                            'position': str(i + 1),
                            'id': str(track.get('id', '')),
                            'title': str(track.get('title', 'Unknown')),
                            'artist': str(track.get('artist_name', 'Unknown')),
                            'album': str(track.get('album_title', 'Unknown')),
                            'duration_ms': str(duration_ms),
                            'duration_formatted': duration_formatted,
                            'genre': str(track.get('genre', '') or ''),
                            'year': str(track.get('year', '') or ''),
                        })

                    self.selected_playlist_tracks = formatted_tracks

                    # Calculate and format total duration
                    total_minutes = total_duration_ms // 60000
                    total_seconds = (total_duration_ms // 1000) % 60
                    playlist_dict['total_duration_ms'] = str(total_duration_ms)
                    playlist_dict['total_duration_formatted'] = f"{total_minutes}:{total_seconds:02d}"
                    self.selected_playlist = playlist_dict

                    # Open modal
                    self.is_detail_modal_open = True

                db.close()
        except Exception as e:
            logger.error(f"Error selecting playlist: {e}")
            return rx.toast.error(f"Error loading playlist: {str(e)}")

    def close_detail_modal(self):
        self.is_detail_modal_open = False
        self.selected_playlist = {}
        self.selected_playlist_tracks = []

    def set_detail_modal_open(self, is_open: bool):
        """Set the detail modal open state and clear data when closing."""
        self.is_detail_modal_open = is_open
        if not is_open:
            self.selected_playlist = {}
            self.selected_playlist_tracks = []

    def set_error_message(self, message: str):
        """Set the error message state."""
        self.error_message = message

    def set_action_message(self, message: str):
        """Set the action message state."""
        self.action_message = message

    def show_delete_confirmation(self, playlist_id: str):
        self.playlist_to_delete = str(playlist_id)
        self.is_delete_confirmation_open = True

    def cancel_delete(self):
        self.playlist_to_delete = ""
        self.is_delete_confirmation_open = False

    def set_delete_confirmation_open(self, is_open: bool):
        """Handle dialog open/close via on_open_change."""
        self.is_delete_confirmation_open = is_open
        if not is_open:
            self.playlist_to_delete = ""

    @rx.event(background=True)
    async def confirm_delete(self):
        async with self:
            if not self.playlist_to_delete:
                return

            playlist_id_str = self.playlist_to_delete
            self.is_delete_confirmation_open = False

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            db = SQLiteManager(str(db_path))
            db.connect()

            # Delete playlist
            db.delete_playlist(int(playlist_id_str))

            db.close()

            # Reload playlists - yield to let Reflex scheduler manage execution
            yield HistoryState.load_playlists()

            async with self:
                self.playlist_to_delete = ""

                # Close detail modal if it was open for this playlist
                if self.selected_playlist and self.selected_playlist.get('id') == playlist_id_str:
                    self.is_detail_modal_open = False
                    self.selected_playlist = {}
                    self.selected_playlist_tracks = []

            yield rx.toast.success("Playlist deleted successfully!")

        except Exception as e:
            yield rx.toast.error(f"Error deleting playlist: {str(e)}")

    @rx.event(background=True)
    async def export_to_plex(self, playlist_id: str):
        async with self:
            self.exporting = True

        try:
            from plexmix.config.settings import Settings
            from plexmix.config.credentials import get_plex_token
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.plex.client import PlexClient

            settings = Settings.load_from_file()
            plex_token = get_plex_token()

            if not settings.plex.url or not plex_token:
                async with self:
                    self.exporting = False
                yield rx.toast.error("Plex not configured. Please configure in Settings.")
                return

            pid = int(playlist_id)
            db_path = settings.database.get_db_path()
            db = SQLiteManager(str(db_path))
            db.connect()

            # Get playlist details
            playlist = db.get_playlist_by_id(pid)
            if not playlist:
                async with self:
                    self.exporting = False
                yield rx.toast.error("Playlist not found")
                db.close()
                return

            # Get playlist tracks
            tracks = db.get_playlist_tracks(pid)
            track_plex_keys = [t['plex_key'] for t in tracks]

            db.close()

            # Connect to Plex and create playlist
            plex_client = PlexClient(settings.plex.url, plex_token)
            if not plex_client.connect():
                async with self:
                    self.exporting = False
                yield rx.toast.error("Failed to connect to Plex server")
                return

            if not settings.plex.library_name or not plex_client.select_library(settings.plex.library_name):
                async with self:
                    self.exporting = False
                yield rx.toast.error(f"Music library not found: {settings.plex.library_name or '(not configured)'}")
                return

            playlist_name = playlist.name or f"PlexMix Playlist {pid}"
            plex_client.create_playlist(playlist_name, track_plex_keys)

            async with self:
                self.exporting = False
            yield rx.toast.success(f"Exported '{playlist_name}' to Plex!")

        except Exception as e:
            async with self:
                self.exporting = False
            yield rx.toast.error(f"Error exporting to Plex: {str(e)}")

    def export_to_m3u(self, playlist_id: str):
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
            pid = int(playlist_id)

            db = SQLiteManager(str(db_path))
            db.connect()

            # Get playlist details
            playlist = db.get_playlist_by_id(pid)
            if not playlist:
                db.close()
                return rx.toast.error("Playlist not found")

            # Get playlist tracks
            tracks = db.get_playlist_tracks(pid)

            db.close()

            # Generate M3U content
            m3u_content = "#EXTM3U\n"
            m3u_content += f"#PLAYLIST:{playlist.name or 'PlexMix Playlist'}\n"

            for track in tracks:
                duration_sec = (track.get('duration_ms', 0) or 0) // 1000
                artist = track.get('artist', 'Unknown')
                title = track.get('title', 'Unknown')
                m3u_content += f"#EXTINF:{duration_sec},{artist} - {title}\n"
                m3u_content += f"track_{track['id']}.mp3\n"

            filename = f"{playlist.name or 'playlist'}_{pid}.m3u"

            # Return download trigger
            return rx.download(data=m3u_content, filename=filename)

        except Exception as e:
            return rx.toast.error(f"Error exporting M3U: {str(e)}")

    @rx.var(cache=True)
    def filtered_playlists(self) -> list[dict[str, str]]:
        if not self.search_query:
            return self.playlists
        q = self.search_query.lower()
        return [
            p for p in self.playlists
            if q in (p.get("name") or "").lower()
            or q in (p.get("mood_query") or "").lower()
        ]

    def set_search_query(self, query: str):
        self.search_query = query

    _SORT_LABEL_TO_KEY = {
        "Date Created": "created_date",
        "Name": "name",
        "Track Count": "track_count",
    }
    _SORT_KEY_TO_LABEL = {v: k for k, v in _SORT_LABEL_TO_KEY.items()}

    @rx.var(cache=True)
    def sort_by_label(self) -> str:
        return self._SORT_KEY_TO_LABEL.get(self.sort_by, "Date Created")

    def sort_playlists_by_label(self, label: str):
        key = self._SORT_LABEL_TO_KEY.get(label, "created_date")
        self.sort_playlists(key)

    def sort_playlists(self, sort_by: str):
        self.sort_by = sort_by

        if sort_by == "name":
            self.playlists.sort(
                key=lambda p: p.get('name', '').lower(),
                reverse=self.sort_descending
            )
        elif sort_by == "track_count":
            self.playlists.sort(
                key=lambda p: int(p.get('track_count', '0') or '0'),
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