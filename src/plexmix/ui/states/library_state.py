import reflex as rx
import asyncio
import hashlib
import logging
import re
import threading
import time
from typing import Any, AsyncGenerator, Iterable, Mapping, NamedTuple, Optional, Sequence, cast
from concurrent.futures import ThreadPoolExecutor
from plexmix.config.constants import EMBEDDING_BATCH_SIZE, LIBRARY_PAGE_SIZE
from plexmix.ui.states.app_state import (
    AppState,
    current_fingerprint_safe,
    provider_failure_cause,
    record_readiness,
)
from plexmix.ui.job_manager import task_store


from plexmix.ui.utils.errors import friendly_error
from plexmix.ui.utils.helpers import (
    format_count,
    format_eta as _format_eta,
    pluralize,
    safe_error_text,
    str_dict as _str_dict,
)
from plexmix.ui.utils.a11y import enrichment_flags, focus_by_id_script
from plexmix.utils.redaction import mask_urls_in_text

logger = logging.getLogger(__name__)

# Ids of the pager buttons (pages/library.py). Reaching the first or last page
# disables the button that was just activated, which would drop keyboard focus
# to <body>, so focus moves to the opposite pager button instead.
PREV_PAGE_BUTTON_ID = "lib-page-prev"
NEXT_PAGE_BUTTON_ID = "lib-page-next"
_SCROLL_TOP_JS = "window.scrollTo({top: 0, behavior: 'smooth'})"

# Element ids of the client-owned Library text fields (pages/library.py renders
# them with plexmix.ui.components.inputs). The browser owns their text; a handler
# that changes one of these values for any reason other than that field's own
# commit returns ``rx.set_value(<id>, <value>)``. The one exception is Clear: the
# browser empties the fields in the click itself (``data-clear-form``), so
# ``clear_filters`` returns none (see its docstring).
LIBRARY_SEARCH_ID = "lib-search"
LIBRARY_GENRE_ID = "lib-genre"
LIBRARY_YEAR_MIN_ID = "lib-year-min"
LIBRARY_YEAR_MAX_ID = "lib-year-max"
LIBRARY_TAG_ID = "lib-tag"
LIBRARY_BULK_TAGS_ID = "lib-bulk-tags"

# Every text filter of the filter bar: (form field name, state var, element id).
# ``apply_filter_form`` reads the names; Clear empties the fields with these ids.
LIBRARY_TEXT_FILTERS: tuple[tuple[str, str, str], ...] = (
    ("search", "search_query", LIBRARY_SEARCH_ID),
    ("genre", "genre_filter", LIBRARY_GENRE_ID),
    ("year_min", "year_min", LIBRARY_YEAR_MIN_ID),
    ("year_max", "year_max", LIBRARY_YEAR_MAX_ID),
    ("tag", "tag_filter", LIBRARY_TAG_ID),
)
LIBRARY_FILTER_IDS: tuple[str, ...] = tuple(field_id for _, _, field_id in LIBRARY_TEXT_FILTERS)

_YEAR = re.compile(r"[0-9]{4}")


def _year_or_blank(value: Any) -> str:
    """Return ``value`` when it is a four-digit year, else ``""`` (no year bound).

    A partly typed ("19") or invalid ("abcd", "1e3") year is not applied, so the
    list never empties or errors while the user is still typing.
    """
    text = str(value or "").strip()
    return text if _YEAR.fullmatch(text) else ""


def _filter_text(value: Any) -> str:
    """A committed filter value as text (``None`` from a missing form key is ``""``)."""
    return "" if value is None else str(value)


# ── Sync: Plex readiness (webui-honest-status-and-first-run 3.3) ──


def _connect_plex_for_sync(settings: Any) -> tuple[Any, str]:
    """``connect_plex`` plus the Plex fingerprint (blocking; run in an executor)."""
    from plexmix.services.sync_service import connect_plex

    client = connect_plex(settings)
    return client, current_fingerprint_safe("plex", settings)


def record_plex_connection_error(error: Any, settings: Any) -> str:
    """Record a sync's ``PlexConnectionError`` in readiness; return the cause value.

    ``not_configured`` and ``no_library`` are local facts readiness derives by
    itself, so only reachability and a missing library are recorded.
    """
    from plexmix.services.readiness import cause_for_plex_error_code

    code = str(getattr(error, "code", "") or "")
    cause = cause_for_plex_error_code(code).value
    if code not in ("not_configured", "no_library"):
        record_readiness(
            "plex", "failing", cause, fingerprint=current_fingerprint_safe("plex", settings)
        )
    return cause


def record_sync_tagging(sync_engine: Any, settings: Any) -> None:
    """Record what a sync's AI tagging proved about the AI provider (never raises).

    A provider error that stopped tagging (``SyncEngine.tagging_error``) marks AI
    failing with its cause; tracks tagged without one mark it ready. A sync that
    tagged nothing (no untagged tracks) proves nothing and records nothing.
    """
    try:
        from plexmix.ai.errors import FatalProviderError

        error = getattr(sync_engine, "tagging_error", None)
        if not isinstance(error, FatalProviderError):
            error = None
        saved = getattr(sync_engine, "tags_saved", 0)
        if not isinstance(saved, int) or isinstance(saved, bool):
            saved = 0
        if error is None and not saved:
            return
        fingerprint = current_fingerprint_safe("ai", settings)
        if error is not None:
            from plexmix.ui.states.tagging_state import tagging_failure_cause

            cause, message = tagging_failure_cause(error)
            record_readiness("ai", "failing", cause, message=message, fingerprint=fingerprint)
        else:
            record_readiness("ai", "ready", fingerprint=fingerprint)
    except Exception as e:  # readiness is best effort; never break the sync
        logger.warning("Could not record the sync's tagging outcome: %s", type(e).__name__)


def plex_error_title(code: Optional[str], settings: Any = None) -> str:
    """Plain, UI-language title for a ``PlexConnectionError.code`` (no CLI hints)."""
    from plexmix.services.readiness import cause_for_plex_error_code, describe

    context: dict[str, Any] = {"code": code or ""}
    if settings is not None:
        context.update(url=settings.plex.url or "", library=settings.plex.library_name or "")
    return describe("plex", cause_for_plex_error_code(code), context)["title"]


# ── Embeddings: whole-library "Build search index" (webui-library-selection) ──

ALL_EMBEDDED_MESSAGE = "All tracks already have embeddings."
NO_TRACKS_TO_EMBED_MESSAGE = (
    "No tracks synced yet. Sync your library first, then build the search index."
)
EMBED_ALL_RUNNING_MESSAGE = "Embeddings are already being generated."
EMBED_ALL_ERROR_MESSAGE = "Couldn't read your library. Details are in the server log."


class EmbedAllPreflight(NamedTuple):
    """What ``request_embed_all`` found before asking for confirmation.

    ``state``: ``ready`` (``missing`` tracks lack an embedding; ``scope`` holds the
    confirmation copy), ``all_embedded``, ``no_tracks``, ``running`` (an
    embedding job is already running), ``blocked`` (the embedding provider can't
    start; ``notice`` says why and how to fix it), ``mismatch`` (nothing is
    missing but the index doesn't match the provider: rebuild instead) or
    ``error``. ``ready`` with ``rebuild_index`` means every track has an
    embedding but the search index file is missing or empty: ``missing`` is then
    the number of stored embeddings the index is rebuilt from.

    ``action_label``/``action_href`` and ``secondary_label``/``secondary_href``
    are the notice's links (readiness copy: "Rebuild embeddings" → Doctor and
    "Switch back" → Settings for a mismatch; the provider's fix when blocked).
    """

    state: str
    missing: int = 0
    scope: dict[str, str] = {}
    notice: str = ""
    action_label: str = ""
    action_href: str = ""
    secondary_label: str = ""
    secondary_href: str = ""
    rebuild_index: bool = False


def embed_all_preflight() -> EmbedAllPreflight:
    """Count the tracks the search index is missing and build the confirmation copy.

    Blocking (settings, SQLite, ``find_spec``): run it in an executor. "Missing"
    means no embedding from the *current* embedding provider (embeddings are
    stored per provider id, and the search index is rebuilt from the current
    provider's vectors only, as sync does). With one provider all along this
    equals the Dashboard/Doctor "without embeddings" count; after a provider
    switch it also covers tracks embedded only by the old provider, so building
    the index makes search work again. When nothing is missing but the index
    still doesn't match the provider (same provider, other model size), the
    notice says so and links to the rebuild and to switching back instead of
    claiming all is well. When nothing is missing but the search index file is
    missing or empty, the confirmation offers to rebuild the index from the stored
    embeddings (local; nothing is generated or sent).
    """
    from plexmix.config.settings import Settings
    from plexmix.database.sqlite_manager import SQLiteManager
    from plexmix.services import readiness as rd
    from plexmix.services.providers import diagnose_embedding_provider
    from plexmix.services.registry import embedding_provider_display_name

    if task_store.is_running("embedding"):
        return EmbedAllPreflight("running", notice=EMBED_ALL_RUNNING_MESSAGE)
    settings = Settings.load_from_file()
    provider = (settings.embedding.default_provider or "").strip().lower()
    label = embedding_provider_display_name(provider)
    model = rd.embedding_model_for(settings)
    db_path = settings.database.get_db_path()
    total = missing = 0
    if db_path.exists():
        with SQLiteManager(str(db_path)) as db:
            cursor = db.get_connection().cursor()
            cursor.execute("SELECT COUNT(*) FROM tracks")
            row = cursor.fetchone()
            total = int(row[0]) if row else 0
            # EmbeddingGenerator stores its provider id (lower-case) as the model.
            missing = db.count_tracks_without_embeddings(provider)
    if total == 0:
        return EmbedAllPreflight("no_tracks", notice=NO_TRACKS_TO_EMBED_MESSAGE)
    rebuild_index = False
    if missing == 0:
        meta = rd.read_index_metadata(settings)
        index_dim = meta["dimension"]
        try:
            expected = int(settings.embedding.get_dimension_for_provider(provider))
        except Exception:
            expected = 0
        if index_dim and expected and index_dim != expected:
            copy = rd.describe(
                "index",
                rd.Cause.DIMENSION_MISMATCH,
                {
                    "label": label,
                    "provider": provider,
                    "model": model,
                    "index_dimension": index_dim,
                    "expected_dimension": expected,
                },
            )
            return EmbedAllPreflight(
                "mismatch",
                notice=f"{copy['title']}. {copy['body']}",
                action_label=copy["action_label"],
                action_href=copy["action_href"],
                secondary_label=copy["secondary_label"],
                secondary_href=copy["secondary_href"],
            )
        index_file = settings.database.get_index_path()
        if index_file.exists() and not (index_dim and not meta["vector_count"]):
            return EmbedAllPreflight("all_embedded", notice=ALL_EMBEDDED_MESSAGE)
        # Every track has an embedding, but the index file is missing or empty.
        rebuild_index = True

    failure = diagnose_embedding_provider(settings)
    if failure is not None:
        copy = rd.describe_failure("embeddings", failure, {"model": model})
        return EmbedAllPreflight(
            "blocked",
            notice=f"{copy['title']}. {copy['body']}",
            action_label=copy["action_label"],
            action_href=copy["action_href"],
        )
    if rebuild_index:
        embedded = total - missing
        scope = rd.describe_job_scope("index", embedded, label, model)
        scope.update(provider_label=label, model=model, count=format_count(embedded))
        return EmbedAllPreflight("ready", missing=embedded, scope=scope, rebuild_index=True)
    destination = rd.embedding_sends_to(settings)
    scope = rd.describe_job_scope(
        "embeddings", missing, label, model, bool(destination), {"sends_to": destination}
    )
    scope.update(provider_label=label, model=model, count=format_count(missing))
    return EmbedAllPreflight("ready", missing=missing, scope=scope)


def run_embedding_job(
    track_ids: Optional[list[int]], cancel_event: threading.Event
) -> tuple[str, str]:
    """Body of the ``"embedding"`` task (blocking; run in an executor).

    Embeds ``track_ids``, or (None) every track without an embedding from the
    current provider, then rebuilds the search index from that provider's vectors. Records
    the provider's real outcome in readiness. Returns ``(status, message)`` for
    ``task_store.complete``; messages are plain and secret-free.
    """
    from plexmix.config.settings import Settings
    from plexmix.database.sqlite_manager import SQLiteManager
    from plexmix.services import readiness as rd
    from plexmix.services.providers import ProviderFailure, build_embedding_generator_with_reason
    from plexmix.services.sync_service import build_vector_index
    from plexmix.services.tagging_service import (
        generate_embeddings_for_tracks,
        rebuild_vector_index,
    )

    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()
    if not db_path.exists():
        return "failed", rd.describe("library", rd.Cause.NOT_SYNCED)["title"]

    fp = current_fingerprint_safe("embeddings", settings)
    model = rd.embedding_model_for(settings)
    generator, failure = build_embedding_generator_with_reason(settings)
    if generator is None:
        provider = (settings.embedding.default_provider or "").strip().lower()
        failure = failure or ProviderFailure(rd.Cause.UNKNOWN, provider)
        detail = failure.detail if failure.cause == rd.Cause.UNKNOWN else ""
        record_readiness(
            "embeddings", "failing", failure.cause.value, message=detail, fingerprint=fp
        )
        return "failed", rd.describe_failure("embeddings", failure, {"model": model})["title"]

    with SQLiteManager(str(db_path)) as db:
        if track_ids is None:
            tracks = db.get_tracks_without_embeddings(generator.provider_name)
        else:
            found = (db.get_track_by_id(int(tid)) for tid in track_ids)
            tracks = [t for t in found if t is not None]
        if not tracks and track_ids is not None:
            return "completed", ""
        # The whole-library run with nothing to embed still rebuilds the search
        # index below: that is how a missing or empty index file is recreated
        # from the stored embeddings ("Build search index", rebuild_index).

        def on_progress(generated: int, total: int) -> None:
            if cancel_event.is_set():
                return
            task_store.update(
                "embedding",
                progress=int((generated / total) * 100) if total else 0,
                message=f"Generated {generated}/{total} embeddings",
            )

        try:
            generated = (
                generate_embeddings_for_tracks(
                    db,
                    generator,
                    tracks,
                    batch_size=EMBEDDING_BATCH_SIZE,
                    progress_callback=on_progress,
                    cancel_event=cancel_event,
                )
                if tracks
                else 0
            )
        except Exception as e:
            key = rd.resolve_secret("embeddings", settings) or ""
            cause, message = provider_failure_cause(e, key=key)
            record_readiness("embeddings", "failing", cause, message=message, fingerprint=fp)
            return "failed", friendly_error(e, [key])
        if generated:
            record_readiness("embeddings", "ready", fingerprint=fp)

        # Persist to the FAISS index too — writing only the DB rows left the index
        # stale, so newly embedded tracks never appeared in search.
        if not cancel_event.is_set():
            task_store.update("embedding", message="Updating search index...")
            vector_index = build_vector_index(settings, generator)
            indexed = rebuild_vector_index(
                db,
                vector_index,
                str(settings.database.get_index_path()),
                generator.provider_name,
            )
            if not tracks:
                return "completed", (
                    f"Rebuilt the search index from {pluralize(indexed, 'stored embedding')}."
                    if indexed
                    else ALL_EMBEDDED_MESSAGE
                )
    return ("cancelled" if cancel_event.is_set() else "completed"), ""


# ── Bulk tags: add (default) or replace (webui-library-selection) ──

BULK_TAG_ADD = "add"
BULK_TAG_REPLACE = "replace"
BULK_TAG_MODES: tuple[str, ...] = (BULK_TAG_ADD, BULK_TAG_REPLACE)
BULK_TAG_EMPTY_MESSAGE = "Enter at least one tag."
BULK_TAG_COUNTING_MESSAGE = "Still checking which tracks already have tags. Try again in a moment."
BULK_TAG_ERROR_MESSAGE = "Couldn't apply the tags. Details are in the server log."
TRACKS_LOAD_ERROR_MESSAGE = "Couldn't load your tracks. Details are in the server log."
SYNC_FAILED_TITLE = "Sync failed"


def bulk_tag_mode(value: Any) -> str:
    """A submitted Add/Replace choice; anything unknown is the safe ``"add"``."""
    text = str(value or "").strip().lower()
    return text if text in BULK_TAG_MODES else BULK_TAG_ADD


def parse_tag_text(text: Optional[str]) -> list[str]:
    """Tags from comma-separated text, parsed tolerantly by ``Track.get_tags_list``.

    Spaces around commas and empty items are ignored, so "chill,piano",
    "chill, piano" and " chill ,, piano" all give ``["chill", "piano"]``.
    """
    if not text:
        return []
    from plexmix.database.models import Track

    return Track.model_construct(tags=str(text)).get_tags_list()


def merge_tags(existing: Sequence[str], new: Sequence[str]) -> list[str]:
    """Existing tags, then the new ones, each once (case-insensitive), in order.

    The first spelling wins: "chill,piano" + "Piano, focus" gives
    ``["chill", "piano", "focus"]``.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for tag in [*existing, *new]:
        text = tag.strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        merged.append(text)
    return merged


def track_ids_of(values: Iterable[Any]) -> list[int]:
    """Selected track ids (strings in state) as ints, in order; non-numeric ids dropped."""
    ids: list[int] = []
    for value in values:
        text = str(value).strip()
        if text.isdigit():
            ids.append(int(text))
    return ids


def count_tagged_tracks(track_ids: list[int]) -> int:
    """How many of ``track_ids`` already have tags (blocking; run it in an executor)."""
    from plexmix.config.settings import Settings
    from plexmix.database.sqlite_manager import SQLiteManager

    if not track_ids:
        return 0
    db_path = Settings.load_from_file().database.get_db_path()
    if not db_path.exists():
        return 0
    with SQLiteManager(str(db_path)) as db:
        stored = db.get_tags_for_tracks(track_ids)
    return sum(1 for columns in stored.values() if parse_tag_text(columns.get("tags")))


def apply_tags_to_tracks(track_ids: list[int], tags_text: str, mode: str) -> tuple[int, int]:
    """Write bulk tags (blocking; run it in an executor). Returns ``(updated, unchanged)``.

    ``"add"`` merges the entered tags into each track's existing tags with
    :func:`merge_tags` (case-insensitive, order kept, no duplicates) and writes
    only tracks whose tags change. ``"replace"`` overwrites each track's tags
    with the entered text, as bulk Tag always did. Only the ``tags`` column is
    written; environments and instruments are never touched. Ids no longer in
    the database are skipped.
    """
    from plexmix.config.settings import Settings
    from plexmix.database.sqlite_manager import SQLiteManager

    new_tags = parse_tag_text(tags_text)
    if not new_tags or not track_ids:
        return 0, 0
    db_path = Settings.load_from_file().database.get_db_path()
    if not db_path.exists():
        return 0, 0
    updated = unchanged = 0
    with SQLiteManager(str(db_path)) as db:
        stored = db.get_tags_for_tracks(track_ids)
        for track_id in dict.fromkeys(track_ids):
            if track_id not in stored:
                continue
            if mode == BULK_TAG_REPLACE:
                db.update_track_tags(track_id, tags=tags_text.strip())
                updated += 1
                continue
            existing = parse_tag_text(stored[track_id].get("tags"))
            merged = merge_tags(existing, new_tags)
            if merged == existing:
                unchanged += 1
                continue
            # Comma-joined without spaces, as sync and the Tagging page store tags.
            db.update_track_tags(track_id, tags=",".join(merged))
            updated += 1
    return updated, unchanged


def bulk_tag_result_message(mode: str, updated: int, unchanged: int) -> str:
    """The toast after a bulk Tag ("" when no selected track was found)."""
    if mode == BULK_TAG_REPLACE:
        return f"Replaced the tags on {pluralize(updated, 'track')}." if updated else ""
    if updated and unchanged:
        return (
            f"Added tags to {pluralize(updated, 'track')} · "
            f"{pluralize(unchanged, 'track')} already had them."
        )
    if updated:
        return f"Added tags to {pluralize(updated, 'track')}."
    if unchanged:
        return "The selected tracks already have these tags."
    return ""


def offpage_phrase(selected: int, offpage: int) -> str:
    """How many of ``selected`` tracks are on other pages, as a clause ("" when none).

    "all on other pages", "on another page" (one track, off the page), "3 of
    them on other pages", "1 of them on another page".
    """
    if offpage <= 0 or selected <= 0:
        return ""
    if offpage >= selected:
        return "all on other pages" if selected > 1 else "on another page"
    if offpage == 1:
        return "1 of them on another page"
    return f"{format_count(offpage)} of them on other pages"


def sync_tagging_note(readiness: Mapping[str, Mapping[str, str]]) -> str:
    """The Library sync controls' AI-tagging disclosure ("" without a usable AI provider).

    Sync tags new untagged tracks with the configured AI provider; for a cloud
    provider their metadata leaves the machine. Same wording as the Dashboard
    setup checklist's sync step (``readiness.setup_steps``).
    """
    from plexmix.services.readiness import data_destination

    ai = readiness.get("ai") or {}
    level = str(ai.get("level") or "")
    if not level or level == "not_set_up" or ai.get("blocking") == "true":
        return ""
    label = str(ai.get("label") or "").strip() or "your AI provider"
    note = f"Sync also tags new tracks with {label}"
    destination = data_destination(ai)
    if destination:
        note += f" · track metadata is sent to {destination}"
    return note


def sync_error_copy(code: Optional[str], message: str, settings: Any = None) -> dict[str, str]:
    """Inline copy for a failed sync: ``{title, body, action_label, action_href}``.

    A ``PlexConnectionError.code`` maps to ``readiness.describe("plex", …)`` (plain
    copy that links to the Plex settings, never a terminal command). Any other
    failure keeps the task's (secret-free) message as the body.
    """
    from plexmix.services.readiness import cause_for_plex_error_code, describe
    from plexmix.services.sync_service import PLEX_ERROR_CODES

    if code and code in PLEX_ERROR_CODES:
        context: dict[str, Any] = {"code": code}
        if settings is not None:
            context.update(url=settings.plex.url or "", library=settings.plex.library_name or "")
        copy = describe("plex", cause_for_plex_error_code(code), context)
        return {
            "title": copy["title"],
            "body": copy["body"],
            "action_label": copy["action_label"],
            "action_href": copy["action_href"],
        }
    return {
        "title": SYNC_FAILED_TITLE,
        "body": (message or "").strip() or "Details are in the server log.",
        "action_label": "",
        "action_href": "",
    }


def redact_secrets(text: str, secrets: Iterable[Optional[str]]) -> str:
    """``text`` with every non-empty secret replaced by ``[redacted]`` (for logs)."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _sync_secrets(settings: Any) -> list[str]:
    """The Plex token and AI key a sync uses, so failure text can be redacted."""
    from plexmix.services.readiness import resolve_secret

    secrets: list[str] = []
    if settings is None:
        return secrets
    for component in ("plex", "ai"):
        try:
            secret = resolve_secret(component, settings)
        except Exception:
            secret = None
        if secret:
            secrets.append(secret)
    return secrets


# ── Sync: what the engine kept or held, and the confirmed removal ──
#
# A sync removes a track only when Plex's listing leaves it out, and a removal
# above the engine's threshold is held for confirmation (``SyncEngine.outcome``).
# ``start_sync`` copies the outcome into the "sync" task's extra data. The page
# derives its notice from that task entry, so the notice survives a reload and
# shows in every tab until the next sync or a server restart. The task store drops
# a finished entry after an hour, so the hold is also remembered in this module
# (``remember_held_removal``), together with the rating keys of the held tracks.

# ``SyncOutcome.reason`` values the page reacts to. Literals, so the 2-second poll
# never imports the sync engine (and FAISS with it); a test keeps them equal to
# ``plexmix.plex.sync.REASON_THRESHOLD`` and ``REASON_EMPTY_LISTING``.
REASON_HELD = "threshold"
REASON_EMPTY_LISTING = "empty_listing"
# ``RemovalResult.status`` of a confirmed removal that found the tracks listed again,
# and of one that removed tracks.
REMOVAL_LISTED_AGAIN = "listed_again"
REMOVAL_REMOVED = "removed"

# Keys of the "sync" task's extra data.
REMOVAL_REASON_KEY = "removal_reason"  # SyncOutcome.reason ("" when nothing was skipped)
REMOVAL_HELD_KEY = "removal_held"  # tracks kept because the removal needs confirmation
REMOVAL_TOTAL_KEY = "removal_total"  # tracks in the library before that sync
UNREAD_TRACKS_KEY = "unread_tracks"  # listed tracks whose details could not be read
SYNC_PARTIAL_KEY = "sync_partial"  # True when anything was unread, skipped or held
REMOVAL_RUN_KEY = "removal_run"  # True when the task is a confirmed removal, not a sync
REMOVAL_RESULT_KEY = "removal_result"  # RemovalResult.status of that removal

NOTHING_REMOVED = "Nothing was removed."
REMOVAL_CHECKING_MESSAGE = "Checking Plex again before removing tracks..."
REMOVAL_ERROR_MESSAGE = "Couldn't remove the tracks. Details are in the server log."
REMOVAL_SYNC_RUNNING_MESSAGE = f"A sync is running. {NOTHING_REMOVED}"
REMOVAL_NOT_HELD_MESSAGE = f"No removal is waiting any more. {NOTHING_REMOVED}"
REMOVAL_CANCELLED_MESSAGE = f"Removal cancelled. {NOTHING_REMOVED}"
REMOVAL_SETTINGS_CHANGED_MESSAGE = (
    "The library settings changed after that sync, so its removal no longer applies. "
    f"{NOTHING_REMOVED} Run a sync to compare the library with Plex again."
)
# Added to the failure of a "Regenerate" sync that Plex stopped: the engine's own
# sentence ("... so nothing was changed.") is about the library sync, and that mode
# clears the tags and embeddings before it reads Plex.
REGENERATE_CLEARED_MESSAGE = (
    "The existing tags and embeddings had already been cleared. "
    "Run a sync to rebuild the tags, then generate the embeddings again."
)


def _whole(value: Any) -> int:
    """``value`` when it is a whole number of 0 or more, else 0 (a bool is not a count)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return int(value)


def held_removal_line(held: int, total: int) -> str:
    """The notice's headline: "Plex no longer lists 5,443 of 5,943 tracks"."""
    return f"Plex no longer lists {format_count(held)} of {format_count(total)} tracks"


def sync_outcome_extra(outcome: Any) -> dict[str, Any]:
    """A sync's outcome as extra data for its task; ``{}`` unless it is a real ``SyncOutcome``.

    A stand-in engine (the tests pass a mock) answers every attribute, so an
    ``outcome`` that is not the engine's own dataclass never reaches the page.
    """
    from plexmix.plex.sync import SyncOutcome

    if not isinstance(outcome, SyncOutcome):
        return {}
    return {
        REMOVAL_REASON_KEY: outcome.reason if isinstance(outcome.reason, str) else "",
        REMOVAL_HELD_KEY: _whole(outcome.held),
        REMOVAL_TOTAL_KEY: _whole(outcome.library_total),
        UNREAD_TRACKS_KEY: _whole(outcome.unread_tracks),
        # Also true for a run whose only unread items are artists or albums.
        SYNC_PARTIAL_KEY: outcome.is_partial is True,
    }


def sync_result_message(outcome: Any, tagging_skipped: bool = False) -> str:
    """The result of a finished sync, for its toast ("" keeps the last progress text).

    A run that kept unread tracks says how many. A run that removed nothing says
    why. The copy is plain UI text: the engine's own sentence for an empty listing
    names a terminal command, so that case is worded here.
    """
    from plexmix.plex.client import READ_KINDS
    from plexmix.plex.sync import SyncOutcome

    done = "Sync completed (tagging skipped)" if tagging_skipped else ""
    if not isinstance(outcome, SyncOutcome) or not outcome.is_partial:
        return done
    reason = outcome.reason if isinstance(outcome.reason, str) else ""
    if reason and reason not in (REASON_HELD, REASON_EMPTY_LISTING):
        # Too many failed reads (with the counts), or a listing that was not
        # available: the engine's sentences are plain and end with "Nothing was removed."
        return outcome.describe() or f"Plex could not be read completely. {NOTHING_REMOVED}"
    if reason == REASON_HELD:
        lead = f"{held_removal_line(outcome.held, outcome.library_total)}. They were kept."
    elif reason == REASON_EMPTY_LISTING:
        lead = (
            f"Plex listed 0 tracks, but PlexMix holds "
            f"{pluralize(outcome.library_total, 'track')}. {NOTHING_REMOVED}"
        )
    else:
        lead = f"{done or 'Sync completed'}."
    unread = [outcome.unread_sentence(kind) for kind in READ_KINDS]
    return " ".join([lead, *(sentence for sentence in unread if sentence)])


def sync_result_tone(extra: Mapping[str, Any]) -> str:
    """Toast tone for a completed "sync" task: ``warning``, ``info`` or ``success``.

    A sync that removed nothing for a reason (a held removal, an empty listing,
    too many failed reads) is a warning, never a success.
    """
    if str(extra.get(REMOVAL_REASON_KEY) or ""):
        return "warning"
    if extra.get(REMOVAL_RESULT_KEY) == REMOVAL_LISTED_AGAIN:
        return "info"
    return "success"


def result_toast(tone: str, message: str) -> Any:
    """``rx.toast`` in the given tone (``success``, ``info``, ``warning`` or ``error``)."""
    if tone == "success":
        return rx.toast.success(message)
    if tone == "info":
        return rx.toast.info(message)
    if tone == "warning":
        return rx.toast.warning(message)
    return rx.toast.error(message)


def held_removal(entry: Any) -> tuple[int, int]:
    """``(held, total)`` of the removal a finished "sync" task holds, else ``(0, 0)``.

    Only a removal held for confirmation counts: an empty listing or too many
    failed reads offer no removal. A running task holds nothing yet, and a new
    sync replaces the entry, so the notice ends with the next sync.
    """
    if entry is None or getattr(entry, "status", "") == "running":
        return 0, 0
    extra = getattr(entry, "extra", None)
    if not isinstance(extra, Mapping) or extra.get(REMOVAL_REASON_KEY) != REASON_HELD:
        return 0, 0
    held = _whole(extra.get(REMOVAL_HELD_KEY))
    if not held:
        return 0, 0
    return held, max(held, _whole(extra.get(REMOVAL_TOTAL_KEY)))


def held_removal_extra(held: int, total: int) -> dict[str, Any]:
    """Extra data that keeps ``held`` of ``total`` tracks waiting (0 clears the hold)."""
    return {
        REMOVAL_REASON_KEY: REASON_HELD if held else "",
        REMOVAL_HELD_KEY: held,
        REMOVAL_TOTAL_KEY: total if held else 0,
    }


class HeldRemoval(NamedTuple):
    """The removal that waits for confirmation: ``held`` of ``total`` tracks.

    ``keys`` are the rating keys of the held tracks, or None when they are not known.
    ``scope`` says which library the tracks were counted in (:func:`hold_scope`),
    or is "" when that is not known.
    """

    held: int
    total: int
    keys: Optional[frozenset[str]] = None
    scope: str = ""


def hold_scope(settings: Any) -> str:
    """What a held removal belongs to: the database, the Plex server and the library.

    A removal is confirmed for the tracks one sync found missing. When the settings
    name another database or another Plex library afterwards, the same confirmation
    would remove tracks from a library nobody was asked about. The value is a
    digest, so the Plex address is not kept a second time. "" when it cannot be told.
    """
    try:
        parts = (
            str(settings.database.get_db_path().expanduser().resolve()),
            str(settings.plex.url or "").strip(),
            str(settings.plex.library_name or "").strip(),
        )
    except Exception:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# The hold of the last sync, kept in this process until the next sync starts, the
# removal is settled or the server restarts. The "sync" task entry carries the same
# two numbers, but the task store drops a finished entry after an hour.
_remembered_hold: Optional[HeldRemoval] = None


def remember_held_removal(held: int, total: int, keys: Any = None, scope: str = "") -> None:
    """Remember that ``held`` of ``total`` tracks wait for confirmation (0 forgets).

    ``scope`` is the library they were counted in (:func:`hold_scope`).
    """
    global _remembered_hold
    if held <= 0:
        _remembered_hold = None
        return
    known = frozenset(keys) if isinstance(keys, (frozenset, set)) and keys else None
    _remembered_hold = HeldRemoval(held, max(held, total), known, scope or "")


def forget_held_removal() -> None:
    """Forget the remembered hold: a new sync started, or nothing waits any more."""
    remember_held_removal(0, 0)


def waiting_removal(entry: Any) -> tuple[int, int]:
    """``(held, total)`` of the removal that waits for confirmation, else ``(0, 0)``.

    While the "sync" task has an entry, the entry says so (:func:`held_removal`).
    Once the task store has dropped the finished entry, the remembered hold does.
    """
    if entry is not None:
        return held_removal(entry)
    hold = _remembered_hold
    return (hold.held, hold.total) if hold is not None else (0, 0)


def waiting_removal_keys(held: int, total: int) -> Optional[frozenset[str]]:
    """The rating keys of the tracks that wait, when they are known for this hold."""
    hold = _remembered_hold
    if hold is None or (hold.held, hold.total) != (held, total):
        return None
    return hold.keys


def waiting_removal_scope(held: int, total: int) -> str:
    """The library this hold was counted in ("" when it is not known for this hold)."""
    hold = _remembered_hold
    if hold is None or (hold.held, hold.total) != (held, total):
        return ""
    return hold.scope


def hold_applies_to(settings: Any, held: int, total: int) -> bool:
    """False when the hold was counted in another library than ``settings`` name now.

    True when either side is not known: the engine's own checks (Plex is listed
    again, only held tracks, never more than the count) still apply then.
    """
    remembered = waiting_removal_scope(held, total)
    current = hold_scope(settings)
    return not remembered or not current or remembered == current


def record_plex_read_interrupted(settings: Any) -> str:
    """Record that Plex stopped answering during a sync; return the cause value.

    The same record as a connection that failed (``record_plex_connection_error``
    with ``unreachable``): the server was reached and then stopped answering, or
    could not list the library.
    """
    from plexmix.services.readiness import Cause

    cause = Cause.UNREACHABLE.value
    record_readiness(
        "plex", "failing", cause, fingerprint=current_fingerprint_safe("plex", settings)
    )
    return cause


class RemovalReport(NamedTuple):
    """How a confirmed removal ended.

    ``status`` completes the "sync" task and ``tone`` picks the toast for
    ``message``. ``held`` of ``total`` tracks still wait for confirmation
    afterwards (0 clears the notice), and ``keys`` are their rating keys when
    known. ``result`` is the engine's ``RemovalResult.status`` ("" when the engine
    was not reached).
    """

    status: str
    tone: str
    message: str
    held: int = 0
    total: int = 0
    result: str = ""
    keys: Optional[frozenset[str]] = None


def _backup_note(result: Any) -> str:
    """Where the backup of a removal is and how large it is ("" when none was made)."""
    if result.backup_folder is None:
        return ""
    from plexmix.cli.console_helpers import format_size

    size = format_size(_whole(result.backup_size_bytes))
    return f" A backup was saved to {result.backup_folder} ({size})."


def removal_report(
    result: Any, held: int, total: int, keys: Optional[frozenset[str]] = None
) -> RemovalReport:
    """Turn the engine's ``RemovalResult`` into what the page reports.

    ``held`` of ``total`` tracks were waiting, with the rating keys ``keys``. Removed
    and "Plex lists the tracks again" clear the hold, more missing tracks replace it
    with the new count, and a failed backup, listing or delete and a cancelled removal
    keep it. Anything that is not a real ``RemovalResult`` is a failure that keeps the hold.
    """
    from plexmix.plex import sync as engine

    failed = RemovalReport("failed", "error", REMOVAL_ERROR_MESSAGE, held, total, keys=keys)
    if not isinstance(result, engine.RemovalResult):
        return failed
    status = result.status if isinstance(result.status, str) else ""
    message = str(result.message or "").strip()
    if status == engine.REMOVAL_REMOVED:
        text = message or f"Removed {pluralize(_whole(result.removed), 'track')}."
        return RemovalReport("completed", "success", text + _backup_note(result), result=status)
    if status == engine.REMOVAL_CANCELLED:
        # Cancelled while Plex was listed or the backup was written: nothing was
        # removed. A backup that was already made stays, so say where it is.
        text = REMOVAL_CANCELLED_MESSAGE + _backup_note(result)
        return failed._replace(status="cancelled", tone="warning", message=text, result=status)
    if status == engine.REMOVAL_LISTED_AGAIN:
        text = message or f"Plex lists those tracks again. {NOTHING_REMOVED}"
        return RemovalReport("completed", "info", text, result=status)
    if status == engine.REMOVAL_MORE_MISSING:
        missing = _whole(result.missing) or held
        text = message or f"More tracks are missing from Plex now. {NOTHING_REMOVED}"
        # The new count replaces the hold, with the keys of the tracks it counts.
        missing_keys = result.missing_keys
        known = isinstance(missing_keys, (frozenset, set)) and len(missing_keys) == missing
        return RemovalReport(
            "completed",
            "warning",
            text,
            missing,
            max(missing, _whole(result.library_total) or total),
            status,
            frozenset(missing_keys) if known else None,
        )
    if status == engine.REMOVAL_BACKUP_FAILED:
        text = message or f"The backup could not be made. {NOTHING_REMOVED}"
        return failed._replace(message=text, result=status)
    if status == engine.REMOVAL_LISTING_FAILED:
        text = message or f"Plex could not list the library's tracks. {NOTHING_REMOVED}"
        return failed._replace(message=text, result=status)
    if status == engine.REMOVAL_FAILED:
        # The tracks could not be deleted, after the backup was made: say where it is.
        text = (message or REMOVAL_ERROR_MESSAGE) + _backup_note(result)
        return failed._replace(message=text, result=status)
    return failed


def _saved_index_for_removal(settings: Any) -> dict[str, Any]:
    """Where the saved search index is, as arguments for ``SyncEngine``.

    The engine copies the index files into the backup first and opens the index
    only after that, to drop the removed tracks' vectors: search then never
    returns a track that is gone. An index that cannot be loaded does not survive
    being opened, so it must not be opened before the backup.
    """
    try:
        provider = (settings.embedding.default_provider or "").strip().lower()
        return {
            "saved_index_path": settings.database.get_index_path(),
            "saved_index_dimension": int(settings.embedding.get_dimension_for_provider(provider)),
        }
    except Exception as e:
        logger.warning("Could not find the search index before a removal: %s", safe_error_text(e))
        return {}


def run_confirmed_removal(
    plex_client: Any,
    settings: Any,
    confirmed_count: int,
    held_keys: Optional[frozenset[str]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Any:
    """Body of a confirmed removal (blocking; run it in an executor).

    ``SyncEngine.remove_unlisted_tracks`` lists Plex again, makes the backup and
    removes only tracks that are still not listed: never more than
    ``confirmed_count``, and with ``held_keys`` only the tracks that were held.
    It removes nothing once ``cancel_event`` is set. Returns the engine's
    ``RemovalResult``.
    """
    from plexmix.database.sqlite_manager import SQLiteManager
    from plexmix.plex.sync import SyncEngine

    db_path = settings.database.get_db_path()
    if not db_path.exists():
        # SQLiteManager would create an empty database here.
        raise FileNotFoundError("the library database does not exist")
    with SQLiteManager(str(db_path)) as db:
        engine = SyncEngine(plex_client, db, **_saved_index_for_removal(settings))
        return engine.remove_unlisted_tracks(
            confirmed_count, held_keys=held_keys, cancel_event=cancel_event
        )


class LibraryState(AppState):
    tracks: list[dict[str, str]] = []
    total_filtered_tracks: int = 0
    current_page: int = 1
    page_size: int = LIBRARY_PAGE_SIZE
    search_query: str = ""
    genre_filter: str = ""
    year_min: str = ""
    year_max: str = ""
    tag_filter: str = ""
    has_audio: bool = False

    is_syncing: bool = False
    sync_progress: int = 0
    sync_message: str = ""
    sync_mode: str = "incremental"
    show_regenerate_confirm: bool = False

    is_embedding: bool = False
    embedding_progress: int = 0
    embedding_message: str = ""

    is_analyzing_audio: bool = False
    audio_analysis_paused: bool = False
    audio_analysis_progress: int = 0
    audio_analysis_message: str = ""
    audio_analysis_eta: str = ""

    is_enriching_musicbrainz: bool = False
    musicbrainz_progress: int = 0
    musicbrainz_message: str = ""

    selected_tracks: list[str] = []
    sort_column: str = "title"
    sort_ascending: bool = True

    # Bulk operations
    show_bulk_tag_dialog: bool = False
    bulk_tag_input: str = ""
    show_delete_confirm: bool = False
    # Tag dialog: Add (default) or Replace, and how many selected tracks already
    # have tags (counted when the dialog opens; unknown while counting or failed).
    bulk_tag_mode: str = BULK_TAG_ADD
    selected_with_tags_count: int = 0
    bulk_tag_counting: bool = False
    bulk_tag_count_failed: bool = False
    bulk_tag_error: str = ""
    _bulk_tag_count_gen: int = 0

    # Table area: a placeholder until the first query completes (tracks_loaded),
    # then the rows, "No tracks synced yet" or "No tracks match".
    tracks_loaded: bool = False
    tracks_error: str = ""
    library_has_tracks: bool = False

    # Inline sync failure next to the action bar (the toast stays as well).
    # sync_error is the headline ("" = no failure shown).
    sync_error: str = ""
    sync_error_body: str = ""
    sync_error_action_label: str = ""
    sync_error_action_href: str = ""

    # Held removal: tracks Plex no longer lists, kept by the last sync because a
    # removal that large needs confirmation. Both numbers are derived from the
    # "sync" task entry on every poll and on load (0 = no notice), so the notice
    # survives a reload and shows in every tab. ``show_removal_confirm`` is the
    # open state of the removal confirmation.
    removal_notice_count: int = 0
    removal_notice_total: int = 0
    show_removal_confirm: bool = False

    # Whole-library embedding confirmation (request_embed_all → confirm_embed_all).
    # ``embed_all_scope`` holds readiness.describe_job_scope("embeddings", …) keys
    # (title, count_line, replace_line, provider_line, privacy_line, duration_line,
    # confirm_label, secondary_label, danger) plus provider_label, model and count.
    embed_all_open: bool = False
    embed_all_scope: dict[str, str] = {}
    embed_all_count: int = 0
    embed_all_checking: bool = False
    embed_all_notice: str = ""
    # Callout tone of embed_all_notice: info (all embedded, nothing synced,
    # already running), warning (index mismatch) or error (provider blocked).
    embed_all_notice_tone: str = "info"
    # The notice's links ("" when none): a mismatch offers "Rebuild embeddings"
    # (Doctor) and "Switch back" (Settings); a blocked provider offers its fix.
    embed_all_notice_action_label: str = ""
    embed_all_notice_action_href: str = ""
    embed_all_notice_secondary_label: str = ""
    embed_all_notice_secondary_href: str = ""

    def set_sort(self, column: str):
        """Toggle sort direction if same column, otherwise sort ascending by new column."""
        if self.sort_column == column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column
            self.sort_ascending = True
        self.current_page = 1
        self.load_tracks()
        return rx.call_script("window.scrollTo({top: 0, behavior: 'smooth'})")

    def set_sync_mode(self, mode: str):
        self.sync_mode = mode

    def start_sync_or_confirm(self):
        """Sync button entry point: the destructive 'regenerate' mode opens a
        confirmation first; other modes start immediately. Previously the button
        always called start_sync, so the regenerate mode/confirm was unreachable."""
        if self.sync_mode == "regenerate":
            self.show_regenerate_confirm = True
            return None
        return LibraryState.start_sync

    def confirm_regenerate_sync(self):
        self.show_regenerate_confirm = True

    def cancel_regenerate_confirm(self):
        self.show_regenerate_confirm = False

    def set_regenerate_confirm_open(self, is_open: bool) -> None:
        """Dialog on_open_change: close on False (idempotent); True is a no-op.

        The dialog opens only through state, and the confirm handler closes it by
        state too, so a repeated False (Escape, Cancel, confirm) is harmless.
        """
        if not is_open:
            self.show_regenerate_confirm = False

    def on_load(self):
        if not self.check_auth():
            self.is_page_loading = False
            return
        self.load_app_data()
        # The "has audio analysis" checkbox is hidden while no track is analyzed;
        # a filter left on from before would list nothing, so drop it.
        if self.has_audio and self.audio_analyzed_tracks == "0":
            self.has_audio = False
        self.load_tracks()
        self.poll_task_progress()  # Recover in-progress tasks from TaskStore
        self.is_page_loading = False

    def poll_task_progress(self):
        """Client-initiated poll: read TaskStore -> update state vars.

        Called by a hidden button clicked via JavaScript setInterval.
        Because this is a regular (non-background) handler, the response
        travels over the active WebSocket -- no risk of pushing to a dead connection.
        """
        # -- Sync --
        sync_entry = task_store.get("sync")
        # The held-removal notice follows the task entry on every poll and on
        # load, whether or not this tab started the sync.
        self._refresh_removal_notice(sync_entry)
        if sync_entry:
            if sync_entry.status == "running":
                self.is_syncing = True
                self.sync_progress = sync_entry.progress
                self.sync_message = sync_entry.message
            elif self.is_syncing and sync_entry.status == "completed":
                self.is_syncing = False
                self.sync_progress = 100
                self.sync_message = ""
                self._clear_sync_error()
                self.load_tracks()
                self.check_configuration_status()
                self.load_library_stats()
                if sync_entry.extra.get(REMOVAL_RESULT_KEY) == REMOVAL_REMOVED:
                    # A removal confirmed in another tab removed tracks: this tab's
                    # selection could still name some of them.
                    self.selected_tracks = []
                msg = sync_entry.message or "Sync completed!"
                # A warning, not a success, when the sync held removals, met an
                # empty listing or skipped removals after too many failed reads.
                return result_toast(sync_result_tone(sync_entry.extra), msg)
            elif self.is_syncing and sync_entry.status == "failed":
                self.is_syncing = False
                self.sync_message = ""
                if sync_entry.extra.get(REMOVAL_RUN_KEY):
                    # A confirmed removal that another tab started: nothing was
                    # removed and the notice stays. It is not a failed sync.
                    return rx.toast.error(sync_entry.message or REMOVAL_ERROR_MESSAGE)
                self._show_sync_error(sync_entry)
                msg = sync_entry.message or "Sync failed"
                return rx.toast.error(f"Sync failed: {msg}")
            elif self.is_syncing and sync_entry.status == "cancelled":
                self.is_syncing = False
                self.sync_message = ""
                if sync_entry.extra.get(REMOVAL_RUN_KEY):
                    # A confirmed removal that another tab started and cancelled.
                    return rx.toast.warning(sync_entry.message or REMOVAL_CANCELLED_MESSAGE)
                return rx.toast.warning("Sync cancelled")

        # -- Embedding --
        embed_entry = task_store.get("embedding")
        if embed_entry:
            if embed_entry.status == "running":
                self.is_embedding = True
                self.embedding_progress = embed_entry.progress
                self.embedding_message = embed_entry.message
            elif self.is_embedding and embed_entry.status == "completed":
                self.is_embedding = False
                self.embedding_progress = 100
                self.embedding_message = ""
                self.clear_selection()
                self.load_tracks()
                self.load_library_stats()
                # complete() keeps the last progress message when given none, so only
                # the "nothing was missing" result replaces the default text.
                if embed_entry.message == ALL_EMBEDDED_MESSAGE:
                    return rx.toast.success(ALL_EMBEDDED_MESSAGE)
                return rx.toast.success("Embeddings generated successfully!")
            elif self.is_embedding and embed_entry.status == "failed":
                self.is_embedding = False
                self.embedding_message = ""
                msg = embed_entry.message or "Embedding generation failed"
                return rx.toast.error(msg)
            elif self.is_embedding and embed_entry.status == "cancelled":
                self.is_embedding = False
                self.embedding_message = ""
                self.load_tracks()
                self.load_library_stats()
                return rx.toast.warning("Embedding generation cancelled")

        # -- MusicBrainz enrichment --
        mb_entry = task_store.get("musicbrainz")
        if mb_entry:
            if mb_entry.status == "running":
                self.is_enriching_musicbrainz = True
                self.musicbrainz_progress = mb_entry.progress
                self.musicbrainz_message = mb_entry.message
            elif self.is_enriching_musicbrainz and mb_entry.status == "completed":
                self.is_enriching_musicbrainz = False
                self.musicbrainz_progress = 100
                self.musicbrainz_message = ""
                self.load_library_stats()
                msg = mb_entry.message or "MusicBrainz enrichment complete!"
                return rx.toast.success(msg)
            elif self.is_enriching_musicbrainz and mb_entry.status == "failed":
                self.is_enriching_musicbrainz = False
                self.musicbrainz_message = ""
                msg = mb_entry.message or "MusicBrainz enrichment failed"
                return rx.toast.error(msg)
            elif self.is_enriching_musicbrainz and mb_entry.status == "cancelled":
                self.is_enriching_musicbrainz = False
                self.musicbrainz_message = ""
                self.load_library_stats()
                return rx.toast.warning("MusicBrainz enrichment cancelled")

        # -- Audio analysis --
        audio_entry = task_store.get("audio")
        if audio_entry:
            if audio_entry.status == "running":
                self.is_analyzing_audio = True
                self.audio_analysis_progress = audio_entry.progress
                self.audio_analysis_message = audio_entry.message
                self.audio_analysis_eta = audio_entry.extra.get("eta", "")
                self.audio_analysis_paused = audio_entry.extra.get("paused", False)
            elif self.is_analyzing_audio and audio_entry.status == "completed":
                self.is_analyzing_audio = False
                self.audio_analysis_progress = 100
                self.audio_analysis_message = ""
                self.audio_analysis_eta = ""
                self.audio_analysis_paused = False
                self.load_library_stats()
                msg = audio_entry.extra.get("result_msg", "Audio analysis complete!")
                return rx.toast.success(msg)
            elif self.is_analyzing_audio and audio_entry.status == "cancelled":
                self.is_analyzing_audio = False
                self.audio_analysis_message = ""
                self.audio_analysis_eta = ""
                self.audio_analysis_paused = False
                msg = audio_entry.extra.get("result_msg", "Audio analysis stopped")
                return rx.toast.warning(msg)
            elif self.is_analyzing_audio and audio_entry.status == "failed":
                self.is_analyzing_audio = False
                self.audio_analysis_message = ""
                self.audio_analysis_eta = ""
                self.audio_analysis_paused = False
                msg = audio_entry.message or "Audio analysis failed"
                return rx.toast.error(msg)

    def _filters_active(self) -> bool:
        """True while any filter narrows the list (search, genre, years, tag, audio)."""
        return bool(
            self.search_query
            or self.genre_filter
            or self.year_min
            or self.year_max
            or self.tag_filter
            or self.has_audio
        )

    def load_tracks(self) -> None:
        """Query the current page and the filtered count, then mark the table loaded.

        ``tracks_loaded`` turns True after the first query, whatever its outcome,
        so the page swaps its placeholder for the rows, "No tracks synced yet"
        (``library_has_tracks`` False) or "No tracks match". A failed query sets
        ``tracks_error`` instead of pretending the library is empty. A page past
        the end (after deleting the last page's tracks) falls back to the last page.
        """
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                self.tracks = []
                self.total_filtered_tracks = 0
                self.library_has_tracks = False
                self.tracks_error = ""
                return

            with SQLiteManager(str(db_path)) as db:
                year_min_int = int(self.year_min) if self.year_min else None
                year_max_int = int(self.year_max) if self.year_max else None
                filters: dict[str, Any] = {
                    "search": self.search_query if self.search_query else None,
                    "genre": self.genre_filter if self.genre_filter else None,
                    "year_min": year_min_int,
                    "year_max": year_max_int,
                    "tag": self.tag_filter if self.tag_filter else None,
                    "has_audio": self.has_audio,
                }

                for _attempt in range(2):
                    raw_tracks = db.get_tracks(
                        limit=self.page_size,
                        offset=(self.current_page - 1) * self.page_size,
                        sort_column=self.sort_column,
                        sort_ascending=self.sort_ascending,
                        **filters,
                    )
                    total = int(db.count_tracks(**filters) or 0)
                    last_page = max(1, (total + self.page_size - 1) // self.page_size)
                    if raw_tracks or self.current_page <= last_page:
                        break
                    self.current_page = last_page

                # Each row also carries enr_* flags + an sr-only enr_label for the
                # Library enrichment column (tags, embedding, audio, MusicBrainz).
                rows = [_str_dict(t) for t in raw_tracks]
                self.tracks = [{**row, **enrichment_flags(row)} for row in rows]
                self.total_filtered_tracks = total

                # "No tracks match" needs a non-empty library behind the filters.
                if total or not self._filters_active():
                    self.library_has_tracks = total > 0
                else:
                    cursor = db.get_connection().cursor()
                    cursor.execute("SELECT EXISTS(SELECT 1 FROM tracks)")
                    row = cursor.fetchone()
                    self.library_has_tracks = bool(row and row[0])
            self.tracks_error = ""

        except Exception as e:
            logger.error("Error loading tracks: %s", safe_error_text(e))
            self.tracks = []
            self.total_filtered_tracks = 0
            self.tracks_error = TRACKS_LOAD_ERROR_MESSAGE
        finally:
            self.tracks_loaded = True

    def retry_load_tracks(self) -> None:
        """Retry button of the table's load error."""
        self.load_tracks()

    @rx.var(cache=True)
    def table_view(self) -> str:
        """What the table area shows: loading, error, rows, no_match or not_synced."""
        if not self.tracks_loaded:
            return "loading"
        if self.tracks_error:
            return "error"
        if self.tracks:
            return "rows"
        return "no_match" if self.library_has_tracks else "not_synced"

    # ── Filters ──────────────────────────────────────────────────────
    # Each text filter is a client-owned field (pages/library.py): it commits
    # through its own setter 300 ms after typing pauses and on blur, and the
    # filter bar form commits every field at once on Enter (apply_filter_form).
    # Setters are idempotent, so the duplicate commits this produces cost nothing.

    def _commit_filter(self, attr: str, value: str) -> bool:
        """Store one filter value. Returns True, back on page 1, when it changed."""
        if getattr(self, attr) == value:
            return False
        setattr(self, attr, value)
        self.current_page = 1
        return True

    def set_search_query(self, query: str) -> None:
        """Commit the search text; an unchanged value is a no-op.

        A plain handler (not a background task), so commits apply in the order
        they were sent. The 300 ms pause is the field's client-side debounce.
        """
        if self._commit_filter("search_query", _filter_text(query)):
            self.load_tracks()

    def set_genre_filter(self, genre: str) -> None:
        """Commit the genre text; an unchanged value is a no-op."""
        if self._commit_filter("genre_filter", _filter_text(genre)):
            self.load_tracks()

    def set_year_range(self, year_min: str, year_max: str) -> None:
        """Set both year bounds with one reload (each kept only when a four-digit year)."""
        changed = self._commit_filter("year_min", _year_or_blank(year_min))
        changed = self._commit_filter("year_max", _year_or_blank(year_max)) or changed
        if changed:
            self.load_tracks()

    def set_year_min(self, value: str) -> None:
        """Commit the from-year: a partial or invalid year means no lower bound."""
        if self._commit_filter("year_min", _year_or_blank(value)):
            self.load_tracks()

    def set_year_max(self, value: str) -> None:
        """Commit the to-year: a partial or invalid year means no upper bound."""
        if self._commit_filter("year_max", _year_or_blank(value)):
            self.load_tracks()

    def set_tag_filter(self, value: str) -> None:
        """Commit the tag text; an unchanged value is a no-op."""
        if self._commit_filter("tag_filter", _filter_text(value)):
            self.load_tracks()

    def toggle_has_audio(self, checked: bool) -> None:
        """Turn the "has audio analysis" filter on or off; unchanged is a no-op."""
        checked = bool(checked)
        if checked == self.has_audio:
            return
        self.has_audio = checked
        self.current_page = 1
        self.load_tracks()

    def apply_filter_form(self, form_data: dict[str, Any]) -> None:
        """Apply every text filter from the submitted filter bar with one reload.

        The values come from the form (what is on screen), not from state, so a
        commit still waiting for its pause cannot be lost. A field missing from
        ``form_data`` keeps its current value. Years are sanitized like their
        setters. Reloads only when a filter changed.
        """
        changed = False
        for name, attr, _field_id in LIBRARY_TEXT_FILTERS:
            if name not in form_data:
                continue
            value = form_data[name]
            text = (
                _year_or_blank(value) if attr in ("year_min", "year_max") else _filter_text(value)
            )
            changed = self._commit_filter(attr, text) or changed
        if changed:
            self.load_tracks()

    def clear_filters(self) -> None:
        """Reset every filter and list the whole library from page 1.

        The Clear button (``data-clear-form``) has already emptied the client-owned
        text fields in the browser, in the click itself (assets/plexmix.js), so this
        returns no ``rx.set_value``: one arriving a round trip later would wipe text
        the user typed after clicking Clear. A field mounted later starts from the
        cleared state (``default_value``). The audio checkbox is controlled and
        follows ``has_audio``.
        """
        self.search_query = ""
        self.genre_filter = ""
        self.year_min = ""
        self.year_max = ""
        self.tag_filter = ""
        self.has_audio = False
        self.current_page = 1
        self.load_tracks()

    def next_page(self) -> Any:
        total_pages = (self.total_filtered_tracks + self.page_size - 1) // self.page_size
        if self.current_page < total_pages:
            self.current_page += 1
            self.load_tracks()
            events = [rx.call_script(_SCROLL_TOP_JS)]
            # Next is now disabled (same test as has_next_page, whose cached value
            # is only refreshed after this handler): focus Previous.
            if self.current_page * self.page_size >= self.total_filtered_tracks:
                events.append(rx.call_script(focus_by_id_script(PREV_PAGE_BUTTON_ID)))
            return events
        return None

    def previous_page(self) -> Any:
        if self.current_page > 1:
            self.current_page -= 1
            self.load_tracks()
            events = [rx.call_script(_SCROLL_TOP_JS)]
            if self.current_page <= 1:  # Previous is now disabled: focus Next
                events.append(rx.call_script(focus_by_id_script(NEXT_PAGE_BUTTON_ID)))
            return events
        return None

    def go_to_page(self, page: int):
        total_pages = (self.total_filtered_tracks + self.page_size - 1) // self.page_size
        if 1 <= page <= total_pages:
            self.current_page = page
            self.load_tracks()
            return rx.call_script("window.scrollTo({top: 0, behavior: 'smooth'})")

    @rx.var(cache=True)
    def has_next_page(self) -> bool:
        """True while a later page exists (drives the Next page button's disabled state)."""
        return self.current_page * self.page_size < self.total_filtered_tracks

    @rx.var(cache=True)
    def all_page_selected(self) -> bool:
        """Returns True when all current-page track IDs are in selected_tracks."""
        if not self.tracks:
            return False
        page_ids = {track["id"] for track in self.tracks}
        return page_ids.issubset(set(self.selected_tracks))

    def toggle_select_all(self, checked: bool):
        """Toggle selection of all tracks on the current page, preserving off-page selections."""
        page_ids = {track["id"] for track in self.tracks}
        if checked:
            existing = set(self.selected_tracks)
            existing.update(page_ids)
            self.selected_tracks = list(existing)
        else:
            self.selected_tracks = [tid for tid in self.selected_tracks if tid not in page_ids]

    def toggle_track_selection(self, track_id: str):
        if track_id in self.selected_tracks:
            self.selected_tracks.remove(track_id)
        else:
            self.selected_tracks.append(track_id)

    def select_all_tracks(self):
        self.selected_tracks = [track["id"] for track in self.tracks]

    def clear_selection(self):
        self.selected_tracks = []

    # ── Selection honesty (webui-library-selection) ─────────────────

    @rx.var(cache=True)
    def offpage_selected_count(self) -> int:
        """Selected tracks that are not on the current page."""
        page_ids = {track.get("id") for track in self.tracks}
        return sum(1 for tid in self.selected_tracks if tid not in page_ids)

    @rx.var(cache=True)
    def selection_label(self) -> str:
        """The bulk bar's count, e.g. "50 selected · 50 on other pages"."""
        label = f"{format_count(len(self.selected_tracks))} selected"
        offpage = self.offpage_selected_count
        if offpage:
            where = "another page" if offpage == 1 else "other pages"
            label += f" · {format_count(offpage)} on {where}"
        return label

    @rx.var(cache=True)
    def delete_scope_line(self) -> str:
        """Delete confirmation: the total and how many of them are off this page."""
        selected = len(self.selected_tracks)
        line = f"{pluralize(selected, 'track')} will be deleted from PlexMix"
        where = offpage_phrase(selected, self.offpage_selected_count)
        return f"{line}, {where}." if where else f"{line}."

    @rx.var(cache=True)
    def bulk_tag_scope_line(self) -> str:
        """Tag dialog: what the confirm does to how many tracks, and how many are off-page."""
        selected = len(self.selected_tracks)
        if self.bulk_tag_mode == BULK_TAG_REPLACE:
            line = f"Replaces the tags on {pluralize(selected, 'selected track')}"
        else:
            line = f"Adds these tags to {pluralize(selected, 'selected track')}"
        where = offpage_phrase(selected, self.offpage_selected_count)
        return f"{line}, {where}." if where else f"{line}."

    @rx.var(cache=True)
    def bulk_tag_existing_line(self) -> str:
        """Tag dialog: how many selected tracks already have tags (the replace count)."""
        replace = self.bulk_tag_mode == BULK_TAG_REPLACE
        if self.bulk_tag_counting:
            return "Checking which selected tracks already have tags…"
        if self.bulk_tag_count_failed:
            return "Couldn't check which selected tracks already have tags. " + (
                "Any existing tags will be replaced." if replace else "Existing tags are kept."
            )
        tagged = self.selected_with_tags_count
        if replace:
            if not tagged:
                return "None of the selected tracks have tags yet, so nothing is replaced."
            return f"Existing tags on {pluralize(tagged, 'track')} will be replaced."
        if not tagged:
            return "None of the selected tracks have tags yet."
        verb = "has" if tagged == 1 else "have"
        return (
            f"{pluralize(tagged, 'track')} already {verb} tags. They are kept, "
            "and only new tags are added."
        )

    @rx.var(cache=True)
    def bulk_tag_confirm_blocked(self) -> bool:
        """Replace can't be confirmed until the replace count is known."""
        return self.bulk_tag_mode == BULK_TAG_REPLACE and self.bulk_tag_counting

    @rx.var(cache=True)
    def sync_tagging_note(self) -> str:
        """Sync controls note: sync also AI-tags new tracks (and where metadata goes)."""
        return sync_tagging_note(self.readiness or {})

    # ── Bulk tag operations ──────────────────────────────────────────

    def open_bulk_tag_dialog(self) -> Any:
        """Open the Tag dialog in Add mode and count the selected tracks that have tags.

        The count runs off the event loop (:meth:`count_selected_with_tags`); until
        it arrives the dialog says it is checking, and Replace can't be confirmed.
        """
        if not self.selected_tracks:
            return None
        self.show_bulk_tag_dialog = True
        self.bulk_tag_mode = BULK_TAG_ADD
        self.bulk_tag_error = ""
        self.selected_with_tags_count = 0
        self.bulk_tag_count_failed = False
        self.bulk_tag_counting = True
        self._bulk_tag_count_gen += 1
        return LibraryState.count_selected_with_tags

    @rx.event(background=True)
    async def count_selected_with_tags(self) -> None:
        """Set ``selected_with_tags_count`` for the open Tag dialog (blocking read off-loop)."""
        async with self:
            if not self.access_allowed():
                return
            generation = self._bulk_tag_count_gen
            track_ids = track_ids_of(self.selected_tracks)
        failed = False
        count = 0
        try:
            loop = asyncio.get_running_loop()
            count = await loop.run_in_executor(None, count_tagged_tracks, track_ids)
        except Exception as e:
            logger.error("Could not count tagged tracks: %s", safe_error_text(e))
            failed = True
        async with self:
            if generation != self._bulk_tag_count_gen or not self.show_bulk_tag_dialog:
                return  # the dialog closed or reopened meanwhile: a newer count owns it
            self.selected_with_tags_count = int(count)
            self.bulk_tag_count_failed = failed
            self.bulk_tag_counting = False

    def set_bulk_tag_mode(self, mode: str) -> None:
        """The Add/Replace choice (a native radio group; controlled)."""
        self.bulk_tag_mode = bulk_tag_mode(mode)
        self.bulk_tag_error = ""

    def close_bulk_tag_dialog(self) -> Any:
        """Close the Tag dialog and reset it: text, Add mode, count and message.

        The tags field is client-owned, so the cleared text is also written to it
        (``rx.set_value``); the field is usually unmounting, which makes that a no-op.
        """
        self.show_bulk_tag_dialog = False
        self.bulk_tag_input = ""
        self.bulk_tag_mode = BULK_TAG_ADD
        self.bulk_tag_error = ""
        self.bulk_tag_counting = False
        self._bulk_tag_count_gen += 1
        return rx.set_value(LIBRARY_BULK_TAGS_ID, "")

    def set_bulk_tag_dialog_open(self, is_open: bool) -> Any:
        """Dialog on_open_change: close and clear the input on False (idempotent)."""
        if not is_open:
            return self.close_bulk_tag_dialog()
        return None

    def set_bulk_tag_input(self, value: str) -> None:
        """Blur commit of the bulk-tag field; ignored once the dialog has closed.

        A late commit (the field losing focus as the dialog unmounts) must not
        bring back text that closing the dialog cleared.
        """
        if not self.show_bulk_tag_dialog:
            return
        self.bulk_tag_input = _filter_text(value)

    def submit_bulk_tags(self, form_data: dict[str, Any]) -> Any:
        """Apply the tags typed in the bulk-tag form (the confirm button, or Enter).

        The text and the Add/Replace choice come from ``form_data`` (what is on
        screen: the ``tags`` field and the ``mode`` radios), never from state, so
        a blur commit still in flight cannot make the submit use stale text. A
        missing ``mode`` falls back to the dialog's current choice.

        Blank text keeps the dialog open with "Enter at least one tag." Otherwise
        returns ``LibraryState.apply_bulk_tags`` (Add, its default mode) or the
        same handler called with ``"replace"``.
        """
        self.bulk_tag_input = _filter_text(form_data.get("tags"))
        mode = bulk_tag_mode(form_data.get("mode", self.bulk_tag_mode))
        self.bulk_tag_mode = mode
        if not parse_tag_text(self.bulk_tag_input):
            self.bulk_tag_error = BULK_TAG_EMPTY_MESSAGE
            return None
        if mode == BULK_TAG_REPLACE and self.bulk_tag_counting:
            # The dialog must state the replace count before Replace is confirmed.
            self.bulk_tag_error = BULK_TAG_COUNTING_MESSAGE
            return None
        self.bulk_tag_error = ""
        if mode == BULK_TAG_ADD:
            return LibraryState.apply_bulk_tags
        # mypy sees the plain method, not Reflex's bound EventHandler.
        return cast(Any, LibraryState.apply_bulk_tags)(mode)

    @rx.event(background=True)
    async def apply_bulk_tags(self, mode: str = BULK_TAG_ADD) -> AsyncGenerator[Any, None]:
        """Tag the selected tracks: ``"add"`` (default) merges, ``"replace"`` overwrites.

        Add keeps each track's existing tags and appends the new ones that are
        not already there (case-insensitive). Replace writes the entered text as
        the track's tags. Environments and instruments are never changed. The
        writes run off the event loop; the dialog closes and the selection clears.
        """
        async with self:
            if not self.access_allowed():
                return
            mode = bulk_tag_mode(mode)
            tags_text = self.bulk_tag_input.strip()
            track_ids = track_ids_of(self.selected_tracks)
            self.show_bulk_tag_dialog = False
            self.bulk_tag_input = ""
            self.bulk_tag_mode = BULK_TAG_ADD
            self.bulk_tag_error = ""
            self.bulk_tag_counting = False
            self._bulk_tag_count_gen += 1
        yield rx.set_value(LIBRARY_BULK_TAGS_ID, "")

        if not parse_tag_text(tags_text) or not track_ids:
            return

        try:
            loop = asyncio.get_running_loop()
            updated, unchanged = await loop.run_in_executor(
                None, apply_tags_to_tracks, track_ids, tags_text, mode
            )
        except Exception as e:
            logger.error("Bulk tagging failed: %s", safe_error_text(e))
            yield rx.toast.error(BULK_TAG_ERROR_MESSAGE)
            return

        # load_tracks mutates state, so it must run inside `async with self`
        # in a background event or it raises ImmutableStateError.
        async with self:
            self.selected_tracks = []
            self.load_tracks()

        message = bulk_tag_result_message(mode, updated, unchanged)
        if message:
            yield rx.toast.success(message)
        else:
            yield rx.toast.warning("None of the selected tracks are in your library any more.")

    # ── Bulk delete operations ───────────────────────────────────────

    def open_delete_confirm(self):
        self.show_delete_confirm = True

    def close_delete_confirm(self):
        self.show_delete_confirm = False

    def set_delete_confirm_open(self, is_open: bool) -> None:
        """Dialog on_open_change: close on False (idempotent); True is a no-op.

        The dialog opens only through state, and the confirm handler closes it by
        state too, so a repeated False (Escape, Cancel, confirm) is harmless.
        """
        if not is_open:
            self.show_delete_confirm = False

    @rx.event(background=True)
    async def delete_selected_tracks(self):
        async with self:
            if not self.access_allowed():
                return
            track_ids = list(self.selected_tracks)
            self.show_delete_confirm = False

        ids = track_ids_of(track_ids)
        if not ids:
            return

        def delete_tracks() -> None:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            with SQLiteManager(str(db_path)) as db:
                for track_id in ids:
                    db.delete_track(track_id)

        try:
            await asyncio.get_running_loop().run_in_executor(None, delete_tracks)

            async with self:
                self.selected_tracks = []
                self.load_tracks()

            yield rx.toast.success(f"Deleted {pluralize(len(ids), 'track')} from PlexMix.")

        except Exception as e:
            logger.error("Deleting tracks failed: %s", safe_error_text(e))
            yield rx.toast.error("Couldn't delete the tracks. Details are in the server log.")

    # ── Held removal: notice, confirmation and confirmed removal ─────

    @rx.var(cache=True)
    def removal_notice_line(self) -> str:
        """The notice's headline, e.g. "Plex no longer lists 5,443 of 5,943 tracks"."""
        return held_removal_line(self.removal_notice_count, self.removal_notice_total)

    @rx.var(cache=True)
    def removal_scope_line(self) -> str:
        """Removal confirmation: how many tracks are removed, out of how many."""
        return (
            f"{format_count(self.removal_notice_count)} of your "
            f"{pluralize(self.removal_notice_total, 'track')} will be removed from PlexMix, "
            "because Plex no longer lists them."
        )

    def _set_removal_notice(self, held: int, total: int) -> None:
        """Show the held-removal notice (0 hides it and closes its confirmation)."""
        if self.removal_notice_count != held:
            self.removal_notice_count = held
        if self.removal_notice_total != total:
            self.removal_notice_total = total
        if not held and self.show_removal_confirm:
            self.show_removal_confirm = False

    def _refresh_removal_notice(self, entry: Any) -> None:
        """Set the notice from the "sync" task entry (see :func:`waiting_removal`).

        The poll that calls this is a public handler with no access check of its
        own. So a caller that is not signed in gets no notice: the held count and
        the library size are not for it. The check runs only while a removal waits.
        """
        held, total = waiting_removal(entry)
        if held and not self.access_allowed():
            held = total = 0
        self._set_removal_notice(held, total)

    def open_removal_confirm(self) -> None:
        """ "Review removal": open the confirmation for the removal the last sync held.

        The count is read from the sync task again first, so the dialog states what
        is held now. With nothing held any more it stays closed.
        """
        if not self.access_allowed():
            return
        self._refresh_removal_notice(task_store.get("sync"))
        self.show_removal_confirm = self.removal_notice_count > 0

    def set_removal_confirm_open(self, is_open: bool) -> None:
        """Dialog on_open_change: close on False (idempotent); True is a no-op.

        Escape and Cancel only close the dialog: no track is removed and the notice
        stays. The dialog opens only through :meth:`open_removal_confirm`.
        """
        if not is_open:
            self.show_removal_confirm = False

    @rx.event(background=True)
    async def confirm_removal(self) -> AsyncGenerator[Any, None]:
        """Confirm button of the removal dialog: remove the tracks the last sync held.

        Runs as the ``"sync"`` task, so it never runs beside a sync. Plex is listed
        again and a backup is made first (``SyncEngine.remove_unlisted_tracks``, off
        the event loop). Exactly one result is reported: the tracks were removed
        (the notice is cleared), Plex lists them again (cleared), more tracks are
        missing now (the notice shows the new count), or nothing could be removed
        because the backup, the listing, the delete or the connection failed, or
        because the task was cancelled before the deletes were saved (the notice
        stays). A hold that was counted in another database or Plex library than the
        settings name now is ended without a removal.
        A confirm that finds a sync running, or no removal held any more, says so
        and starts nothing.
        """
        cancel_event: Optional[threading.Event] = None
        confirmed = held = total = 0
        held_keys: Optional[frozenset[str]] = None
        scope = ""
        refusal = ("info", REMOVAL_NOT_HELD_MESSAGE)
        async with self:
            if not self.access_allowed():
                return
            stated = _whole(self.removal_notice_count)
            asked = self.show_removal_confirm and stated > 0
            self.show_removal_confirm = False
            if not asked:
                return  # dismissed, or a repeated click
            # The count the dialog stated, never more than the sync task holds.
            held, total = waiting_removal(task_store.get("sync"))
            held_keys = waiting_removal_keys(held, total)
            scope = waiting_removal_scope(held, total)
            confirmed = min(stated, held)
            if confirmed > 0:
                # Taken inside this block, so no poll can report the finished sync
                # a second time between these lines.
                cancel_event = task_store.start("sync", message=REMOVAL_CHECKING_MESSAGE)
            if cancel_event is None:
                # A sync is running (it replaced the task entry), or the entry
                # holds no removal any more: there is nothing to confirm.
                if task_store.is_running("sync"):
                    refusal = ("warning", REMOVAL_SYNC_RUNNING_MESSAGE)
                self._refresh_removal_notice(task_store.get("sync"))
            else:
                # The hold travels with the new task entry: a removal that fails
                # leaves the notice as it was.
                task_store.update(
                    "sync", extra={REMOVAL_RUN_KEY: True, **held_removal_extra(held, total)}
                )
                self.is_syncing = True
                self.sync_progress = 0
                self.sync_message = REMOVAL_CHECKING_MESSAGE
                self._clear_sync_error()
                self._set_removal_notice(0, 0)  # hidden while the removal runs
        if cancel_event is None:
            yield result_toast(*refusal)
            return

        report = RemovalReport(
            "failed", "error", REMOVAL_ERROR_MESSAGE, held, total, keys=held_keys
        )
        settings: Any = None
        try:
            from plexmix.config.settings import Settings
            from plexmix.services.sync_service import PlexConnectionError

            settings = Settings.load_from_file()
            loop = asyncio.get_running_loop()
            plex_client: Any = None
            if not hold_applies_to(settings, held, total):
                # The settings name another database or another Plex library than
                # the sync that held these tracks. Nobody was asked about that one:
                # the hold ends here, and nothing is listed, backed up or removed.
                report = RemovalReport("cancelled", "warning", REMOVAL_SETTINGS_CHANGED_MESSAGE)
            else:
                # A hold that stays (a failed backup, more missing tracks) keeps this.
                scope = hold_scope(settings) or scope
                try:
                    plex_client, plex_fp = await loop.run_in_executor(
                        None, _connect_plex_for_sync, settings
                    )
                except PlexConnectionError as e:
                    record_plex_connection_error(e, settings)
                    title = mask_urls_in_text(plex_error_title(e.code, settings))
                    report = report._replace(message=f"{title}. {NOTHING_REMOVED}")
                else:
                    record_readiness("plex", "ready", fingerprint=plex_fp)
            if plex_client is not None:
                if cancel_event.is_set():
                    report = report._replace(
                        status="cancelled", tone="warning", message=REMOVAL_CANCELLED_MESSAGE
                    )
                else:
                    # The engine checks the same event after it has listed Plex, after
                    # the backup, and before every delete until the deletes are saved.
                    result = await loop.run_in_executor(
                        None,
                        run_confirmed_removal,
                        plex_client,
                        settings,
                        confirmed,
                        held_keys,
                        cancel_event,
                    )
                    report = removal_report(result, held, total, held_keys)
        except Exception as e:
            # An error from Plex can repeat the server address: mask it for the log.
            secrets = _sync_secrets(settings)
            detail = redact_secrets(mask_urls_in_text(safe_error_text(e)), secrets)
            logger.error("Confirmed removal failed: %s", detail)
        except BaseException:
            # Never leave the "sync" task running: it would block every later sync.
            task_store.complete("sync", status="failed", message=REMOVAL_ERROR_MESSAGE)
            raise

        task_store.update(
            "sync",
            extra={
                REMOVAL_RESULT_KEY: report.result,
                **held_removal_extra(report.held, report.total),
            },
        )
        remember_held_removal(report.held, report.total, report.keys, scope)
        async with self:
            # Completed inside this block, with is_syncing cleared: this tab's poll
            # then never reports the result a second time.
            task_store.complete("sync", status=report.status, message=report.message)
            self.is_syncing = False
            self.sync_message = ""
            self._refresh_removal_notice(task_store.get("sync"))
            if report.tone == "success":
                # Tracks were removed: a selection could still name some of them.
                self.selected_tracks = []
            self.load_tracks()
            self.load_library_stats()
        yield result_toast(report.tone, report.message)

    @rx.event(background=True)
    async def start_sync(self) -> None:
        # Bound before anything starts: the handler at the end names them.
        from plexmix.plex.client import PlexFetchError, PlexReadInterrupted

        async with self:
            if not self.access_allowed():
                return
            self.is_syncing = True
            self.sync_progress = 0
            self.sync_message = "Starting sync..."
            self.show_regenerate_confirm = False
            self._clear_sync_error()
            # A new sync replaces the task entry the notice is derived from.
            self._set_removal_notice(0, 0)
            sync_mode = self.sync_mode

        cancel_event = task_store.start("sync", message="Starting sync...")
        if cancel_event is None:
            # Already running — polling will show existing task's progress
            return
        # The new sync replaces whatever the last one held.
        forget_held_removal()

        settings: Any = None
        sync_engine: Any = None
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.services.sync_service import PlexConnectionError

            settings = Settings.load_from_file()

            # Connecting retries with backoff: keep it off the event loop. The
            # result also verifies Plex for readiness (webui-readiness).
            try:
                plex_client, plex_fp = await asyncio.get_running_loop().run_in_executor(
                    None, _connect_plex_for_sync, settings
                )
            except PlexConnectionError as e:
                # The UI gets plain copy plus the code (``extra["plex_error_code"]``,
                # ``extra["readiness_cause"]``) instead of the CLI text, which
                # points at a terminal command.
                cause = record_plex_connection_error(e, settings)
                task_store.update(
                    "sync", extra={"plex_error_code": e.code, "readiness_cause": cause}
                )
                task_store.complete(
                    "sync", status="failed", message=plex_error_title(e.code, settings)
                )
                return
            record_readiness("plex", "ready", fingerprint=plex_fp)

            if not settings.plex.library_name:
                task_store.update(
                    "sync",
                    extra={"plex_error_code": "no_library", "readiness_cause": "missing_config"},
                )
                task_store.complete(
                    "sync", status="failed", message=plex_error_title("no_library", settings)
                )
                return

            db_path = settings.database.get_db_path()
            db = SQLiteManager(str(db_path))
            db.connect()

            from plexmix.plex.sync import SyncEngine
            from plexmix.services.providers import build_ai_provider

            def progress_callback(progress: float, message: str) -> None:
                if cancel_event.is_set():
                    return
                task_store.update("sync", progress=int(progress * 100), message=message)

            ai_provider = build_ai_provider(settings, silent=True)
            if ai_provider is None:
                task_store.update(
                    "sync", message="AI provider not configured — syncing without tagging..."
                )

            # Pass MusicBrainz settings if enabled and enrichment on sync is on
            mb_settings = None
            if settings.musicbrainz.enabled and settings.musicbrainz.enrich_on_sync:
                mb_settings = settings.musicbrainz

            sync_engine = SyncEngine(
                plex_client,
                db,
                ai_provider=ai_provider,
                musicbrainz_settings=mb_settings,
            )

            loop = asyncio.get_running_loop()

            def run_sync():
                if sync_mode == "regenerate":
                    sync_engine.regenerate_sync(
                        generate_embeddings=False,
                        progress_callback=progress_callback,
                        cancel_event=cancel_event,
                    )
                else:
                    sync_engine.incremental_sync(
                        generate_embeddings=False,
                        progress_callback=progress_callback,
                        cancel_event=cancel_event,
                    )

            try:
                await loop.run_in_executor(None, run_sync)
            finally:
                if ai_provider is not None:
                    # Sync tagging is a real use of the AI provider (readiness D2).
                    record_sync_tagging(sync_engine, settings)

            # What the engine kept or held: unread tracks, and a removal that waits
            # for confirmation. Only a real SyncOutcome is copied (see the helper).
            outcome = getattr(sync_engine, "outcome", None)
            outcome_extra = sync_outcome_extra(outcome)
            if outcome_extra:
                task_store.update("sync", extra=outcome_extra)
                if outcome_extra[REMOVAL_REASON_KEY] == REASON_HELD:
                    # Also outside the task store, which forgets a finished task
                    # after an hour, and with the keys of the held tracks.
                    remember_held_removal(
                        outcome_extra[REMOVAL_HELD_KEY],
                        outcome_extra[REMOVAL_TOTAL_KEY],
                        getattr(outcome, "held_keys", None),
                        hold_scope(settings),
                    )

            # Run audio analysis if enabled
            run_audio = settings.audio.analyze_on_sync
            if run_audio and not cancel_event.is_set():
                task_store.update("sync", message="Running audio analysis...")
                try:
                    from plexmix.audio.analyzer import EssentiaAnalyzer

                    analyzer = EssentiaAnalyzer()
                    pending_tracks = [
                        t for t in db.get_tracks_without_audio_features() if t.file_path
                    ]
                    if pending_tracks:
                        loop = asyncio.get_running_loop()
                        duration_limit = settings.audio.duration_limit
                        num_workers = max(1, settings.audio.workers)
                        executor = ThreadPoolExecutor(max_workers=num_workers)
                        analyzed = 0
                        total = len(pending_tracks)
                        track_iter = iter(pending_tracks)
                        in_flight: dict[asyncio.Future, object] = {}

                        def _submit():
                            t = next(track_iter, None)
                            if t is None:
                                return

                            def do_analyze(trk=t):
                                resolved = settings.audio.resolve_path(trk.file_path)
                                return trk, analyzer.analyze(
                                    resolved, duration_limit=duration_limit
                                )

                            in_flight[loop.run_in_executor(executor, do_analyze)] = t

                        for _ in range(min(num_workers, total)):
                            _submit()

                        while in_flight:
                            if cancel_event.is_set():
                                for fut in in_flight:
                                    fut.cancel()
                                break
                            done, _ = await asyncio.wait(
                                in_flight.keys(), return_when=asyncio.FIRST_COMPLETED
                            )
                            for fut in done:
                                in_flight.pop(fut)
                                try:
                                    track_obj, features = fut.result()
                                    # insert_audio_features expects a dict, not the
                                    # AudioFeatures dataclass — otherwise every insert
                                    # errored and no features were ever saved.
                                    db.insert_audio_features(track_obj.id, features.to_dict())
                                    analyzed += 1
                                except Exception as e:
                                    logger.warning(f"Audio analysis failed: {e}")
                                _submit()
                            if not cancel_event.is_set():
                                task_store.update(
                                    "sync",
                                    progress=int((analyzed / total) * 100) if total else 0,
                                    message=f"Audio analysis: {analyzed}/{total} tracks ({num_workers} workers)",
                                )

                        executor.shutdown(wait=False)
                except ImportError:
                    logger.warning("Essentia not installed, skipping audio analysis")
                except Exception as e:
                    logger.warning(f"Audio analysis error: {e}")

            db.close()

            if cancel_event.is_set():
                task_store.complete("sync", status="cancelled")
            else:
                # Plain result text; "" keeps the engine's last progress message.
                task_store.complete(
                    "sync",
                    message=sync_result_message(outcome, tagging_skipped=ai_provider is None),
                )

        except KeyboardInterrupt:
            # Cancellation propagates as KeyboardInterrupt from the sync engine;
            # complete the task as cancelled so its TaskStore entry doesn't wedge
            # in the "running" state forever (KeyboardInterrupt isn't an Exception).
            task_store.complete("sync", status="cancelled")
        except PlexFetchError as e:
            # Plex stopped answering or could not list the library, so the engine
            # wrote nothing. The error is one plain sentence without an address.
            secrets = _sync_secrets(settings)
            sentence = redact_secrets(mask_urls_in_text(str(e)), secrets)
            if getattr(sync_engine, "cleared_before_sync", False) is True:
                # A "Regenerate" sync: "nothing was changed" is true for the library
                # sync, but that mode had cleared the tags and embeddings first.
                sentence = f"{sentence} {REGENERATE_CLEARED_MESSAGE}".strip()
            logger.error("Sync stopped: %s", sentence)
            if isinstance(e, PlexReadInterrupted):
                cause = record_plex_read_interrupted(settings)
                task_store.update("sync", extra={"readiness_cause": cause})
            task_store.complete("sync", status="failed", message=sentence or SYNC_FAILED_TITLE)
        except Exception as e:
            # Shown in the toast and the inline sync_error: plain text, with the
            # Plex token and AI key redacted (from the log line too).
            secrets = _sync_secrets(settings)
            logger.error("Sync failed: %s", redact_secrets(safe_error_text(e), secrets))
            task_store.complete("sync", status="failed", message=friendly_error(e, secrets))

    # ── Inline sync failure (sync_error) ──

    def _clear_sync_error(self) -> None:
        self.sync_error = ""
        self.sync_error_body = ""
        self.sync_error_action_label = ""
        self.sync_error_action_href = ""

    def _show_sync_error(self, entry: Any) -> None:
        """Fill ``sync_error`` from a failed sync task (its ``plex_error_code`` extra)."""
        extra = getattr(entry, "extra", None) or {}
        code = str(extra.get("plex_error_code") or "")
        settings: Any = None
        if code:
            try:
                from plexmix.config.settings import Settings

                settings = Settings.load_from_file()
            except Exception:
                settings = None
        copy = sync_error_copy(code, str(getattr(entry, "message", "") or ""), settings)
        self.sync_error = copy["title"]
        self.sync_error_body = copy["body"]
        self.sync_error_action_label = copy["action_label"]
        self.sync_error_action_href = copy["action_href"]

    def dismiss_sync_error(self) -> None:
        """Dismiss button of the inline sync failure."""
        self._clear_sync_error()

    @rx.event(background=True)
    async def generate_embeddings(self) -> AsyncGenerator[Any, None]:
        """Embed the selected tracks; with nothing selected, offer the whole library.

        With a selection, runs the ``"embedding"`` task over those tracks. With no
        selection it behaves as :meth:`request_embed_all`: it counts the tracks
        without an embedding and opens the scope confirmation, or reports that
        all tracks already have embeddings (``embed_all_notice`` plus a toast).
        """
        async with self:
            if not self.access_allowed():
                return
            selected = [int(t) for t in self.selected_tracks if str(t).strip().isdigit()]
            if selected:
                self.is_embedding = True
                self.embedding_progress = 0
                self.embedding_message = "Starting embedding generation..."

        if not selected:
            async for event in self._embed_all_preflight_flow():
                yield event
            return
        await self._run_embedding_job(selected)

    # ── Whole-library embedding: request → scope_confirm → confirm ──
    #
    # Rendered with components/scope_confirm.embed_all_confirm_dialog() on the
    # Dashboard (setup checklist) and the Library page, bound to these vars:
    #   embed_all_open / set_embed_all_open   controlled AlertDialog open state
    #   embed_all_scope                       readiness.describe_job_scope copy
    #   confirm_embed_all / cancel_embed_all  the dialog buttons
    #   embed_all_checking                    True while counting (button busy)
    #   embed_all_notice                      why nothing opened ("" when it did)

    @rx.event(background=True)
    async def request_embed_all(self) -> AsyncGenerator[Any, None]:
        """Ask to embed every track that has no embedding ("Build search index").

        Counts the tracks without an embedding (in an executor) and opens the
        scope confirmation with the count, the embedding provider and model, and
        whether metadata leaves the machine. Opens nothing, and sets
        ``embed_all_notice`` plus a toast, when every track already has an
        embedding, nothing is synced, an embedding job already runs, or the
        embedding provider can't start (with the readiness cause and remedy).
        """
        async with self:
            if not self.access_allowed():
                return
        async for event in self._embed_all_preflight_flow():
            yield event

    def _clear_embed_all_notice(self) -> None:
        self.embed_all_notice = ""
        self.embed_all_notice_action_label = self.embed_all_notice_action_href = ""
        self.embed_all_notice_secondary_label = self.embed_all_notice_secondary_href = ""

    async def _embed_all_preflight_flow(self) -> AsyncGenerator[Any, None]:
        async with self:
            if self.embed_all_checking:
                return  # a count is already running (double activation)
            self.embed_all_checking = True
            self._clear_embed_all_notice()
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, embed_all_preflight)
        except Exception as e:
            logger.error("Could not count tracks without embeddings: %s", safe_error_text(e))
            result = EmbedAllPreflight("error", notice=EMBED_ALL_ERROR_MESSAGE)
        async with self:
            self.embed_all_checking = False
            if result.state == "ready":
                self.embed_all_scope = dict(result.scope)
                self.embed_all_count = result.missing
                self.embed_all_open = True
            else:
                self.embed_all_notice = result.notice
                self.embed_all_notice_action_label = result.action_label
                self.embed_all_notice_action_href = result.action_href
                self.embed_all_notice_secondary_label = result.secondary_label
                self.embed_all_notice_secondary_href = result.secondary_href
                self.embed_all_notice_tone = (
                    "error"
                    if result.state in ("blocked", "error")
                    else "warning"
                    if result.state == "mismatch"
                    else "info"
                )
        if result.state in ("blocked", "error"):
            yield rx.toast.error(result.notice)
        elif result.state == "mismatch":
            yield rx.toast.warning(result.notice)
        elif result.state != "ready":
            yield rx.toast.info(result.notice)

    @rx.event
    def set_embed_all_open(self, is_open: bool) -> None:
        """``on_open_change`` of the embed-all confirmation: close on False (idempotent).

        True is a no-op: the dialog only opens through :meth:`request_embed_all`.
        The scope copy is kept so the closing animation never shows blank text.
        """
        if not is_open:
            self.embed_all_open = False
            self.embed_all_count = 0

    @rx.event
    def dismiss_embed_all_notice(self) -> None:
        """Dismiss button of the embed-all notice (Library page)."""
        self._clear_embed_all_notice()

    @rx.event
    def cancel_embed_all(self) -> None:
        """Close the embed-all confirmation without starting anything."""
        self.embed_all_open = False
        self.embed_all_count = 0

    @rx.event
    def confirm_embed_all(self) -> Any:
        """Confirm: close the dialog and start :meth:`embed_all_missing`.

        A plain button (not ``alert_dialog.action``) calls this, so it closes the
        dialog itself; a repeated click finds it closed and starts nothing.
        """
        pending = self.embed_all_open and self.embed_all_count > 0
        self.embed_all_open = False
        self.embed_all_count = 0
        if not pending:
            return None
        return LibraryState.embed_all_missing

    @rx.event(background=True)
    async def embed_all_missing(self) -> None:
        """Run the ``"embedding"`` task over every track without an embedding.

        Same task, progress and Cancel as the selection path; the search index is
        rebuilt afterwards. Started by :meth:`confirm_embed_all`.
        """
        async with self:
            if not self.access_allowed():
                return
            self.is_embedding = True
            self.embedding_progress = 0
            self.embedding_message = "Starting embedding generation..."
            self._clear_embed_all_notice()
        await self._run_embedding_job(None)

    async def _run_embedding_job(self, track_ids: Optional[list[int]]) -> None:
        """Start the ``"embedding"`` task and run :func:`run_embedding_job` off-loop.

        ``LibraryState.poll_task_progress`` (and the Dashboard's setup poller)
        report its progress and outcome from ``task_store``.
        """
        cancel_event = task_store.start("embedding", message="Starting embedding generation...")
        if cancel_event is None:
            return  # already running: polling shows the existing task's progress
        try:
            loop = asyncio.get_running_loop()
            status, message = await loop.run_in_executor(
                None, run_embedding_job, track_ids, cancel_event
            )
            task_store.complete("embedding", status=status, message=message)
        except Exception as e:
            logger.error("Embedding generation failed: %s", safe_error_text(e))
            task_store.complete("embedding", status="failed", message=friendly_error(e))

    @rx.event(background=True)
    async def enrich_musicbrainz(self):
        async with self:
            if not self.access_allowed():
                return
            self.is_enriching_musicbrainz = True
            self.musicbrainz_progress = 0
            self.musicbrainz_message = "Starting MusicBrainz enrichment..."

        cancel_event = task_store.start("musicbrainz", message="Starting MusicBrainz enrichment...")
        if cancel_event is None:
            return

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            try:
                from plexmix.services.musicbrainz_service import (
                    get_enrichable_tracks,
                    enrich_tracks,
                )
            except ImportError:
                task_store.complete(
                    "musicbrainz",
                    status="failed",
                    # Plain copy: no terminal commands in the UI.
                    message="MusicBrainz support isn't installed. Reinstall PlexMix to add it.",
                )
                return

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                task_store.complete("musicbrainz", status="failed", message="Database not found")
                return

            def run_enrichment():
                """Run enrichment in executor thread with its own DB connection."""
                with SQLiteManager(str(db_path)) as db:
                    tracks = get_enrichable_tracks(db)

                    if not tracks:
                        return 0, 0, 0, False  # enriched, cached, errors, had_tracks

                    def on_progress(
                        enriched: int, cached: int, mb_errors: int, total_count: int
                    ) -> None:
                        if cancel_event.is_set():
                            return
                        processed = enriched + cached + mb_errors
                        task_store.update(
                            "musicbrainz",
                            progress=int((processed / total_count) * 100) if total_count else 0,
                            message=f"Enriched {enriched}/{total_count} tracks ({cached} cached)",
                        )

                    e, c, err = enrich_tracks(
                        db,
                        settings.musicbrainz,
                        tracks,
                        progress_callback=on_progress,
                        cancel_event=cancel_event,
                    )
                    return e, c, err, True

            loop = asyncio.get_running_loop()
            enriched, cached, errors, had_tracks = await loop.run_in_executor(
                None,
                run_enrichment,
            )

            if not had_tracks:
                task_store.complete(
                    "musicbrainz",
                    message="All tracks already have MusicBrainz metadata.",
                )
                return

            if cancel_event.is_set():
                task_store.complete("musicbrainz", status="cancelled")
            else:
                task_store.complete(
                    "musicbrainz",
                    message=f"Enriched {enriched} tracks, {cached} cached ({errors} errors)",
                )

        except Exception as e:
            task_store.complete("musicbrainz", status="failed", message=str(e))

    def pause_audio_analysis(self):
        task_store.pause("audio")
        self.audio_analysis_paused = True

    def resume_audio_analysis(self):
        task_store.resume("audio")
        self.audio_analysis_paused = False

    @rx.event(background=True)
    async def analyze_audio(self):
        async with self:
            self.is_analyzing_audio = True
            self.audio_analysis_paused = False
            self.audio_analysis_progress = 0
            self.audio_analysis_message = "Starting audio analysis..."
            self.audio_analysis_eta = ""

        cancel_event = task_store.start("audio", message="Starting audio analysis...")
        if cancel_event is None:
            return

        pause_event = task_store.get_pause_event("audio")

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            try:
                from plexmix.audio.analyzer import EssentiaAnalyzer

                # The constructor imports essentia: a missing package fails here,
                # with plain copy instead of the package's install command.
                analyzer = EssentiaAnalyzer()
            except ImportError:
                from plexmix.services.readiness import AUDIO_REMEDY

                task_store.complete(
                    "audio",
                    status="failed",
                    message=f"Audio analysis isn't installed. {AUDIO_REMEDY}.",
                )
                return

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            duration_limit = settings.audio.duration_limit
            num_workers = max(1, settings.audio.workers)

            if not db_path.exists():
                task_store.complete("audio", status="failed", message="Database not found")
                return

            loop = asyncio.get_running_loop()
            cancelled = False

            with SQLiteManager(str(db_path)) as db:
                pending_tracks = [t for t in db.get_tracks_without_audio_features() if t.file_path]

                if not pending_tracks:
                    task_store.complete(
                        "audio",
                        message="All tracks already have audio features.",
                    )
                    return

                total = len(pending_tracks)
                analyzed = 0
                eta_base_time = time.monotonic()
                eta_base_count = 0

                executor = ThreadPoolExecutor(max_workers=num_workers)
                track_iter = iter(pending_tracks)

                # Sliding window: keep up to num_workers tasks in flight
                in_flight: dict[asyncio.Future, object] = {}

                def _submit_next() -> bool:
                    """Submit the next track. Returns True if one was submitted."""
                    track = next(track_iter, None)
                    if track is None:
                        return False

                    def do_analyze(t=track):
                        resolved = settings.audio.resolve_path(t.file_path)
                        return (
                            t,
                            analyzer.analyze(resolved, duration_limit=duration_limit).to_dict(),
                        )

                    fut = loop.run_in_executor(executor, do_analyze)
                    in_flight[fut] = track
                    return True

                # Fill initial window
                for _ in range(min(num_workers, total)):
                    _submit_next()

                while in_flight:
                    # Check cancellation
                    if cancel_event.is_set():
                        cancelled = True
                        for fut in in_flight:
                            fut.cancel()
                        break

                    # Check pause — let in-flight work finish, then hold
                    if task_store.is_paused("audio"):
                        task_store.update(
                            "audio",
                            message=f"Paused — {analyzed}/{total} tracks analyzed",
                            extra={"paused": True},
                        )
                        await pause_event.wait()
                        if cancel_event.is_set():
                            cancelled = True
                            for fut in in_flight:
                                fut.cancel()
                            break
                        eta_base_time = time.monotonic()
                        eta_base_count = analyzed

                    # Wait for at least one to complete
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
                            logger.warning("Audio analysis failed: %s", exc)

                        # Submit a replacement
                        _submit_next()

                    # Update progress & ETA
                    if not cancel_event.is_set():
                        elapsed = time.monotonic() - eta_base_time
                        since_baseline = analyzed - eta_base_count
                        if since_baseline > 0 and elapsed > 0:
                            rate = since_baseline / elapsed
                            remaining = (total - analyzed) / rate
                            eta_str = _format_eta(remaining)
                        else:
                            eta_str = "calculating..."

                        task_store.update(
                            "audio",
                            progress=int((analyzed / total) * 100) if total else 0,
                            message=f"Analyzed {analyzed}/{total} tracks ({num_workers} workers)",
                            extra={"eta": eta_str, "paused": False},
                        )

                executor.shutdown(wait=False)

            if cancelled:
                task_store.update(
                    "audio",
                    extra={
                        "result_msg": f"Audio analysis stopped — {analyzed}/{total} tracks analyzed"
                    },
                )
                task_store.complete("audio", status="cancelled")
            else:
                task_store.update(
                    "audio",
                    extra={"result_msg": f"Audio analysis complete! Analyzed {analyzed} tracks."},
                )
                task_store.complete("audio")

        except Exception as e:
            task_store.complete("audio", status="failed", message=str(e))
