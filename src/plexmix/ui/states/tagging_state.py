import logging
import reflex as rx
import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, AsyncGenerator, Mapping, NamedTuple, Optional
from plexmix.config.constants import TAG_BATCH_SIZE
from plexmix.ai.errors import FatalProviderError
from plexmix.ui.states.app_state import (
    AppState,
    current_fingerprint_safe,
    provider_failure_cause,
    record_readiness,
)
from plexmix.ui.job_manager import task_store


from plexmix.ui.utils.a11y import focus_by_id_script
from plexmix.ui.utils.errors import friendly_error
from plexmix.ui.utils.helpers import (
    format_count,
    format_tags_for_display,
    pluralize,
    safe_error_text,
)
from plexmix.ui.utils.helpers import str_dict as _str_dict

if TYPE_CHECKING:
    from reflex.event import EventSpec


logger = logging.getLogger(__name__)


def tagging_failure_cause(error: FatalProviderError) -> tuple[str, str]:
    """Map a tagging run's ``FatalProviderError`` to a readiness ``(cause, message)``.

    Rejected credentials → ``invalid_credentials``; otherwise the original
    exception's category when known, else ``unknown`` with the error's
    user-facing (secret-free) message.
    """
    from plexmix.services.readiness import Cause

    text = (error.user_message or "").lower()
    if "credentials" in text or "authentication" in text or "malformed or invalid" in text:
        return Cause.INVALID_CREDENTIALS.value, ""
    if error.cause is not None:
        cause, _ = provider_failure_cause(error.cause)
        if cause != Cause.UNKNOWN.value:
            return cause, ""
    return Cause.UNKNOWN.value, error.user_message or ""


# Prefix of the read-only row's Edit button id (``tag-edit-btn-<track id>``).
# pages/tagging.py renders the same id so focus can return to it after an edit.
EDIT_BUTTON_ID_PREFIX = "tag-edit-btn-"

# Element ids of the client-owned filter fields (pages/tagging.py renders them).
# A handler that changes one of these filters for any reason other than the
# field's own commit must also return ``rx.set_value(<id>, <value>)`` so the
# visible field follows (webui-input-responsiveness D3).
ARTIST_FILTER_ID = "tag-filter-artist"
GENRE_FILTER_ID = "tag-filter-genre"
STALE_DAYS_ID = "tag-filter-stale-days"

# Element ids of the inline editor's fields. Their form ``name``s (``tags``,
# ``environments``, ``instruments``) are the keys ``submit_tag_edit`` reads.
EDIT_MOODS_ID = "tag-edit-moods"
EDIT_ENVIRONMENTS_ID = "tag-edit-envs"
EDIT_INSTRUMENTS_ID = "tag-edit-instr"

# ── Tagging scope (webui-honest-status-and-first-run D10) ─────────────────────
# Ids pages/tagging.py renders: the scope confirmation (its buttons are
# ``<id>-cancel``, ``<id>-confirm`` and ``<id>-secondary``), the Tag tracks button
# and its visible reason, and the persistent notice region.
TAG_CONFIRM_ID = "tag-confirm"
TAG_START_ID = "tag-start"
TAG_START_REASON_ID = "tag-start-reason"
TAG_NOTICE_REGION_ID = "tag-notice-region"

# ``tagging_notice`` shape: level ("error" | "warning", "" when there is none),
# an optional bold title, the text, an optional link action, and its source
# ("preview", "start" or "job"). Every key is always present so the page can
# index it without a JS ``undefined``. A preview replaces only preview and start
# notices: a failed job's notice stays until dismissed or a new start.
EMPTY_NOTICE: dict[str, str] = {
    "level": "",
    "title": "",
    "text": "",
    "action_label": "",
    "action_href": "",
    "source": "",
}

# ``tag_scope`` (the confirmation copy): ``readiness.describe_job_scope`` keys plus
# the provider label/model and the counts. Every key is always present.
EMPTY_SCOPE: dict[str, str] = {
    "title": "",
    "count_line": "",
    "replace_line": "",
    "provider_line": "",
    "privacy_line": "",
    "duration_line": "",
    "confirm_label": "",
    "secondary_label": "",
    "danger": "false",
    "provider_label": "",
    "model": "",
    "count": "",
    "untagged": "",
}

# Why a start is refused, and the matching reason under a disabled Tag tracks.
PREVIEW_FIRST_MESSAGE = "Preview first to see which tracks will be tagged."
PREVIEW_AGAIN_MESSAGE = (
    "The filters changed after the preview, so nothing was tagged. Preview again to see "
    "which tracks will be tagged."
)
WAIT_FOR_COUNT_MESSAGE = "Wait for the count to finish, then tag."
NOTHING_TO_TAG_MESSAGE = "No tracks match these filters, so there is nothing to tag."
NO_UNTAGGED_MESSAGE = "Every previewed track already has tags."
TAGGING_RUNNING_MESSAGE = "Tagging is already running. Wait for it to finish or cancel it."
NOT_SYNCED_TEXT = (
    "Tagging works on the tracks PlexMix has synced from Plex. Sync your library, then "
    "preview again."
)
SYNC_ACTION_LABEL = "Sync library"
PREVIEW_ERROR_TITLE = "The library database couldn't be read"
TAGGING_FAILED_TITLE = "Tagging stopped"
NO_TRACKS_LEFT_MESSAGE = "No tracks match the previewed filters any more. Preview again."
NO_DATABASE_MESSAGE = "Your library hasn't been synced yet. Sync it, then preview again."
RECENT_LOAD_ERROR = "Couldn't load the recently tagged tracks."

# Stale-tag badge window (days), as before.
_STALE_BADGE_DAYS = 30


def _notice(
    level: str,
    text: str,
    *,
    title: str = "",
    action_label: str = "",
    action_href: str = "",
    source: str = "start",
) -> dict[str, str]:
    """A ``tagging_notice`` value (see :data:`EMPTY_NOTICE`)."""
    return {
        "level": level,
        "title": title,
        "text": text,
        "action_label": action_label if action_href else "",
        "action_href": action_href,
        "source": source,
    }


def _whole_number(value: Any, label: str) -> Optional[int]:
    """A blank value means no filter (None); a whole number of ASCII digits is the
    number; anything else raises ``ValueError("<label> must be a whole number.")``."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number.")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{label} must be a whole number.")
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.isascii() and text.isdigit():
        return int(text)
    raise ValueError(f"{label} must be a whole number.")


def _text_filter(value: Any) -> Optional[str]:
    """Trimmed text, or None (no filter) when blank."""
    text = str(value if value is not None else "").strip()
    return text or None


def normalize_tag_filters(
    genre: Any = "",
    year_min: Any = "",
    year_max: Any = "",
    artist: Any = "",
    has_no_tags: Any = False,
    stale_days: Any = "",
) -> dict[str, Any]:
    """Validate and normalise the Tagging filters into ``get_tracks_by_filter`` kwargs.

    A blank (or None) year or stale-days value means no filter, never 0; any other
    value that is not a whole number raises ``ValueError`` with a plain message
    (for example "Stale days must be a whole number."). This is defence in depth:
    ``set_stale_days`` already keeps digits only. Blank text filters are dropped.

    Returns ``{"genre", "year_min", "year_max", "artist", "has_no_tags",
    "stale_days"}`` (str/int or None, and a bool for ``has_no_tags``).
    """
    return {
        "genre": _text_filter(genre),
        "year_min": _whole_number(year_min, "From year"),
        "year_max": _whole_number(year_max, "To year"),
        "artist": _text_filter(artist),
        "has_no_tags": bool(has_no_tags),
        "stale_days": _whole_number(stale_days, "Stale days"),
    }


def tag_filter_signature(filters: Mapping[str, Any]) -> str:
    """Stable JSON of normalised filters (the preview signature).

    ``json.loads`` of it gives back the ``get_tracks_by_filter`` kwargs, so a start
    can run exactly the previewed filters.
    """
    return json.dumps(dict(filters), sort_keys=True, separators=(",", ":"))


def current_filter_signature(
    genre: Any,
    year_min: Any,
    year_max: Any,
    artist: Any,
    has_no_tags: Any,
    stale_days: Any,
) -> str:
    """Signature of the filters as they are now.

    Filters that don't validate get a marker that never equals a stored preview
    signature (a preview only stores a signature for valid filters).
    """
    try:
        return tag_filter_signature(
            normalize_tag_filters(genre, year_min, year_max, artist, has_no_tags, stale_days)
        )
    except ValueError:
        raw = [genre, year_min, year_max, artist, bool(has_no_tags), stale_days]
        return "!invalid:" + json.dumps([str(v) for v in raw])


def _has_tags(row: Any) -> bool:
    """True when a ``get_tracks_by_filter`` row has tags (the SQL definition:
    ``tags`` is neither NULL nor '', as ``count_untagged_tracks`` counts)."""
    tags = row.get("tags") if isinstance(row, Mapping) else getattr(row, "tags", None)
    return tags is not None and tags != ""


def _as_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def recently_tagged_row(track: Mapping[str, Any]) -> dict[str, str]:
    """A "Recently tagged" row for the page: every value as a string, plus ``tags_display``.

    ``tags`` stays exactly as stored (the Edit button prefills the editor with it);
    ``tags_display`` is the same list spaced as ``"a, b"`` so a long list can wrap
    (webui-zoom-and-narrow-fit D8). Rows from ``get_recently_tagged_tracks`` always
    carry ``tags``; a row without that column gets no display key.
    """
    row = _str_dict(dict(track))
    if "tags" in row:
        row["tags_display"] = format_tags_for_display(row["tags"])
    return row


class PreviewResult(NamedTuple):
    """What a preview found: ``state`` is ``ok`` or ``no_database``; ``matched``
    tracks match the filters and ``tagged`` of them already have tags."""

    state: str
    matched: int = 0
    tagged: int = 0
    stale: int = 0


def count_preview(filters: Mapping[str, Any]) -> PreviewResult:
    """Count the tracks matching normalised ``filters`` and how many already have tags.

    Blocking (settings + SQLite): run it in an executor. Never creates the
    database: a missing one gives ``PreviewResult("no_database")``.
    """
    from plexmix.config.settings import Settings
    from plexmix.database.sqlite_manager import SQLiteManager

    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()
    if not db_path.exists():
        return PreviewResult("no_database")
    db = SQLiteManager(str(db_path))
    db.connect()
    try:
        tracks = db.get_tracks_by_filter(**dict(filters))
        stale = db.count_stale_tagged_tracks(_STALE_BADGE_DAYS)
    finally:
        db.close()
    tagged = sum(1 for track in tracks if _has_tags(track))
    return PreviewResult("ok", matched=len(tracks), tagged=tagged, stale=_as_count(stale))


class TagPreflight(NamedTuple):
    """What ``request_tagging`` found before asking for confirmation.

    ``state``: ``ready`` (``scope`` holds the confirmation copy), ``running`` (a
    tagging job already runs) or ``blocked`` (the AI provider can't run; ``notice``
    says why and links to the fix).
    """

    state: str
    scope: dict[str, str] = {}
    notice: dict[str, str] = {}


def tagging_preflight(count: int, tagged: int) -> TagPreflight:
    """Build the tagging confirmation copy for a previewed set.

    Blocking (settings, SQLite counts, ``find_spec``): run it in an executor. The
    AI provider's readiness comes from :func:`readiness.evaluate` over local facts,
    so a missing key, endpoint or local-model package opens nothing and explains
    the fix instead. The copy names the provider and model, says whether track
    metadata leaves the machine, how many existing tag sets are replaced and, when
    some tracks are untagged, offers "Tag only the N untagged tracks".
    """
    from plexmix.config.settings import Settings
    from plexmix.services import readiness as rd

    if task_store.is_running("tagging"):
        return TagPreflight("running", notice=_notice("warning", TAGGING_RUNNING_MESSAGE))
    settings = Settings.load_from_file()
    facts = rd.collect_facts(settings)
    ai = rd.evaluate(settings, facts)["ai"]
    if ai.blocking:
        return TagPreflight(
            "blocked",
            notice=_notice(
                "error",
                ai.body,
                title=ai.title,
                action_label=ai.fix_label,
                action_href=ai.action_href,
            ),
        )
    # Where metadata goes: a Local or Custom endpoint can be another machine.
    destination = rd.data_destination(ai)
    scope = rd.describe_job_scope(
        "tagging",
        count,
        ai.label,
        ai.model,
        bool(destination),
        {"replace_count": tagged, "sends_to": destination},
    )
    full = dict(EMPTY_SCOPE)
    full.update(scope)
    full.update(
        provider_label=ai.label,
        model=ai.model,
        count=format_count(count),
        untagged=str(max(count - tagged, 0)),
    )
    return TagPreflight("ready", scope=full)


class _TaggingAborted(Exception):
    """A tagging run that stopped before calling the provider (plain message)."""


def _digits_or_blank(value: Any) -> str:
    """Return ``value`` when it is a whole number of days (ASCII digits only), else ``""``.

    Surrounding whitespace is ignored. Anything else (``"abc"``, ``"-3"``,
    ``"1.5"``, ``"1e3"``, Unicode digits such as ``"²"`` that ``int()`` rejects)
    becomes ``""``, which means "no stale-days filter".
    """
    text = str(value if value is not None else "").strip()
    if text and text.isascii() and text.isdigit():
        return text
    return ""


def _focus_edit_button(track_id: str) -> Optional["EventSpec"]:
    """Return a ``call_script`` event that focuses the track's Edit button.

    Used after an inline tag edit is saved or cancelled so keyboard focus lands
    back on the row instead of falling to ``<body>`` when the editor unmounts.
    Returns None when there is no track id or the id can't form a safe element id.
    """
    if not track_id:
        return None
    try:
        script = focus_by_id_script(f"{EDIT_BUTTON_ID_PREFIX}{track_id}")
    except ValueError:
        logger.debug("Skipping focus return: unusable track id %r", track_id)
        return None
    return rx.call_script(script)


def _focus_after_save(
    track_id: str, old_ids: list[str], rows: list[dict[str, str]]
) -> Optional["EventSpec"]:
    """Return the focus event after an inline edit is saved and the list reloaded.

    The edited track's Edit button when it is still listed. A track whose tags
    were cleared drops out of "Recently tagged" (only tagged tracks are listed),
    so focus goes to the row that took its place (or the new last row), or to the
    page's main heading when the list is now empty. ``old_ids`` are the listed
    track ids before the save; a track that was not listed keeps the plain target.
    """
    new_ids = [str(row.get("id", "")) for row in rows]
    if track_id in new_ids or track_id not in old_ids:
        return _focus_edit_button(track_id)
    if new_ids:
        successor = new_ids[min(old_ids.index(track_id), len(new_ids) - 1)]
        return _focus_edit_button(successor)
    return rx.call_script(focus_by_id_script("main-heading"))


def _ai_secrets(settings: Any) -> list[str]:
    """The AI key a tagging run uses (none when it can't be resolved)."""
    try:
        from plexmix.services.readiness import resolve_secret

        secret = resolve_secret("ai", settings)
    except Exception:
        return []
    return [secret] if isinstance(secret, str) and secret else []


def _redact(text: str, secrets: list[str]) -> str:
    """``text`` with every known secret replaced by ``[redacted]`` (for logs)."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


class TaggingState(AppState):
    # Filter criteria
    genre_filter: str = ""
    year_min: str = ""
    year_max: str = ""
    artist_filter: str = ""
    has_no_tags: bool = False
    stale_days: str = ""

    # Preview (D10). ``preview_count``/``preview_tagged_count`` belong to the
    # filters whose signature is ``_preview_signature`` (server-only JSON of the
    # normalised filters, "" when there is no usable preview). The page shows the
    # count only while ``preview_is_current``.
    preview_count: int = 0
    preview_tagged_count: int = 0
    preview_running: bool = False
    _preview_signature: str = ""
    _preview_seq: int = 0

    # Job progress (``tagging_message`` is for job progress only).
    is_tagging: bool = False
    tagging_progress: int = 0
    current_batch: int = 0
    total_batches: int = 0
    tags_generated_count: int = 0
    estimated_time_remaining: int = 0
    tagging_message: str = ""

    # Preview, validation and start problems; rendered outside the job card.
    tagging_notice: dict[str, str] = dict(EMPTY_NOTICE)

    # Stale tag stats
    stale_track_count: int = 0

    # Scope confirmation (D7/D10): replaces the unused tag-all-untagged dialog.
    tag_confirm_open: bool = False
    tag_confirm_checking: bool = False
    tag_scope: dict[str, str] = dict(EMPTY_SCOPE)

    # Recently tagged tracks (``recent_loaded`` False → loading placeholder).
    recently_tagged_tracks: list[dict[str, str]] = []
    recent_loaded: bool = False
    recent_error: str = ""

    # For inline editing
    editing_track_id: str = ""
    edit_tags: str = ""
    edit_environments: str = ""
    edit_instruments: str = ""

    @rx.event
    def on_load(self) -> Any:
        if not self.check_auth():
            self.is_page_loading = False
            return None
        self.load_app_data()
        self.load_recently_tagged()
        self._load_stale_count()
        self.poll_task_progress()  # Recover in-progress tagging from TaskStore
        self.is_page_loading = False
        # A revisit re-counts an earlier preview, so the count never describes an
        # older library (a sync or tagging run may have changed it meanwhile).
        if self._preview_signature and not self.preview_running:
            return TaggingState.preview_selection
        return None

    def poll_task_progress(self) -> Any:
        """Client-initiated poll: read TaskStore -> update state vars."""
        entry = task_store.get("tagging")
        if entry is None:
            return None
        if entry.status == "running":
            self.is_tagging = True
            self.tagging_progress = entry.progress
            self.tagging_message = entry.message
            self.current_batch = int(entry.extra.get("current_batch", 0))
            self.total_batches = int(entry.extra.get("total_batches", 0))
            self.tags_generated_count = int(entry.extra.get("tags_generated_count", 0))
            self.estimated_time_remaining = int(entry.extra.get("estimated_time_remaining", 0))
            return None
        if not self.is_tagging:
            return None
        self.is_tagging = False
        self.tagging_message = ""
        if entry.status == "completed":
            self.tagging_progress = 100
            self.load_recently_tagged()
            count = entry.extra.get("tags_generated_count", 0)
            event: Any = rx.toast.success(f"Successfully tagged {count} tracks!")
        elif entry.status == "failed":
            msg = entry.message or "Tagging failed"
            # The failure stays next to Tag tracks until dismissed or a new action.
            href = ""
            if entry.extra.get("provider_failure"):
                from plexmix.services.readiness import SETTINGS_SECTION_HREFS

                href = SETTINGS_SECTION_HREFS["ai"]
            self.tagging_notice = _notice(
                "error",
                msg,
                title=TAGGING_FAILED_TITLE,
                action_label="Open AI settings",
                action_href=href,
                source="job",
            )
            self.load_recently_tagged()  # tags saved before the failure
            event = rx.toast.error(f"Error during tagging: {msg}")
        elif entry.status == "cancelled":
            self.load_recently_tagged()
            event = rx.toast.warning("Tagging cancelled")
        else:
            return None
        # The run changed which tracks have tags: count the previewed filters again.
        if self._preview_signature:
            return [event, TaggingState.preview_selection]
        return event

    def _load_stale_count(self) -> None:
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            if db_path.exists():
                with SQLiteManager(str(db_path)) as db:
                    self.stale_track_count = db.count_stale_tagged_tracks(_STALE_BADGE_DAYS)
        except Exception as e:
            logger.error("Error loading stale track count: %s", e)

    def load_recently_tagged(self) -> None:
        """Load the "Recently tagged" list; ``recent_loaded`` ends the placeholder."""
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if db_path.exists():
                db = SQLiteManager(str(db_path))
                db.connect()
                try:
                    raw_tracks = db.get_recently_tagged_tracks(limit=100)
                finally:
                    db.close()
                self.recently_tagged_tracks = [recently_tagged_row(t) for t in raw_tracks]
            else:
                self.recently_tagged_tracks = []
            self.recent_error = ""
        except Exception as e:
            logger.error("Error loading recently tagged tracks: %s", safe_error_text(e))
            self.recent_error = RECENT_LOAD_ERROR
        self.recent_loaded = True

    def retry_recently_tagged(self) -> None:
        """Retry after the "Recently tagged" list failed to load."""
        self.load_recently_tagged()

    def set_genre_filter(self, value: str) -> None:
        """Commit the Genre filter field; an unchanged value is a no-op."""
        if value == self.genre_filter:
            return
        self.genre_filter = value

    def set_year_range(self, year_min: str, year_max: str):
        self.year_min = year_min
        self.year_max = year_max

    def set_year_min(self, value: str):
        self.year_min = value

    def set_year_max(self, value: str):
        self.year_max = value

    def set_artist_filter(self, value: str) -> None:
        """Commit the artist filter field; an unchanged value is a no-op."""
        if value == self.artist_filter:
            return
        self.artist_filter = value

    def toggle_has_no_tags(self):
        self.has_no_tags = not self.has_no_tags

    def set_stale_days(self, value: str) -> None:
        """Commit the Stale days field, keeping only a whole number of days.

        Anything that is not ASCII digits (after trimming) is stored as ``""``,
        meaning no stale-days filter, so ``int(self.stale_days)`` in Preview and
        tagging can never raise. An unchanged value is a no-op.
        """
        cleaned = _digits_or_blank(value)
        if cleaned == self.stale_days:
            return
        self.stale_days = cleaned

    @rx.event
    def apply_filter_form(self, form_data: dict[str, Any]) -> Any:
        """Apply the submitted filter bar (Enter in a filter, or Preview) and preview it.

        The values come from ``form_data`` (field names ``artist``, ``genre`` and
        ``stale_days``), never from state, so a debounced commit still in flight
        cannot make the preview use stale filters. They go through the existing
        setters; a key missing from ``form_data`` leaves that filter unchanged.

        Returns ``TaggingState.preview_selection``.
        """
        if "artist" in form_data:
            self.set_artist_filter(str(form_data["artist"] or ""))
        if "genre" in form_data:
            self.set_genre_filter(str(form_data["genre"] or ""))
        if "stale_days" in form_data:
            self.set_stale_days(str(form_data["stale_days"] or ""))
        return TaggingState.preview_selection

    # ── Preview signature (D10) ───────────────────────────────────────────

    def _preview_matches_filters(self) -> bool:
        """True when a preview exists and was made for the filters currently set."""
        signature = self._preview_signature
        return bool(signature) and signature == current_filter_signature(
            self.genre_filter,
            self.year_min,
            self.year_max,
            self.artist_filter,
            self.has_no_tags,
            self.stale_days,
        )

    @rx.var(cache=True)
    def preview_is_current(self) -> bool:
        """True when the last preview was made for exactly the filters now set."""
        return self._preview_matches_filters()

    @rx.var(cache=True)
    def preview_state(self) -> str:
        """The count card's state.

        ``running`` (counting), ``current`` (a count for these filters), ``empty``
        (these filters match nothing), ``stale`` (the filters changed since the
        last preview) or ``none`` (nothing previewed yet).
        """
        if self.preview_running:
            return "running"
        if self._preview_matches_filters():
            return "current" if self.preview_count > 0 else "empty"
        if self._preview_signature:
            return "stale"
        return "none"

    @rx.var(cache=True)
    def preview_count_label(self) -> str:
        """The previewed count, grouped ("5,943")."""
        return format_count(self.preview_count)

    @rx.var(cache=True)
    def preview_match_text(self) -> str:
        """The words after the count ("tracks match the current filters")."""
        if self.preview_count == 1:
            return "track matches the current filters"
        return "tracks match the current filters"

    @rx.var(cache=True)
    def preview_tagged_note(self) -> str:
        """How many previewed tracks already have tags ("" when none do)."""
        tagged = self.preview_tagged_count
        if tagged <= 0:
            return ""
        if tagged >= self.preview_count:
            return "all already have tags" if tagged > 1 else "it already has tags"
        return f"{format_count(tagged)} already {'has' if tagged == 1 else 'have'} tags"

    @rx.var(cache=True)
    def can_start_tagging(self) -> bool:
        """Tag tracks is enabled only for a current preview that matched tracks."""
        return (
            not self.is_tagging
            and not self.preview_running
            and self.preview_count > 0
            and self._preview_matches_filters()
        )

    @rx.var(cache=True)
    def tag_block_reason(self) -> str:
        """Why Tag tracks is disabled ("" while it is enabled)."""
        if self.is_tagging:
            return "Tagging is running."
        if self.preview_running:
            return "Wait for the count to finish."
        if self._preview_matches_filters():
            return "" if self.preview_count > 0 else "No tracks match these filters."
        if self._preview_signature:
            return "Filters changed. Preview again before tagging."
        return PREVIEW_FIRST_MESSAGE

    def _start_refusal(self) -> Optional[dict[str, str]]:
        """The notice that refuses a start for the current preview, or None."""
        if self.preview_running:
            return _notice("warning", WAIT_FOR_COUNT_MESSAGE)
        if not self._preview_signature:
            return _notice("warning", PREVIEW_FIRST_MESSAGE)
        if not self._preview_matches_filters():
            return _notice("warning", PREVIEW_AGAIN_MESSAGE)
        if self.preview_count <= 0:
            return _notice("warning", NOTHING_TO_TAG_MESSAGE)
        return None

    @rx.event
    def dismiss_notice(self) -> None:
        """Hide the tagging notice."""
        self.tagging_notice = dict(EMPTY_NOTICE)

    @rx.event(background=True)
    async def preview_selection(self) -> None:
        """Count the tracks matching the current filters (stores the signature).

        The filters are captured and validated (:func:`normalize_tag_filters`)
        when the preview starts; the count, the already-tagged count and that
        filters' signature are stored when it finishes. Problems (invalid
        filters, no synced library, a database error) go to ``tagging_notice``.
        A newer preview supersedes one still counting.
        """
        async with self:
            self._preview_seq += 1
            seq = self._preview_seq
            self._preview_signature = ""
            if self.tagging_notice.get("source") != "job":
                self.tagging_notice = dict(EMPTY_NOTICE)
            try:
                filters = normalize_tag_filters(
                    self.genre_filter,
                    self.year_min,
                    self.year_max,
                    self.artist_filter,
                    self.has_no_tags,
                    self.stale_days,
                )
            except ValueError as e:
                self.preview_running = False
                self.tagging_notice = _notice("error", str(e), source="preview")
                return
            self.preview_running = True

        loop = asyncio.get_running_loop()
        error: Optional[BaseException] = None
        result = PreviewResult("error")
        try:
            result = await loop.run_in_executor(None, count_preview, filters)
        except Exception as e:
            logger.error("Error counting tracks for tagging: %s", safe_error_text(e))
            error = e

        async with self:
            if seq != self._preview_seq:
                return  # superseded by a newer preview
            self.preview_running = False
            if error is not None:
                self.tagging_notice = _notice(
                    "error", friendly_error(error), title=PREVIEW_ERROR_TITLE, source="preview"
                )
                return
            no_filters = not filters["has_no_tags"] and all(
                filters[key] is None
                for key in ("genre", "year_min", "year_max", "artist", "stale_days")
            )
            if result.state == "no_database" or (result.matched == 0 and no_filters):
                # Nothing synced yet: say so and offer the sync, instead of a 0 count.
                self._show_not_synced()
                return
            self.preview_count = result.matched
            self.preview_tagged_count = result.tagged
            self.stale_track_count = result.stale
            self._preview_signature = tag_filter_signature(filters)
            if not self.is_tagging:
                # Plain-text summary (the job card shows tagging_message only
                # while a job runs, so this never replaces job progress).
                verb = "matches" if result.matched == 1 else "match"
                self.tagging_message = f"{pluralize(result.matched, 'track')} {verb} your filters"

    def _show_not_synced(self) -> None:
        from plexmix.services import readiness as rd

        copy = rd.describe("library", rd.Cause.NOT_SYNCED)
        self.preview_count = 0
        self.preview_tagged_count = 0
        self.tagging_notice = _notice(
            "error",
            NOT_SYNCED_TEXT,
            title=copy["title"],
            action_label=SYNC_ACTION_LABEL,
            action_href=rd.LIBRARY_HREF,
            source="preview",
        )

    # ── Scope confirmation (D7/D10) ────────────────────────────────────────

    @rx.event(background=True)
    async def request_tagging(self) -> None:
        """Tag tracks: open the scope confirmation for the current preview.

        Refuses (with a notice) unless a preview for the current filters matched
        tracks. Builds the copy off the event loop (count, existing tag sets that
        would be replaced, AI provider + model, whether metadata leaves the
        machine, "Tag only the N untagged tracks"). When the AI provider can't run
        (no key, endpoint or local-model package) it opens nothing and the notice
        explains the fix. A second activation while it is checking is ignored.
        """
        async with self:
            if not self.access_allowed():
                return
            if self.tag_confirm_checking or self.tag_confirm_open:
                return
            if self.is_tagging:
                self.tagging_notice = _notice("warning", TAGGING_RUNNING_MESSAGE)
                return
            refusal = self._start_refusal()
            if refusal is not None:
                self.tagging_notice = refusal
                return
            self.tag_confirm_checking = True
            self.tagging_notice = dict(EMPTY_NOTICE)
            signature = self._preview_signature
            count = self.preview_count
            tagged = self.preview_tagged_count

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, tagging_preflight, count, tagged)
        except Exception as e:
            logger.error("Could not prepare the tagging confirmation: %s", safe_error_text(e))
            result = TagPreflight(
                "error", notice=_notice("error", friendly_error(e), title=TAGGING_FAILED_TITLE)
            )

        async with self:
            self.tag_confirm_checking = False
            if result.state != "ready":
                self.tagging_notice = dict(result.notice)
                return
            if signature != self._preview_signature or not self._preview_matches_filters():
                # The filters changed while the copy was built.
                self.tagging_notice = _notice("warning", PREVIEW_AGAIN_MESSAGE)
                return
            self.tag_scope = dict(result.scope)
            self.tag_confirm_open = True

    @rx.event
    def set_tag_confirm_open(self, is_open: bool) -> None:
        """``on_open_change`` of the confirmation: close on False (idempotent).

        True is a no-op: the dialog only opens through :meth:`request_tagging`.
        The scope copy is kept so the closing animation never shows blank text.
        """
        if not is_open:
            self.tag_confirm_open = False

    @rx.event
    def cancel_tag_confirm(self) -> None:
        """Close the confirmation without starting anything."""
        self.tag_confirm_open = False

    @rx.event
    def confirm_tagging(self) -> Any:
        """Confirm: close the dialog and tag the whole previewed set.

        A plain button (not ``alert_dialog.action``) calls this, so it closes the
        dialog itself; a repeated click finds it closed and starts nothing.
        """
        pending = self.tag_confirm_open
        self.tag_confirm_open = False
        if not pending:
            return None
        return TaggingState.start_tagging

    @rx.event
    def tag_all_untagged(self) -> Any:
        """The confirmation's secondary action: "Tag only the N untagged tracks".

        Folded from the old tag-all-untagged dialog: it keeps the previewed
        filters and adds ``has_no_tags=True`` for this run only, so no filter
        field is reset (nothing needs ``rx.set_value``) and the preview stays
        current. Closes the dialog; starts nothing when it was already closed.
        """
        if not self.access_allowed():
            self.tag_confirm_open = False
            return None
        pending = self.tag_confirm_open and self.preview_count > self.preview_tagged_count
        self.tag_confirm_open = False
        if not pending:
            return None
        return TaggingState.start_untagged_tagging

    @rx.event(background=True)
    async def start_tagging(self, only_untagged: bool = False) -> None:
        """Tag exactly the previewed set (``only_untagged``: only its untagged tracks).

        The server re-checks the preview: a start whose current filters differ
        from the previewed ones (or with no usable preview) is refused with a
        notice and never starts the job.
        """
        await self._start_tagging(bool(only_untagged))

    @rx.event(background=True)
    async def start_untagged_tagging(self) -> None:
        """Tag only the untagged tracks of the previewed set."""
        await self._start_tagging(True)

    async def _start_tagging(self, only_untagged: bool) -> None:
        async with self:
            if not self.access_allowed():
                return
            self.tag_confirm_open = False
            refusal = self._start_refusal()
            if refusal is not None:
                self.tagging_notice = refusal
                return
            filters: dict[str, Any] = json.loads(self._preview_signature)
            if only_untagged:
                if self.preview_count - self.preview_tagged_count <= 0:
                    self.tagging_notice = _notice("warning", NO_UNTAGGED_MESSAGE)
                    return
                filters["has_no_tags"] = True

            self.tagging_notice = dict(EMPTY_NOTICE)
            self.is_tagging = True
            self.tagging_progress = 0
            self.current_batch = 0
            self.tags_generated_count = 0
            self.tagging_message = "Starting tag generation..."

        cancel_event = task_store.start("tagging", message="Starting tag generation...")
        if cancel_event is None:
            # Already running — polling will show existing task's progress
            return

        # The AI key this run uses, so failure text (log and message) can be redacted.
        secrets: list[str] = []
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.ai.tag_generator import TagGenerator, clean_tag_list

            # Settings, the track query, the provider (and, for builtin local
            # mode, the model), generation and saving all run in the executor so
            # nothing blocks the event loop.
            def run_tagging() -> int:
                from plexmix.services.providers import build_ai_provider

                settings = Settings.load_from_file()
                secrets.extend(_ai_secrets(settings))
                db_path = settings.database.get_db_path()
                if not db_path.exists():
                    raise _TaggingAborted(NO_DATABASE_MESSAGE)

                db = SQLiteManager(str(db_path))
                db.connect()
                try:
                    # Exactly the previewed filters (plus has_no_tags for "only untagged").
                    tracks = db.get_tracks_by_filter(**filters)
                    if not tracks:
                        raise _TaggingAborted(NO_TRACKS_LEFT_MESSAGE)

                    batch_size = TAG_BATCH_SIZE
                    total_batches = (len(tracks) + batch_size - 1) // batch_size
                    task_store.update(
                        "tagging",
                        message=f"Tagging {len(tracks)} tracks in {total_batches} batches...",
                        extra={"total_batches": total_batches},
                    )
                    start_time = time.time()

                    def progress_callback(batch_num: int, total: int, tracks_tagged: int) -> None:
                        if cancel_event.is_set():
                            return
                        elapsed = time.time() - start_time
                        if tracks_tagged > 0:
                            time_per_track = elapsed / tracks_tagged
                            remaining_tracks = len(tracks) - tracks_tagged
                            estimated_remaining = int(time_per_track * remaining_tracks)
                        else:
                            estimated_remaining = 0

                        task_store.update(
                            "tagging",
                            progress=int((tracks_tagged / len(tracks) * 100)) if tracks else 0,
                            message=(
                                f"Processing batch {batch_num}/{total} - "
                                f"{tracks_tagged} tracks tagged"
                            ),
                            extra={
                                "current_batch": batch_num,
                                "total_batches": total,
                                "tags_generated_count": tracks_tagged,
                                "estimated_time_remaining": estimated_remaining,
                            },
                        )

                    # A real tagging run verifies (or fails) the AI provider for readiness.
                    ai_fp = current_fingerprint_safe("ai", settings)
                    ai_provider = build_ai_provider(settings, silent=True)
                    if ai_provider is None:
                        error = FatalProviderError(
                            "AI provider unavailable",
                            user_message="Error initializing AI provider — check settings.",
                        )
                        cause, message = tagging_failure_cause(error)
                        record_readiness("ai", "failing", cause, message=message, fingerprint=ai_fp)
                        raise error
                    tag_generator = TagGenerator(ai_provider)
                    fatal = None
                    try:
                        results = tag_generator.generate_tags_batch(
                            tracks,
                            batch_size=batch_size,
                            progress_callback=progress_callback,
                            cancel_event=cancel_event,
                        )
                    except FatalProviderError as e:
                        # Save whatever succeeded, then surface the reason upstream.
                        results = e.partial_results
                        fatal = e

                    saved_count = 0
                    for track_id, tag_data in results.items():
                        # Only texts with content count (``clean_tag_list``), also for a
                        # result that did not come through the parser. A column is
                        # written only when its list has content: None leaves it as it
                        # is, so a reply without instruments never blanks a track's.
                        tags = clean_tag_list(tag_data.get("tags"))
                        environments = clean_tag_list(tag_data.get("environments"))
                        instruments = clean_tag_list(tag_data.get("instruments"))
                        if tags or environments or instruments:
                            db.update_track_tags(
                                track_id,
                                tags=",".join(tags) if tags else None,
                                environments=",".join(environments) if environments else None,
                                instruments=",".join(instruments) if instruments else None,
                            )
                            saved_count += 1
                finally:
                    db.close()

                if fatal:
                    cause, message = tagging_failure_cause(fatal)
                    record_readiness("ai", "failing", cause, message=message, fingerprint=ai_fp)
                    raise fatal
                if saved_count:
                    record_readiness("ai", "ready", fingerprint=ai_fp)
                return saved_count

            loop = asyncio.get_running_loop()
            saved_count = await loop.run_in_executor(None, run_tagging)

            task_store.update("tagging", extra={"tags_generated_count": saved_count})
            if cancel_event is not None and cancel_event.is_set():
                task_store.complete("tagging", status="cancelled")
            else:
                task_store.complete("tagging")

        except FatalProviderError as e:
            # Marks the failure as the provider's, so the notice links to AI settings.
            task_store.update("tagging", extra={"provider_failure": True})
            task_store.complete("tagging", status="failed", message=e.user_message)
        except _TaggingAborted as e:
            task_store.complete("tagging", status="failed", message=str(e))
        except Exception as e:
            logger.error("Tagging failed: %s", _redact(safe_error_text(e), secrets))
            task_store.complete("tagging", status="failed", message=friendly_error(e, secrets))

    def cancel_tagging(self):
        task_store.cancel("tagging")
        self.tagging_message = "Cancelling tagging..."

    def start_edit_tag(
        self,
        track_id: str,
        tags: str,
        environments: str,
        instruments: str,
    ):
        self.editing_track_id = track_id
        self.edit_tags = tags
        self.edit_environments = environments
        self.edit_instruments = instruments

    def cancel_edit(self) -> Optional["EventSpec"]:
        """Close the inline editor without saving and return focus to the row's Edit button."""
        track_id = self.editing_track_id  # capture before it is cleared
        self.editing_track_id = ""
        self.edit_tags = ""
        self.edit_environments = ""
        self.edit_instruments = ""
        return _focus_edit_button(track_id)

    def submit_tag_edit(self, form_data: dict[str, Any]) -> Any:
        """Save the inline editor from its form (Enter in any field, or the Save button).

        Copies the values currently shown in the three fields (``name=tags``,
        ``environments``, ``instruments``) into the edit state, so the save uses
        what the user sees even if the field bindings lag, then chains to
        :meth:`save_tag_edit`. A field missing from ``form_data`` keeps its state value.
        """
        if "tags" in form_data:
            self.edit_tags = str(form_data["tags"] or "")
        if "environments" in form_data:
            self.edit_environments = str(form_data["environments"] or "")
        if "instruments" in form_data:
            self.edit_instruments = str(form_data["instruments"] or "")
        return TaggingState.save_tag_edit

    def set_edit_tags(self, value: str):
        self.edit_tags = value

    def set_edit_environments(self, value: str):
        self.edit_environments = value

    def set_edit_instruments(self, value: str):
        self.edit_instruments = value

    @rx.event(background=True)
    async def save_tag_edit(self) -> AsyncGenerator[Any, None]:
        async with self:
            if not self.editing_track_id:
                return

            editing_id = self.editing_track_id
            track_id = int(editing_id)
            listed_ids = [str(t.get("id", "")) for t in self.recently_tagged_tracks]
            # Capture the edit fields inside the context — reading them outside
            # `async with self` in a background event is unsafe.
            tags = self.edit_tags
            environments = self.edit_environments
            instruments = self.edit_instruments

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            db = SQLiteManager(str(db_path))
            db.connect()

            db.update_track_tags(
                track_id,
                tags=tags,
                environments=environments,
                instruments=instruments,
            )

            db.close()

            # Clear the editor and reload — all state access inside the context,
            # or Reflex raises ImmutableStateError (which showed an error toast and
            # left the inline editor stuck).
            async with self:
                self.editing_track_id = ""
                self.edit_tags = ""
                self.edit_environments = ""
                self.edit_instruments = ""
                self.load_recently_tagged()
                # The editor has unmounted: put focus back on this row's Edit button
                # (or its successor, if clearing the tags removed the row).
                focus = _focus_after_save(editing_id, listed_ids, self.recently_tagged_tracks)

            if focus is not None:
                yield focus
            yield rx.toast.success("Tags updated successfully!")

        except Exception as e:
            yield rx.toast.error(f"Error saving tags: {str(e)}")
