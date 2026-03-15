"""Tests for the centralized job manager and persistent task store."""

import asyncio
from threading import Event as ThreadingEvent

import pytest

from plexmix.ui.job_manager import JobManager, TaskStore


@pytest.fixture
def jm():
    return JobManager()


@pytest.fixture
def ts():
    return TaskStore()


# ── JobManager tests ─────────────────────────────────────────────────


class TestStartFinishCancel:
    def test_start_returns_threading_event(self, jm: JobManager):
        event = jm.start("tok-a", "sync")
        assert isinstance(event, ThreadingEvent)
        assert not event.is_set()

    def test_cancel_sets_event(self, jm: JobManager):
        jm.start("tok-a", "sync")
        assert not jm.is_cancelled("tok-a", "sync")
        jm.cancel("tok-a", "sync")
        assert jm.is_cancelled("tok-a", "sync")

    def test_finish_removes_job(self, jm: JobManager):
        jm.start("tok-a", "sync")
        jm.finish("tok-a", "sync")
        assert not jm.is_cancelled("tok-a", "sync")
        assert jm.active_job_count == 0

    def test_start_cancels_previous_same_type(self, jm: JobManager):
        first = jm.start("tok-a", "sync")
        jm.start("tok-a", "sync")
        assert first.is_set()  # old one was cancelled

    def test_multiple_job_types(self, jm: JobManager):
        jm.start("tok-a", "sync")
        jm.start("tok-a", "audio")
        assert jm.active_job_count == 2
        jm.cancel("tok-a", "sync")
        assert jm.is_cancelled("tok-a", "sync")
        assert not jm.is_cancelled("tok-a", "audio")


class TestCancelStaleClients:
    def test_cancels_other_tokens(self, jm: JobManager):
        old_event = jm.start("old-token", "sync")
        jm.start("current-token", "sync")

        jm.cancel_stale_clients("current-token")

        assert old_event.is_set()  # old token cancelled
        assert not jm.is_cancelled("current-token", "sync")  # current kept

    def test_cancels_multiple_stale_tokens(self, jm: JobManager):
        e1 = jm.start("stale-1", "sync")
        e2 = jm.start("stale-2", "audio")
        jm.start("current", "tagging")

        jm.cancel_stale_clients("current")

        assert e1.is_set()
        assert e2.is_set()
        assert not jm.is_cancelled("current", "tagging")

    def test_noop_when_no_stale(self, jm: JobManager):
        jm.start("tok", "sync")
        jm.cancel_stale_clients("tok")
        assert not jm.is_cancelled("tok", "sync")

    def test_unblocks_paused_stale_jobs(self, jm: JobManager):
        jm.start("old", "audio")
        pause = jm.get_pause_event("old", "audio")
        jm.pause("old", "audio")
        assert not pause.is_set()

        jm.cancel_stale_clients("new")
        # Pause event should be set (unblocked) so the loop can exit
        assert pause.is_set()


class TestPauseResume:
    def test_pause_and_resume(self, jm: JobManager):
        jm.start("tok", "audio")
        event = jm.get_pause_event("tok", "audio")
        assert event.is_set()  # unpaused by default
        jm.pause("tok", "audio")
        assert jm.is_paused("tok", "audio")
        jm.resume("tok", "audio")
        assert not jm.is_paused("tok", "audio")


class TestCleanup:
    def test_cleanup_client(self, jm: JobManager):
        e1 = jm.start("tok-a", "sync")
        jm.start("tok-b", "sync")

        jm.cleanup_client("tok-a")
        assert e1.is_set()
        assert jm.active_job_count == 1

    def test_cleanup_all(self, jm: JobManager):
        jm.start("tok-a", "sync")
        jm.start("tok-b", "audio")
        jm.cleanup_all()
        assert jm.active_job_count == 0


# ── TaskStore tests ──────────────────────────────────────────────────


class TestTaskStoreLifecycle:
    def test_start_returns_cancel_event(self, ts: TaskStore):
        event = ts.start("sync")
        assert isinstance(event, ThreadingEvent)
        assert not event.is_set()

    def test_start_rejects_duplicate(self, ts: TaskStore):
        ts.start("sync")
        result = ts.start("sync")
        assert result is None

    def test_start_allows_after_complete(self, ts: TaskStore):
        ts.start("sync")
        ts.complete("sync")
        event = ts.start("sync")
        assert event is not None

    def test_start_allows_different_types(self, ts: TaskStore):
        e1 = ts.start("sync")
        e2 = ts.start("tagging")
        assert e1 is not None
        assert e2 is not None

    def test_complete_sets_status(self, ts: TaskStore):
        ts.start("sync")
        ts.complete("sync", status="failed", message="oops")
        entry = ts.get("sync")
        assert entry is not None
        assert entry.status == "failed"
        assert entry.message == "oops"

    def test_clear_removes_entry(self, ts: TaskStore):
        ts.start("sync")
        ts.complete("sync")
        ts.clear("sync")
        assert ts.get("sync") is None

    def test_is_running(self, ts: TaskStore):
        assert not ts.is_running("sync")
        ts.start("sync")
        assert ts.is_running("sync")
        ts.complete("sync")
        assert not ts.is_running("sync")


class TestTaskStoreProgress:
    def test_update_progress(self, ts: TaskStore):
        ts.start("sync")
        ts.update("sync", progress=50, message="halfway")
        entry = ts.get("sync")
        assert entry.progress == 50
        assert entry.message == "halfway"

    def test_update_extra_merges(self, ts: TaskStore):
        ts.start("audio")
        ts.update("audio", extra={"eta": "5m", "paused": False})
        ts.update("audio", extra={"eta": "2m"})
        entry = ts.get("audio")
        assert entry.extra == {"eta": "2m", "paused": False}

    def test_update_noop_after_complete(self, ts: TaskStore):
        ts.start("sync")
        ts.complete("sync")
        ts.update("sync", progress=99)
        entry = ts.get("sync")
        assert entry.progress == 0  # unchanged

    def test_update_noop_for_nonexistent(self, ts: TaskStore):
        ts.update("sync", progress=50)  # no error


class TestTaskStoreCancelPause:
    def test_cancel(self, ts: TaskStore):
        ts.start("sync")
        assert not ts.is_cancelled("sync")
        ts.cancel("sync")
        assert ts.is_cancelled("sync")

    def test_pause_and_resume(self, ts: TaskStore):
        ts.start("audio")
        assert not ts.is_paused("audio")
        ts.pause("audio")
        assert ts.is_paused("audio")
        ts.resume("audio")
        assert not ts.is_paused("audio")

    def test_get_pause_event(self, ts: TaskStore):
        ts.start("audio")
        event = ts.get_pause_event("audio")
        assert isinstance(event, asyncio.Event)
        assert event.is_set()  # unpaused by default

    def test_complete_cleans_up_events(self, ts: TaskStore):
        ts.start("sync")
        ts.complete("sync")
        assert ts.get_cancel_event("sync") is None
        assert ts.get_pause_event("sync") is None


class TestTaskStoreExclusivity:
    def test_different_users_same_type_rejected(self, ts: TaskStore):
        ts.start("sync", user_id="alice")
        result = ts.start("sync", user_id="bob")
        assert result is None  # global exclusivity

    def test_different_users_different_types_allowed(self, ts: TaskStore):
        e1 = ts.start("sync", user_id="alice")
        e2 = ts.start("tagging", user_id="bob")
        assert e1 is not None
        assert e2 is not None
