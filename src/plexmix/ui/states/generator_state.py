import reflex as rx
import asyncio
import logging
from typing import TYPE_CHECKING, Any, Mapping, Optional, cast
from plexmix.config.constants import GENERATION_LOG_MAX
from plexmix.ui.job_manager import jobs
from plexmix.ui.states.app_state import (
    AppState,
    current_fingerprint_safe,
    provider_failure_cause,
    record_readiness,
)


from plexmix.ui.utils.errors import friendly_error
from plexmix.ui.utils.helpers import format_count, pluralize, safe_error_text
from plexmix.ui.utils.helpers import str_dict as _str_dict
from plexmix.ui.utils.playlist_identity import cover_seed
from plexmix.ui.utils.playlist_identity import cover_variant as _cover_variant

if TYPE_CHECKING:
    from reflex.event import EventSpec

logger = logging.getLogger(__name__)

# The Generator prompt holds at most this many characters. The textarea's
# ``maxlength`` stops typing and pasting there; every value the application sets
# (a rerun, a template, a palette prefill) is cut to it, so what is shown is what
# is sent.
PROMPT_MAX_CHARS = 280

# Element ids of the client-owned prompt and Genre focus fields (pages/generator.py
# renders them; webui-keyboard-and-semantics labels them by these ids). A handler
# that changes ``mood_query``/``genre_filter`` for any reason other than that
# field's own commit returns ``rx.set_value(<id>, <value>)`` (see
# ``plexmix.ui.components.inputs``).
PROMPT_INPUT_ID = "gen-prompt"
GENRE_INPUT_ID = "gen-genre"

# Shown under the prompt when a blank or whitespace-only prompt is submitted.
BLANK_PROMPT_ERROR = "Describe a vibe first."

# Toast for a prompt the application would set (palette prefill, History rerun,
# template) while a generation runs: the running generation keeps its prompt, so
# its results and the playlist saved from them are never relabelled.
GENERATION_BUSY_MESSAGE = "A playlist is being generated. Try again when it finishes."

# Run on the client just before a generation starts (returned by ``use_vibe`` ahead of
# ``generate_playlist``; pages/generator.py runs it first in the prompt form's submit
# chain). A generation that fails fast arrives in the same React render as its start,
# so the thinking stage never mounts, the idle hero never remounts and its on_mount
# never runs: this chases the error callout instead (forced: the user just asked for
# this result). The stale callout of an earlier failure is excluded while polling
# (accepted only if it is still there when the ~1 s poll expires, i.e. re-used in place
# for the new error); a stage change starts a newer pmFocus.when() that supersedes it.
FAILURE_FOCUS_SCRIPT = (
    "window.pmFocus&&pmFocus.when('#gen-error',"
    "{force:true,exclude:document.getElementById('gen-error')})"
)

# ── Generation failures (design D9) ───────────────────────────────────
# ``generation_error`` always holds every key below (all strings, "" when absent), so
# the page can test ``generation_error["title"] != ""``. ``component``/``cause`` name
# the readiness component and ``readiness.Cause`` value the failure came from, so
# on_load can drop the callout once readiness no longer reports that cause.
# ``component`` is "generation" for failures that are not a readiness component
# (no matching tracks, an unexpected error). ``tone`` is "error" or "warning".
GENERATION_ERROR_KEYS: tuple[str, ...] = (
    "title",
    "body",
    "action_label",
    "action_href",
    "secondary_label",
    "secondary_href",
    "component",
    "cause",
    "tone",
)

NO_MATCH_TITLE = "No tracks matched this prompt"
NO_MATCH_BODY = "Nothing in your library matched this prompt. Try a broader description."
NO_MATCH_FILTERED_BODY = (
    "Nothing in your library matched this prompt with the current filters. Remove some "
    "filters or try a broader description."
)


def empty_generation_error() -> dict[str, str]:
    """A ``generation_error`` value that shows no callout."""
    return {key: "" for key in GENERATION_ERROR_KEYS}


def generation_error_for(
    component: str, cause: str, copy: Mapping[str, str], tone: str = "error"
) -> dict[str, str]:
    """Build a ``generation_error`` from ``readiness.describe`` copy (title, body, actions)."""
    error = empty_generation_error()
    for key in ("title", "body", "action_label", "action_href", "secondary_label"):
        error[key] = str(copy.get(key, "") or "")
    error["secondary_href"] = str(copy.get("secondary_href", "") or "")
    error["component"] = component
    error["cause"] = cause
    error["tone"] = tone
    return error


# ── Constraint chips (design D9) ──────────────────────────────────────
# Filters a History rerun or a template can carry that have no control in the
# Options panel. Each is shown as a removable chip while it is applied.
CONSTRAINT_KEYS: tuple[str, ...] = (
    "year",
    "tempo",
    "energy",
    "key",
    "danceability",
    "avoid_recent",
)


def _parse_int(value: Any) -> Optional[int]:
    """``playlist_service.safe_int`` semantics: the value generation would apply."""
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _parse_float(value: Any) -> Optional[float]:
    """``playlist_service.safe_float`` semantics: the value generation would apply."""
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def _num(value: float) -> str:
    """``90.0`` → ``"90"``, ``92.5`` → ``"92.5"``."""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _range_value(low: Optional[str], high: Optional[str]) -> str:
    if low and high:
        return f"{low}–{high}"
    if low:
        return f"≥ {low}"
    return f"≤ {high}"


def constraint_chips(
    *,
    year_min: str = "",
    year_max: str = "",
    tempo_min: str = "",
    tempo_max: str = "",
    energy_level: str = "",
    key_filter: str = "",
    danceability_min: str = "",
    avoid_recent: int = 0,
) -> list[dict[str, str]]:
    """The applied filters without an Options control, as ``{key, name, value, label}``.

    Mirrors what ``generate_playlist`` passes on (a value generation would ignore
    because it doesn't parse is not a constraint). ``label`` is ``name`` and
    ``value`` joined, e.g. ``"Tempo 90–120"``.
    """
    chips: list[dict[str, str]] = []

    def add(key: str, name: str, value: str) -> None:
        chips.append({"key": key, "name": name, "value": value, "label": f"{name} {value}"})

    y_lo, y_hi = _parse_int(year_min), _parse_int(year_max)
    if y_lo is not None or y_hi is not None:
        add(
            "year",
            "Year",
            _range_value(
                str(y_lo) if y_lo is not None else None, str(y_hi) if y_hi is not None else None
            ),
        )
    t_lo, t_hi = _parse_float(tempo_min), _parse_float(tempo_max)
    if t_lo is not None or t_hi is not None:
        add(
            "tempo",
            "Tempo",
            _range_value(
                _num(t_lo) if t_lo is not None else None, _num(t_hi) if t_hi is not None else None
            ),
        )
    if energy_level:
        add("energy", "Energy", str(energy_level))
    if key_filter:
        add("key", "Key", str(key_filter))
    dance = _parse_float(danceability_min)
    if dance is not None:
        add("danceability", "Danceability", f"≥ {_num(dance)}")
    if avoid_recent and avoid_recent > 0:
        add("avoid_recent", "Skip tracks from last", pluralize(avoid_recent, "playlist"))
    return chips


# ── Readiness line (design D9) ────────────────────────────────────────
GENERATION_STATUS_KEYS: tuple[str, ...] = (
    "loaded",
    "tracks",
    "provider",
    "model",
    "model_dim",
    "privacy",
    "state",
    "tone",
    "blocked",
    "kind",
    "issue",
    "component",
    "cause",
    "title",
    "body",
    "action_label",
    "action_href",
    "secondary_label",
    "secondary_href",
)


def generation_status(
    readiness: Mapping[str, Mapping[str, str]],
    *,
    loaded: bool,
    total: int,
    embedded: int,
    error: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """What the Generator's readiness line shows, from the ``AppState.readiness`` snapshot.

    Keys (all strings): ``loaded`` ("true" once the library facts are known),
    ``tracks`` ("5,943 tracks"), ``provider`` (embedding provider display name),
    ``model``, ``model_dim`` ("all-MiniLM-L6-v2 (384d)"), ``privacy`` ("prompt stays
    on this machine" / "prompt sent to Gemini" / "prompt sent to embed.example.com":
    the destination the job confirmations name, from ``readiness.data_destination``, so
    a Custom endpoint on a loopback address stays on this machine and a remote one is
    named by its host), ``state`` ("ready", "not ready",
    "last attempt failed"), ``tone`` (``.dot-<tone>``), ``blocked`` ("true" when a
    blocking local cause stops generation), ``kind`` ("blocked", "warning" or ""),
    ``issue`` ("true" when the line shows the cause row), the cause's ``component``
    and ``cause``, and its ``describe()`` copy: ``title``, ``body``, ``action_label``,
    ``action_href``, ``secondary_label``, ``secondary_href``.

    Only blocking causes (``readiness.generation_blockers``) block. Otherwise the first
    failing generation component (a failure recorded by an earlier run, such as a
    timeout) is a warning. The cause row is left out while the error callout shows
    the same component and cause, so the two never repeat each other.
    """
    from plexmix.services import readiness as rd

    out = {key: "" for key in GENERATION_STATUS_KEYS}
    out.update(loaded="false", blocked="false", issue="false")
    status = readiness or {}
    emb = status.get("embeddings") or {}
    if not loaded or not emb:
        return out
    out["loaded"] = "true"

    if 0 < embedded < total:
        out["tracks"] = f"{format_count(embedded)} of {pluralize(total, 'track')} searchable"
    else:
        out["tracks"] = pluralize(total, "track")
    label = emb.get("label", "")
    model = emb.get("model", "")
    dim = emb.get("detail", "")
    out["provider"] = label
    out["model"] = model
    out["model_dim"] = f"{model} ({dim})" if model and dim else model
    # Same destination logic as the job confirmations ("" = stays on this machine).
    destination = rd.data_destination(emb)
    if destination:
        out["privacy"] = f"prompt sent to {destination}"
    elif emb.get("provider", "") or label:
        out["privacy"] = "prompt stays on this machine"

    blockers = rd.generation_blockers(status)
    component = ""
    if blockers:
        component, kind = blockers[0], "blocked"
        out.update(state="not ready", blocked="true")
    else:
        kind = "warning"
        for name in rd.GENERATION_COMPONENTS:
            if (status.get(name) or {}).get("level") == rd.Level.FAILING.value:
                component = name
                break
    entry = (status.get(component) or {}) if component else {}
    if not entry:
        index = status.get("index") or {}
        if index.get("level") == rd.Level.READY.value:
            out.update(state="ready", tone="success")
        else:
            out.update(state=(index.get("state") or "").lower(), tone=index.get("tone", "idle"))
        return out

    if kind == "blocked":
        out["tone"] = entry.get("tone") or "warning"
    else:
        out.update(state="last attempt failed", tone="warning")
    out.update(
        kind=kind,
        component=component,
        cause=entry.get("cause", ""),
        title=entry.get("title", ""),
        body=entry.get("body", ""),
        action_label=entry.get("fix_label") or entry.get("action_label", ""),
        action_href=entry.get("action_href", ""),
        secondary_label=entry.get("secondary_label", ""),
        secondary_href=entry.get("secondary_href", ""),
    )
    shown = error or {}
    same_as_callout = (
        bool(shown.get("title"))
        and shown.get("component") == component
        and shown.get("cause") == out["cause"]
    )
    out["issue"] = "true" if out["title"] and not same_as_callout else "false"
    return out


class _TrackedEmbeddings:
    """Delegates to an ``EmbeddingGenerator`` and remembers how its calls went.

    ``PlaylistGenerator`` embeds the prompt inside ``generate``; wrapping the provider
    tells a provider failure (recorded in readiness under its cause) apart from any
    other error, and a real successful call (recorded as ready) from a run that never
    reached the provider.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.succeeded = False
        self.error: Optional[BaseException] = None

    def generate_embedding(self, text: str) -> Any:
        try:
            result = self._inner.generate_embedding(text)
        except Exception as e:
            self.error = e
            raise
        self.succeeded = True
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _plex_error_title(error: BaseException, settings: Any) -> str:
    """UI copy for a ``PlexConnectionError`` (its message carries CLI hints)."""
    from plexmix.services.readiness import cause_for_plex_error_code, describe

    code = str(getattr(error, "code", "") or "")
    context: dict[str, Any] = {"code": code}
    if settings is not None:
        context.update(url=settings.plex.url or "", library=settings.plex.library_name or "")
    return describe("plex", cause_for_plex_error_code(code), context)["title"]


class GeneratorState(AppState):
    mood_query: str = ""
    max_tracks: int = 50
    genre_filter: str = ""
    year_min: str = ""
    year_max: str = ""
    include_artists: str = ""
    exclude_artists: str = ""
    candidate_pool_multiplier: int = 25
    shuffle_mode: str = "similarity"
    avoid_recent: int = 0

    tempo_min: str = ""
    tempo_max: str = ""
    energy_level: str = ""
    key_filter: str = ""
    danceability_min: str = ""

    audio_analyzed_count: int = 0

    is_generating: bool = False
    # Separate flag for save operations so saving doesn't reuse is_generating and
    # flip the results view back to the "AI-thinking" stage.
    is_saving: bool = False
    generation_progress: int = 0
    # Progress text for the thinking stage only; failures go to generation_error.
    generation_message: str = ""
    generation_log: list[str] = []
    # The failure callout (see GENERATION_ERROR_KEYS). It stays for the session until
    # dismiss_error, a new generation, or on_load finding its cause resolved.
    generation_error: dict[str, str] = empty_generation_error()

    generated_playlist: list[dict[str, str]] = []
    playlist_name: str = ""
    total_duration_ms: int = 0

    # Bumped on every start/cancel so late callbacks from a superseded generation
    # are dropped. The cancel handle itself lives in ``jobs`` (keyed by client
    # token), never on state: Reflex pickles state and a thread Event cannot be.
    _generation_id: int = 0

    # View state for the showpiece
    show_advanced: bool = False

    # Inline message under the prompt after a blank submit ("" when none).
    prompt_error: str = ""

    # Templates
    templates: list[dict[str, str]] = []
    show_save_template_dialog: bool = False
    template_name_input: str = ""

    mood_examples: list[str] = [
        "Chill rainy day vibes with acoustic guitar",
        "Energetic workout music to pump me up",
        "Relaxing background music for studying",
        "Upbeat party anthems from the 2000s",
        "Melancholic indie tracks for late night reflection",
    ]

    @rx.event
    def on_load(self) -> Optional[list["EventSpec"]]:
        if not self.check_auth():
            self.is_page_loading = False
            return None
        self.load_app_data()
        self._clear_resolved_error()
        self._load_audio_stats()
        self._load_templates()

        # Check for rerun config from History page. Only a rerun that actually
        # changed the prompt or genre writes those fields, so a plain page load
        # never overwrites a draft in the client-owned fields.
        events: list["EventSpec"] = []
        if self._rerun_generation_config:
            config = self._rerun_generation_config
            self._rerun_generation_config = ""
            busy = self._prompt_change_blocked()
            if busy is not None:
                events = [busy]
            else:
                prompt_before, genre_before = self.mood_query, self.genre_filter
                self.load_generation_config(config)
                events = self._field_sync_events(prompt_before, genre_before)

        # Re-check Plex reachability in the background when due (TTL + fingerprint).
        events = [*events, *self._plex_readiness_events()]
        self.is_page_loading = False
        return events or None

    def _clear_resolved_error(self) -> None:
        """Drop the failure callout once readiness no longer reports its cause.

        Run after ``load_app_data`` refreshed ``readiness``. A failure from a readiness
        component (embeddings, library, index) stays while that component still
        reports the same cause; a failure that readiness can't see (component
        "generation": no matching tracks, an unexpected error) stays until it is
        dismissed or a new generation starts.
        """
        from plexmix.services.readiness import COMPONENTS

        error = self.generation_error or {}
        component = error.get("component", "")
        if not error.get("title") or component not in COMPONENTS:
            return
        entry = (self.readiness or {}).get(component) or {}
        if entry and entry.get("cause", "") != error.get("cause", ""):
            self.generation_error = empty_generation_error()

    @rx.event
    def dismiss_error(self) -> None:
        """Close the failure callout (the readiness line still shows a live cause)."""
        self.generation_error = empty_generation_error()

    @rx.var(cache=True)
    def can_generate(self) -> bool:
        """False while a blocking local cause stops generation (design D2/D9).

        Blocking: the library isn't synced, the search index is empty or doesn't match
        the embedding provider, or the provider is missing its key or package. A
        failure recorded by an earlier run (a timeout, a rejected key) never blocks.
        """
        from plexmix.services import readiness as rd

        return rd.can_generate(self.readiness or {})

    @rx.var(cache=True)
    def gen_status(self) -> dict[str, str]:
        """The readiness line under the prompt (see :func:`generation_status`)."""
        return generation_status(
            self.readiness or {},
            loaded=self.library_stats_loaded,
            total=self.stat_total,
            embedded=self.stat_embedded,
            error=self.generation_error,
        )

    @rx.var(cache=True)
    def active_constraints(self) -> list[dict[str, str]]:
        """Applied filters that have no Options control, shown as removable chips."""
        return constraint_chips(
            year_min=self.year_min,
            year_max=self.year_max,
            tempo_min=self.tempo_min,
            tempo_max=self.tempo_max,
            energy_level=self.energy_level,
            key_filter=self.key_filter,
            danceability_min=self.danceability_min,
            avoid_recent=self.avoid_recent,
        )

    @rx.event
    def remove_constraint(self, key: str) -> None:
        """Stop applying one chip's filter (``CONSTRAINT_KEYS``); unknown keys are ignored.

        None of these filters has a client-owned field, so nothing is echoed back.
        """
        self._reset_constraint(key)

    @rx.event
    def clear_constraints(self) -> None:
        """Stop applying every chip filter (the "Clear all" action)."""
        for key in CONSTRAINT_KEYS:
            self._reset_constraint(key)

    def _reset_constraint(self, key: str) -> None:
        if key == "year":
            self.year_min = ""
            self.year_max = ""
        elif key == "tempo":
            self.tempo_min = ""
            self.tempo_max = ""
        elif key == "energy":
            self.energy_level = ""
        elif key == "key":
            self.key_filter = ""
        elif key == "danceability":
            self.danceability_min = ""
        elif key == "avoid_recent":
            self.avoid_recent = 0

    def _prompt_change_blocked(self) -> Optional["EventSpec"]:
        """Make way for a prompt the application sets (rerun, prefill, template).

        While a generation runs, returns a toast and the caller leaves the prompt
        alone: changing it would relabel the running generation's results and the
        playlist saved from them. Otherwise clears any results on screen (as "New
        playlist" does), so the idle hero mounts and its prompt field shows the
        new text (it starts from ``default_value``), and returns ``None``.
        """
        if self.is_generating:
            busy: "EventSpec" = rx.toast.info(GENERATION_BUSY_MESSAGE)
            return busy
        if self.generated_playlist:
            self._clear_results()
        return None

    def _clear_results(self) -> None:
        """Drop the generated playlist so the Generator shows the idle hero again."""
        self.generated_playlist = []
        self.total_duration_ms = 0
        self.generation_progress = 0
        self.generation_message = ""
        self.generation_log = []

    def _field_sync_events(self, prompt_before: str, genre_before: str) -> list["EventSpec"]:
        """``rx.set_value`` events for the prompt/genre fields whose value changed."""
        events: list["EventSpec"] = []
        if self.mood_query != prompt_before:
            events.append(rx.set_value(PROMPT_INPUT_ID, self.mood_query))
        if self.genre_filter != genre_before:
            events.append(rx.set_value(GENRE_INPUT_ID, self.genre_filter))
        return events

    def _load_audio_stats(self):
        """Check how many tracks have audio analysis data."""
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            if db_path.exists():
                db = SQLiteManager(str(db_path))
                db.connect()
                row = db.conn.execute("SELECT COUNT(*) FROM audio_features").fetchone()
                self.audio_analyzed_count = row[0] if row else 0
                db.close()
            else:
                self.audio_analyzed_count = 0
        except Exception:
            self.audio_analyzed_count = 0

    def _load_templates(self):
        """Load saved templates from database."""
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            if db_path.exists():
                db = SQLiteManager(str(db_path))
                db.connect()
                templates = db.get_templates()
                db.close()
                self.templates = [
                    _str_dict(
                        {
                            "id": str(t.id or ""),
                            "name": t.name,
                            "mood_query": t.mood_query,
                            "max_tracks": str(t.max_tracks),
                            "genre_filter": t.genre_filter,
                            "year_min": str(t.year_min or ""),
                            "year_max": str(t.year_max or ""),
                            "tempo_min": str(t.tempo_min or ""),
                            "tempo_max": str(t.tempo_max or ""),
                            "energy_level": t.energy_level,
                            "key_filter": t.key_filter,
                            "danceability_min": str(t.danceability_min or ""),
                            "shuffle_mode": t.shuffle_mode,
                            "candidate_pool_multiplier": str(t.candidate_pool_multiplier),
                            "is_preset": "1" if t.is_preset else "0",
                        }
                    )
                    for t in templates
                ]
            else:
                self.templates = []

            # NOTE: built-in presets are intentionally NOT auto-seeded here. The
            # template apply/save UI is not yet wired, so writing preset rows into
            # the DB on every generator page load was a surprising side effect.
            # _seed_presets() remains available to call once the feature is wired.

        except Exception:
            self.templates = []

    def _seed_presets(self):
        """Insert built-in template presets into the database."""
        from plexmix.config.settings import Settings
        from plexmix.database.sqlite_manager import SQLiteManager
        from plexmix.database.models import PlaylistTemplate

        presets = [
            PlaylistTemplate(
                name="Morning Commute",
                mood_query="Upbeat feel-good songs to start the day",
                max_tracks=30,
                energy_level="medium",
                shuffle_mode="alternating_artists",
                is_preset=True,
            ),
            PlaylistTemplate(
                name="Workout",
                mood_query="High energy pump-up workout music",
                max_tracks=40,
                energy_level="high",
                shuffle_mode="energy_curve",
                is_preset=True,
            ),
            PlaylistTemplate(
                name="Study Session",
                mood_query="Calm instrumental focus music for studying",
                max_tracks=50,
                energy_level="low",
                shuffle_mode="similarity",
                is_preset=True,
            ),
            PlaylistTemplate(
                name="Dinner Party",
                mood_query="Sophisticated jazz and soul for an evening gathering",
                max_tracks=40,
                genre_filter="jazz",
                shuffle_mode="alternating_artists",
                is_preset=True,
            ),
            PlaylistTemplate(
                name="Late Night",
                mood_query="Mellow downtempo and ambient tracks for winding down",
                max_tracks=30,
                energy_level="low",
                shuffle_mode="similarity",
                is_preset=True,
            ),
        ]

        try:
            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            if db_path.exists():
                db = SQLiteManager(str(db_path))
                db.connect()
                for preset in presets:
                    db.insert_template(preset)
                db.close()
                self._load_templates()
        except Exception:
            pass

    def apply_template(self, template_id: str) -> Optional[list["EventSpec"]]:
        """Load a template's settings into the generator form.

        Returns ``rx.set_value`` events for the prompt and genre fields when the
        template changed them (they are client-owned). Shown results are cleared
        first; while a generation runs nothing changes and a toast says why.
        """
        template = next((t for t in self.templates if t.get("id") == template_id), None)
        if not template:
            return None
        busy = self._prompt_change_blocked()
        if busy is not None:
            return [busy]

        prompt_before, genre_before = self.mood_query, self.genre_filter
        self.set_mood_query(template.get("mood_query", "") or "")
        self.max_tracks = int(template.get("max_tracks", "50") or "50")
        self.genre_filter = template.get("genre_filter", "") or ""
        self.year_min = template.get("year_min", "")
        self.year_max = template.get("year_max", "")
        self.tempo_min = template.get("tempo_min", "")
        self.tempo_max = template.get("tempo_max", "")
        self.energy_level = template.get("energy_level", "")
        self.key_filter = template.get("key_filter", "")
        self.danceability_min = template.get("danceability_min", "")
        self.shuffle_mode = template.get("shuffle_mode", "similarity")
        self.candidate_pool_multiplier = int(
            template.get("candidate_pool_multiplier", "25") or "25"
        )
        return self._field_sync_events(prompt_before, genre_before) or None

    def open_save_template_dialog(self):
        self.template_name_input = ""
        self.show_save_template_dialog = True

    def close_save_template_dialog(self):
        self.show_save_template_dialog = False

    def set_template_name_input(self, value: str):
        self.template_name_input = value

    def save_current_as_template(self):
        """Save the current generator config as a named template."""
        name = self.template_name_input.strip()
        if not name:
            return

        from plexmix.config.settings import Settings
        from plexmix.database.sqlite_manager import SQLiteManager
        from plexmix.database.models import PlaylistTemplate

        try:
            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            db = SQLiteManager(str(db_path))
            db.connect()

            template = PlaylistTemplate(
                name=name,
                mood_query=self.mood_query,
                max_tracks=self.max_tracks,
                genre_filter=self.genre_filter,
                year_min=int(self.year_min) if self.year_min else None,
                year_max=int(self.year_max) if self.year_max else None,
                tempo_min=float(self.tempo_min) if self.tempo_min else None,
                tempo_max=float(self.tempo_max) if self.tempo_max else None,
                energy_level=self.energy_level,
                key_filter=self.key_filter,
                danceability_min=float(self.danceability_min) if self.danceability_min else None,
                shuffle_mode=self.shuffle_mode,
                candidate_pool_multiplier=self.candidate_pool_multiplier,
                is_preset=False,
            )

            db.insert_template(template)
            db.close()
            self._load_templates()
            self.show_save_template_dialog = False
        except Exception as e:
            logger.error("Failed to save template: %s", e)

    def delete_template(self, template_id: str):
        """Delete a user-created template (presets cannot be deleted)."""
        template = next((t for t in self.templates if t.get("id") == template_id), None)
        if not template or template.get("is_preset") == "1":
            return

        from plexmix.config.settings import Settings
        from plexmix.database.sqlite_manager import SQLiteManager

        try:
            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()
            db = SQLiteManager(str(db_path))
            db.connect()
            db.delete_template(int(template_id))
            db.close()
            self._load_templates()
        except Exception as e:
            logger.error("Failed to delete template: %s", e)

    def load_generation_config(self, config_json: str):
        """Load generation parameters from a JSON config string (used by rerun)."""
        import json as _json

        try:
            config = _json.loads(config_json)
        except (ValueError, TypeError):
            return

        # Cut to the prompt limit: the field shows, and generation sends, this text.
        self.set_mood_query(str(config.get("mood_query", "") or ""))
        self.max_tracks = int(config.get("max_tracks", 50))
        self.genre_filter = str(config.get("genre_filter", "") or "")
        self.year_min = str(config.get("year_min", "") or "")
        self.year_max = str(config.get("year_max", "") or "")
        self.tempo_min = str(config.get("tempo_min", "") or "")
        self.tempo_max = str(config.get("tempo_max", "") or "")
        self.energy_level = str(config.get("energy_level", "") or "")
        self.key_filter = str(config.get("key_filter", "") or "")
        self.danceability_min = str(config.get("danceability_min", "") or "")
        self.shuffle_mode = config.get("shuffle_mode", "similarity")
        self.candidate_pool_multiplier = int(config.get("candidate_pool_multiplier", 25))
        self.avoid_recent = int(config.get("avoid_recent", 0) or 0)
        # Year, tempo, energy, key, danceability and avoid-recent have no Options
        # control: they show as removable chips (active_constraints) whether Options
        # is open or not. Genre focus lives in the Options panel, so reveal it when
        # the rerun brought one rather than applying it while hidden.
        if self.genre_filter:
            self.show_advanced = True

    def use_example(self, example: str) -> Optional["EventSpec"]:
        """Fill the prompt with an example (a server write: also updates the field)."""
        return self._prefill(example)

    def set_mood_query(self, value: str) -> Any:
        """Commit the prompt text (the field's own blur commit, and every app write).

        Cuts to ``PROMPT_MAX_CHARS`` and clears the blank-prompt message once the
        prompt holds text. Never returns ``rx.set_value``: that would echo over
        the user's typing. It returns nothing; the return is annotated ``Any``
        because pages chain it as an event spec (``GeneratorState.set_mood_query(v)``
        in the vibe pills), which mypy reads as using a ``None`` result.
        """
        self.mood_query = (value or "")[:PROMPT_MAX_CHARS]
        if self.mood_query.strip():
            self.prompt_error = ""

    def set_genre_filter(self, value: str) -> None:
        """Commit the Genre focus text; an unchanged value is a no-op."""
        if value == self.genre_filter:
            return
        self.genre_filter = value

    @rx.event
    def submit_prompt(self, form_data: dict[str, Any]) -> Any:
        """Generate from the submitted form: the single entry point for Generate.

        Used by the prompt form (Generate, ⌘/Ctrl+Enter) and by the command
        palette's Generate row (``{"prompt": text}``). The text comes from
        ``form_data``, never from state, so a commit still in flight cannot make
        the submit use stale text. The values go through the existing setters. A
        ``"genre"`` key, when present, updates the Genre focus too.

        Returns ``GeneratorState.generate_playlist``, or ``None`` when the prompt
        is blank (``prompt_error`` is set instead), a generation is running, or a
        blocking readiness cause stops generation (``can_generate``; the prompt is
        kept and the readiness line says why).
        """
        if self.is_generating:
            return None
        self.set_mood_query(str(form_data.get("prompt", "") or ""))
        if "genre" in form_data:
            self.set_genre_filter(str(form_data.get("genre") or ""))
        if not self.mood_query.strip():
            self.prompt_error = BLANK_PROMPT_ERROR
            return None
        self.prompt_error = ""
        if not self.can_generate:
            return None
        return GeneratorState.generate_playlist

    @rx.event
    def use_vibe(self, text: str) -> Any:
        """A vibe pill: fill the prompt with ``text`` and generate when generation can run.

        Always returns the ``rx.set_value`` that shows ``text`` in the client-owned
        prompt field (input-responsiveness D3). When ``can_generate`` it also returns
        the failure-focus chase and ``generate_playlist``; while a blocking cause stops
        generation it returns only the ``set_value`` (the readiness line says why).
        While a generation runs nothing starts (see :meth:`prefill_prompt`).
        """
        if self.is_generating:
            return self._prefill(text)
        fill = self._prefill(text)
        if not self.can_generate:
            return fill
        return [fill, rx.call_script(FAILURE_FOCUS_SCRIPT), GeneratorState.generate_playlist]

    @rx.event
    def prefill_prompt(self, text: str) -> Optional["EventSpec"]:
        """Replace the prompt with ``text`` (cut to ``PROMPT_MAX_CHARS``) without generating.

        Used by the command palette's Recent prompts (the vibe pills use
        :meth:`use_vibe`, built on the same fill). Results on screen are cleared first, so the prompt field is shown with the new text.
        Returns the ``rx.set_value`` that shows the text in the prompt field when
        it is already mounted. While a generation runs the prompt is kept and a
        toast says why (nothing at all when ``text`` already is the prompt, e.g.
        a double-clicked vibe pill).
        """
        return self._prefill(text)

    def _prefill(self, text: str) -> Optional["EventSpec"]:
        """Set the prompt (cut to the limit) and return the ``set_value`` for its field."""
        value = str(text or "")
        if self.is_generating and value[:PROMPT_MAX_CHARS] == self.mood_query:
            return None
        busy = self._prompt_change_blocked()
        if busy is not None:
            return busy
        self.set_mood_query(value)
        event: "EventSpec" = rx.set_value(PROMPT_INPUT_ID, self.mood_query)
        return event

    @rx.var(cache=True)
    def prompt_count_label(self) -> str:
        """Prompt length against the limit, e.g. ``"12/280"``."""
        return f"{len(self.mood_query)}/{PROMPT_MAX_CHARS}"

    def set_playlist_name(self, value: str):
        self.playlist_name = value

    def set_max_tracks(self, value: int):
        self.max_tracks = max(10, min(100, value))

    def set_candidate_pool_multiplier(self, value: int):
        self.candidate_pool_multiplier = max(1, min(100, value))

    def set_shuffle_mode(self, value: str):
        self.shuffle_mode = value

    def set_avoid_recent(self, value: str):
        try:
            self.avoid_recent = max(0, int(value)) if value else 0
        except ValueError:
            self.avoid_recent = 0

    def set_year_range(self, year_min: str, year_max: str):
        self.year_min = year_min
        self.year_max = year_max

    def set_year_min(self, value: str):
        self.year_min = value

    def set_year_max(self, value: str):
        self.year_max = value

    def set_tempo_min(self, value: str):
        self.tempo_min = value

    def set_tempo_max(self, value: str):
        self.tempo_max = value

    def set_energy_level(self, value: str):
        self.energy_level = "" if value == "Any" else value

    def set_key_filter(self, value: str):
        self.key_filter = "" if value == "Any" else value

    def set_danceability_min(self, value: str):
        self.danceability_min = value

    @rx.event(background=True)
    async def generate_playlist(self) -> None:
        async with self:
            token = self.router.session.client_token
            if not self.access_allowed():
                return
            # Guard against concurrent generations (e.g. a double-click) so two
            # overlapping jobs don't run against the index at once.
            if self.is_generating:
                return
            if not self.mood_query.strip():
                return
            # Readiness gate (design D9): every entry point (Generate, ⌘/Ctrl+Enter, the
            # palette, a vibe pill, Regenerate) ends here. A blocking local cause starts
            # nothing; the readiness line says why. Network failures never block.
            if not self.can_generate:
                return

            self._generation_id += 1
            cancel_event = jobs.start(token, "generation")
            self.is_generating = True
            self.generation_error = empty_generation_error()
            self.generation_progress = 0
            self.generation_message = "Starting playlist generation..."
            self.generation_log = ["Starting playlist generation..."]
            self.generated_playlist = []
            self.total_duration_ms = 0
            # Default the playlist name to THIS generation's mood, so a second
            # generation isn't saved under the first playlist's name.
            self.playlist_name = self.mood_query.strip()[:60]
            total_tracks = self.stat_total
            filters_applied = bool(self.active_constraints) or bool(self.genre_filter)

        current_gen = self._generation_id

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.database.vector_index import VectorIndex
            from plexmix.playlist.generator import PlaylistGenerator
            from plexmix.services import readiness as rd
            from plexmix.services.providers import (
                ProviderFailure,
                build_embedding_generator_with_reason,
            )
            from plexmix.services.registry import embedding_provider_display_name

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                await self._fail_generation(
                    generation_error_for(
                        "library",
                        rd.Cause.NOT_SYNCED.value,
                        rd.describe("library", rd.Cause.NOT_SYNCED),
                    )
                )
                return

            # Resolve embedding settings now (but don't instantiate generator yet - it blocks!)
            emb_provider = (settings.embedding.default_provider or "").strip().lower()
            emb_ctx: dict[str, Any] = {
                "label": embedding_provider_display_name(emb_provider),
                "provider": emb_provider,
                "model": rd.embedding_model_for(settings),
            }
            index_path = str(settings.database.get_index_path())

            from plexmix.services.playlist_service import (
                build_generation_filters,
                safe_int,
                safe_float,
            )

            filters = build_generation_filters(
                genre=self.genre_filter or None,
                year_min=safe_int(self.year_min),
                year_max=safe_int(self.year_max),
                tempo_min=safe_float(self.tempo_min),
                tempo_max=safe_float(self.tempo_max),
                energy_level=self.energy_level or None,
                key=self.key_filter or None,
                danceability_min=safe_float(self.danceability_min),
            )

            loop = asyncio.get_running_loop()

            def progress_callback(progress: float, message: str) -> None:
                gen_id = current_gen

                async def update() -> None:
                    async with self:
                        # Drop callbacks from a cancelled or superseded generation
                        # so they don't repopulate the cleared message/log.
                        if self._generation_id != gen_id or (
                            cancel_event is not None and cancel_event.is_set()
                        ):
                            return
                        progress_value = max(0, min(100, int(progress * 100)))
                        self.generation_progress = progress_value
                        self.generation_message = message
                        self.generation_log = (self.generation_log + [message])[
                            -GENERATION_LOG_MAX:
                        ]

                asyncio.run_coroutine_threadsafe(update(), loop)

            mood_query_text = self.mood_query
            max_tracks_val = self.max_tracks
            pool_multiplier = self.candidate_pool_multiplier
            shuffle = self.shuffle_mode
            avoid_recent_val = self.avoid_recent

            logger.info(
                "Playlist generation started | mood='%s' max_tracks=%s multiplier=%s shuffle=%s",
                mood_query_text,
                max_tracks_val,
                pool_multiplier,
                shuffle,
            )

            def failed(
                component: str, cause: str, copy: Mapping[str, str], recorded: bool = False
            ) -> dict[str, Any]:
                return {
                    "tracks": [],
                    "total_duration": 0,
                    "error": generation_error_for(component, cause, copy),
                    "recorded": recorded,
                }

            def run_generation() -> dict[str, Any]:
                # Runs in an executor: building the provider can block (Local loads its
                # model), and the provider's outcome is recorded in readiness here.
                emb_fp = current_fingerprint_safe("embeddings", settings)
                embedding_generator, failure = build_embedding_generator_with_reason(settings)
                if embedding_generator is None:
                    failure = failure or ProviderFailure(rd.Cause.UNKNOWN, emb_provider)
                    detail = ""
                    if failure.cause == rd.Cause.UNKNOWN:
                        # failure.detail is already redacted; make it plain language.
                        detail = friendly_error(RuntimeError(failure.detail))
                    record_readiness(
                        "embeddings",
                        rd.Level.FAILING.value,
                        failure.cause.value,
                        message=detail,
                        fingerprint=emb_fp,
                    )
                    copy = rd.describe_failure(
                        "embeddings", failure, {"model": emb_ctx["model"], "detail": detail}
                    )
                    return failed("embeddings", failure.cause.value, copy, recorded=True)
                dimension = embedding_generator.get_dimension()

                local_db = SQLiteManager(str(db_path))
                local_db.connect()
                try:
                    local_vector_index = VectorIndex(dimension=dimension, index_path=index_path)

                    if not local_vector_index.index or local_vector_index.index.ntotal == 0:
                        cause = rd.Cause.INDEX_EMPTY
                        copy = rd.describe("index", cause, {"total_tracks": total_tracks})
                        return failed("index", cause.value, copy)

                    if local_vector_index.dimension_mismatch:
                        cause = rd.Cause.DIMENSION_MISMATCH
                        copy = rd.describe(
                            "index",
                            cause,
                            {
                                **emb_ctx,
                                "index_dimension": local_vector_index.loaded_dimension or 0,
                                "expected_dimension": dimension,
                            },
                        )
                        return failed("index", cause.value, copy)

                    provider = _TrackedEmbeddings(embedding_generator)
                    playlist_generator = PlaylistGenerator(
                        db_manager=local_db,
                        vector_index=local_vector_index,
                        embedding_generator=cast(Any, provider),
                    )

                    try:
                        tracks = playlist_generator.generate(
                            mood_query=mood_query_text,
                            max_tracks=max_tracks_val,
                            candidate_pool_multiplier=pool_multiplier,
                            filters=filters if filters else None,
                            progress_callback=progress_callback,
                            shuffle_mode=shuffle,
                            avoid_recent=avoid_recent_val,
                            cancel_event=cancel_event,
                        )
                    except Exception as e:
                        if provider.error is None:
                            raise
                        # The embedding provider failed on the prompt: record its cause.
                        key = rd.resolve_secret("embeddings", settings) or ""
                        cause_value, message = provider_failure_cause(e, key=key)
                        logger.warning(
                            "Playlist generation: the embedding provider failed (%s, %s)",
                            cause_value,
                            type(e).__name__,
                        )
                        record_readiness(
                            "embeddings",
                            rd.Level.FAILING.value,
                            cause_value,
                            message=message,
                            fingerprint=emb_fp,
                        )
                        copy = rd.describe(
                            "embeddings", cause_value, {**emb_ctx, "detail": message}
                        )
                        return failed("embeddings", cause_value, copy, recorded=True)

                    recorded = False
                    if provider.succeeded:
                        # A real call embedded the prompt: the provider works.
                        record_readiness("embeddings", rd.Level.READY.value, fingerprint=emb_fp)
                        recorded = True

                    # Coerce NULL durations to 0 — a track with duration_ms=None
                    # otherwise raised TypeError in sum().
                    total_duration = sum((track.get("duration_ms") or 0) for track in tracks)

                    return {
                        "tracks": tracks,
                        "total_duration": total_duration,
                        "error": None,
                        "recorded": recorded,
                    }
                finally:
                    local_db.close()

            generation_result = await loop.run_in_executor(None, run_generation)

            if generation_result.get("recorded"):
                # Show the provider's new outcome (ready, or failing with its cause) on
                # the readiness line and every other status surface.
                await self._republish_readiness(settings)

            if cancel_event.is_set() or self._generation_id != current_gen:
                async with self:
                    self.is_generating = False
                return

            error = generation_result.get("error")
            if error:
                logger.warning("Playlist generation aborted: %s", error.get("title", ""))
                await self._fail_generation(error)
                return

            playlist_tracks = generation_result["tracks"]
            total_duration = generation_result["total_duration"]

            logger.info("Playlist generation finished with %s tracks", len(playlist_tracks))

            if not playlist_tracks:
                body = NO_MATCH_FILTERED_BODY if filters_applied else NO_MATCH_BODY
                await self._fail_generation(
                    generation_error_for(
                        "generation", "", {"title": NO_MATCH_TITLE, "body": body}, tone="warning"
                    )
                )
                return

            # Format durations for display
            for track in playlist_tracks:
                duration_ms = track.get("duration_ms") or 0
                if duration_ms:
                    minutes = duration_ms // 60000
                    seconds = (duration_ms // 1000) % 60
                    track["duration_formatted"] = f"{minutes}:{seconds:02d}"
                else:
                    track["duration_formatted"] = "0:00"

            async with self:
                self.generated_playlist = [_str_dict(t) for t in playlist_tracks]
                self.total_duration_ms = total_duration
                self.is_generating = False
                self.generation_progress = 100
                if not self.playlist_name.strip():
                    self.playlist_name = self.mood_query[:60]
                final_msg = f"Generated {len(playlist_tracks)} tracks!"
                self.generation_message = final_msg
                self.generation_log = (self.generation_log + [final_msg])[-GENERATION_LOG_MAX:]

            # Auto-dismiss the success message after 5 seconds.
            await asyncio.sleep(5)
            async with self:
                if self._generation_id == current_gen and not self.is_generating:
                    self.generation_message = ""
                    self.generation_log = []

        except Exception as e:
            # Not a provider failure (those are classified above): describe it plainly.
            # safe_error_text keeps a settings ValidationError from echoing input values.
            logger.error("Playlist generation failed: %s", safe_error_text(e), exc_info=True)
            from plexmix.services import readiness as rd

            detail = friendly_error(RuntimeError(safe_error_text(e)))
            await self._fail_generation(
                generation_error_for(
                    "generation",
                    rd.Cause.UNKNOWN.value,
                    rd.describe("generation", rd.Cause.UNKNOWN, {"detail": detail}),
                )
            )
        finally:
            # Release this generation's cancel handle (a newer generation of the
            # same session keeps its own; a reaped one is already gone).
            jobs.finish(token, "generation", cancel_event)

    async def _fail_generation(self, error: dict[str, str]) -> None:
        """End the running generation with ``error`` shown in the failure callout.

        ``generation_message`` is progress only, so it is cleared; the title also goes
        to the generation log.
        """
        async with self:
            self.is_generating = False
            self.generation_error = error
            self.generation_message = ""
            title = error.get("title", "")
            if title:
                self.generation_log = (self.generation_log + [title])[-GENERATION_LOG_MAX:]

    async def _republish_readiness(self, settings: Any) -> None:
        """Re-read the local facts (in an executor) and re-evaluate ``readiness``."""
        from plexmix.services import readiness as rd

        loop = asyncio.get_running_loop()
        try:
            facts = await loop.run_in_executor(None, rd.collect_facts, settings)
        except Exception as e:
            logger.warning("Could not refresh readiness after generation: %s", type(e).__name__)
            return
        await self._publish_status(settings, facts)

    @rx.event
    def regenerate(self):
        # Trigger the background generation handler by returning it; awaiting a
        # background event directly raises (it is dispatched, not awaited).
        return GeneratorState.generate_playlist

    def remove_track(self, track_id: str):
        self.generated_playlist = [t for t in self.generated_playlist if t["id"] != track_id]
        self.total_duration_ms = sum(
            int(track.get("duration_ms", "0") or "0") for track in self.generated_playlist
        )

    @rx.event
    def cancel_generation(self) -> None:
        """Signal this session's in-flight generation to stop (coarse-grained,
        between stages)."""
        jobs.cancel(self.router.session.client_token, "generation")
        self._generation_id += 1
        self.is_generating = False
        self.generation_progress = 0
        self.generation_message = ""
        self.generation_log = []

    @rx.event
    def toggle_advanced(self):
        self.show_advanced = not self.show_advanced

    @rx.event
    def new_playlist(self):
        """Clear results and return the showpiece to the idle hero."""
        self._clear_results()

    @rx.var
    def active_phase(self) -> int:
        """Active thinking-stage phase from real progress bands (0/10/40/70)."""
        p = self.generation_progress
        if p < 10:
            return 0
        if p < 40:
            return 1
        if p < 70:
            return 2
        return 3

    @rx.var
    def total_duration_label(self) -> str:
        total_seconds = self.total_duration_ms // 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        if hours > 0:
            return f"{hours} hr {minutes} min"
        return f"{minutes} min"

    @rx.var
    def ordering_label(self) -> str:
        return {
            "similarity": "similarity",
            "random": "random",
            "alternating_artists": "alternating artists",
            "energy_curve": "energy curve",
        }.get(self.shuffle_mode, self.shuffle_mode)

    @rx.var(cache=True)
    def cover_variant(self) -> int:
        """Cover swatch variant (``cover-v0``…``cover-v5``) for the results header.

        Seeded the way History seeds a saved playlist's card
        (``cover_seed(mood_query, name)``). A saved playlist stores this prompt as
        its ``mood_query``, so the same vibe shows the same swatch on both pages.
        """
        return _cover_variant(cover_seed(self.mood_query, ""))

    @rx.event(background=True)
    async def save_to_plex(self):
        async with self:
            if not self.access_allowed():
                return
            if not self.generated_playlist or not self.playlist_name.strip():
                return

            self.is_saving = True
            self.generation_message = "Saving to Plex..."

        try:
            from plexmix.config.settings import Settings
            from plexmix.services.sync_service import connect_plex, PlexConnectionError

            settings = Settings.load_from_file()

            if not settings.plex.library_name:
                async with self:
                    self.is_saving = False
                    self.generation_message = ""
                yield rx.toast.error("Music library not configured")
                return

            # Capture state inside the context, then do the blocking Plex network
            # work in an executor so the event loop isn't frozen.
            async with self:
                playlist_name = self.playlist_name
                track_plex_keys = [int(track["plex_key"]) for track in self.generated_playlist]

            def _create() -> Any:
                client = connect_plex(settings)
                return client.create_playlist(playlist_name, track_plex_keys)

            loop = asyncio.get_running_loop()
            try:
                playlist = await loop.run_in_executor(None, _create)
            except PlexConnectionError as e:
                async with self:
                    self.is_saving = False
                    self.generation_message = ""
                # Plain UI copy for e.code: its message points at CLI commands.
                yield rx.toast.error(_plex_error_title(e, settings))
                return

            async with self:
                self.is_saving = False
                self.generation_message = ""

            # Honor the return: create_playlist returns None on failure, so don't
            # claim success when nothing was actually created in Plex.
            if playlist is None:
                yield rx.toast.error("Failed to save to Plex (the playlist was not created).")
            else:
                yield rx.toast.success(f"Saved to Plex: {playlist_name}")

        except Exception as e:
            async with self:
                self.is_saving = False
                self.generation_message = ""

            yield rx.toast.error(f"Failed to save to Plex: {str(e)}")

    @rx.event(background=True)
    async def save_locally(self):
        async with self:
            if not self.generated_playlist or not self.playlist_name.strip():
                return

            self.is_saving = True
            self.generation_message = "Saving locally..."

        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager
            from plexmix.database.models import Playlist

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            db = SQLiteManager(str(db_path))
            db.connect()

            import json as _json

            track_ids = [int(track["id"]) for track in self.generated_playlist]

            # Store full generation config for rerun capability
            gen_config = _json.dumps(
                {
                    "mood_query": self.mood_query,
                    "max_tracks": self.max_tracks,
                    "genre_filter": self.genre_filter,
                    "year_min": self.year_min,
                    "year_max": self.year_max,
                    "tempo_min": self.tempo_min,
                    "tempo_max": self.tempo_max,
                    "energy_level": self.energy_level,
                    "key_filter": self.key_filter,
                    "danceability_min": self.danceability_min,
                    "shuffle_mode": self.shuffle_mode,
                    "candidate_pool_multiplier": self.candidate_pool_multiplier,
                    "avoid_recent": self.avoid_recent,
                }
            )

            playlist = Playlist(
                name=self.playlist_name,
                created_by_ai=True,
                mood_query=self.mood_query,
                generation_config=gen_config,
            )

            playlist_id = db.insert_playlist(playlist)
            db.add_tracks_to_playlist(playlist_id, track_ids)

            db.close()

            async with self:
                self.is_saving = False
                self.generation_message = ""

            yield rx.toast.success(f"Saved locally: {self.playlist_name}")

        except Exception as e:
            async with self:
                self.is_saving = False
                self.generation_message = ""

            yield rx.toast.error(f"Failed to save locally: {str(e)}")

    def format_duration(self, duration_ms: int) -> str:
        """Format duration from milliseconds to mm:ss"""
        if not duration_ms:
            return "0:00"

        total_seconds = duration_ms // 1000
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}:{seconds:02d}"

    def export_m3u(self):
        if not self.generated_playlist:
            return

        from plexmix.config.settings import Settings

        settings = Settings.load_from_file()

        m3u_content = "#EXTM3U\n"
        for track in self.generated_playlist:
            duration_sec = int(track.get("duration_ms", "0") or "0") // 1000
            artist = track.get("artist", "Unknown")
            title = track.get("title", "Unknown")
            m3u_content += f"#EXTINF:{duration_sec},{artist} - {title}\n"
            file_path = track.get("file_path") or ""
            if file_path:
                file_path = settings.audio.resolve_path(file_path)
            m3u_content += f"{file_path}\n" if file_path else f"track_{track['id']}.mp3\n"

        return rx.download(data=m3u_content, filename=f"{self.playlist_name or 'playlist'}.m3u")
