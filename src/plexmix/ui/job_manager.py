"""Centralized job manager for UI background tasks.

Two subsystems live here:

``JobManager`` (``jobs`` singleton)
    Session-scoped job lifecycle (cancel / pause / resume) keyed by
    ``(client_token, job_type)``.  Used for short-lived, session-bound work
    like search debounce.

``TaskStore`` (``task_store`` singleton)
    Persistent task progress store keyed by ``(user_id, job_type)``.
    Background tasks write progress here instead of pushing via
    ``async with self:``.  Reflex states read from here via client-initiated
    polling, so progress survives browser disconnection and reconnection.

Usage::

    from plexmix.ui.job_manager import jobs, task_store

    # Session-scoped jobs (unchanged)
    cancel_event = jobs.start(client_token, "search")
    jobs.cancel(client_token, "search")

    # Persistent tasks (new)
    cancel_event = task_store.start("sync")          # returns None if already running
    task_store.update("sync", progress=45, message="Syncing…")
    task_store.complete("sync")                       # or status="failed"
    entry = task_store.get("sync")                    # read from polling handler
    task_store.clear("sync")                          # after UI consumes completion
"""

import asyncio
import atexit
import logging
import threading
import time
from dataclasses import dataclass, field
from threading import Event as ThreadingEvent
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class _JobKey:
    """Composite key for job lookups."""

    __slots__ = ("client_token", "job_type")

    def __init__(self, client_token: str, job_type: str) -> None:
        self.client_token = client_token
        self.job_type = job_type

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _JobKey):
            return NotImplemented
        return self.client_token == other.client_token and self.job_type == other.job_type

    def __hash__(self) -> int:
        return hash((self.client_token, self.job_type))

    def __repr__(self) -> str:
        return f"Job({self.client_token!r}, {self.job_type!r})"


class JobManager:
    """Thread-safe registry for background job lifecycle management.

    Tracks per-client, per-job-type:
    - ``cancel_events``: threading.Event for cooperative cancellation
    - ``pause_events``: asyncio.Event for pause/resume
    - ``async_tasks``: asyncio.Task references for cancellation on disconnect
    """

    def __init__(self) -> None:
        self._cancel_events: Dict[_JobKey, ThreadingEvent] = {}
        self._pause_events: Dict[_JobKey, asyncio.Event] = {}
        self._async_tasks: Dict[_JobKey, asyncio.Task] = {}  # type: ignore[type-arg]

    # ── Start / stop ─────────────────────────────────────────────

    def start(self, client_token: str, job_type: str) -> ThreadingEvent:
        """Register a new job and return its cancel event.

        If a job of the same type is already running for this client,
        the old one is cancelled first.
        """
        key = _JobKey(client_token, job_type)

        # Cancel any previous job of the same type
        old = self._cancel_events.get(key)
        if old is not None:
            old.set()

        cancel_event = ThreadingEvent()
        self._cancel_events[key] = cancel_event
        return cancel_event

    def finish(self, client_token: str, job_type: str) -> None:
        """Mark a job as finished and remove its resources."""
        key = _JobKey(client_token, job_type)
        self._cancel_events.pop(key, None)
        self._pause_events.pop(key, None)
        self._async_tasks.pop(key, None)

    def cancel(self, client_token: str, job_type: str) -> None:
        """Signal cancellation for a running job."""
        key = _JobKey(client_token, job_type)
        event = self._cancel_events.get(key)
        if event is not None:
            event.set()

    def is_cancelled(self, client_token: str, job_type: str) -> bool:
        """Check whether a job has been cancelled."""
        key = _JobKey(client_token, job_type)
        event = self._cancel_events.get(key)
        return event is not None and event.is_set()

    def get_cancel_event(self, client_token: str, job_type: str) -> Optional[ThreadingEvent]:
        """Return the cancel event for a running job, or None."""
        key = _JobKey(client_token, job_type)
        return self._cancel_events.get(key)

    # ── Pause / resume (async) ───────────────────────────────────

    def get_pause_event(self, client_token: str, job_type: str) -> asyncio.Event:
        """Get or create a pause event for a job. Starts in unpaused (set) state."""
        key = _JobKey(client_token, job_type)
        event = self._pause_events.get(key)
        if event is None:
            event = asyncio.Event()
            event.set()  # Unpaused by default
            self._pause_events[key] = event
        return event

    def pause(self, client_token: str, job_type: str) -> None:
        """Pause a job (clear the pause event so workers await)."""
        key = _JobKey(client_token, job_type)
        event = self._pause_events.get(key)
        if event is not None:
            event.clear()

    def resume(self, client_token: str, job_type: str) -> None:
        """Resume a paused job."""
        key = _JobKey(client_token, job_type)
        event = self._pause_events.get(key)
        if event is not None:
            event.set()

    def is_paused(self, client_token: str, job_type: str) -> bool:
        """Check whether a job is paused."""
        key = _JobKey(client_token, job_type)
        event = self._pause_events.get(key)
        return event is not None and not event.is_set()

    # ── Async task tracking ──────────────────────────────────────

    def register_task(
        self, client_token: str, job_type: str, task: asyncio.Task  # type: ignore[type-arg]
    ) -> None:
        """Register an asyncio.Task for a job so it can be cancelled on disconnect."""
        key = _JobKey(client_token, job_type)
        self._async_tasks[key] = task

    def cancel_task(self, client_token: str, job_type: str) -> None:
        """Cancel a registered async task."""
        key = _JobKey(client_token, job_type)
        task = self._async_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    # ── Cleanup ──────────────────────────────────────────────────

    def cancel_stale_clients(self, current_token: str) -> None:
        """Cancel all jobs that do NOT belong to *current_token*.

        Called from ``on_load`` so that when a browser window is closed and
        reopened (new session token), orphaned background tasks from the old
        session stop pushing state updates to a dead WebSocket.
        """
        stale_keys = [k for k in self._cancel_events if k.client_token != current_token]
        for key in stale_keys:
            logger.info("Cancelling stale job %s (current token: %s…)", key, current_token[:8])
            self._cancel_events[key].set()
            self._cancel_events.pop(key, None)

        stale_tasks = [k for k in self._async_tasks if k.client_token != current_token]
        for key in stale_tasks:
            task = self._async_tasks.pop(key, None)
            if task is not None and not task.done():
                task.cancel()

        stale_pauses = [k for k in self._pause_events if k.client_token != current_token]
        for key in stale_pauses:
            # Unblock any paused loops so they can see the cancellation
            event = self._pause_events.pop(key, None)
            if event is not None:
                event.set()

    def cleanup_client(self, client_token: str) -> None:
        """Clean up all jobs for a disconnected client."""
        keys_to_remove = [k for k in self._cancel_events if k.client_token == client_token]
        for key in keys_to_remove:
            self._cancel_events[key].set()
            self._cancel_events.pop(key, None)

        keys_to_remove = [k for k in self._async_tasks if k.client_token == client_token]
        for key in keys_to_remove:
            task = self._async_tasks.pop(key, None)
            if task is not None and not task.done():
                task.cancel()

        keys_to_remove = [k for k in self._pause_events if k.client_token == client_token]
        for key in keys_to_remove:
            self._pause_events.pop(key, None)

    def cleanup_all(self) -> None:
        """Clean up all jobs on process exit."""
        for event in self._cancel_events.values():
            event.set()
        self._cancel_events.clear()

        for task in self._async_tasks.values():
            if not task.done():
                task.cancel()
        self._async_tasks.clear()

        self._pause_events.clear()

    @property
    def active_job_count(self) -> int:
        """Number of active (non-cancelled) jobs."""
        return sum(1 for e in self._cancel_events.values() if not e.is_set())


# ── Persistent task store ────────────────────────────────────────────

_COMPLETED_TTL: float = 3600.0  # auto-expire completed entries after 1 hour


@dataclass
class TaskEntry:
    """Snapshot of a background task's progress.

    Written by the background task, read by the polling event handler.
    """

    job_type: str
    user_id: str
    status: str  # "running", "completed", "failed", "cancelled"
    progress: int  # 0-100
    message: str
    started_at: float  # time.monotonic()
    updated_at: float
    extra: Dict[str, Any] = field(default_factory=dict)


class TaskStore:
    """Thread-safe singleton for persistent task progress.

    Keyed by ``(user_id, job_type)``.  Only one task of a given *job_type*
    can run at a time (global exclusivity) regardless of user, which prevents
    concurrent syncs/tagging from causing DB contention.

    Cancel and pause events are keyed by *job_type* alone (not session token)
    so any browser session can pause/cancel a running task.
    """

    def __init__(self) -> None:
        self._tasks: Dict[tuple, TaskEntry] = {}  # (user_id, job_type) → entry
        self._cancel_events: Dict[str, ThreadingEvent] = {}  # job_type → event
        self._pause_events: Dict[str, asyncio.Event] = {}  # job_type → event
        self._lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(
        self,
        job_type: str,
        user_id: str = "default",
        message: str = "",
    ) -> Optional[ThreadingEvent]:
        """Register a new persistent task.

        Returns a ``threading.Event`` for cooperative cancellation, or
        ``None`` if a task of the same *job_type* is already running.
        """
        with self._lock:
            # Global exclusivity: reject if any user has a running task of this type
            for key, entry in self._tasks.items():
                if key[1] == job_type and entry.status == "running":
                    return None

            now = time.monotonic()
            self._tasks[(user_id, job_type)] = TaskEntry(
                job_type=job_type,
                user_id=user_id,
                status="running",
                progress=0,
                message=message,
                started_at=now,
                updated_at=now,
            )

            # Fresh cancel event
            old = self._cancel_events.get(job_type)
            if old is not None:
                old.set()
            cancel_event = ThreadingEvent()
            self._cancel_events[job_type] = cancel_event

            # Fresh pause event (starts unpaused)
            pause_event = asyncio.Event()
            pause_event.set()
            self._pause_events[job_type] = pause_event

            return cancel_event

    def update(
        self,
        job_type: str,
        user_id: str = "default",
        progress: Optional[int] = None,
        message: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update progress for a running task (called from worker threads)."""
        with self._lock:
            entry = self._tasks.get((user_id, job_type))
            if entry is None or entry.status != "running":
                return
            if progress is not None:
                entry.progress = progress
            if message is not None:
                entry.message = message
            if extra is not None:
                entry.extra.update(extra)
            entry.updated_at = time.monotonic()

    def complete(
        self,
        job_type: str,
        user_id: str = "default",
        status: str = "completed",
        message: str = "",
    ) -> None:
        """Mark a task as finished (completed / failed / cancelled)."""
        with self._lock:
            entry = self._tasks.get((user_id, job_type))
            if entry is None:
                return
            entry.status = status
            if message:
                entry.message = message
            entry.updated_at = time.monotonic()
            # Clean up lifecycle events
            self._cancel_events.pop(job_type, None)
            self._pause_events.pop(job_type, None)

    def get(self, job_type: str, user_id: str = "default") -> Optional[TaskEntry]:
        """Read current task state (called from polling handlers)."""
        with self._lock:
            entry = self._tasks.get((user_id, job_type))
            if entry is None:
                return None
            # Auto-expire old completed/failed entries
            if entry.status != "running" and (time.monotonic() - entry.updated_at) > _COMPLETED_TTL:
                self._tasks.pop((user_id, job_type), None)
                return None
            return entry

    def clear(self, job_type: str, user_id: str = "default") -> None:
        """Remove a consumed task entry."""
        with self._lock:
            self._tasks.pop((user_id, job_type), None)
            self._cancel_events.pop(job_type, None)
            self._pause_events.pop(job_type, None)

    def is_running(self, job_type: str, user_id: str = "default") -> bool:
        """Check whether a task of this type is currently running."""
        entry = self.get(job_type, user_id)
        return entry is not None and entry.status == "running"

    # ── Cancel / pause ───────────────────────────────────────────

    def cancel(self, job_type: str) -> None:
        """Signal cancellation for a running task."""
        event = self._cancel_events.get(job_type)
        if event is not None:
            event.set()

    def is_cancelled(self, job_type: str) -> bool:
        """Check whether a task has been cancelled."""
        event = self._cancel_events.get(job_type)
        return event is not None and event.is_set()

    def get_cancel_event(self, job_type: str) -> Optional[ThreadingEvent]:
        """Return the cancel event for a running task."""
        return self._cancel_events.get(job_type)

    def pause(self, job_type: str) -> None:
        """Pause a task (clear the asyncio.Event so the worker loop blocks)."""
        event = self._pause_events.get(job_type)
        if event is not None:
            event.clear()

    def resume(self, job_type: str) -> None:
        """Resume a paused task."""
        event = self._pause_events.get(job_type)
        if event is not None:
            event.set()

    def is_paused(self, job_type: str) -> bool:
        """Check whether a task is paused."""
        event = self._pause_events.get(job_type)
        return event is not None and not event.is_set()

    def get_pause_event(self, job_type: str) -> Optional[asyncio.Event]:
        """Return the pause event for a running task."""
        return self._pause_events.get(job_type)


# Module-level singletons
jobs = JobManager()
task_store = TaskStore()
atexit.register(jobs.cleanup_all)
