from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Dict, Callable, FrozenSet, List
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

from ..plex.client import READ_KINDS, PlexClient, PlexReadReport
from ..database.backup import backup_library
from ..database.sqlite_manager import SQLiteManager
from ..database.models import Artist, Album, Track, Genre, SyncHistory
from ..database.vector_index import VectorIndex
from ..utils.embeddings import EmbeddingGenerator, create_track_text
from ..utils.redaction import mask_urls_in_text
from ..ai.tag_generator import TagGenerator
from ..ai.errors import FatalProviderError
from ..ai.base import AIProvider
from ..config.settings import MusicBrainzSettings

logger = logging.getLogger(__name__)

# Reads of one kind (artists, albums or tracks) that failed block every removal of a
# run when they are more than both numbers: Plex is then not trusted about what is gone.
MAX_FAILED_READS = 10
MAX_FAILED_READS_PERCENT = 1

# A removal needs confirmation when it is more than both numbers. The percentage is
# of the tracks the database held before the sync.
REMOVAL_CONFIRM_TRACKS = 50
REMOVAL_CONFIRM_PERCENT = 5

# Why a sync removed nothing (``SyncOutcome.reason``).
REASON_THRESHOLD = "threshold"
REASON_EMPTY_LISTING = "empty_listing"
REASON_TOO_MANY_UNREAD = "too_many_unread"
REASON_BACKUP_FAILED = "backup_failed"
REASON_LISTING_UNKNOWN = "listing_unknown"

# What ``SyncEngine.remove_unlisted_tracks`` did (``RemovalResult.status``).
REMOVAL_REMOVED = "removed"
REMOVAL_LISTED_AGAIN = "listed_again"
REMOVAL_MORE_MISSING = "more_missing"
REMOVAL_LISTING_FAILED = "listing_failed"
REMOVAL_BACKUP_FAILED = "backup_failed"
REMOVAL_CANCELLED = "cancelled"
REMOVAL_FAILED = "failed"

_NOTHING_REMOVED = "Nothing was removed."
_NOTHING_CHANGED = "Nothing was changed."
_NOT_SYNCED = "The library was not synced."

# ``regenerate_sync`` clears the tags and embeddings before it reads Plex. When the
# sync after it fails, "nothing was changed" is true for the library sync only, so
# this sentence is added to the history row. Callers add their own wording when
# ``SyncEngine.cleared_before_sync`` is True.
REGENERATE_CLEARED_NOTE = "The existing tags and embeddings had already been cleared."


def too_many_failed_reads(failed: int, listed: int) -> bool:
    """True when so many reads of one kind failed that the run must remove nothing.

    ``failed`` of the ``listed`` items of one kind could not be read. Too many means
    more than ``MAX_FAILED_READS`` items and more than ``MAX_FAILED_READS_PERCENT``
    percent of the listing.
    """
    return failed > MAX_FAILED_READS and failed * 100 > listed * MAX_FAILED_READS_PERCENT


def removal_needs_confirmation(count: int, library_total: int) -> bool:
    """True when removing ``count`` tracks has to be confirmed first.

    That is more than ``REMOVAL_CONFIRM_TRACKS`` tracks and more than
    ``REMOVAL_CONFIRM_PERCENT`` percent of ``library_total``, the number of tracks in
    the database before the sync.
    """
    return count > REMOVAL_CONFIRM_TRACKS and count * 100 > library_total * REMOVAL_CONFIRM_PERCENT


def _zero_counts() -> Dict[str, int]:
    return {kind: 0 for kind in READ_KINDS}


def _counted(count: int, kind: str) -> str:
    """``1 track`` or ``5,443 tracks``. ``kind`` is the plural name of the items."""
    return f"{count:,} {kind[:-1] if count == 1 else kind}"


def _backup_failed_message(error: BaseException) -> str:
    reason = mask_urls_in_text(str(error)).strip().rstrip(".") or type(error).__name__
    return f"The backup could not be made: {reason}. {_NOTHING_REMOVED}"


def _check_failed_message(reason: Any, nothing_changed: bool = True) -> str:
    """The text of a failed connection check: the client's reason, then what that means.

    The check runs before a sync reads or writes anything, and before a regenerate
    clears anything, so nothing was changed. ``nothing_changed`` is False for the
    one check that comes later: the sync that a regenerate starts after it has
    cleared the tags and embeddings.
    """
    text = str(reason or "").strip() or "The Plex connection could not be checked"
    if not text.endswith((".", "!", "?")):
        text += "."
    return f"{text} {_NOTHING_CHANGED if nothing_changed else _NOT_SYNCED}"


def _removal_failed_message(error: BaseException) -> str:
    reason = mask_urls_in_text(str(error)).strip().rstrip(".") or type(error).__name__
    return f"The tracks could not be removed: {reason}. {_NOTHING_REMOVED}"


def _listed_again_sentence(count: int) -> str:
    """What to say about confirmed tracks that Plex lists again and that were kept."""
    if count <= 0:
        return ""
    if count == 1:
        return " Plex lists 1 of the confirmed tracks again, and it was kept."
    return f" Plex lists {count:,} of the confirmed tracks again, and they were kept."


class _RemovalCancelled(Exception):
    """A cancel request arrived while tracks were being deleted.

    Raised inside the delete transaction, which is then rolled back: nothing was removed.
    """


def _other_missing_sentence(count: int) -> str:
    """What to say about tracks that are missing now and were not confirmed; "" for 0."""
    if count <= 0:
        return ""
    if count == 1:
        return (
            " 1 other track is missing from Plex now. It was not part of this "
            "confirmation and was kept. Run a sync to review it."
        )
    return (
        f" {count:,} other tracks are missing from Plex now. They were not part of this "
        "confirmation and were kept. Run a sync to review them."
    )


@dataclass
class SyncOutcome:
    """What one sync run did about unread items and about removals.

    ``SyncEngine.outcome`` holds a new one for every run, also for a run that raises.

    ``unread`` and ``listed`` are keyed by kind ("artists", "albums", "tracks"):
    the items whose details could not be read (they were kept as they are) and the
    items Plex listed. ``library_total`` is the number of tracks in the database
    before the sync. ``removed`` tracks were removed. ``held`` tracks are no longer
    listed by Plex and were kept because a removal that large needs confirmation.
    ``held_keys`` are their rating keys, and
    ``SyncEngine.remove_unlisted_tracks(held, held_keys)`` applies the removal.
    ``reason`` says why
    nothing was removed (one of the ``REASON_*`` values) and is empty otherwise.
    ``message`` is the text for ``reason``, ready to show. ``backup_folder`` and
    ``backup_size_bytes`` describe the backup made before a large removal.

    ``compared`` is True once the compare stage has saved its work, and ``added``
    and ``updated`` are the tracks it wrote. Together with ``removed`` they say what
    a run had already changed when a later stage stopped it. They take no part in
    comparing two outcomes.
    """

    unread: Dict[str, int] = field(default_factory=_zero_counts)
    listed: Dict[str, int] = field(default_factory=_zero_counts)
    library_total: int = 0
    removed: int = 0
    held: int = 0
    held_keys: FrozenSet[str] = field(default=frozenset(), repr=False)
    reason: str = ""
    message: str = ""
    backup_folder: Optional[Path] = None
    backup_size_bytes: int = 0
    added: int = field(default=0, compare=False)
    updated: int = field(default=0, compare=False)
    compared: bool = field(default=False, compare=False)

    @property
    def unread_tracks(self) -> int:
        """Tracks that Plex listed and whose details could not be read."""
        return self.unread.get("tracks", 0)

    @property
    def is_partial(self) -> bool:
        """True when items were unread, or removals were skipped or held."""
        return bool(self.reason) or any(self.unread.get(kind, 0) for kind in READ_KINDS)

    def unread_sentence(self, kind: str = "tracks") -> str:
        """What happened to the items of ``kind`` that could not be read; "" for none.

        For tracks: ``3 tracks could not be read and were kept.`` An unread artist or
        album may be new to PlexMix, so there may be no row that was kept. Its
        sentence says what the sync did with the tracks instead: a track is filed
        under "Unknown Artist" only when it is new and its artist is new as well.
        """
        count = self.unread.get(kind, 0)
        if not count:
            return ""
        lead = f"{_counted(count, kind)} could not be read"
        if kind == "tracks":
            return f"{lead} and {'was' if count == 1 else 'were'} kept."
        one = kind[:-1]
        return (
            f"{lead}. Tracks keep the {one} PlexMix already has for them. A new track "
            f"whose {one} is also new to PlexMix is filed under Unknown "
            f"{one.capitalize()} until a later sync reads that {one}."
        )

    def describe(self) -> str:
        """Everything a partial run has to say, for its history row. "" for a clean run."""
        parts = [self.message] if self.message else []
        for kind in READ_KINDS:
            # ``message`` already gives the counts of a kind with too many failed reads.
            named = self.reason == REASON_TOO_MANY_UNREAD and too_many_failed_reads(
                self.unread.get(kind, 0), self.listed.get(kind, 0)
            )
            if not named and self.unread.get(kind, 0):
                parts.append(self.unread_sentence(kind))
        return " ".join(parts)


@dataclass
class RemovalResult:
    """What ``SyncEngine.remove_unlisted_tracks`` did.

    ``status`` is one of the ``REMOVAL_*`` values. ``removed`` tracks were removed
    (0 unless the status is ``removed``). ``missing`` is the number of tracks the new
    listing leaves out, and ``library_total`` the number of tracks in the database
    before the removal. ``other_missing`` of the missing tracks were not among the
    confirmed ones (``held_keys``) and were kept. ``missing_keys`` are the rating
    keys of the missing tracks when more are missing than were confirmed, for a
    caller that asks again with the new count. ``listed_again`` of the confirmed
    tracks are listed by Plex now and were kept (counted only when ``held_keys``
    were given). ``message`` is ready to show.
    ``backup_folder`` and ``backup_size_bytes`` describe the backup made before the
    removal; a removal that was cancelled or failed after its backup names it too.
    """

    status: str
    removed: int = 0
    missing: int = 0
    library_total: int = 0
    message: str = ""
    backup_folder: Optional[Path] = None
    backup_size_bytes: int = 0
    other_missing: int = 0
    missing_keys: FrozenSet[str] = field(default=frozenset(), repr=False)
    listed_again: int = 0


class SyncEngine:
    def __init__(
        self,
        plex_client: PlexClient,
        db_manager: SQLiteManager,
        embedding_generator: Optional[EmbeddingGenerator] = None,
        vector_index: Optional[VectorIndex] = None,
        ai_provider: Optional[AIProvider] = None,
        musicbrainz_settings: Optional[MusicBrainzSettings] = None,
        audio_settings: Optional[Any] = None,
        saved_index_path: Optional[Path] = None,
        saved_index_dimension: Optional[int] = None,
    ):
        self.plex = plex_client
        self.db = db_manager
        self.embedding_generator = embedding_generator
        self.vector_index = vector_index
        # Where the saved vector index is, and its dimension, for a run that has no
        # index object (a sync without embeddings, a confirmed removal). The backup
        # before a large removal copies the files from the path. The index is opened
        # only after such a backup, to drop the removed tracks' vectors: opening an
        # index that cannot be loaded can destroy the file. A small removal makes no
        # backup, so it leaves a saved index as it is.
        self.saved_index_path = saved_index_path
        self.saved_index_dimension = saved_index_dimension
        self.ai_provider = ai_provider
        self.tag_generator = TagGenerator(ai_provider) if ai_provider else None
        # Outcome of the last sync's AI tagging, for callers that report provider
        # health (the Web UI's readiness): tracks tagged, and the provider error
        # that stopped tagging (None when it ran to the end or didn't run).
        self.tags_saved: int = 0
        self.tagging_error: Optional[FatalProviderError] = None
        # What the last sync did about unread items and removals. Every run starts
        # a new one, so callers read it after the run, also after a run that raised.
        self.outcome: SyncOutcome = SyncOutcome()
        # True once ``regenerate_sync`` has cleared the tags and embeddings. Callers
        # read it when that run then failed, to say what the run did change.
        self.cleared_before_sync: bool = False
        # Added to the history row of a failed sync (see ``REGENERATE_CLEARED_NOTE``).
        self._failure_note: str = ""
        # How far the last ``remove_unlisted_tracks`` call got, for a caller whose
        # call was interrupted (Ctrl+C) and so returned nothing: None until its
        # backup exists, then a result that names the backup with 0 removed, then
        # the removal itself.
        self.last_removal: Optional[RemovalResult] = None
        self.musicbrainz_settings = musicbrainz_settings
        # When set (CLI --audio / audio.analyze_on_sync), audio analysis runs as a
        # pipeline stage BEFORE embeddings so the embedding text includes audio
        # features. The UI runs its own audio pass and leaves this None.
        self.audio_settings = audio_settings

    def incremental_sync(
        self,
        generate_embeddings: bool = True,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        allow_large_removals: bool = False,
    ) -> SyncHistory:
        """Sync the library from Plex. ``self.outcome`` says what was kept, held or removed.

        A track is removed only when Plex's own listing leaves it out. A removal
        above the confirmation threshold (``removal_needs_confirmation``) is held
        unless ``allow_large_removals`` is True, and is then applied only after a
        backup. When Plex stops answering or cannot list the library, the client
        raises ``PlexReadInterrupted`` before anything is written.
        """
        logger.info("Starting incremental library sync")
        self.outcome = SyncOutcome()
        if not self._failure_note:
            # A plain sync clears nothing. ``regenerate_sync`` sets the note before it
            # calls this, so its own flag is kept.
            self.cleared_before_sync = False
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
            # After a regenerate has cleared (the note is set), "nothing was
            # changed" would be false.
            raise ConnectionError(
                _check_failed_message(token_msg, nothing_changed=not self._failure_note)
            )

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
                # What Plex listed, apart from what could be read. Only a real
                # report counts: without one the listing is unknown.
                read_report = self._accepted_read_report(plex_library)
                if read_report is not None:
                    self.outcome.unread = {kind: read_report.failed[kind] for kind in READ_KINDS}
                    self.outcome.listed = {kind: read_report.listed[kind] for kind in READ_KINDS}

                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                task = progress.add_task("Comparing with database...", total=None)
                changes = self._detect_library_changes(
                    plex_library, progress, task, progress_callback, cancel_event, 0.3, 0.45
                )
                stats.update(changes)
                # The compare stage has saved its work. A caller whose run fails in
                # a later stage reads this to say what was already changed.
                self.outcome.added = stats["tracks_added"]
                self.outcome.updated = stats["tracks_updated"]
                self.outcome.compared = True

                # The removal step below never runs after a cancel request.
                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                stats["tracks_removed"] = self._remove_tracks_plex_no_longer_lists(
                    read_report,
                    # Only a real True allows it: a placeholder object does not.
                    allow_large_removals is True,
                    cancel_event,
                    progress_callback,
                    0.45,
                )
                progress.update(
                    task,
                    description=f"Changes: +{stats['tracks_added']} ~{stats['tracks_updated']} -{stats['tracks_removed']}",
                )

                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                # MusicBrainz enrichment (between DB sync and AI tags). Presence of
                # musicbrainz_settings is the gate: callers (CLI --musicbrainz, UI)
                # only set it when enrichment is intended, so re-checking
                # enrich_on_sync here made the --musicbrainz flag a silent no-op.
                if self.musicbrainz_settings:
                    task = progress.add_task("Enriching with MusicBrainz...", total=None)
                    self._enrich_musicbrainz(
                        progress, task, progress_callback, cancel_event, 0.45, 0.55
                    )

                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                if self.tag_generator and self.ai_provider:
                    task = progress.add_task("Generating AI tags...", total=None)
                    self._generate_tags_for_untagged_tracks(
                        progress, task, progress_callback, cancel_event, 0.55, 0.65
                    )

                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                # Audio analysis runs BEFORE embeddings so audio features are in the
                # DB when the embedding text is built for newly synced tracks.
                if self.audio_settings is not None:
                    task = progress.add_task("Analyzing audio features...", total=None)
                    self._analyze_audio_features(
                        progress, task, progress_callback, cancel_event, 0.65, 0.75
                    )

                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Sync cancelled by user")

                if generate_embeddings and self.embedding_generator and self.vector_index:
                    task = progress.add_task("Generating embeddings...", total=None)
                    self._generate_embeddings_for_new_tracks(
                        progress, task, progress_callback, cancel_event, 0.75, 1.0
                    )

            # A run that kept unread items, or skipped or held removals, is recorded
            # as partial, with the reason.
            partial = self.outcome.is_partial
            sync_record = SyncHistory(
                tracks_added=stats["tracks_added"],
                tracks_updated=stats["tracks_updated"],
                tracks_removed=stats["tracks_removed"],
                status="partial" if partial else "success",
                error_message=self.outcome.describe() if partial else None,
            )
            self.db.insert_sync_record(sync_record)

            if progress_callback:
                progress_callback(
                    1.0,
                    (
                        "Incremental sync finished with warnings"
                        if partial
                        else "Incremental sync completed successfully"
                    ),
                )

            logger.info(
                f"Incremental sync completed: {stats['tracks_added']} added, "
                f"{stats['tracks_updated']} updated, {stats['tracks_removed']} removed"
            )
            if partial:
                logger.warning("Incremental sync was partial: %s", sync_record.error_message)
            return sync_record

        except KeyboardInterrupt:
            logger.warning("Sync interrupted by user")
            sync_record = SyncHistory(
                tracks_added=stats["tracks_added"],
                tracks_updated=stats["tracks_updated"],
                # The removal step records its deletes as soon as they are saved, so
                # an interrupt right after them still counts them.
                tracks_removed=max(stats["tracks_removed"], self.outcome.removed),
                status="interrupted",
                error_message="User interrupted sync",
            )
            self.db.insert_sync_record(sync_record)
            raise

        except Exception as e:
            # PlexAPI repeats the server address in its error text; hide any
            # credentials in it before the text reaches the log and the database.
            error_text = mask_urls_in_text(str(e))
            logger.error(f"Incremental sync failed: {error_text}")
            if self._failure_note:
                # "Nothing was changed" is about the library sync; say what was.
                error_text = f"{error_text} {self._failure_note}"
            # The compare stage and the removal step save their work when they end.
            # A later stage that fails leaves it in the library, so the row counts it.
            sync_record = SyncHistory(
                tracks_added=stats["tracks_added"],
                tracks_updated=stats["tracks_updated"],
                tracks_removed=max(stats["tracks_removed"], self.outcome.removed),
                status="failed",
                error_message=error_text,
            )
            self.db.insert_sync_record(sync_record)
            raise

    def regenerate_sync(
        self,
        generate_embeddings: bool = True,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        allow_large_removals: bool = False,
    ) -> SyncHistory:
        logger.info("Starting regenerate sync - will clear and regenerate all tags and embeddings")
        self.outcome = SyncOutcome()
        self.cleared_before_sync = False

        # Validate the Plex connection and honor cancellation BEFORE the
        # destructive clear. Otherwise a failed connection, or a Ctrl+C moments
        # after starting, wipes every tag and embedding with nothing regenerated —
        # discarding potentially hours of paid AI work.
        if progress_callback:
            progress_callback(0.0, "Validating Plex connection...")
        token_valid, token_msg = self.plex.validate_token()
        if not token_valid:
            logger.error("Plex token validation failed: %s", token_msg)
            raise ConnectionError(_check_failed_message(token_msg))
        if cancel_event and cancel_event.is_set():
            raise KeyboardInterrupt("Sync cancelled by user")

        if progress_callback:
            progress_callback(0.0, "Clearing existing tags and embeddings...")

        cursor = self.db.get_connection().cursor()
        cursor.execute("UPDATE tracks SET tags = NULL, environments = NULL, instruments = NULL")
        cursor.execute("DELETE FROM embeddings")
        self.db._commit()
        logger.info("Cleared all existing tags and embeddings")
        self.cleared_before_sync = True

        # A sync that fails from here on did change something: its history row says so.
        self._failure_note = REGENERATE_CLEARED_NOTE
        try:
            return self.incremental_sync(
                generate_embeddings=generate_embeddings,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                allow_large_removals=allow_large_removals,
            )
        finally:
            self._failure_note = ""

    def full_sync(
        self,
        generate_embeddings: bool = True,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        allow_large_removals: bool = False,
    ) -> SyncHistory:
        logger.info("Starting full library sync (alias for incremental_sync)")
        return self.incremental_sync(
            generate_embeddings=generate_embeddings,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            allow_large_removals=allow_large_removals,
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
                # Advance monotonically as items are indexed (the grand total isn't
                # known up front) instead of reporting a constant 0.5 fraction.
                indexed_fraction = min(0.95, total_items / (total_items + 1000.0))
                current_progress = (
                    progress_start + (progress_end - progress_start) * indexed_fraction
                )
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
        # The size of the library before this sync: the removal threshold is a share of it.
        self.outcome.library_total = len(db_tracks)

        # Pre-cache known genres to avoid per-track DB lookups
        known_genres: set = set()
        for genre_obj in self.db.get_all_genres():
            known_genres.add(genre_obj.name)

        with self.db.deferred_commits():
            # Both maps start from the rows the database already holds, and the items
            # read from Plex overwrite them. A track PlexMix holds then keeps its artist
            # and album when that artist's or album's read failed; without this it
            # would be moved to "Unknown Artist" or "Unknown Album".
            artist_map: Dict[str, int] = {
                key: artist.id for key, artist in db_artists.items() if artist.id is not None
            }
            for plex_key, plex_artist in plex_library["artists"].items():
                existing_artist = db_artists.get(plex_key)
                # Upsert new artists, and refresh existing ones whose metadata
                # changed in Plex — previously existing rows were only mapped by
                # id, so renames/genre edits never propagated to the DB.
                if (
                    existing_artist is None
                    or existing_artist.id is None
                    or existing_artist.name != plex_artist.name
                    or existing_artist.genre != plex_artist.genre
                    or existing_artist.bio != plex_artist.bio
                ):
                    artist_map[plex_key] = self.db.insert_artist(plex_artist)
                else:
                    artist_map[plex_key] = existing_artist.id

            album_map: Dict[str, int] = {
                key: album.id for key, album in db_albums.items() if album.id is not None
            }
            for plex_key, plex_album in plex_library["albums"].items():
                artist_plex_key = plex_album.__dict__.get("_artist_key")
                existing_album = db_albums.get(plex_key)
                if artist_plex_key and artist_plex_key in artist_map:
                    plex_album.artist_id = artist_map[artist_plex_key]
                elif existing_album is not None and existing_album.id is not None:
                    # Plex names no artist for it, or one that is in neither map: that
                    # artist could not be read in this run and is new to PlexMix. An
                    # album PlexMix holds keeps the artist it has; only a new album
                    # goes to "Unknown Artist".
                    plex_album.artist_id = existing_album.artist_id
                else:
                    logger.warning(
                        f"Album '{plex_album.title}' missing artist link; assigning to Unknown Artist"
                    )
                    plex_album.artist_id = unknown_artist_id

                # Upsert new albums, and refresh existing ones on metadata change
                # (title, artist link, year, genre, cover art) so Plex edits sync.
                if (
                    existing_album is None
                    or existing_album.id is None
                    or existing_album.title != plex_album.title
                    or existing_album.artist_id != plex_album.artist_id
                    or existing_album.year != plex_album.year
                    or existing_album.genre != plex_album.genre
                    or existing_album.cover_art_url != plex_album.cover_art_url
                ):
                    album_map[plex_key] = self.db.insert_album(plex_album)
                else:
                    album_map[plex_key] = existing_album.id

            total_tracks = len(plex_library["tracks"])
            processed = 0

            for plex_key, plex_track in plex_library["tracks"].items():
                if cancel_event and cancel_event.is_set():
                    break

                artist_plex_key = plex_track.__dict__.get("_artist_key")
                album_plex_key = plex_track.__dict__.get("_album_key")
                existing = db_tracks.get(plex_key)

                # An artist or album that is in neither map could not be read in this
                # run and is new to PlexMix. A track PlexMix holds then keeps the link
                # it has; only a new track goes to "Unknown Artist" or "Unknown Album".
                if artist_plex_key and artist_plex_key in artist_map:
                    plex_track.artist_id = artist_map[artist_plex_key]
                elif existing is not None:
                    plex_track.artist_id = existing.artist_id
                else:
                    plex_track.artist_id = unknown_artist_id

                if album_plex_key and album_plex_key in album_map:
                    plex_track.album_id = album_map[album_plex_key]
                elif existing is not None:
                    plex_track.album_id = existing.album_id
                else:
                    plex_track.album_id = unknown_album_id

                if existing is not None:
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

        # Nothing is removed here. Removal is its own step, after this stage has
        # committed: see ``_remove_tracks_plex_no_longer_lists``.
        progress.update(
            task,
            description=f"Changes: +{stats['tracks_added']} ~{stats['tracks_updated']}",
        )
        return stats

    def _accepted_read_report(self, plex_library: Dict[str, Any]) -> Optional[PlexReadReport]:
        """The client's report of what Plex listed, or None when there is no real one.

        Only a ``PlexReadReport`` counts, with a set of listed keys and plain counts.
        It must also hold every track that was read, because they all came out of
        the listing; a report without them describes some other listing. With None
        the listing is unknown, and a sync then removes nothing.
        """
        report = getattr(self.plex, "read_report", None)
        if not isinstance(report, PlexReadReport):
            return None
        if not isinstance(report.listed_track_keys, frozenset):
            return None
        for table in (report.listed, report.failed):
            if not isinstance(table, dict):
                return None
            for kind in READ_KINDS:
                count = table.get(kind)
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    return None
        if not report.listed_track_keys.issuperset(plex_library["tracks"]):
            return None
        return report

    def _index_path(self) -> Optional[Path]:
        """Where the vector index is saved, for the backup. None when that is not known.

        The path of the index object when the run has one, else ``saved_index_path``.
        """
        if self.vector_index is None:
            index_path: Any = self.saved_index_path
        else:
            index_path = getattr(self.vector_index, "index_path", None)
        return Path(index_path) if isinstance(index_path, (str, Path)) else None

    def _open_saved_index(self) -> Optional[VectorIndex]:
        """Open the saved vector index of a run that has no index object, or None.

        Call it only after a backup has copied the index files: an index file that
        cannot be loaded does not survive the attempt. An index that holds no
        vectors after loading is not returned: there is nothing to drop from it, and
        saving it would put an empty index where an unreadable one was.
        """
        path = self._index_path()
        dimension = self.saved_index_dimension
        if path is None or isinstance(dimension, bool) or not isinstance(dimension, int):
            return None
        try:
            if dimension <= 0 or not path.expanduser().exists():
                return None
            index = VectorIndex(dimension=dimension, index_path=str(path))
            if index.index is None or index.index.ntotal == 0:
                logger.warning(
                    "The saved vector index holds no vectors (it may not have been "
                    "readable), so the removed tracks were not dropped from it."
                )
                return None
            return index
        except Exception as e:
            logger.warning(
                "Could not open the vector index to drop removed tracks: %s",
                mask_urls_in_text(str(e)),
            )
            return None

    def _delete_rows(
        self,
        track_ids: List[int],
        cancel_event: Optional[Event] = None,
        history: Optional[SyncHistory] = None,
    ) -> None:
        """Delete the tracks with these ids in one transaction: all of them or none.

        A cancel request (``cancel_event``) that arrives before the deletes are
        saved raises ``_RemovalCancelled``. That, an error and Ctrl+C each undo
        every delete of this call. ``history`` is written in the same transaction,
        so a removal and its history row are saved together or not at all.
        """
        try:
            with self.db.deferred_commits():
                for track_id in track_ids:
                    if cancel_event is not None and cancel_event.is_set():
                        raise _RemovalCancelled()
                    self.db.delete_track(track_id)
                if cancel_event is not None and cancel_event.is_set():
                    raise _RemovalCancelled()
                if history is not None:
                    self.db.insert_sync_record(history)
        except BaseException:
            # ``deferred_commits`` rolls back on an ``Exception`` only. Ctrl+C would
            # leave a half-applied removal in an open transaction, and the next commit
            # (the history row of the interrupted sync) would make it permanent.
            self.db.get_connection().rollback()
            raise

    def _update_index(self, track_ids: List[int], backed_up: bool) -> None:
        index = self.vector_index
        if index is None and backed_up:
            index = self._open_saved_index()
        if (
            index is not None
            and index.index is not None
            and index.index_path is not None
            and not index.dimension_mismatch
        ):
            try:
                index.remove_vectors(track_ids)
                index.save_index(str(index.index_path))
            except Exception as e:
                logger.warning(
                    "Failed to remove %d deleted vectors from FAISS index: %s",
                    len(track_ids),
                    mask_urls_in_text(str(e)),
                )

    def _drop_vectors(self, track_ids: List[int], backed_up: bool = False) -> None:
        """Drop the vectors of deleted tracks from the vector index, so search never returns them.

        ``backed_up`` says that a backup of the library was made for this removal.
        Only then is a saved index opened by a run that has no index object.

        The tracks are gone from the database when this runs, and the caller has
        recorded that. So a failure here is logged and not raised, and after Ctrl+C
        the index is updated once more before the interrupt goes on: a save that was
        cut off would leave the removed tracks in the index, with nothing to repair it.
        """
        if not track_ids:
            return
        try:
            self._update_index(track_ids, backed_up)
        except KeyboardInterrupt:
            logger.warning("Interrupted while the vector index was updated; finishing the update")
            self._update_index(track_ids, backed_up)
            raise

    def _remove_tracks_plex_no_longer_lists(
        self,
        read_report: Optional[PlexReadReport],
        allow_large_removals: bool,
        cancel_event: Optional[Event] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        progress_at: float = 0.45,
    ) -> int:
        """Remove the tracks that Plex's listing leaves out. Return how many were removed.

        This is the only place where a sync removes tracks. It runs after the compare
        stage has committed, so a backup is never made inside an open transaction.
        A track is a candidate only when its key is not in the listing: a track that
        was listed but could not be read is kept. When nothing may be removed, the
        reason goes into ``self.outcome``. ``allow_large_removals`` lifts the
        confirmation threshold and nothing else.
        """
        outcome = self.outcome
        if cancel_event is not None and cancel_event.is_set():
            return 0

        db_tracks = self.db.get_all_tracks()

        if read_report is None:
            # No report from the client: what Plex listed is unknown.
            if db_tracks:
                outcome.reason = REASON_LISTING_UNKNOWN
                outcome.message = f"Plex's track listing was not available. {_NOTHING_REMOVED}"
                logger.warning(
                    "No listing report from the Plex client; skipping the removal step "
                    "(the database holds %d tracks).",
                    len(db_tracks),
                )
            return 0

        listed_keys = read_report.listed_track_keys

        # Safety guard: never mass-delete when Plex listed zero tracks but the DB is
        # populated. A mis-selected or emptied library is indistinguishable here from
        # a listing that came back short; deleting on it would wipe the entire local
        # database (with cascades to embeddings, tags, audio features, and playlist
        # contents). ``allow_large_removals`` does not lift this guard.
        if not listed_keys and db_tracks:
            logger.warning(
                "Plex returned 0 tracks but the database holds %d; skipping the "
                "deletion phase to avoid wiping the library. Re-run sync once Plex "
                "is returning tracks, or clear the database intentionally if the "
                "library really is empty.",
                len(db_tracks),
            )
            outcome.reason = REASON_EMPTY_LISTING
            outcome.message = (
                f"Plex listed 0 tracks, but the PlexMix library holds {len(db_tracks):,}. "
                f"{_NOTHING_REMOVED} If the Plex library really is empty, run "
                "'plexmix db reset' to clear the PlexMix library."
            )
            return 0

        # Many failed reads: Plex is not trusted about what is gone in this run.
        unreliable = [
            kind
            for kind in READ_KINDS
            if too_many_failed_reads(read_report.failed[kind], read_report.listed[kind])
        ]
        if unreliable:
            counts = " and ".join(
                f"{read_report.failed[kind]:,} of {read_report.listed[kind]:,} {kind}"
                for kind in unreliable
            )
            outcome.reason = REASON_TOO_MANY_UNREAD
            outcome.message = f"{counts} could not be read and were kept. {_NOTHING_REMOVED}"
            logger.warning("Too many reads failed (%s); skipping the removal step.", counts)
            return 0

        unlisted = [track for track in db_tracks if track.plex_key not in listed_keys]
        if not unlisted:
            return 0

        if removal_needs_confirmation(len(unlisted), outcome.library_total):
            if not allow_large_removals:
                outcome.held = len(unlisted)
                outcome.held_keys = frozenset(track.plex_key for track in unlisted)
                outcome.reason = REASON_THRESHOLD
                outcome.message = (
                    f"Plex no longer lists {len(unlisted):,} of {outcome.library_total:,} "
                    "tracks. They were kept, because a removal this large needs confirmation."
                )
                logger.warning(
                    "Plex no longer lists %d of %d tracks; kept them, because a removal "
                    "this large needs confirmation.",
                    len(unlisted),
                    outcome.library_total,
                )
                return 0

            if progress_callback:
                progress_callback(
                    progress_at,
                    f"Backing up the library before removing {len(unlisted):,} tracks...",
                )
            try:
                backup = backup_library(self.db, self._index_path())
            except Exception as e:
                # Any failure of the backup: without a copy, nothing is removed.
                outcome.reason = REASON_BACKUP_FAILED
                outcome.message = _backup_failed_message(e)
                logger.error(
                    "Backup before removing %d tracks failed; nothing was removed: %s",
                    len(unlisted),
                    mask_urls_in_text(str(e)),
                )
                return 0
            outcome.backup_folder = backup.folder
            outcome.backup_size_bytes = backup.size_bytes
            if cancel_event is not None and cancel_event.is_set():
                # Cancelled while the backup was written: the removal does not run.
                # The copy stays where it is, and the outcome names it.
                return 0

        track_ids = [track.id for track in unlisted if track.id is not None]
        try:
            self._delete_rows(track_ids, cancel_event)
        except _RemovalCancelled:
            # Cancelled while the tracks were being deleted: every delete was undone.
            return 0
        # Recorded before the index is touched. The tracks are gone from here on,
        # whatever stops the run next.
        outcome.removed = len(track_ids)
        logger.info("Removed %d tracks that Plex no longer lists", outcome.removed)
        self._drop_vectors(track_ids, backed_up=outcome.backup_folder is not None)
        return outcome.removed

    def remove_unlisted_tracks(
        self,
        confirmed_count: int,
        held_keys: Optional[FrozenSet[str]] = None,
        cancel_event: Optional[Event] = None,
    ) -> RemovalResult:
        """Apply a removal that was confirmed, against Plex's listing as it is now.

        ``confirmed_count`` is the number of tracks the user agreed to remove (the
        ``held`` count of the sync that asked), and ``held_keys`` are the rating keys
        of those tracks (``SyncOutcome.held_keys``). Plex is listed again, keys only.
        Only tracks that are still not listed are removed, after a backup. With
        ``held_keys``, a track that went missing after the question was asked is
        kept and counted in ``RemovalResult.other_missing``: nobody confirmed it.
        Without ``held_keys`` (None) only the count is checked. An empty set is not
        "no keys": it is refused when tracks were confirmed, and so are keys that
        are not text, because no track could match them.

        Nothing is removed when the listing fails or is empty, when Plex lists the
        tracks again, when more tracks are missing than were confirmed, when the
        backup fails, when the delete fails, or when ``cancel_event`` is set before
        the deletes are saved. The deletes are one transaction, so a removal is
        applied whole or not at all. ``last_removal`` says how far a call got that
        was interrupted.
        """
        if (
            isinstance(confirmed_count, bool)
            or not isinstance(confirmed_count, int)
            or confirmed_count < 0
        ):
            raise ValueError("confirmed_count must be the number of tracks that was confirmed")
        if held_keys is not None:
            if not isinstance(held_keys, (frozenset, set)) or not all(
                isinstance(key, str) for key in held_keys
            ):
                raise ValueError(
                    "held_keys must be the rating keys of the tracks that were confirmed"
                )
            if not held_keys and confirmed_count > 0:
                raise ValueError(
                    "held_keys is empty although tracks were confirmed; pass None when "
                    "the keys are not known"
                )
        self.last_removal = None

        try:
            listed_keys = self.plex.list_track_keys()
        except Exception as e:
            logger.error(
                "Could not list Plex before a confirmed removal: %s", mask_urls_in_text(str(e))
            )
            listed_keys = None
        if not isinstance(listed_keys, (frozenset, set)):
            return RemovalResult(
                status=REMOVAL_LISTING_FAILED,
                message=f"Plex could not list the library's tracks. {_NOTHING_REMOVED}",
            )

        db_tracks = self.db.get_all_tracks()
        library_total = len(db_tracks)
        cancelled_message = f"The removal was cancelled. {_NOTHING_REMOVED}"
        if cancel_event is not None and cancel_event.is_set():
            # Cancelled while Plex was being listed: no backup is started.
            return RemovalResult(
                status=REMOVAL_CANCELLED, library_total=library_total, message=cancelled_message
            )
        if not listed_keys:
            # An empty listing never removes anything, confirmed or not.
            return RemovalResult(
                status=REMOVAL_LISTING_FAILED,
                library_total=library_total,
                message=f"Plex listed 0 tracks. {_NOTHING_REMOVED}",
            )

        unlisted = [track for track in db_tracks if track.plex_key not in listed_keys]
        missing = len(unlisted)
        if not unlisted:
            return RemovalResult(
                status=REMOVAL_LISTED_AGAIN,
                library_total=library_total,
                message=f"Plex lists those tracks again. {_NOTHING_REMOVED}",
            )
        if missing > confirmed_count:
            return RemovalResult(
                status=REMOVAL_MORE_MISSING,
                missing=missing,
                library_total=library_total,
                message=(
                    f"Plex no longer lists {_counted(missing, 'tracks')}, more than the "
                    f"{confirmed_count:,} that were confirmed. {_NOTHING_REMOVED}"
                ),
                missing_keys=frozenset(track.plex_key for track in unlisted),
            )

        # Only the tracks that were confirmed. One that Plex listed when the question
        # was asked and leaves out now was never shown to the user, so it is kept.
        confirmed = (
            unlisted
            if held_keys is None
            else [track for track in unlisted if track.plex_key in held_keys]
        )
        other_missing = missing - len(confirmed)
        other_sentence = _other_missing_sentence(other_missing)
        # Of the confirmed tracks, the ones Plex lists now. Only the keys can say.
        listed_again = 0 if held_keys is None else len(held_keys & listed_keys)
        if not confirmed:
            # None of the confirmed tracks is missing. They are listed again, unless
            # they are no longer in the PlexMix library at all.
            lead = (
                "Plex lists those tracks again."
                if listed_again
                else "The confirmed tracks are no longer in the PlexMix library."
            )
            return RemovalResult(
                status=REMOVAL_LISTED_AGAIN,
                missing=missing,
                library_total=library_total,
                message=f"{lead} {_NOTHING_REMOVED}{other_sentence}",
                other_missing=other_missing,
                listed_again=listed_again,
            )

        try:
            backup = backup_library(self.db, self._index_path())
        except Exception as e:
            logger.error(
                "Backup before removing %d tracks failed; nothing was removed: %s",
                len(confirmed),
                mask_urls_in_text(str(e)),
            )
            return RemovalResult(
                status=REMOVAL_BACKUP_FAILED,
                missing=missing,
                library_total=library_total,
                message=_backup_failed_message(e),
            )

        # The backup exists and nothing is removed yet. A cancelled or interrupted
        # removal names the copy, which stays where it is.
        cancelled = RemovalResult(
            status=REMOVAL_CANCELLED,
            missing=missing,
            library_total=library_total,
            message=cancelled_message,
            backup_folder=backup.folder,
            backup_size_bytes=backup.size_bytes,
        )
        self.last_removal = cancelled
        if cancel_event is not None and cancel_event.is_set():
            # Cancelled while the backup was written.
            return cancelled

        track_ids = [track.id for track in confirmed if track.id is not None]
        removed = len(track_ids)
        history = SyncHistory(
            tracks_removed=removed,
            status="partial" if other_missing else "success",
            error_message=other_sentence.strip() or None,
        )
        try:
            # The history row is saved with the deletes, in one transaction.
            self._delete_rows(track_ids, cancel_event, history)
        except _RemovalCancelled:
            # Cancelled while the tracks were being deleted: every delete was undone.
            return cancelled
        except Exception as e:
            logger.error(
                "Removing %d tracks failed; nothing was removed: %s",
                removed,
                mask_urls_in_text(str(e)),
            )
            self.last_removal = RemovalResult(
                status=REMOVAL_FAILED,
                missing=missing,
                library_total=library_total,
                message=_removal_failed_message(e),
                backup_folder=backup.folder,
                backup_size_bytes=backup.size_bytes,
            )
            return self.last_removal

        again_sentence = _listed_again_sentence(listed_again)
        result = RemovalResult(
            status=REMOVAL_REMOVED,
            removed=removed,
            missing=missing,
            library_total=library_total,
            message=(
                f"Removed {_counted(removed, 'tracks')} that Plex no longer lists."
                f"{again_sentence}{other_sentence}"
            ),
            backup_folder=backup.folder,
            backup_size_bytes=backup.size_bytes,
            other_missing=other_missing,
            listed_again=listed_again,
        )
        # Recorded before the index is touched. The tracks are gone from here on,
        # whatever stops the call next.
        self.last_removal = result
        logger.info("Removed %d tracks that Plex no longer lists (confirmed removal)", removed)
        self._drop_vectors(track_ids, backed_up=True)
        return result

    def _track_needs_update(self, db_track: Track, plex_track: Track) -> bool:
        return (
            db_track.title != plex_track.title
            # The links too. The compare stage has set them on ``plex_track``, and for
            # an artist or album it could not read it has set the links the database
            # holds. So a difference means Plex moved the track, or a track that an
            # earlier sync filed under "Unknown Artist" can now be linked.
            or db_track.artist_id != plex_track.artist_id
            or db_track.album_id != plex_track.album_id
            or db_track.year != plex_track.year
            or db_track.genre != plex_track.genre
            or db_track.duration_ms != plex_track.duration_ms
            or db_track.rating != plex_track.rating
            or db_track.play_count != plex_track.play_count
            or db_track.last_played != plex_track.last_played
            # Detect relocated/renamed media so audio analysis (which needs a
            # valid file_path) picks up the new location on the next sync.
            or db_track.file_path != plex_track.file_path
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
        # Scope to the active embedding model so switching provider/model re-embeds
        # tracks that were only embedded by a previous model.
        existing_track_ids = self.db.get_track_ids_with_embeddings(
            self.embedding_generator.provider_name
        )
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
            # Rebuild the FAISS index from the DB (which holds the partial results)
            # so the index doesn't diverge from the embeddings table after an
            # interrupt. The rebuild is scoped to the active model.
            if self.vector_index is not None and embeddings_saved > 0:
                try:
                    partial = self.db.get_all_embeddings(self.embedding_generator.provider_name)
                    if partial:
                        self.vector_index.build_index(
                            [v for _, v in partial], [t for t, _ in partial]
                        )
                        if self.vector_index.index_path is not None:
                            self.vector_index.save_index(str(self.vector_index.index_path))
                except Exception as e:
                    logger.warning("Failed to persist partial embeddings to index: %s", e)
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
            # Full rebuild needed (new index or dimension mismatch). Scope to the
            # active model so stale vectors from a previous provider/model don't
            # get mixed in (which would crash the build with ragged dimensions).
            all_embeddings = self.db.get_all_embeddings(self.embedding_generator.provider_name)
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
        self.tags_saved = 0
        self.tagging_error = None

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

        def _save_tag_results(results: Dict[int, Dict[str, Any]]) -> int:
            saved = 0
            for track in tracks_needing_tags:
                if track.id in results:
                    result = results[track.id]
                    self.db.update_track_tags(
                        track.id,
                        tags=",".join(result.get("tags", [])),
                        environments=",".join(result.get("environments", [])),
                        instruments=",".join(result.get("instruments", [])),
                    )
                    saved += 1
            return saved

        partial_results: Dict[int, Dict[str, Any]] = {}
        try:
            tag_results = self.tag_generator.generate_tags_batch(
                track_data_list,
                batch_size=20,
                progress_callback=tag_progress_callback,
                cancel_event=cancel_event,
                result_sink=partial_results,
            )
            tags_saved = _save_tag_results(tag_results)
            self.tags_saved = tags_saved
            progress.update(task, description=f"Generated tags for {tags_saved} tracks")
            logger.info(f"Generated AI tags for {tags_saved} tracks")

        except FatalProviderError as e:
            # Unrecoverable error (e.g. bad API key): stop tagging, keep partial
            # results, surface the reason once, and let the rest of sync proceed.
            tags_saved = _save_tag_results(e.partial_results)
            self.tags_saved = tags_saved
            self.tagging_error = e
            logger.error("AI tagging stopped after %d tracks: %s", tags_saved, e.user_message)
            progress.update(task, description=f"AI tagging stopped: {e.user_message}")
            if progress_callback:
                progress_callback(progress_end, f"AI tagging stopped: {e.user_message}")
        except KeyboardInterrupt:
            # Persist tags generated before the interrupt so they aren't discarded
            # (the CLI/UI tells the user "progress saved").
            tags_saved = _save_tag_results(partial_results)
            logger.warning("Tag generation interrupted; saved %d tagged tracks", tags_saved)
            raise
        except Exception as e:
            logger.error(f"Tag generation failed: {e}")
            progress.update(task, description="Tag generation failed")

    def _analyze_audio_features(
        self,
        progress: Progress,
        task: Any,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
    ) -> None:
        if self.audio_settings is None:
            return

        try:
            from ..audio.analyzer import EssentiaAnalyzer

            analyzer = EssentiaAnalyzer()
        except ImportError:
            logger.warning("essentia not available, skipping audio analysis")
            progress.update(task, description="Audio: Essentia not installed")
            return

        tracks = self.db.get_tracks_without_audio_features()
        if not tracks:
            progress.update(task, description="No tracks need audio analysis")
            return

        progress.update(task, total=len(tracks))
        logger.info(f"Analyzing audio features for {len(tracks)} tracks")
        duration_limit = self.audio_settings.duration_limit
        analyzed = 0
        errors = 0

        for track in tracks:
            if cancel_event and cancel_event.is_set():
                break
            if not track.file_path or track.id is None:
                continue
            try:
                resolved = self.audio_settings.resolve_path(track.file_path)
                features = analyzer.analyze(resolved, duration_limit=duration_limit)
                self.db.insert_audio_features(track.id, features.to_dict())
                analyzed += 1
            except Exception as e:
                errors += 1
                logger.debug("Audio analysis failed for %s: %s", track.title, e)

            processed = analyzed + errors
            progress.update(task, completed=processed)
            if progress_callback and processed % 5 == 0:
                current_progress = progress_start + (progress_end - progress_start) * (
                    processed / len(tracks)
                )
                progress_callback(
                    current_progress, f"Analyzing audio... ({processed}/{len(tracks)})"
                )

        progress.update(task, description=f"Audio: {analyzed} analyzed, {errors} errors")
        logger.info(f"Audio analysis: {analyzed} analyzed, {errors} errors")

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
