import asyncio
import reflex as rx
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, AsyncGenerator, Optional

from plexmix.ui.states.app_state import AppState
from plexmix.ui.utils.a11y import focus_by_id_script
from plexmix.ui.utils.errors import friendly_error
from plexmix.ui.utils.playlist_identity import (
    cover_seed,
    cover_variant,
    short_date,
    top_artists_label,
)

if TYPE_CHECKING:
    from reflex.event import EventSpec


def _str_dict(d: dict) -> dict[str, str]:
    """Convert a dict with mixed-type values to all-string values for Reflex."""
    return {k: ("" if v is None else str(v)) for k, v in d.items()}


logger = logging.getLogger(__name__)

# Element id prefixes of the playlist detail dialog's reorder buttons
# (``hist-move-up-<index>`` / ``hist-move-down-<index>``, 0-based). pages/history.py
# renders the same ids so focus can follow a moved track.
MOVE_UP_ID_PREFIX = "hist-move-up-"
MOVE_DOWN_ID_PREFIX = "hist-move-down-"

# Element id and form field name of the client-owned History search field
# (pages/history.py). A handler that changes ``search_query`` for any reason other
# than the field's own commit must also return ``rx.set_value(SEARCH_INPUT_ID, ...)``
# (webui-input-responsiveness D3).
SEARCH_INPUT_ID = "hist-search"
SEARCH_FIELD_NAME = "q"

# Deep link from the Dashboard's recent playlists: /history?playlist=<id> opens
# that playlist's detail once the list has loaded.
PLAYLIST_QUERY_PARAM = "playlist"
PLAYLIST_NOT_FOUND_MESSAGE = "That playlist no longer exists."
# Drops the consumed ?playlist= from the address bar without a navigation, so a
# reconnect (which re-runs on_load) doesn't reopen a dialog the user closed.
STRIP_PLAYLIST_PARAM_JS = "history.replaceState(history.state,'','/history')"

# Words for the sort button's accessible name, per sort key: (descending, ascending).
_SORT_DIRECTION_WORDS: dict[str, tuple[str, str]] = {
    "created_date": ("newest first", "oldest first"),
    "name": ("Z to A", "A to Z"),
    "track_count": ("most tracks first", "fewest tracks first"),
}


def _format_total_duration(duration_ms: int) -> str:
    """Return a total duration as ``H:MM:SS``, or ``M:SS`` under an hour."""
    total_seconds = max(int(duration_ms or 0), 0) // 1000
    hours, rest = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _reorder_focus_id(moved_up: bool, new_index: int, total: int) -> str:
    """Return the id of the reorder button that should hold focus after a move.

    Focus stays on the moved track's same-direction control at its new index. When
    the track reached the end it was moving towards, that control is disabled, so
    the opposite control is targeted instead.
    """
    if moved_up:
        if new_index <= 0:
            return f"{MOVE_DOWN_ID_PREFIX}{new_index}"
        return f"{MOVE_UP_ID_PREFIX}{new_index}"
    if new_index >= total - 1:
        return f"{MOVE_UP_ID_PREFIX}{new_index}"
    return f"{MOVE_DOWN_ID_PREFIX}{new_index}"


def _read_playlists() -> list[dict[str, str]]:
    """Read every saved playlist with its card identity (blocking; run in an executor).

    Returns ``[]`` when there is no library database yet. Sorted newest first.
    """
    from plexmix.config.settings import Settings
    from plexmix.database.sqlite_manager import SQLiteManager

    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()
    if not db_path.exists():
        return []
    db = SQLiteManager(str(db_path))
    db.connect()
    try:
        playlists = [_str_dict(p.model_dump()) for p in db.get_playlists()]
        # Card identity: top artists are supplementary, so a failed read leaves
        # them out instead of failing the whole list.
        try:
            top_artists = db.get_playlist_top_artists()
        except Exception as e:
            logger.warning("Could not load playlist top artists: %s", e)
            top_artists = {}
    finally:
        db.close()
    playlists = HistoryState._decorate_playlists(playlists, top_artists)
    playlists.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    return playlists


def _read_playlist_detail(
    playlist_id: str,
) -> Optional[tuple[dict[str, str], list[dict[str, str]]]]:
    """Read one playlist and its tracks for the detail dialog (blocking).

    Returns ``(playlist, tracks)`` with string values, or None when the id is not
    a number, the database is missing or no playlist has that id.
    """
    from plexmix.config.settings import Settings
    from plexmix.database.sqlite_manager import SQLiteManager

    text = str(playlist_id or "").strip()
    if not text.isdigit():
        return None
    pid = int(text)
    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()
    if not db_path.exists():
        return None
    db = SQLiteManager(str(db_path))
    db.connect()
    try:
        playlist = db.get_playlist_by_id(pid)
        if not playlist:
            return None
        playlist_dict = _str_dict(playlist.model_dump())
        tracks = db.get_playlist_tracks(pid)
    finally:
        db.close()

    formatted_tracks = []
    total_duration_ms = 0
    for i, track in enumerate(tracks):
        duration_ms = track.get("duration_ms", 0) or 0
        total_duration_ms += duration_ms
        if duration_ms:
            minutes = duration_ms // 60000
            seconds = (duration_ms // 1000) % 60
            duration_formatted = f"{minutes}:{seconds:02d}"
        else:
            duration_formatted = "0:00"
        formatted_tracks.append(
            {
                "position": str(i + 1),
                "id": str(track.get("id", "")),
                "title": str(track.get("title", "Unknown")),
                "artist": str(track.get("artist_name", "Unknown")),
                "album": str(track.get("album_title", "Unknown")),
                "duration_ms": str(duration_ms),
                "duration_formatted": duration_formatted,
                "genre": str(track.get("genre", "") or ""),
                "year": str(track.get("year", "") or ""),
            }
        )

    # Identity shown in the dialog description: count, total duration and created
    # date (the prompt is mood_query).
    playlist_dict["track_count"] = str(len(formatted_tracks))
    playlist_dict["total_duration_ms"] = str(total_duration_ms)
    playlist_dict["total_duration_formatted"] = _format_total_duration(total_duration_ms)
    playlist_dict["created_date"] = short_date(playlist_dict.get("created_at", ""))
    return playlist_dict, formatted_tracks


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

    # UI feedback. ``loading_playlists`` starts True so the first render is a
    # skeleton, never "No playlists yet" before the list is known. A failed load
    # sets ``load_failed`` and ``error_message`` (an error callout with Retry in
    # the playlist area); a deep link to a playlist that no longer exists sets
    # only ``error_message`` (shown above the list).
    loading_playlists: bool = True
    load_failed: bool = False
    loading_details: bool = False
    deleting_playlist: bool = False
    exporting: bool = False
    action_message: str = ""
    error_message: str = ""

    # /history?playlist=<id> waiting for the list to load (backend only).
    _pending_playlist_id: str = ""

    # Import modal
    is_import_modal_open: bool = False
    import_playlist_name: str = ""
    import_status: str = ""
    importing: bool = False

    # Playlist detail reorder: the last move, read by an sr-only role=status region
    # ("Moved <title> to position k of N").
    reorder_announcement: str = ""

    def _playlist_query_param(self) -> str:
        """The ``?playlist=`` value of the current page URL, or ""."""
        try:
            value = self.router.url.query_parameters.get(PLAYLIST_QUERY_PARAM)
        except Exception:
            return ""
        if isinstance(value, (list, tuple)):  # a repeated ?playlist=a&playlist=b
            value = value[0] if value else ""
        return value.strip() if isinstance(value, str) else ""

    @rx.event
    def on_load(self) -> Any:
        if not self.check_auth():
            self.is_page_loading = False
            return None
        self.load_app_data()
        deep_link = self._playlist_query_param()
        if not deep_link:
            return HistoryState.load_playlists
        # Opened from a Dashboard recent row: show that playlist once the list has
        # loaded (load_playlists), then drop the parameter from the address bar.
        self._pending_playlist_id = deep_link
        return [HistoryState.load_playlists, rx.call_script(STRIP_PLAYLIST_PARAM_JS)]

    @rx.event
    def retry_load(self) -> Any:
        """Retry after a failed load: back to the loading placeholder, then reload."""
        self.loading_playlists = True
        self.load_failed = False
        self.error_message = ""
        return HistoryState.load_playlists

    @rx.event
    def dismiss_error(self) -> None:
        """Hide the message above the list (for example a missing deep-linked playlist)."""
        if not self.load_failed:
            self.error_message = ""

    @rx.event
    def clear_search(self) -> Any:
        """Clear the search (the "no playlists match" state's action).

        The field is client-owned, so its text is emptied with ``rx.set_value``
        (webui-input-responsiveness D3), and focus moves to it because the
        Clear search button leaves the page with the list.
        """
        self.search_query = ""
        return [
            rx.set_value(SEARCH_INPUT_ID, ""),
            rx.call_script(focus_by_id_script(SEARCH_INPUT_ID)),
        ]

    @staticmethod
    def _decorate_playlists(
        playlists: list[dict[str, str]], top: dict[int, list[str]]
    ) -> list[dict[str, str]]:
        """Return copies of ``playlists`` with the card identity fields added.

        Adds ``cover_variant`` (the swatch, seeded by the prompt or else the name),
        ``created_date`` (``YYYY-MM-DD``, or ``""`` when unknown) and ``top_artists``
        (up to three names from ``top``, keyed by playlist id; ``""`` when none).
        """
        decorated: list[dict[str, str]] = []
        for playlist in playlists:
            try:
                artists = top.get(int(playlist.get("id") or ""), [])
            except (TypeError, ValueError):
                artists = []
            seed = cover_seed(playlist.get("mood_query") or "", playlist.get("name") or "")
            decorated.append(
                {
                    **playlist,
                    "cover_variant": str(cover_variant(seed)),
                    "created_date": short_date(playlist.get("created_at") or ""),
                    "top_artists": top_artists_label(artists),
                }
            )
        return decorated

    @rx.event(background=True)
    async def load_playlists(self) -> None:
        """Load the playlists off the event loop; then open a deep-linked one.

        Shows the loading placeholder only while nothing is listed yet (a revisit
        keeps the last real list while it refreshes). A failure is shown in the
        playlist area with Retry (``load_failed``), never as an empty state. A
        pending ``?playlist=`` id opens its detail dialog, or says the playlist
        no longer exists.
        """
        async with self:
            self.loading_playlists = True
            if self.load_failed:
                self.load_failed = False
                self.error_message = ""
            pending = self._pending_playlist_id
            self._pending_playlist_id = ""
            if pending:
                self.error_message = ""

        loop = asyncio.get_running_loop()
        try:
            playlists = await loop.run_in_executor(None, _read_playlists)
        except Exception as e:
            logger.error("Error loading playlists: %s", e)
            async with self:
                self.loading_playlists = False
                self.is_page_loading = False
                self.load_failed = True
                self.error_message = friendly_error(e)
                # Keep the deep link for the retry.
                self._pending_playlist_id = pending
            return

        detail: Optional[tuple[dict[str, str], list[dict[str, str]]]] = None
        if pending:
            try:
                detail = await loop.run_in_executor(None, _read_playlist_detail, pending)
            except Exception as e:
                logger.error("Error opening playlist %s: %s", pending, e)
                detail = None

        async with self:
            self.playlists = playlists
            # Keep the user's chosen order (and the sort button's name,
            # sort_button_label) true after a reload.
            self.sort_playlists(self.sort_by)
            self.loading_playlists = False
            self.is_page_loading = False
            if pending:
                if detail is None:
                    self.error_message = PLAYLIST_NOT_FOUND_MESSAGE
                else:
                    self._show_playlist_detail(*detail)

    def _show_playlist_detail(self, playlist: dict[str, str], tracks: list[dict[str, str]]) -> None:
        """Fill and open the detail dialog."""
        self.selected_playlist_tracks = tracks
        self.selected_playlist = playlist
        self.reorder_announcement = ""
        self.is_detail_modal_open = True

    def select_playlist(self, playlist_id: str) -> Any:
        """Open a playlist's detail dialog (a card's activation)."""
        try:
            detail = _read_playlist_detail(playlist_id)
        except Exception as e:
            logger.error(f"Error selecting playlist: {e}")
            return rx.toast.error(f"Error loading playlist: {str(e)}")
        if detail is not None:
            self._show_playlist_detail(*detail)
        return None

    def _move_track(self, index: str, step: int) -> Optional["EventSpec"]:
        """Swap the track at ``index`` with its neighbour ``step`` (-1 up, +1 down).

        Renumbers positions, announces the move in ``reorder_announcement`` and
        returns a script that keeps focus on the moved track's reorder control at
        its new index. An unparseable or out-of-range move changes nothing and
        returns None.
        """
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return None
        total = len(self.selected_playlist_tracks)
        new_idx = idx + step
        if idx < 0 or idx >= total or new_idx < 0 or new_idx >= total:
            return None
        tracks = [dict(t) for t in self.selected_playlist_tracks]
        tracks[idx], tracks[new_idx] = tracks[new_idx], tracks[idx]
        # Renumber positions
        for i, t in enumerate(tracks):
            t["position"] = str(i + 1)
        self.selected_playlist_tracks = tracks
        title = tracks[new_idx].get("title") or "Track"
        self.reorder_announcement = f"Moved {title} to position {new_idx + 1} of {total}"
        target = _reorder_focus_id(step < 0, new_idx, total)
        return rx.call_script(focus_by_id_script(target))

    def move_track_up(self, index: str) -> Optional["EventSpec"]:
        """Move a track one position up in the playlist (see ``_move_track``)."""
        return self._move_track(index, -1)

    def move_track_down(self, index: str) -> Optional["EventSpec"]:
        """Move a track one position down in the playlist (see ``_move_track``)."""
        return self._move_track(index, 1)

    @rx.event(background=True)
    async def save_track_order(self):
        """Persist reordered track positions to the database."""
        async with self:
            if not self.access_allowed():
                return
            playlist_id = self.selected_playlist.get("id", "")
            tracks = list(self.selected_playlist_tracks)

        if not playlist_id or not tracks:
            return

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            with SQLiteManager(str(db_path)) as db:
                cursor = db.get_connection().cursor()
                # Rebuild the rows in the new order. Updating by track_id alone
                # can't disambiguate a track that appears more than once and would
                # collide on the (playlist_id, track_id, position) primary key; a
                # delete-then-reinsert with fresh sequential positions is safe.
                cursor.execute(
                    "DELETE FROM playlist_tracks WHERE playlist_id = ?", (int(playlist_id),)
                )
                for new_pos, t in enumerate(tracks):
                    cursor.execute(
                        "INSERT INTO playlist_tracks (playlist_id, track_id, position) "
                        "VALUES (?, ?, ?)",
                        (int(playlist_id), int(t["id"]), new_pos),
                    )
                db.get_connection().commit()

            yield rx.toast.success("Track order saved")

        except Exception as e:
            yield rx.toast.error(f"Error saving order: {e}")

    def rerun_playlist(self):
        """Store the selected playlist's generation config and navigate to Generator."""
        import json as _json

        config_json = self.selected_playlist.get("generation_config", "")
        mood_query = self.selected_playlist.get("mood_query", "")

        if config_json:
            self._rerun_generation_config = config_json
        elif mood_query:
            # Fallback: create a minimal config from just the mood query
            self._rerun_generation_config = _json.dumps({"mood_query": mood_query})
        else:
            return rx.toast.error("No generation config available for this playlist")

        self.is_detail_modal_open = False
        return rx.redirect("/generator")

    def close_detail_modal(self) -> None:
        self.is_detail_modal_open = False
        self.selected_playlist = {}
        self.selected_playlist_tracks = []
        self.reorder_announcement = ""

    def set_detail_modal_open(self, is_open: bool) -> None:
        """Set the detail modal open state and clear data when closing."""
        self.is_detail_modal_open = is_open
        if not is_open:
            self.selected_playlist = {}
            self.selected_playlist_tracks = []
            self.reorder_announcement = ""

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

    def set_delete_confirmation_open(self, is_open: bool) -> None:
        """Handle the alert dialog's on_open_change (Escape or Cancel).

        Idempotent: closing an already-closed dialog (for example after
        ``confirm_delete`` closed it by state) just leaves it closed with nothing
        pending, so the confirm button never needs ``alert_dialog.action``.
        """
        self.is_delete_confirmation_open = is_open
        if not is_open:
            self.playlist_to_delete = ""

    @rx.event(background=True)
    async def confirm_delete(self) -> AsyncGenerator[Any, None]:
        async with self:
            if not self.access_allowed():
                return
            if not self.playlist_to_delete:
                return

            playlist_id_str = self.playlist_to_delete
            # Close the confirmation by state (its Delete is not an
            # alert_dialog.action) and clear the pending id so a double
            # activation can't delete twice.
            self.is_delete_confirmation_open = False
            self.playlist_to_delete = ""

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            with SQLiteManager(str(db_path)) as db:
                db.delete_playlist(int(playlist_id_str))

            async with self:
                # Drop the card in the same update that closes the detail dialog:
                # the dialog's focus restore then finds its opener gone and falls
                # back to the page heading (#main-heading) instead of <body>.
                self.playlists = [p for p in self.playlists if p.get("id") != playlist_id_str]
                if self.selected_playlist and self.selected_playlist.get("id") == playlist_id_str:
                    self.is_detail_modal_open = False
                    self.selected_playlist = {}
                    self.selected_playlist_tracks = []
                    self.reorder_announcement = ""

            yield rx.toast.success("Playlist deleted successfully!")

            # Reload playlists - yield to let Reflex scheduler manage execution
            yield HistoryState.load_playlists()

        except Exception as e:
            yield rx.toast.error(f"Error deleting playlist: {str(e)}")

    @rx.event(background=True)
    async def export_to_plex(self, playlist_id: str):
        async with self:
            if not self.access_allowed():
                return
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
            track_plex_keys = [t["plex_key"] for t in tracks]

            db.close()

            # Connect to Plex and create playlist
            plex_client = PlexClient(settings.plex.url, plex_token)
            if not plex_client.connect():
                async with self:
                    self.exporting = False
                yield rx.toast.error("Failed to connect to Plex server")
                return

            if not settings.plex.library_name or not plex_client.select_library(
                settings.plex.library_name
            ):
                async with self:
                    self.exporting = False
                yield rx.toast.error(
                    f"Music library not found: {settings.plex.library_name or '(not configured)'}"
                )
                return

            playlist_name = playlist.name or f"PlexMix Playlist {pid}"
            created = plex_client.create_playlist(playlist_name, track_plex_keys)

            async with self:
                self.exporting = False
            # create_playlist returns None on failure — don't claim success.
            if created is None:
                yield rx.toast.error(f"Failed to export '{playlist_name}' to Plex.")
            else:
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
                duration_sec = (track.get("duration_ms", 0) or 0) // 1000
                artist = track.get("artist_name", "Unknown")
                title = track.get("title", "Unknown")
                m3u_content += f"#EXTINF:{duration_sec},{artist} - {title}\n"
                file_path = track.get("file_path")
                if file_path:
                    m3u_content += f"{file_path}\n"
                else:
                    m3u_content += f"{artist} - {title}.mp3\n"

            filename = f"{playlist.name or 'playlist'}_{pid}.m3u"

            # Return download trigger
            return rx.download(data=m3u_content, filename=filename)

        except Exception as e:
            return rx.toast.error(f"Error exporting M3U: {str(e)}")

    def export_to_json(self, playlist_id: str):
        """Export playlist to JSON format and trigger download."""
        try:
            import json as _json

            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            pid = int(playlist_id)

            db = SQLiteManager(str(db_path))
            db.connect()

            playlist = db.get_playlist_by_id(pid)
            if not playlist:
                db.close()
                return rx.toast.error("Playlist not found")

            tracks = db.get_playlist_tracks(pid)
            db.close()

            data = {
                "plexmix_version": "1",
                "playlist": {
                    "name": playlist.name,
                    "description": playlist.description,
                    "mood_query": playlist.mood_query,
                    "created_at": playlist.created_at.isoformat() if playlist.created_at else None,
                    "created_by_ai": playlist.created_by_ai,
                },
                "tracks": [
                    {
                        "title": t.get("title", ""),
                        "artist": t.get("artist_name", ""),
                        "album": t.get("album_title", ""),
                        "genre": t.get("genre", ""),
                        "year": t.get("year"),
                        "duration_ms": t.get("duration_ms"),
                        "plex_key": t.get("plex_key", ""),
                        "position": t.get("position", 0),
                    }
                    for t in tracks
                ],
            }

            json_content = _json.dumps(data, indent=2, ensure_ascii=False)
            filename = f"{playlist.name or 'playlist'}_{pid}.json"

            return rx.download(data=json_content, filename=filename)

        except Exception as e:
            return rx.toast.error(f"Error exporting JSON: {str(e)}")

    def open_import_modal(self):
        self.is_import_modal_open = True
        self.import_playlist_name = ""
        self.import_status = ""

    def close_import_modal(self):
        self.is_import_modal_open = False
        self.import_status = ""

    def set_import_modal_open(self, is_open: bool):
        self.is_import_modal_open = is_open
        if not is_open:
            self.import_status = ""

    def set_import_playlist_name(self, value: str):
        self.import_playlist_name = value

    async def handle_import_upload(self, files: list[rx.UploadFile]):
        """Handle uploaded playlist file (JSON or M3U)."""
        if not files:
            return

        self.importing = True
        self.import_status = "Processing file..."

        try:
            upload_file = files[0]
            file_content = (await upload_file.read()).decode("utf-8")
            filename = upload_file.filename or ""

            # Detect format
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext == "json" or file_content.strip().startswith("{"):
                playlist_name, tracks_meta = self._parse_json_import(file_content)
            elif ext in ("m3u", "m3u8") or file_content.strip().startswith("#EXTM3U"):
                playlist_name, tracks_meta = self._parse_m3u_import(file_content)
            else:
                self.import_status = "Unsupported file format. Use JSON or M3U."
                self.importing = False
                return

            # Use override name if provided
            if self.import_playlist_name.strip():
                playlist_name = self.import_playlist_name.strip()
            elif not playlist_name:
                playlist_name = filename.rsplit(".", 1)[0] if "." in filename else filename

            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.database.models import Playlist

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                self.import_status = "Database not found. Sync your library first."
                self.importing = False
                return

            db = SQLiteManager(str(db_path))
            db.connect()

            matched_ids = []
            unmatched = []

            for tm in tracks_meta:
                track_id = self._find_track(db, tm)
                if track_id:
                    matched_ids.append(track_id)
                else:
                    label = tm.get("title", "") or tm.get("file_path", "unknown")
                    artist = tm.get("artist", "")
                    if artist:
                        label = f"{artist} - {label}"
                    unmatched.append(label)

            if not matched_ids:
                db.close()
                self.import_status = "No tracks matched any entries in your library."
                self.importing = False
                return

            pl = Playlist(name=playlist_name, created_by_ai=False)
            pid = db.insert_playlist(pl)
            db.add_tracks_to_playlist(pid, matched_ids)
            db.close()

            status_parts = [f"Imported '{playlist_name}' with {len(matched_ids)} tracks."]
            if unmatched:
                status_parts.append(f"{len(unmatched)} track(s) could not be matched.")
            self.import_status = " ".join(status_parts)
            self.importing = False
            self.is_import_modal_open = False

            yield rx.toast.success(f"Imported '{playlist_name}' with {len(matched_ids)} tracks")
            yield HistoryState.load_playlists()

        except Exception as e:
            logger.error("Import failed: %s", e)
            self.import_status = f"Import failed: {str(e)}"
            self.importing = False

    @staticmethod
    def _parse_json_import(content: str) -> tuple:
        import json as _json

        data = _json.loads(content)
        playlist_info = data.get("playlist", {})
        playlist_name = playlist_info.get("name", "")
        tracks = data.get("tracks", [])
        return playlist_name, tracks

    @staticmethod
    def _parse_m3u_import(content: str) -> tuple:
        lines = content.strip().splitlines()
        playlist_name = ""
        tracks: list[dict] = []
        current_extinf = None

        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#PLAYLIST:"):
                playlist_name = line[len("#PLAYLIST:") :]
            elif line.startswith("#EXTINF:"):
                current_extinf = line[len("#EXTINF:") :]
            elif line.startswith("#"):
                continue
            else:
                track_meta: dict = {"file_path": line}
                if current_extinf:
                    parts = current_extinf.split(",", 1)
                    if len(parts) == 2:
                        display = parts[1]
                        if " - " in display:
                            artist, title = display.split(" - ", 1)
                            track_meta["artist"] = artist.strip()
                            track_meta["title"] = title.strip()
                        else:
                            track_meta["title"] = display.strip()
                current_extinf = None
                tracks.append(track_meta)

        return playlist_name, tracks

    @staticmethod
    def _find_track(db: object, meta: dict) -> "int | None":
        # By plex_key
        plex_key = meta.get("plex_key")
        if plex_key:
            track = db.get_track_by_plex_key(plex_key)
            if track and track.id is not None:
                return track.id

        # By file_path
        file_path = meta.get("file_path")
        if file_path:
            track = db.get_track_by_file_path(file_path)
            if track and track.id is not None:
                return track.id

        # By title + artist FTS
        title = meta.get("title", "")
        artist = meta.get("artist", "")
        if title:
            query = f"{title} {artist}" if artist else title
            try:
                results = db.search_tracks_fts(query)
                if results:
                    return results[0].id
            except Exception:
                pass

        return None

    @rx.var(cache=True)
    def has_playlists(self) -> bool:
        """True when at least one playlist is saved (whatever the search shows)."""
        return bool(self.playlists)

    @rx.var(cache=True)
    def show_loading(self) -> bool:
        """Show the loading placeholder: a load runs and nothing is listed yet.

        A revisit keeps showing the last real list while it reloads instead.
        """
        return bool(self.loading_playlists) and not self.playlists and not self.load_failed

    @rx.var(cache=True)
    def filtered_playlists(self) -> list[dict[str, str]]:
        if not self.search_query:
            return self.playlists
        q = self.search_query.lower()
        return [
            p
            for p in self.playlists
            if q in (p.get("name") or "").lower() or q in (p.get("mood_query") or "").lower()
        ]

    def set_search_query(self, query: str) -> None:
        """Commit the search field (debounced while typing, and on blur).

        An unchanged value is a no-op, so a blur commit after a debounced one (or
        the reverse) costs nothing.
        """
        if query == self.search_query:
            return
        self.search_query = query

    @rx.event
    def apply_search_form(self, form_data: dict[str, Any]) -> None:
        """Apply the submitted search form (Enter in the field) right away.

        Reads the field's ``q`` value from ``form_data``, never from state, so a
        debounced commit still in flight cannot leave the list filtered by older
        text. A missing key leaves the search unchanged.
        """
        if SEARCH_FIELD_NAME in form_data:
            self.set_search_query(str(form_data[SEARCH_FIELD_NAME] or ""))

    _SORT_LABEL_TO_KEY = {
        "Date Created": "created_date",
        "Name": "name",
        "Track Count": "track_count",
    }
    _SORT_KEY_TO_LABEL = {v: k for k, v in _SORT_LABEL_TO_KEY.items()}

    @rx.var(cache=True)
    def sort_by_label(self) -> str:
        return self._SORT_KEY_TO_LABEL.get(self.sort_by, "Date Created")

    @rx.var(cache=True)
    def selected_track_count(self) -> int:
        """Number of tracks in the open playlist (bounds its Move down controls)."""
        return len(self.selected_playlist_tracks)

    @rx.var(cache=True)
    def sort_button_label(self) -> str:
        """Accessible name for the sort button: the key (its visible text) and order.

        For example "Sort playlists by Date Created, newest first".
        """
        key = self.sort_by if self.sort_by in _SORT_DIRECTION_WORDS else "created_date"
        label = self._SORT_KEY_TO_LABEL.get(key, "Date Created")
        descending, ascending = _SORT_DIRECTION_WORDS[key]
        order = descending if self.sort_descending else ascending
        return f"Sort playlists by {label}, {order}"

    def sort_playlists_by_label(self, label: str):
        key = self._SORT_LABEL_TO_KEY.get(label, "created_date")
        self.sort_playlists(key)

    def sort_playlists(self, sort_by: str):
        self.sort_by = sort_by

        if sort_by == "name":
            self.playlists.sort(
                key=lambda p: p.get("name", "").lower(), reverse=self.sort_descending
            )
        elif sort_by == "track_count":
            self.playlists.sort(
                key=lambda p: int(p.get("track_count", "0") or "0"), reverse=self.sort_descending
            )
        else:  # created_date (default)
            self.playlists.sort(key=lambda p: p.get("created_at", ""), reverse=self.sort_descending)

    def toggle_sort_order(self):
        self.sort_descending = not self.sort_descending
        self.sort_playlists(self.sort_by)

    def format_date(self, date_str: str) -> str:
        try:
            # Parse the datetime string
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
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
