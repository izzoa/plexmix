"""webui-honest-status-and-first-run: History loading/empty/error states (11.1) and the
``/history?playlist=<id>`` deep link from the Dashboard's recent rows (5.5).

Reflex is mocked (tests/ui/conftest.py). The database is a temp SQLite file that a
stubbed ``Settings.load_from_file`` points at (never ~/.plexmix); no network.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def _harness() -> Any:
    """HistoryState plus the bits of rx.State a background handler needs, with
    fresh per-instance values (the mocked State shares class attributes)."""
    from plexmix.ui.states.history_state import HistoryState

    class _Harness(HistoryState):  # type: ignore[misc, valid-type]
        async def __aenter__(self) -> "_Harness":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        def access_allowed(self) -> bool:
            return True

    state = _Harness()
    state.playlists = []
    state.selected_playlist = {}
    state.selected_playlist_tracks = []
    state.is_detail_modal_open = False
    state.search_query = ""
    state.error_message = ""
    state._pending_playlist_id = ""
    return state


def _with_query(state: Any, params: dict[str, Any]) -> Any:
    state.router = SimpleNamespace(url=SimpleNamespace(query_parameters=params))
    state.check_auth = lambda: True
    state.load_app_data = lambda: None
    return state


@pytest.fixture
def markers(monkeypatch: pytest.MonkeyPatch) -> None:
    """``rx.call_script`` returns ``("call_script", js)`` (set_value is mocked already)."""
    from plexmix.ui.states import history_state

    monkeypatch.setattr(history_state.rx, "call_script", lambda js: ("call_script", js))


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A temp library with two playlists; ``Settings.load_from_file`` points at it."""
    from plexmix.config import settings as settings_mod
    from plexmix.database.models import Album, Artist, Playlist, Track
    from plexmix.database.sqlite_manager import SQLiteManager

    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path))
    db_path = tmp_path / "plexmix.db"
    fake = SimpleNamespace(database=SimpleNamespace(get_db_path=lambda: db_path))
    monkeypatch.setattr(
        settings_mod.Settings, "load_from_file", classmethod(lambda cls, *a, **k: fake)
    )
    with SQLiteManager(str(db_path)) as conn:
        aid = conn.insert_artist(Artist(plex_key="a1", name="Bill Evans"))
        alb = conn.insert_album(Album(plex_key="al1", title="Waltz", artist_id=aid))
        tracks = [
            conn.insert_track(
                Track(plex_key=f"t{i}", title=f"Song {i}", artist_id=aid, album_id=alb)
            )
            for i in range(3)
        ]
        jazz = conn.insert_playlist(
            Playlist(name="Late Night Jazz", mood_query="smoky jazz", created_by_ai=True)
        )
        conn.add_tracks_to_playlist(jazz, tracks)
        other = conn.insert_playlist(Playlist(name="Morning", created_by_ai=False))
    return SimpleNamespace(path=db_path, jazz=str(jazz), other=str(other))


@pytest.fixture
def no_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from plexmix.config import settings as settings_mod

    db_path = tmp_path / "missing" / "plexmix.db"
    fake = SimpleNamespace(database=SimpleNamespace(get_db_path=lambda: db_path))
    monkeypatch.setattr(
        settings_mod.Settings, "load_from_file", classmethod(lambda cls, *a, **k: fake)
    )
    return db_path


def load(state: Any) -> None:
    asyncio.run(state.load_playlists())


# ── 11.1 Loading versus empty ────────────────────────────────────────────


class TestLoadingState:
    def test_first_render_is_loading_not_empty(self) -> None:
        from plexmix.ui.states.history_state import HistoryState

        assert HistoryState.loading_playlists is True
        assert HistoryState.load_failed is False

    def test_placeholder_only_while_nothing_is_listed(self) -> None:
        state = _harness()
        state.loading_playlists = True
        state.load_failed = False
        assert state.show_loading is True and state.has_playlists is False
        state.playlists = [{"id": "1", "name": "Jazz"}]
        assert state.show_loading is False  # a revisit keeps the last real list
        assert state.has_playlists is True
        state.playlists = []
        state.load_failed = True
        assert state.show_loading is False  # the error callout shows instead
        state.load_failed = False
        state.loading_playlists = False
        assert state.show_loading is False  # loaded and empty: "No playlists yet"

    def test_a_completed_load_ends_loading(self, db: Any) -> None:
        state = _harness()
        load(state)
        assert state.loading_playlists is False
        assert state.load_failed is False
        assert {p["name"] for p in state.playlists} == {"Morning", "Late Night Jazz"}
        assert state.error_message == ""

    def test_no_database_is_an_empty_list_after_loading(self, no_db: Path) -> None:
        state = _harness()
        load(state)
        assert state.loading_playlists is False
        assert state.playlists == []
        assert state.load_failed is False
        assert not no_db.exists()

    def test_a_failed_load_is_an_error_with_retry_never_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.states import history_state

        def boom() -> list[dict[str, str]]:
            raise RuntimeError("database disk image is malformed")

        monkeypatch.setattr(history_state, "_read_playlists", boom)
        state = _harness()
        load(state)
        assert state.load_failed is True
        assert state.loading_playlists is False
        assert "malformed" in state.error_message
        assert state.playlists == []

    def test_retry_returns_to_loading_and_reloads(self) -> None:
        from plexmix.ui.states.history_state import HistoryState

        state = _harness()
        state.load_failed = True
        state.loading_playlists = False
        state.error_message = "database is locked"
        assert state.retry_load() is HistoryState.load_playlists
        assert state.loading_playlists is True
        assert state.load_failed is False
        assert state.error_message == ""

    def test_a_reload_after_a_failure_clears_it(self, db: Any) -> None:
        state = _harness()
        state.load_failed = True
        state.error_message = "database is locked"
        load(state)
        assert state.load_failed is False and state.error_message == ""
        assert len(state.playlists) == 2

    def test_blocking_reads_run_off_the_event_loop(
        self, db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading

        from plexmix.ui.states import history_state

        threads: list[str] = []
        real = history_state._read_playlists

        def spy() -> list[dict[str, str]]:
            threads.append(threading.current_thread().name)
            return real()

        monkeypatch.setattr(history_state, "_read_playlists", spy)
        load(_harness())
        assert threads and threads[0] != threading.main_thread().name


class TestEmptyStates:
    def test_clear_search_empties_the_client_owned_field(self, markers: None) -> None:
        state = _harness()
        state.search_query = "nothing like this"
        events = state.clear_search()
        assert state.search_query == ""
        assert ("set_value", "hist-search", "") in events
        focus = [e for e in events if e[0] == "call_script"]
        assert focus and "#hist-search" in focus[0][1]

    def test_no_match_versus_no_playlists(self, db: Any) -> None:
        state = _harness()
        load(state)
        state.set_search_query("zzz")
        assert state.filtered_playlists == [] and len(state.playlists) == 2  # no match
        state.set_search_query("")
        assert len(state.filtered_playlists) == 2


# ── 5.5 Deep link from the Dashboard ─────────────────────────────────────


class TestDeepLink:
    def test_on_load_reads_the_playlist_parameter(self, markers: None) -> None:
        from plexmix.ui.states.history_state import STRIP_PLAYLIST_PARAM_JS, HistoryState

        state = _with_query(_harness(), {"playlist": "7"})
        events = state.on_load()
        assert state._pending_playlist_id == "7"
        assert events[0] is HistoryState.load_playlists
        assert ("call_script", STRIP_PLAYLIST_PARAM_JS) in events
        assert "replaceState" in STRIP_PLAYLIST_PARAM_JS

    def test_on_load_without_the_parameter_just_loads(self) -> None:
        from plexmix.ui.states.history_state import HistoryState

        state = _with_query(_harness(), {})
        assert state.on_load() is HistoryState.load_playlists
        assert state._pending_playlist_id == ""

    def test_repeated_parameter_uses_the_first(self, markers: None) -> None:
        state = _with_query(_harness(), {"playlist": ["5", "6"]})
        state.on_load()
        assert state._pending_playlist_id == "5"

    def test_no_router_is_harmless(self) -> None:
        from plexmix.ui.states.history_state import HistoryState

        state = _harness()
        state.check_auth = lambda: True
        state.load_app_data = lambda: None
        assert state.on_load() is HistoryState.load_playlists

    def test_unauthenticated_reads_nothing(self) -> None:
        state = _with_query(_harness(), {"playlist": "7"})
        state.check_auth = lambda: False
        assert state.on_load() is None
        assert state._pending_playlist_id == ""

    def test_existing_playlist_opens_its_detail_after_the_list_loads(self, db: Any) -> None:
        state = _harness()
        state._pending_playlist_id = db.jazz
        load(state)
        assert len(state.playlists) == 2  # the list is shown behind the dialog
        assert state.is_detail_modal_open is True
        assert state.selected_playlist["id"] == db.jazz
        assert state.selected_playlist["name"] == "Late Night Jazz"
        assert state.selected_playlist["track_count"] == "3"
        assert [t["position"] for t in state.selected_playlist_tracks] == ["1", "2", "3"]
        assert state.error_message == ""
        assert state._pending_playlist_id == ""

    @pytest.mark.parametrize("pid", ["99999", "abc", "-1", ""])
    def test_missing_playlist_shows_the_list_with_a_message(self, db: Any, pid: str) -> None:
        from plexmix.ui.states.history_state import PLAYLIST_NOT_FOUND_MESSAGE

        state = _harness()
        state._pending_playlist_id = pid
        load(state)
        assert len(state.playlists) == 2
        assert state.is_detail_modal_open is False
        if pid:
            assert state.error_message == PLAYLIST_NOT_FOUND_MESSAGE
            assert PLAYLIST_NOT_FOUND_MESSAGE == "That playlist no longer exists."
            assert state.load_failed is False  # a notice above the list, not a load error
        else:
            assert state.error_message == ""

    def test_missing_database_says_the_playlist_no_longer_exists(self, no_db: Path) -> None:
        state = _harness()
        state._pending_playlist_id = "3"
        load(state)
        assert state.error_message == "That playlist no longer exists."

    def test_a_second_trigger_keeps_the_message(self, db: Any) -> None:
        # on_load runs from the route and from on_mount: the second load (with the
        # deep link already consumed) must not wipe the first one's message.
        state = _harness()
        state._pending_playlist_id = "99999"
        load(state)
        load(state)
        assert state.error_message == "That playlist no longer exists."

    def test_dismiss_clears_the_notice_but_not_a_load_failure(self) -> None:
        state = _harness()
        state.error_message = "That playlist no longer exists."
        state.load_failed = False
        state.dismiss_error()
        assert state.error_message == ""
        state.error_message = "database is locked"
        state.load_failed = True
        state.dismiss_error()
        assert state.error_message == "database is locked"

    def test_a_failed_load_keeps_the_deep_link_for_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.states import history_state

        def boom() -> list[dict[str, str]]:
            raise OSError("disk I/O error")

        monkeypatch.setattr(history_state, "_read_playlists", boom)
        state = _harness()
        state._pending_playlist_id = "7"
        load(state)
        assert state.load_failed is True
        assert state._pending_playlist_id == "7"

    def test_card_selection_still_opens_the_dialog(self, db: Any) -> None:
        state = _harness()
        assert state.select_playlist(db.jazz) is None
        assert state.is_detail_modal_open is True
        assert state.selected_playlist["name"] == "Late Night Jazz"
        missing = _harness()
        assert missing.select_playlist("99999") is None
        assert missing.is_detail_modal_open is False
