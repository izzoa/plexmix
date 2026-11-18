"""Session-scoped job lifetime: liveness adapter and the Generator cancel handle.

Covers the job-cancellation spec requirements "Session-scoped work is reaped only
for disconnected sessions" and "Cancellation handles live outside serialized UI
state". Reflex is mocked in ``conftest.py``; no network, keyring or ~/.plexmix.
"""

import asyncio
import pickle
import sys
import threading
import types
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from plexmix.ui import session_liveness
from plexmix.ui.job_manager import JobManager

TOKEN_A = "tok-tab-a"
TOKEN_B = "tok-tab-b"


# ── session_liveness ─────────────────────────────────────────────────


@pytest.fixture
def fresh_liveness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the cached app so each test resolves it again."""
    monkeypatch.setattr(session_liveness, "_app", None)


def _fake_app(token_to_sid: Any) -> SimpleNamespace:
    return SimpleNamespace(event_namespace=SimpleNamespace(token_to_sid=token_to_sid))


class TestConnectedClientTokens:
    def test_returns_token_set(self, fresh_liveness: None, monkeypatch: pytest.MonkeyPatch):
        app = _fake_app({TOKEN_A: "sid-1", TOKEN_B: "sid-2"})
        monkeypatch.setattr(session_liveness, "_get_app", lambda: app)

        tokens = session_liveness.connected_client_tokens()

        assert tokens == frozenset({TOKEN_A, TOKEN_B})
        assert isinstance(tokens, frozenset)

    def test_reads_live_map_each_call(self, fresh_liveness: None, monkeypatch: pytest.MonkeyPatch):
        token_to_sid: Dict[str, str] = {TOKEN_A: "sid-1"}
        app = _fake_app(token_to_sid)
        monkeypatch.setattr(session_liveness, "_get_app", lambda: app)

        assert session_liveness.connected_client_tokens() == frozenset({TOKEN_A})
        del token_to_sid[TOKEN_A]
        token_to_sid[TOKEN_B] = "sid-2"
        assert session_liveness.connected_client_tokens() == frozenset({TOKEN_B})

    def test_no_connections_is_an_empty_set_not_none(
        self, fresh_liveness: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(session_liveness, "_get_app", lambda: _fake_app({}))
        assert session_liveness.connected_client_tokens() == frozenset()

    def test_accessor_raising_returns_none(
        self, fresh_liveness: None, monkeypatch: pytest.MonkeyPatch
    ):
        def boom() -> Any:
            raise RuntimeError("app not available")

        monkeypatch.setattr(session_liveness, "_get_app", boom)
        assert session_liveness.connected_client_tokens() is None

    def test_missing_event_namespace_returns_none(
        self, fresh_liveness: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            session_liveness, "_get_app", lambda: SimpleNamespace(event_namespace=None)
        )
        assert session_liveness.connected_client_tokens() is None

    def test_renamed_attribute_returns_none(
        self, fresh_liveness: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            session_liveness, "_get_app", lambda: SimpleNamespace(event_namespace=object())
        )
        assert session_liveness.connected_client_tokens() is None

    def test_non_mapping_token_map_returns_none(
        self, fresh_liveness: None, monkeypatch: pytest.MonkeyPatch
    ):
        # e.g. a future Reflex turning token_to_sid into something else entirely.
        monkeypatch.setattr(session_liveness, "_get_app", lambda: _fake_app(MagicMock()))
        assert session_liveness.connected_client_tokens() is None

    def test_resolves_app_through_reflex_prerequisites_once(
        self, fresh_liveness: None, monkeypatch: pytest.MonkeyPatch
    ):
        app = _fake_app({TOKEN_A: "sid-1"})
        calls: List[int] = []

        def get_and_validate_app() -> SimpleNamespace:
            calls.append(1)
            return SimpleNamespace(app=app, module=None)

        prerequisites = types.ModuleType("reflex.utils.prerequisites")
        prerequisites.get_and_validate_app = get_and_validate_app  # type: ignore[attr-defined]
        utils = types.ModuleType("reflex.utils")
        utils.prerequisites = prerequisites  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "reflex.utils", utils)
        monkeypatch.setitem(sys.modules, "reflex.utils.prerequisites", prerequisites)

        assert session_liveness.connected_client_tokens() == frozenset({TOKEN_A})
        assert session_liveness.connected_client_tokens() == frozenset({TOKEN_A})
        assert len(calls) == 1  # app cached; get_app's sys.path insert runs once

    def test_reflex_unavailable_returns_none(
        self, fresh_liveness: None, monkeypatch: pytest.MonkeyPatch
    ):
        prerequisites = types.ModuleType("reflex.utils.prerequisites")

        def get_and_validate_app() -> Any:
            raise RuntimeError("no rxconfig")

        prerequisites.get_and_validate_app = get_and_validate_app  # type: ignore[attr-defined]
        utils = types.ModuleType("reflex.utils")
        utils.prerequisites = prerequisites  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "reflex.utils", utils)
        monkeypatch.setitem(sys.modules, "reflex.utils.prerequisites", prerequisites)

        assert session_liveness.connected_client_tokens() is None
        assert session_liveness._app is None  # a failure is not cached


# ── Generator cancel handle ──────────────────────────────────────────


@pytest.fixture
def fresh_jobs(monkeypatch: pytest.MonkeyPatch) -> JobManager:
    """A private JobManager wired into every module that uses the singleton."""
    import plexmix.ui.job_manager as job_manager
    import plexmix.ui.states.generator_state as generator_state

    manager = JobManager()
    monkeypatch.setattr(job_manager, "jobs", manager)
    monkeypatch.setattr(generator_state, "jobs", manager)
    return manager


def _harness_cls() -> type:
    """GeneratorState plus the bits of rx.State a background handler needs."""
    from plexmix.ui.states.generator_state import GeneratorState

    class _GeneratorHarness(GeneratorState):  # type: ignore[misc, valid-type]
        async def __aenter__(self) -> "_GeneratorHarness":
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        def access_allowed(self) -> bool:
            return True

        def check_configuration_status(self) -> None:
            pass

        def load_library_stats(self) -> None:
            pass

    return _GeneratorHarness


def _make_state(token: str, mood: str = "rainy day acoustic") -> Any:
    state = _harness_cls()()
    state.router = SimpleNamespace(session=SimpleNamespace(client_token=token))
    state.mood_query = mood
    return state


def _install_fake_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    during_generate: Callable[[Optional[threading.Event]], None],
) -> None:
    """Patch settings, DB, index and providers so generate_playlist runs offline.

    *during_generate* runs inside the executor while the generation is in flight
    and receives the cancel event the generator was handed.
    """
    db_path = tmp_path / "plexmix.db"
    db_path.write_bytes(b"")
    fake_settings = SimpleNamespace(
        database=SimpleNamespace(
            get_db_path=lambda: db_path,
            get_index_path=lambda: tmp_path / "embeddings.index",
        ),
        embedding=SimpleNamespace(default_provider="local", model="all-MiniLM-L6-v2"),
    )
    monkeypatch.setattr(
        "plexmix.config.settings.Settings.load_from_file", lambda *a, **k: fake_settings
    )
    monkeypatch.setattr(
        "plexmix.services.providers.build_embedding_generator",
        lambda settings: SimpleNamespace(get_dimension=lambda: 384),
    )
    # generate_playlist builds the provider through the reason-returning variant
    # (webui-honest-status-and-first-run 7.2).
    monkeypatch.setattr(
        "plexmix.services.providers.build_embedding_generator_with_reason",
        lambda settings: (SimpleNamespace(get_dimension=lambda: 384), None),
    )
    monkeypatch.setattr("plexmix.database.sqlite_manager.SQLiteManager", MagicMock())
    monkeypatch.setattr(
        "plexmix.database.vector_index.VectorIndex",
        lambda **kw: SimpleNamespace(index=SimpleNamespace(ntotal=10), dimension_mismatch=False),
    )

    class _FakePlaylistGenerator:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def generate(self, **kwargs: Any) -> List[Dict[str, Any]]:
            kwargs["progress_callback"](0.5, "Halfway there")
            during_generate(kwargs.get("cancel_event"))
            return [{"id": "1", "title": "Song", "artist": "Band", "duration_ms": 180000}]

    monkeypatch.setattr("plexmix.playlist.generator.PlaylistGenerator", _FakePlaylistGenerator)

    # Skip the 5 s auto-dismiss pause at the end of a successful generation.
    real_sleep = asyncio.sleep

    async def fast_sleep(delay: float, result: Any = None) -> Any:
        return await real_sleep(0, result)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)


class TestGeneratorCancelHandle:
    def test_state_has_no_cancel_event_attribute(self):
        from plexmix.ui.states.generator_state import GeneratorState

        assert not hasattr(GeneratorState, "_cancel_event")
        assert "_cancel_event" not in getattr(GeneratorState, "__annotations__", {})

    def test_state_pickles_while_generation_runs(
        self, fresh_jobs: JobManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ):
        state = _make_state(TOKEN_A)
        seen: Dict[str, Any] = {}

        def during(cancel_event: Optional[threading.Event]) -> None:
            seen["cancel_event"] = cancel_event
            seen["registered"] = fresh_jobs.get_cancel_event(TOKEN_A, "generation")
            seen["is_generating"] = state.is_generating
            failures = []
            for name, value in list(vars(state).items()):
                try:
                    pickle.dumps(value)
                except Exception as exc:  # pragma: no cover - only on regression
                    failures.append(f"{name}: {exc}")
            seen["failures"] = failures
            seen["whole"] = pickle.dumps(dict(vars(state)))

        _install_fake_generation(monkeypatch, tmp_path, during)
        asyncio.run(state.generate_playlist())

        assert seen["is_generating"] is True
        assert seen["failures"] == []
        assert seen["whole"]
        # The generator got the JobManager-owned event, not one stored on state.
        assert isinstance(seen["cancel_event"], threading.Event)
        assert seen["registered"] is seen["cancel_event"]
        assert not any(
            isinstance(v, (threading.Event, type(threading.Lock()), asyncio.Event, asyncio.Task))
            for v in vars(state).values()
        )
        # Completed normally and released its handle in ``finally``.
        assert state.is_generating is False
        assert [t["id"] for t in state.generated_playlist] == ["1"]
        assert fresh_jobs.get_cancel_event(TOKEN_A, "generation") is None
        assert not seen["cancel_event"].is_set()

    def test_cancel_generation_sets_registered_event(self, fresh_jobs: JobManager):
        state = _make_state(TOKEN_A)
        event = fresh_jobs.start(TOKEN_A, "generation")
        other = fresh_jobs.start(TOKEN_B, "generation")
        state._generation_id = 3

        state.cancel_generation()

        assert event.is_set()
        assert not other.is_set()  # only this session's generation
        assert state._generation_id == 4  # superseded-callback guard kept
        assert state.is_generating is False

    def test_cancel_without_running_generation_is_safe(self, fresh_jobs: JobManager):
        state = _make_state(TOKEN_A)
        state.cancel_generation()
        assert fresh_jobs.get_cancel_event(TOKEN_A, "generation") is None

    def test_cancel_reaches_running_generation(
        self, fresh_jobs: JobManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ):
        state = _make_state(TOKEN_A)
        seen: Dict[str, Any] = {}

        def during(cancel_event: Optional[threading.Event]) -> None:
            seen["event"] = cancel_event
            state.cancel_generation()  # user clicks Cancel mid-generation
            seen["set_after_cancel"] = cancel_event is not None and cancel_event.is_set()

        _install_fake_generation(monkeypatch, tmp_path, during)
        asyncio.run(state.generate_playlist())

        assert seen["set_after_cancel"] is True
        assert state.is_generating is False
        assert state.generated_playlist == []  # results of a cancelled run are dropped
        assert fresh_jobs.get_cancel_event(TOKEN_A, "generation") is None

    def test_global_cancel_returns_generator_cancel(self, monkeypatch: pytest.MonkeyPatch):
        from plexmix.ui.job_manager import task_store
        from plexmix.ui.states.app_state import AppState
        from plexmix.ui.states.generator_state import GeneratorState

        cancel_all = MagicMock(return_value=[])
        monkeypatch.setattr(task_store, "cancel_all_running", cancel_all)
        state = AppState()
        state.pending_cancel_job = "__all__"

        result = state.confirm_cancel()

        assert result is GeneratorState.cancel_generation
        cancel_all.assert_called_once()
        assert state.pending_cancel_job == ""

    def test_global_cancel_stops_this_sessions_generation(
        self, fresh_jobs: JobManager, monkeypatch: pytest.MonkeyPatch
    ):
        from plexmix.ui.job_manager import task_store
        from plexmix.ui.states.app_state import AppState

        monkeypatch.setattr(task_store, "cancel_all_running", MagicMock(return_value=[]))
        mine = fresh_jobs.start(TOKEN_A, "generation")
        theirs = fresh_jobs.start(TOKEN_B, "generation")

        app = AppState()
        app.pending_cancel_job = "__all__"
        handler = app.confirm_cancel()
        # Reflex dispatches the returned handler for the issuing session.
        handler(_make_state(TOKEN_A))

        assert mine.is_set()
        assert not theirs.is_set()

    def test_new_generation_does_not_lose_handle_to_old_finally(self, fresh_jobs: JobManager):
        """A finishing generation must not drop a newer one's cancel handle."""
        old = fresh_jobs.start(TOKEN_A, "generation")
        new = fresh_jobs.start(TOKEN_A, "generation")
        fresh_jobs.finish(TOKEN_A, "generation", old)  # old run's ``finally``
        state = _make_state(TOKEN_A)
        state.cancel_generation()
        assert new.is_set()


class TestGenerationAcrossTabs:
    def test_generation_survives_page_load_in_other_tab(
        self, fresh_jobs: JobManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ):
        monkeypatch.setattr(
            session_liveness, "connected_client_tokens", lambda: frozenset({TOKEN_A, TOKEN_B})
        )
        state_a = _make_state(TOKEN_A)
        seen: Dict[str, Any] = {}

        def during(cancel_event: Optional[threading.Event]) -> None:
            # Tab B opens /dashboard, /settings, … while tab A is generating.
            tab_b = _make_state(TOKEN_B)
            for _ in range(3):
                tab_b.load_app_data()
            seen["cancelled"] = cancel_event is not None and cancel_event.is_set()

        _install_fake_generation(monkeypatch, tmp_path, during)
        asyncio.run(state_a.generate_playlist())

        assert seen["cancelled"] is False
        assert [t["id"] for t in state_a.generated_playlist] == ["1"]
        assert state_a.generation_progress == 100
        assert state_a.is_generating is False

    def test_orphaned_generation_reaped_after_grace(
        self, fresh_jobs: JobManager, monkeypatch: pytest.MonkeyPatch
    ):
        import time

        from plexmix.ui.job_manager import SESSION_REAP_GRACE_S

        orphan = fresh_jobs.start(TOKEN_A, "generation")
        monkeypatch.setattr(
            session_liveness, "connected_client_tokens", lambda: frozenset({TOKEN_B})
        )
        clock = [1_000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        tab_b = _make_state(TOKEN_B)

        tab_b.load_app_data()  # tab A first seen disconnected
        clock[0] += SESSION_REAP_GRACE_S - 1
        tab_b.load_app_data()
        assert not orphan.is_set()

        clock[0] += 1
        tab_b.load_app_data()
        assert orphan.is_set()
        assert fresh_jobs.get_cancel_event(TOKEN_A, "generation") is None

    def test_reconnect_within_grace_keeps_generation(
        self, fresh_jobs: JobManager, monkeypatch: pytest.MonkeyPatch
    ):
        import time

        running = fresh_jobs.start(TOKEN_A, "generation")
        connected = {"now": frozenset({TOKEN_B})}
        monkeypatch.setattr(session_liveness, "connected_client_tokens", lambda: connected["now"])
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        tab_b = _make_state(TOKEN_B)

        tab_b.load_app_data()  # tab A dropped (reload in progress)
        clock[0] = 30.0
        connected["now"] = frozenset({TOKEN_A, TOKEN_B})  # same token reconnects
        tab_b.load_app_data()
        clock[0] = 10_000.0
        tab_b.load_app_data()

        assert not running.is_set()

    def test_unknown_liveness_never_reaps(
        self, fresh_jobs: JobManager, monkeypatch: pytest.MonkeyPatch
    ):
        import time

        running = fresh_jobs.start(TOKEN_A, "generation")
        monkeypatch.setattr(session_liveness, "connected_client_tokens", lambda: None)
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        tab_b = _make_state(TOKEN_B)

        for step in range(5):
            clock[0] = step * 1_000.0
            tab_b.load_app_data()

        assert not running.is_set()
        assert fresh_jobs.get_cancel_event(TOKEN_A, "generation") is running


# ── Draft-only sessions, the periodic sweep and the landing route ────


@pytest.fixture
def fresh_drafts(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A private SecretDrafts registry in place of the module singleton."""
    import plexmix.ui.secret_drafts as secret_drafts_module
    from plexmix.ui.secret_drafts import SecretDrafts

    drafts = SecretDrafts()
    monkeypatch.setattr(secret_drafts_module, "secret_drafts", drafts)
    return drafts


@pytest.fixture
def no_sweep_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_liveness, "_sweep_loop", None)


class _FakeLoop:
    """Records ``call_later`` scheduling instead of running anything."""

    def __init__(self) -> None:
        self.scheduled: List[Any] = []
        self.closed = False

    def call_later(self, delay: float, callback: Callable[..., Any], *args: Any) -> None:
        self.scheduled.append((delay, callback, args))

    def is_closed(self) -> bool:
        return self.closed


class TestDraftOnlySessionsAreReaped:
    def test_reap_disconnected_tracks_extra_tokens(self) -> None:
        manager = JobManager()
        assert manager.reap_disconnected(frozenset(), 0.0, 120.0, extra_tokens={"tok-x"}) == []
        assert manager.reap_disconnected(frozenset(), 119.0, 120.0, extra_tokens={"tok-x"}) == []
        assert manager.reap_disconnected(frozenset(), 120.0, 120.0, extra_tokens={"tok-x"}) == [
            "tok-x"
        ]
        # Timer forgotten once reaped; an extra token no longer listed is forgotten too.
        assert manager._disconnected_since == {}
        manager.reap_disconnected(frozenset(), 0.0, 120.0, extra_tokens={"tok-y"})
        manager.reap_disconnected(frozenset(), 1.0, 120.0, extra_tokens=())
        assert manager._disconnected_since == {}

    def test_closed_settings_tab_drafts_dropped_after_grace(
        self, fresh_jobs: JobManager, fresh_drafts: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Settings tab staged a key, owns no job, and was closed without saving."""
        import time

        from plexmix.ui.job_manager import SESSION_REAP_GRACE_S

        monkeypatch.setattr(
            session_liveness, "connected_client_tokens", lambda: frozenset({TOKEN_B})
        )
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        fresh_drafts.stage(TOKEN_A, "ai_api_key", "SENTINEL-OPENAI-0000")
        fresh_drafts.stage(TOKEN_B, "plex_token", "SENTINEL-PLEX-0000")
        tab_b = _make_state(TOKEN_B)

        tab_b.load_app_data()  # tab A first seen disconnected
        clock[0] = SESSION_REAP_GRACE_S - 1
        tab_b.load_app_data()
        assert fresh_drafts.action(TOKEN_A, "ai_api_key") == "replace"

        clock[0] = SESSION_REAP_GRACE_S
        tab_b.load_app_data()
        assert fresh_drafts.tokens() == frozenset({TOKEN_B})
        assert fresh_drafts.get(TOKEN_B, "plex_token") == "SENTINEL-PLEX-0000"

    def test_connected_draft_owner_is_kept(
        self, fresh_jobs: JobManager, fresh_drafts: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        monkeypatch.setattr(
            session_liveness, "connected_client_tokens", lambda: frozenset({TOKEN_A})
        )
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        fresh_drafts.stage(TOKEN_A, "ai_api_key", "SENTINEL-OPENAI-0000")
        for step in range(5):
            clock[0] = step * 1_000.0
            assert session_liveness.sweep_disconnected_sessions() == []
        assert fresh_drafts.action(TOKEN_A, "ai_api_key") == "replace"

    def test_unknown_liveness_keeps_drafts(
        self, fresh_jobs: JobManager, fresh_drafts: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        monkeypatch.setattr(session_liveness, "connected_client_tokens", lambda: None)
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        fresh_drafts.stage(TOKEN_A, "ai_api_key", "SENTINEL-OPENAI-0000")
        for step in range(5):
            clock[0] = step * 1_000.0
            session_liveness.sweep_disconnected_sessions()
        assert fresh_drafts.action(TOKEN_A, "ai_api_key") == "replace"


@pytest.mark.usefixtures("no_sweep_loop")
class TestPeriodicSweep:
    def test_without_a_running_loop_nothing_is_scheduled(self) -> None:
        assert session_liveness.ensure_periodic_sweep() is False
        assert session_liveness._sweep_loop is None

    def test_scheduled_once_per_loop(self) -> None:
        async def main() -> Any:
            loop = asyncio.get_running_loop()
            first = session_liveness.ensure_periodic_sweep()
            second = session_liveness.ensure_periodic_sweep()
            return first, second, session_liveness._sweep_loop is loop

        assert asyncio.run(main()) == (True, False, True)

    def test_a_new_loop_gets_its_own_sweep(self) -> None:
        async def ensure() -> bool:
            return session_liveness.ensure_periodic_sweep()

        assert asyncio.run(ensure()) is True
        assert asyncio.run(ensure()) is True  # the first loop is closed by now

    def test_page_load_on_the_event_loop_starts_the_sweep(
        self, fresh_jobs: JobManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(session_liveness, "connected_client_tokens", lambda: None)

        async def load() -> bool:
            _make_state(TOKEN_A).load_app_data()
            return session_liveness._sweep_loop is asyncio.get_running_loop()

        assert asyncio.run(load()) is True

    def test_tick_sweeps_and_reschedules(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: List[str] = []
        monkeypatch.setattr(
            session_liveness, "sweep_disconnected_sessions", lambda: calls.append("sweep") or []
        )
        loop = _FakeLoop()
        monkeypatch.setattr(session_liveness, "_sweep_loop", loop)

        session_liveness._periodic_sweep(loop, 30.0)  # type: ignore[arg-type]

        assert calls == ["sweep"]
        assert loop.scheduled == [(30.0, session_liveness._periodic_sweep, (loop, 30.0))]

    def test_tick_survives_a_failing_sweep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom() -> List[str]:
            raise RuntimeError("sweep failed")

        monkeypatch.setattr(session_liveness, "sweep_disconnected_sessions", boom)
        loop = _FakeLoop()
        monkeypatch.setattr(session_liveness, "_sweep_loop", loop)
        session_liveness._periodic_sweep(loop, 30.0)  # type: ignore[arg-type]
        assert len(loop.scheduled) == 1

    def test_tick_stops_on_a_replaced_or_closed_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(session_liveness, "sweep_disconnected_sessions", lambda: [])
        old, new = _FakeLoop(), _FakeLoop()
        monkeypatch.setattr(session_liveness, "_sweep_loop", new)
        session_liveness._periodic_sweep(old, 30.0)  # type: ignore[arg-type]
        assert old.scheduled == []
        new.closed = True
        session_liveness._periodic_sweep(new, 30.0)  # type: ignore[arg-type]
        assert new.scheduled == []

    def test_closed_tab_reaped_by_ticks_alone(
        self, fresh_jobs: JobManager, fresh_drafts: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only tab: it starts a generation and closes; no page is ever loaded again."""
        import time

        from plexmix.ui.job_manager import SESSION_REAP_GRACE_S

        orphan = fresh_jobs.start(TOKEN_A, "generation")
        monkeypatch.setattr(session_liveness, "connected_client_tokens", lambda: frozenset())
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        loop = _FakeLoop()
        monkeypatch.setattr(session_liveness, "_sweep_loop", loop)
        interval = session_liveness.SWEEP_INTERVAL_S

        ticks = 0
        while not orphan.is_set():
            session_liveness._periodic_sweep(loop, interval)  # type: ignore[arg-type]
            ticks += 1
            clock[0] += interval
            assert ticks <= SESSION_REAP_GRACE_S / interval + 2
        assert clock[0] - interval >= SESSION_REAP_GRACE_S
        assert fresh_jobs.get_cancel_event(TOKEN_A, "generation") is None

    def test_reconnect_between_ticks_resets_the_timer(
        self, fresh_jobs: JobManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two short blips 125 s apart never add up to a reap."""
        import time

        running = fresh_jobs.start(TOKEN_A, "generation")
        connected = {"now": frozenset()}
        monkeypatch.setattr(session_liveness, "connected_client_tokens", lambda: connected["now"])
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])

        session_liveness.sweep_disconnected_sessions()  # blip 1 seen at t=0
        clock[0] = 30.0
        connected["now"] = frozenset({TOKEN_A})
        session_liveness.sweep_disconnected_sessions()  # reconnected: timer cleared
        clock[0] = 125.0
        connected["now"] = frozenset()
        session_liveness.sweep_disconnected_sessions()  # blip 2 starts a new timer
        assert not running.is_set()


def test_dashboard_load_runs_the_session_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The landing route sweeps like every other page (and starts the periodic sweep)."""
    from plexmix.ui.states.dashboard_state import DashboardState

    calls: List[str] = []
    monkeypatch.setattr(
        session_liveness, "sweep_disconnected_sessions", lambda: calls.append("sweep") or []
    )
    monkeypatch.setattr(
        session_liveness, "ensure_periodic_sweep", lambda: calls.append("periodic") or False
    )
    monkeypatch.setattr(DashboardState, "check_auth", lambda self: True)
    for name in ("check_configuration_status", "load_library_stats", "load_recent_playlists"):
        monkeypatch.setattr(DashboardState, name, lambda self, _n=name: calls.append(_n))

    DashboardState().on_load()

    assert calls == [
        "sweep",
        "periodic",
        "check_configuration_status",
        "load_library_stats",
        "load_recent_playlists",
    ]
