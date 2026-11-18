"""Tests for the centralized job manager and persistent task store."""

import asyncio
from threading import Event as ThreadingEvent

import pytest

from plexmix.ui.job_manager import SESSION_REAP_GRACE_S, JobManager, TaskStore


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


class TestFinishGuard:
    def test_finish_with_stale_event_keeps_newer_job(self, jm: JobManager):
        first = jm.start("tok-a", "generation")
        second = jm.start("tok-a", "generation")
        # The superseded job finishing must not drop the newer job's handle.
        jm.finish("tok-a", "generation", first)
        assert jm.get_cancel_event("tok-a", "generation") is second
        jm.finish("tok-a", "generation", second)
        assert jm.get_cancel_event("tok-a", "generation") is None

    def test_finish_with_current_event_releases(self, jm: JobManager):
        event = jm.start("tok-a", "generation")
        jm.finish("tok-a", "generation", event)
        assert jm.get_cancel_event("tok-a", "generation") is None
        assert jm.active_job_count == 0


class TestReapDisconnected:
    def test_default_grace_is_120s(self):
        assert SESSION_REAP_GRACE_S == 120.0

    def test_two_live_tokens_both_survive_a_sweep(self, jm: JobManager):
        """Replaces the old cross-client cancel: a sweep from either tab's page
        load never cancels the other live tab's work."""
        a = jm.start("tok-a", "generation")
        b = jm.start("tok-b", "generation")

        for now in (0.0, 500.0, 10_000.0):
            assert jm.reap_disconnected({"tok-a", "tok-b"}, now) == []

        assert not a.is_set()
        assert not b.is_set()
        assert jm.active_job_count == 2

    def test_connected_token_is_never_reaped(self, jm: JobManager):
        event = jm.start("tok-a", "generation")
        for now in (0.0, 121.0, 1_000.0, 100_000.0):
            assert jm.reap_disconnected(frozenset({"tok-a"}), now) == []
        assert not event.is_set()
        assert jm.get_cancel_event("tok-a", "generation") is event

    def test_disconnected_token_reaped_only_after_grace(self, jm: JobManager):
        gone = jm.start("tok-gone", "generation")
        live = jm.start("tok-live", "generation")

        assert jm.reap_disconnected({"tok-live"}, 1_000.0) == []  # first sighting
        assert not gone.is_set()
        assert jm.reap_disconnected({"tok-live"}, 1_119.9) == []  # still within grace
        assert not gone.is_set()

        assert jm.reap_disconnected({"tok-live"}, 1_120.0) == ["tok-gone"]
        assert gone.is_set()
        assert jm.get_cancel_event("tok-gone", "generation") is None
        assert not live.is_set()

        # Already reaped: nothing left to reap.
        assert jm.reap_disconnected({"tok-live"}, 5_000.0) == []

    def test_custom_grace(self, jm: JobManager):
        event = jm.start("tok-a", "generation")
        assert jm.reap_disconnected(set(), 10.0, grace_s=5.0) == []
        assert jm.reap_disconnected(set(), 15.0, grace_s=5.0) == ["tok-a"]
        assert event.is_set()

    def test_zero_grace_reaps_on_first_sighting(self, jm: JobManager):
        event = jm.start("tok-a", "generation")
        assert jm.reap_disconnected([], 0.0, grace_s=0.0) == ["tok-a"]
        assert event.is_set()

    def test_reconnect_within_grace_resets_timer(self, jm: JobManager):
        event = jm.start("tok-a", "generation")

        assert jm.reap_disconnected(set(), 0.0) == []  # disconnected at t=0
        assert jm.reap_disconnected({"tok-a"}, 100.0) == []  # reconnected in grace
        assert jm.reap_disconnected(set(), 150.0) == []  # disconnected again: new timer
        # 150 s after the first drop but only 100 s after the second one.
        assert jm.reap_disconnected(set(), 250.0) == []
        assert not event.is_set()

        assert jm.reap_disconnected(set(), 270.0) == ["tok-a"]
        assert event.is_set()

    def test_none_is_a_noop(self, jm: JobManager):
        event = jm.start("tok-a", "generation")
        jm.get_pause_event("tok-a", "generation")
        jm.pause("tok-a", "generation")

        for now in (0.0, 1_000.0, 1_000_000.0):
            assert jm.reap_disconnected(None, now) == []

        assert not event.is_set()
        assert jm.is_paused("tok-a", "generation")
        assert jm.active_job_count == 1
        # Unknown liveness must not start (or advance) a disconnect timer either.
        assert jm.reap_disconnected(set(), 2_000_000.0) == []
        assert not event.is_set()

    def test_unblocks_paused_loops(self, jm: JobManager):
        jm.start("tok-a", "audio")
        pause = jm.get_pause_event("tok-a", "audio")
        jm.pause("tok-a", "audio")
        assert not pause.is_set()

        assert jm.reap_disconnected(set(), 0.0) == []
        assert not pause.is_set()  # still within grace: stays paused
        assert jm.reap_disconnected(set(), 120.0) == ["tok-a"]
        # Pause event set (unblocked) so the loop can wake up and see the cancel.
        assert pause.is_set()
        assert jm.is_cancelled("tok-a", "audio") is False  # handles dropped
        assert jm.get_cancel_event("tok-a", "audio") is None

    def test_cancels_registered_tasks(self, jm: JobManager):
        async def scenario() -> None:
            task = asyncio.create_task(asyncio.sleep(3600))
            jm.register_task("tok-a", "search", task)
            jm.register_task("tok-b", "search", asyncio.create_task(asyncio.sleep(0)))

            assert jm.reap_disconnected({"tok-b"}, 0.0) == []
            assert jm.reap_disconnected({"tok-b"}, 120.0) == ["tok-a"]
            await asyncio.sleep(0)
            assert task.cancelled()

        asyncio.run(scenario())

    def test_task_only_owner_is_tracked(self, jm: JobManager):
        """A session-bound asyncio task registered without a cancel event is tracked."""

        async def scenario() -> None:
            task = asyncio.create_task(asyncio.sleep(3600))
            jm.register_task("tok-a", "search", task)
            assert jm.reap_disconnected({"tok-a"}, 0.0) == []
            await asyncio.sleep(0)
            assert not task.done()
            task.cancel()

        asyncio.run(scenario())

    def test_finished_work_forgets_timer(self, jm: JobManager):
        event = jm.start("tok-a", "generation")
        assert jm.reap_disconnected(set(), 0.0) == []  # timer starts
        jm.finish("tok-a", "generation", event)
        assert jm.reap_disconnected(set(), 50.0) == []  # no work: timer dropped
        # A new job for the same token starts a fresh timer.
        event2 = jm.start("tok-a", "generation")
        assert jm.reap_disconnected(set(), 130.0) == []
        assert not event2.is_set()
        assert jm.reap_disconnected(set(), 250.0) == ["tok-a"]
        assert event2.is_set()

    def test_reaps_each_token_independently(self, jm: JobManager):
        a = jm.start("tok-a", "generation")
        b = jm.start("tok-b", "generation")
        assert jm.reap_disconnected({"tok-b"}, 0.0) == []
        assert jm.reap_disconnected(set(), 60.0) == []  # tok-b drops at t=60
        assert jm.reap_disconnected(set(), 120.0) == ["tok-a"]
        assert a.is_set() and not b.is_set()
        assert jm.reap_disconnected(set(), 180.0) == ["tok-b"]
        assert b.is_set()


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

    def test_cleanup_client_unblocks_paused_loop(self, jm: JobManager):
        jm.start("tok-a", "audio")
        pause = jm.get_pause_event("tok-a", "audio")
        jm.pause("tok-a", "audio")
        jm.cleanup_client("tok-a")
        assert pause.is_set()

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

    def test_cancel_unblocks_paused(self, ts: TaskStore):
        # Cancelling must resume the pause event so a loop blocked on
        # `await pause_event.wait()` wakes up and can observe the cancel.
        ts.start("audio")
        ts.pause("audio")
        assert ts.is_paused("audio")
        ts.cancel("audio")
        assert ts.is_cancelled("audio")
        assert not ts.is_paused("audio")  # resumed so the loop can exit

    def test_cancel_all_running_unblocks_paused(self, ts: TaskStore):
        ts.start("audio")
        ts.pause("audio")
        cancelled = ts.cancel_all_running()
        assert "audio" in cancelled
        assert ts.is_cancelled("audio")
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
