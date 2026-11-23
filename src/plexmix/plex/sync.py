from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional, Dict, Callable, FrozenSet, Iterable, List, Set, Tuple
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
from ..database.index_rebuild import (
    IndexRebuild,
    left_out_sentence,
    rebuild_index,
    tracks_in_the_way,
)
from ..database.sqlite_manager import SQLiteManager
from ..database.models import Artist, Album, Track, Genre, SyncHistory
from ..database.vector_index import VectorIndex
from ..utils.embeddings import EmbeddingGenerator, create_track_text
from ..utils.redaction import REDACTED, mask_urls_in_text, redact_partly_hidden_keys
from ..ai.tag_generator import RepeatedBatchFailure, TagGenerator, clean_tag_list
from ..ai.errors import FatalProviderError
from ..ai.base import AIProvider
from ..config.settings import MusicBrainzSettings
from ..services.regenerate_service import (
    COMPONENT_AI,
    COMPONENT_EMBEDDINGS,
    COMPONENT_INDEX,
    SCOPE_EMBEDDINGS,
    SCOPE_TAGS,
    RegenerateRefused,
    RegenerateRun,
    RunProvider,
    finish_scope,
    open_run,
    preview_run,
    remaining_track_ids,
)

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

# Tracks per request to the AI provider and to the embedding provider. Every
# batch is saved before the next request is sent.
_TAG_BATCH_SIZE = 20
_EMBEDDING_BATCH_SIZE = 50

# A regenerate run stops its tags pass when this many requests in a row were not
# answered: the AI provider is no longer reached, and a run that went on would keep
# every track that is left and then pass for finished. A request that was answered
# without usable text does not count (``tag_generator.provider_did_not_answer``).
_MAX_FAILED_TAG_BATCHES = 3

_REDACTED = REDACTED
_MIN_SECRET_CHARS = 4
_ERROR_TEXT_MAX_CHARS = 300


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


def _check_failed_message(reason: Any) -> str:
    """The text of a failed connection check: the client's reason, then what that means.

    The check runs before a sync reads or writes anything. A regenerate run has
    changed nothing by then either: it clears nothing, and it replaces tags and
    embeddings only after the library was compared with Plex.
    """
    text = str(reason or "").strip() or "The Plex connection could not be checked"
    if not text.endswith((".", "!", "?")):
        text += "."
    return f"{text} {_NOTHING_CHANGED}"


def _sentence(text: str) -> str:
    """``text`` with a full stop when it ends without one; "" stays ""."""
    text = text.strip()
    if text and not text.endswith((".", "!", "?", "…")):
        text += "."
    return text


def _redact(text: str, secrets: Iterable[str] = ()) -> str:
    """``text`` without known key values and without credentials inside an address.

    A key that the provider repeats with its middle hidden goes as well, by the
    rule the regenerate check and the CLI use (``redact_partly_hidden_keys``).
    """
    for secret in secrets:
        if len(secret) >= _MIN_SECRET_CHARS:
            text = text.replace(secret, _REDACTED)
    return mask_urls_in_text(redact_partly_hidden_keys(text))


def _plain_error_text(error: BaseException, secrets: Iterable[str] = ()) -> str:
    """An error as one line that is safe to log and to store.

    Known key values (``secrets``) are replaced, credentials inside an address are
    masked, and the text is capped.
    """
    text = " ".join(_redact(str(error) or type(error).__name__, secrets).split())
    if len(text) > _ERROR_TEXT_MAX_CHARS:
        text = text[: _ERROR_TEXT_MAX_CHARS - 1] + "…"
    return text


def _holds_no_vectors(index: Any) -> bool:
    """True when the loaded index is known to hold no vector (a test double states none)."""
    held = getattr(getattr(index, "index", None), "ntotal", None)
    return isinstance(held, int) and not isinstance(held, bool) and held == 0


def _progress_display() -> Progress:
    """The progress display of a run: one line per stage."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
    )


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

    ``index_left_out`` is not 0 when the embeddings stage of a plain sync could not
    rebuild the search index: that many tracks hold an embedding of another model
    or vector size, so the saved index was left as it is (see
    ``plexmix.database.index_rebuild``). ``index_sentence`` is the text to show.
    The sync itself did its work, and the run still counts as partial.

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
    index_left_out: int = 0

    @property
    def unread_tracks(self) -> int:
        """Tracks that Plex listed and whose details could not be read."""
        return self.unread.get("tracks", 0)

    @property
    def index_sentence(self) -> str:
        """What to say about a search index that was left as it is; "" when it was not."""
        return left_out_sentence(self.index_left_out)

    @property
    def is_partial(self) -> bool:
        """True when items were unread, removals were skipped or held, or the search
        index could not be rebuilt."""
        return (
            bool(self.reason)
            or any(self.unread.get(kind, 0) for kind in READ_KINDS)
            or self.index_left_out > 0
        )

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
        if self.index_left_out > 0:
            parts.append(self.index_sentence)
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


@dataclass
class RegeneratePass:
    """One pass of a regenerate run: the tags or the embeddings.

    ``requested`` says that the run was asked to do this pass, and ``started`` that
    the pass began. ``continued`` is True when it took up an unfinished earlier run.
    ``done_earlier`` is True for a tags pass that the earlier run had finished: this
    run then tags only the tracks that have no tags at all. ``finished`` is True
    when the pass reached its end, so no interrupt, cancel request or error stopped
    it. ``error`` is what stopped it, as it was raised. Its text is not redacted, so
    never print or log it.

    ``to_do`` tracks were to be replaced and ``written`` rows were written. ``kept``
    tracks of the to-do set were not replaced and hold the data they had before. A
    pass that was requested and never started has every track to do and kept.
    ``replaced_earlier`` tracks had been replaced since the marker's start time
    when this pass took the run up again (0 for a new run): the work of the run it
    continues.
    """

    requested: bool = False
    started: bool = False
    continued: bool = False
    done_earlier: bool = False
    finished: bool = False
    error: Optional[BaseException] = field(default=None, repr=False, compare=False)
    to_do_ids: Set[int] = field(default_factory=set, repr=False)
    written_ids: Set[int] = field(default_factory=set, repr=False)
    replaced_earlier: int = 0

    @property
    def to_do(self) -> int:
        """Tracks this pass was to replace."""
        return len(self.to_do_ids)

    @property
    def written(self) -> int:
        """Rows this pass wrote."""
        return len(self.written_ids)

    @property
    def kept_ids(self) -> Set[int]:
        """The tracks of the to-do set that were not replaced."""
        return self.to_do_ids - self.written_ids

    @property
    def kept(self) -> int:
        """Tracks of the to-do set that hold the data they had before."""
        return len(self.kept_ids)


@dataclass
class RegenerateResult:
    """What one regenerate run did, for the caller's summary and exit status.

    ``SyncEngine.regenerate_result`` holds a new one for every run of
    ``regenerate_sync`` or ``regenerate_local``, also for a run that raises or is
    interrupted. After any other sync it is empty (``requested`` is False).

    ``tags`` and ``embeddings`` are the two passes. ``stopped_scope`` names the pass
    that an error stopped part-way (``SCOPE_TAGS`` or ``SCOPE_EMBEDDINGS``, "" for
    none). Such an error is not raised: the run ends, and ``error`` holds it for
    classification (not redacted: never print or log it) while ``error_message`` is
    the text to show. ``other_model_rows_removed`` embedding rows of other models
    were removed after the embeddings pass replaced every track.

    ``index_left_out`` is not 0 when the embeddings pass reached its end and the
    search index could still not be rebuilt: that many tracks hold only an
    embedding of another model or vector size, so the saved index was left as it
    is. ``index_sentence`` is the text to show. Such a run is not a clean finish.
    After a pass that was stopped part-way the index is left as it is too, and
    that is not recorded here: the stop is what the run reports.
    """

    tags: RegeneratePass = field(default_factory=RegeneratePass)
    embeddings: RegeneratePass = field(default_factory=RegeneratePass)
    stopped_scope: str = ""
    error: Optional[BaseException] = field(default=None, repr=False, compare=False)
    error_message: str = ""
    other_model_rows_removed: int = 0
    index_left_out: int = 0

    @property
    def requested(self) -> bool:
        """True for the result of a regenerate run."""
        return self.tags.requested or self.embeddings.requested

    @property
    def index_sentence(self) -> str:
        """What to say about a search index that was left as it is; "" when it was not."""
        return left_out_sentence(self.index_left_out)

    @property
    def nothing_to_do(self) -> bool:
        """True when the run reached its end and had no track to do: an empty library.

        Such a run wrote nothing and is no failure.
        """
        parts = [part for part in (self.tags, self.embeddings) if part.requested]
        return bool(parts) and all(part.finished and part.to_do == 0 for part in parts)

    @property
    def tags_written(self) -> int:
        """Tag rows written in this run."""
        return self.tags.written

    @property
    def embeddings_written(self) -> int:
        """Embedding rows written in this run."""
        return self.embeddings.written

    @property
    def rows_written(self) -> int:
        """Tag rows and embedding rows written in this run."""
        return self.tags.written + self.embeddings.written

    @property
    def wrote_nothing(self) -> bool:
        """True when the run wrote no tags and no embeddings."""
        return self.rows_written == 0

    @property
    def tracks_kept(self) -> int:
        """Tracks that hold previous tags or a previous embedding this run was to replace."""
        return len(self.tags.kept_ids | self.embeddings.kept_ids)

    @property
    def provider_failed(self) -> bool:
        """True when an error stopped a pass part-way."""
        return bool(self.stopped_scope)

    @property
    def finished(self) -> bool:
        """True when every pass the run was asked to do reached its end."""
        return all(part.finished for part in (self.tags, self.embeddings) if part.requested)

    @property
    def no_new_tags(self) -> bool:
        """True when the tags pass reached its end and no tags were replaced since
        the marker's start time, in this run or in the run it continues.

        The AI provider then gave nothing usable for any track it was sent. Such a
        run makes no embeddings either: their text would hold the tags from before,
        so every one of them would be made again from what it was made from. False
        for a pass that had no track to do, for a run that continues behind tags an
        earlier run finished, and for a continued pass whose earlier run replaced
        tags (``replaced_earlier``): the tracks that are left then keep their
        previous tags, and the run goes on to the embeddings.
        """
        tags = self.tags
        return (
            tags.requested
            and tags.finished
            and not tags.done_earlier
            and tags.to_do > 0
            and tags.written == 0
            and tags.replaced_earlier == 0
        )

    def describe(self) -> str:
        """What the run left undone, for its history row. "" for a run that did its work."""
        if not self.requested:
            return ""
        if self.error_message:
            lead = self.error_message
        elif self.no_new_tags and self.embeddings.requested and not self.embeddings.started:
            # The embeddings pass was left out for that reason: it did not start.
            lead = "No tags were regenerated, so no embedding was regenerated either."
        elif not self.finished:
            lead = "The regenerate run was stopped before it reached its end."
        elif self.wrote_nothing and not self.nothing_to_do:
            wanted = "tags or embeddings" if self.embeddings.requested else "tags"
            lead = f"No {wanted} were regenerated."
        else:
            # The run did its work. What is left to say is an index it could not rebuild.
            return self.index_sentence
        kept = self.tracks_kept
        if not kept:
            return lead
        return (
            f"{lead} {_counted(kept, 'tracks')} "
            f"{'keeps its' if kept == 1 else 'keep their'} previous data."
        )


@dataclass(frozen=True)
class RegeneratePlan:
    """What a regenerate run would do with the run marker as it is now.

    ``tags`` is the tags pass and ``embeddings`` the embeddings pass (None for a
    run of tags alone). ``tags_done_earlier`` is True when the run takes up an
    earlier run of both whose tags pass had reached its end: only the embeddings
    that run did not replace are made then, and ``tags`` does not apply (only
    tracks without any tags are tagged).
    """

    tags: RegenerateRun
    embeddings: Optional[RegenerateRun] = None
    tags_done_earlier: bool = False

    @property
    def continued(self) -> bool:
        """True when an unfinished earlier run is taken up again."""
        return self.tags_done_earlier or self.tags.continued

    @property
    def remaining(self) -> int:
        """Tracks the run still has to replace: all of them for a new run."""
        if self.tags_done_earlier and self.embeddings is not None:
            return self.embeddings.remaining
        return self.tags.remaining

    @property
    def total_tracks(self) -> int:
        """Tracks in the library."""
        return self.tags.total_tracks

    @property
    def marker_path(self) -> Path:
        """The marker file. Deleting it makes the next run start over."""
        return self.tags.marker_path


@dataclass(frozen=True)
class _RegenerateRequest:
    """What a regenerate run was asked to do. Set on the engine while the run lasts."""

    generate_embeddings: bool
    continue_run: bool
    tags_provider: RunProvider
    embeddings_provider: Optional[RunProvider]


def _embeddings_after_tags(tags: RunProvider, embeddings: RunProvider) -> RunProvider:
    """How the marker names the embeddings pass of a run of tags and embeddings.

    The pass starts when the tags pass has reached its end, and the marker entry of
    the tags is gone by then. So the entry of the embeddings also records the AI
    provider and model the tags were made with. A later run takes the pass up again
    only when both are unchanged, and an unfinished ``embeddings generate
    --regenerate`` (which records the embedding model alone) is never mistaken for it.
    """
    return RunProvider(
        embeddings.provider,
        f"{embeddings.model} (tags: {tags.provider} {tags.model})",
        embeddings.label,
        embeddings.dimension,
    )


def plan_regenerate(
    db: SQLiteManager,
    tags_provider: RunProvider,
    embeddings_provider: Optional[RunProvider] = None,
    continue_run: bool = True,
) -> RegeneratePlan:
    """Say what a regenerate run of tags, or of tags and embeddings, would do now.

    Nothing is written. A command asks its question from this, and the engine
    follows the same rule when it runs. ``embeddings_provider`` is None for a run
    of tags alone. Take both providers from ``regenerate_service.run_provider``.

    A run of both regenerates the tags first and every embedding after them. One
    case differs: the marker holds an unfinished embeddings pass of an earlier run
    of both, made with the same providers and models, and no unfinished tags pass.
    The tags of that run are complete, so only its remaining embeddings are made.
    """
    tags = preview_run(db, SCOPE_TAGS, tags_provider, continue_run)
    if embeddings_provider is None:
        return RegeneratePlan(tags=tags)
    identity = _embeddings_after_tags(tags_provider, embeddings_provider)
    embeddings = preview_run(db, SCOPE_EMBEDDINGS, identity, continue_run)
    done_earlier = embeddings.continued and not tags.continued
    if embeddings.continued and not done_earlier:
        # The tags pass runs first, so every embedding is made anew after it.
        embeddings = preview_run(db, SCOPE_EMBEDDINGS, identity, continue_run=False)
    return RegeneratePlan(
        tags=tags,
        embeddings=replace(embeddings, provider=embeddings_provider),
        tags_done_earlier=done_earlier,
    )


def remove_other_model_embeddings(db: SQLiteManager, embedding_model: str) -> int:
    """Delete the embedding rows of other models. Return how many were deleted.

    Only for tracks that have a row of ``embedding_model``. A track without one
    keeps the embedding it has, whichever model made it: nothing is deleted before
    its replacement exists.
    """
    cursor = db.get_connection().cursor()
    cursor.execute(
        """
        DELETE FROM embeddings
        WHERE embedding_model != ?
          AND track_id IN (SELECT track_id FROM embeddings WHERE embedding_model = ?)
        """,
        (embedding_model, embedding_model),
    )
    removed = cursor.rowcount
    db._commit()
    return max(removed, 0)


def rebuild_index_from_rows(
    db: SQLiteManager, vector_index: VectorIndex, embedding_model: str
) -> int:
    """Rebuild the vector index from the rows of ``embedding_model`` and save it.

    A thin wrapper over ``index_rebuild.rebuild_index``, which holds the rule: the
    index is built and saved only when it is complete. Returns the number of
    vectors in the index that was built, and 0 when none was: there was no row, or
    a track holds an embedding of another model or vector size, and the saved
    index was then left as it is. The whole result is ``vector_index.last_rebuild``.
    """
    return rebuild_index(db, vector_index, embedding_model).vectors


def index_may_follow_rows(
    db: SQLiteManager, vector_index: VectorIndex, embedding_model: str
) -> bool:
    """Whether an index built from the rows of ``embedding_model`` would be complete.

    True when no track is in the way (``index_rebuild.tracks_in_the_way``), so
    ``rebuild_index`` would build and save. Read-only. After a change of the
    embedding provider or of the vector size, a pass that was stopped part-way has
    replaced only some rows: the answer is then False, and the saved index and its
    dimension mismatch stay until a run has replaced every track.
    """
    return tracks_in_the_way(db, vector_index, embedding_model) == 0


def follow_rows_after_stopped_pass(
    db: SQLiteManager, vector_index: VectorIndex, embedding_model: str
) -> bool:
    """After an embeddings pass that was stopped part-way: let the index follow the rows.

    The rows are the new ones and the previous ones. ``rebuild_index`` builds the
    index from them only when it is complete, and otherwise leaves the saved index
    as it is and logs why. Nothing is raised: the rows are saved either way, and
    the caller is reporting what stopped the pass. Returns True when the index was
    rebuilt. The whole result is ``vector_index.last_rebuild``.
    """
    try:
        return rebuild_index(db, vector_index, embedding_model).vectors > 0
    except Exception as e:
        logger.warning(
            "Failed to rebuild the vector index after a stopped pass: %s", _plain_error_text(e)
        )
        return False


def _close_embeddings_pass(
    db: SQLiteManager,
    vector_index: VectorIndex,
    embedding_model: str,
    run: Optional[RegenerateRun] = None,
) -> Tuple[int, IndexRebuild]:
    """What ``finish_embeddings_pass`` does, with the result of the index rebuild.

    The marker entry of ``run`` is cleared only when no track is left to do. A track
    the provider returned no vector for still holds the embedding it had, and it can
    keep the search index from being rebuilt (``index_rebuild``). The entry stays
    then, so that the next run makes only the embeddings that are left, in place of
    starting over for every track and paying the provider again.
    """
    if run is not None:
        nothing_left = not remaining_track_ids(db, run)
        if nothing_left:
            finish_scope(db, SCOPE_EMBEDDINGS)
    else:
        nothing_left = db.count_tracks_without_embeddings(embedding_model) == 0
    removed = remove_other_model_embeddings(db, embedding_model) if nothing_left else 0
    if removed:
        logger.info("Removed %d embedding rows of other models", removed)
    return removed, rebuild_index(db, vector_index, embedding_model)


def finish_embeddings_pass(
    db: SQLiteManager,
    vector_index: VectorIndex,
    embedding_model: str,
    run: Optional[RegenerateRun] = None,
) -> int:
    """Close an embeddings pass that reached its end.

    Call it only for such a pass: not after an interrupt, a cancel request or a
    provider error. ``run`` is the marker run of the pass. Its marker entry is
    cleared when no track is left to do, and stays while the provider returned no
    vector for some track, so that the next run continues with those tracks. Pass
    None for a pass that used no marker and embedded every track.

    When no track is left to do, the embedding rows of other models are removed.
    That is: every track has a row of ``embedding_model`` that was written since
    ``run`` started (without a run: every track has a row of that model). The index
    is then rebuilt from the rows of ``embedding_model`` and saved, through
    ``index_rebuild.rebuild_index``: only when it is complete. When a track still
    holds only an embedding of another model or vector size, the saved index is
    left as it is.

    Returns the number of rows of other models that were removed. What became of
    the index is ``vector_index.last_rebuild`` (an ``IndexRebuild``): ``left_out``
    is the number of tracks in the way, and ``saved`` says whether the files were
    written.
    """
    removed, _rebuilt = _close_embeddings_pass(db, vector_index, embedding_model, run)
    return removed


class SyncEngine:
    def __init__(
        self,
        plex_client: Optional[PlexClient],
        db_manager: SQLiteManager,
        embedding_generator: Optional[EmbeddingGenerator] = None,
        vector_index: Optional[VectorIndex] = None,
        ai_provider: Optional[AIProvider] = None,
        musicbrainz_settings: Optional[MusicBrainzSettings] = None,
        audio_settings: Optional[Any] = None,
        saved_index_path: Optional[Path] = None,
        saved_index_dimension: Optional[int] = None,
        known_secrets: Optional[Iterable[Optional[str]]] = None,
    ):
        # None only for an engine that never reads Plex (``regenerate_local``).
        self._plex = plex_client
        # Key values the caller knows (the provider keys from the settings). They
        # are replaced in every error text the engine logs, stores or hands back,
        # together with the keys found on the provider objects: not every provider
        # object keeps its key where the engine can find it.
        self._given_secrets: List[str] = [
            value.strip()
            for value in (known_secrets or ())
            if isinstance(value, str) and len(value.strip()) >= _MIN_SECRET_CHARS
        ]
        self.db = db_manager
        self.embedding_generator = embedding_generator
        self.vector_index = vector_index
        # Where the saved vector index is, and its dimension, for a run that has no
        # index object (a sync without embeddings, a confirmed removal). The backup
        # before a large removal copies the files from the path. The index is opened
        # only after such a backup, to drop the removed tracks' vectors: opening an
        # index that cannot be loaded moves its files aside under another name, and
        # the backup then still holds them under their own. A small removal makes no
        # backup, so it leaves a saved index as it is.
        self.saved_index_path = saved_index_path
        self.saved_index_dimension = saved_index_dimension
        # The index object the last run opened itself from ``saved_index_path`` for
        # a removal, also when its files could not be loaded (``load_failed``). A
        # caller reads it to tell the user about files that were moved aside. None
        # when the run opened none.
        self.opened_saved_index: Optional[VectorIndex] = None
        self.ai_provider = ai_provider
        self.tag_generator = TagGenerator(ai_provider) if ai_provider else None
        # Outcome of the last sync's AI tagging, for callers that report provider
        # health (the Web UI's readiness): tracks tagged, and the provider error
        # that stopped tagging (None when it ran to the end or didn't run).
        self.tags_saved: int = 0
        self.tagging_error: Optional[FatalProviderError] = None
        # Embedding rows the last embedding stage wrote.
        self.embeddings_saved: int = 0
        # What the last sync did about unread items and removals. Every run starts
        # a new one, so callers read it after the run, also after a run that raised.
        self.outcome: SyncOutcome = SyncOutcome()
        # What the last regenerate run wrote and kept. Every regenerate run starts a
        # new one, and callers read it after the run, also after a run that raised.
        self.regenerate_result: RegenerateResult = RegenerateResult()
        # Set while ``regenerate_sync`` or ``regenerate_local`` runs.
        self._regenerating: Optional[_RegenerateRequest] = None
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

    @property
    def plex(self) -> PlexClient:
        """The Plex client. An engine that was built without one cannot sync."""
        if self._plex is None:
            raise RuntimeError("This SyncEngine has no Plex client, so it cannot read Plex.")
        return self._plex

    @plex.setter
    def plex(self, plex_client: Optional[PlexClient]) -> None:
        self._plex = plex_client

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
        self.opened_saved_index = None
        # Set by ``regenerate_sync`` for the run it starts here.
        regenerate = self._regenerating
        if regenerate is None:
            # A plain sync regenerates nothing: an earlier result no longer applies.
            self.regenerate_result = RegenerateResult()
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
            raise ConnectionError(_check_failed_message(token_msg))

        if progress_callback:
            progress_callback(0.0, "Starting incremental sync...")

        try:
            with _progress_display() as progress:
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

                if regenerate is not None:
                    self._regenerate_tags(
                        regenerate, progress, progress_callback, cancel_event, 0.55, 0.65
                    )
                elif self.tag_generator and self.ai_provider:
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

                if regenerate is not None:
                    if regenerate.generate_embeddings:
                        self._regenerate_embeddings(
                            regenerate, progress, progress_callback, cancel_event, 0.75, 1.0
                        )
                elif generate_embeddings and self.embedding_generator and self.vector_index:
                    task = progress.add_task("Generating embeddings...", total=None)
                    self._generate_embeddings_for_new_tracks(
                        progress, task, progress_callback, cancel_event, 0.75, 1.0
                    )

            # A run that kept unread items, or skipped or held removals, is recorded
            # as partial, with the reason. So is a regenerate run that an error
            # stopped part-way, or that replaced nothing.
            notes = [self.outcome.describe() if self.outcome.is_partial else ""]
            if regenerate is not None:
                # A pass that never started has kept every track. Counted before the
                # row is written, so the row and the result give the same number.
                self._count_unstarted_passes()
                notes.append(self.regenerate_result.describe())
            warning = " ".join(note for note in notes if note)
            partial = bool(warning) or self.outcome.is_partial
            sync_record = SyncHistory(
                tracks_added=stats["tracks_added"],
                tracks_updated=stats["tracks_updated"],
                tracks_removed=stats["tracks_removed"],
                status="partial" if partial else "success",
                error_message=warning if partial else None,
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
            # A provider can repeat its key in an error, so known keys go as well.
            error_text = _redact(str(e), self._known_secrets())
            logger.error(f"Incremental sync failed: {error_text}")
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
        continue_run: bool = True,
        tags_provider: Optional[RunProvider] = None,
        embeddings_provider: Optional[RunProvider] = None,
    ) -> SyncHistory:
        """Sync the library, then replace tags and embeddings in place.

        Nothing is cleared first. A track keeps its tags and its embedding until the
        new ones are saved, so a run that fails, is refused or is interrupted leaves
        every track with what it had. ``self.regenerate_result`` says what the run
        wrote and kept. Read it after the run, also after a run that raised.

        The embeddings pass starts only when the tags pass reached its end. An error
        of a provider that stops a pass part-way is not raised: the run ends, and
        the result names the pass and the reason (``provider_failed``).

        ``continue_run`` takes up an unfinished earlier run from the run marker
        (see ``plan_regenerate``). False starts a new run for every track.
        ``tags_provider`` and ``embeddings_provider`` are the provider and model
        that the marker records. Pass the values of
        ``regenerate_service.run_provider``, which the caller's own question uses
        too. Without them they are read from the provider objects.

        Raises:
            RegenerateRefused: the engine has no AI provider, or embeddings were
                asked for and it has no embedding generator or no vector index.
                Nothing was changed, and Plex was not asked.
        """
        logger.info("Starting regenerate sync: tags and embeddings are replaced in place")
        self.outcome = SyncOutcome()
        self._begin_regenerate(
            generate_embeddings, continue_run, tags_provider, embeddings_provider
        )
        try:
            return self.incremental_sync(
                generate_embeddings=generate_embeddings,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                allow_large_removals=allow_large_removals,
            )
        finally:
            self._end_regenerate()

    def regenerate_local(
        self,
        generate_embeddings: bool = True,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        continue_run: bool = True,
        tags_provider: Optional[RunProvider] = None,
        embeddings_provider: Optional[RunProvider] = None,
    ) -> RegenerateResult:
        """Replace tags and embeddings in place, without reading Plex.

        The two passes of ``regenerate_sync`` under one progress display, for the
        tracks the database holds. The engine needs no Plex client for it, and no
        sync history row is written. The rules, the arguments and the refusal are
        those of ``regenerate_sync``. Returns ``self.regenerate_result``.
        """
        logger.info("Starting local regenerate: tags and embeddings are replaced in place")
        request = self._begin_regenerate(
            generate_embeddings, continue_run, tags_provider, embeddings_provider
        )
        try:
            with _progress_display() as progress:
                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Regenerate cancelled by user")
                self._regenerate_tags(request, progress, progress_callback, cancel_event, 0.0, 0.5)
                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Regenerate cancelled by user")
                if request.generate_embeddings:
                    self._regenerate_embeddings(
                        request, progress, progress_callback, cancel_event, 0.5, 1.0
                    )
                if cancel_event and cancel_event.is_set():
                    raise KeyboardInterrupt("Regenerate cancelled by user")
            if progress_callback:
                progress_callback(1.0, "Regenerate finished")
            return self.regenerate_result
        finally:
            self._end_regenerate()

    def _begin_regenerate(
        self,
        generate_embeddings: bool,
        continue_run: bool,
        tags_provider: Optional[RunProvider],
        embeddings_provider: Optional[RunProvider],
    ) -> _RegenerateRequest:
        """Start a regenerate run: a new result, then the engine's own guard.

        Raises ``RegenerateRefused`` before anything is read or written when the
        engine was not given what the run needs.
        """
        wanted = bool(generate_embeddings)
        self.regenerate_result = RegenerateResult(
            tags=RegeneratePass(requested=True), embeddings=RegeneratePass(requested=wanted)
        )
        self.tags_saved = 0
        self.tagging_error = None
        self.embeddings_saved = 0
        if self.ai_provider is None or self.tag_generator is None:
            raise RegenerateRefused.not_given(COMPONENT_AI)
        if wanted and self.embedding_generator is None:
            raise RegenerateRefused.not_given(COMPONENT_EMBEDDINGS)
        if wanted and self.vector_index is None:
            raise RegenerateRefused.not_given(COMPONENT_INDEX)
        request = _RegenerateRequest(
            generate_embeddings=wanted,
            continue_run=bool(continue_run),
            tags_provider=tags_provider or self._tags_run_provider(),
            embeddings_provider=self._sized(embeddings_provider) if wanted else None,
        )
        self._regenerating = request
        return request

    def _sized(self, embeddings_provider: Optional[RunProvider]) -> RunProvider:
        """The embedding provider for the run marker, with its vector size.

        The marker records the size, so that a run is never continued across a
        change of it. A caller that names none gets the size of the generator.
        """
        own = self._embeddings_run_provider()
        if embeddings_provider is None:
            return own
        if embeddings_provider.dimension is None and own.dimension is not None:
            return replace(embeddings_provider, dimension=own.dimension)
        return embeddings_provider

    def _end_regenerate(self) -> None:
        """End a regenerate run. A pass that never started has kept every track."""
        self._regenerating = None
        self._count_unstarted_passes()

    def _count_unstarted_passes(self) -> None:
        """Give every pass that was requested and never started its kept tracks: all."""
        result = self.regenerate_result
        unstarted = [
            part
            for part in (result.tags, result.embeddings)
            if part.requested and not part.started and not part.to_do_ids
        ]
        if not unstarted:
            return
        try:
            every_track = {track.id for track in self.db.get_all_tracks() if track.id is not None}
        except Exception as e:
            # Only the kept count is lost. The error of the run itself goes on.
            logger.debug("Could not count the tracks a regenerate run kept: %s", type(e).__name__)
            return
        for part in unstarted:
            part.to_do_ids = set(every_track)

    def _tags_run_provider(self) -> RunProvider:
        """The AI provider and model for the run marker, read from the provider object."""
        name = getattr(self.ai_provider, "provider_name", None)
        if not isinstance(name, str) or not name:
            name = type(self.ai_provider).__name__
        model = getattr(self.ai_provider, "model", None)
        return RunProvider(name.lower(), model if isinstance(model, str) else "", name)

    def _embeddings_run_provider(self) -> RunProvider:
        """The embedding provider and model for the run marker, read from the generator.

        The provider is the ``embedding_model`` value of the rows the generator writes.
        """
        generator = self.embedding_generator
        name = getattr(generator, "provider_name", None)
        if not isinstance(name, str) or not name:
            name = type(generator).__name__
        model = getattr(getattr(generator, "provider", None), "model_name", None)
        size: Any = None
        try:
            size = generator.get_dimension() if generator is not None else None
        except Exception as e:
            logger.debug("Could not read the vector size of the generator: %s", type(e).__name__)
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            size = None
        return RunProvider(name, model if isinstance(model, str) else "", name, size)

    def _known_secrets(self) -> List[str]:
        """The key values to keep out of error text.

        Those the caller gave the engine (``known_secrets``), and those this run's
        provider objects hold where the engine can find them.
        """
        inner = getattr(self.embedding_generator, "provider", None)
        holders = [
            self.ai_provider,
            self.embedding_generator,
            inner,
            getattr(inner, "client", None),
        ]
        secrets: List[str] = list(self._given_secrets)
        for holder in holders:
            value = getattr(holder, "api_key", None) if holder is not None else None
            if isinstance(value, str) and len(value.strip()) >= _MIN_SECRET_CHARS:
                secrets.append(value.strip())
        return secrets

    def _open_scope(
        self, scope: str, provider: RunProvider, continue_run: bool
    ) -> Tuple[Optional[RegenerateRun], List[int]]:
        """Start the marker run of ``scope`` or continue it. Return it with the tracks to do.

        A marker that cannot be written does not stop the run. Every track is then
        to do, and only the chance to continue this run later is lost.
        """
        try:
            run = open_run(self.db, scope, provider, continue_run)
        except OSError as e:
            logger.warning(
                "The regenerate marker could not be written (%s), so this run cannot be "
                "continued later.",
                type(e).__name__,
            )
            return None, [track.id for track in self.db.get_all_tracks() if track.id is not None]
        return run, list(run.track_ids)

    def _note_stopped_pass(self, scope: str, record: RegeneratePass) -> None:
        """Put the error that stopped a pass into the run's result, with a text to show."""
        error = record.error
        if error is None:
            return
        result = self.regenerate_result
        result.stopped_scope = scope
        result.error = error
        if isinstance(error, FatalProviderError):
            # One of the fixed sentences of ``classify_provider_error``, or the
            # fixed sentence of requests that failed in a row.
            reason = _redact(error.user_message, self._known_secrets())
            if isinstance(error, RepeatedBatchFailure) and error.cause is not None:
                # What the provider said last, redacted and capped.
                last = _plain_error_text(error.cause, self._known_secrets())
                if last and last not in reason:
                    reason = f"{_sentence(reason)} Last error: {last}"
        else:
            reason = _plain_error_text(error, self._known_secrets())
        lead = "AI tagging stopped" if scope == SCOPE_TAGS else "Embedding generation stopped"
        result.error_message = f"{lead}: {_sentence(reason)}"

    def _regenerate_tags(
        self,
        request: _RegenerateRequest,
        progress: Progress,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
    ) -> None:
        """The tags pass of a regenerate run: replace the tags of the tracks still to do.

        Its marker entry is cleared when the pass reached its end and tags were
        replaced since the marker's start time: in this run, or in the run it
        continues. So a continued pass whose remaining tracks all got nothing does
        not stay open for ever: those tracks keep their previous tags, and the run
        goes on to the embeddings. A pass that had no track to do is cleared too.
        """
        record = self.regenerate_result.tags
        plan = plan_regenerate(
            self.db,
            request.tags_provider,
            request.embeddings_provider if request.generate_embeddings else None,
            request.continue_run,
        )
        task = progress.add_task("Regenerating AI tags...", total=None)
        if plan.tags_done_earlier:
            # The run this one continues had finished its tags. Only tracks without
            # any tags (new since then) are tagged before its embeddings go on.
            record.done_earlier = True
            self._generate_tags_for_untagged_tracks(
                progress,
                task,
                progress_callback,
                cancel_event,
                progress_start,
                progress_end,
                record=record,
            )
            self._note_stopped_pass(SCOPE_TAGS, record)
            return

        run, to_do = self._open_scope(SCOPE_TAGS, request.tags_provider, request.continue_run)
        record.continued = run is not None and run.continued
        if run is not None and run.continued:
            # What the run this one continues had replaced when it stopped.
            record.replaced_earlier = max(run.total_tracks - run.remaining, 0)
        self._generate_tags_for_untagged_tracks(
            progress,
            task,
            progress_callback,
            cancel_event,
            progress_start,
            progress_end,
            to_do=to_do,
            record=record,
        )
        self._note_stopped_pass(SCOPE_TAGS, record)
        if (
            run is not None
            and record.finished
            and (record.written or record.replaced_earlier or not record.to_do_ids)
        ):
            finish_scope(self.db, SCOPE_TAGS)

    def _regenerate_embeddings(
        self,
        request: _RegenerateRequest,
        progress: Progress,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
    ) -> None:
        """The embeddings pass of a regenerate run. It waits for the tags pass.

        An embedding holds the tags in its text. One made before the new tags exist
        would pass for done with the old text, so the pass does not start unless
        the tags pass reached its end. When this pass reaches its end and wrote a
        row (or continued a run that had), the index is rebuilt from the rows, as
        ``finish_embeddings_pass`` does, and its marker entry is cleared when no
        track is left to do. A track the provider returned no vector for keeps the
        entry open, so that the next run makes only those embeddings. The index is
        saved only when it is complete: tracks that are in the way are counted in
        ``regenerate_result.index_left_out``.
        """
        result = self.regenerate_result
        record = result.embeddings
        generator, index = self.embedding_generator, self.vector_index
        if generator is None or index is None or request.embeddings_provider is None:
            return
        if not result.tags.finished:
            logger.info("The tags pass did not reach its end, so no embedding was regenerated")
            return
        if result.no_new_tags:
            # Every embedding would be made again from the text it was made from.
            logger.warning(
                "The tags pass regenerated no tags, so no embedding was regenerated either"
            )
            return

        task = progress.add_task("Regenerating embeddings...", total=None)
        identity = _embeddings_after_tags(request.tags_provider, request.embeddings_provider)
        # An earlier pass is taken up only behind tags that an earlier run finished.
        # Tags written in this run make every embedding from before out of date.
        run, to_do = self._open_scope(
            SCOPE_EMBEDDINGS, identity, request.continue_run and result.tags.done_earlier
        )
        record.continued = run is not None and run.continued
        if run is not None and run.continued:
            # What the run this one continues had replaced when it stopped.
            record.replaced_earlier = max(run.total_tracks - run.remaining, 0)
        if record.continued and result.tags.written_ids:
            # A track that got its tags only in this run (it had none when the
            # earlier run embedded it) holds an embedding made from text without
            # them. It counts as done by its time, so it is added here.
            to_do = sorted(set(to_do) | result.tags.written_ids)
        self._generate_embeddings_for_new_tracks(
            progress,
            task,
            progress_callback,
            cancel_event,
            progress_start,
            progress_end,
            to_do=to_do,
            record=record,
        )
        self._note_stopped_pass(SCOPE_EMBEDDINGS, record)

        model = generator.provider_name
        if record.finished and (record.written or record.replaced_earlier or not record.to_do_ids):
            # Also for a pass that had no track to do (an empty library), so that
            # its marker entry does not stay behind, and for a continued pass whose
            # remaining tracks got no vector (the run before it replaced the others).
            # The marker entry is cleared when no track is left to do.
            removed, rebuilt = _close_embeddings_pass(self.db, index, model, run)
            result.other_model_rows_removed = removed
            result.index_left_out = rebuilt.left_out
        elif record.written:
            # Stopped part-way: the index follows the rows, the new and the previous,
            # unless a track is in the way. The saved index then stays as it is, and
            # the stop is what the run reports.
            follow_rows_after_stopped_pass(self.db, index, model)

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

        Call it only after a backup has copied the index files. Index files that
        cannot be loaded are moved aside under another name when they are opened
        (``VectorIndex.load_failed``), and the backup then still holds them under
        their own. An index that holds no vectors after loading is not returned:
        there is nothing to drop from it, and saving it would put an empty index at
        the path as if it were the library's index.
        """
        path = self._index_path()
        dimension = self.saved_index_dimension
        if path is None or isinstance(dimension, bool) or not isinstance(dimension, int):
            return None
        try:
            if dimension <= 0 or not path.expanduser().exists():
                return None
            index = VectorIndex(dimension=dimension, index_path=str(path))
            # Kept for the caller, also when the load failed: it tells the user.
            self.opened_saved_index = index
            if index.index is None or index.index.ntotal == 0:
                if getattr(index, "load_failed", False) is True:
                    logger.warning(
                        "The saved vector index could not be loaded, so the removed "
                        "tracks were not dropped from it. Run 'plexmix embeddings "
                        "generate' to rebuild it."
                    )
                else:
                    logger.warning(
                        "The saved vector index holds no vectors, so the removed "
                        "tracks were not dropped from it."
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
            # An index whose saved files could not be loaded is empty (its files were
            # moved aside), and so is one that has no file. It has no vector to drop,
            # and saving it would put an empty index at the path as if it were the
            # library's: the embeddings stage then adds only the tracks it embeds and
            # saves a partial index. The stage rebuilds it from the rows instead.
            and getattr(index, "load_failed", False) is not True
            and not _holds_no_vectors(index)
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
        self.opened_saved_index = None

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

    def _index_takes_updates(self) -> bool:
        """True when the loaded index is the library's and can take new vectors one by one.

        That is an index that holds vectors, whose dimension matches the embedding
        provider and that loaded from its saved files. Any other index (none, a
        mismatch, a failed load) is rebuilt from the rows instead, through
        ``rebuild_index``. So is an index that holds no vector: its files are gone
        (moved aside after a failed load, or deleted) or it was saved empty. Adding
        only the vectors of this run to it would save a partial index, with the
        success line and nothing to repair it, while the rows of the other tracks
        are stored.
        """
        index = self.vector_index
        return (
            index is not None
            and index.index is not None
            and not index.dimension_mismatch
            and getattr(index, "load_failed", False) is not True
            and not _holds_no_vectors(index)
        )

    def _save_index_with(self, saved_vectors: List[tuple[int, List[float]]]) -> None:
        """Put the vectors that were saved in this stage into the loaded index and save it.

        An index whose saved files could not be loaded is empty, and adding only the
        new vectors to it would save a nearly empty index as if it were the library's:
        callers check ``_index_takes_updates`` first.
        """
        index = self.vector_index
        assert index is not None
        if saved_vectors:
            new_track_ids = [tv[0] for tv in saved_vectors]
            new_embeddings = [tv[1] for tv in saved_vectors]
            # update_vectors removes old entries first, avoiding duplicates
            index.update_vectors(new_embeddings, new_track_ids)
            logger.info(f"Incrementally updated {len(new_embeddings)} vectors in FAISS index")
        index.save_index(str(index.index_path))

    def _update_index_after_stop(self, saved_vectors: List[tuple[int, List[float]]]) -> None:
        """``_save_index_with`` for a stage that stopped: it logs a failure and never raises."""
        try:
            self._save_index_with(saved_vectors)
        except Exception as e:
            logger.warning(
                "Failed to update the vector index after a stopped pass: %s",
                _plain_error_text(e, self._known_secrets()),
            )

    def _generate_embeddings_for_new_tracks(
        self,
        progress: Progress,
        task: Any,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
        to_do: Optional[Iterable[int]] = None,
        record: Optional[RegeneratePass] = None,
    ) -> None:
        """Generate embeddings and save them batch by batch.

        Without ``to_do``, the tracks that have no embedding of the active model are
        embedded, and the vector index is updated at the end. With ``to_do`` (a
        regenerate run) exactly those tracks are embedded. Each keeps its row until
        the new one is saved over it, and the caller rebuilds the index afterwards.
        A request that the provider fails then stops the pass without raising, and
        ``record.error`` holds the error.

        ``record`` receives what the pass did, also when it is interrupted, and
        ``self.embeddings_saved`` counts the rows written.
        """
        if not self.embedding_generator or not self.vector_index:
            return
        self.embeddings_saved = 0
        if record is None:
            record = RegeneratePass()
        record.started = True
        in_place = to_do is not None

        all_tracks = self.db.get_all_tracks()
        if to_do is None:
            # Scope to the active embedding model so switching provider/model re-embeds
            # tracks that were only embedded by a previous model.
            existing_track_ids = self.db.get_track_ids_with_embeddings(
                self.embedding_generator.provider_name
            )
            tracks_needing_embeddings = [t for t in all_tracks if t.id not in existing_track_ids]
        else:
            wanted = set(to_do)
            tracks_needing_embeddings = [t for t in all_tracks if t.id in wanted]
        record.to_do_ids = {t.id for t in tracks_needing_embeddings if t.id is not None}

        if not tracks_needing_embeddings:
            progress.update(task, description="No new tracks need embeddings")
            record.finished = True
            if not in_place and not self._index_takes_updates():
                # Every track has a row of the active model by name, yet the saved
                # index is none this run can add to: it is for another vector size,
                # it could not be loaded, or there is none. It is rebuilt from the
                # rows as after an embeddings pass, and only when it is complete.
                # After a change of the vector size the rows are of the old size,
                # so nothing is built, and the run is recorded as partial with the
                # sentence that says what to run, as it is after a pass that wrote rows.
                self._rebuild_index_from_rows()
            return

        progress.update(task, total=len(tracks_needing_embeddings))
        logger.info(f"Generating embeddings for {len(tracks_needing_embeddings)} tracks")

        from ..database.models import Embedding

        batch_size = _EMBEDDING_BATCH_SIZE
        embeddings_saved = 0
        total_batches = (len(tracks_needing_embeddings) + batch_size - 1) // batch_size
        saved_vectors: List[
            tuple[int, List[float]]
        ] = []  # Collect (track_id, vector) in-memory for FAISS update
        # True when a cancel request or a provider error ended the pass early.
        stopped = False

        try:
            for i in range(0, len(tracks_needing_embeddings), batch_size):
                if cancel_event and cancel_event.is_set():
                    logger.warning(
                        f"Embedding generation cancelled. Saved {embeddings_saved} embeddings."
                    )
                    stopped = True
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

                try:
                    embeddings = self.embedding_generator.generate_batch_embeddings(
                        texts, batch_size=batch_size
                    )
                except Exception as e:
                    if not in_place:
                        raise
                    # A regenerate run ends here and reports the error. Every track
                    # that was not reached keeps the embedding it has.
                    record.error = e
                    stopped = True
                    logger.error(
                        "Embedding generation stopped after %d embeddings: %s",
                        embeddings_saved,
                        _plain_error_text(e, self._known_secrets()),
                    )
                    break

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

                # Saved first, counted after: the count is of rows that exist.
                self.db.insert_embeddings_batch(batch_embedding_objects)

                for saved in batch_embedding_objects:
                    saved_vectors.append((saved.track_id, saved.vector))
                    record.written_ids.add(saved.track_id)
                    embeddings_saved += 1
                    self.embeddings_saved = embeddings_saved
                    progress.update(task, advance=1)

                    if progress_callback and embeddings_saved % 10 == 0:
                        current_progress = progress_start + (progress_end - progress_start) * (
                            embeddings_saved / len(tracks_needing_embeddings)
                        )
                        progress_callback(
                            current_progress,
                            f"Generating embeddings... ({embeddings_saved}/{len(tracks_needing_embeddings)})",
                        )

                logger.debug(f"Completed batch {batch_num}/{total_batches}")

        except KeyboardInterrupt:
            logger.warning(
                f"Embedding generation interrupted. Saved {embeddings_saved} embeddings."
            )
            # Let the index follow the rows that were saved, so that it doesn't diverge
            # from the embeddings table after an interrupt.
            if self.vector_index is not None and embeddings_saved > 0:
                if not in_place and self._index_takes_updates():
                    # An index that loaded cleanly takes the new vectors one by one,
                    # as at the end of the stage: the tracks not reached keep their
                    # earlier vectors, the others get the vectors of their rows. A
                    # rebuild from the rows would be refused here when the provider
                    # name changed and a track still holds only the earlier row.
                    self._update_index_after_stop(saved_vectors)
                else:
                    # The index follows the rows only when it is complete then
                    # (``index_rebuild.rebuild_index``). Never raises.
                    follow_rows_after_stopped_pass(
                        self.db, self.vector_index, self.embedding_generator.provider_name
                    )
            raise
        except Exception:
            # A plain run does not catch a provider error: it is raised to the caller.
            # The rows that were saved are in the index first, as after Ctrl+C.
            if not in_place and embeddings_saved > 0 and self._index_takes_updates():
                self._update_index_after_stop(saved_vectors)
            raise

        record.finished = not stopped
        if in_place:
            # The caller rebuilds the index once, from the rows of the active model.
            progress.update(task, description=f"Generated {embeddings_saved} embeddings")
            logger.info(f"Generated embeddings for {embeddings_saved} tracks")
            return

        # Use incremental FAISS updates if index already exists with correct dimension.
        # An index whose saved files could not be loaded is empty: adding only the new
        # vectors would save a nearly empty index as if it were the library's, so it
        # is rebuilt from the database.
        if self._index_takes_updates():
            self._save_index_with(saved_vectors)
        else:
            # Full rebuild needed (new index, dimension mismatch or failed load), from
            # the rows of the active model. It is saved only when it is complete.
            # After a change of the vector size or of the provider, the tracks that
            # were not replaced yet are in the way: the saved index and its
            # mismatch then stay as they are, and the run is recorded as partial.
            self._rebuild_index_from_rows()

        progress.update(task, description=f"Generated {embeddings_saved} embeddings")
        logger.info(f"Generated embeddings for {embeddings_saved} tracks")

    def _rebuild_index_from_rows(self) -> None:
        """Rebuild the saved index from the rows of the active model, through ``rebuild_index``.

        Tracks in the way (a row of another model or vector size is all they hold)
        keep it from being built and saved: the saved index stays as it is, and the
        count goes to ``outcome.index_left_out``, which makes the run partial.
        """
        assert self.embedding_generator is not None and self.vector_index is not None
        rebuilt = rebuild_index(self.db, self.vector_index, self.embedding_generator.provider_name)
        if rebuilt.left_out:
            self.outcome.index_left_out = rebuilt.left_out
        else:
            logger.info(f"Rebuilt FAISS index with {rebuilt.vectors} vectors")

    def _generate_tags_for_untagged_tracks(
        self,
        progress: Progress,
        task: Any,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[Event] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
        to_do: Optional[Iterable[int]] = None,
        record: Optional[RegeneratePass] = None,
    ) -> None:
        """Generate AI tags and save them after every batch.

        Without ``to_do``, the tracks that have no tags at all are tagged. With
        ``to_do`` (a regenerate run) exactly those tracks are, and each keeps its
        tags until its new ones are saved.

        An empty result is no result. When the provider returns no tag, no
        environment and no instrument for a track (it left the track out, its reply
        could not be read, or the request of that batch failed), nothing is written
        for the track: its tags and its ``tags_generated_at`` stay as they are, and
        it is not counted. An empty list never overwrites a column either: a reply
        with tags and no instruments leaves the instruments the track has. With
        ``to_do``, a reply without tags is no result for the track, whatever else
        it holds: the track keeps all it has and stays to do.
        ``self.tags_saved`` counts the rows written. ``record`` receives what the
        pass did, also when it is interrupted.

        In a regenerate run the pass stops when ``_MAX_FAILED_TAG_BATCHES`` requests
        in a row failed. ``record.error`` then holds a ``RepeatedBatchFailure``.
        """
        if not self.tag_generator:
            return
        self.tags_saved = 0
        self.tagging_error = None
        if record is None:
            record = RegeneratePass()
        record.started = True
        in_place = to_do is not None

        all_tracks = self.db.get_all_tracks()
        if to_do is None:
            tracks_needing_tags = [
                track
                for track in all_tracks
                if not track.tags and not track.environments and not track.instruments
            ]
        else:
            wanted = set(to_do)
            tracks_needing_tags = [track for track in all_tracks if track.id in wanted]
        record.to_do_ids = {track.id for track in tracks_needing_tags if track.id is not None}

        if not tracks_needing_tags:
            progress.update(task, description="No tracks need AI tags")
            record.finished = True
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

        written = record.written_ids
        pending = set(record.to_do_ids)

        def save_results(results: Any) -> None:
            """Write the usable results that are not written yet, and count them."""
            if not isinstance(results, dict):
                return
            for track_id, result in results.items():
                if track_id not in pending or not isinstance(result, dict):
                    continue
                # Only texts with content count: a list of blanks is an empty list.
                tags = clean_tag_list(result.get("tags"))
                environments = clean_tag_list(result.get("environments"))
                instruments = clean_tag_list(result.get("instruments"))
                if not (tags or environments or instruments):
                    # No result for this track: it keeps the tags it has.
                    continue
                if in_place and not tags:
                    # A regenerate run replaces tags. A reply without them is no
                    # replacement: the track keeps all it has and is not counted.
                    continue
                # A column is written only when its list has content. None leaves
                # the column as it is, so an empty list never blanks what was there.
                self.db.update_track_tags(
                    track_id,
                    tags=",".join(tags) if tags else None,
                    environments=",".join(environments) if environments else None,
                    instruments=",".join(instruments) if instruments else None,
                )
                pending.discard(track_id)
                written.add(track_id)
                self.tags_saved = len(written)

        partial_results: Dict[int, Dict[str, Any]] = {}
        # A regenerate run does not go on asking a provider that no longer answers.
        # A plain sync tries every batch, as it always did.
        stop_after: Dict[str, Any] = {}
        if self._regenerating is not None:
            stop_after["max_failed_batches"] = _MAX_FAILED_TAG_BATCHES
        try:
            tag_results = self.tag_generator.generate_tags_batch(
                track_data_list,
                batch_size=_TAG_BATCH_SIZE,
                progress_callback=tag_progress_callback,
                cancel_event=cancel_event,
                result_sink=partial_results,
                on_batch=save_results,
                **stop_after,
            )
            # Every batch was saved when it arrived, so there is nothing left to write
            # here or in the branches below. They cover a generator that hands its
            # results back without reporting the batches.
            save_results(tag_results)
            record.finished = not (cancel_event is not None and cancel_event.is_set())
            progress.update(task, description=f"Generated tags for {self.tags_saved} tracks")
            logger.info(f"Generated AI tags for {self.tags_saved} tracks")

        except FatalProviderError as e:
            # Unrecoverable error (e.g. bad API key): stop tagging, keep partial
            # results, surface the reason once, and let the rest of sync proceed.
            save_results(e.partial_results)
            self.tagging_error = e
            record.error = e
            logger.error("AI tagging stopped after %d tracks: %s", self.tags_saved, e.user_message)
            progress.update(task, description=f"AI tagging stopped: {e.user_message}")
            if progress_callback:
                progress_callback(progress_end, f"AI tagging stopped: {e.user_message}")
        except KeyboardInterrupt:
            # Tags generated before the interrupt are saved already
            # (the CLI/UI tells the user "progress saved").
            save_results(partial_results)
            logger.warning("Tag generation interrupted; saved %d tagged tracks", self.tags_saved)
            raise
        except Exception as e:
            record.error = e
            logger.error(
                "Tag generation failed after %d tracks: %s",
                self.tags_saved,
                _plain_error_text(e, self._known_secrets()),
            )
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
