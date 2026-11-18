"""webui-honest-status-and-first-run, group 9.2/9.3: Library selection honesty, bulk
tags (add by default, replace explicit), loading versus empty, sync failures and
the sync AI-tagging note (spec ``webui-library-selection``).

Reflex is mocked (tests/ui/conftest.py). Every test runs on a scratch
``PLEXMIX_DATA_DIR`` with its own config.yaml and a real SQLite library; the
keyring lookup is patched out, Plex and providers are fakes, and the readiness
and task stores are cleared around each test. Nothing touches the network.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pickle
import re
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Iterator, NamedTuple, Optional
from unittest.mock import MagicMock, patch

import pytest

from plexmix.services.readiness import readiness_store
from plexmix.ui.job_manager import task_store

SENTINEL_TOKEN = "plexTOKEN-SENTINEL-library-0001"
SENTINEL_OPENAI = "sk-SENTINEL-library-openai-0001"

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
_JOBS = ("sync", "embedding", "audio")


@pytest.fixture(autouse=True)
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Scratch data dir with a config.yaml, no keyring, no env config, clean stores."""
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text("{}\n")  # never fall back to ~/.plexmix/config.yaml
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(data))
    monkeypatch.setattr("plexmix.config.credentials._get_keyring", lambda: None)
    _clear_stores()
    yield data
    _clear_stores()


def _clear_stores() -> None:
    """Empty everything the server process keeps between requests."""
    from plexmix.ui.states import library_state as ls

    readiness_store.clear()
    for job in _JOBS:
        task_store.clear(job)
    ls.forget_held_removal()


def write_config(data: Path, body: str) -> None:
    (data / "config.yaml").write_text(body or "{}\n")


def make_library(
    data: Path,
    tracks: int,
    *,
    tags: Optional[dict[int, tuple[Optional[str], Optional[str], Optional[str]]]] = None,
    embedded: int = 0,
) -> list[int]:
    """A real SQLite library; ``tags`` maps a track index to (tags, environments, instruments)."""
    from plexmix.database.models import Album, Artist, Embedding, Track
    from plexmix.database.sqlite_manager import SQLiteManager

    with SQLiteManager(str(data / "plexmix.db")) as db:
        artist = db.insert_artist(Artist(plex_key="a1", name="Artist"))
        album = db.insert_album(Album(plex_key="al1", title="Album", artist_id=artist))
        ids = [
            db.insert_track(
                Track(plex_key=f"t{i}", title=f"Song {i:03d}", artist_id=artist, album_id=album)
            )
            for i in range(tracks)
        ]
        for index, (tag_text, envs, instruments) in (tags or {}).items():
            db.update_track_tags(
                ids[index], tags=tag_text, environments=envs, instruments=instruments
            )
        for tid in ids[:embedded]:
            db.insert_embedding(
                Embedding(
                    track_id=tid, embedding_model="openai", embedding_dim=2, vector=[0.1, 0.2]
                )
            )
    return ids


def stored_tags(data: Path, track_id: int) -> dict[str, Optional[str]]:
    from plexmix.database.sqlite_manager import SQLiteManager

    with SQLiteManager(str(data / "plexmix.db")) as db:
        return db.get_tags_for_tracks([track_id])[track_id]


def _harness(cls: Any) -> Any:
    """``cls`` plus what a background handler needs from rx.State."""

    class _Harness(cls):  # type: ignore[misc, valid-type]
        async def __aenter__(self) -> "_Harness":
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        def access_allowed(self) -> bool:
            return True

    return _Harness()


def library() -> Any:
    from plexmix.ui.states.library_state import LibraryState

    state = _harness(LibraryState)
    state.selected_tracks = []
    state.tracks = []
    return state


async def _drain(gen: Any) -> list[Any]:
    if hasattr(gen, "__aiter__"):
        return [event async for event in gen]
    await gen
    return []


def run(gen: Any) -> list[Any]:
    return asyncio.run(_drain(gen))


def toasts() -> MagicMock:
    toast = MagicMock()
    for kind in ("info", "error", "warning", "success"):
        getattr(toast, kind).side_effect = lambda msg, _k=kind: (_k, msg)
    return toast


# ── Pure helpers ─────────────────────────────────────────────────────────


class TestTagHelpers:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("chill,piano", ["chill", "piano"]),
            ("chill, piano", ["chill", "piano"]),
            (" chill ,, piano ,", ["chill", "piano"]),
            ("", []),
            (None, []),
            (", ,", []),
        ],
    )
    def test_parse_is_tolerant(self, text: Optional[str], expected: list[str]) -> None:
        from plexmix.ui.states.library_state import parse_tag_text

        assert parse_tag_text(text) == expected

    def test_merge_is_case_insensitive_and_keeps_order(self) -> None:
        from plexmix.ui.states.library_state import merge_tags

        assert merge_tags(["chill", "piano"], ["Piano", "focus"]) == ["chill", "piano", "focus"]
        assert merge_tags([], ["Focus", "focus", "FOCUS"]) == ["Focus"]
        assert merge_tags(["a", "A"], []) == ["a"]

    def test_merge_never_caps_the_count(self) -> None:
        """Adding a tag to a track with five tags keeps all six (set_tags_list caps at 5)."""
        from plexmix.ui.states.library_state import merge_tags

        five = ["a", "b", "c", "d", "e"]
        assert merge_tags(five, ["f"]) == [*five, "f"]

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("add", "add"),
            ("replace", "replace"),
            (" Replace ", "replace"),
            ("x", "add"),
            (None, "add"),
        ],
    )
    def test_unknown_mode_is_add(self, value: Any, expected: str) -> None:
        from plexmix.ui.states.library_state import bulk_tag_mode

        assert bulk_tag_mode(value) == expected

    @pytest.mark.parametrize(
        "selected,offpage,expected",
        [
            (12, 0, ""),
            (50, 50, "all on other pages"),
            (1, 1, "on another page"),
            (12, 3, "3 of them on other pages"),
            (12, 1, "1 of them on another page"),
            (2000, 1500, "1,500 of them on other pages"),
        ],
    )
    def test_offpage_phrase(self, selected: int, offpage: int, expected: str) -> None:
        from plexmix.ui.states.library_state import offpage_phrase

        assert offpage_phrase(selected, offpage) == expected


class TestApplyTagsToTracks:
    def test_add_merges_and_leaves_environments_untouched(self, data_dir: Path) -> None:
        """Spec: "chill, piano" + "Piano, focus" in the default mode → chill, piano, focus."""
        from plexmix.ui.states.library_state import apply_tags_to_tracks

        ids = make_library(data_dir, 2, tags={0: ("chill,piano", "night,rain", "piano")})
        updated, unchanged = apply_tags_to_tracks(ids, "Piano, focus", "add")

        assert (updated, unchanged) == (2, 0)
        first = stored_tags(data_dir, ids[0])
        assert first["tags"] == "chill,piano,focus"
        assert first["environments"] == "night,rain"
        assert first["instruments"] == "piano"
        second = stored_tags(data_dir, ids[1])
        assert second["tags"] == "Piano,focus"  # no tags before: the entered tags, each once
        assert second["environments"] is None and second["instruments"] is None

    def test_add_tolerates_spaced_storage_and_skips_unchanged(self, data_dir: Path) -> None:
        from plexmix.ui.states.library_state import apply_tags_to_tracks

        ids = make_library(
            data_dir, 2, tags={0: ("Chill, Focus", None, None), 1: ("a", None, None)}
        )
        updated, unchanged = apply_tags_to_tracks(ids, "focus,chill", "add")

        assert (updated, unchanged) == (1, 1)
        assert stored_tags(data_dir, ids[0])["tags"] == "Chill, Focus"  # not rewritten
        assert stored_tags(data_dir, ids[1])["tags"] == "a,focus,chill"

    def test_replace_overwrites_tags_only(self, data_dir: Path) -> None:
        from plexmix.ui.states.library_state import apply_tags_to_tracks

        ids = make_library(data_dir, 2, tags={0: ("chill,piano", "night", "guitar")})
        updated, unchanged = apply_tags_to_tracks(ids, "Piano, focus", "replace")

        assert (updated, unchanged) == (2, 0)
        first = stored_tags(data_dir, ids[0])
        assert first["tags"] == "Piano, focus"  # today's behaviour: the entered text
        assert (first["environments"], first["instruments"]) == ("night", "guitar")
        assert stored_tags(data_dir, ids[1])["tags"] == "Piano, focus"

    def test_missing_ids_and_blank_text_write_nothing(self, data_dir: Path) -> None:
        from plexmix.ui.states.library_state import apply_tags_to_tracks

        ids = make_library(data_dir, 1, tags={0: ("chill", None, None)})
        assert apply_tags_to_tracks([999_999], "focus", "add") == (0, 0)
        assert apply_tags_to_tracks(ids, " , ", "replace") == (0, 0)
        assert stored_tags(data_dir, ids[0])["tags"] == "chill"

    def test_count_tagged_tracks(self, data_dir: Path) -> None:
        from plexmix.ui.states.library_state import count_tagged_tracks

        ids = make_library(
            data_dir,
            4,
            tags={0: ("chill", None, None), 1: (" , ", None, None), 3: ("x", None, None)},
        )
        assert count_tagged_tracks(ids) == 2  # " , " parses to no tags
        assert count_tagged_tracks([]) == 0

    @pytest.mark.parametrize(
        "mode,updated,unchanged,expected",
        [
            ("add", 12, 0, "Added tags to 12 tracks."),
            ("add", 1, 2, "Added tags to 1 track · 2 tracks already had them."),
            ("add", 0, 3, "The selected tracks already have these tags."),
            ("replace", 12, 0, "Replaced the tags on 12 tracks."),
            ("add", 0, 0, ""),
        ],
    )
    def test_result_message(self, mode: str, updated: int, unchanged: int, expected: str) -> None:
        from plexmix.ui.states.library_state import bulk_tag_result_message

        assert bulk_tag_result_message(mode, updated, unchanged) == expected


# ── Bulk Tag dialog flow ─────────────────────────────────────────────────


class TestBulkTagFlow:
    def test_submit_add_returns_the_handler_in_its_default_mode(self) -> None:
        from plexmix.ui.states.library_state import LibraryState

        state = library()
        state.show_bulk_tag_dialog = True
        result = state.submit_bulk_tags({"tags": "chill, focus", "mode": "add"})
        assert result is LibraryState.apply_bulk_tags
        assert state.bulk_tag_mode == "add" and state.bulk_tag_error == ""

    def test_submit_forwards_replace_from_the_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plexmix.ui.states.library_state import LibraryState

        calls: list[str] = []
        monkeypatch.setattr(
            LibraryState, "apply_bulk_tags", lambda mode: calls.append(mode) or ("event", mode)
        )
        state = library()
        state.bulk_tag_mode = "add"  # the form's radios are what count
        assert state.submit_bulk_tags({"tags": "x", "mode": "replace"}) == ("event", "replace")
        assert calls == ["replace"] and state.bulk_tag_mode == "replace"

    def test_submit_without_mode_uses_the_dialog_choice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.states.library_state import LibraryState

        monkeypatch.setattr(LibraryState, "apply_bulk_tags", lambda mode: ("event", mode))
        state = library()
        state.bulk_tag_mode = "replace"
        assert state.submit_bulk_tags({"tags": "x"}) == ("event", "replace")
        state.bulk_tag_mode = "add"
        state.submit_bulk_tags({"tags": "x", "mode": "bogus"})
        assert state.bulk_tag_mode == "add"

    def test_blank_tags_keep_the_dialog_open_with_a_message(self) -> None:
        state = library()
        state.show_bulk_tag_dialog = True
        assert state.submit_bulk_tags({"tags": " , ", "mode": "replace"}) is None
        assert state.bulk_tag_error == "Enter at least one tag."
        assert state.show_bulk_tag_dialog is True

    def test_replace_waits_for_the_count(self) -> None:
        state = library()
        state.bulk_tag_counting = True
        assert state.bulk_tag_confirm_blocked is False  # Add is never blocked
        assert state.submit_bulk_tags({"tags": "x", "mode": "replace"}) is None
        assert state.bulk_tag_confirm_blocked is True
        assert "Still checking" in state.bulk_tag_error

    def test_open_counts_the_tracks_that_already_have_tags(self, data_dir: Path) -> None:
        """Spec: Replace with 12 selected, 9 tagged → "existing tags on 9 tracks will be replaced"."""
        from plexmix.ui.states.library_state import LibraryState

        ids = make_library(data_dir, 12, tags={i: (f"tag{i}", None, None) for i in range(9)})
        state = library()
        state.load_tracks()  # all 12 on this page
        state.selected_tracks = [str(t) for t in ids]
        assert state.open_bulk_tag_dialog() is LibraryState.count_selected_with_tags
        assert state.show_bulk_tag_dialog is True and state.bulk_tag_mode == "add"
        assert state.bulk_tag_counting is True
        assert state.bulk_tag_existing_line == "Checking which selected tracks already have tags…"

        run(state.count_selected_with_tags())

        assert state.selected_with_tags_count == 9 and state.bulk_tag_counting is False
        assert state.bulk_tag_existing_line == (
            "9 tracks already have tags. They are kept, and only new tags are added."
        )
        state.set_bulk_tag_mode("replace")
        assert state.bulk_tag_existing_line == "Existing tags on 9 tracks will be replaced."
        assert state.bulk_tag_scope_line == "Replaces the tags on 12 selected tracks."

    def test_a_stale_count_never_lands_in_a_reopened_dialog(self, data_dir: Path) -> None:
        ids = make_library(data_dir, 2, tags={0: ("a", None, None)})
        state = library()
        state.selected_tracks = [str(t) for t in ids]
        state.open_bulk_tag_dialog()
        pending = state.count_selected_with_tags()
        state.set_bulk_tag_dialog_open(False)
        run(pending)
        assert state.bulk_tag_counting is False and state.selected_with_tags_count == 0

    def test_open_with_nothing_selected_does_nothing(self) -> None:
        state = library()
        assert state.open_bulk_tag_dialog() is None
        assert state.show_bulk_tag_dialog is False

    def test_close_resets_and_clears_the_client_owned_field(self) -> None:
        state = library()
        state.show_bulk_tag_dialog = True
        state.bulk_tag_input = "chill"
        state.bulk_tag_mode = "replace"
        state.bulk_tag_error = "Enter at least one tag."
        assert state.set_bulk_tag_dialog_open(False) == ("set_value", "lib-bulk-tags", "")
        assert (state.bulk_tag_input, state.bulk_tag_mode, state.bulk_tag_error) == (
            "",
            "add",
            "",
        )
        assert state.set_bulk_tag_dialog_open(True) is None

    @pytest.mark.parametrize("failed_mode", ["add", "replace"])
    def test_count_failure_is_said_not_hidden(self, failed_mode: str) -> None:
        state = library()
        state.bulk_tag_mode = failed_mode
        state.bulk_tag_count_failed = True
        line = state.bulk_tag_existing_line
        assert line.startswith("Couldn't check")
        assert ("replaced" in line) is (failed_mode == "replace")

    @pytest.mark.parametrize("mode", ["add", "replace"])
    def test_apply_end_to_end(self, data_dir: Path, mode: str) -> None:
        from plexmix.ui.states import library_state as ls

        ids = make_library(data_dir, 2, tags={0: ("chill,piano", "night", "piano")})
        state = library()
        state.selected_tracks = [str(t) for t in ids]
        state.show_bulk_tag_dialog = True
        state.bulk_tag_input = "Piano, focus"
        state.bulk_tag_mode = mode
        with patch.object(ls.rx, "toast", toasts()):
            events = run(state.apply_bulk_tags(mode))

        assert events[0] == ("set_value", "lib-bulk-tags", "")
        expected = {
            "add": ("success", "Added tags to 2 tracks."),
            "replace": ("success", "Replaced the tags on 2 tracks."),
        }[mode]
        assert events[-1] == expected
        assert state.show_bulk_tag_dialog is False and state.selected_tracks == []
        assert state.bulk_tag_input == "" and state.bulk_tag_mode == "add"
        first = stored_tags(data_dir, ids[0])
        assert first["tags"] == ("chill,piano,focus" if mode == "add" else "Piano, focus")
        assert (first["environments"], first["instruments"]) == ("night", "piano")

    def test_apply_defaults_to_add(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        ids = make_library(data_dir, 1, tags={0: ("chill", None, None)})
        state = library()
        state.selected_tracks = [str(ids[0])]
        state.bulk_tag_input = "focus"
        with patch.object(ls.rx, "toast", toasts()):
            run(state.apply_bulk_tags())
        assert stored_tags(data_dir, ids[0])["tags"] == "chill,focus"

    def test_apply_writes_off_the_event_loop(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        ids = make_library(data_dir, 1)
        state = library()
        state.selected_tracks = [str(ids[0])]
        state.bulk_tag_input = "focus"
        seen: list[Any] = []

        async def go() -> None:
            loop = asyncio.get_running_loop()
            original = loop.run_in_executor

            def spy(executor: Any, func: Any, *args: Any) -> Any:
                seen.append(func)
                return original(executor, func, *args)

            loop.run_in_executor = spy  # type: ignore[method-assign]
            with patch.object(ls.rx, "toast", toasts()):
                await _drain(state.apply_bulk_tags("add"))

        asyncio.run(go())
        assert ls.apply_tags_to_tracks in seen


# ── Off-page selection ───────────────────────────────────────────────────


class TestOffPageSelection:
    def test_page_two_shows_the_page_one_selection(self, data_dir: Path) -> None:
        """Spec: select 50 on page 1, move to page 2 → "50 selected · 50 on other pages"."""
        make_library(data_dir, 120)
        state = library()
        state.load_tracks()
        assert len(state.tracks) == 50 and state.offpage_selected_count == 0

        state.toggle_select_all(True)
        assert state.selection_label == "50 selected"
        state.next_page()

        assert state.current_page == 2
        assert state.offpage_selected_count == 50
        assert state.selection_label == "50 selected · 50 on other pages"
        assert state.delete_scope_line == (
            "50 tracks will be deleted from PlexMix, all on other pages."
        )
        assert state.bulk_tag_scope_line == (
            "Adds these tags to 50 selected tracks, all on other pages."
        )

        state.toggle_track_selection(state.tracks[0]["id"])
        assert state.selection_label == "51 selected · 50 on other pages"
        assert state.delete_scope_line == (
            "51 tracks will be deleted from PlexMix, 50 of them on other pages."
        )

    def test_selection_on_this_page_only(self) -> None:
        state = library()
        state.tracks = [{"id": "1"}, {"id": "2"}]
        state.selected_tracks = ["1"]
        assert state.offpage_selected_count == 0
        assert state.selection_label == "1 selected"
        assert state.delete_scope_line == "1 track will be deleted from PlexMix."

    def test_one_track_on_another_page(self) -> None:
        state = library()
        state.tracks = [{"id": "1"}]
        state.selected_tracks = ["9"]
        assert state.selection_label == "1 selected · 1 on another page"
        assert state.delete_scope_line == ("1 track will be deleted from PlexMix, on another page.")

    def test_delete_runs_off_the_loop_and_reports_plainly(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        ids = make_library(data_dir, 3)
        state = library()
        state.selected_tracks = [str(ids[0]), str(ids[1])]
        state.show_delete_confirm = True
        with patch.object(ls.rx, "toast", toasts()):
            events = run(state.delete_selected_tracks())
        assert events == [("success", "Deleted 2 tracks from PlexMix.")]
        assert state.selected_tracks == [] and state.total_filtered_tracks == 1


# ── Loading versus empty ─────────────────────────────────────────────────


class TestTableView:
    def test_loading_until_the_first_query(self) -> None:
        state = library()
        assert state.tracks_loaded is False
        assert state.table_view == "loading"

    def test_no_database_is_not_synced(self, data_dir: Path) -> None:
        state = library()
        state.load_tracks()
        assert state.tracks_loaded is True and state.table_view == "not_synced"
        assert not (data_dir / "plexmix.db").exists()  # loading never creates the DB

    def test_empty_library_is_not_synced_even_with_filters(self, data_dir: Path) -> None:
        make_library(data_dir, 0)
        state = library()
        state.search_query = "anything"
        state.load_tracks()
        assert state.table_view == "not_synced"

    def test_filters_excluding_everything_is_no_match(self, data_dir: Path) -> None:
        make_library(data_dir, 3)
        state = library()
        state.load_tracks()
        assert state.table_view == "rows"
        state.set_tag_filter("no-such-tag")
        assert state.tracks == [] and state.table_view == "no_match"
        state.clear_filters()
        assert state.table_view == "rows"

    def test_a_failed_query_is_an_error_not_an_empty_library(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.database import sqlite_manager

        make_library(data_dir, 3)

        def broken(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("database disk image is malformed")

        monkeypatch.setattr(sqlite_manager.SQLiteManager, "get_tracks", broken)
        state = library()
        state.load_tracks()
        assert state.table_view == "error"
        assert state.tracks_error == "Couldn't load your tracks. Details are in the server log."
        monkeypatch.undo()
        monkeypatch.setenv("PLEXMIX_DATA_DIR", str(data_dir))
        state.retry_load_tracks()
        assert state.table_view == "rows" and state.tracks_error == ""

    def test_a_page_past_the_end_falls_back_to_the_last_page(self, data_dir: Path) -> None:
        make_library(data_dir, 60)
        state = library()
        state.current_page = 3  # e.g. after deleting the tracks of the last page
        state.load_tracks()
        assert state.current_page == 2 and len(state.tracks) == 10


# ── Sync: inline failure and AI-tagging note ─────────────────────────────


def _failed_sync(message: str, code: Optional[str] = None) -> None:
    task_store.start("sync", message="Starting sync...")
    if code:
        task_store.update("sync", extra={"plex_error_code": code})
    task_store.complete("sync", status="failed", message=message)


class TestSyncError:
    def test_plex_code_maps_to_readiness_copy(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, "plex:\n  url: http://plex.local:32400\n  library_name: Music\n")
        _failed_sync("Can't reach your Plex server", code="unreachable")
        state = library()
        state.is_syncing = True
        with patch.object(ls.rx, "toast", toasts()):
            event = state.poll_task_progress()

        assert event == ("error", "Sync failed: Can't reach your Plex server")  # toast stays
        assert state.sync_error.startswith("Can't reach your Plex server")
        assert "plex.local" in state.sync_error + state.sync_error_body
        assert state.sync_error_action_href == "/settings?section=plex"
        assert state.sync_error_action_label
        shown = " ".join([state.sync_error, state.sync_error_body, state.sync_error_action_label])
        assert "plexmix config" not in shown and "Run:" not in shown

    @pytest.mark.parametrize(
        "code,title",
        [
            ("not_configured", "Plex isn't connected yet"),
            ("no_library", "No music library chosen"),
            ("library_missing", "Music library not found"),
        ],
    )
    def test_each_code_has_its_title(self, code: str, title: str) -> None:
        from plexmix.ui.states.library_state import sync_error_copy

        copy = sync_error_copy(code, "ignored CLI text: Run 'plexmix config init'")
        assert copy["title"].startswith(title)
        assert "plexmix config" not in " ".join(copy.values())
        assert copy["action_href"].startswith("/settings?section=plex")

    def test_other_failures_keep_the_message(self) -> None:
        from plexmix.ui.states import library_state as ls

        _failed_sync("Invalid API key. Please check your key and try again.")
        state = library()
        state.is_syncing = True
        with patch.object(ls.rx, "toast", toasts()):
            state.poll_task_progress()
        assert state.sync_error == "Sync failed"
        assert state.sync_error_body == "Invalid API key. Please check your key and try again."
        assert state.sync_error_action_href == ""

    def test_success_and_dismiss_clear_it(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        state = library()
        state.sync_error = "Sync failed"
        state.sync_error_body = "x"
        state.dismiss_sync_error()
        assert (state.sync_error, state.sync_error_body) == ("", "")

        state.sync_error = "Sync failed"
        task_store.start("sync")
        task_store.complete("sync", message="Sync completed!")
        state.is_syncing = True
        with patch.object(ls.rx, "toast", toasts()), patch.object(
            state, "check_configuration_status", lambda: None
        ), patch.object(state, "load_library_stats", lambda: None):
            state.poll_task_progress()
        assert state.sync_error == ""

    def test_a_new_sync_clears_the_old_failure(self, data_dir: Path) -> None:
        from plexmix.services.sync_service import PlexConnectionError

        state = library()
        state.sync_error = "Old failure"

        def fail(settings: Any) -> Any:
            raise PlexConnectionError("Plex not configured.", code="not_configured")

        seen: list[str] = []
        original = task_store.start

        def spy(job: str, *a: Any, **k: Any) -> Any:
            seen.append(state.sync_error)
            return original(job, *a, **k)

        with patch("plexmix.services.sync_service.connect_plex", fail), patch.object(
            task_store, "start", spy
        ):
            run(state.start_sync())
        assert seen == [""]

    def test_unexpected_failure_text_never_carries_secrets(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        write_config(
            data_dir,
            "plex:\n  url: http://plex.local:32400\n  library_name: Music\n"
            "ai:\n  default_provider: openai\n  model: gpt-5-mini\n",
        )
        monkeypatch.setenv("PLEX_TOKEN", SENTINEL_TOKEN)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        engine = MagicMock()
        engine.incremental_sync.side_effect = RuntimeError(
            f"boom token={SENTINEL_TOKEN} key={SENTINEL_OPENAI}"
        )
        state = library()
        caplog.set_level(logging.DEBUG)
        with patch(
            "plexmix.services.sync_service.connect_plex", lambda s: SimpleNamespace()
        ), patch("plexmix.plex.sync.SyncEngine", return_value=engine), patch(
            "plexmix.services.providers.build_ai_provider", return_value=None
        ):
            run(state.start_sync())
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "failed"
        for secret in (SENTINEL_TOKEN, SENTINEL_OPENAI):
            assert secret not in entry.message
            assert secret not in caplog.text
        assert "boom" in entry.message


def _ai(level: str, provider: str, label: str, blocking: str = "false") -> dict[str, Any]:
    return {"ai": {"level": level, "provider": provider, "label": label, "blocking": blocking}}


class TestSyncTaggingNote:
    @pytest.mark.parametrize(
        "readiness,expected",
        [
            (
                _ai("unchecked", "openai", "OpenAI"),
                "Sync also tags new tracks with OpenAI · track metadata is sent to OpenAI",
            ),
            (
                _ai("ready", "claude", "Claude"),
                "Sync also tags new tracks with Claude · track metadata is sent to Claude",
            ),
            (_ai("ready", "local", "Local"), "Sync also tags new tracks with Local"),
            (_ai("not_set_up", "openai", "OpenAI"), ""),
            (_ai("not_set_up", "local", "Local", blocking="true"), ""),
            (_ai("failing", "local", "Local", blocking="true"), ""),
            ({}, ""),
        ],
    )
    def test_note(self, readiness: dict[str, Any], expected: str) -> None:
        from plexmix.ui.states.library_state import sync_tagging_note

        assert sync_tagging_note(readiness) == expected

    def test_state_reads_app_readiness(self) -> None:
        state = library()
        state.readiness = _ai("unchecked", "openai", "OpenAI")
        assert state.sync_tagging_note.startswith("Sync also tags new tracks with OpenAI")

    def test_same_words_as_the_setup_checklist(self) -> None:
        from plexmix.services.readiness import setup_steps
        from plexmix.ui.states.library_state import sync_tagging_note

        readiness = _ai("unchecked", "openai", "OpenAI")
        step = next(s for s in setup_steps(readiness) if s["id"] == "sync")
        assert step["note"] == sync_tagging_note(readiness)

    def test_a_remote_local_endpoint_is_named(self) -> None:
        """Local in endpoint mode can be another machine: say where metadata goes."""
        from plexmix.ui.states.library_state import sync_tagging_note

        readiness = _ai("unchecked", "local", "Local")
        readiness["ai"]["sends_to"] = "192.168.1.20"
        assert sync_tagging_note(readiness) == (
            "Sync also tags new tracks with Local · track metadata is sent to 192.168.1.20"
        )
        readiness["ai"]["sends_to"] = ""
        assert sync_tagging_note(readiness) == "Sync also tags new tracks with Local"


class TestSyncTaggingVerifiesAi:
    """A sync's AI tagging is a real use of the AI provider (readiness D2)."""

    def _settings(self, data_dir: Path) -> Any:
        from plexmix.config.settings import Settings

        write_config(data_dir, "ai:\n  default_provider: openai\n")
        return Settings.load_from_file()

    def test_a_rejected_key_marks_ai_failing(self, data_dir: Path) -> None:
        from plexmix.ai.errors import FatalProviderError
        from plexmix.services import readiness as rd
        from plexmix.ui.states.library_state import record_sync_tagging

        settings = self._settings(data_dir)
        engine = SimpleNamespace(
            tags_saved=0,
            tagging_error=FatalProviderError(
                "401",
                user_message=(
                    "The AI provider rejected your credentials (authentication failed). "
                    "Re-check your API key in Settings."
                ),
            ),
        )
        record_sync_tagging(engine, settings)
        rec = readiness_store.get("ai")
        assert rec is not None and rec.level == rd.Level.FAILING
        assert rec.cause == rd.Cause.INVALID_CREDENTIALS
        assert rec.fingerprint == rd.current_fingerprint("ai", settings)

    def test_tagged_tracks_mark_ai_ready(self, data_dir: Path) -> None:
        from plexmix.services import readiness as rd
        from plexmix.ui.states.library_state import record_sync_tagging

        settings = self._settings(data_dir)
        record_sync_tagging(SimpleNamespace(tags_saved=4, tagging_error=None), settings)
        rec = readiness_store.get("ai")
        assert rec is not None and rec.level == rd.Level.READY

    def test_nothing_to_tag_records_nothing(self, data_dir: Path) -> None:
        from plexmix.ui.states.library_state import record_sync_tagging

        settings = self._settings(data_dir)
        record_sync_tagging(SimpleNamespace(tags_saved=0, tagging_error=None), settings)
        record_sync_tagging(object(), settings)  # an engine without the attributes
        assert readiness_store.get("ai") is None


# ── Sync outcome: unread tracks, held removals, the confirmed removal ────
#
# Spec ``webui-library-selection`` ("Sync holds large removals for confirmation")
# and the web UI scenarios of ``library-sync-safety``. The real SyncEngine runs
# against a fake Plex client on the scratch library, so no request is made; only
# the tests that say so pass a mock engine.

PLEX_CONFIG = "plex:\n  url: http://127.0.0.1:9\n  library_name: Music\n"
HELD_MESSAGE = "Plex no longer lists 100 of 120 tracks. They were kept."
TAGGING_SKIPPED = "Sync completed (tagging skipped)"


def _plex_track(index: int) -> Any:
    """The track ``make_library`` stored at ``index``, as the Plex client hands it out."""
    from plexmix.database.models import Track

    track = Track(plex_key=f"t{index}", title=f"Song {index:03d}", artist_id=0, album_id=0)
    track.__dict__["_artist_key"] = "a1"
    track.__dict__["_album_key"] = "al1"
    return track


class FakePlex:
    """Stands in for ``PlexClient``; no request is made.

    ``read`` are the track indexes it hands out and ``listed`` the indexes its
    listing held (the same unless given). ``failed_tracks`` of the listed tracks
    could not be read. ``read_error`` is raised when the tracks are read.
    ``relisted`` is what ``list_track_keys`` answers at confirmation time (track
    indexes), or the error it raises.
    """

    def __init__(
        self,
        read: Iterable[int],
        *,
        listed: Optional[Iterable[int]] = None,
        failed_tracks: int = 0,
        read_error: Optional[BaseException] = None,
        relisted: Any = None,
    ) -> None:
        from plexmix.plex.client import PlexReadReport

        self.read = list(read)
        keys = frozenset(f"t{i}" for i in (self.read if listed is None else listed))
        self.read_report = PlexReadReport(
            listed_track_keys=keys,
            listed={"artists": 1, "albums": 1, "tracks": len(keys)},
            failed={"artists": 0, "albums": 0, "tracks": failed_tracks},
        )
        self.read_error = read_error
        self.relisted = relisted
        self.listings = 0

    def validate_token(self) -> tuple[bool, str]:
        return True, "Connected"

    def get_all_artists(self, batch_size: int = 100) -> Iterator[list[Any]]:
        from plexmix.database.models import Artist

        yield [Artist(plex_key="a1", name="Artist")]

    def get_all_albums(self, batch_size: int = 100) -> Iterator[list[Any]]:
        from plexmix.database.models import Album

        album = Album(plex_key="al1", title="Album", artist_id=0)
        album.__dict__["_artist_key"] = "a1"
        yield [album]

    def get_all_tracks(self, batch_size: int = 100) -> Iterator[list[Any]]:
        if self.read_error is not None:
            raise self.read_error
        if self.read:
            yield [_plex_track(i) for i in self.read]

    def list_track_keys(self) -> Any:
        self.listings += 1
        if isinstance(self.relisted, BaseException):
            raise self.relisted
        return frozenset(f"t{i}" for i in self.relisted)


def run_sync(state: Any, plex: Any) -> Any:
    """Run ``start_sync`` against ``plex`` with no AI provider; return the task entry."""
    with patch("plexmix.services.sync_service.connect_plex", lambda settings: plex), patch(
        "plexmix.services.providers.build_ai_provider", return_value=None
    ):
        run(state.start_sync())
    entry = task_store.get("sync")
    assert entry is not None
    return entry


def poll(state: Any) -> Any:
    """One ``poll_task_progress`` with inspectable toasts and no readiness refresh."""
    from plexmix.ui.states import library_state as ls

    with patch.object(ls.rx, "toast", toasts()), patch.object(
        state, "check_configuration_status", lambda: None
    ), patch.object(state, "load_library_stats", lambda: None):
        return state.poll_task_progress()


def held_sync(held: int = 100, total: int = 120, message: str = HELD_MESSAGE) -> None:
    """A finished sync that kept ``held`` of ``total`` tracks for confirmation."""
    from plexmix.ui.states import library_state as ls

    task_store.start("sync", message="Starting sync...")
    task_store.update("sync", extra=ls.held_removal_extra(held, total))
    task_store.complete("sync", message=message)


def restart_server() -> None:
    """A server restart: the task store is empty and no hold is remembered."""
    from plexmix.ui.states import library_state as ls

    task_store.clear("sync")
    ls.forget_held_removal()


def forget_finished_sync() -> None:
    """Let the task store drop the finished "sync" entry, as it does after an hour."""
    from plexmix.ui import job_manager

    entry = task_store.get("sync")
    assert entry is not None and entry.status != "running"
    entry.updated_at -= job_manager._COMPLETED_TTL + 1
    assert task_store.get("sync") is None


def with_notice(held: int = 100, total: int = 120) -> Any:
    """A Library tab that shows the notice of :func:`held_sync` (it did not start the sync)."""
    held_sync(held, total)
    state = library()
    assert poll(state) is None
    assert (state.removal_notice_count, state.removal_notice_total) == (held, total)
    return state


class Confirmed(NamedTuple):
    events: list[Any]
    executor_calls: list[Any]
    stats_reloads: int


def confirm(state: Any, plex: Any = None, *, connect: Any = None) -> Confirmed:
    """Run ``confirm_removal`` against ``plex`` (or the ``connect`` function)."""
    from plexmix.ui.states import library_state as ls

    connect = connect or (lambda settings: plex)
    seen: list[Any] = []
    reloads: list[int] = []

    async def go() -> list[Any]:
        loop = asyncio.get_running_loop()
        original = loop.run_in_executor

        def spy(executor: Any, func: Any, *args: Any) -> Any:
            seen.append(func)
            return original(executor, func, *args)

        loop.run_in_executor = spy  # type: ignore[method-assign]
        return await _drain(state.confirm_removal())

    with patch.object(ls.rx, "toast", toasts()), patch(
        "plexmix.services.sync_service.connect_plex", connect
    ), patch.object(state, "load_library_stats", lambda: reloads.append(1)):
        events = asyncio.run(go())
    return Confirmed(events, seen, len(reloads))


def track_count(data: Path) -> int:
    from plexmix.database.sqlite_manager import SQLiteManager

    with SQLiteManager(str(data / "plexmix.db")) as db:
        return len(db.get_all_tracks())


def track_keys(data: Path) -> list[str]:
    from plexmix.database.sqlite_manager import SQLiteManager

    with SQLiteManager(str(data / "plexmix.db")) as db:
        return sorted(track.plex_key for track in db.get_all_tracks())


def query(data: Path, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(str(data / "plexmix.db"))
    try:
        return list(connection.execute(sql).fetchall())
    finally:
        connection.close()


def backup_folders(data: Path) -> list[Path]:
    backups = data / "backups"
    return sorted(backups.iterdir()) if backups.is_dir() else []


def tracks_in_copy(database_file: Path) -> int:
    """Count the tracks in a backup copy without writing anything beside it."""
    connection = sqlite3.connect(f"{database_file.as_uri()}?immutable=1", uri=True)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])
    finally:
        connection.close()


class TestSyncOutcome:
    def test_reason_literals_match_the_engine(self) -> None:
        """The poll uses literals so it never imports the engine; keep them equal."""
        from plexmix.plex import sync as engine
        from plexmix.ui.states import library_state as ls

        assert ls.REASON_HELD == engine.REASON_THRESHOLD
        assert ls.REASON_EMPTY_LISTING == engine.REASON_EMPTY_LISTING
        assert ls.REMOVAL_LISTED_AGAIN == engine.REMOVAL_LISTED_AGAIN

    def test_sync_with_held_removals_completes_with_warning_and_notice(
        self, data_dir: Path
    ) -> None:
        """Spec "Notice after the sync": every track is kept and the result is a warning."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = library()
        entry = run_sync(state, FakePlex(range(20)))  # Plex lists 20 of the 120

        assert entry.status == "completed" and entry.message == HELD_MESSAGE
        assert entry.extra["removal_reason"] == "threshold"
        assert (entry.extra["removal_held"], entry.extra["removal_total"]) == (100, 120)
        assert entry.extra["unread_tracks"] == 0
        assert track_count(data_dir) == 120

        assert poll(state) == ("warning", HELD_MESSAGE)  # never the success toast
        assert state.is_syncing is False
        assert (state.removal_notice_count, state.removal_notice_total) == (100, 120)
        assert state.removal_notice_line == "Plex no longer lists 100 of 120 tracks"
        assert poll(state) is None  # reported once; the notice stays
        assert state.removal_notice_count == 100

    def test_sync_below_threshold_shows_no_notice(self, data_dir: Path) -> None:
        """Spec "Removal below the threshold": the sync removes the tracks itself."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = library()
        entry = run_sync(state, FakePlex(range(110)))  # 10 of 120 are gone from Plex

        assert entry.status == "completed"
        assert (entry.extra["removal_reason"], entry.extra["removal_held"]) == ("", 0)
        assert track_count(data_dir) == 110
        assert poll(state) == ("success", TAGGING_SKIPPED)
        assert state.removal_notice_count == 0
        state.open_removal_confirm()
        assert state.show_removal_confirm is False

    def test_sync_with_empty_listing_warns_and_offers_no_removal(self, data_dir: Path) -> None:
        """Spec "Empty listing in the web UI": a warning, nothing removed, no removal offered."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 60)
        state = library()
        entry = run_sync(state, FakePlex([]))

        expected = "Plex listed 0 tracks, but PlexMix holds 60 tracks. Nothing was removed."
        assert entry.status == "completed" and entry.message == expected
        assert "plexmix " not in entry.message  # no terminal command in the UI
        assert entry.extra["removal_reason"] == "empty_listing"
        assert entry.extra["removal_held"] == 0
        assert track_count(data_dir) == 60
        assert poll(state) == ("warning", expected)
        assert state.removal_notice_count == 0
        state.open_removal_confirm()
        assert state.show_removal_confirm is False
        assert confirm(state, FakePlex([], relisted=[])).events == []
        assert track_count(data_dir) == 60

    def test_sync_with_unread_tracks_says_how_many_were_kept(self, data_dir: Path) -> None:
        """Spec "A few reads fail in a web UI sync": the result gives the count."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 30)
        state = library()
        plex = FakePlex(range(27), listed=range(30), failed_tracks=3)
        entry = run_sync(state, plex)

        expected = f"{TAGGING_SKIPPED}. 3 tracks could not be read and were kept."
        assert entry.status == "completed" and entry.message == expected
        assert entry.extra["unread_tracks"] == 3 and entry.extra["removal_reason"] == ""
        assert track_count(data_dir) == 30  # listed but unread tracks are kept
        assert poll(state) == ("success", expected)
        assert state.removal_notice_count == 0

    def test_sync_with_too_many_unread_warns(self, data_dir: Path) -> None:
        """More than 10 and more than 1 percent of the reads failed: nothing is removed."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 65)
        state = library()
        # Plex lists 60 of the 65 tracks; 12 of those 60 could not be read.
        plex = FakePlex(range(48), listed=range(60), failed_tracks=12)
        entry = run_sync(state, plex)

        assert entry.status == "completed"
        assert entry.extra["removal_reason"] == "too_many_unread"
        assert "12 of 60 tracks could not be read" in entry.message
        assert entry.message.endswith("Nothing was removed.")
        assert "plexmix " not in entry.message
        assert track_count(data_dir) == 65  # the 5 unlisted tracks stay too
        assert poll(state) == ("warning", entry.message)
        assert state.removal_notice_count == 0  # no removal is offered

    def test_mock_engine_outcome_is_ignored(self, data_dir: Path) -> None:
        """A stand-in engine answers every attribute; its "outcome" never reaches the page."""
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, PLEX_CONFIG)
        engine = MagicMock()
        assert engine.outcome.held  # truthy, and not a SyncOutcome
        state = library()
        with patch(
            "plexmix.services.sync_service.connect_plex", lambda s: SimpleNamespace()
        ), patch("plexmix.plex.sync.SyncEngine", return_value=engine), patch(
            "plexmix.services.providers.build_ai_provider", return_value=None
        ):
            run(state.start_sync())
        entry = task_store.get("sync")

        assert entry is not None and entry.status == "completed"
        assert entry.message == TAGGING_SKIPPED
        assert not [key for key in entry.extra if key.startswith(("removal_", "unread_"))]
        assert poll(state) == ("success", TAGGING_SKIPPED)
        assert state.removal_notice_count == 0
        assert ls.sync_outcome_extra(engine.outcome) == {}
        assert ls.sync_result_message(engine.outcome) == ""

    def test_notice_is_shown_from_the_task_entry_after_reload(self, data_dir: Path) -> None:
        """Spec "Notice survives a reload": it follows the task entry, in any tab."""
        make_library(data_dir, 120)
        held_sync(100, 120)

        reloaded = library()  # a fresh page: it never saw the sync run
        reloaded.check_auth = lambda: True
        reloaded.load_app_data = lambda: None
        with patch.object(reloaded, "check_configuration_status", lambda: None), patch.object(
            reloaded, "load_library_stats", lambda: None
        ):
            reloaded.on_load()
        assert (reloaded.removal_notice_count, reloaded.removal_notice_total) == (100, 120)
        assert reloaded.is_syncing is False

        other_tab = library()
        assert poll(other_tab) is None  # no toast for a sync this tab did not watch
        assert (other_tab.removal_notice_count, other_tab.removal_notice_total) == (100, 120)

        # The next sync replaces the task entry, and the notice ends with it.
        task_store.start("sync", message="Starting sync...")
        poll(other_tab)
        assert other_tab.removal_notice_count == 0 and other_tab.is_syncing is True
        restart_server()  # nothing is held any more
        poll(reloaded)
        assert reloaded.removal_notice_count == 0

    def test_a_new_sync_hides_the_notice_and_closes_its_dialog(self, data_dir: Path) -> None:
        from plexmix.services.sync_service import PlexConnectionError

        state = with_notice()
        state.open_removal_confirm()

        def fail(settings: Any) -> Any:
            raise PlexConnectionError("Plex not configured.", code="not_configured")

        with patch("plexmix.services.sync_service.connect_plex", fail):
            run(state.start_sync())
        assert state.removal_notice_count == 0 and state.show_removal_confirm is False

    def test_sync_read_interrupted_fails_with_sentence_and_marks_plex_failing(
        self, data_dir: Path
    ) -> None:
        """Spec "Sync started from the web UI": failed with the sentence, nothing changed."""
        from plexmix.plex.client import PlexReadInterrupted
        from plexmix.services import readiness as rd

        write_config(data_dir, PLEX_CONFIG)
        ids = make_library(data_dir, 120, tags={0: ("chill", None, None)})
        state = library()
        plex = FakePlex(range(20), read_error=PlexReadInterrupted("tracks", 522, 5943))
        entry = run_sync(state, plex)

        sentence = (
            "Plex stopped answering after 522 of 5,943 tracks were read, " "so nothing was changed."
        )
        assert entry.status == "failed" and entry.message == sentence
        assert entry.extra["readiness_cause"] == "unreachable"
        assert "plex_error_code" not in entry.extra  # the sentence stays the inline body
        rec = readiness_store.get("plex")
        assert rec is not None
        assert (rec.level, rec.cause) == (rd.Level.FAILING, rd.Cause.UNREACHABLE)
        assert track_count(data_dir) == 120
        assert stored_tags(data_dir, ids[0])["tags"] == "chill"

        assert poll(state) == ("error", f"Sync failed: {sentence}")
        assert (state.sync_error, state.sync_error_body) == ("Sync failed", sentence)
        assert state.removal_notice_count == 0

    def test_another_plex_fetch_error_fails_with_its_sentence(self, data_dir: Path) -> None:
        """A fetch that was impossible is a plain failure; Plex itself answered."""
        from plexmix.plex.client import PlexFetchError
        from plexmix.services import readiness as rd

        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 3)
        state = library()
        entry = run_sync(
            state, FakePlex([], read_error=PlexFetchError("No music library selected"))
        )

        assert entry.status == "failed" and entry.message == "No music library selected"
        rec = readiness_store.get("plex")
        assert rec is not None and rec.level == rd.Level.READY  # from the connect
        assert track_count(data_dir) == 3

    def test_a_fetch_error_text_never_shows_url_credentials(
        self, data_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from plexmix.plex.client import PlexFetchError

        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 3)
        state = library()
        error = PlexFetchError("failed at http://user:dummy-ui-password-0000@127.0.0.1:9/x")
        caplog.set_level(logging.DEBUG)
        entry = run_sync(state, FakePlex([], read_error=error))

        assert entry.status == "failed" and "127.0.0.1:9" in entry.message
        assert "dummy-ui-password-0000" not in entry.message
        assert "dummy-ui-password-0000" not in caplog.text

    def test_regenerate_sync_that_plex_stops_says_what_was_cleared(self, data_dir: Path) -> None:
        """A stopped "Regenerate" sync must not let "nothing was changed" stand alone.

        That mode clears the tags and embeddings before it reads Plex. The sentence
        and the database have to agree: when the engine stops clearing first, the
        added sentence has to go.
        """
        from plexmix.plex.client import PlexReadInterrupted
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, PLEX_CONFIG)
        ids = make_library(data_dir, 6, tags={0: ("chill", None, None)}, embedded=6)
        state = library()
        state.sync_mode = "regenerate"
        plex = FakePlex(range(6), read_error=PlexReadInterrupted("tracks", 3, 6))
        entry = run_sync(state, plex)

        sentence = "Plex stopped answering after 3 of 6 tracks were read, so nothing was changed."
        assert entry.status == "failed" and entry.message.startswith(sentence)
        cleared = stored_tags(data_dir, ids[0])["tags"] is None
        embeddings_left = query(data_dir, "SELECT COUNT(*) FROM embeddings")[0][0]
        assert cleared == (embeddings_left == 0)
        assert (ls.REGENERATE_CLEARED_MESSAGE in entry.message) == cleared
        assert "plexmix " not in entry.message  # no terminal command in the UI
        # The toast and the inline failure carry the same text.
        assert poll(state) == ("error", f"Sync failed: {entry.message}")
        assert state.sync_error_body == entry.message
        # And so does the history row.
        status, text = query(
            data_dir, "SELECT status, error_message FROM sync_history ORDER BY id DESC LIMIT 1"
        )[0]
        assert status == "failed" and text.startswith(sentence)
        assert ("had already been cleared" in text) == cleared

    def test_notice_and_removal_outlive_the_task_entry(self, data_dir: Path) -> None:
        """Spec "Notice survives a reload": with no new sync and no restart it stays.

        The task store drops a finished entry after an hour. The hold does not go
        with it, and the removal can still be reviewed and confirmed.
        """
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        plex = FakePlex(range(20), relisted=range(20))
        state = library()
        run_sync(state, plex)  # holds 100 of 120
        poll(state)
        forget_finished_sync()

        reloaded = library()
        assert poll(reloaded) is None
        assert (reloaded.removal_notice_count, reloaded.removal_notice_total) == (100, 120)
        reloaded.open_removal_confirm()
        assert reloaded.show_removal_confirm is True
        done = confirm(reloaded, plex)
        assert done.events[0][0] == "success" and track_count(data_dir) == 20
        assert reloaded.removal_notice_count == 0

        # Once the removal is settled, nothing is remembered.
        forget_finished_sync()
        later = library()
        poll(later)
        assert later.removal_notice_count == 0

    def test_a_new_sync_ends_a_hold_that_the_task_store_forgot(self, data_dir: Path) -> None:
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = library()
        run_sync(state, FakePlex(range(20)))  # holds 100 of 120
        forget_finished_sync()
        run_sync(state, FakePlex(range(120)))  # Plex lists every track again
        forget_finished_sync()

        later = library()
        poll(later)
        assert later.removal_notice_count == 0
        later.open_removal_confirm()
        assert later.show_removal_confirm is False

    def test_removed_literal_matches_the_engine(self) -> None:
        from plexmix.plex import sync as engine
        from plexmix.ui.states import library_state as ls

        assert ls.REMOVAL_REMOVED == engine.REMOVAL_REMOVED

    def test_poll_gives_the_notice_only_to_a_caller_with_access(self, data_dir: Path) -> None:
        """The poll is a public handler: the held count is not for a caller who is not signed in."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        owner = library()
        run_sync(owner, FakePlex(range(20)))  # holds 100 of 120
        poll(owner)
        assert (owner.removal_notice_count, owner.removal_notice_total) == (100, 120)

        stranger = library()
        stranger.access_allowed = lambda: False  # not signed in
        poll(stranger)
        assert (stranger.removal_notice_count, stranger.removal_notice_total) == (0, 0)
        assert stranger.removal_notice_line == "Plex no longer lists 0 of 0 tracks"

        # The same once the task store has dropped the entry and the module answers.
        forget_finished_sync()
        poll(stranger)
        assert (stranger.removal_notice_count, stranger.removal_notice_total) == (0, 0)
        stranger.show_removal_confirm = True
        assert confirm(stranger, FakePlex([], relisted=range(20))).events == []
        assert track_count(data_dir) == 120

        # A signed-in tab still gets it.
        poll(owner)
        assert (owner.removal_notice_count, owner.removal_notice_total) == (100, 120)

    def test_poll_checks_access_only_while_a_removal_waits(self, data_dir: Path) -> None:
        """The access check reads the settings file, so the 2-second poll skips it when idle."""
        state = library()
        checks: list[int] = []
        state.access_allowed = lambda: checks.append(1) is None

        poll(state)
        assert checks == []

        held_sync(100, 120)
        poll(state)
        assert checks == [1] and state.removal_notice_count == 100

    @pytest.mark.parametrize(
        "unread, partial",
        [
            ({"artists": 0, "albums": 0, "tracks": 0}, False),
            ({"artists": 1, "albums": 0, "tracks": 0}, True),
            ({"artists": 0, "albums": 2, "tracks": 0}, True),
            ({"artists": 0, "albums": 0, "tracks": 3}, True),
        ],
    )
    def test_sync_extra_says_whether_anything_was_kept_back(
        self, unread: dict[str, int], partial: bool
    ) -> None:
        """An unread artist or album counts too: the Dashboard reads this flag."""
        from plexmix.plex.sync import SyncOutcome
        from plexmix.ui.states import library_state as ls

        extra = ls.sync_outcome_extra(SyncOutcome(unread=unread))

        assert extra[ls.SYNC_PARTIAL_KEY] is partial
        assert (
            ls.sync_outcome_extra(SyncOutcome(reason="threshold", held=3))[ls.SYNC_PARTIAL_KEY]
            is True
        )

    @pytest.mark.parametrize(
        "outcome_kwargs,tagging_skipped,expected",
        [
            ({}, False, ""),
            ({}, True, TAGGING_SKIPPED),
            (
                {"unread": {"artists": 0, "albums": 0, "tracks": 3}},
                False,
                "Sync completed. 3 tracks could not be read and were kept.",
            ),
            (
                {"unread": {"artists": 1, "albums": 0, "tracks": 1}},
                False,
                "Sync completed. 1 artist could not be read. Tracks keep the artist PlexMix "
                "already has for them. A new track whose artist is also new to PlexMix is "
                "filed under Unknown Artist until a later sync reads that artist. "
                "1 track could not be read and was kept.",
            ),
            (
                {"reason": "threshold", "held": 5443, "library_total": 5943},
                True,
                "Plex no longer lists 5,443 of 5,943 tracks. They were kept.",
            ),
            (
                {
                    "reason": "threshold",
                    "held": 300,
                    "library_total": 400,
                    "unread": {"artists": 0, "albums": 0, "tracks": 2},
                },
                False,
                "Plex no longer lists 300 of 400 tracks. They were kept. "
                "2 tracks could not be read and were kept.",
            ),
            (
                {"reason": "empty_listing", "library_total": 5943, "message": "run 'plexmix x'"},
                False,
                "Plex listed 0 tracks, but PlexMix holds 5,943 tracks. Nothing was removed.",
            ),
            (
                {"reason": "listing_unknown", "message": "Plex's track listing was not available."},
                False,
                "Plex's track listing was not available.",
            ),
        ],
    )
    def test_result_message(
        self, outcome_kwargs: dict[str, Any], tagging_skipped: bool, expected: str
    ) -> None:
        from plexmix.plex.sync import SyncOutcome
        from plexmix.ui.states.library_state import sync_result_message

        outcome = SyncOutcome(**outcome_kwargs)
        assert sync_result_message(outcome, tagging_skipped=tagging_skipped) == expected

    @pytest.mark.parametrize(
        "status,extra,expected",
        [
            ("completed", {"removal_reason": "threshold", "removal_held": 300}, (300, 300)),
            (
                "failed",
                {"removal_reason": "threshold", "removal_held": 300, "removal_total": 400},
                (300, 400),
            ),
            ("running", {"removal_reason": "threshold", "removal_held": 300}, (0, 0)),
            ("completed", {"removal_reason": "empty_listing", "removal_held": 300}, (0, 0)),
            ("completed", {"removal_reason": "too_many_unread", "removal_held": 300}, (0, 0)),
            ("completed", {"removal_reason": "threshold", "removal_held": 0}, (0, 0)),
            ("completed", {"removal_reason": "threshold", "removal_held": True}, (0, 0)),
            ("completed", {"removal_reason": "threshold", "removal_held": "300"}, (0, 0)),
            ("completed", {"removal_reason": "threshold", "removal_held": -5}, (0, 0)),
            ("completed", {}, (0, 0)),
        ],
    )
    def test_only_a_held_removal_gives_a_notice(
        self, status: str, extra: dict[str, Any], expected: tuple[int, int]
    ) -> None:
        from plexmix.ui.states.library_state import held_removal

        assert held_removal(SimpleNamespace(status=status, extra=extra)) == expected
        assert held_removal(None) == (0, 0)
        assert held_removal(SimpleNamespace(status="completed", extra=MagicMock())) == (0, 0)


class TestConfirmRemoval:
    """The "Review removal" dialog and the removal it confirms (design D9)."""

    def test_confirm_removal_runs_the_engine_removal(self, data_dir: Path) -> None:
        """Spec "Confirmed": Plex is listed again, a backup is made, the tracks are removed."""
        from plexmix.database.vector_index import VectorIndex
        from plexmix.plex.sync import SyncEngine
        from plexmix.ui.states import library_state as ls

        write_config(
            data_dir,
            PLEX_CONFIG + "embedding:\n  default_provider: custom\n  custom_dimension: 2\n",
        )
        ids = make_library(data_dir, 120, embedded=120)
        index_file = data_dir / "embeddings.index"
        index = VectorIndex(dimension=2, index_path=str(index_file))
        index.build_index([[1.0, float(i)] for i in range(120)], ids)
        index.save_index(str(index_file))

        plex = FakePlex(range(20), relisted=range(20))
        state = library()
        run_sync(state, plex)  # holds 100 of 120
        poll(state)
        # Selected before the removal: one track that stays and two that go.
        state.selected_tracks = [str(ids[0]), str(ids[50]), str(ids[119])]
        state.open_removal_confirm()
        assert state.show_removal_confirm is True
        assert state.removal_scope_line == (
            "100 of your 120 tracks will be removed from PlexMix, "
            "because Plex no longer lists them."
        )

        with patch.object(
            SyncEngine,
            "remove_unlisted_tracks",
            autospec=True,
            side_effect=SyncEngine.remove_unlisted_tracks,
        ) as removal:
            done = confirm(state, plex)

        removal.assert_called_once()
        assert removal.call_args.args[1] == 100  # the count the dialog stated
        assert plex.listings == 1  # Plex was checked again
        # The blocking work ran off the event loop.
        assert ls._connect_plex_for_sync in done.executor_calls
        assert ls.run_confirmed_removal in done.executor_calls

        assert len(done.events) == 1  # exactly one result
        tone, message = done.events[0]
        assert tone == "success" and message.startswith("Removed 100 tracks")
        (backup,) = backup_folders(data_dir)
        assert backup.name in message  # where the copy is
        assert re.search(r"\(\d[\d.,]* (bytes|KB|MB)\)\.$", message)  # and how large
        # The database and both search index files are in the copy.
        assert {"plexmix.db", "embeddings.index", "embeddings.metadata"} <= {
            item.name for item in backup.iterdir()
        }
        assert tracks_in_copy(backup / "plexmix.db") == 120  # from before the removal

        assert track_count(data_dir) == 20
        pruned = VectorIndex(dimension=2, index_path=str(index_file))
        assert sorted(pruned.track_ids) == sorted(ids[:20])
        # No selection is left that could name a track that is gone.
        assert state.selected_tracks == []
        assert (state.removal_notice_count, state.removal_notice_total) == (0, 0)
        assert state.show_removal_confirm is False and state.is_syncing is False
        assert state.total_filtered_tracks == 20 and done.stats_reloads == 1  # reloaded
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "completed"
        assert entry.extra["removal_result"] == "removed" and entry.extra["removal_held"] == 0
        assert poll(state) is None  # not reported a second time by the poll

    def test_confirm_removal_reports_tracks_listed_again(self, data_dir: Path) -> None:
        """Spec "Plex lists the tracks again": nothing is removed and the notice is cleared."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        watching = library()
        state.open_removal_confirm()
        plex = FakePlex([], relisted=range(120))
        done = confirm(state, plex)

        assert len(done.events) == 1
        tone, message = done.events[0]
        assert tone == "info"
        assert "Plex lists those tracks again" in message and "Nothing was removed" in message
        assert track_count(data_dir) == 120 and backup_folders(data_dir) == []
        assert state.removal_notice_count == 0 and state.is_syncing is False
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "completed"
        assert entry.extra["removal_result"] == "listed_again"
        # A tab that watched the removal run reports the same result, in the same tone.
        watching.is_syncing = True
        assert poll(watching) == ("info", message)
        assert watching.removal_notice_count == 0

    def test_confirm_removal_reports_more_missing_and_updates_the_notice(
        self, data_dir: Path
    ) -> None:
        """Spec "More tracks are missing than the dialog stated": refused, new count shown."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = with_notice(60, 120)  # the dialog states 60 tracks
        state.open_removal_confirm()
        plex = FakePlex([], relisted=range(10))  # 110 are missing now
        done = confirm(state, plex)

        assert len(done.events) == 1
        tone, message = done.events[0]
        assert tone == "warning" and "110" in message and "Nothing was removed" in message
        assert track_count(data_dir) == 120 and backup_folders(data_dir) == []
        assert (state.removal_notice_count, state.removal_notice_total) == (110, 120)
        assert state.show_removal_confirm is False
        entry = task_store.get("sync")
        assert entry is not None and entry.extra["removal_held"] == 110
        # Nothing is removed until the new count is confirmed.
        state.open_removal_confirm()
        assert state.show_removal_confirm is True
        assert state.removal_scope_line.startswith("110 of your 120 tracks")
        done = confirm(state, plex)
        assert done.events[0][0] == "success" and track_count(data_dir) == 10

    def test_confirm_removal_reports_backup_failure_and_keeps_the_notice(
        self, data_dir: Path
    ) -> None:
        """Spec "Backup fails": no track is removed, the user is told, the notice stays."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        (data_dir / "backups").write_text("a file where the backup folder would go")
        state = with_notice(100, 120)
        watching = library()
        state.open_removal_confirm()
        done = confirm(state, FakePlex([], relisted=range(20)))

        assert len(done.events) == 1
        tone, message = done.events[0]
        assert tone == "error"
        assert "backup could not be made" in message and "Nothing was removed" in message
        assert track_count(data_dir) == 120
        assert (state.removal_notice_count, state.removal_notice_total) == (100, 120)
        assert state.is_syncing is False
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "failed"
        assert entry.extra["removal_result"] == "backup_failed"
        # Another tab: the same sentence, not a "Sync failed" label, and its notice stays.
        watching.is_syncing = True
        assert poll(watching) == ("error", message)
        assert watching.sync_error == "" and watching.removal_notice_count == 100

    def test_dismissing_the_removal_dialog_keeps_the_notice(self, data_dir: Path) -> None:
        """Spec "Dismissed": Escape or Cancel removes nothing and the notice stays."""
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        state.open_removal_confirm()
        assert state.show_removal_confirm is True

        state.set_removal_confirm_open(False)  # Escape, or Cancel
        state.set_removal_confirm_open(False)  # idempotent
        assert state.show_removal_confirm is False
        assert (state.removal_notice_count, state.removal_notice_total) == (100, 120)
        state.set_removal_confirm_open(True)  # opens only through open_removal_confirm
        assert state.show_removal_confirm is False

        # A confirm that arrives after the dismissal starts nothing.
        plex = FakePlex([], relisted=range(20))
        assert confirm(state, plex).events == []
        assert plex.listings == 0 and track_count(data_dir) == 120
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "completed"
        assert state.removal_notice_count == 100

    def test_confirm_removal_reports_a_failed_listing_and_keeps_the_notice(
        self, data_dir: Path
    ) -> None:
        """Spec "Plex cannot be listed at confirmation time": nothing removed, told why."""
        from plexmix.plex.client import PlexReadInterrupted

        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        state.open_removal_confirm()
        done = confirm(state, FakePlex([], relisted=PlexReadInterrupted("tracks")))

        assert len(done.events) == 1
        tone, message = done.events[0]
        assert tone == "error" and "could not list" in message
        assert "Nothing was removed" in message
        assert track_count(data_dir) == 120 and backup_folders(data_dir) == []
        assert state.removal_notice_count == 100

    def test_confirm_removal_when_plex_cannot_be_reached(self, data_dir: Path) -> None:
        from plexmix.services import readiness as rd
        from plexmix.services.sync_service import PlexConnectionError

        write_config(
            data_dir,
            "plex:\n  url: http://user:dummy-ui-password-0000@127.0.0.1:9\n"
            "  library_name: Music\n",
        )
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        state.open_removal_confirm()

        def fail(settings: Any) -> Any:
            raise PlexConnectionError("Failed to connect to Plex server.", code="unreachable")

        done = confirm(state, connect=fail)

        assert len(done.events) == 1
        tone, message = done.events[0]
        assert tone == "error" and message.startswith("Can't reach your Plex server")
        assert message.endswith("Nothing was removed.")
        assert "dummy-ui-password-0000" not in message  # credentials in the address
        assert track_count(data_dir) == 120
        assert state.removal_notice_count == 100 and state.is_syncing is False
        rec = readiness_store.get("plex")
        assert rec is not None and rec.level == rd.Level.FAILING
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "failed"

    def test_confirm_removal_needs_access(self, data_dir: Path) -> None:
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        state.open_removal_confirm()
        state.access_allowed = lambda: False
        plex = FakePlex([], relisted=range(20))

        assert confirm(state, plex).events == []
        assert plex.listings == 0 and track_count(data_dir) == 120
        assert state.show_removal_confirm is True  # nothing was touched

    def test_confirm_removal_never_runs_beside_a_sync(self, data_dir: Path) -> None:
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        state.open_removal_confirm()
        # Another tab starts a sync after this dialog opened.
        held_before = task_store.get("sync")
        assert held_before is not None
        held_before.status = "running"
        plex = FakePlex([], relisted=range(20))
        done = confirm(state, plex)

        assert done.events == [("warning", "A sync is running. Nothing was removed.")]
        assert plex.listings == 0 and track_count(data_dir) == 120
        assert done.executor_calls == [] and state.show_removal_confirm is False
        assert state.removal_notice_count == 0  # the running sync replaced the hold

    def test_confirm_removal_never_confirms_more_than_the_task_holds(self, data_dir: Path) -> None:
        """State vars can be set from the browser; the count comes from the task entry."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = with_notice(60, 120)
        state.open_removal_confirm()
        state.removal_notice_count = 10_000  # forged
        plex = FakePlex([], relisted=range(10))  # 110 missing: more than the 60 held
        done = confirm(state, plex)

        assert done.events[0][0] == "warning"
        assert track_count(data_dir) == 120

        # After a server restart nothing is held, so there is nothing to confirm.
        restart_server()
        state.removal_notice_count = 110
        state.show_removal_confirm = True
        assert confirm(state, plex).events == [
            ("info", "No removal is waiting any more. Nothing was removed.")
        ]
        assert track_count(data_dir) == 120 and plex.listings == 1
        assert state.removal_notice_count == 0 and state.show_removal_confirm is False

    def test_cancel_before_the_engine_starts_removes_nothing(self, data_dir: Path) -> None:
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        state.open_removal_confirm()
        plex = FakePlex([], relisted=range(20))

        def connect(settings: Any) -> Any:
            task_store.cancel("sync")  # Cancel in the job bar while Plex connects
            return plex

        done = confirm(state, connect=connect)

        assert done.events == [("warning", "Removal cancelled. Nothing was removed.")]
        assert plex.listings == 0 and track_count(data_dir) == 120
        assert state.removal_notice_count == 100
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "cancelled"

    def test_unexpected_failure_is_plain_and_keeps_the_notice(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        token = "dummy-plex-token-0000"
        write_config(data_dir, PLEX_CONFIG)
        monkeypatch.setenv("PLEX_TOKEN", token)
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        state.open_removal_confirm()
        engine = MagicMock()
        engine.remove_unlisted_tracks.side_effect = RuntimeError(
            f"boom token={token} at http://user:dummy-ui-password-0000@127.0.0.1:9/library"
        )
        caplog.set_level(logging.DEBUG)
        with patch("plexmix.plex.sync.SyncEngine", return_value=engine):
            done = confirm(state, SimpleNamespace())

        expected = "Couldn't remove the tracks. Details are in the server log."
        assert done.events == [("error", expected)]
        assert token not in caplog.text and "boom" in caplog.text
        assert "dummy-ui-password-0000" not in caplog.text  # the address is masked too
        assert state.removal_notice_count == 100 and state.is_syncing is False
        entry = task_store.get("sync")
        assert entry is not None and (entry.status, entry.message) == ("failed", expected)

    def test_a_stand_in_result_is_not_trusted(self, data_dir: Path) -> None:
        """Only the engine's own RemovalResult is reported; a mock's answer is a failure."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        state.open_removal_confirm()
        engine = MagicMock()  # remove_unlisted_tracks answers another mock
        with patch("plexmix.plex.sync.SyncEngine", return_value=engine):
            done = confirm(state, SimpleNamespace())

        engine.remove_unlisted_tracks.assert_called_once()
        call = engine.remove_unlisted_tracks.call_args
        assert call.args == (100,)
        # The keys are not known for a hold that only the task entry carries.
        assert call.kwargs["held_keys"] is None
        assert isinstance(call.kwargs["cancel_event"], threading.Event)
        assert done.events == [
            ("error", "Couldn't remove the tracks. Details are in the server log.")
        ]
        assert state.removal_notice_count == 100

    def test_opening_reads_the_count_from_the_task_again(self, data_dir: Path) -> None:
        state = with_notice(100, 120)
        task_store.clear("sync")
        held_sync(300, 400)  # another tab synced meanwhile
        state.open_removal_confirm()
        assert state.show_removal_confirm is True
        assert (state.removal_notice_count, state.removal_notice_total) == (300, 400)

        restart_server()  # nothing is held any more
        poll(state)
        assert state.show_removal_confirm is False and state.removal_notice_count == 0

    def test_open_removal_confirm_needs_access(self, data_dir: Path) -> None:
        held_sync(5443, 5943)
        state = library()
        state.access_allowed = lambda: False

        state.open_removal_confirm()

        # Nothing about the held removal reaches a caller that is not signed in.
        assert (state.removal_notice_count, state.removal_notice_total) == (0, 0)
        assert state.show_removal_confirm is False

    def test_cancel_while_plex_is_checked_removes_nothing(self, data_dir: Path) -> None:
        """Cancel in the job bar during the second listing: nothing is removed."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        watching = library()
        state.open_removal_confirm()

        class CancelledWhileListing(FakePlex):
            def list_track_keys(self) -> Any:
                task_store.cancel("sync")
                return super().list_track_keys()

        plex = CancelledWhileListing([], relisted=range(20))
        done = confirm(state, plex)

        cancelled = "Removal cancelled. Nothing was removed."
        assert done.events == [("warning", cancelled)]  # never a success
        assert plex.listings == 1 and track_count(data_dir) == 120
        assert backup_folders(data_dir) == []  # no backup was started either
        assert state.removal_notice_count == 100 and state.is_syncing is False
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "cancelled"
        assert entry.extra["removal_result"] == "cancelled"
        # A tab that watched the removal run says the same, not "Sync cancelled".
        watching.is_syncing = True
        assert poll(watching) == ("warning", cancelled)
        assert watching.removal_notice_count == 100

    def test_cancel_while_the_backup_is_written_removes_nothing(self, data_dir: Path) -> None:
        """Cancel during the backup: nothing is removed, and the copy that exists is named."""
        from plexmix.database.backup import backup_library

        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        state.open_removal_confirm()

        def cancelled_during_backup(db: Any, index_path: Any) -> Any:
            made = backup_library(db, index_path)
            task_store.cancel("sync")
            return made

        with patch("plexmix.plex.sync.backup_library", cancelled_during_backup):
            done = confirm(state, FakePlex([], relisted=range(20)))

        assert len(done.events) == 1
        tone, message = done.events[0]
        assert tone == "warning"
        assert message.startswith("Removal cancelled. Nothing was removed.")
        (backup,) = backup_folders(data_dir)
        assert f"A backup was saved to {backup} (" in message
        assert track_count(data_dir) == 120
        assert state.removal_notice_count == 100
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "cancelled"

    def test_confirm_removal_keeps_tracks_that_went_missing_after_the_sync(
        self, data_dir: Path
    ) -> None:
        """Only tracks the sync held are removed: nobody was asked about the others."""
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        # The sync: Plex lists t0 to t19, so t20 to t119 are held. At confirmation
        # time Plex lists t10 to t69: 50 held tracks are listed again, 50 are still
        # missing, and t0 to t9 are missing now.
        plex = FakePlex(range(20), relisted=range(10, 70))
        state = library()
        run_sync(state, plex)
        poll(state)
        state.open_removal_confirm()
        assert state.removal_notice_count == 100
        done = confirm(state, plex)

        assert len(done.events) == 1
        tone, message = done.events[0]
        assert tone == "success" and message.startswith("Removed 50 tracks")
        assert "10 other tracks are missing from Plex now" in message
        assert track_keys(data_dir) == sorted(f"t{i}" for i in range(70))
        assert state.removal_notice_count == 0

    def test_tracks_listed_again_while_others_went_missing_removes_nothing(
        self, data_dir: Path
    ) -> None:
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        # The 100 held tracks are listed again; 20 other tracks are missing now.
        plex = FakePlex(range(20), relisted=range(20, 120))
        state = library()
        run_sync(state, plex)
        poll(state)
        state.open_removal_confirm()
        done = confirm(state, plex)

        assert len(done.events) == 1
        tone, message = done.events[0]
        assert tone == "info" and "Plex lists those tracks again" in message
        assert "20 other tracks are missing from Plex now" in message
        assert track_count(data_dir) == 120 and backup_folders(data_dir) == []
        assert state.removal_notice_count == 0

    def test_the_new_count_after_more_missing_is_confirmed_with_its_own_keys(
        self, data_dir: Path
    ) -> None:
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        plex = FakePlex(range(60), relisted=range(10))  # 60 held; then 110 are missing
        state = library()
        run_sync(state, plex)
        poll(state)
        state.open_removal_confirm()
        assert confirm(state, plex).events[0][0] == "warning"
        assert (state.removal_notice_count, track_count(data_dir)) == (110, 120)

        # The second confirmation is for the 110 tracks of the new count.
        state.open_removal_confirm()
        done = confirm(state, plex)
        assert done.events[0][0] == "success"
        assert track_keys(data_dir) == sorted(f"t{i}" for i in range(10))

    def test_an_index_that_cannot_be_loaded_is_backed_up_before_it_is_opened(
        self, data_dir: Path
    ) -> None:
        """The backup copies the index files as they are; the index is opened after it."""
        write_config(
            data_dir,
            PLEX_CONFIG + "embedding:\n  default_provider: custom\n  custom_dimension: 2\n",
        )
        make_library(data_dir, 120)
        (data_dir / "embeddings.index").write_bytes(b"not a search index")
        (data_dir / "embeddings.metadata").write_bytes(b"not its metadata")
        state = with_notice(100, 120)
        state.open_removal_confirm()
        done = confirm(state, FakePlex([], relisted=range(20)))

        assert done.events[0][0] == "success" and track_count(data_dir) == 20
        (backup,) = backup_folders(data_dir)
        assert (backup / "embeddings.index").read_bytes() == b"not a search index"
        assert (backup / "embeddings.metadata").read_bytes() == b"not its metadata"
        assert tracks_in_copy(backup / "plexmix.db") == 120

    def test_a_watching_tab_drops_its_selection_when_tracks_were_removed(
        self, data_dir: Path
    ) -> None:
        """The tab that confirmed clears its selection. A tab that watched does the same."""
        ids = make_library(data_dir, 120)
        write_config(data_dir, PLEX_CONFIG)
        state = with_notice(100, 120)
        watching = library()
        watching.is_syncing = True  # it saw the removal run
        watching.selected_tracks = [str(ids[50]), str(ids[119])]  # both will be removed
        state.open_removal_confirm()

        done = confirm(state, FakePlex([], relisted=range(20)))

        assert done.events[0][0] == "success" and track_count(data_dir) == 20
        tone, _message = poll(watching)
        assert tone == "success"
        assert watching.selected_tracks == []

    def test_a_watching_tab_keeps_its_selection_when_nothing_was_removed(
        self, data_dir: Path
    ) -> None:
        ids = make_library(data_dir, 120)
        write_config(data_dir, PLEX_CONFIG)
        state = with_notice(100, 120)
        watching = library()
        watching.is_syncing = True
        watching.selected_tracks = [str(ids[50])]
        state.open_removal_confirm()

        done = confirm(state, FakePlex([], relisted=range(120)))  # all listed again

        assert done.events[0][0] == "info"
        poll(watching)
        assert watching.selected_tracks == [str(ids[50])]

    @pytest.mark.parametrize("changed", ["database", "library", "server"])
    def test_hold_of_another_library_is_ended_without_a_removal(
        self, data_dir: Path, changed: str
    ) -> None:
        """A hold belongs to the database and the Plex library its sync compared."""
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = library()
        plex = FakePlex(range(20), relisted=range(20))
        run_sync(state, plex)  # holds t20 to t119 of this database and library
        poll(state)
        assert state.removal_notice_count == 100

        # The settings file is edited while the server runs.
        other = data_dir / "other" / "plexmix.db"
        if changed == "database":
            other.parent.mkdir()
            make_library(other.parent, 150)  # holds t0 to t149: the held keys exist here too
            write_config(data_dir, PLEX_CONFIG + f"database:\n  path: {other}\n")
        elif changed == "library":
            write_config(data_dir, PLEX_CONFIG.replace("Music", "Audiobooks"))
        else:
            write_config(data_dir, PLEX_CONFIG.replace("127.0.0.1:9", "127.0.0.1:10"))

        tab = library()
        poll(tab)
        tab.open_removal_confirm()
        done = confirm(tab, plex)

        assert done.events == [("warning", ls.REMOVAL_SETTINGS_CHANGED_MESSAGE)]
        # Plex was not asked, nothing was backed up, and no track was removed anywhere.
        assert plex.listings == 0
        assert track_count(data_dir) == 120
        assert backup_folders(data_dir) == []
        if changed == "database":
            assert tracks_in_copy(other) == 150
            assert not (other.parent / "backups").exists()
        # The hold has ended: no notice, in this tab or after the entry is dropped.
        assert tab.removal_notice_count == 0 and tab.is_syncing is False
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "cancelled"
        forget_finished_sync()
        assert ls.waiting_removal(None) == (0, 0)

    def test_hold_is_kept_when_other_settings_change(self, data_dir: Path) -> None:
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = library()
        plex = FakePlex(range(20), relisted=range(20))
        run_sync(state, plex)
        poll(state)

        write_config(data_dir, PLEX_CONFIG + "ai:\n  default_provider: openai\n")
        state.open_removal_confirm()
        done = confirm(state, plex)

        assert done.events[0][0] == "success"
        assert track_count(data_dir) == 20

    def test_a_new_count_after_more_missing_belongs_to_the_same_library(
        self, data_dir: Path
    ) -> None:
        """The hold that "more missing" leaves behind is tied to the library as well."""
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        plex = FakePlex(range(60), relisted=range(10))  # 60 held; then 110 are missing
        state = library()
        run_sync(state, plex)
        poll(state)
        state.open_removal_confirm()
        assert confirm(state, plex).events[0][0] == "warning"
        assert state.removal_notice_count == 110

        write_config(data_dir, PLEX_CONFIG.replace("Music", "Audiobooks"))
        state.open_removal_confirm()
        done = confirm(state, plex)

        assert done.events == [("warning", ls.REMOVAL_SETTINGS_CHANGED_MESSAGE)]
        assert track_count(data_dir) == 120

    def test_cancel_while_the_tracks_are_deleted_removes_nothing(self, data_dir: Path) -> None:
        """Cancel in the job bar while the removal is being applied: every delete is undone."""
        from plexmix.database.sqlite_manager import SQLiteManager

        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        state.open_removal_confirm()
        delete_track = SQLiteManager.delete_track
        deleted: list[int] = []

        def cancelled_at_the_fifth(db: Any, track_id: int) -> None:
            deleted.append(track_id)
            if len(deleted) == 5:
                task_store.cancel("sync")
            delete_track(db, track_id)

        with patch.object(SQLiteManager, "delete_track", cancelled_at_the_fifth):
            done = confirm(state, FakePlex([], relisted=range(20)))

        assert len(done.events) == 1
        tone, message = done.events[0]
        assert tone == "warning"
        assert message.startswith("Removal cancelled. Nothing was removed.")
        assert len(deleted) == 5
        assert track_count(data_dir) == 120
        (backup,) = backup_folders(data_dir)
        assert f"A backup was saved to {backup} (" in message
        assert state.removal_notice_count == 100
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "cancelled"
        assert query(data_dir, "SELECT COUNT(*) FROM sync_history") == [(0,)]

    def test_removal_that_cannot_delete_reports_the_reason_and_its_backup(
        self, data_dir: Path
    ) -> None:
        import sqlite3 as sqlite

        from plexmix.database.sqlite_manager import SQLiteManager

        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        state = with_notice(100, 120)
        state.open_removal_confirm()

        with patch.object(
            SQLiteManager, "delete_track", side_effect=sqlite.OperationalError("database is locked")
        ):
            done = confirm(state, FakePlex([], relisted=range(20)))

        assert len(done.events) == 1
        tone, message = done.events[0]
        (backup,) = backup_folders(data_dir)
        assert tone == "error"
        assert message.startswith(
            "The tracks could not be removed: database is locked. Nothing was removed."
        )
        assert f"A backup was saved to {backup} (" in message
        assert track_count(data_dir) == 120
        # Nothing was removed, so the removal still waits.
        assert state.removal_notice_count == 100
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "failed"
        assert entry.extra["removal_result"] == "failed"

    def test_removed_result_says_which_confirmed_tracks_were_kept(self, data_dir: Path) -> None:
        write_config(data_dir, PLEX_CONFIG)
        make_library(data_dir, 120)
        # 100 tracks are held; at confirmation time Plex lists 30 of them again.
        plex = FakePlex(range(20), relisted=range(50))
        state = library()
        run_sync(state, plex)
        poll(state)
        state.open_removal_confirm()
        done = confirm(state, plex)

        tone, message = done.events[0]
        assert tone == "success"
        assert message.startswith(
            "Removed 70 tracks that Plex no longer lists. Plex lists 30 of the confirmed "
            "tracks again, and they were kept."
        )
        assert track_count(data_dir) == 50


# ── Whole-library embedding from Library (spec scenario) ─────────────────


class TestEmbedAllFromLibrary:
    def test_nothing_selected_and_nothing_missing_says_so(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, "embedding:\n  default_provider: openai\n")
        os.environ["OPENAI_API_KEY"] = SENTINEL_OPENAI
        try:
            ids = make_library(data_dir, 3, embedded=3)
            # A built index: the FAISS file and its metadata.
            (data_dir / "embeddings.index").write_bytes(b"faiss")
            with open(data_dir / "embeddings.metadata", "wb") as f:
                pickle.dump({"dimension": 1536, "track_ids": list(ids)}, f)
            state = library()
            with patch.object(ls.rx, "toast", toasts()):
                events = run(state.generate_embeddings())
        finally:
            os.environ.pop("OPENAI_API_KEY", None)
        assert events == [("info", "All tracks already have embeddings.")]
        assert "all tracks already have embeddings" in state.embed_all_notice.lower()
        assert state.embed_all_notice_tone == "info"
        assert state.embed_all_open is False
        state.dismiss_embed_all_notice()
        assert state.embed_all_notice == ""

    def test_nothing_selected_with_missing_tracks_opens_the_confirmation(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, "embedding:\n  default_provider: openai\n")
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        make_library(data_dir, 5, embedded=1)
        state = library()
        with patch.object(ls.rx, "toast", toasts()):
            run(state.generate_embeddings())
        assert state.embed_all_open is True and state.embed_all_count == 4
        assert state.embed_all_scope["provider_label"] == "OpenAI"

    def test_a_blocked_provider_is_an_error_notice(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, "embedding:\n  default_provider: openai\n")  # no key
        make_library(data_dir, 2)
        state = library()
        with patch.object(ls.rx, "toast", toasts()):
            run(state.generate_embeddings())
        assert state.embed_all_open is False
        assert state.embed_all_notice and state.embed_all_notice_tone == "error"

    def test_completion_toast_reports_nothing_missing(self) -> None:
        from plexmix.ui.states import library_state as ls

        task_store.start("embedding")
        task_store.update("embedding", message="Updating search index...")
        task_store.complete("embedding", message=ls.ALL_EMBEDDED_MESSAGE)
        state = library()
        state.is_embedding = True
        with patch.object(ls.rx, "toast", toasts()), patch.object(
            state, "load_library_stats", lambda: None
        ), patch.object(state, "load_tracks", lambda: None):
            assert state.poll_task_progress() == ("success", ls.ALL_EMBEDDED_MESSAGE)

        task_store.clear("embedding")
        task_store.start("embedding")
        task_store.update("embedding", message="Updating search index...")
        task_store.complete("embedding")
        state.is_embedding = True
        with patch.object(ls.rx, "toast", toasts()), patch.object(
            state, "load_library_stats", lambda: None
        ), patch.object(state, "load_tracks", lambda: None):
            # complete() keeps the last progress text; it is never shown as the result.
            assert state.poll_task_progress() == ("success", "Embeddings generated successfully!")


# ── Plain copy (no terminal commands in the UI) ──────────────────────────


class TestPlainCopy:
    _CLI = ("Run:", "pip install", "poetry install", "plexmix config", "poetry run")

    def test_no_cli_hints_in_library_ui_strings(self) -> None:
        import ast

        root = Path(__file__).resolve().parents[2] / "src" / "plexmix" / "ui"
        offenders = []
        for rel in ("states/library_state.py", "pages/library.py"):
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if any(hint in node.value for hint in self._CLI):
                        offenders.append(f"{rel}:{node.lineno}: {node.value[:60]}")
        assert offenders == []

    def test_audio_analysis_without_essentia_says_how_in_plain_words(self, data_dir: Path) -> None:
        import importlib.util

        if importlib.util.find_spec("essentia") is not None:
            pytest.skip("essentia is installed here")
        make_library(data_dir, 1)  # past the database check, as in the real app
        state = library()
        run(state.analyze_audio())
        entry = task_store.get("audio")
        assert entry is not None and entry.status == "failed"
        assert entry.message.startswith("Audio analysis isn't installed.")
        assert "plexmix[audio]" in entry.message
        assert not any(hint in entry.message for hint in self._CLI)
