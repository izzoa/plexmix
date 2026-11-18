"""webui-honest-status-and-first-run, group 5: Dashboard coverage, setup checklist,
setup-job polling and recent playlists (DashboardState).

Reflex is mocked (tests/ui/conftest.py). Every test runs on a scratch
``PLEXMIX_DATA_DIR`` with its own config.yaml; the keyring is patched out, and the
readiness and task stores are cleared around each test. No network.
"""

from __future__ import annotations

import os
import pickle
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from plexmix.services import readiness as rd
from plexmix.services.readiness import readiness_store
from plexmix.ui.job_manager import task_store

SENTINEL_TOKEN = "plexTOKEN-SENTINEL-dash-0001"
SENTINEL_OPENAI = "sk-SENTINEL-dash-openai-0001"

_ENV_PREFIXES = (
    "PLEX_",
    "AI_",
    "EMBEDDING_",
    "DATABASE_",
    "AUDIO_",
    "MUSICBRAINZ_",
    "LOGGING_",
    "UI_",
    "GOOGLE_",
    "GEMINI_",
    "OPENAI_",
    "ANTHROPIC_",
    "COHERE_",
    "CUSTOM_",
    "PLEXMIX_",
)
_JOBS = ("sync", "embedding")

PLEX_YAML = "plex:\n  url: http://plex.local:32400\n  library_name: Music\n"
LOCAL_EMBED_YAML = "embedding:\n  default_provider: local\n  model: all-MiniLM-L6-v2\n"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Scratch data dir with a config.yaml, no keyring, no env config, clean stores."""
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text("{}\n")  # never fall back to ~/.plexmix/config.yaml
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(data))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    monkeypatch.setattr("plexmix.config.credentials._get_keyring", lambda: None)
    readiness_store.clear()
    for job in _JOBS:
        task_store.clear(job)
    yield data
    readiness_store.clear()
    for job in _JOBS:
        task_store.clear(job)


@pytest.fixture
def toasts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record ``rx.toast.<kind>(message)`` calls as ``(kind, message)``."""
    from plexmix.ui.states import dashboard_state

    calls: list[tuple[str, str]] = []

    def kind(name: str) -> Any:
        def toast(message: str, **_: Any) -> tuple[str, str]:
            calls.append((name, message))
            return (name, message)

        return toast

    monkeypatch.setattr(
        dashboard_state.rx,
        "toast",
        SimpleNamespace(
            error=kind("error"),
            success=kind("success"),
            warning=kind("warning"),
            info=kind("info"),
        ),
    )
    return calls


# ── Fixtures ─────────────────────────────────────────────────────────────


def write_config(data: Path, body: str) -> None:
    (data / "config.yaml").write_text(body or "{}\n")


def make_db(data: Path, *, tracks: int = 3, embedded: int = 0, synced: bool = True) -> list[int]:
    """A real SQLite library in the scratch data dir; returns the track ids."""
    from plexmix.database.models import Album, Artist, Embedding, SyncHistory, Track
    from plexmix.database.sqlite_manager import SQLiteManager

    with SQLiteManager(str(data / "plexmix.db")) as db:
        artist = db.insert_artist(Artist(plex_key="a1", name="Artist"))
        album = db.insert_album(Album(plex_key="al1", title="Album", artist_id=artist))
        ids = [
            db.insert_track(
                Track(plex_key=f"t{i}", title=f"Song {i}", artist_id=artist, album_id=album)
            )
            for i in range(tracks)
        ]
        for tid in ids[:embedded]:
            db.insert_embedding(
                Embedding(track_id=tid, embedding_model="local", embedding_dim=2, vector=[0.1, 0.2])
            )
        if synced:
            db.insert_sync_record(
                SyncHistory(
                    tracks_added=tracks, tracks_updated=0, tracks_removed=0, status="success"
                )
            )
    return ids


def write_index_metadata(data: Path, dimension: int, track_ids: list[int]) -> None:
    """The FAISS index file (readiness checks that it exists) and its metadata."""
    (data / "embeddings.index").write_bytes(b"faiss")
    with open(data / "embeddings.metadata", "wb") as f:
        pickle.dump({"dimension": dimension, "track_ids": list(track_ids)}, f)


def dashboard() -> Any:
    """A DashboardState with fresh per-instance values (the mocked State shares
    class attributes) and the rx.State bits handlers need."""
    from plexmix.ui.states.dashboard_state import DashboardState

    class _Harness(DashboardState):  # type: ignore[misc, valid-type]
        def access_allowed(self) -> bool:
            return True

    state = _Harness()
    state.readiness = {}
    state.recent_playlists = []
    state.setup_jobs = {}
    state.setup_error = {}
    state.setup_polling = False
    state._setup_watch = {}
    state._setup_tracked = {}
    state._setup_misses = 0
    state.library_stats_loaded = False
    state._status_prefetched = False
    return state


def loaded(state: Any) -> Any:
    state.load_library_stats()
    return state


def current(steps: list[dict[str, str]]) -> list[str]:
    return [s["id"] for s in steps if s["state"] == "current"]


@pytest.fixture
def local_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the local embedding package is installed (the venv lacks it)."""
    monkeypatch.setattr(rd, "_module_available", lambda name: True)


@pytest.fixture
def synced_no_index(_isolated: Path, monkeypatch: pytest.MonkeyPatch, local_deps: None) -> Path:
    write_config(_isolated, PLEX_YAML + LOCAL_EMBED_YAML)
    monkeypatch.setenv("PLEX_TOKEN", SENTINEL_TOKEN)
    make_db(_isolated, tracks=3, embedded=0)
    return _isolated


@pytest.fixture
def complete_setup(_isolated: Path, monkeypatch: pytest.MonkeyPatch, local_deps: None) -> Path:
    write_config(_isolated, PLEX_YAML + LOCAL_EMBED_YAML)
    monkeypatch.setenv("PLEX_TOKEN", SENTINEL_TOKEN)
    ids = make_db(_isolated, tracks=3, embedded=3)
    write_index_metadata(_isolated, 384, ids)
    return _isolated


# ── 5.1 Setup checklist ─────────────────────────────────────────────────


class TestSetupChecklist:
    def test_fresh_install_makes_connect_plex_current(self) -> None:
        state = loaded(dashboard())
        steps = state.setup_steps
        assert [s["id"] for s in steps] == ["plex", "providers", "sync", "index", "vibe"]
        assert current(steps) == ["plex"]
        assert [s["id"] for s in steps if s["primary"] == "true"] == ["plex"]
        assert steps[0]["href"] == "/settings?section=plex"
        assert steps[1]["href"] == "/settings?section=embedding"
        assert state.setup_incomplete is True
        assert state.generate_primary is False

    def test_synced_without_embeddings_makes_build_index_current(
        self, synced_no_index: Path
    ) -> None:
        state = loaded(dashboard())
        steps = {s["id"]: s for s in state.setup_steps}
        assert steps["plex"]["state"] == "done"
        assert steps["providers"]["state"] == "done"
        assert steps["sync"]["state"] == "done"
        assert steps["index"]["state"] == "current"
        assert steps["index"]["action"] == "embed_all"
        assert steps["index"]["primary"] == "true"
        assert "3 tracks need embeddings" in steps["index"]["desc"]
        assert "runs on this machine" in steps["index"]["note"]
        assert state.setup_incomplete is True
        assert state.generate_primary is False

    def test_complete_setup_hides_the_checklist_and_makes_generate_primary(
        self, complete_setup: Path
    ) -> None:
        state = loaded(dashboard())
        assert state.setup_incomplete is False
        assert state.generate_primary is True
        assert current(state.setup_steps) == ["vibe"]

    def test_unreadable_settings_show_no_checklist_and_no_primary(
        self, complete_setup: Path
    ) -> None:
        """Unknown counts are not an empty library: no "Sync library" step."""
        from unittest.mock import patch

        state = dashboard()
        with patch(
            "plexmix.config.settings.Settings.load_from_file",
            side_effect=ValueError("broken yaml"),
        ):
            state.load_library_stats()
        assert state.library_stats_loaded is True
        assert state.settings_unreadable is True
        assert state.setup_incomplete is False
        assert state.generate_primary is False
        state.load_library_stats()  # readable again
        assert state.settings_unreadable is False
        assert state.setup_incomplete is False
        assert state.generate_primary is True

    def test_nothing_is_decided_before_the_first_stats_load(self) -> None:
        state = dashboard()
        assert state.library_stats_loaded is False
        assert state.setup_incomplete is False  # no checklist flash over a set-up library
        assert state.generate_primary is False
        assert len(state.setup_steps) == 5  # the rows always exist for the page to index

    def test_ai_tagging_note_before_the_sync(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, local_deps: None
    ) -> None:
        write_config(
            _isolated,
            PLEX_YAML + LOCAL_EMBED_YAML + "ai:\n  default_provider: openai\n",
        )
        monkeypatch.setenv("PLEX_TOKEN", SENTINEL_TOKEN)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        state = loaded(dashboard())
        sync = state.setup_steps[2]
        assert sync["state"] == "current"
        assert sync["note"] == (
            "Sync also tags new tracks with OpenAI · track metadata is sent to OpenAI"
        )
        assert SENTINEL_OPENAI not in str(state.setup_steps)

    def test_no_step_copy_mentions_a_terminal_command(self) -> None:
        state = loaded(dashboard())
        text = str(state.setup_steps)
        assert "plexmix config" not in text and "Run:" not in text


# ── 5.1 Setup job progress ──────────────────────────────────────────────


class TestSetupPolling:
    def test_watch_shows_the_step_starting_and_mounts_the_poller(self) -> None:
        state = loaded(dashboard())
        state.watch_setup_job("sync")
        assert state.setup_polling is True
        assert state.setup_jobs["sync"]["status"] == "running"
        assert state._setup_watch == {"sync": -1.0}

    def test_watch_ignores_other_jobs(self) -> None:
        state = dashboard()
        state.watch_setup_job("tagging")
        assert state.setup_polling is False and state._setup_watch == {}

    def test_running_job_reports_progress_in_its_step(
        self, synced_no_index: Path, toasts: list
    ) -> None:
        state = loaded(dashboard())
        state.watch_setup_job("embedding")
        task_store.start("embedding", message="Starting…")
        task_store.update("embedding", progress=42, message="Embedding 42 of 100 tracks")

        assert state.poll_setup_progress() is None
        index = state.setup_steps[3]
        assert index["state"] == "running"
        assert index["progress"] == "42"
        assert index["message"] == "Embedding 42 of 100 tracks"
        assert index["primary"] == "false"
        assert state.setup_polling is True
        assert state._setup_watch == {}

    def test_completion_reloads_stats_and_unmounts_the_poller(
        self, synced_no_index: Path, monkeypatch: pytest.MonkeyPatch, toasts: list
    ) -> None:
        state = loaded(dashboard())
        state.watch_setup_job("embedding")
        task_store.start("embedding")
        state.poll_setup_progress()

        reloads: list[str] = []
        monkeypatch.setattr(state, "load_library_stats", lambda: reloads.append("stats"))
        task_store.complete("embedding")
        state.poll_setup_progress()

        assert reloads == ["stats"]
        assert state.setup_polling is False
        assert "embedding" not in state.setup_jobs
        assert toasts == [("success", "Search index built")]
        assert state.setup_error == {}

    def test_a_fast_failure_between_polls_is_reported_inline(self, toasts: list) -> None:
        state = loaded(dashboard())
        state.watch_setup_job("sync")
        # start_sync failed before the first poll (Plex not set up).
        task_store.start("sync")
        task_store.update("sync", extra={"plex_error_code": "not_configured"})
        task_store.complete("sync", status="failed", message="Plex isn't connected yet")

        state.poll_setup_progress()

        error = state.setup_error
        assert error["step"] == "sync"
        assert error["title"] == "Plex isn't connected yet"
        assert error["action_href"] == "/settings?section=plex"
        assert "plexmix config" not in str(error)
        assert state.setup_polling is False
        assert toasts == [("error", "Plex isn't connected yet")]

    def test_an_older_finished_run_is_not_mistaken_for_the_new_one(self, toasts: list) -> None:
        task_store.start("sync")
        task_store.complete("sync", status="failed", message="old failure")
        state = loaded(dashboard())
        state.watch_setup_job("sync")  # baseline: the old entry

        for _ in range(4):
            state.poll_setup_progress()
            assert state.setup_error == {}
            assert state.setup_polling is True
        state.poll_setup_progress()  # 5th miss: the request never registered
        assert state.setup_polling is False
        assert state._setup_watch == {}
        assert "sync" not in state.setup_jobs
        assert toasts == []

    def test_a_cancelled_job_is_not_an_error(self, toasts: list) -> None:
        state = loaded(dashboard())
        state.watch_setup_job("sync")
        task_store.start("sync")
        state.poll_setup_progress()
        task_store.complete("sync", status="cancelled")
        state.poll_setup_progress()
        assert state.setup_error == {}
        assert toasts == [("warning", "Sync cancelled")]

    def test_retrying_clears_the_steps_error(self, toasts: list) -> None:
        state = loaded(dashboard())
        state.setup_error = {"step": "sync", "title": "Sync failed"}
        state.watch_setup_job("embedding")
        assert state.setup_error["step"] == "sync"  # another step's error stays
        state.watch_setup_job("sync")
        assert state.setup_error == {}

    def test_dismiss(self) -> None:
        state = dashboard()
        state.setup_error = {"step": "index", "title": "x"}
        state.dismiss_setup_error()
        assert state.setup_error == {}

    def test_on_load_follows_a_job_started_on_another_page(self) -> None:
        from unittest.mock import MagicMock

        task_store.start("sync")
        task_store.update("sync", progress=10, message="Syncing tracks")
        state = dashboard()
        state.check_auth = MagicMock(return_value=True)
        state.load_app_data = MagicMock()
        state.load_recent_playlists = MagicMock()
        events = state.on_load()
        assert isinstance(events, list)
        assert state.setup_polling is True
        assert state.setup_jobs["sync"] == {
            "status": "running",
            "progress": "10",
            "message": "Syncing tracks",
        }

    def test_on_load_without_running_jobs_mounts_no_poller(self) -> None:
        from unittest.mock import MagicMock

        state = dashboard()
        state.check_auth = MagicMock(return_value=True)
        state.load_app_data = MagicMock()
        state.load_recent_playlists = MagicMock()
        state.on_load()
        assert state.setup_polling is False
        assert state.setup_jobs == {}


# ── What a finished "sync" task says (fix-cli-secrets-and-data-loss, group 2) ──
#
# A completed "sync" task is not always a clean sync. The entries are written here
# with the Library state's own helpers, the way ``start_sync`` and
# ``confirm_removal`` write them, so the Dashboard is checked against the real
# extra data and the real result sentences.

HELD = {"reason": "threshold", "held": 5443, "library_total": 5943}
HELD_SENTENCE = "Plex no longer lists 5,443 of 5,943 tracks. They were kept."
EMPTY_LISTING = {"reason": "empty_listing", "library_total": 5943}
TOO_MANY_UNREAD = {
    "reason": "too_many_unread",
    "message": "40 of 400 tracks could not be read and were kept. Nothing was removed.",
    "unread": {"artists": 0, "albums": 0, "tracks": 40},
    "listed": {"artists": 30, "albums": 60, "tracks": 400},
}
THREE_UNREAD = {"unread": {"artists": 0, "albums": 0, "tracks": 3}}
LAST_PROGRESS = "Syncing tracks: 5,943 of 5,943"


def complete_sync(outcome: dict[str, Any]) -> Any:
    """Complete the running "sync" task the way ``LibraryState.start_sync`` does."""
    from plexmix.plex.sync import SyncOutcome
    from plexmix.ui.states.library_state import sync_outcome_extra, sync_result_message

    result = SyncOutcome(**outcome)
    task_store.update("sync", extra=sync_outcome_extra(result))
    # "" for a clean run: the task then keeps its last progress text.
    task_store.complete("sync", message=sync_result_message(result))
    return task_store.get("sync")


def checklist_sync(state: Any, outcome: dict[str, Any]) -> Any:
    """A sync started from the setup checklist and followed to its end by the poll."""
    state.watch_setup_job("sync")
    task_store.start("sync", message="Starting…")
    task_store.update("sync", progress=90, message=LAST_PROGRESS)
    assert state.poll_setup_progress() is None  # still running: nothing to announce
    complete_sync(outcome)
    return state.poll_setup_progress()


def removal_run(state: Any, result: Any, *, held: int = 300, total: int = 400) -> Any:
    """A removal confirmed on the Library page (``LibraryState.confirm_removal``): it
    runs as the "sync" task, the Dashboard is opened while it runs, then it ends."""
    from plexmix.ui.states import library_state as ls

    task_store.start("sync", message=ls.REMOVAL_CHECKING_MESSAGE)
    task_store.update(
        "sync", extra={ls.REMOVAL_RUN_KEY: True, **ls.held_removal_extra(held, total)}
    )
    state._sync_setup_jobs()
    assert state.setup_polling is True and state.setup_jobs["sync"]["status"] == "running"
    report = ls.removal_report(result, held, total)
    task_store.update(
        "sync",
        extra={
            ls.REMOVAL_RESULT_KEY: report.result,
            **ls.held_removal_extra(report.held, report.total),
        },
    )
    task_store.complete("sync", status=report.status, message=report.message)
    return state.poll_setup_progress()


class TestSyncResultFeedback:
    def test_clean_sync_is_still_the_success_library_synced(self, toasts: list) -> None:
        state = loaded(dashboard())
        events = checklist_sync(state, {})
        entry = task_store.get("sync")
        assert entry.status == "completed" and entry.message == LAST_PROGRESS
        assert toasts == [("success", "Library synced")]
        assert events == [("success", "Library synced")]
        assert state.setup_error == {} and state.setup_polling is False

    def test_held_removal_is_a_warning_with_the_counts(self, toasts: list) -> None:
        """Spec "Notice after the sync": a warning, not a success."""
        state = loaded(dashboard())
        checklist_sync(state, HELD)
        assert task_store.get("sync").extra["removal_reason"] == "threshold"
        assert toasts == [("warning", HELD_SENTENCE)]
        assert state.setup_error == {} and state.setup_polling is False

    def test_empty_listing_is_a_warning(self, toasts: list) -> None:
        state = loaded(dashboard())
        checklist_sync(state, EMPTY_LISTING)
        assert toasts == [
            (
                "warning",
                "Plex listed 0 tracks, but PlexMix holds 5,943 tracks. Nothing was removed.",
            )
        ]

    def test_too_many_unread_is_a_warning(self, toasts: list) -> None:
        state = loaded(dashboard())
        checklist_sync(state, TOO_MANY_UNREAD)
        assert toasts == [("warning", TOO_MANY_UNREAD["message"])]

    def test_a_few_unread_tracks_are_counted_in_the_result(self, toasts: list) -> None:
        """Spec "A few reads fail in a web UI sync": the result says how many were kept."""
        state = loaded(dashboard())
        checklist_sync(state, THREE_UNREAD)
        entry = task_store.get("sync")
        assert entry.extra["unread_tracks"] == 3 and entry.extra["removal_reason"] == ""
        assert len(toasts) == 1
        tone, text = toasts[0]
        assert "3 tracks could not be read and were kept" in text
        assert text == entry.message and text != "Library synced"
        # The tone is the Library page's tone for the same task.
        from plexmix.ui.states.library_state import sync_result_tone

        assert tone == sync_result_tone(entry.extra)

    @pytest.mark.parametrize("kind", ["artists", "albums"])
    def test_an_unread_artist_or_album_is_said_too(self, kind: str, toasts: list) -> None:
        """No track was unread, but the sync was not clean: it is not "Library synced"."""
        unread = {"artists": 0, "albums": 0, "tracks": 0, kind: 2}
        state = loaded(dashboard())
        checklist_sync(state, {"unread": unread})
        entry = task_store.get("sync")
        assert entry.extra["unread_tracks"] == 0 and entry.extra["sync_partial"] is True
        assert len(toasts) == 1
        tone, text = toasts[0]
        assert f"2 {kind} could not be read" in text
        assert text == entry.message and text != "Library synced"
        # The tone is the Library page's tone for the same task.
        from plexmix.ui.states.library_state import sync_result_tone

        assert tone == sync_result_tone(entry.extra)

    @pytest.mark.parametrize("outcome", [HELD, EMPTY_LISTING, TOO_MANY_UNREAD])
    def test_a_sync_that_removed_nothing_is_never_a_success(
        self, outcome: dict[str, Any], toasts: list
    ) -> None:
        state = loaded(dashboard())
        checklist_sync(state, outcome)
        assert [tone for tone, _ in toasts] == ["warning"]
        assert "Library synced" not in str(toasts)

    def test_kept_back_without_a_result_text_is_still_not_a_success(self) -> None:
        from plexmix.ui.states.dashboard_state import completed_sync_result

        for extra in ({"removal_reason": "threshold"}, {"unread_tracks": 3}):
            entry = SimpleNamespace(status="completed", message="", extra=extra)
            tone, text = completed_sync_result(entry)
            assert tone == "warning" and text and text != "Library synced"
        clean = SimpleNamespace(status="completed", message="last progress", extra={})
        assert completed_sync_result(clean) == ("success", "Library synced")

    def test_running_sync_from_another_page_ends_with_a_held_removal(self, toasts: list) -> None:
        """The sync was started on the Library page; the Dashboard is opened while
        it runs (``_sync_setup_jobs``) and follows it to the end with its poll."""
        task_store.start("sync", message="Starting…")
        task_store.update("sync", progress=40, message="Syncing tracks: 2,377 of 5,943")
        state = loaded(dashboard())
        state._sync_setup_jobs()
        assert state._setup_watch == {} and state.setup_polling is True
        assert state.setup_jobs["sync"]["progress"] == "40"
        assert state.poll_setup_progress() is None

        complete_sync(HELD)
        events = state.poll_setup_progress()

        assert toasts == [("warning", HELD_SENTENCE)]
        assert events == [("warning", HELD_SENTENCE)]
        assert state.setup_polling is False and "sync" not in state.setup_jobs
        assert state.setup_error == {}

    # ── A confirmed removal runs as the "sync" task; it is not a sync ──

    def test_removal_that_removed_tracks_reports_its_own_result(
        self, _isolated: Path, toasts: list
    ) -> None:
        from plexmix.plex import sync as engine

        backup = _isolated / "backups" / "before-removal"
        result = engine.RemovalResult(
            status=engine.REMOVAL_REMOVED,
            removed=300,
            missing=300,
            library_total=400,
            message="Removed 300 tracks that Plex no longer lists.",
            backup_folder=backup,
            backup_size_bytes=2048,
        )
        state = loaded(dashboard())
        removal_run(state, result)

        entry = task_store.get("sync")
        assert entry.status == "completed" and entry.extra["removal_result"] == "removed"
        assert toasts == [("success", entry.message)]
        assert entry.message.startswith("Removed 300 tracks that Plex no longer lists.")
        assert str(backup) in entry.message
        assert state.setup_error == {} and state.setup_polling is False

    def test_removal_that_failed_is_an_error_but_not_a_failed_sync(self, toasts: list) -> None:
        from plexmix.plex import sync as engine

        message = "The backup could not be made: disk full. Nothing was removed."
        result = engine.RemovalResult(
            status=engine.REMOVAL_BACKUP_FAILED, missing=300, library_total=400, message=message
        )
        state = loaded(dashboard())
        removal_run(state, result)

        assert task_store.get("sync").status == "failed"
        assert toasts == [("error", message)]
        assert state.setup_error == {}  # the sync step shows no "Sync failed"
        assert "Sync failed" not in str(toasts)

    def test_removal_that_failed_leaves_an_earlier_sync_error_alone(self, toasts: list) -> None:
        from plexmix.plex import sync as engine

        earlier = {"step": "sync", "title": "Plex isn't connected yet"}
        state = loaded(dashboard())
        state.setup_error = dict(earlier)
        removal_run(state, engine.RemovalResult(status=engine.REMOVAL_LISTING_FAILED))

        assert [tone for tone, _ in toasts] == ["error"]
        assert state.setup_error == earlier

    def test_removal_that_was_cancelled_is_not_a_cancelled_sync(self, toasts: list) -> None:
        from plexmix.plex import sync as engine
        from plexmix.ui.states.library_state import REMOVAL_CANCELLED_MESSAGE

        result = engine.RemovalResult(
            status=engine.REMOVAL_CANCELLED,
            library_total=400,
            message="The removal was cancelled. Nothing was removed.",
        )
        state = loaded(dashboard())
        removal_run(state, result)

        entry = task_store.get("sync")
        assert entry.status == "cancelled" and entry.message == REMOVAL_CANCELLED_MESSAGE
        assert toasts == [("warning", REMOVAL_CANCELLED_MESSAGE)]
        assert "Sync cancelled" not in str(toasts)
        assert state.setup_error == {}

    def test_removal_that_found_the_tracks_listed_again_is_not_a_success(
        self, toasts: list
    ) -> None:
        from plexmix.plex import sync as engine

        message = "Plex lists those tracks again. Nothing was removed."
        result = engine.RemovalResult(
            status=engine.REMOVAL_LISTED_AGAIN, library_total=400, message=message
        )
        state = loaded(dashboard())
        removal_run(state, result)
        assert toasts == [("info", message)]

    def test_removal_that_found_more_tracks_missing_is_a_warning(self, toasts: list) -> None:
        from plexmix.plex import sync as engine

        message = (
            "Plex no longer lists 900 tracks, more than the 300 that were confirmed. "
            "Nothing was removed."
        )
        result = engine.RemovalResult(
            status=engine.REMOVAL_MORE_MISSING, missing=900, library_total=5943, message=message
        )
        state = loaded(dashboard())
        removal_run(state, result)

        entry = task_store.get("sync")
        assert entry.status == "completed" and entry.extra["removal_held"] == 900
        assert toasts == [("warning", message)]

    def test_removal_without_a_message_falls_back_to_the_removal_texts(self) -> None:
        from plexmix.ui.states.dashboard_state import removal_run_result
        from plexmix.ui.states.library_state import (
            REMOVAL_CANCELLED_MESSAGE,
            REMOVAL_ERROR_MESSAGE,
        )

        def entry(status: str) -> Any:
            return SimpleNamespace(status=status, message="", extra={"removal_run": True})

        assert removal_run_result(entry("failed")) == ("error", REMOVAL_ERROR_MESSAGE)
        assert removal_run_result(entry("cancelled")) == ("warning", REMOVAL_CANCELLED_MESSAGE)
        assert removal_run_result(entry("completed")) == ("", "")

    def test_an_ordinary_failed_sync_still_fills_the_steps_error(self, toasts: list) -> None:
        state = loaded(dashboard())
        state.watch_setup_job("sync")
        task_store.start("sync")
        state.poll_setup_progress()
        task_store.complete("sync", status="failed", message="boom")
        state.poll_setup_progress()
        assert state.setup_error["step"] == "sync" and state.setup_error["title"] == "Sync failed"
        assert toasts == [("error", "Sync failed")]


class TestDashboardImports:
    def test_importing_the_states_loads_no_sync_engine_and_no_faiss(self) -> None:
        """The Dashboard state takes the sync-result helpers from the Library state.
        Importing the two must not load the sync engine or FAISS: the web app
        imports both when it starts, and their polls run every 2 seconds."""
        import subprocess
        import sys

        script = (
            "import sys\n"
            "import tests.ui.conftest  # the Reflex stand-in these tests use\n"
            "import plexmix.ui.states.dashboard_state\n"
            "import plexmix.ui.states.library_state\n"
            "heavy = ('faiss', 'plexmix.plex.sync', 'plexmix.database.vector_index')\n"
            "print('LOADED=' + ','.join(name for name in heavy if name in sys.modules))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[2],  # the repo root: "tests" imports from it
            env=dict(os.environ),  # the scratch data dir of ``_isolated``
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr[-2000:]
        assert result.stdout.strip().splitlines()[-1] == "LOADED="


class TestSetupFailureCopy:
    def test_sync_codes_use_plex_readiness_copy(self) -> None:
        from plexmix.ui.states.dashboard_state import setup_failure_copy

        for code, title in (
            ("not_configured", "Plex isn't connected yet"),
            ("no_library", "No music library chosen"),
        ):
            entry = SimpleNamespace(status="failed", message="x", extra={"plex_error_code": code})
            copy = setup_failure_copy("sync", entry, {})
            assert copy["title"] == title
            assert copy["action_href"] == "/settings?section=plex"
        entry = SimpleNamespace(
            status="failed", message="x", extra={"plex_error_code": "unreachable"}
        )
        copy = setup_failure_copy("sync", entry, {}, plex_url="http://127.0.0.1:9")
        assert "http://127.0.0.1:9" in copy["title"] + copy["body"]

    def test_generic_sync_failure_never_shows_a_plex_token(self) -> None:
        from plexmix.ui.states.dashboard_state import setup_failure_copy

        entry = SimpleNamespace(
            status="failed",
            message=f"HTTP 500 at http://plex/library?X-Plex-Token={SENTINEL_TOKEN}&x=1",
            extra={},
        )
        copy = setup_failure_copy("sync", entry, {})
        assert SENTINEL_TOKEN not in str(copy)
        assert copy["title"] == "Sync failed" and copy["action_href"] == "/library"

    def test_index_failure_uses_the_embedding_readiness(self) -> None:
        from plexmix.ui.states.dashboard_state import setup_failure_copy

        entry = SimpleNamespace(status="failed", message="boom", extra={})
        readiness = {
            "embeddings": {
                "level": "failing",
                "title": "OpenAI rejected the API key",
                "body": "Check the key in Settings.",
                "fix_label": "Update API key",
                "action_href": "/settings?section=embedding",
            }
        }
        copy = setup_failure_copy("embedding", entry, readiness)
        assert copy["step"] == "index"
        assert copy["title"] == "OpenAI rejected the API key"
        assert copy["action_label"] == "Update API key"
        generic = setup_failure_copy("embedding", entry, {})
        assert generic["title"] == "Couldn't build the search index"
        assert generic["body"] == "boom"
        assert generic["action_href"] == "/settings?section=embedding"


# ── 5.2 Coverage readouts and header ─────────────────────────────────────


class TestCoverage:
    def _tiles(self, **kw: Any) -> dict[str, dict[str, str]]:
        from plexmix.ui.states.dashboard_state import coverage_tiles

        args = dict(total=5943, tagged=4120, embedded=5943, audio=0, musicbrainz=0)
        flags = dict(audio_available=True, audio_enabled=True, mb_enabled=True)
        for key in list(kw):
            if key in flags:
                flags[key] = kw.pop(key)
        args.update(kw)
        return coverage_tiles(**args, **flags)  # type: ignore[arg-type]

    def test_partial_coverage_links_to_the_page_that_fills_it(self) -> None:
        tagged = self._tiles()["tagged"]
        assert tagged["count"] == "4,120"
        assert tagged["percent"] == "69%"
        assert tagged["width"] == "69"
        assert tagged["show"] == "true"
        assert tagged["link_href"] == "/tagging"
        assert tagged["link_label"] == "Fix →"
        assert tagged["link_aria"].startswith("Fix")
        assert tagged["valuetext"] == "69% of tracks tagged"

    def test_full_coverage_has_no_fix_link(self) -> None:
        embedded = self._tiles()["embedded"]
        assert embedded["percent"] == "100%" and embedded["width"] == "100"
        assert embedded["link_href"] == ""

    def test_labels(self) -> None:
        labels = [t["label"] for t in self._tiles().values()]
        assert labels == ["Tagged", "Embedded", "Audio analyzed", "MusicBrainz genres"]
        assert "MB Enriched" not in str(self._tiles())

    def test_audio_not_installed_is_not_a_bare_zero(self) -> None:
        audio = self._tiles(audio_available=False)["audio"]
        assert audio["show"] == "false"
        assert audio["word"] == "Not installed"
        assert "plexmix[audio]" in audio["note"]
        assert "pip install" not in str(audio)

    def test_audio_off_links_to_its_settings_section(self) -> None:
        audio = self._tiles(audio_enabled=False)["audio"]
        assert audio["show"] == "false" and audio["word"] == "Off"
        assert audio["link_href"] == "/settings?section=audio"

    def test_musicbrainz_off_links_to_its_settings_section(self) -> None:
        mb = self._tiles(mb_enabled=False)["musicbrainz"]
        assert mb["word"] == "Off" and mb["show"] == "false"
        assert mb["link_href"] == "/settings?section=musicbrainz"
        assert mb["link_aria"].startswith("Turn on")

    def test_off_with_existing_data_keeps_the_figures(self) -> None:
        mb = self._tiles(mb_enabled=False, musicbrainz=1204)["musicbrainz"]
        assert mb["show"] == "true" and mb["count"] == "1,204" and mb["word"] == "Off"

    def test_enabled_but_never_run_says_so(self) -> None:
        audio = self._tiles()["audio"]
        assert audio["show"] == "true" and audio["count"] == "0"
        assert audio["word"] == "Not run yet"
        assert audio["link_href"] == "/doctor"

    def test_empty_library(self) -> None:
        for tile in self._tiles(total=0, tagged=0, embedded=0).values():
            assert tile["show"] == "false" and tile["word"] == "No tracks yet"

    def test_meter_width_never_hides_a_partial_share(self) -> None:
        from plexmix.ui.states.dashboard_state import meter_width

        assert meter_width(1, 5943) == "1"
        assert meter_width(5942, 5943) == "99"
        assert meter_width(0, 5943) == "0"
        assert meter_width(5, 0) == "0"
        assert meter_width(9, 5) == "100"
        assert meter_width(None, "x") == "0"

    def test_state_readouts_from_real_stats(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_db(_isolated, tracks=3, embedded=2)
        monkeypatch.setattr(rd, "_module_available", lambda name: False)
        state = loaded(dashboard())
        assert state.library_count == "3" and state.library_noun == "tracks"
        assert state.sync_readout == "synced just now"
        cov = state.coverage
        assert cov["embedded"]["count"] == "2" and cov["embedded"]["percent"] == "67%"
        assert cov["audio"]["word"] == "Not installed"  # essentia missing in this venv
        assert cov["musicbrainz"]["word"] == "Off"

    def test_never_synced(self, _isolated: Path) -> None:
        make_db(_isolated, tracks=1, synced=False)
        state = loaded(dashboard())
        assert state.sync_readout == "not synced yet"
        assert state.library_noun == "track"
        assert state.library_count == "1"


# ── 5.5 Recent playlists ─────────────────────────────────────────────────


@pytest.fixture
def utc() -> Any:
    """Run with the process in UTC so stored (UTC) times read back unchanged."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


class TestRecentPlaylists:
    def test_row_exposes_id_and_a_date_without_seconds(self, utc: Any) -> None:
        from plexmix.ui.states.dashboard_state import recent_playlist_row

        row = recent_playlist_row((7, "Late Night Jazz", "2025-12-27 20:35:19.327064", 5))
        assert row == {
            "id": "7",
            "name": "Late Night Jazz",
            "track_count": "5",
            "track_word": "tracks",
            "created_label": "Dec 27 · 20:35",
            "created_title": "Dec 27, 2025 · 20:35",
        }
        one = recent_playlist_row((8, None, None, 1))
        assert one["track_word"] == "track" and one["created_label"] == ""
        assert one["name"] == "Untitled playlist"

    def test_stored_utc_is_shown_in_local_time(self) -> None:
        from plexmix.ui.states.dashboard_state import recent_playlist_row

        old = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        try:
            row = recent_playlist_row((7, "Jazz", "2025-12-27 20:35:19.327064", 5))
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        assert row["created_label"] == "Dec 27 · 15:35"

    def test_load_recent_playlists_selects_the_id(self, _isolated: Path, utc: Any) -> None:
        from plexmix.database.models import Playlist
        from plexmix.database.sqlite_manager import SQLiteManager

        ids = make_db(_isolated, tracks=2)
        with SQLiteManager(str(_isolated / "plexmix.db")) as db:
            pid = db.insert_playlist(Playlist(name="Late Night Jazz", created_by_ai=True))
            db.add_tracks_to_playlist(pid, ids)
            cur = db.get_connection().cursor()
            cur.execute(
                "UPDATE playlists SET created_at = ? WHERE id = ?",
                ("2025-12-27 20:35:19.327064", pid),
            )
            db.get_connection().commit()
        state = dashboard()
        state.load_recent_playlists()
        assert state.recent_playlists == [
            {
                "id": str(pid),
                "name": "Late Night Jazz",
                "track_count": "2",
                "track_word": "tracks",
                "created_label": "Dec 27 · 20:35",
                "created_title": "Dec 27, 2025 · 20:35",
            }
        ]

    def test_missing_database_lists_nothing_and_creates_nothing(self, _isolated: Path) -> None:
        state = dashboard()
        state.load_recent_playlists()
        assert state.recent_playlists == []
        assert not (_isolated / "plexmix.db").exists()


# ── 5.6 Settings deep links ──────────────────────────────────────────────


class TestSettingsDeepLinks:
    def test_checklist_and_strip_links_open_their_settings_section(self) -> None:
        import re

        from plexmix.ui.utils.a11y import resolve_settings_section

        state = loaded(dashboard())
        hrefs = [s["href"] for s in state.setup_steps]
        hrefs += [entry["action_href"] for entry in state.readiness.values()]
        hrefs += [t["link_href"] for t in state.coverage.values()]
        settings_links = [h for h in hrefs if h.startswith("/settings")]
        assert "/settings?section=plex" in settings_links
        assert "/settings?section=embedding" in settings_links
        for href in settings_links:
            match = re.fullmatch(r"/settings\?section=([a-z]+)", href)
            assert match, href
            # SettingsState.on_load selects exactly this section (not "embeddings").
            assert resolve_settings_section(match.group(1), "advanced") == match.group(1)


# ── 5.3 Status strip data ────────────────────────────────────────────────


class TestStatusStrip:
    def test_broken_profile_shows_no_success_and_names_the_cause(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_config(
            _isolated,
            "plex:\n  url: http://127.0.0.1:9\n  library_name: Music\n"
            "ai:\n  default_provider: openai\n  model: gpt-5-mini\n"
            "embedding:\n  default_provider: openai\n  model: text-embedding-3-small\n",
        )
        monkeypatch.setenv("PLEX_TOKEN", SENTINEL_TOKEN)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        from plexmix.config.settings import Settings

        state = loaded(dashboard())
        fp = rd.current_fingerprint("plex", Settings.load_from_file())
        readiness_store.record("plex", "failing", "unreachable", fingerprint=fp)
        state.load_library_stats()

        strip = {c: state.readiness[c] for c in ("plex", "ai", "embeddings")}
        assert all(entry["tone"] != "success" for entry in strip.values())
        plex = strip["plex"]
        assert plex["tone"] == "error" and plex["action_label"] == "Fix"
        assert plex["title"].startswith("Can't reach your Plex server at http://127.0.0.1:9")
        assert plex["action_href"] == "/settings?section=plex"
        ai = strip["ai"]
        assert (ai["label"], ai["model"]) == ("OpenAI", "gpt-5-mini")
        assert "not checked" in ai["state"] and ai["action_label"] == "Check"
        assert ai["action_href"] == "/settings?section=ai"
        text = str(strip)
        assert "Openai" not in text
        assert SENTINEL_TOKEN not in text and SENTINEL_OPENAI not in text
