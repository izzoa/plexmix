"""Dashboard state: coverage readouts, the first-run checklist and recent playlists.

The readiness snapshot, the ``stat_*`` counts and the last-sync times come from
:class:`~plexmix.ui.states.app_state.AppState` (``load_app_data``). This state
adds:

- ``coverage``: one readout per enrichment (Tagged, Embedded, Audio analyzed,
  MusicBrainz genres) as a share of the library, plus the header readout
  ``library_readout`` / ``sync_readout`` (design D14);
- ``setup_steps`` / ``setup_incomplete``: the first-run checklist
  (``readiness.setup_steps``, design D13), with the progress of a sync or an
  index build started from it (``poll_setup_progress``, mounted only while one
  runs);
- ``recent_playlists``: the latest playlists with their id, so each row opens
  its detail in History (``/history?playlist=<id>``).
"""

import logging
import re
import sqlite3
from contextlib import closing
from typing import Any, Dict, List, Mapping

import reflex as rx

from plexmix.ui.states.app_state import AppState, stored_utc
from plexmix.ui.states.library_state import (
    REMOVAL_CANCELLED_MESSAGE,
    REMOVAL_ERROR_MESSAGE,
    REMOVAL_RUN_KEY,
    SYNC_PARTIAL_KEY,
    UNREAD_TRACKS_KEY,
    result_toast,
    sync_result_tone,
)
from plexmix.ui.utils.helpers import format_count, format_percent, format_short_datetime

logger = logging.getLogger(__name__)

# TaskStore job types a setup step can start, and the step each one belongs to.
SETUP_JOBS: tuple[str, ...] = ("sync", "embedding")
SETUP_JOB_STEPS: Dict[str, str] = {"sync": "sync", "embedding": "index"}

# A watched job that never registers in the TaskStore (for example the request
# was refused) stops being watched after this many polls (2 s apart).
SETUP_WATCH_MAX_MISSES = 5

RECENT_PLAYLIST_LIMIT = 10

# Raw error text can carry a Plex URL with its token; never show that part.
_PLEX_TOKEN_RE = re.compile(r"(x-plex-token=)[^&\s'\"]+", re.I)
_MAX_ERROR_CHARS = 200

# Toast of a sync that finished clean. A sync that kept tracks back shows the result
# text of its task instead; the second text stands in when the task has none.
SYNC_DONE_MESSAGE = "Library synced"
SYNC_KEPT_BACK_MESSAGE = "Sync completed, but some tracks were kept as they were."


# ── Pure helpers (no Reflex) ──────────────────────────────────────────


def _int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def meter_width(part: Any, total: Any) -> str:
    """Width of a coverage meter in percent ("0"-"100"), as a string.

    A non-zero share never draws as empty and an incomplete one never as full,
    matching the "<1%" / ">99%" readings of ``format_percent``.
    """
    p, t = _int(part), _int(total)
    if t <= 0 or p <= 0:
        return "0"
    if p >= t:
        return "100"
    share = int(p * 100 / t + 0.5)
    return str(min(max(share, 1), 99))


def _tile(
    tile_id: str,
    label: str,
    count: int,
    total: int,
    *,
    noun: str,
    fix_href: str,
    fix_aria: str,
    unavailable_word: str = "",
    unavailable_note: str = "",
    unavailable_label: str = "",
    unavailable_href: str = "",
    unavailable_aria: str = "",
) -> Dict[str, str]:
    """One coverage readout.

    Keys: id, label, count ("4,120"), percent ("69%"), width (meter, "0"-"100"),
    show ("true" when the figures render), word (shown instead of the figures, or
    next to them), note, link_label, link_href, link_aria, valuetext (the
    meter's spoken value). An unavailable enrichment (not installed / off) with
    no data shows its word instead of a bare zero; with data it keeps the
    figures and adds the word.
    """
    tile = {
        "id": tile_id,
        "label": label,
        "count": format_count(count) or "0",
        "percent": format_percent(count, total) or "0%",
        "width": meter_width(count, total),
        "show": "true",
        "word": "",
        "note": "",
        "link_label": "",
        "link_href": "",
        "link_aria": "",
        "valuetext": "",
    }
    tile["valuetext"] = f"{tile['percent']} of tracks {noun}"
    if total <= 0:
        tile["word"] = "No tracks yet"
        tile["show"] = "false"
        return tile
    if unavailable_word:
        tile["word"] = unavailable_word
        tile["note"] = unavailable_note
        tile["link_label"] = unavailable_label
        tile["link_href"] = unavailable_href
        tile["link_aria"] = unavailable_aria
        if count <= 0:
            tile["show"] = "false"
        return tile
    if count <= 0:
        tile["word"] = "Not run yet"
    if count < total:
        tile["link_label"] = "Fix →"
        tile["link_href"] = fix_href
        tile["link_aria"] = fix_aria
    return tile


def coverage_tiles(
    total: Any,
    tagged: Any,
    embedded: Any,
    audio: Any,
    musicbrainz: Any,
    *,
    audio_available: bool,
    audio_enabled: bool,
    mb_enabled: bool,
) -> Dict[str, Dict[str, str]]:
    """The four Dashboard coverage readouts, keyed tagged|embedded|audio|musicbrainz.

    Each readout is a share of the library with a link to the page that fills
    the gap below 100%: Tagging for tags, Doctor for embeddings, audio and
    MusicBrainz. Audio says "Not installed" when the audio package is missing
    and "Off" when it is switched off; MusicBrainz says "Off" when disabled,
    both linking to their Settings section.
    """
    t = _int(total)
    audio_word = audio_note = audio_label = audio_href = audio_aria = ""
    if not audio_available:
        audio_word = "Not installed"
        audio_note = "Optional · needs the plexmix[audio] extra"
        audio_label = "Details →"
        audio_href = "/doctor"
        audio_aria = "Details about audio analysis in Doctor"
    elif not audio_enabled:
        audio_word = "Off"
        audio_label = "Turn on →"
        audio_href = "/settings?section=audio"
        audio_aria = "Turn on audio analysis in Settings"
    mb_word = mb_label = mb_href = mb_aria = ""
    if not mb_enabled:
        mb_word = "Off"
        mb_label = "Turn on →"
        mb_href = "/settings?section=musicbrainz"
        mb_aria = "Turn on MusicBrainz in Settings"
    return {
        "tagged": _tile(
            "tagged",
            "Tagged",
            _int(tagged),
            t,
            noun="tagged",
            fix_href="/tagging",
            fix_aria="Fix tag coverage on the Tagging page",
        ),
        "embedded": _tile(
            "embedded",
            "Embedded",
            _int(embedded),
            t,
            noun="embedded",
            fix_href="/doctor",
            fix_aria="Fix embedding coverage in Doctor",
        ),
        "audio": _tile(
            "audio",
            "Audio analyzed",
            _int(audio),
            t,
            noun="analyzed",
            fix_href="/doctor",
            fix_aria="Fix audio analysis coverage in Doctor",
            unavailable_word=audio_word,
            unavailable_note=audio_note,
            unavailable_label=audio_label,
            unavailable_href=audio_href,
            unavailable_aria=audio_aria,
        ),
        "musicbrainz": _tile(
            "musicbrainz",
            "MusicBrainz genres",
            _int(musicbrainz),
            t,
            noun="with MusicBrainz genres",
            fix_href="/doctor",
            fix_aria="Fix MusicBrainz coverage in Doctor",
            unavailable_word=mb_word,
            unavailable_label=mb_label,
            unavailable_href=mb_href,
            unavailable_aria=mb_aria,
        ),
    }


def recent_playlist_row(row: tuple[Any, ...]) -> Dict[str, str]:
    """One recent-playlist row from ``(id, name, created_at, track_count)``.

    ``created_at`` is stored as naive UTC; it is shown in local time without
    seconds ("Dec 27 · 20:35"), with the year in ``created_title``.
    """
    pid, name, created_at, track_count = row
    created = stored_utc(created_at)
    count = _int(track_count)
    return {
        "id": "" if pid is None else str(pid),
        "name": str(name or "Untitled playlist"),
        "track_count": str(count),
        "track_word": "track" if count == 1 else "tracks",
        "created_label": format_short_datetime(created) if created else "",
        "created_title": format_short_datetime(created, with_year=True) if created else "",
    }


def job_snapshot(entry: Any) -> Dict[str, str]:
    """A TaskStore entry as the ``{status, progress, message}`` strings setup_steps reads."""
    return {
        "status": str(getattr(entry, "status", "") or ""),
        "progress": str(max(0, min(100, _int(getattr(entry, "progress", 0))))),
        "message": str(getattr(entry, "message", "") or ""),
    }


def _clean_error(text: str) -> str:
    cleaned = " ".join(_PLEX_TOKEN_RE.sub(r"\1[redacted]", str(text or "")).split())
    if len(cleaned) > _MAX_ERROR_CHARS:
        cleaned = cleaned[: _MAX_ERROR_CHARS - 1].rstrip() + "…"
    return cleaned


def setup_failure_copy(
    job: str,
    entry: Any,
    readiness: Mapping[str, Mapping[str, str]],
    *,
    plex_url: str = "",
    plex_library: str = "",
) -> Dict[str, str]:
    """Inline error for a setup job that failed: ``{step, title, body, action_label,
    action_href}``, in UI words (never a terminal command).

    A sync that could not connect carries ``extra["plex_error_code"]`` and is
    described by ``readiness.describe("plex", …)`` ("Plex isn't connected yet"
    with a Settings action). An index build that failed uses the embedding
    provider's readiness copy when it is failing, else the job's message.
    """
    from plexmix.services import readiness as rd

    step = SETUP_JOB_STEPS.get(job, job)
    extra = getattr(entry, "extra", None) or {}
    message = _clean_error(getattr(entry, "message", "") or "")
    if job == "sync":
        code = extra.get("plex_error_code") if isinstance(extra, Mapping) else None
        if code:
            copy = rd.describe(
                "plex",
                rd.cause_for_plex_error_code(str(code)),
                {"code": str(code), "url": plex_url, "library": plex_library},
            )
            return {
                "step": step,
                "title": copy["title"],
                "body": copy["body"],
                "action_label": copy["action_label"],
                "action_href": copy["action_href"],
            }
        return {
            "step": step,
            "title": "Sync failed",
            "body": message or "The library sync stopped with an error.",
            "action_label": "Open Library",
            "action_href": "/library",
        }
    emb = readiness.get("embeddings") or {}
    if emb.get("level") == "failing" and emb.get("title"):
        return {
            "step": step,
            "title": emb.get("title", ""),
            "body": emb.get("body", "") or message,
            "action_label": emb.get("fix_label", "") if emb.get("action_href") else "",
            "action_href": emb.get("action_href", ""),
        }
    return {
        "step": step,
        "title": "Couldn't build the search index",
        "body": message or "Generating embeddings stopped with an error.",
        "action_label": "Embedding settings",
        "action_href": "/settings?section=embedding",
    }


# ── What a finished "sync" task says ──
#
# The Library state writes the result into the task: the engine's outcome as extra
# data and one plain sentence as the message (``start_sync``, ``confirm_removal``).
# It masks that sentence when it writes it (no token, no address with credentials),
# so the sentence is shown whole here, as the Library page shows it. The key names,
# the tone rule and the fallback texts come from that module, so both pages
# announce a task alike.


def _task_extra(entry: Any) -> Mapping[str, Any]:
    extra = getattr(entry, "extra", None)
    return extra if isinstance(extra, Mapping) else {}


def _task_message(entry: Any) -> str:
    return str(getattr(entry, "message", "") or "").strip()


def completed_sync_result(entry: Any) -> tuple[str, str]:
    """``(tone, text)`` of the toast for a "sync" task that completed as a sync.

    A clean run is the success "Library synced". A run that removed nothing for a
    reason (a removal held for confirmation, an empty listing, too many failed
    reads) is a warning and never a success, and a run that could not read some
    tracks, artists or albums says how many. Both show the sentence the task
    carries, as the Library page does.
    """
    extra = _task_extra(entry)
    tone = sync_result_tone(extra)
    kept_back = extra.get(SYNC_PARTIAL_KEY) is True or _int(extra.get(UNREAD_TRACKS_KEY)) > 0
    if tone == "success" and not kept_back:
        return tone, SYNC_DONE_MESSAGE
    text = _task_message(entry)
    return (tone, text) if text else ("warning", SYNC_KEPT_BACK_MESSAGE)


def removal_run_result(entry: Any) -> tuple[str, str]:
    """``(tone, text)`` of the toast for a finished confirmed removal; ``("", "")`` for none.

    The text is the removal's own result: how many tracks were removed and where
    the backup is, that Plex lists the tracks again, or why nothing was removed. A
    removal that failed or was cancelled removed nothing. It is not a failed or a
    cancelled sync, so it is never worded as one.
    """
    status = str(getattr(entry, "status", "") or "")
    text = _task_message(entry)
    if status == "failed":
        return "error", text or REMOVAL_ERROR_MESSAGE
    if status == "cancelled":
        return "warning", text or REMOVAL_CANCELLED_MESSAGE
    if status == "completed" and text:
        return sync_result_tone(_task_extra(entry)), text
    return "", ""


class DashboardState(AppState):
    recent_playlists: List[Dict[str, str]] = []

    # First-run checklist jobs. ``setup_jobs`` holds a {status, progress, message}
    # snapshot per running setup job ("sync", "embedding"); ``setup_polling``
    # mounts the 2 s poller only while one runs (or was just requested);
    # ``setup_error`` is the inline failure of the last setup job, shown in its
    # step until dismissed or retried ({step, title, body, action_label,
    # action_href}).
    setup_jobs: Dict[str, Dict[str, str]] = {}
    setup_polling: bool = False
    setup_error: Dict[str, str] = {}
    # Backend only: job -> TaskStore ``started_at`` of the entry present when a
    # step asked for the job (so an old finished entry is never mistaken for the
    # new job), job -> ``started_at`` of the running entry being shown, and how
    # many polls a requested job has not shown up.
    _setup_watch: Dict[str, float] = {}
    _setup_tracked: Dict[str, float] = {}
    _setup_misses: int = 0

    @rx.event
    def on_load(self) -> Any:
        """Load dashboard data when the page loads."""
        logger.debug("DashboardState.on_load called")
        if not self.check_auth():
            self.is_page_loading = False
            return None
        # Config status + library stats, plus the disconnected-session sweep every
        # other page runs (the dashboard is the landing route).
        self.load_app_data()
        self.load_recent_playlists()
        self._sync_setup_jobs()
        self.is_page_loading = False
        # Re-check Plex reachability in the background when due (TTL + fingerprint).
        return [rx.console_log("Dashboard loaded"), *self._plex_readiness_events()]

    def load_recent_playlists(self) -> None:
        """Read the latest playlists (id, name, local created time, track count)."""
        try:
            from plexmix.config.settings import Settings

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                self.recent_playlists = []
                return

            with closing(sqlite3.connect(str(db_path))) as conn:
                rows = conn.execute(
                    """
                    SELECT p.id, p.name, p.created_at, COUNT(pt.track_id) AS track_count
                    FROM playlists p
                    LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
                    GROUP BY p.id, p.name, p.created_at
                    ORDER BY p.created_at DESC
                    LIMIT ?
                    """,
                    (RECENT_PLAYLIST_LIMIT,),
                ).fetchall()
            self.recent_playlists = [recent_playlist_row(tuple(row)) for row in rows]
        except Exception as e:
            logger.error("Error loading recent playlists: %s", e)
            self.recent_playlists = []

    @rx.var(cache=True)
    def has_recent_playlists(self) -> bool:
        """True when there is at least one saved playlist to list."""
        return bool(self.recent_playlists)

    def refresh_stats(self) -> None:
        self.load_library_stats()
        self.load_recent_playlists()

    # ── Coverage and header readouts (design D14) ──

    @rx.var(cache=True)
    def coverage(self) -> Dict[str, Dict[str, str]]:
        """The four coverage readouts (see :func:`coverage_tiles`)."""
        return coverage_tiles(
            self.stat_total,
            self.stat_tagged,
            self.stat_embedded,
            self.stat_audio,
            self.stat_musicbrainz,
            audio_available=self.audio_available,
            audio_enabled=self.audio_analysis_enabled,
            mb_enabled=self.mb_enabled,
        )

    @rx.var(cache=True)
    def library_count(self) -> str:
        """The library total with thousands separators ("5,943")."""
        return format_count(_int(self.stat_total)) or "0"

    @rx.var(cache=True)
    def library_noun(self) -> str:
        """ "track" or "tracks", to follow :attr:`library_count`."""
        return "track" if _int(self.stat_total) == 1 else "tracks"

    @rx.var(cache=True)
    def sync_readout(self) -> str:
        """ "synced 2 h ago", or "not synced yet" when no sync was recorded."""
        if self.last_sync_ago:
            return f"synced {self.last_sync_ago}"
        return "not synced yet"

    # ── First-run checklist (design D13) ──

    @rx.var(cache=True)
    def setup_steps(self) -> List[Dict[str, str]]:
        """The five setup steps from ``readiness.setup_steps`` (always five rows)."""
        from plexmix.services import readiness as rd

        return rd.setup_steps(
            self.readiness or {},
            {
                "total_tracks": self.stat_total,
                "missing_embeddings": self.stat_missing_embeddings,
            },
            self.setup_jobs or {},
        )

    @rx.var(cache=True)
    def setup_incomplete(self) -> bool:
        """True while a setup step is open. False until the first stats load, so
        the checklist never flashes over a library that is already set up, and
        while the settings can't be read (the steps would rest on unknown counts;
        the overview shows that error instead)."""
        from plexmix.services import readiness as rd

        if not self.library_stats_loaded or self.settings_unreadable:
            return False
        return rd.setup_incomplete(self.readiness or {})

    @rx.var(cache=True)
    def generate_primary(self) -> bool:
        """True once setup is complete: then "Generate Playlist" is the primary quick action."""
        from plexmix.services import readiness as rd

        if not self.library_stats_loaded or self.settings_unreadable:
            return False
        return not rd.setup_incomplete(self.readiness or {})

    def _sync_setup_jobs(self) -> None:
        """Show setup jobs that are running now (started here or on another page)."""
        from plexmix.ui.job_manager import task_store

        jobs: Dict[str, Dict[str, str]] = {}
        tracked: Dict[str, float] = {}
        for job in SETUP_JOBS:
            entry = task_store.get(job)
            if entry is not None and entry.status == "running":
                jobs[job] = job_snapshot(entry)
                tracked[job] = float(entry.started_at)
            elif job in self._setup_watch and job in self.setup_jobs:
                jobs[job] = self.setup_jobs[job]  # requested, not registered yet
        self.setup_jobs = jobs
        self._setup_tracked = tracked
        self.setup_polling = bool(tracked) or bool(self._setup_watch)

    @rx.event
    def watch_setup_job(self, job: str) -> None:
        """A setup step is about to start ``job``: follow it with the poller.

        Dispatched just before ``LibraryState.start_sync`` (Sync library) and
        ``LibraryState.confirm_embed_all`` (Build search index), so the entry
        that exists now is recorded as the baseline: a finished entry with the
        same start time is an older run, not this one. Shows the step as
        starting right away and clears that step's earlier error.
        """
        if job not in SETUP_JOBS:
            return
        from plexmix.ui.job_manager import task_store

        entry = task_store.get(job)
        baseline = float(entry.started_at) if entry is not None else -1.0
        self._setup_watch = {**self._setup_watch, job: baseline}
        self._setup_misses = 0
        if job not in self.setup_jobs:
            self.setup_jobs = {
                **self.setup_jobs,
                job: {"status": "running", "progress": "0", "message": "Starting…"},
            }
        if self.setup_error.get("step") == SETUP_JOB_STEPS[job]:
            self.setup_error = {}
        self.setup_polling = True

    @rx.event
    def dismiss_setup_error(self) -> None:
        """Hide the inline setup-job failure."""
        self.setup_error = {}

    @rx.event
    def poll_setup_progress(self) -> Any:
        """Poll the TaskStore for the setup jobs (mounted only while one runs).

        Updates each running job's progress; when one finishes, reloads the
        library stats and readiness (so the checklist moves on), shows a failure
        inline in its step, and unmounts the poller once nothing runs.
        """
        from plexmix.ui.job_manager import task_store

        jobs = dict(self.setup_jobs)
        watch = dict(self._setup_watch)
        tracked = dict(self._setup_tracked)
        finished: list[tuple[str, Any]] = []
        for job in SETUP_JOBS:
            entry = task_store.get(job)
            if entry is not None and entry.status == "running":
                jobs[job] = job_snapshot(entry)
                tracked[job] = float(entry.started_at)
                watch.pop(job, None)
                continue
            if job in tracked:
                tracked.pop(job)
                jobs.pop(job, None)
                finished.append((job, entry))
            elif job in watch:
                if entry is not None and float(entry.started_at) != watch[job]:
                    # Started and finished between two polls (a fast failure).
                    watch.pop(job)
                    jobs.pop(job, None)
                    finished.append((job, entry))
            else:
                jobs.pop(job, None)

        misses = self._setup_misses
        if watch and not finished and not tracked:
            misses += 1
            if misses >= SETUP_WATCH_MAX_MISSES:
                for job in watch:
                    jobs.pop(job, None)
                watch = {}
                misses = 0
        else:
            misses = 0

        self.setup_jobs = jobs
        self._setup_watch = watch
        self._setup_tracked = tracked
        self._setup_misses = misses
        self.setup_polling = bool(tracked) or bool(watch)
        if not finished:
            return None

        # The checklist moves on from the fresh facts (and readiness records).
        self.load_library_stats()
        events: list[Any] = []
        for job, entry in finished:
            events.extend(self._finish_setup_job(job, entry))
        return events or None

    def _finish_setup_job(self, job: str, entry: Any) -> list[Any]:
        """Feedback for one finished setup job (toast, plus inline copy on failure).

        The "sync" task also runs a removal confirmed on the Library page. That is
        not a sync: it reports its own result (:func:`removal_run_result`) and the
        sync step's inline error stays as it is. A sync that completed says what it
        kept back (:func:`completed_sync_result`).
        """
        status = str(getattr(entry, "status", "") or "") if entry is not None else ""
        step = SETUP_JOB_STEPS[job]
        if job == "sync" and _task_extra(entry).get(REMOVAL_RUN_KEY):
            tone, text = removal_run_result(entry)
            return [result_toast(tone, text)] if text else []
        if status == "failed":
            copy = setup_failure_copy(
                job,
                entry,
                self.readiness or {},
                plex_url=self.plex_server_url,
                plex_library=self.plex_library_name,
            )
            self.setup_error = copy
            return [rx.toast.error(copy["title"])]
        if self.setup_error.get("step") == step:
            self.setup_error = {}
        if status == "cancelled":
            word = "Sync cancelled" if job == "sync" else "Search index build cancelled"
            return [rx.toast.warning(word)]
        if status == "completed":
            if job == "sync":
                return [result_toast(*completed_sync_result(entry))]
            return [rx.toast.success("Search index built")]
        return []
