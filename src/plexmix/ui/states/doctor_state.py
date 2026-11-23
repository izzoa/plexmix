"""Doctor: library health scan, rows and verdict from readiness, confirmed fixes.

webui-honest-status-and-first-run, design D8:

- ``scan_state`` (``idle|checking|done|no_database|error``) replaces the old
  ``is_healthy`` flag. Nothing claims health before a scan measured it: until
  the first scan finishes, every library row and the hero read "Checking…".
- ``rows``, ``row_map`` and ``verdict`` are computed from
  ``readiness.doctor_rows`` / ``readiness.verdict`` over the last scan and
  ``AppState.readiness``, so the hero always matches the rows and the
  connection rows match every other status surface.
- Every fix goes through :meth:`DoctorState.start_fix`, which opens a scope
  confirmation (``readiness.describe_job_scope``: count, provider and model,
  whether data leaves the machine, duration). Confirming dispatches the
  existing background handler; cancelling starts nothing. One fix runs at a
  time.
- A failed fix stays in ``fix_errors[target]`` (rendered on its row, with the
  action that fixes the cause) until the next scan or fix.

fix-cli-secrets-and-data-loss, design D19: "Rebuild embeddings" replaces every
embedding in place (:func:`check_embedding_rebuild`, :func:`rebuild_embeddings`).
The provider answers one test request before anything changes, no embedding row
and no index file is cleared first, and a refused, failed or cancelled run leaves
every track with its new or its previous embedding.
"""

import asyncio
import importlib.util
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event as ThreadingEvent
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
)

import reflex as rx

from plexmix.ai.errors import FatalProviderError
from plexmix.config.constants import EMBEDDING_BATCH_SIZE
from plexmix.ui.job_manager import task_store
from plexmix.ui.states.app_state import (
    AppState,
    current_fingerprint_safe,
    provider_failure_cause,
    record_readiness,
)
from plexmix.ui.utils.errors import friendly_error
from plexmix.ui.utils.helpers import format_count, pluralize, safe_error_text

if TYPE_CHECKING:
    from plexmix.services.providers import ProviderFailure
    from plexmix.services.regenerate_service import RegenerateRefused

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

FIX_JOB = "doctor_fix"

# Fix target → the background handler that runs it (DoctorState attribute name).
FIX_HANDLERS: Dict[str, str] = {
    "embeddings_incremental": "generate_missing_embeddings",
    "embeddings_full": "regenerate_all_embeddings",
    "cleanup": "delete_orphaned_embeddings",
    "tags": "regenerate_missing_tags",
    "audio_analysis": "analyze_missing_audio",
    "musicbrainz": "enrich_missing_musicbrainz",
}
FIX_TARGETS: tuple[str, ...] = tuple(FIX_HANDLERS)

# Fix target → the Doctor row that shows its button and its failure.
FIX_ROWS: Dict[str, str] = {
    "embeddings_incremental": "embeddings",
    "embeddings_full": "embeddings",
    "cleanup": "orphans",
    "tags": "tags",
    "audio_analysis": "audio",
    "musicbrainz": "musicbrainz",
}

# Shown in the running-fix callout (the job's own progress text has raw numbers).
FIX_TITLES: Dict[str, str] = {
    "embeddings_incremental": "Generating embeddings",
    "embeddings_full": "Rebuilding embeddings",
    "cleanup": "Removing orphaned embeddings",
    "tags": "Tagging tracks",
    "audio_analysis": "Analyzing audio",
    "musicbrainz": "Enriching from MusicBrainz",
}

ANOTHER_FIX_RUNNING = "Another fix is running"
NOTHING_TO_FIX = "Nothing to fix there any more. Re-scan to refresh the counts."
SCAN_ERROR_MESSAGE = "The health check couldn't read your library. Details are in the server log."
FIX_FAILED_MESSAGE = "The fix failed. Details are in the server log."
FIX_CANCELLED_MESSAGE = "Fix cancelled"
# "Rebuild embeddings" replaces in place, so a run that could not start changed nothing.
REBUILD_NOTHING_CHANGED = "No embedding was changed."

# A scan stuck in "checking" this long (lost task) no longer blocks a new one.
SCAN_STALE_S = 120.0

ROW_IDS: tuple[str, ...] = (
    "conn_plex",
    "conn_ai",
    "conn_embeddings",
    "library",
    "embeddings",
    "orphans",
    "tags",
    "audio",
    "musicbrainz",
)
# Keys of a readiness.doctor_rows row, plus the display keys row_map adds.
ROW_KEYS: tuple[str, ...] = (
    "id",
    "group",
    "tier",
    "state",
    "state_word",
    "tone",
    "title",
    "count",
    "model",
    "summary",
    "body",
    "why",
    "action_label",
    "action_href",
    "check_label",
    "check_href",
    "secondary_label",
    "secondary_href",
    "fix",
    "fix_label",
)
ROW_EXTRA_KEYS: tuple[str, ...] = (
    "badge",
    "lead",
    "num",
    "tail",
    "checked_ago",
    "error_title",
    "error_body",
    "error_action_label",
    "error_action_href",
)
_BADGES: Dict[str, str] = {
    "success": "badge badge-green",
    "warning": "badge badge-yellow",
    "error": "badge badge-red",
    "idle": "badge badge-gray",
}
_CONNECTION_COMPONENTS: Dict[str, str] = {
    "conn_plex": "plex",
    "conn_ai": "ai",
    "conn_embeddings": "embeddings",
}


# ── Pure helpers (no Reflex) ─────────────────────────────────────────────


class FixFailed(Exception):
    """A fix stopped for a known reason; ``copy`` is the row message.

    ``copy`` keys: ``title`` (also the toast), ``body``, ``action_label``,
    ``action_href`` (a Settings ``?section=`` link or another page).
    """

    def __init__(self, copy: Mapping[str, str]) -> None:
        super().__init__(copy.get("title", ""))
        self.copy: Dict[str, str] = {k: str(v or "") for k, v in copy.items()}


def copy_of(described: Mapping[str, str]) -> Dict[str, str]:
    """The row-message keys of a ``readiness.describe()`` result."""
    return {
        "title": str(described.get("title", "") or ""),
        "body": str(described.get("body", "") or ""),
        "action_label": str(described.get("action_label", "") or ""),
        "action_href": str(described.get("action_href", "") or ""),
    }


def _not_synced_copy() -> Dict[str, str]:
    from plexmix.services import readiness as rd

    return copy_of(rd.describe("library", rd.Cause.NOT_SYNCED))


def split_count(summary: str, count: str) -> tuple[str, str, str]:
    """Split ``summary`` around ``count`` so the count can render in mono.

    Returns ``(lead, num, tail)``; ``num`` is "" when the count is not a whole
    number inside the sentence ("1,204 tracks without embeddings" →
    ``("", "1,204", " tracks without embeddings")``).
    """
    if not summary or not count:
        return summary, "", ""
    start = 0
    while True:
        i = summary.find(count, start)
        if i < 0:
            return summary, "", ""
        end = i + len(count)
        before = summary[i - 1] if i > 0 else " "
        after = summary[end] if end < len(summary) else " "
        if not (before.isdigit() or before == ",") and not (after.isdigit() or after == ","):
            return summary[:i], count, summary[end:]
        start = i + 1


def blank_row(row_id: str) -> Dict[str, str]:
    """A placeholder for a row that is not shown (``state`` "hidden")."""
    row = {key: "" for key in (*ROW_KEYS, *ROW_EXTRA_KEYS)}
    row["id"] = row_id
    row["state"] = "hidden"
    return row


def build_row_map(
    rows: Sequence[Mapping[str, str]],
    fix_errors: Mapping[str, Mapping[str, str]],
    readiness: Mapping[str, Mapping[str, str]],
) -> Dict[str, Dict[str, str]]:
    """Every row id → its row (or a hidden placeholder), with display keys.

    Adds ``badge`` (kit badge classes for the tone), ``lead``/``num``/``tail``
    (the summary split around its count), ``checked_ago`` (connection rows)
    and ``error_*`` (the latest failure of the row's fix, from ``fix_errors``).
    Every id is present so the page can index any row safely.
    """
    out: Dict[str, Dict[str, str]] = {row_id: blank_row(row_id) for row_id in ROW_IDS}
    for raw in rows:
        row_id = str(raw.get("id", "") or "")
        if not row_id:
            continue
        row = blank_row(row_id)
        row.update({k: str(v if v is not None else "") for k, v in raw.items()})
        row["badge"] = _BADGES.get(row.get("tone", ""), _BADGES["idle"])
        row["lead"], row["num"], row["tail"] = split_count(row["summary"], row["count"])
        component = _CONNECTION_COMPONENTS.get(row_id)
        if component:
            entry = readiness.get(component) or {}
            row["checked_ago"] = str(entry.get("checked_ago", "") or "")
        out[row_id] = row
    for target, error in fix_errors.items():
        error_row = FIX_ROWS.get(target, "")
        if not error_row or out[error_row]["state"] == "hidden":
            continue
        out[error_row]["error_title"] = str(error.get("title", "") or "")
        out[error_row]["error_body"] = str(error.get("body", "") or "")
        out[error_row]["error_action_label"] = str(error.get("action_label", "") or "")
        out[error_row]["error_action_href"] = str(error.get("action_href", "") or "")
    return out


def fix_failure(target: str, copy: Mapping[str, str]) -> None:
    """Fail the running Doctor fix with a plain message and the action that fixes it."""
    title = str(copy.get("title", "") or "") or FIX_FAILED_MESSAGE
    task_store.update(
        FIX_JOB,
        extra={
            "fix_target": target,
            "error_body": str(copy.get("body", "") or ""),
            "error_action_label": str(copy.get("action_label", "") or ""),
            "error_action_href": str(copy.get("action_href", "") or ""),
        },
    )
    task_store.complete(FIX_JOB, status="failed", message=title)


def finish_fix(target: str, cancel_event: ThreadingEvent, result_msg: str) -> None:
    """Complete (or mark cancelled) the running Doctor fix."""
    task_store.update(FIX_JOB, extra={"fix_target": target, "result_msg": result_msg})
    task_store.complete(FIX_JOB, status="cancelled" if cancel_event.is_set() else "completed")


class ScanResult(NamedTuple):
    """One health scan: DB stats (None without a library) and fresh readiness."""

    stats: Optional[Dict[str, Any]]
    settings: Any
    facts: Any
    status: Any
    mb_rate_s: float


def scan_library() -> ScanResult:
    """Read the health stats and re-evaluate readiness (blocking: run in an executor).

    A database error propagates (the scan reports ``error``); a readiness
    failure is logged and leaves the current readiness in place.
    """
    from plexmix.config.settings import Settings
    from plexmix.services import readiness as rd

    stats = DoctorState._compute_health_stats()
    settings: Any = None
    facts: Any = None
    status: Any = None
    rate = 1.0
    try:
        settings = Settings.load_from_file()
        rate = float(settings.musicbrainz.rate_limit_delay or 1.0)
        facts = rd.collect_facts(settings)
        status = rd.evaluate(settings, facts)
    except Exception as e:
        logger.warning("Doctor could not refresh readiness: %s", safe_error_text(e))
        status = None
    return ScanResult(stats, settings, facts, status, rate)


def ai_unavailable_copy(settings: Any) -> Dict[str, str]:
    """Why the AI provider can't start, in readiness copy (blocking local cause first)."""
    from plexmix.services import readiness as rd
    from plexmix.services.registry import ai_provider_display_name

    try:
        entry = rd.evaluate(settings, rd.collect_facts(settings))["ai"]
        if entry.title and entry.level in (rd.Level.NOT_SET_UP, rd.Level.FAILING):
            return {
                "title": entry.title,
                "body": entry.body,
                "action_label": entry.fix_label,
                "action_href": entry.action_href,
            }
    except Exception as e:
        logger.warning("Could not evaluate the AI provider: %s", safe_error_text(e))
    label = ai_provider_display_name(settings.ai.default_provider or "")
    return copy_of(rd.describe("ai", rd.Cause.UNKNOWN, {"label": label}))


def ai_failure_copy(settings: Any, error: FatalProviderError) -> Dict[str, str]:
    """Copy for a tagging run the AI provider stopped; records the outcome."""
    from plexmix.services import readiness as rd
    from plexmix.services.registry import ai_provider_display_name
    from plexmix.ui.states.tagging_state import tagging_failure_cause

    cause, message = tagging_failure_cause(error)
    provider = settings.ai.default_provider or ""
    context = {
        "label": ai_provider_display_name(provider),
        "provider": provider,
        "model": rd.ai_model_for(settings),
        "detail": message,
    }
    described = copy_of(rd.describe("ai", cause, context))
    if cause == rd.Cause.UNKNOWN.value:
        described["title"] = error.user_message or described["title"]
        described["body"] = ""
    return described


class EmbeddingFixPrep(NamedTuple):
    settings: Any
    generator: Any
    fingerprint: str


def prepare_embedding_fix() -> EmbeddingFixPrep:
    """Settings and the embedding generator, or :class:`FixFailed` with the real cause.

    Blocking: run in an executor. A provider that can't start is recorded in
    readiness (failing, with its cause) and described the way every status
    surface describes it (missing key, missing local package, model load…).
    """
    from plexmix.config.settings import Settings
    from plexmix.services import readiness as rd
    from plexmix.services.providers import ProviderFailure, build_embedding_generator_with_reason

    settings = Settings.load_from_file()
    if not settings.database.get_db_path().exists():
        raise FixFailed(_not_synced_copy())
    fp = current_fingerprint_safe("embeddings", settings)
    generator, failure = build_embedding_generator_with_reason(settings)
    if generator is None:
        provider = (settings.embedding.default_provider or "").strip().lower()
        failure = failure or ProviderFailure(rd.Cause.UNKNOWN, provider)
        raise FixFailed(embedding_start_failure(settings, fp, failure))
    return EmbeddingFixPrep(settings, generator, fp)


def embedding_start_failure(settings: Any, fp: str, failure: "ProviderFailure") -> Dict[str, str]:
    """Copy for an embedding provider that can't start; records the outcome.

    ``failure`` says why: a missing key, a missing package, a model that won't load…
    """
    from plexmix.services import readiness as rd

    detail = failure.detail if failure.cause == rd.Cause.UNKNOWN else ""
    record_readiness("embeddings", "failing", failure.cause.value, message=detail, fingerprint=fp)
    return copy_of(
        rd.describe_failure("embeddings", failure, {"model": rd.embedding_model_for(settings)})
    )


def embedding_run_failure(settings: Any, fp: str, error: BaseException) -> Dict[str, str]:
    """Copy for an embedding run that failed part-way; records the outcome."""
    from plexmix.services import readiness as rd
    from plexmix.services.registry import embedding_provider_display_name

    key = ""
    try:
        key = rd.resolve_secret("embeddings", settings) or ""
    except Exception:
        key = ""
    cause, message = provider_failure_cause(error, key=key)
    record_readiness("embeddings", "failing", cause, message=message, fingerprint=fp)
    provider = (settings.embedding.default_provider or "").strip().lower()
    described = copy_of(
        rd.describe(
            "embeddings",
            cause,
            {
                "label": embedding_provider_display_name(provider),
                "provider": provider,
                "model": rd.embedding_model_for(settings),
                "detail": message,
            },
        )
    )
    described["title"] = friendly_error(error, [key])
    if cause == rd.Cause.UNKNOWN.value:
        described["body"] = ""
    return described


def embedding_refusal_copy(settings: Any, fp: str, refusal: "RegenerateRefused") -> Dict[str, str]:
    """Copy for a rebuild the provider check refused; records the outcome.

    The cause is the one every status surface shows for it: a provider that could
    not start carries its typed reason, and one that did not answer the test
    request is classified like any other failed embedding request. The check runs
    before the rebuild changes anything, and the row says so.
    """
    from plexmix.services import readiness as rd
    from plexmix.services.providers import ProviderFailure

    if refusal.error is not None:
        described = embedding_run_failure(settings, fp, refusal.error)
    else:
        provider = (settings.embedding.default_provider or "").strip().lower()
        failure = refusal.failure or ProviderFailure(rd.Cause.UNKNOWN, provider)
        if failure.cause == rd.Cause.UNKNOWN and refusal.detail:
            # The refusal's own text: keys and address credentials are already out.
            failure = replace(failure, detail=refusal.detail)
        described = embedding_start_failure(settings, fp, failure)
    described["body"] = f"{described['body']} {REBUILD_NOTHING_CHANGED}".strip()
    return described


def check_embedding_rebuild() -> EmbeddingFixPrep:
    """Settings and an embedding generator that answered one test request.

    Blocking: run in an executor. This is the check every regenerate action runs
    before it changes anything (``regenerate_service.check_providers``). A provider
    that is not ready is recorded in readiness as failing with its cause, and
    :class:`FixFailed` carries the same cause to the Embeddings row.
    """
    from plexmix.config.settings import Settings
    from plexmix.services.regenerate_service import RegenerateRefused, check_providers

    settings = Settings.load_from_file()
    if not settings.database.get_db_path().exists():
        raise FixFailed(_not_synced_copy())
    fp = current_fingerprint_safe("embeddings", settings)
    try:
        checked = check_providers(settings, need_ai=False, need_embeddings=True)
    except RegenerateRefused as refusal:
        logger.warning("Doctor embedding rebuild refused: %s", refusal.message)
        # ``from None``: the refusal holds the provider's own error, which is not redacted.
        raise FixFailed(embedding_refusal_copy(settings, fp, refusal)) from None
    return EmbeddingFixPrep(settings, checked.embedding_generator, fp)


def unreadable_index_copy(files: Sequence[Path]) -> Dict[str, str]:
    """Copy for a saved search index that can be neither read nor moved aside.

    PlexMix never writes over such a file, so a rebuild could not save its index
    and would end without one.
    """
    names = " and ".join(str(file) for file in files)
    return {
        "title": "The search index file couldn't be read or moved aside",
        "body": (
            f"PlexMix won't write over a file it couldn't read. Move {names} out of the "
            f"way, then run this fix again. {REBUILD_NOTHING_CHANGED}"
        ),
    }


class IndexLeft(NamedTuple):
    """A fix saved embeddings, and the search index was left as it is.

    ``left_out`` tracks hold an embedding and none of the current provider and
    vector size, so ``index_rebuild.rebuild_index`` built and saved nothing.
    ``other_provider`` of them hold embeddings of other providers only.
    """

    left_out: int
    other_provider: int = 0


def index_left(db: Any, model: str, left_out: int) -> Optional[IndexLeft]:
    """What kept the index of ``model`` from being rebuilt; None when it was rebuilt.

    ``left_out`` is the refusal of the last rebuild (``library_state.refused_rebuild``).
    Blocking (one count query): call it where ``db`` is used.
    """
    if left_out <= 0:
        return None
    other = db.count_tracks_with_only_other_embeddings(model)
    return IndexLeft(left_out, other if isinstance(other, int) and other > 0 else 0)


def index_not_updated_failure(settings: Any, left: IndexLeft, done_note: str) -> Dict[str, str]:
    """Row copy for a fix that saved embeddings while the search index was left as it is.

    The words are the Library's (``library_state.index_not_updated_copy``): how many
    tracks are in the way and what replaces their embeddings. ``done_note`` says
    what the fix did write. "Rebuild embeddings" is a fix of this page, which the
    Embeddings row offers itself while the index does not match the provider, so
    the row's link is the other way out: switching back in Settings.
    """
    from plexmix.services import readiness as rd
    from plexmix.ui.states.library_state import index_not_updated_copy

    copy = index_not_updated_copy(settings, left.left_out, left.other_provider)
    on_this_page = copy["action_href"] == rd.DOCTOR_HREF
    return {
        "title": copy["title"],
        "body": f"{done_note} {copy['body']}".strip(),
        "action_label": copy["secondary_label"] if on_this_page else copy["action_label"],
        "action_href": copy["secondary_href"] if on_this_page else copy["action_href"],
    }


def index_left_note(settings: Any, left: Optional[IndexLeft]) -> str:
    """One more sentence for a rebuild that was stopped part-way; "" when the index followed.

    Says that the search index was not updated, how many tracks are in the way and
    what replaces their embeddings: "Rebuild embeddings" again while the saved
    index does not match the provider.
    """
    if left is None:
        return ""
    from plexmix.ui.states.library_state import index_not_updated_copy

    copy = index_not_updated_copy(settings, left.left_out, left.other_provider)
    return f"{copy['title']}. {copy['body']}"


def rebuild_progress_note(done: int, total: int) -> str:
    """What a rebuild that did not replace every embedding left behind."""
    if done <= 0:
        return REBUILD_NOTHING_CHANGED
    return (
        f"{format_count(done)} of {pluralize(total, 'track')} got a new embedding. "
        "The others keep the embedding they had."
    )


def rebuild_embeddings(prep: EmbeddingFixPrep, cancel_event: ThreadingEvent) -> str:
    """Replace the embedding of every track in place; returns the result message.

    Blocking: run in an executor, after :func:`check_embedding_rebuild`. Nothing is
    cleared first. A track keeps its embedding row until the new one is saved over
    it, so a provider error or a cancel request leaves every track with its new or
    its previous embedding. The run marker of the CLI regenerate commands is not
    read: this fix embeds every track, as its confirmation says.

    The search index is written by the sync engine's own code, which goes through
    ``index_rebuild.rebuild_index``: the index is built and saved only when it is
    complete. After a pass that reached its end, ``finish_embeddings_pass`` removes
    the rows of other models (only once every track has a row of this one) and
    rebuilds the index. After a pass that was stopped part-way,
    ``follow_rows_after_stopped_pass`` lets the index follow the rows, the new and
    the previous. While a track still holds only an embedding of another provider
    or vector size, nothing is built and the saved index stays as it is: an index
    of the rows that fit would take the place of the library's, and with it would
    go the dimension mismatch that makes Doctor offer this fix. The result then
    says that the search index was not updated (:func:`index_left_note`), and a
    rebuild that reached its end fails with that message instead of a success.

    Raises :class:`FixFailed` when the provider failed part-way. The failure is
    recorded in readiness, and the row message says how far the rebuild got.
    """
    from plexmix.database.sqlite_manager import SQLiteManager
    from plexmix.plex.sync import finish_embeddings_pass, follow_rows_after_stopped_pass
    from plexmix.services.sync_service import build_vector_index
    from plexmix.services.tagging_service import generate_embeddings_for_tracks
    from plexmix.ui.states.library_state import refused_rebuild

    settings, generator = prep.settings, prep.generator
    model = generator.provider_name
    done = 0
    error: Optional[Exception] = None
    left: Optional[IndexLeft] = None

    with SQLiteManager(str(settings.database.get_db_path())) as db:
        tracks = [track for track in db.get_all_tracks() if track.id is not None]
        total = len(tracks)
        if not total:
            return "No tracks available to embed."
        extra = {"fix_target": "embeddings_full", "fix_total": total}
        task_store.update(FIX_JOB, extra=extra)
        # Opened before the first batch, so an index that cannot be replaced stops
        # the fix before the provider is paid for anything. A saved index that
        # cannot be read is moved aside by the load, never deleted. One that cannot
        # be moved either is never written over, so this fix could not save its index.
        vector_index = build_vector_index(settings, generator)
        if vector_index.unmoved_paths:
            raise FixFailed(unreadable_index_copy(vector_index.unmoved_paths))

        def on_progress(generated: int, _total: int) -> None:
            nonlocal done
            done = generated
            task_store.update(
                FIX_JOB,
                progress=generated,
                message=f"Regenerating embeddings... {generated}/{total}",
                extra=extra,
            )

        try:
            done = generate_embeddings_for_tracks(
                db,
                generator,
                tracks,
                batch_size=EMBEDDING_BATCH_SIZE,
                progress_callback=on_progress,
                cancel_event=cancel_event,
            )
        except Exception as e:
            error = e

        stopped = error is not None or (cancel_event.is_set() and done < total)
        if done and not stopped:
            finish_embeddings_pass(db, vector_index, model)
        elif done:
            # Never raises: the rows are saved either way, and what stopped the
            # pass is what this fix reports.
            follow_rows_after_stopped_pass(db, vector_index, model)
        if done:
            left = index_left(db, model, refused_rebuild(vector_index))

    if error is not None:
        described = embedding_run_failure(settings, prep.fingerprint, error)
        logger.error(
            "Doctor embedding rebuild stopped after %d of %d tracks: %s",
            done,
            total,
            described["title"],
        )
        note = f"{rebuild_progress_note(done, total)} {index_left_note(settings, left)}".strip()
        described["body"] = f"{described['body']} {note}".strip()
        raise FixFailed(described)

    if done:
        record_readiness("embeddings", "ready", fingerprint=prep.fingerprint)
    if done >= total:
        if left is not None:
            # Every track got its new embedding, and the index could still not be
            # rebuilt. That is no success: the row says what is in the way.
            got = (
                f"{'The' if total == 1 else 'All'} {pluralize(total, 'track')} got a new embedding."
            )
            raise FixFailed(index_not_updated_failure(settings, left, got))
        return f"Rebuilt the search index with {pluralize(done, 'track')}."
    if left is not None and not cancel_event.is_set():
        # The provider answered with fewer vectors than it was sent, and the index
        # could not be rebuilt either. Like a rebuild that replaced every track, that
        # is no success: the row, the toast and the status say what is in the way.
        raise FixFailed(
            index_not_updated_failure(settings, left, rebuild_progress_note(done, total))
        )
    note = f"{rebuild_progress_note(done, total)} {index_left_note(settings, left)}".strip()
    if cancel_event.is_set():
        task_store.update(FIX_JOB, extra={"cancel_msg": f"{FIX_CANCELLED_MESSAGE}. {note}"})
    return note


class TagFixPrep(NamedTuple):
    settings: Any
    provider: Any
    fingerprint: str


def prepare_tag_fix() -> TagFixPrep:
    """Settings and the AI provider, or :class:`FixFailed` with the real cause (blocking)."""
    from plexmix.config.settings import Settings
    from plexmix.services.providers import build_ai_provider

    settings = Settings.load_from_file()
    if not settings.database.get_db_path().exists():
        raise FixFailed(_not_synced_copy())
    fp = current_fingerprint_safe("ai", settings)
    provider = build_ai_provider(settings, silent=True)
    if provider is None:
        raise FixFailed(ai_unavailable_copy(settings))
    return TagFixPrep(settings, provider, fp)


def cleanup_orphans() -> int:
    """Delete embeddings whose track is gone; returns how many (blocking)."""
    from plexmix.config.settings import Settings
    from plexmix.database.sqlite_manager import SQLiteManager

    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()
    if not db_path.exists():
        raise FixFailed(_not_synced_copy())
    with SQLiteManager(str(db_path)) as db:
        cursor = db.get_connection().cursor()
        cursor.execute("DELETE FROM embeddings WHERE track_id NOT IN (SELECT id FROM tracks)")
        deleted = int(cursor.rowcount or 0)
        db._commit()
    return deleted


def _audio_unavailable_copy() -> Dict[str, str]:
    from plexmix.services import readiness as rd

    copy = copy_of(rd.describe("audio", rd.Cause.MISSING_DEPENDENCY))
    if not copy["action_href"]:
        copy["action_label"] = "Audio settings"
        copy["action_href"] = rd.SETTINGS_SECTION_HREFS["audio"]
    return copy


def _need(count: int, what: str) -> str:
    verb = "needs" if count == 1 else "need"
    return f"{pluralize(count, 'track')} {verb} {what}"


# ── State ────────────────────────────────────────────────────────────────


class DoctorState(AppState):
    # Counts from the last scan (meaningful only while ``stats_loaded``).
    doctor_total_tracks: int = 0
    doctor_tracks_with_embeddings: int = 0
    doctor_orphaned_embeddings: int = 0
    doctor_untagged_tracks: int = 0
    doctor_tracks_needing_embeddings: int = 0
    doctor_tracks_without_audio: int = 0
    doctor_audio_features_count: int = 0
    doctor_tracks_without_musicbrainz: int = 0
    doctor_musicbrainz_enriched: int = 0

    # idle (never scanned) | checking | done | no_database | error
    scan_state: str = "idle"
    # True once a scan measured the library; a re-scan keeps showing that data.
    stats_loaded: bool = False
    # The scan error shown in the status region (scan_state "error" only).
    check_message: str = ""

    is_fixing: bool = False
    fix_message: str = ""
    fix_progress: int = 0
    fix_total: int = 0
    current_fix_target: str = ""

    # Latest failure per fix target: {title, body, action_label, action_href}.
    fix_errors: dict[str, dict[str, str]] = {}

    # Scope confirmation (start_fix → confirm_fix).
    fix_confirm_open: bool = False
    pending_fix: str = ""
    fix_scope: dict[str, str] = {}

    show_retag_confirm: bool = False

    _scan_started: float = 0.0
    _mb_rate_s: float = 1.0

    @rx.event
    def on_load(self) -> Any:
        if not self.check_auth():
            self.is_page_loading = False
            return None
        self.load_app_data()
        # A fix that finished while this page was away still gets its toast (its
        # re-scan is the one below).
        polled = self._poll_fix_progress()
        toasts = [
            event
            for event in (polled if isinstance(polled, list) else [polled])
            if event is not None and event is not DoctorState.run_health_check
        ]
        # Re-check Plex reachability in the background when due (TTL + fingerprint).
        return [*toasts, DoctorState.run_health_check, *self._plex_readiness_events()]

    # ── Scan ─────────────────────────────────────────────────────────

    @staticmethod
    def _compute_health_stats() -> Optional[Dict[str, Any]]:
        """Read health stats from the DB. Pure (no state mutation), so it is safe
        to run in an executor and keep the heavy queries off the event loop.
        Returns None when there is no library database."""
        from plexmix.config.settings import Settings
        from plexmix.database.sqlite_manager import SQLiteManager

        settings = Settings.load_from_file()
        db_path = settings.database.get_db_path()
        if not db_path.exists():
            return None

        with SQLiteManager(str(db_path)) as db:
            cursor = db.get_connection().cursor()
            cursor.execute("SELECT COUNT(*) FROM tracks")
            total_tracks = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(DISTINCT track_id) FROM embeddings")
            tracks_with_embeddings = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM embeddings WHERE track_id NOT IN (SELECT id FROM tracks)"
            )
            orphaned_count = cursor.fetchone()[0]
            # count_untagged_tracks defines "untagged" for every surface.
            untagged_count = db.count_untagged_tracks()
            cursor.execute(
                "SELECT COUNT(*) FROM tracks WHERE id NOT IN (SELECT DISTINCT track_id FROM embeddings)"
            )
            tracks_needing_embeddings = cursor.fetchone()[0]
            return {
                "total_tracks": total_tracks,
                "tracks_with_embeddings": tracks_with_embeddings,
                "orphaned_count": orphaned_count,
                "untagged_count": untagged_count,
                "tracks_needing_embeddings": tracks_needing_embeddings,
                "audio_features_count": db.get_audio_features_count(),
                "tracks_without_audio": len(db.get_tracks_without_audio_features()),
                "mb_enriched": db.get_musicbrainz_enrichment_count(),
                "mb_without": len(db.get_tracks_without_musicbrainz()),
            }

    def _apply_health_stats(self, stats: Optional[Dict[str, Any]]) -> None:
        """Apply computed stats to state. Must run with state access (inside
        `async with self` for a background event)."""
        if stats is None:
            return
        self.doctor_total_tracks = int(stats["total_tracks"])
        self.doctor_tracks_with_embeddings = int(stats["tracks_with_embeddings"])
        self.doctor_orphaned_embeddings = int(stats["orphaned_count"])
        self.doctor_untagged_tracks = int(stats["untagged_count"])
        self.doctor_tracks_needing_embeddings = int(stats["tracks_needing_embeddings"])
        self.doctor_audio_features_count = int(stats["audio_features_count"])
        self.doctor_tracks_without_audio = int(stats["tracks_without_audio"])
        self.doctor_musicbrainz_enriched = int(stats["mb_enriched"])
        self.doctor_tracks_without_musicbrainz = int(stats["mb_without"])

    def _apply_scan(self, result: ScanResult) -> None:
        """Write one scan to state (inside ``async with self``)."""
        if result.stats is None:
            self.scan_state = "no_database"
            self.stats_loaded = False
            self._apply_health_stats(dict.fromkeys(_STAT_KEYS, 0))
        else:
            self._apply_health_stats(result.stats)
            self.stats_loaded = True
            self.scan_state = "done"
        self._mb_rate_s = result.mb_rate_s
        if result.status is not None:
            self._apply_status(result.settings, result.facts, result.status)

    @rx.event(background=True)
    async def run_health_check(self) -> None:
        """Scan the library and refresh readiness.

        Returns at once while a scan is already running, so the page load and
        the page mount (which both ask) run one scan. A new scan clears the
        fix failures shown on the rows.
        """
        async with self:
            if not self.access_allowed():
                self.is_page_loading = False
                return
            now = time.time()
            if self.scan_state == "checking" and now - self._scan_started < SCAN_STALE_S:
                return
            self.scan_state = "checking"
            self._scan_started = now
            self.check_message = ""
            self.fix_errors = {}

        loop = asyncio.get_running_loop()
        try:
            # The DB scan and the readiness evaluation are blocking: keep them off
            # the event loop, then apply the results under the state lock.
            result = await loop.run_in_executor(None, scan_library)
        except Exception as e:
            logger.error("Error during health check: %s", safe_error_text(e))
            async with self:
                self.scan_state = "error"
                self.stats_loaded = False
                self.check_message = SCAN_ERROR_MESSAGE
                self.is_page_loading = False
            return

        async with self:
            self._apply_scan(result)
            self.is_page_loading = False

    # ── Computed: rows and verdict ───────────────────────────────────

    def _health_stats(self) -> Optional[Dict[str, int]]:
        if not self.stats_loaded:
            return None
        return {
            "total_tracks": self.doctor_total_tracks,
            "tracks_with_embeddings": self.doctor_tracks_with_embeddings,
            "orphaned_count": self.doctor_orphaned_embeddings,
            "untagged_count": self.doctor_untagged_tracks,
            "tracks_needing_embeddings": self.doctor_tracks_needing_embeddings,
            "audio_features_count": self.doctor_audio_features_count,
            "tracks_without_audio": self.doctor_tracks_without_audio,
            "mb_enriched": self.doctor_musicbrainz_enriched,
            "mb_without": self.doctor_tracks_without_musicbrainz,
        }

    def _row_list(self) -> List[Dict[str, str]]:
        """readiness.doctor_rows over the last scan and the shared readiness."""
        from plexmix.services import readiness as rd

        status = {str(k): dict(v) for k, v in (self.readiness or {}).items()}
        stats = self._health_stats()
        # With measured stats, a re-scan keeps showing them (stale but real).
        scan = "done" if stats is not None else (self.scan_state or "idle")
        try:
            return [dict(row) for row in rd.doctor_rows(stats, status, {"scan_state": scan})]
        except Exception as e:  # never break the page over a bad snapshot
            logger.warning("Could not build Doctor rows: %s", safe_error_text(e))
            return [dict(row) for row in rd.doctor_rows(None, {}, {"scan_state": "checking"})]

    @rx.var(cache=True)
    def rows(self) -> list[dict[str, str]]:
        """Connection rows, then library rows (``readiness.doctor_rows`` keys)."""
        return self._row_list()

    @rx.var(cache=True)
    def row_map(self) -> dict[str, dict[str, str]]:
        """Every row id → its row with display keys (see :func:`build_row_map`)."""
        return build_row_map(self._row_list(), self.fix_errors, self.readiness or {})

    @rx.var(cache=True)
    def verdict(self) -> dict[str, str]:
        """The hero: ``{state, title, sub, tone}``, derived only from the rows."""
        from plexmix.services import readiness as rd

        rows: List[Mapping[str, str]] = list(self._row_list())
        return dict(rd.verdict(rows))

    @rx.var(cache=True)
    def scan_summary(self) -> str:
        """The status-region line: the scan error, or the verdict's sub-line."""
        if self.scan_state == "error" and self.check_message:
            return self.check_message
        from plexmix.services import readiness as rd

        rows: List[Mapping[str, str]] = list(self._row_list())
        return str(rd.verdict(rows).get("sub", ""))

    @rx.var(cache=True)
    def is_checking(self) -> bool:
        return self.scan_state == "checking"

    @rx.var(cache=True)
    def library_collapsed(self) -> bool:
        """The library list is one block: no library yet, or the scan failed."""
        return self.scan_state in ("no_database", "error") or (
            self.stats_loaded and self.doctor_total_tracks == 0
        )

    # ── Fixes: confirmation ──────────────────────────────────────────

    def _fix_count(self, target: str) -> int:
        if not self.stats_loaded:
            return 0
        counts = {
            "embeddings_incremental": self.doctor_tracks_needing_embeddings,
            "embeddings_full": self.doctor_total_tracks,
            "cleanup": self.doctor_orphaned_embeddings,
            "tags": self.doctor_untagged_tracks,
            "audio_analysis": self.doctor_tracks_without_audio,
            "musicbrainz": self.doctor_tracks_without_musicbrainz,
        }
        return int(counts.get(target, 0) or 0)

    def _readiness_entry(self, component: str) -> Dict[str, str]:
        entry = (self.readiness or {}).get(component) or {}
        return {str(k): str(v) for k, v in entry.items()}

    def _fix_blocker(self, target: str) -> Optional[Dict[str, str]]:
        """Why a fix can't start, from the shared readiness (local facts only)."""
        if target in ("embeddings_incremental", "embeddings_full"):
            entry = self._readiness_entry("embeddings")
            blocked = entry.get("blocking") == "true" or entry.get("level") == "not_set_up"
        elif target == "tags":
            entry = self._readiness_entry("ai")
            blocked = entry.get("level") == "not_set_up"
        elif target == "audio_analysis":
            entry = self._readiness_entry("audio")
            if entry.get("level") == "not_installed":
                return _audio_unavailable_copy()
            blocked = False
        else:
            return None
        if not blocked:
            return None
        return {
            "title": entry.get("title", "") or entry.get("state", ""),
            "body": entry.get("body", ""),
            "action_label": entry.get("fix_label", "") or entry.get("action_label", ""),
            "action_href": entry.get("action_href", ""),
        }

    def _fix_scope(self, target: str, count: int) -> Dict[str, str]:
        """``readiness.describe_job_scope`` copy plus provider_label, model, target."""
        from plexmix.services import readiness as rd

        label = model = ""
        if target == "tags":
            entry = self._readiness_entry("ai")
            label = self.ai_provider_name or entry.get("label", "")
            model = self.ai_model_name or entry.get("model", "")
            # Where metadata goes: a Local or Custom endpoint can be another machine.
            destination = rd.data_destination(entry)
            scope = rd.describe_job_scope(
                "tagging", count, label, model, bool(destination), {"sends_to": destination}
            )
        elif target in ("embeddings_incremental", "embeddings_full"):
            entry = self._readiness_entry("embeddings")
            label = self.embedding_provider_name or entry.get("label", "")
            model = self.embedding_model_name or entry.get("model", "")
            destination = rd.data_destination(entry)
            scope = rd.describe_job_scope(
                "embeddings",
                count,
                label,
                model,
                bool(destination),
                {"rebuild": target == "embeddings_full", "sends_to": destination},
            )
        elif target == "musicbrainz":
            label = "MusicBrainz"
            scope = rd.describe_job_scope("musicbrainz", count, extra={"rate_s": self._mb_rate_s})
        elif target == "audio_analysis":
            scope = rd.describe_job_scope("audio", count)
        else:
            scope = rd.describe_job_scope("cleanup", count)
        out = {str(k): str(v) for k, v in scope.items()}
        out.update(provider_label=label, model=model, target=target, count=format_count(count))
        return out

    def _show_running_fix(self) -> None:
        """Show the Doctor fix that is running (perhaps started from another tab)."""
        entry = task_store.get(FIX_JOB)
        if entry is not None and entry.status == "running":
            self._apply_running(entry)

    def _apply_running(self, entry: Any) -> None:
        self.is_fixing = True
        self.fix_progress = int(entry.progress or 0)
        self.fix_message = str(entry.message or "")
        self.fix_total = int(entry.extra.get("fix_total", 0) or 0)
        self.current_fix_target = str(entry.extra.get("fix_target", "") or "")

    def _clear_fix_progress(self) -> None:
        self.is_fixing = False
        self.fix_message = ""
        self.fix_progress = 0
        self.fix_total = 0
        self.current_fix_target = ""

    def _close_fix_confirm(self) -> None:
        self.fix_confirm_open = False
        self.pending_fix = ""

    @rx.event
    def start_fix(self, target: str) -> Any:
        """Open the scope confirmation for a fix; nothing starts until it is confirmed.

        Refused (with a toast) while another fix runs, when the fix's provider
        or package is unavailable (the cause and its Settings action go on the
        row via ``fix_errors``), or when the last scan found nothing to fix.
        """
        target = str(target or "")
        if target not in FIX_HANDLERS:
            return None
        if self.is_fixing or task_store.is_running(FIX_JOB):
            if self.is_fixing and target == self.current_fix_target:
                return None  # this fix is the one running
            self._show_running_fix()
            return rx.toast.info(ANOTHER_FIX_RUNNING)
        blocker = self._fix_blocker(target)
        if blocker is not None:
            errors = {k: dict(v) for k, v in self.fix_errors.items()}
            errors[target] = blocker
            self.fix_errors = errors
            return rx.toast.error(blocker["title"] or FIX_FAILED_MESSAGE)
        count = self._fix_count(target)
        if count <= 0:
            return rx.toast.info(NOTHING_TO_FIX)
        self.fix_scope = self._fix_scope(target, count)
        self.pending_fix = target
        self.fix_confirm_open = True
        return None

    @rx.event
    def set_fix_confirm_open(self, is_open: bool) -> None:
        """``on_open_change`` of the fix confirmation (Escape, Cancel).

        A close discards the pending fix; idempotent. True is a no-op because the
        dialog only opens through :meth:`start_fix`.
        """
        if not is_open:
            self._close_fix_confirm()

    @rx.event
    def cancel_fix(self) -> None:
        """Dismiss the confirmation: nothing starts."""
        self._close_fix_confirm()

    @rx.event
    def confirm_fix(self) -> Any:
        """Close the confirmation and start the pending fix's background handler."""
        target = self.pending_fix if self.fix_confirm_open else ""
        self._close_fix_confirm()
        name = FIX_HANDLERS.get(target)
        if not name:
            return None
        return getattr(DoctorState, name)

    # ── Fixes: running ───────────────────────────────────────────────

    async def _begin_fix(self, target: str, message: str) -> Optional[ThreadingEvent]:
        """Claim the single Doctor fix slot and show it; None when it can't start."""
        async with self:
            if not self.access_allowed():
                return None
            cancel_event = task_store.start(FIX_JOB, message=message)
            if cancel_event is None:
                # Another Doctor fix is running (perhaps from another tab): show it.
                self._show_running_fix()
                return None
            task_store.update(FIX_JOB, extra={"fix_target": target})
            self.fix_errors = {}
            self.is_fixing = True
            self.fix_message = message
            self.fix_progress = 0
            self.fix_total = 0
            self.current_fix_target = target
        return cancel_event

    async def _run_fix(
        self,
        target: str,
        message: str,
        body: Callable[[ThreadingEvent], Awaitable[str]],
    ) -> None:
        """Run one fix body in the Doctor slot and complete its task either way."""
        cancel_event = await self._begin_fix(target, message)
        if cancel_event is None:
            return
        try:
            result_msg = await body(cancel_event)
        except FixFailed as e:
            fix_failure(target, e.copy)
            return
        except FatalProviderError as e:
            fix_failure(target, {"title": e.user_message or FIX_FAILED_MESSAGE})
            return
        except Exception as e:
            logger.error("Doctor fix %s failed: %s", target, safe_error_text(e))
            fix_failure(target, {"title": friendly_error(e)})
            return
        finish_fix(target, cancel_event, result_msg)

    @rx.event(background=True)
    async def delete_orphaned_embeddings(self) -> None:
        async def body(cancel_event: ThreadingEvent) -> str:
            loop = asyncio.get_running_loop()
            deleted = await loop.run_in_executor(None, cleanup_orphans)
            return f"Removed {pluralize(deleted, 'orphaned embedding')}."

        await self._run_fix("cleanup", "Removing orphaned embeddings…", body)

    @rx.event(background=True)
    async def generate_missing_embeddings(self) -> None:
        await self._run_fix(
            "embeddings_incremental",
            "Generating embeddings…",
            lambda cancel_event: self._embeddings_body(cancel_event, rebuild=False),
        )

    @rx.event(background=True)
    async def regenerate_all_embeddings(self) -> None:
        await self._run_fix(
            "embeddings_full",
            "Rebuilding embeddings…",
            lambda cancel_event: self._embeddings_body(cancel_event, rebuild=True),
        )

    async def _embeddings_body(self, cancel_event: ThreadingEvent, *, rebuild: bool) -> str:
        from plexmix.ui.states.library_state import (
            REGENERATE_RUNNING_BODY,
            REGENERATE_RUNNING_TITLE,
            regenerate_sync_running,
        )

        if regenerate_sync_running():
            # The Library's "Regenerate" sync is replacing the embeddings and saves
            # the search index itself. Two writers would pay the provider twice and
            # save the same index files over each other.
            raise FixFailed(
                {
                    "title": REGENERATE_RUNNING_TITLE,
                    "body": f"{REGENERATE_RUNNING_BODY} {REBUILD_NOTHING_CHANGED}",
                }
            )
        if rebuild:
            return await self._rebuild_embeddings_body(cancel_event)

        from plexmix.database.sqlite_manager import SQLiteManager
        from plexmix.database.vector_index import VectorIndex

        loop = asyncio.get_running_loop()
        prep = await loop.run_in_executor(None, prepare_embedding_fix)
        settings, generator = prep.settings, prep.generator
        db_path = settings.database.get_db_path()
        index_path = settings.database.get_index_path()

        vector_index = await loop.run_in_executor(
            None,
            lambda: VectorIndex(dimension=generator.get_dimension(), index_path=str(index_path)),
        )

        with SQLiteManager(str(db_path)) as db:
            tracks_to_embed = await loop.run_in_executor(
                None,
                lambda: [
                    t
                    for t in db.get_all_tracks()
                    if t.id is not None and not db.get_embedding_by_track_id(t.id)
                ],
            )
            try:
                count, left = await self._generate_embeddings_for_tracks(
                    tracks_to_embed=tracks_to_embed,
                    embedding_generator=generator,
                    vector_index=vector_index,
                    db=db,
                    index_path=index_path,
                    progress_label="Generating embeddings...",
                    cancel_event=cancel_event,
                )
            except Exception as e:
                logger.error("Doctor embedding fix failed: %s", safe_error_text(e))
                raise FixFailed(embedding_run_failure(settings, prep.fingerprint, e)) from e

        if count:
            record_readiness("embeddings", "ready", fingerprint=prep.fingerprint)
        if left is not None:
            # The new embeddings are saved, and the search index was left as it is:
            # tracks hold an embedding of another provider or vector size. That is
            # no success, so the row says what is in the way.
            wrote = f"Generated embeddings for {pluralize(count, 'track')}." if count else ""
            if cancel_event.is_set():
                # The user stopped it: the task ends as cancelled, as a rebuild that is
                # stopped does, and its message still says that the index was not updated.
                note = f"{wrote} {index_left_note(settings, left)}".strip()
                task_store.update(FIX_JOB, extra={"cancel_msg": f"{FIX_CANCELLED_MESSAGE}. {note}"})
                return note
            raise FixFailed(index_not_updated_failure(settings, left, wrote))
        if count:
            return f"Generated embeddings for {pluralize(count, 'track')}."
        return "All tracks already have embeddings."

    async def _rebuild_embeddings_body(self, cancel_event: ThreadingEvent) -> str:
        """The "Rebuild embeddings" fix: check the provider, then replace every embedding.

        Nothing is cleared before its replacement exists (see :func:`rebuild_embeddings`).
        Both steps block on the provider, the database and the index, so both run in
        an executor, and neither touches state.
        """
        loop = asyncio.get_running_loop()
        prep = await loop.run_in_executor(None, check_embedding_rebuild)
        return await loop.run_in_executor(None, rebuild_embeddings, prep, cancel_event)

    @rx.event
    def open_retag_confirm(self):
        self.show_retag_confirm = True

    @rx.event
    def close_retag_confirm(self):
        self.show_retag_confirm = False

    @rx.event
    def confirm_retag(self):
        self.show_retag_confirm = False
        return DoctorState.regenerate_all_tags

    @rx.event(background=True)
    async def regenerate_all_tags(self) -> None:
        """Regenerate tags for ALL tracks (force retag)."""
        await self._run_fix(
            "tags",
            "Regenerating tags for all tracks…",
            lambda cancel_event: self._tags_body(cancel_event, all_tracks=True),
        )

    @rx.event(background=True)
    async def regenerate_missing_tags(self) -> None:
        await self._run_fix(
            "tags",
            "Tagging untagged tracks…",
            lambda cancel_event: self._tags_body(cancel_event, all_tracks=False),
        )

    async def _tags_body(self, cancel_event: ThreadingEvent, *, all_tracks: bool) -> str:
        from plexmix.ai.tag_generator import TagGenerator, clean_tag_list
        from plexmix.database.sqlite_manager import SQLiteManager
        from plexmix.services.tagging_service import build_track_embedding_data

        loop = asyncio.get_running_loop()
        prep = await loop.run_in_executor(None, prepare_tag_fix)
        tag_generator = TagGenerator(prep.provider)
        db_path = prep.settings.database.get_db_path()

        with SQLiteManager(str(db_path)) as db:
            if all_tracks:
                # TagGenerator expects a list of track-data dicts, not Track model
                # objects; passing the models raised TypeError and the whole
                # force-retag crashed before saving anything.
                track_data_list: List[Any] = await loop.run_in_executor(
                    None,
                    lambda: [build_track_embedding_data(db, t) for t in db.get_all_tracks()],
                )
                if not track_data_list:
                    return "No tracks in the library."
            else:
                track_data_list = await loop.run_in_executor(
                    None, lambda: list(db.get_tracks_by_filter(has_no_tags=True))
                )
                if not track_data_list:
                    return "All tracks already have AI-generated tags."

            total = len(track_data_list)
            task_store.update(FIX_JOB, extra={"fix_target": "tags", "fix_total": total})

            def progress_callback(batch_num: int, batches: int, tracks_tagged: int) -> None:
                task_store.update(
                    FIX_JOB,
                    progress=tracks_tagged,
                    message=f"Tagging tracks... {tracks_tagged}/{total}",
                    extra={"fix_target": "tags", "fix_total": total},
                )

            def run_tag_regen() -> int:
                fatal: Optional[FatalProviderError] = None
                try:
                    results = tag_generator.generate_tags_batch(
                        track_data_list,
                        batch_size=20,
                        progress_callback=progress_callback,
                        cancel_event=cancel_event,
                    )
                except FatalProviderError as e:
                    results = e.partial_results
                    fatal = e

                count = 0
                for track_id, tag_data in results.items():
                    # Only items with content count (``clean_tag_list``): a list of
                    # blanks is an empty list, and never writes "" or "," over tags.
                    tags = ",".join(clean_tag_list(tag_data.get("tags")))
                    environments = ",".join(clean_tag_list(tag_data.get("environments")))
                    instruments = ",".join(clean_tag_list(tag_data.get("instruments")))

                    if tags or environments or instruments:
                        # None leaves a column as it is: an empty list in the reply
                        # never blanks what the track already has.
                        db.update_track_tags(
                            track_id,
                            tags=tags or None,
                            environments=environments or None,
                            instruments=instruments or None,
                        )
                        count += 1
                if fatal:
                    raise fatal
                return count

            try:
                updated = await loop.run_in_executor(None, run_tag_regen)
            except FatalProviderError as e:
                from plexmix.ui.states.tagging_state import tagging_failure_cause

                cause, message = tagging_failure_cause(e)
                record_readiness(
                    "ai", "failing", cause, message=message, fingerprint=prep.fingerprint
                )
                raise FixFailed(ai_failure_copy(prep.settings, e)) from e

        if updated:
            record_readiness("ai", "ready", fingerprint=prep.fingerprint)
        return f"Tagged {pluralize(updated, 'track')}."

    @rx.event(background=True)
    async def analyze_missing_audio(self) -> None:
        await self._run_fix("audio_analysis", "Analyzing audio features…", self._audio_body)

    async def _audio_body(self, cancel_event: ThreadingEvent) -> str:
        from plexmix.config.settings import Settings
        from plexmix.database.sqlite_manager import SQLiteManager

        if importlib.util.find_spec("essentia") is None:
            raise FixFailed(_audio_unavailable_copy())
        try:
            from plexmix.audio.analyzer import EssentiaAnalyzer

            analyzer = EssentiaAnalyzer()
        except ImportError as e:
            raise FixFailed(_audio_unavailable_copy()) from e

        loop = asyncio.get_running_loop()
        settings = await loop.run_in_executor(None, Settings.load_from_file)
        db_path = settings.database.get_db_path()
        duration_limit = settings.audio.duration_limit
        if not db_path.exists():
            raise FixFailed(_not_synced_copy())

        num_workers = max(1, settings.audio.workers)

        with SQLiteManager(str(db_path)) as db:
            pending_tracks = await loop.run_in_executor(
                None, lambda: [t for t in db.get_tracks_without_audio_features() if t.file_path]
            )
            if not pending_tracks:
                return "All tracks already have audio features."

            total = len(pending_tracks)
            task_store.update(FIX_JOB, extra={"fix_target": "audio_analysis", "fix_total": total})

            analyzed = 0
            executor = ThreadPoolExecutor(max_workers=num_workers)
            track_iter = iter(pending_tracks)
            in_flight: dict[asyncio.Future, object] = {}

            def _submit() -> None:
                if cancel_event.is_set():
                    return
                t = next(track_iter, None)
                if t is None:
                    return

                def do_analyze(trk: Any = t) -> tuple[Any, Dict[str, Any]]:
                    resolved = settings.audio.resolve_path(trk.file_path)
                    features = analyzer.analyze(resolved, duration_limit=duration_limit)
                    return trk, features.to_dict()

                in_flight[loop.run_in_executor(executor, do_analyze)] = t

            for _ in range(min(num_workers, total)):
                _submit()

            try:
                while in_flight:
                    done, _ = await asyncio.wait(
                        in_flight.keys(), return_when=asyncio.FIRST_COMPLETED
                    )
                    for fut in done:
                        in_flight.pop(fut)
                        try:
                            track_obj, features_dict = fut.result()
                            db.insert_audio_features(track_obj.id, features_dict)
                            analyzed += 1
                        except Exception as exc:
                            logger.warning(
                                "Audio analysis failed for track %s: %s",
                                getattr(exc, "track_id", "?"),
                                exc,
                            )
                        _submit()

                    task_store.update(
                        FIX_JOB,
                        progress=analyzed,
                        message=f"Analyzing audio... {analyzed}/{total} ({num_workers} workers)",
                        extra={"fix_target": "audio_analysis", "fix_total": total},
                    )
            finally:
                executor.shutdown(wait=False)

        return f"Analyzed audio for {pluralize(analyzed, 'track')}."

    @rx.event(background=True)
    async def enrich_missing_musicbrainz(self) -> None:
        await self._run_fix(
            "musicbrainz", "Enriching tracks from MusicBrainz…", self._musicbrainz_body
        )

    async def _musicbrainz_body(self, cancel_event: ThreadingEvent) -> str:
        from plexmix.config.settings import Settings
        from plexmix.database.sqlite_manager import SQLiteManager
        from plexmix.services.musicbrainz_service import enrich_tracks, get_enrichable_tracks

        loop = asyncio.get_running_loop()
        settings = await loop.run_in_executor(None, Settings.load_from_file)
        db_path = settings.database.get_db_path()
        if not db_path.exists():
            raise FixFailed(_not_synced_copy())

        def run_enrichment() -> tuple[int, int, int]:
            with SQLiteManager(str(db_path)) as db:
                tracks = get_enrichable_tracks(db)
                if not tracks:
                    return 0, 0, 0

                total = len(tracks)
                task_store.update(FIX_JOB, extra={"fix_target": "musicbrainz", "fix_total": total})

                def on_progress(
                    enriched: int, cached: int, mb_errors: int, total_count: int
                ) -> None:
                    processed = enriched + cached + mb_errors
                    task_store.update(
                        FIX_JOB,
                        progress=processed,
                        message=f"MusicBrainz enrichment... {processed}/{total_count}",
                        extra={"fix_target": "musicbrainz", "fix_total": total_count},
                    )

                return enrich_tracks(
                    db,
                    settings.musicbrainz,
                    tracks,
                    progress_callback=on_progress,
                    cancel_event=cancel_event,
                )

        enriched, cached, errors = await loop.run_in_executor(None, run_enrichment)
        if not (enriched or cached or errors):
            return "All tracks already have MusicBrainz data."
        return (
            f"Enriched {pluralize(enriched, 'track')} from MusicBrainz "
            f"({format_count(cached)} from cache, {pluralize(errors, 'error')})."
        )

    async def _generate_embeddings_for_tracks(
        self,
        tracks_to_embed: List[Any],
        embedding_generator: Any,
        vector_index: Any,
        db: Any,
        index_path: Path,
        progress_label: str,
        cancel_event: Optional[ThreadingEvent] = None,
    ) -> tuple[int, Optional[IndexLeft]]:
        """Generate embeddings for tracks, then rebuild the search index from the rows.

        Returns the count of embeddings saved, and what kept the index from being
        rebuilt (None when it was rebuilt, and when there was no track to embed).
        The index goes through ``index_rebuild.rebuild_index``: it is built and
        saved only when it is complete, and otherwise the saved one stays as it is.
        """
        from plexmix.database.index_rebuild import rebuild_index
        from plexmix.database.models import Embedding
        from plexmix.utils.embeddings import create_track_text

        if not tracks_to_embed:
            return 0, None

        total = len(tracks_to_embed)
        batch_size = 50

        def run_embedding_generation() -> tuple[int, Optional[IndexLeft]]:
            embeddings_saved = 0

            for i in range(0, len(tracks_to_embed), batch_size):
                if cancel_event is not None and cancel_event.is_set():
                    break

                batch_tracks = tracks_to_embed[i : i + batch_size]

                track_data_list: List[Dict[str, Any]] = []
                for track in batch_tracks:
                    artist = db.get_artist_by_id(track.artist_id)
                    album = db.get_album_by_id(track.album_id)

                    track_data = {
                        "id": track.id,
                        "title": track.title,
                        "artist": artist.name if artist else "Unknown",
                        "album": album.title if album else "Unknown",
                        "genre": track.genre or "",
                        "year": track.year or "",
                        "tags": track.tags or "",
                    }
                    track_data_list.append(track_data)

                texts = [create_track_text(td) for td in track_data_list]
                embeddings = embedding_generator.generate_batch_embeddings(
                    texts, batch_size=batch_size
                )

                for track_data, embedding_vector in zip(track_data_list, embeddings):
                    embedding = Embedding(
                        track_id=track_data["id"],
                        embedding_model=embedding_generator.provider_name,
                        embedding_dim=embedding_generator.get_dimension(),
                        vector=embedding_vector,
                    )
                    db.insert_embedding(embedding)
                    embeddings_saved += 1

                    task_store.update(
                        FIX_JOB,
                        progress=embeddings_saved,
                        message=f"{progress_label} {embeddings_saved}/{total}",
                        extra={"fix_total": total},
                    )

            # From the rows of the active model, and only when that index is
            # complete. While a track holds only an embedding of another provider
            # or vector size, nothing is built and the saved index stays as it is.
            model = embedding_generator.provider_name
            rebuilt = rebuild_index(db, vector_index, model, index_path=str(index_path))
            return embeddings_saved, index_left(db, model, rebuilt.left_out)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, run_embedding_generation)

    # ── Progress polling ─────────────────────────────────────────────

    @rx.event
    def poll_task_progress(self) -> Any:
        """Client-initiated poll for Doctor fix progress.

        On completion or cancellation it re-scans (the counts changed); on
        failure it keeps the message, its body and its fix action in
        ``fix_errors[target]`` for the row, and still returns the error toast.
        """
        return self._poll_fix_progress()

    def _poll_fix_progress(self) -> Any:
        """Body of :meth:`poll_task_progress` (also run directly by ``on_load``)."""
        entry = task_store.get(FIX_JOB)
        if entry is None:
            return None

        if entry.status == "running":
            self._apply_running(entry)
            return None
        if not self.is_fixing:
            return None

        target = str(entry.extra.get("fix_target", "") or self.current_fix_target or "")
        self._clear_fix_progress()
        if entry.status == "completed":
            msg = str(entry.extra.get("result_msg", "") or "Fix completed.")
            return [rx.toast.success(msg), DoctorState.run_health_check]
        if entry.status == "failed":
            msg = str(entry.message or "") or FIX_FAILED_MESSAGE
            if target:
                errors = {k: dict(v) for k, v in self.fix_errors.items()}
                errors[target] = {
                    "title": msg,
                    "body": str(entry.extra.get("error_body", "") or ""),
                    "action_label": str(entry.extra.get("error_action_label", "") or ""),
                    "action_href": str(entry.extra.get("error_action_href", "") or ""),
                }
                self.fix_errors = errors
            return rx.toast.error(msg)
        if entry.status == "cancelled":
            # A fix that replaces data says what the cancel left behind.
            msg = str(entry.extra.get("cancel_msg", "") or "") or FIX_CANCELLED_MESSAGE
            return [rx.toast.warning(msg), DoctorState.run_health_check]
        return None

    # ── Labels ───────────────────────────────────────────────────────

    @rx.var(cache=True)
    def orphaned_embeddings_label(self) -> str:
        return pluralize(self.doctor_orphaned_embeddings, "orphaned embedding")

    @rx.var(cache=True)
    def missing_embeddings_label(self) -> str:
        return _need(self.doctor_tracks_needing_embeddings, "embeddings")

    @rx.var(cache=True)
    def fix_progress_label(self) -> str:
        """ "1,204 / 5,807" while the total is known; "" otherwise (never "0 / 0")."""
        if self.fix_total <= 0:
            return ""
        return f"{format_count(self.fix_progress)} / {format_count(self.fix_total)}"

    @rx.var(cache=True)
    def fix_title(self) -> str:
        """What the running fix does, for the progress callout."""
        return FIX_TITLES.get(self.current_fix_target, "") or "Running a fix"

    @rx.var(cache=True)
    def fix_percent(self) -> int:
        """Fix progress as a whole percentage (0-100) for the progress bar fill.

        ``fix_progress`` counts processed tracks out of ``fix_total``. Returns 0
        when the total is unknown (the bar then renders as indeterminate).
        """
        if self.fix_total <= 0:
            return 0
        return max(0, min(100, int(self.fix_progress * 100 / self.fix_total)))

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
        return _need(self.doctor_tracks_without_audio, "audio analysis")

    @rx.var(cache=True)
    def audio_job_running(self) -> bool:
        return self.current_fix_target == "audio_analysis"

    @rx.var(cache=True)
    def untagged_tracks_message(self) -> str:
        """Plain, pluralised tag coverage; never claims coverage before a scan."""
        if not self.stats_loaded:
            return "Checking…" if self.scan_state in ("idle", "checking") else ""
        untagged = self.doctor_untagged_tracks
        if untagged > 0:
            verb = "doesn't" if untagged == 1 else "don't"
            return f"{pluralize(untagged, 'track')} {verb} have AI-generated tags."
        total = self.doctor_total_tracks
        if total <= 0:
            return ""
        if total == 1:
            return "The 1 track has AI-generated tags."
        return f"All {format_count(total)} tracks have AI-generated tags."

    @rx.var(cache=True)
    def missing_musicbrainz_label(self) -> str:
        return _need(self.doctor_tracks_without_musicbrainz, "MusicBrainz enrichment")

    @rx.var(cache=True)
    def musicbrainz_job_running(self) -> bool:
        return self.current_fix_target == "musicbrainz"


_STAT_KEYS: tuple[str, ...] = (
    "total_tracks",
    "tracks_with_embeddings",
    "orphaned_count",
    "untagged_count",
    "tracks_needing_embeddings",
    "audio_features_count",
    "tracks_without_audio",
    "mb_enriched",
    "mb_without",
)
