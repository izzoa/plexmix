"""Unit tests for the command palette state (webui-input-responsiveness, group 2).

Reflex is mocked by ``tests/ui/conftest.py``: ``rx.event`` is a pass-through and
``rx.State`` a bare class, so handlers are called directly and async handlers run
through ``asyncio.run``. Cross-state handlers (``GeneratorState.prefill_prompt``,
``submit_prompt``, ``AppState.request_cancel``) are patched with mocks, and the
database is a temporary SQLite file reached through a patched ``Settings``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plexmix.ui.states import command_palette_state as cps
from plexmix.ui.states.app_state import AppState
from plexmix.ui.states.command_palette_state import (
    ACTION_COMMANDS,
    NAV_COMMANDS,
    CommandPaletteState,
    load_recent_prompts,
    recent_prompt_entries,
)
from plexmix.ui.states.generator_state import PROMPT_INPUT_ID, GeneratorState
from plexmix.ui import job_manager
from plexmix.ui.job_manager import TaskStore

PALETTE_SOURCE = Path(cps.__file__)
UI_ROOT = PALETTE_SOURCE.parents[1]


def _pl(prompt: Optional[str], name: str = "Mix") -> SimpleNamespace:
    return SimpleNamespace(mood_query=prompt, name=name)


def _state(generating: bool = False) -> CommandPaletteState:
    state = CommandPaletteState()
    state.recent_prompts = []
    state.has_running_job = False
    state.get_state = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(is_generating=generating)
    )
    return state


@pytest.fixture(autouse=True)
def store() -> Iterator[TaskStore]:
    """A fresh, idle TaskStore in place of the global one; tests start jobs on it."""
    fresh = TaskStore()
    with patch.object(job_manager, "task_store", fresh):
        yield fresh


@pytest.fixture
def db_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``Settings.load_from_file()`` at a temp DB path; never reads ~/.plexmix."""
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path))
    db_path = tmp_path / "plexmix.db"
    fake = SimpleNamespace(database=SimpleNamespace(get_db_path=lambda: db_path))
    with patch("plexmix.config.settings.Settings.load_from_file", return_value=fake):
        yield db_path


def _seed(db_path: Path, rows: list[tuple[str, Optional[str], int]]) -> None:
    """Insert playlists as (name, mood_query, minutes ago)."""
    from plexmix.database.models import Playlist
    from plexmix.database.sqlite_manager import SQLiteManager

    now = datetime(2026, 1, 1, 12, 0, 0)
    with SQLiteManager(str(db_path)) as db:
        for name, prompt, minutes_ago in rows:
            db.insert_playlist(
                Playlist(
                    name=name,
                    mood_query=prompt,
                    created_at=now - timedelta(minutes=minutes_ago),
                )
            )


# ── 2.1: the server-side query model is gone ─────────────────────────────


class TestQueryModelRemoved:
    REMOVED = (
        "is_open",
        "query",
        "selected_index",
        "set_query",
        "nav_results",
        "action_results",
        "vibe_results",
        "has_results",
        "goto",
        "run_action",
        "run_vibe",
        "open_palette",
        "close_palette",
    )

    @pytest.mark.parametrize("name", REMOVED)
    def test_state_has_no_query_model(self, name: str) -> None:
        assert not hasattr(CommandPaletteState, name)

    def test_vibe_commands_removed(self) -> None:
        assert not hasattr(cps, "VIBE_COMMANDS")

    def test_no_palette_references_to_removed_names(self) -> None:
        """``grep -n "toggle_color_mode\\|set_query\\|VIBE_COMMANDS"`` finds no palette use."""
        offenders = []
        for path in UI_ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if "VIBE_COMMANDS" in line or "CommandPaletteState.set_query" in line:
                    offenders.append(f"{path.name}:{lineno}")
        assert offenders == []
        source = PALETTE_SOURCE.read_text(encoding="utf-8")
        assert "toggle_color_mode" not in source
        assert "set_query" not in source

    def test_nav_commands_carry_keywords_and_gkey(self) -> None:
        assert [c["gkey"] for c in NAV_COMMANDS] == ["d", "g", "l", "t", "h", "x", "s"]
        for cmd in NAV_COMMANDS:
            assert cmd["keywords"].strip()
            assert cmd["href"] == f"/{cmd['id']}"
        assert {c["href"] for c in NAV_COMMANDS} == {
            "/dashboard",
            "/generator",
            "/library",
            "/tagging",
            "/history",
            "/doctor",
            "/settings",
        }

    def test_action_commands_carry_keywords_and_routes(self) -> None:
        ids = [c["id"] for c in ACTION_COMMANDS]
        assert ids == ["generate", "sync", "tag", "doctor", "theme", "cancel"]
        for cmd in ACTION_COMMANDS:
            assert "keywords" in cmd
        routes = {c["id"]: c["href"] for c in ACTION_COMMANDS}
        assert routes == {
            "generate": "/generator",
            "sync": "/library",
            "tag": "/tagging",
            "doctor": "/doctor",
            "theme": "",
            "cancel": "",
        }

    def test_only_history_matches_hist(self) -> None:
        """Typing "hist" must leave only History among the static commands."""

        def text(c: dict[str, str]) -> str:
            return f"{c['title']} {c['sub']} {c['keywords']}".lower()

        matches = [c["id"] for c in NAV_COMMANDS + ACTION_COMMANDS if "hist" in text(c)]
        assert matches == ["history"]


# ── 2.2: recent prompts and running work ─────────────────────────────────


class TestRecentPromptEntries:
    def test_keeps_order_newest_first(self) -> None:
        entries = recent_prompt_entries([_pl("c"), _pl("b"), _pl("a")])
        assert [e["prompt"] for e in entries] == ["c", "b", "a"]

    def test_dedupes_ignoring_case_and_whitespace_keeping_newest(self) -> None:
        entries = recent_prompt_entries(
            [
                _pl("Rainy  Day jazz"),
                _pl("rainy day JAZZ"),
                _pl("  rainy day jazz  "),
                _pl("focus"),
            ]
        )
        assert [e["prompt"] for e in entries] == ["Rainy  Day jazz", "focus"]

    def test_skips_empty_and_missing_prompts(self) -> None:
        entries = recent_prompt_entries([_pl(None), _pl(""), _pl("   "), _pl("ok")])
        assert [e["prompt"] for e in entries] == ["ok"]

    def test_limit_of_five(self) -> None:
        entries = recent_prompt_entries([_pl(f"p{i}") for i in range(9)])
        assert [e["prompt"] for e in entries] == ["p0", "p1", "p2", "p3", "p4"]

    def test_duplicates_do_not_count_toward_the_limit(self) -> None:
        playlists = [_pl("a"), _pl("A"), _pl("b"), _pl("b "), _pl("c"), _pl("d"), _pl("e")]
        entries = recent_prompt_entries(playlists)
        assert [e["prompt"] for e in entries] == ["a", "b", "c", "d", "e"]

    def test_sub_names_the_playlist_or_says_use_this_prompt(self) -> None:
        entries = recent_prompt_entries(
            [_pl("late night", name="Night Owl"), _pl("Sunday coffee", name="sunday  COFFEE")]
        )
        assert entries[0]["sub"] == "Night Owl"
        assert entries[1]["sub"] == "Use this prompt"

    def test_search_text_is_lower_case_and_entries_are_str_dicts(self) -> None:
        (entry,) = recent_prompt_entries([_pl("Jazz For Cooking", name="Kitchen")])
        assert entry["search"] == "jazz for cooking kitchen recent prompt"
        assert set(entry) == {"prompt", "sub", "search"}
        assert all(isinstance(v, str) for v in entry.values())


class TestLoadRecentPrompts:
    def test_missing_db_gives_empty_list_and_is_not_created(self, db_settings: Path) -> None:
        assert not db_settings.exists()
        assert load_recent_prompts() == []
        assert not db_settings.exists()

    def test_reads_newest_first_deduped_and_limited(self, db_settings: Path) -> None:
        _seed(
            db_settings,
            [
                ("Oldest", "oldest prompt", 90),
                ("Coffee", "Sunday coffee", 50),
                ("Dup", "sunday   COFFEE", 40),
                ("No prompt", None, 35),
                ("Blank", "  ", 30),
                ("Rain", "rainy day", 20),
                ("Focus", "deep focus", 10),
                ("Gym", "gym energy", 5),
                ("Newest", "late night drive", 1),
            ],
        )
        entries = load_recent_prompts()
        assert [e["prompt"] for e in entries] == [
            "late night drive",
            "gym energy",
            "deep focus",
            "rainy day",
            "sunday   COFFEE",
        ]
        assert entries[0]["sub"] == "Newest"

    def test_db_errors_are_logged_at_debug_and_give_empty_list(
        self, db_settings: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        db_settings.write_text("not a database", encoding="utf-8")
        with caplog.at_level("DEBUG", logger=cps.logger.name):
            assert load_recent_prompts() == []
        records = [r for r in caplog.records if r.name == cps.logger.name]
        assert records and all(r.levelname == "DEBUG" for r in records)

    def test_settings_errors_give_empty_list(self) -> None:
        with patch(
            "plexmix.config.settings.Settings.load_from_file", side_effect=RuntimeError("boom")
        ):
            assert load_recent_prompts() == []


class TestOnOpenChange:
    def test_open_loads_recent_prompts(self) -> None:
        state = _state()
        prompts = [{"prompt": "a", "sub": "A", "search": "a a recent prompt"}]
        with patch.object(cps, "load_recent_prompts", return_value=prompts) as load:
            asyncio.run(state.on_open_change(True))
        load.assert_called_once_with()
        assert state.recent_prompts == prompts

    def test_open_with_missing_db_gives_empty_recents(self, db_settings: Path) -> None:
        state = _state()
        state.recent_prompts = [{"prompt": "stale", "sub": "", "search": ""}]
        asyncio.run(state.on_open_change(True))
        assert state.recent_prompts == []
        assert not db_settings.exists()

    def test_close_reads_nothing(self) -> None:
        state = _state(generating=True)
        with patch.object(cps, "load_recent_prompts") as load:
            asyncio.run(state.on_open_change(False))
        load.assert_not_called()
        state.get_state.assert_not_called()  # type: ignore[attr-defined]
        assert state.has_running_job is False

    def test_close_drops_the_running_flag(self, store: TaskStore) -> None:
        """A flag from this open never lists the cancel row on the next open."""
        state = _state(generating=False)
        store.start("sync")
        with patch.object(cps, "load_recent_prompts", return_value=[]):
            asyncio.run(state.on_open_change(True))
        assert state.has_running_job is True
        recents = state.recent_prompts
        asyncio.run(state.on_open_change(False))
        assert state.has_running_job is False
        assert state.recent_prompts is recents  # recents stay for the next open

    def test_running_flag_task_store_only(self, store: TaskStore) -> None:
        state = _state(generating=False)
        assert store.start("sync") is not None
        with patch.object(cps, "load_recent_prompts", return_value=[]):
            asyncio.run(state.on_open_change(True))
        assert state.has_running_job is True

    def test_running_flag_generation_only(self) -> None:
        state = _state(generating=True)
        with patch.object(cps, "load_recent_prompts", return_value=[]):
            asyncio.run(state.on_open_change(True))
        assert state.has_running_job is True
        state.get_state.assert_awaited_once_with(GeneratorState)  # type: ignore[attr-defined]

    def test_running_flag_idle(self) -> None:
        state = _state(generating=False)
        state.has_running_job = True  # stale from an earlier open
        with patch.object(cps, "load_recent_prompts", return_value=[]):
            asyncio.run(state.on_open_change(True))
        assert state.has_running_job is False

    def test_cancelled_job_no_longer_counts(self, store: TaskStore) -> None:
        state = _state(generating=False)
        store.start("tagging")
        store.cancel_all_running()
        with patch.object(cps, "load_recent_prompts", return_value=[]):
            asyncio.run(state.on_open_change(True))
        assert state.has_running_job is False


# ── 2.3: command handlers ────────────────────────────────────────────────


class TestUseRecentPrompt:
    def test_prefills_then_redirects_without_generating(self) -> None:
        state = _state()
        with (
            patch.object(GeneratorState, "prefill_prompt", create=True) as prefill,
            patch.object(GeneratorState, "submit_prompt", create=True) as submit,
            patch.object(GeneratorState, "generate_playlist", create=True) as generate,
            patch.object(cps.rx, "redirect") as redirect,
        ):
            events = state.use_recent_prompt("rainy day")
        prefill.assert_called_once_with("rainy day")
        redirect.assert_called_once_with("/generator")
        assert events == [prefill.return_value, redirect.return_value]
        submit.assert_not_called()
        generate.assert_not_called()


class TestGenerateFromQuery:
    @pytest.mark.parametrize("query", ["", "   ", "\n\t"])
    def test_blank_query_does_nothing(self, query: str) -> None:
        state = _state()
        with (
            patch.object(GeneratorState, "submit_prompt", create=True) as submit,
            patch.object(cps.rx, "redirect") as redirect,
        ):
            assert state.generate_from_query(query) is None
        submit.assert_not_called()
        redirect.assert_not_called()

    def test_redirects_then_submits_trimmed_query(self) -> None:
        state = _state()
        with (
            patch.object(GeneratorState, "submit_prompt", create=True) as submit,
            patch.object(cps.rx, "redirect") as redirect,
        ):
            events = state.generate_from_query("  jazz for cooking  ")
        redirect.assert_called_once_with("/generator")
        submit.assert_called_once_with({"prompt": "jazz for cooking"})
        assert events is not None
        assert events[:2] == [redirect.return_value, submit.return_value]
        # The prompt field (when it stays mounted) is synced after submit_prompt (D3).
        assert events[2] == ("set_value", PROMPT_INPUT_ID, "jazz for cooking")
        assert len(events) == 3

    def test_long_query_set_value_is_cut_to_the_prompt_limit(self) -> None:
        state = _state()
        long_query = "x" * 320
        with (
            patch.object(GeneratorState, "submit_prompt", create=True) as submit,
            patch.object(cps.rx, "redirect"),
        ):
            events = state.generate_from_query(long_query)
        assert events is not None
        # submit_prompt does its own cut; the client-side sync matches it.
        submit.assert_called_once_with({"prompt": long_query})
        assert events[2] == ("set_value", PROMPT_INPUT_ID, "x" * 280)


class TestRequestGlobalCancel:
    def _run(self, state: CommandPaletteState) -> tuple[Any, MagicMock, MagicMock]:
        with (
            patch.object(AppState, "request_cancel", create=True) as request_cancel,
            patch.object(cps.rx, "toast") as toast,
        ):
            result = asyncio.run(state.request_global_cancel())
        return result, request_cancel, toast

    def test_task_store_job_requests_the_global_confirm(self, store: TaskStore) -> None:
        state = _state(generating=False)
        store.start("sync")
        result, request_cancel, toast = self._run(state)
        request_cancel.assert_called_once_with("__all__")
        assert result is request_cancel.return_value
        toast.info.assert_not_called()
        assert state.has_running_job is True

    def test_generation_requests_the_global_confirm(self) -> None:
        state = _state(generating=True)
        result, request_cancel, toast = self._run(state)
        request_cancel.assert_called_once_with("__all__")
        assert result is request_cancel.return_value
        toast.info.assert_not_called()

    def test_idle_tells_the_user_and_asks_no_confirm(self) -> None:
        state = _state(generating=False)
        state.has_running_job = True  # listed when opened, finished since
        result, request_cancel, toast = self._run(state)
        request_cancel.assert_not_called()
        toast.info.assert_called_once_with("Nothing is running right now.")
        assert result is toast.info.return_value
        assert state.has_running_job is False

    def test_rechecks_at_activation(self, store: TaskStore) -> None:
        """The flag set on open is not trusted: the re-check decides."""
        state = _state(generating=False)
        state.has_running_job = False
        store.start("embeddings")
        _result, request_cancel, toast = self._run(state)
        request_cancel.assert_called_once_with("__all__")
        toast.info.assert_not_called()
        assert state.has_running_job is True
