import asyncio
import hmac
import logging
import time
from datetime import datetime
import reflex as rx
from typing import Any, Callable, Mapping, NamedTuple, Optional, TypeVar

from plexmix.config.constants import PLEX_READINESS_TIMEOUT_S
from plexmix.ui import auth_session as ui_auth_session
from plexmix.ui.auth_session import COOKIE_NAME, SESSION_MAX_AGE_S
from plexmix.ui.utils.errors import friendly_error
from plexmix.ui.utils.helpers import format_relative, format_short_datetime, safe_error_text

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])


def _sanctioned_reflex_override(fn: _F) -> _F:
    """Mark *fn* as an intended override of a built-in Reflex State method.

    Reflex rejects State subclasses that shadow its built-in methods
    (``EventHandlerShadowsBuiltInStateMethodError``) unless the function carries
    ``__override_base_method__``, the marker Reflex's own (private)
    ``reflex.state._override_base_method`` sets in 0.8.24. Recheck on Reflex
    upgrades: if the marker stops being honoured, importing this module fails.
    """
    setattr(fn, "__override_base_method__", True)
    return fn


class AppState(rx.State):
    # Authentication state
    is_authenticated: bool = False
    # False until the server has confirmed whether a password is required; the
    # shell shows a neutral boot screen meanwhile (never the login card).
    auth_checked: bool = False
    auth_required: bool = False
    login_password: str = ""
    login_error: str = ""
    # Counts failed sign-ins. The login view keys its error text by it, so a
    # repeated "Incorrect password" is a new node in the alert region and is
    # announced again (an unchanged string would make no DOM change).
    login_failures: int = 0
    # Signed, expiring session token (see plexmix.ui.auth_session) shared by every
    # tab of this browser, so one sign-in covers them all until logout, a password
    # change or expiry.
    auth_session: str = rx.Cookie(
        "",
        name=COOKIE_NAME,
        path="/",
        max_age=SESSION_MAX_AGE_S,
        same_site="strict",
    )

    # ── Readiness (webui-honest-status-and-first-run; see services/readiness.py) ──
    # One snapshot per component, recomputed by check_configuration_status /
    # load_library_stats (every page load) and by refresh_plex_readiness. Keys:
    # plex, ai, embeddings, library, index, audio, musicbrainz; every entry has
    # the same string keys (see ``readiness.ComponentStatus.as_dict`` plus
    # ``checked_ago`` and ``checked_title``). Never holds a secret or fingerprint.
    readiness: dict[str, dict[str, str]] = {}
    # False until the first stats load finished: surfaces show a skeleton, never
    # a fabricated zero, until then.
    library_stats_loaded: bool = False
    # True when the last load could not read the settings (or library): the
    # counts are unknown, not zero, so the Dashboard shows that error instead of
    # "No tracks yet" and a setup checklist.
    settings_unreadable: bool = False

    # Library stats as ints (format them with ui/utils/helpers.format_count etc.).
    stat_total: int = 0
    stat_tagged: int = 0
    stat_untagged: int = 0
    stat_embedded: int = 0
    stat_missing_embeddings: int = 0
    stat_audio: int = 0
    stat_musicbrainz: int = 0
    stat_db_exists: bool = False
    stat_index_dimension: int = 0
    stat_expected_dimension: int = 0
    # Optional enrichments: installed (essentia importable) and switched on in Settings.
    audio_available: bool = False
    audio_analysis_enabled: bool = False
    mb_enabled: bool = False
    # Last successful sync (stored as UTC): local ISO time, "2 h ago" and
    # "Dec 27, 2025 · 20:35" (for a ``title``); "" when there was none.
    last_sync_at: str = ""
    last_sync_ago: str = ""
    last_sync_title: str = ""

    # Configuration details (display names, never ``.title()``-cased ids)
    plex_library_name: str = ""
    plex_server_url: str = ""
    ai_provider_name: str = ""
    ai_model_name: str = ""
    embedding_provider_name: str = ""
    embedding_model_name: str = ""

    # Legacy string counts (plain ``str(n)``, "0" when unknown or empty), kept for
    # callers that compare against "0"; new code reads the ``stat_*`` ints.
    total_tracks: str = "0"
    embedded_tracks: str = "0"
    audio_analyzed_tracks: str = "0"
    musicbrainz_enriched_tracks: str = "0"
    last_sync: Optional[str] = None

    # The readiness "index" body when the index dimension mismatches the provider
    # (names both dimensions, no emoji), else "".
    embedding_dimension_warning: str = ""

    # load_app_data runs check_configuration_status then load_library_stats; the
    # first already refreshed everything, so the second skips one repeat read.
    _status_prefetched: bool = False

    current_task: Optional[str] = None
    task_progress: int = 0

    # Mobile navigation state
    is_mobile_nav_open: bool = False

    # Page loading state for navigation transitions
    is_page_loading: bool = True

    # Changelog modal: release notes rendered to HTML on the server when it opens.
    show_changelog: bool = False
    changelog_html: str = ""
    changelog_version: str = ""
    changelog_available: bool = False

    # Cross-page rerun config (set by History, consumed by Generator)
    _rerun_generation_config: str = ""

    @_sanctioned_reflex_override
    def _reset_client_storage(self) -> None:
        """Also forget the auth view whenever Reflex re-hydrates this session.

        Reflex calls this on every hydrate (page load, reload, websocket
        reconnect) before sending the full state to the browser. Without this,
        that state would replay the session's earlier ``is_authenticated`` and
        ``auth_checked`` values, so a reload after a logout in another tab, a
        password change or an expired session would briefly render the shell and
        the page's cached content before ``check_auth`` showed the login view.
        Resetting both flags shows the neutral boot screen until the page's
        ``on_load`` has re-verified the session cookie.
        """
        self.auth_checked = False
        self.is_authenticated = False
        super()._reset_client_storage()

    @rx.event
    def set_page_loading(self, loading: bool):
        """Set the page loading state."""
        self.is_page_loading = loading

    @rx.var(cache=True)
    def show_doctor_badge(self) -> bool:
        """App-wide attention flag for the Doctor rail badge, from ``readiness``.

        True when a component required for generation (Plex, embeddings, library,
        search index) is not set up, is failing, or has a blocking cause (for
        example a missing local package or an index dimension mismatch). A
        component that is only "set up · not checked" never raises the badge.
        """
        from plexmix.services.readiness import needs_attention

        return needs_attention(self.readiness or {})

    @rx.var(cache=True)
    def plex_configured(self) -> bool:
        """Plex is set up (URL, token and library), whether or not it was checked."""
        return _is_set_up(self.readiness, "plex")

    @rx.var(cache=True)
    def ai_provider_configured(self) -> bool:
        """The AI provider is set up and nothing local stops it from starting."""
        return _is_set_up(self.readiness, "ai")

    @rx.var(cache=True)
    def embedding_provider_configured(self) -> bool:
        """The embedding provider is set up and nothing local stops it from starting."""
        return _is_set_up(self.readiness, "embeddings")

    # ── Shared job-cancel confirmation (shell-level: works from any page + palette) ──
    pending_cancel_job: str = ""

    @rx.event
    def request_cancel(self, job_type: str):
        """Open the shared confirm dialog for cancelling a job (``"__all__"`` = global)."""
        self.pending_cancel_job = job_type

    @rx.event
    def dismiss_cancel(self):
        self.pending_cancel_job = ""

    @rx.event
    def set_cancel_dialog_open(self, is_open: bool) -> None:
        """``on_open_change`` of the shell's cancel AlertDialog.

        Radix reports a close (Escape, or "Keep running") as ``False``: that
        dismisses the request, so a later ``confirm_cancel`` cancels nothing.
        ``True`` is a no-op because the dialog only opens via ``request_cancel``.
        Idempotent: closing an already-dismissed dialog changes nothing.
        """
        if not is_open:
            self.pending_cancel_job = ""

    @rx.event
    def confirm_cancel(self):
        """Confirm: cancel the pending job, or all running cancellable work."""
        from plexmix.ui.job_manager import task_store

        job = self.pending_cancel_job
        self.pending_cancel_job = ""
        if not job:
            return None
        if job == "__all__":
            task_store.cancel_all_running()
            # Playlist generation is not TaskStore-backed — signal its own event too.
            from plexmix.ui.states.generator_state import GeneratorState

            return GeneratorState.cancel_generation
        task_store.cancel(job)
        return None

    @rx.event
    def open_changelog(self) -> None:
        """Open the changelog modal with the running version and release notes.

        The notes are rendered on the server (see ``plexmix.ui.changelog``) and
        cached by file path and mtime, so they are read once and re-read only
        when the file changes. Vars are reassigned only when their value changed,
        so reopening does not resend the notes to the browser.
        """
        from plexmix.ui.changelog import load_changelog

        view = load_changelog()
        if self.changelog_html != view.html:
            self.changelog_html = view.html
        if self.changelog_version != view.version:
            self.changelog_version = view.version
        if self.changelog_available != view.available:
            self.changelog_available = view.available
        self.show_changelog = True

    @rx.event
    def close_changelog(self):
        """Close the changelog modal."""
        self.show_changelog = False

    @rx.event
    def toggle_mobile_nav(self):
        """Toggle the mobile navigation sidebar."""
        self.is_mobile_nav_open = not self.is_mobile_nav_open

    @rx.event
    def close_mobile_nav(self):
        """Close the mobile navigation sidebar."""
        self.is_mobile_nav_open = False

    @staticmethod
    def _configured_ui_password() -> Optional[str]:
        """Return the configured UI password (config file or env var), or None."""
        import os

        try:
            from plexmix.config.settings import Settings

            configured_password = Settings.load_from_file().ui.password
        except Exception:
            configured_password = None
        if not configured_password:
            configured_password = os.environ.get("PLEXMIX_UI_PASSWORD")
        return configured_password or None

    def check_auth(self) -> bool:
        """Check if authentication is required and whether user is authenticated.

        Returns True if the user is authenticated (or no password is set).
        Returns False if the user needs to log in.
        """
        configured_password = self._configured_ui_password()
        # The auth requirement is now known: the shell leaves its boot screen.
        self.auth_checked = True

        if not configured_password:
            # No password configured — auto-authenticate
            self.auth_required = False
            self.is_authenticated = True
            return True

        self.auth_required = True
        # The signed browser-session cookie is authoritative (Reflex re-reads it
        # from the browser on every page load), so a sign-in in another tab lets
        # this one in, while a logout, a password change or an expired session
        # ends access on the next load.
        self.is_authenticated = ui_auth_session.verify(
            self.auth_session, configured_password, time.time()
        )
        return self.is_authenticated

    def access_allowed(self) -> bool:
        """Server-side authorization gate for sensitive event handlers.

        Reflex exposes every public State method as a websocket-dispatchable
        event, so enforcing the password only in ``on_load`` and in client-side
        rendering leaves the backend open: an unauthenticated client could invoke
        handlers directly to read secrets (``load_settings``) or trigger
        destructive/costly work. Sensitive handlers must call this and bail out
        when it returns False.

        Unlike :meth:`check_auth`, this is read-only (it never mutates state), so
        it is safe to call at the top of a background event before entering
        ``async with self``.

        The gate re-verifies the signed session token rather than trusting
        ``is_authenticated``: Reflex auto-generates a public ``set_is_authenticated``
        event for that var, so any websocket client could flip the flag. The
        token is HMAC-signed with the server key, so a client cannot forge one.
        For an honest client the two agree (``check_auth`` and ``attempt_login``
        derive the flag from the same token); a password change or expiry now
        also takes effect mid-page.
        """
        configured_password = self._configured_ui_password()
        if not configured_password:
            return True
        return ui_auth_session.verify(self.auth_session, configured_password, time.time())

    @rx.event
    def attempt_login(self, form_data: dict) -> None:
        """Validate the submitted password and, on success, issue the session cookie."""
        password = str(form_data.get("password", "") or "")
        configured_password = self._configured_ui_password()
        self.auth_checked = True
        self.login_password = ""

        # Compare as UTF-8 bytes: hmac.compare_digest raises TypeError on str
        # inputs containing non-ASCII characters.
        if configured_password and hmac.compare_digest(
            password.encode("utf-8"), configured_password.encode("utf-8")
        ):
            self.auth_required = True
            self.is_authenticated = True
            self.login_error = ""
            # Written to the browser cookie by Reflex, so other tabs are let in too.
            self.auth_session = ui_auth_session.issue(configured_password)
        else:
            self.login_error = "Incorrect password"
            self.login_failures += 1

    @rx.event
    def logout(self) -> Any:
        """Sign out this browser: clear auth state and the session cookie."""
        self.is_authenticated = False
        self.login_password = ""
        self.auth_session = ""
        return rx.remove_cookie(COOKIE_NAME)

    def load_app_data(self) -> None:
        """Load shared app data (config status + library stats) and reap orphaned
        session work. A plain method so subclass on_load handlers can invoke it
        directly: calling ``super().on_load()`` on the @rx.event-decorated handler
        only builds and discards an EventSpec, so this body never ran from
        subclasses.

        Every page load from any client runs a sweep that cancels session-scoped
        work (e.g. a playlist generation) and drops staged secret drafts only for
        sessions whose websocket has been disconnected for longer than
        ``SESSION_REAP_GRACE_S``; the first page load also starts the periodic
        sweep (see ``plexmix.ui.session_liveness``). Loading a page never cancels
        another live session's work, and when liveness is unknown nothing is
        cancelled. TaskStore jobs (sync, tagging, Doctor fixes, …) are not
        session-scoped and are never touched here."""
        from plexmix.ui.session_liveness import (
            ensure_periodic_sweep,
            sweep_disconnected_sessions,
        )

        # Reaps job handles and staged secret drafts of long-disconnected sessions.
        sweep_disconnected_sessions()
        # From the first page load on, the same sweep also runs periodically, so a
        # closed tab is reaped even when no other tab ever loads a page.
        ensure_periodic_sweep()

        # Config status + readiness + library stats. check_configuration_status
        # already refreshes everything (it also runs alone, e.g. after a save), so
        # load_library_stats reuses that refresh instead of reading it all again.
        self.check_configuration_status()
        self._status_prefetched = True
        try:
            self.load_library_stats()
        finally:
            self._status_prefetched = False

    @rx.event
    def on_load(self):
        """Load app data when the page loads."""
        logger.debug("AppState.on_load called")
        if not self.check_auth():
            self.is_page_loading = False
            return
        self.load_app_data()
        return rx.console_log("App state loaded")

    # ── Readiness ─────────────────────────────────────────────────────

    def check_configuration_status(self) -> None:
        """Refresh configuration details, library stats and ``readiness``.

        Reads the settings, gathers local facts (``readiness.collect_facts``: DB
        counts, index metadata, installed packages, key presence) and evaluates
        every component. Nothing is sent over the network: Plex reachability is
        checked by :meth:`refresh_plex_readiness`, and cloud keys only by a
        Settings test or a real job.
        """
        self._refresh_status()

    def load_library_stats(self) -> None:
        """Refresh library stats and ``readiness`` (see :meth:`check_configuration_status`).

        Sets ``library_stats_loaded`` once the load finished, whatever the result.
        """
        if self._status_prefetched:
            # load_app_data: check_configuration_status refreshed it all just now.
            self._status_prefetched = False
            return
        self._refresh_status()

    def _refresh_status(self) -> None:
        from plexmix.config.settings import Settings
        from plexmix.services import readiness as rd

        try:
            settings = Settings.load_from_file()
            facts = rd.collect_facts(settings)
            status = rd.evaluate(settings, facts)
        except Exception as e:
            # Never the raw text: config errors can echo rejected (possibly secret) input.
            logger.error("Error checking configuration: %s", config_error_text(e))
            self._apply_unreadable_status()
            return
        self._apply_status(settings, facts, status)

    def _apply_status(self, settings: Any, facts: Any, status: Mapping[str, Any]) -> None:
        """Write one evaluation (``readiness.evaluate``) and its facts to state."""
        from plexmix.services import readiness as rd

        now = datetime.now()
        snapshot = _with_check_times(rd.status_snapshot(status), now)
        self.readiness = snapshot

        self.plex_library_name = settings.plex.library_name or ""
        self.plex_server_url = settings.plex.url or ""
        self.ai_provider_name = snapshot["ai"]["label"]
        self.ai_model_name = snapshot["ai"]["model"]
        self.embedding_provider_name = snapshot["embeddings"]["label"]
        self.embedding_model_name = snapshot["embeddings"]["model"]

        self.stat_db_exists = bool(facts.db_exists)
        self.stat_total = int(facts.total_tracks)
        self.stat_untagged = int(facts.untagged_tracks)
        self.stat_tagged = int(facts.tagged_tracks)
        self.stat_embedded = int(facts.embedded_tracks)
        self.stat_missing_embeddings = int(facts.missing_embeddings)
        self.stat_audio = int(facts.audio_tracks)
        self.stat_musicbrainz = int(facts.musicbrainz_tracks)
        self.stat_index_dimension = int(facts.index_dimension)
        self.stat_expected_dimension = int(facts.expected_dimension)
        self.audio_available = bool(facts.essentia_available)
        self.audio_analysis_enabled = bool(facts.audio_enabled)
        self.mb_enabled = bool(facts.musicbrainz_enabled)

        self.total_tracks = str(facts.total_tracks)
        self.embedded_tracks = str(facts.embedded_tracks)
        self.audio_analyzed_tracks = str(facts.audio_tracks)
        self.musicbrainz_enriched_tracks = str(facts.musicbrainz_tracks)

        # SyncHistory.sync_date is stored as naive UTC (models default to
        # datetime.utcnow), so read it as UTC and show it in local time.
        synced = stored_utc(facts.last_sync)
        self.last_sync_at = _local_iso(synced) if synced else ""
        self.last_sync_ago = format_relative(synced, now) if synced else ""
        self.last_sync_title = format_short_datetime(synced, with_year=True) if synced else ""
        self.last_sync = self.last_sync_title or None
        library = snapshot.get("library")
        if synced is not None and library is not None and library.get("checked_at"):
            # The library is "checked" by its last sync: same UTC reading.
            library["checked_at"] = self.last_sync_at
            library["checked_ts"] = str(int(synced.timestamp()))
            library["checked_ago"] = self.last_sync_ago
            library["checked_title"] = self.last_sync_title

        index = snapshot["index"]
        self.embedding_dimension_warning = (
            index["body"] if index["cause"] == rd.Cause.DIMENSION_MISMATCH.value else ""
        )
        self.settings_unreadable = False
        self.library_stats_loaded = True

    def _apply_unreadable_status(self) -> None:
        """The settings or library could not be read: report it, never "ready".

        Every component fails with an "unknown" cause (which never disables an
        action) and the counts read as unknown zeros behind ``library_stats_loaded``;
        ``settings_unreadable`` tells the Dashboard not to present those zeros (or
        a setup checklist built on them) as the library's state.
        """
        from plexmix.services import readiness as rd

        detail = "PlexMix couldn't read its settings. Details are in the server log."
        entries: dict[str, dict[str, str]] = {}
        for component in rd.COMPONENTS:
            copy = rd.describe(component, rd.Cause.UNKNOWN, {"detail": detail})
            entry = rd.ComponentStatus(
                component,
                rd.Level.FAILING,
                rd.Cause.UNKNOWN,
                label=rd.COMPONENT_LABELS.get(component, component),
                title=copy["title"],
                body=copy["body"],
                fix_label=copy["action_label"],
                action_href=copy["action_href"],
                action_label="Fix" if copy["action_href"] else "",
            ).as_dict()
            entries[component] = entry
        self.readiness = _with_check_times(entries, datetime.now())
        self.plex_library_name = self.plex_server_url = ""
        self.ai_provider_name = self.ai_model_name = ""
        self.embedding_provider_name = self.embedding_model_name = ""
        for name in (
            "stat_total",
            "stat_tagged",
            "stat_untagged",
            "stat_embedded",
            "stat_missing_embeddings",
            "stat_audio",
            "stat_musicbrainz",
            "stat_index_dimension",
            "stat_expected_dimension",
        ):
            setattr(self, name, 0)
        self.stat_db_exists = False
        self.audio_available = self.audio_analysis_enabled = self.mb_enabled = False
        self.total_tracks = self.embedded_tracks = "0"
        self.audio_analyzed_tracks = self.musicbrainz_enriched_tracks = "0"
        self.last_sync = None
        self.last_sync_at = self.last_sync_ago = self.last_sync_title = ""
        self.embedding_dimension_warning = ""
        self.settings_unreadable = True
        self.library_stats_loaded = True

    def _plex_readiness_events(self) -> list[Any]:
        """``[AppState.refresh_plex_readiness]`` when this page should (re)check Plex.

        For the Dashboard, Doctor and Generator ``on_load``. Due when Plex is set
        up and has no current result, its result is older than
        ``PLEX_READINESS_TTL_S``, or a check is in flight (so this tab receives
        its outcome). The event itself re-checks the TTL and the configuration
        fingerprint and dedupes concurrent checks, so returning it is cheap.
        """
        from plexmix.config.constants import PLEX_READINESS_TTL_S

        entry = (self.readiness or {}).get("plex") or {}
        level = entry.get("level", "")
        if level in ("unchecked", "checking"):
            return [AppState.refresh_plex_readiness]
        if level in ("ready", "failing"):
            try:
                checked = float(entry.get("checked_ts") or 0)
            except ValueError:
                checked = 0.0
            if not checked or time.time() - checked >= PLEX_READINESS_TTL_S:
                return [AppState.refresh_plex_readiness]
        return []

    @rx.event(background=True)
    async def refresh_plex_readiness(self, force: bool = False) -> None:
        """Check that Plex answers and lists the configured music library.

        Free and bounded (design D3): skipped unless Plex is set up and there is no
        result for the current configuration fingerprint younger than
        ``PLEX_READINESS_TTL_S`` (``force`` skips only the age check); concurrent
        requests (duplicate page triggers, other tabs) share one check through
        ``readiness_store.begin_check``. The probe runs ``PlexServer(url, token,
        timeout=PLEX_READINESS_TIMEOUT_S)`` and ``library.sections()`` in an
        executor and records ready / unreachable / timeout / invalid_credentials /
        library_not_found / unknown. ``readiness`` shows "Checking…" meanwhile and
        is refreshed under ``async with self`` when the result is in. The token is
        never logged or stored; only a hash of it is part of the fingerprint.
        """
        if not self.access_allowed():
            return
        from plexmix.services import readiness as rd

        store = rd.readiness_store
        loop = asyncio.get_running_loop()
        try:
            inputs = await loop.run_in_executor(None, _plex_probe_inputs)
        except Exception as e:
            logger.warning("Plex readiness check skipped: %s", type(e).__name__)
            return
        if inputs is None:
            return  # Plex isn't set up: local facts already say so.
        if not force and not store.needs_check("plex", inputs.fingerprint):
            # A current result exists (perhaps recorded by another tab while this
            # one showed "Checking…"): show it rather than keep a stale snapshot.
            await self._publish_fresh_status()
            return
        if not store.begin_check("plex"):
            # Another tab or trigger is checking: wait for its result, then show it.
            deadline = time.monotonic() + PLEX_PROBE_DEADLINE_S + 1.0
            while store.is_checking("plex") and time.monotonic() < deadline:
                await asyncio.sleep(0.25)
            await self._publish_fresh_status()
            return
        try:
            await self._publish_status(inputs.settings, inputs.facts)  # "Checking…"
            try:
                libraries = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        probe_plex_libraries,
                        inputs.url,
                        inputs.token,
                        float(PLEX_READINESS_TIMEOUT_S),
                    ),
                    timeout=PLEX_PROBE_DEADLINE_S,
                )
                level, cause = plex_probe_outcome(libraries, inputs.library)
                message = ""
            except Exception as e:
                level = rd.Level.FAILING.value
                cause = plex_failure_cause(e)
                message = (
                    friendly_error(e, [inputs.token]) if cause == rd.Cause.UNKNOWN.value else ""
                )
                logger.info("Plex readiness check failed: %s (%s)", cause, type(e).__name__)
            store.record("plex", level, cause, message=message, fingerprint=inputs.fingerprint)
        finally:
            store.end_check("plex")
        # The probe can take seconds: re-read the facts so a sync or index build
        # that finished meanwhile isn't overwritten by the ones read before it.
        await self._publish_fresh_status()

    async def _publish_fresh_status(self) -> None:
        """Re-read the settings and local facts and publish ``readiness`` (in an executor)."""
        loop = asyncio.get_running_loop()
        try:
            settings, facts, status = await loop.run_in_executor(None, _fresh_status)
        except Exception as e:
            logger.warning("Could not refresh readiness: %s", type(e).__name__)
            return
        async with self:
            self._apply_status(settings, facts, status)

    async def _publish_status(self, settings: Any, facts: Any) -> None:
        """Re-evaluate against the store (in an executor) and write ``readiness``."""
        from plexmix.services import readiness as rd

        loop = asyncio.get_running_loop()
        try:
            status = await loop.run_in_executor(None, rd.evaluate, settings, facts)
        except Exception as e:
            logger.warning("Could not evaluate readiness: %s", type(e).__name__)
            return
        async with self:
            self._apply_status(settings, facts, status)


# ── Readiness helpers (pure; no Reflex) ───────────────────────────────

# Overall bound on one Plex check: PlexServer() and library.sections() are two
# requests, each bounded by PLEX_READINESS_TIMEOUT_S, plus a second of slack.
PLEX_PROBE_DEADLINE_S: float = 2 * PLEX_READINESS_TIMEOUT_S + 1.0


def _is_set_up(readiness: Mapping[str, Mapping[str, str]], component: str) -> bool:
    """Set up = has its configuration and no blocking local cause (checked or not)."""
    entry = (readiness or {}).get(component) or {}
    level = entry.get("level", "")
    return bool(level) and level != "not_set_up" and entry.get("blocking") != "true"


def config_error_text(exc: BaseException) -> str:
    """Log text for a configuration that could not be read or evaluated.

    Only error kinds whose text is known not to echo config values are described:
    pydantic validation errors and YAML parse errors (both through
    ``safe_error_text``, which drops rejected values and source snippets), OS
    errors (a path) and a settings environment variable of the wrong type (its
    text names the variable, never the value). Anything else is named by its
    type, because its message can quote config.yaml content such as a plaintext
    token.
    """
    from pydantic import ValidationError

    from plexmix.config.settings import SettingsEnvError
    from plexmix.ui.utils.helpers import is_yaml_error

    if isinstance(exc, (ValidationError, OSError, SettingsEnvError)) or is_yaml_error(exc):
        return safe_error_text(exc)
    return type(exc).__name__


def stored_utc(value: Any) -> Optional[datetime]:
    """A timestamp the models store as naive UTC (``datetime.utcnow``), made aware.

    ``SyncHistory.sync_date`` and ``Playlist.created_at`` default to
    ``datetime.utcnow()``; read naively they would be off by the UTC offset
    ("synced 6 h ago" right after a sync). Aware values are kept; None or
    unparseable input gives None.
    """
    from datetime import timezone

    from plexmix.ui.utils.helpers import _coerce_datetime

    parsed = _coerce_datetime(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _local_iso(moment: datetime) -> str:
    """An aware datetime as a naive local ISO string (seconds precision)."""
    try:
        return moment.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return ""


def _with_check_times(
    snapshot: dict[str, dict[str, str]], now: datetime
) -> dict[str, dict[str, str]]:
    """Add ``checked_ago`` ("4 min ago") and ``checked_title`` (absolute) to each entry."""
    for entry in snapshot.values():
        checked = entry.get("checked_at", "")
        entry["checked_ago"] = format_relative(checked, now) if checked else ""
        entry["checked_title"] = format_short_datetime(checked, with_year=True) if checked else ""
    return snapshot


class PlexProbeInputs(NamedTuple):
    """What one Plex check needs. Server-side only: holds the token."""

    settings: Any
    facts: Any
    url: str
    library: str
    token: str
    fingerprint: str

    def __repr__(self) -> str:  # never print the token
        return f"PlexProbeInputs(url={self.url!r}, library={self.library!r})"


def _fresh_status() -> tuple[Any, Any, dict[str, Any]]:
    """``(settings, facts, readiness.evaluate(...))`` read now (blocking; run in an executor)."""
    from plexmix.config.settings import Settings
    from plexmix.services import readiness as rd

    settings = Settings.load_from_file()
    facts = rd.collect_facts(settings)
    return settings, facts, rd.evaluate(settings, facts)


def _plex_probe_inputs() -> Optional[PlexProbeInputs]:
    """Load settings + facts and resolve the Plex token (blocking; run in an executor).

    Returns None when Plex isn't set up (no URL, token or library).
    """
    from plexmix.config.settings import Settings
    from plexmix.services import readiness as rd

    settings = Settings.load_from_file()
    facts = rd.collect_facts(settings)
    url = settings.plex.url or ""
    library = settings.plex.library_name or ""
    token = rd.resolve_secret("plex", settings) or ""
    if not (url and token and library):
        return None
    fp = facts.fingerprints.get("plex") or rd.fingerprint("plex", settings, token)
    return PlexProbeInputs(settings, facts, url, library, token, fp)


def probe_plex_libraries(url: str, token: str, timeout: Optional[float] = None) -> list[str]:
    """Connect to Plex and list its music library titles (blocking; run in an executor).

    Raises whatever PlexAPI/requests raise; map it with :func:`plex_failure_cause`.
    """
    server = _plexapi("server").PlexServer(url, (token or "").strip(), timeout=timeout)
    return [s.title for s in server.library.sections() if s.type == "artist"]


def _plexapi(submodule: str) -> Any:
    """``plexapi.<submodule>``, imported by name (PlexAPI ships no type information)."""
    import importlib

    return importlib.import_module(f"plexapi.{submodule}")


def plex_probe_outcome(libraries: list[str], library: str) -> tuple[str, Optional[str]]:
    """``(level, cause)`` for a Plex server that answered with these music libraries.

    Ready only when the configured library is among them (case-insensitive, like
    PlexAPI's own ``library.section``); otherwise failing / library_not_found.
    """
    wanted = (library or "").strip().lower()
    titles = {str(t).strip().lower() for t in libraries}
    if wanted and wanted in titles:
        return "ready", None
    return "failing", "library_not_found"


def plex_failure_cause(exc: BaseException) -> str:
    """Map an exception from a Plex check to a readiness cause value.

    Timeouts → ``timeout``; HTTP 401 → ``invalid_credentials``; connection
    errors, bad URLs and a non-Plex answer (404) → ``unreachable``; anything else
    → ``unknown``.
    """
    import concurrent.futures

    from plexmix.services.readiness import Cause

    if isinstance(exc, (asyncio.TimeoutError, concurrent.futures.TimeoutError, TimeoutError)):
        return Cause.TIMEOUT.value
    try:
        import requests

        if isinstance(exc, requests.exceptions.Timeout):
            return Cause.TIMEOUT.value
        if isinstance(exc, requests.exceptions.RequestException):
            return Cause.UNREACHABLE.value
    except ImportError:  # pragma: no cover - requests ships with plexapi
        pass
    try:
        plex_errors = _plexapi("exceptions")
        if isinstance(exc, plex_errors.Unauthorized):
            return Cause.INVALID_CREDENTIALS.value
        if isinstance(exc, plex_errors.NotFound):
            return Cause.UNREACHABLE.value
    except ImportError:  # pragma: no cover - plexapi is a core dependency
        pass
    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return Cause.TIMEOUT.value
    if "(401)" in text or "unauthorized" in text:
        return Cause.INVALID_CREDENTIALS.value
    if isinstance(exc, OSError):
        return Cause.UNREACHABLE.value
    return Cause.UNKNOWN.value


def provider_failure_cause(
    exc: BaseException, *, key_required: bool = False, key: str = ""
) -> tuple[str, str]:
    """Map an AI or embedding provider failure to ``(cause, message)``.

    ``message`` is plain text for the "unknown" cause only (secret-free: it comes
    from :func:`friendly_error` with ``key`` redacted), else "". A provider that
    needs a key and got none is ``missing_api_key``.
    """
    from plexmix.config.credentials import InvalidAPIKeyError
    from plexmix.services.readiness import Cause
    from plexmix.ui.utils.errors import INVALID_API_KEY_MESSAGE

    if key_required and not (key or "").strip():
        return Cause.MISSING_API_KEY.value, ""
    if isinstance(exc, ImportError):
        return Cause.MISSING_DEPENDENCY.value, ""
    if isinstance(exc, InvalidAPIKeyError):
        return Cause.INVALID_CREDENTIALS.value, ""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return Cause.TIMEOUT.value, ""
    friendly = friendly_error(exc, [key])
    lowered = friendly.lower()
    if friendly == INVALID_API_KEY_MESSAGE:
        return Cause.INVALID_CREDENTIALS.value, ""
    if "timed out" in lowered:
        return Cause.TIMEOUT.value, ""
    if lowered.startswith("could not connect") or lowered.startswith("could not resolve"):
        return Cause.UNREACHABLE.value, ""
    return Cause.UNKNOWN.value, friendly


def record_readiness(
    component: str,
    level: str,
    cause: Optional[str] = None,
    *,
    message: str = "",
    fingerprint: str = "",
) -> None:
    """Record a real outcome in ``readiness_store`` without ever raising.

    For the code paths that verify a component as a side effect (Settings tests,
    sync, tagging, embedding jobs). ``message`` must be secret-free.
    """
    try:
        from plexmix.services.readiness import readiness_store

        readiness_store.record(component, level, cause, message=message, fingerprint=fingerprint)
    except Exception as e:  # recording is best effort; never break the job
        logger.warning("Could not record %s readiness: %s", component, type(e).__name__)


def current_fingerprint_safe(component: str, settings: Any) -> str:
    """``readiness.current_fingerprint`` that returns "" instead of raising."""
    try:
        from plexmix.services.readiness import current_fingerprint

        return current_fingerprint(component, settings)
    except Exception as e:
        logger.warning("Could not fingerprint %s: %s", component, type(e).__name__)
        return ""
