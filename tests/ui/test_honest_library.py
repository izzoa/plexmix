"""webui-honest-status-and-first-run, group 9.2/9.3: Library selection honesty, bulk
tags (add by default, replace explicit), loading versus empty, sync failures and
the sync AI-tagging note (spec ``webui-library-selection``). Also the "Regenerate" sync:
its confirmation, the provider check and the in-place run (spec ``regeneration-safety``).

Reflex is mocked (tests/ui/conftest.py). Every test runs on a scratch
``PLEXMIX_DATA_DIR`` with its own config.yaml and a real SQLite library; the
keyring lookup is patched out, Plex and providers are fakes, and the readiness
and task stores are cleared around each test. Nothing touches the network.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
import re
import sqlite3
import threading
from datetime import datetime
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

    def test_a_sync_that_left_the_search_index_as_it_is_is_a_warning(self) -> None:
        """The engine records tracks in the way of the index on its outcome (a plain sync
        that makes embeddings); the web UI's own sync makes none, so this is the guard."""
        from plexmix.plex.sync import SyncOutcome
        from plexmix.ui.states import library_state as ls

        outcome = SyncOutcome(index_left_out=4)
        extra = ls.sync_outcome_extra(outcome)

        assert extra[ls.INDEX_LEFT_OUT_KEY] == 4 and extra[ls.SYNC_PARTIAL_KEY] is True
        assert ls.sync_result_tone(extra) == "warning"
        message = ls.sync_result_message(outcome)
        assert message.startswith("Sync completed. The search index was not updated. 4 tracks hold")
        assert "Regenerate sync on the Library page" in message
        # A clean outcome is a success, and says nothing about the index.
        clean = ls.sync_outcome_extra(SyncOutcome())
        assert clean[ls.INDEX_LEFT_OUT_KEY] == 0 and ls.sync_result_tone(clean) == "success"
        assert ls.sync_result_message(SyncOutcome()) == ""

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


# ── "Regenerate" sync: confirmation, provider check and in-place run ─────
#
# Spec ``regeneration-safety`` ("Web UI regenerate actions never clear first") and
# ``webui-library-selection`` ("Regenerate sync confirmation states its scope").
# The real SyncEngine runs against the fake Plex client, a fake AI provider and a
# fake embedding generator: no request is made and no model is loaded.

DUMMY_KEY = "dummy-api-key-0000"
OPENAI_AI = "ai:\n  default_provider: openai\n  model: gpt-5-mini\n"
OPENAI_EMBEDDINGS = "embedding:\n  default_provider: openai\n  model: text-embedding-3-small\n"
LOCAL_EMBEDDINGS = "embedding:\n  default_provider: local\n  model: all-MiniLM-L6-v2\n"
REGENERATE_CONFIG = PLEX_CONFIG + OPENAI_AI + OPENAI_EMBEDDINGS
OLD_VECTOR = [0.1, 0.2]
NEW_VECTOR = [0.9, 0.8]
LOCAL_ONLY = "Runs on this machine. Nothing is sent to a cloud provider."
TAGS_SENT_TO = "Track titles, artists, albums and genres are sent to {}."
EMBEDDING_TEXT_SENT_TO = "Track titles, artists, albums, genres and tags are sent to {}."


def _ids_in_prompt(prompt: str) -> list[int]:
    """The ids of the tracks a tagging request lists."""
    listed = prompt.split("Assign tags to the following songs:")[1]
    listed = listed.split("Return a JSON object")[0]
    return [int(entry["id"]) for entry in json.loads(listed)]


class FakeTagger:
    """Stands in for the AI provider: new tags for every track a request lists.

    Tracks in ``omit`` are left out of the reply. Request number ``fail_on`` (1 is the
    first) raises ``error``. ``before`` is called with the number of each request
    before it is answered.
    """

    provider_name = "OpenAI"
    model = "gpt-5-mini"

    def __init__(
        self,
        *,
        omit: Iterable[int] = (),
        fail_on: int = 0,
        error: Optional[BaseException] = None,
        before: Any = None,
    ) -> None:
        self.omit = set(omit)
        self.fail_on = fail_on
        self.error = error or RuntimeError("401 Unauthorized")
        self.before = before
        self.requests: list[list[int]] = []

    def complete(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        timeout: int = 30,
    ) -> str:
        ids = _ids_in_prompt(prompt)
        self.requests.append(ids)
        if self.before is not None:
            self.before(len(self.requests))
        if self.fail_on == len(self.requests):
            raise self.error
        reply = {
            str(track_id): {
                "tags": [f"new-{track_id}"],
                "environments": ["focus"],
                "instruments": ["piano"],
            }
            for track_id in ids
            if track_id not in self.omit
        }
        return json.dumps(reply)

    @property
    def sent(self) -> list[int]:
        """Every track that was sent to the provider, by id."""
        return sorted({track_id for request in self.requests for track_id in request})


class FakeEmbedder:
    """Stands in for the embedding generator of the "openai" provider (2 dimensions).

    Request number ``fail_on`` (1 is the first) raises ``error``. ``before`` is called
    with the number of each request before it is answered.
    """

    provider_name = "openai"

    def __init__(
        self, *, fail_on: int = 0, error: Optional[BaseException] = None, before: Any = None
    ) -> None:
        self.fail_on = fail_on
        self.error = error or ConnectionError("Connection refused")
        self.before = before
        self.requests: list[list[str]] = []

    def generate_batch_embeddings(
        self, texts: list[str], batch_size: int = 100
    ) -> list[list[float]]:
        self.requests.append(list(texts))
        if self.before is not None:
            self.before(len(self.requests))
        if self.fail_on == len(self.requests):
            raise self.error
        return [list(NEW_VECTOR) for _ in texts]

    def generate_embedding(self, text: str) -> list[float]:
        return list(NEW_VECTOR)

    def get_dimension(self) -> int:
        return 2

    @property
    def embedded(self) -> int:
        """How many texts the generator was sent."""
        return sum(len(request) for request in self.requests)


@pytest.fixture
def small_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two tracks per request, so that six tracks make three batches of each kind."""
    from plexmix.plex import sync as engine

    monkeypatch.setattr(engine, "_TAG_BATCH_SIZE", 2)
    monkeypatch.setattr(engine, "_EMBEDDING_BATCH_SIZE", 2)


def make_old_library(data: Path, tracks: int) -> list[int]:
    """A library whose tags and embeddings were written long ago, with its search index.

    Track ``n`` has the tags ``old-<n>`` and the "openai" embedding ``OLD_VECTOR``.
    Nothing carries a time of today, so a regenerate run never waits for the clock
    (``regenerate_service._new_start``).
    """
    from plexmix.database.models import Album, Artist, Embedding, Track
    from plexmix.database.sqlite_manager import SQLiteManager
    from plexmix.database.vector_index import VectorIndex

    with SQLiteManager(str(data / "plexmix.db")) as db:
        artist = db.insert_artist(Artist(plex_key="a1", name="Artist"))
        album = db.insert_album(Album(plex_key="al1", title="Album", artist_id=artist))
        ids = [
            db.insert_track(
                Track(
                    plex_key=f"t{i}",
                    title=f"Song {i:03d}",
                    artist_id=artist,
                    album_id=album,
                    tags=f"old-{i}",
                    environments="old-room",
                    instruments="old-horn",
                )
            )
            for i in range(tracks)
        ]
        for track_id in ids:
            db.insert_embedding(
                Embedding(
                    track_id=track_id,
                    embedding_model="openai",
                    embedding_dim=2,
                    vector=list(OLD_VECTOR),
                    updated_at=datetime(2020, 1, 1, 9, 0, 0),
                )
            )
    index = VectorIndex(dimension=2, index_path=str(data / "embeddings.index"))
    index.build_index([list(OLD_VECTOR) for _ in ids], ids)
    index.save_index(str(data / "embeddings.index"))
    return ids


def tags_of(data: Path) -> dict[int, str]:
    return dict(query(data, "SELECT id, tags FROM tracks ORDER BY id"))


def vectors_of(data: Path) -> dict[int, list[float]]:
    """The "openai" embedding of every track that has one, rounded for comparison."""
    from plexmix.database.sqlite_manager import SQLiteManager

    with SQLiteManager(str(data / "plexmix.db")) as db:
        rows = db.get_all_embeddings("openai")
    return {track_id: [round(value, 4) for value in vector] for track_id, vector in rows}


def index_ids(data: Path) -> list[int]:
    """The tracks the saved search index holds."""
    from plexmix.database.vector_index import VectorIndex

    return sorted(VectorIndex(dimension=2, index_path=str(data / "embeddings.index")).track_ids)


def stored_state(data: Path) -> dict[str, Any]:
    """Everything a regenerate run may replace: tag columns, embedding rows, index files."""
    return {
        "tags": query(
            data,
            "SELECT id, tags, environments, instruments, tags_generated_at FROM tracks ORDER BY id",
        ),
        "embeddings": query(
            data,
            "SELECT track_id, embedding_model, vector, updated_at FROM embeddings "
            "ORDER BY track_id, embedding_model",
        ),
        "files": {
            item.name: item.read_bytes()
            for item in sorted(data.iterdir())
            if item.name.startswith("embeddings.")
        },
    }


def marker_file(data: Path) -> Path:
    from plexmix.services.regenerate_service import marker_path

    return marker_path(data / "plexmix.db")


def run_regenerate(state: Any, plex: Any, tagger: Any, embedder: Any) -> Any:
    """Run ``start_sync`` in "regenerate" mode with providers that pass the check."""
    from plexmix.services.regenerate_service import CheckedProviders

    def passed(settings: Any, need_ai: bool, need_embeddings: bool) -> Any:
        assert need_ai and need_embeddings
        return CheckedProviders(tagger, embedder)

    state.sync_mode = "regenerate"
    with patch("plexmix.services.sync_service.connect_plex", lambda settings: plex), patch(
        "plexmix.services.regenerate_service.check_providers", passed
    ):
        run(state.start_sync())
    entry = task_store.get("sync")
    assert entry is not None
    return entry


def run_refused(state: Any, plex: Any) -> Any:
    """Run ``start_sync`` in "regenerate" mode through the real provider check."""
    state.sync_mode = "regenerate"
    with patch("plexmix.services.sync_service.connect_plex", lambda settings: plex):
        run(state.start_sync())
    entry = task_store.get("sync")
    assert entry is not None
    return entry


def run_spying(gen: Any) -> list[Any]:
    """Drain a handler; return the functions it handed to ``run_in_executor``."""
    seen: list[Any] = []

    async def go() -> None:
        loop = asyncio.get_running_loop()
        original = loop.run_in_executor

        def spy(executor: Any, func: Any, *args: Any) -> Any:
            seen.append(func)
            return original(executor, func, *args)

        loop.run_in_executor = spy  # type: ignore[method-assign]
        await _drain(gen)

    asyncio.run(go())
    return seen


def readiness_of(component: str) -> tuple[Any, Any]:
    """``(level, cause)`` that readiness holds for ``component`` (``(None, None)`` for none)."""
    record = readiness_store.get(component)
    return (record.level, record.cause) if record is not None else (None, None)


def scope_for(data: Path, config: str, count: int) -> dict[str, str]:
    from plexmix.config.settings import Settings
    from plexmix.ui.states.library_state import regenerate_scope

    write_config(data, config)
    return regenerate_scope(Settings.load_from_file(), count)


class TestRegenerateConfirm:
    """The confirmation states the count, both providers and where track details go."""

    def test_scope_names_count_providers_and_destination(self, data_dir: Path) -> None:
        """Spec "Cloud AI provider and local embeddings"."""
        from plexmix.ui.states import library_state as ls

        scope = scope_for(data_dir, OPENAI_AI + LOCAL_EMBEDDINGS, 5943)

        assert set(scope) == set(ls.REGENERATE_SCOPE_KEYS)
        assert scope["tags_line"] == "5,943 tracks will be tagged."
        assert scope["embeddings_line"] == "The embeddings for 5,943 tracks will be replaced."
        assert (scope["ai_label"], scope["ai_model"]) == ("OpenAI", "gpt-5-mini")
        assert (scope["embedding_label"], scope["embedding_model"]) == ("Local", "all-MiniLM-L6-v2")
        assert scope["privacy_line"] == TAGS_SENT_TO.format("OpenAI")
        assert scope["count"] == "5,943" and scope["checking_line"] == ""
        assert ls.regenerate_scope_stated(scope)

    @pytest.mark.parametrize(
        "config,expected",
        [
            # Spec "Everything local".
            ("ai:\n  default_provider: local\n" + LOCAL_EMBEDDINGS, LOCAL_ONLY),
            # One provider for both: named once, with everything it receives.
            (OPENAI_AI + OPENAI_EMBEDDINGS, EMBEDDING_TEXT_SENT_TO.format("OpenAI")),
            (
                "ai:\n  default_provider: claude\nembedding:\n  default_provider: cohere\n",
                f"{TAGS_SENT_TO.format('Claude')} {EMBEDDING_TEXT_SENT_TO.format('Cohere')}",
            ),
            (
                "ai:\n  default_provider: local\n" + OPENAI_EMBEDDINGS,
                EMBEDDING_TEXT_SENT_TO.format("OpenAI"),
            ),
            # A Custom endpoint on another machine is named by its host, never its key.
            (
                "ai:\n  default_provider: custom\n  custom_model: m\n"
                f"  custom_endpoint: http://192.168.1.20:8080/v1\n  custom_api_key: {DUMMY_KEY}\n"
                + LOCAL_EMBEDDINGS,
                TAGS_SENT_TO.format("192.168.1.20"),
            ),
            (
                "ai:\n  default_provider: custom\n  custom_model: m\n"
                "  custom_endpoint: http://127.0.0.1:8080/v1\n" + LOCAL_EMBEDDINGS,
                LOCAL_ONLY,
            ),
        ],
    )
    def test_privacy_line_names_every_destination_once(
        self, data_dir: Path, config: str, expected: str
    ) -> None:
        scope = scope_for(data_dir, config, 12)
        assert scope["privacy_line"] == expected
        assert DUMMY_KEY not in " ".join(scope.values())

    def test_empty_library_does_not_claim_zero_tracks(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        scope = scope_for(data_dir, OPENAI_AI + LOCAL_EMBEDDINGS, 0)
        assert scope["tags_line"] == ls.REGENERATE_EMPTY_TAGS_LINE
        assert scope["embeddings_line"] == ls.REGENERATE_EMPTY_EMBEDDINGS_LINE
        assert "0 tracks" not in " ".join(scope.values())
        assert ls.regenerate_scope_stated(scope)

    def test_fixed_sentence_says_nothing_is_cleared_first(self) -> None:
        from plexmix.ui.states import library_state as ls

        assert ls.REGENERATE_IN_PLACE_LINE == (
            "Tags and embeddings are replaced as new ones arrive, and nothing is cleared first."
        )

    def test_dialog_opens_at_once_and_then_states_its_scope(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, PLEX_CONFIG + OPENAI_AI + LOCAL_EMBEDDINGS)
        make_library(data_dir, 5)
        state = library()
        state.sync_mode = "regenerate"

        assert state.start_sync_or_confirm() is None
        assert state.show_regenerate_confirm is True
        assert state.regenerate_scope["checking_line"] == ls.REGENERATE_SCOPE_CHECKING_LINE
        assert not ls.regenerate_scope_stated(state.regenerate_scope)
        assert state.regenerate_confirm_blocked is True  # "Regenerate" is disabled

        # The count and the settings are read off the event loop.
        assert run_spying(state.load_regenerate_scope()) == [ls.regenerate_preflight]
        scope = state.regenerate_scope
        assert scope["tags_line"] == "5 tracks will be tagged."
        assert scope["embeddings_line"] == "The embeddings for 5 tracks will be replaced."
        assert (scope["ai_label"], scope["embedding_label"]) == ("OpenAI", "Local")
        assert scope["privacy_line"] == TAGS_SENT_TO.format("OpenAI")
        assert scope["checking_line"] == ""
        assert state.regenerate_confirm_blocked is False

    def test_scope_follows_the_settings_of_now(self, data_dir: Path) -> None:
        """The dialog is a consent to send track details: it reads the settings when it opens."""
        write_config(data_dir, "ai:\n  default_provider: local\n" + LOCAL_EMBEDDINGS)
        make_library(data_dir, 3)
        state = library()
        state.sync_mode = "regenerate"
        state.start_sync_or_confirm()
        run(state.load_regenerate_scope())
        assert state.regenerate_scope["privacy_line"] == LOCAL_ONLY
        state.set_regenerate_confirm_open(False)

        write_config(data_dir, OPENAI_AI + LOCAL_EMBEDDINGS)  # changed in another tab
        state.start_sync_or_confirm()
        assert state.regenerate_scope["privacy_line"] == ""  # never the earlier answer
        run(state.load_regenerate_scope())
        assert state.regenerate_scope["privacy_line"] == TAGS_SENT_TO.format("OpenAI")

    def test_a_click_before_the_scope_is_stated_starts_nothing(self, data_dir: Path) -> None:
        write_config(data_dir, REGENERATE_CONFIG)
        make_library(data_dir, 3)
        state = library()
        state.sync_mode = "regenerate"
        state.start_sync_or_confirm()

        with patch("plexmix.services.sync_service.connect_plex") as connect:
            run(state.start_sync())

        assert task_store.get("sync") is None and connect.call_count == 0
        assert state.is_syncing is False
        assert state.show_regenerate_confirm is True  # still open, to be stated

    def test_scope_that_cannot_be_read_says_so_and_cannot_be_confirmed(
        self, data_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from plexmix.ui.states import library_state as ls

        state = library()
        state.sync_mode = "regenerate"
        state.start_sync_or_confirm()
        with patch.object(ls, "regenerate_preflight", side_effect=OSError("disk gone")):
            run(state.load_regenerate_scope())

        assert state.regenerate_scope["checking_line"] == ls.REGENERATE_SCOPE_ERROR_LINE
        assert state.regenerate_confirm_blocked is True
        run(state.start_sync())
        assert task_store.get("sync") is None
        assert "Could not read the scope of a regenerate sync" in caplog.text

    def test_scope_is_read_only_for_an_open_confirmation(self, data_dir: Path) -> None:
        """The Sync buttons dispatch the loader in every mode: it acts only for this dialog."""
        from plexmix.ui.states.library_state import LibraryState

        state = library()
        assert state.start_sync_or_confirm() is LibraryState.start_sync  # incremental
        assert run_spying(state.load_regenerate_scope()) == []
        assert state.regenerate_scope == {}

        stranger = library()
        stranger.sync_mode = "regenerate"
        stranger.start_sync_or_confirm()
        stranger.access_allowed = lambda: False  # not signed in
        assert run_spying(stranger.load_regenerate_scope()) == []
        assert stranger.regenerate_scope["tags_line"] == ""

    def test_scope_that_arrives_after_the_dialog_closed_is_dropped(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        make_library(data_dir, 2)
        state = library()
        state.sync_mode = "regenerate"
        state.start_sync_or_confirm()

        def closed_meanwhile() -> dict[str, str]:
            state.show_regenerate_confirm = False  # Escape, while the count was read
            return ls.regenerate_scope_notice("late")

        with patch.object(ls, "regenerate_preflight", closed_meanwhile):
            run(state.load_regenerate_scope())
        assert state.regenerate_scope["checking_line"] == ls.REGENERATE_SCOPE_CHECKING_LINE


def _pass(to_do: int, written: int, *, started: bool = True, finished: bool = True) -> Any:
    from plexmix.plex.sync import RegeneratePass

    return RegeneratePass(
        requested=True,
        started=started,
        finished=finished,
        to_do_ids=set(range(to_do)),
        written_ids=set(range(written)),
    )


class TestRegenerateResult:
    """How a "Regenerate" sync ends, from what the engine says it wrote."""

    @pytest.mark.parametrize(
        "tags,embeddings,stopped,status,message",
        [
            (
                (6, 6),
                (6, 6),
                "",
                "completed",
                "Regenerated the tags of 6 tracks and the embeddings of 6 tracks.",
            ),
            (
                (5943, 5900),
                (5943, 5943),
                "",
                "completed",
                "Regenerated the tags of 5,900 tracks and the embeddings of 5,943 tracks. "
                "43 tracks keep their previous tags.",
            ),
            # Every tagging request came back empty, without an error that stops the run.
            (
                (6, 0),
                (6, 6),
                "",
                "completed",
                "Regenerated the embeddings of 6 tracks. 6 tracks keep their previous tags.",
            ),
            (
                (1, 1),
                (1, 1),
                "",
                "completed",
                "Regenerated the tags of 1 track and the embeddings of 1 track.",
            ),
            # Tracks to regenerate and nothing written: never a success.
            (
                (3, 0),
                (3, 0),
                "",
                "failed",
                "No tags or embeddings were regenerated. 3 tracks keep their previous tags "
                "and embeddings.",
            ),
            (
                (6, 2),
                None,
                "tags",
                "failed",
                "AI tagging stopped. Regenerated the tags of 2 tracks. 4 tracks keep their "
                "previous tags, and 6 tracks keep their previous embeddings.",
            ),
            (
                (6, 0),
                None,
                "tags",
                "failed",
                "AI tagging stopped. No tags or embeddings were regenerated. 6 tracks keep "
                "their previous tags and embeddings.",
            ),
            (
                (1, 0),
                None,
                "tags",
                "failed",
                "AI tagging stopped. No tags or embeddings were regenerated. 1 track keeps "
                "its previous tags and embedding.",
            ),
            (
                (6, 6),
                (6, 5),
                "embeddings",
                "failed",
                "Embedding generation stopped. Regenerated the tags of 6 tracks and the "
                "embeddings of 5 tracks. 1 track keeps its previous embedding.",
            ),
        ],
    )
    def test_status_and_message(
        self,
        tags: tuple[int, int],
        embeddings: Optional[tuple[int, int]],
        stopped: str,
        status: str,
        message: str,
    ) -> None:
        from plexmix.plex.sync import RegenerateResult, SyncOutcome
        from plexmix.ui.states.library_state import regenerate_sync_result

        tags_pass = _pass(*tags, finished=stopped != "tags")
        if embeddings is None:  # the embeddings pass waits for the tags: it never started
            embeddings_pass = _pass(tags[0], 0, started=False, finished=False)
        else:
            embeddings_pass = _pass(*embeddings, finished=stopped != "embeddings")
        result = RegenerateResult(
            tags=tags_pass,
            embeddings=embeddings_pass,
            stopped_scope=stopped,
            error=RuntimeError("boom") if stopped else None,
        )
        assert regenerate_sync_result(SyncOutcome(), result) == (status, message)

    def test_what_the_sync_kept_back_is_part_of_the_message(self) -> None:
        from plexmix.plex.sync import RegenerateResult, SyncOutcome
        from plexmix.ui.states.library_state import regenerate_sync_result

        done = RegenerateResult(tags=_pass(27, 27), embeddings=_pass(27, 27))
        unread = SyncOutcome(unread={"artists": 0, "albums": 0, "tracks": 3})
        assert regenerate_sync_result(unread, done) == (
            "completed",
            "Sync completed. 3 tracks could not be read and were kept. "
            "Regenerated the tags of 27 tracks and the embeddings of 27 tracks.",
        )
        # A failed task never reads "Sync failed: Sync completed."
        nothing = RegenerateResult(tags=_pass(2, 0), embeddings=_pass(2, 0))
        assert regenerate_sync_result(unread, nothing) == (
            "failed",
            "No tags or embeddings were regenerated. 2 tracks keep their previous tags and "
            "embeddings. 3 tracks could not be read and were kept.",
        )
        held = SyncOutcome(reason="threshold", held=100, library_total=120)
        assert regenerate_sync_result(held, nothing) == (
            "failed",
            "No tags or embeddings were regenerated. 2 tracks keep their previous tags and "
            "embeddings. Plex no longer lists 100 of 120 tracks. They were kept.",
        )

    def test_a_library_without_tracks_has_nothing_to_regenerate_and_nothing_failed(self) -> None:
        """Every pass reached its end with no track to do: not a failure, and not a success claim."""
        from plexmix.plex.sync import RegenerateResult, SyncOutcome
        from plexmix.ui.states import library_state as ls

        empty = RegenerateResult(tags=_pass(0, 0), embeddings=_pass(0, 0))
        assert ls.regenerate_sync_result(SyncOutcome(), empty) == (
            "completed",
            ls.REGENERATE_NO_TRACKS_MESSAGE,
        )
        # The tags pass alone is the same.
        assert ls.regenerate_sync_result(SyncOutcome(), RegenerateResult(tags=_pass(0, 0))) == (
            "completed",
            ls.REGENERATE_NO_TRACKS_MESSAGE,
        )
        # A pass that was stopped before it ended is no run with nothing to do.
        unfinished = RegenerateResult(tags=_pass(0, 0, finished=False), embeddings=_pass(0, 0))
        assert ls.regenerate_sync_result(SyncOutcome(), unfinished)[0] == "failed"

    @pytest.mark.parametrize("left_out,one", [(1, True), (5943, False)])
    def test_a_run_whose_final_rebuild_was_refused_is_not_a_clean_success(
        self, left_out: int, one: bool
    ) -> None:
        """Every pass reached its end, and the saved index was left as it is.

        Tags and embeddings were written, and the message says how many. But the
        search index was not updated, so the task is failed and says why first.
        """
        from plexmix.plex.sync import RegenerateResult, SyncOutcome
        from plexmix.ui.states import library_state as ls

        done = RegenerateResult(tags=_pass(6, 6), embeddings=_pass(6, 5), index_left_out=left_out)
        assert done.finished and not done.provider_failed and not done.wrote_nothing

        status, message = ls.regenerate_sync_result(SyncOutcome(), done)

        assert status == "failed"
        holds = "1 track holds" if one else "5,943 tracks hold"
        assert message.startswith(
            f"{ls.INDEX_NOT_UPDATED_TITLE}. {holds} an embedding that was made with other "
            "embedding settings, so a new index would leave "
        )
        assert message.endswith(
            "Regenerated the tags of 6 tracks and the embeddings of 5 tracks. "
            "1 track keeps its previous embedding."
        )
        # The settings tell which way out is real: with a saved index of another size
        # the words are the readiness copy for the mismatch.
        assert "Regenerate sync on the Library page" in message

    def test_a_stand_in_engine_claims_nothing(self) -> None:
        from plexmix.plex.sync import RegenerateResult, SyncOutcome
        from plexmix.ui.states.library_state import regenerate_sync_result

        nothing = ("failed", "No tags or embeddings were regenerated.")
        assert regenerate_sync_result(SyncOutcome(), MagicMock()) == nothing
        assert regenerate_sync_result(SyncOutcome(), None) == nothing
        assert regenerate_sync_result(SyncOutcome(), RegenerateResult()) == nothing  # no run

    def test_a_run_that_did_not_reach_its_end_is_not_a_success(self) -> None:
        from plexmix.plex.sync import RegenerateResult, SyncOutcome
        from plexmix.ui.states import library_state as ls

        result = RegenerateResult(tags=_pass(6, 6), embeddings=_pass(6, 2, finished=False))
        status, message = ls.regenerate_sync_result(SyncOutcome(), result)
        assert status == "failed" and message.startswith(ls.REGENERATE_UNFINISHED_MESSAGE)
        assert message.endswith("4 tracks keep their previous embeddings.")

    def test_sync_secrets_cover_the_embedding_key(self, data_dir: Path) -> None:
        from plexmix.config.settings import Settings
        from plexmix.ui.states.library_state import _sync_secrets

        write_config(
            data_dir,
            "embedding:\n  default_provider: custom\n  custom_model: m\n"
            f"  custom_endpoint: http://127.0.0.1:9/v1\n  custom_api_key: {DUMMY_KEY}\n",
        )
        assert DUMMY_KEY in _sync_secrets(Settings.load_from_file())


class _Answers:
    """An AI provider that answers the provider check."""

    def complete(self, prompt: str, **options: Any) -> str:
        return "OK"


class _Raises:
    """A provider whose every request raises ``error``."""

    provider_name = "openai"

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def complete(self, prompt: str, **options: Any) -> str:
        raise self.error

    def generate_embedding(self, text: str) -> list[float]:
        raise self.error


AUDIO_ON_SYNC = "audio:\n  enabled: true\n  analyze_on_sync: true\n  workers: 1\n"
CUSTOM_EMBEDDINGS = (
    "embedding:\n  default_provider: custom\n  custom_endpoint: http://127.0.0.1:9/v1\n"
    "  custom_model: standin\n  custom_dimension: 2\n"
)
EARLIER_VECTOR = [0.1, 0.2, 0.3]


class CustomEmbedder(FakeEmbedder):
    """The fake generator as the "custom" provider (2 dimensions)."""

    provider_name = "custom"


class KeylessEmbedder(FakeEmbedder):
    """A generator that keeps its key where the engine cannot find it (as Gemini and Cohere do)."""

    api_key = None
    provider = None


def make_earlier_library(data: Path, tracks: int, model: str) -> list[int]:
    """A library embedded before the provider was changed, with the index of that time.

    Every track has a row of ``model`` with 3 numbers, and the saved index holds
    those vectors. The settings name a provider that makes 2 numbers now.
    """
    from plexmix.database.models import Album, Artist, Embedding, Track
    from plexmix.database.sqlite_manager import SQLiteManager
    from plexmix.database.vector_index import VectorIndex

    with SQLiteManager(str(data / "plexmix.db")) as db:
        artist = db.insert_artist(Artist(plex_key="a1", name="Artist"))
        album = db.insert_album(Album(plex_key="al1", title="Album", artist_id=artist))
        ids = [
            db.insert_track(
                Track(
                    plex_key=f"t{i}",
                    title=f"Song {i:03d}",
                    artist_id=artist,
                    album_id=album,
                    tags=f"old-{i}",
                )
            )
            for i in range(tracks)
        ]
        for track_id in ids:
            db.insert_embedding(
                Embedding(
                    track_id=track_id,
                    embedding_model=model,
                    embedding_dim=3,
                    vector=list(EARLIER_VECTOR),
                    updated_at=datetime(2020, 1, 1, 9, 0, 0),
                )
            )
    index = VectorIndex(dimension=3, index_path=str(data / "embeddings.index"))
    index.build_index([list(EARLIER_VECTOR) for _ in ids], ids)
    index.save_index(str(data / "embeddings.index"))
    return ids


class PlexWithFiles(FakePlex):
    """The same tracks, each with a file path: audio analysis needs one."""

    def get_all_tracks(self, batch_size: int = 100) -> Iterator[list[Any]]:
        for batch in super().get_all_tracks(batch_size):
            for track in batch:
                track.file_path = f"/music/{track.plex_key}.flac"
            yield batch


class _AudioFeatures:
    def to_dict(self) -> dict[str, Any]:
        return {
            "tempo": 120.0,
            "key": "C",
            "scale": "major",
            "energy_level": "high",
            "danceability": 0.8,
        }


class FakeAnalyzer:
    """Stands in for the Essentia analyzer: the same features for every file."""

    def analyze(self, path: str, duration_limit: int = 60) -> _AudioFeatures:
        return _AudioFeatures()


def spy_poll(state: Any) -> tuple[Any, list[str]]:
    """One poll; returns its toast and which of the three refreshes it ran."""
    from plexmix.ui.states import library_state as ls

    called: list[str] = []
    with patch.object(ls.rx, "toast", toasts()), patch.object(
        state, "check_configuration_status", lambda: called.append("status")
    ), patch.object(state, "load_library_stats", lambda: called.append("stats")), patch.object(
        state, "load_tracks", lambda: called.append("tracks")
    ):
        return state.poll_task_progress(), called


class TestRegenerateSync:
    """The Library "Regenerate" sync: checked first, in place, and honest about its end."""

    def test_regenerate_sync_is_refused_without_an_ai_provider(self, data_dir: Path) -> None:
        """Spec "Library regenerate without an AI provider": nothing changes, shown as failed."""
        write_config(data_dir, REGENERATE_CONFIG)  # OpenAI, and no key is stored
        make_old_library(data_dir, 6)
        before = stored_state(data_dir)
        state = library()
        entry = run_refused(state, FakePlex(range(6)))

        assert entry.status == "failed"
        assert entry.message == (
            "The AI provider OpenAI is not ready: no API key is stored. Nothing was changed."
        )
        assert TAGGING_SKIPPED not in entry.message  # never "completed without tagging"
        assert stored_state(data_dir) == before
        # The engine never ran: no history row and no run marker.
        assert query(data_dir, "SELECT COUNT(*) FROM sync_history")[0][0] == 0
        assert not marker_file(data_dir).exists()
        # Shown as a failure, in the toast and inline.
        assert poll(state) == ("error", f"Sync failed: {entry.message}")
        assert (state.sync_error, state.sync_error_body) == ("Sync failed", entry.message)
        assert state.is_syncing is False

    @pytest.mark.parametrize(
        "case,component,cause,message",
        [
            (
                "no ai key",
                "ai",
                "missing_api_key",
                "The AI provider OpenAI is not ready: no API key is stored.",
            ),
            (
                "ai key rejected",
                "ai",
                "invalid_credentials",
                "The AI provider OpenAI is not ready: it did not answer the test request. "
                "OpenAI rejected the API key.",
            ),
            (
                "embedding provider unreachable",
                "embeddings",
                "unreachable",
                "The embedding provider OpenAI is not ready: it did not answer the test "
                "request. Can't reach OpenAI.",
            ),
            (
                "embedding provider cannot start",
                "embeddings",
                "missing_dependency",
                "The embedding provider OpenAI is not ready: it could not be started. "
                "OpenAI needs a package that isn't installed.",
            ),
        ],
    )
    def test_regenerate_refusal_records_the_provider_as_failing(
        self,
        data_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        case: str,
        component: str,
        cause: str,
        message: str,
    ) -> None:
        from plexmix.config.settings import Settings
        from plexmix.services import readiness as rd
        from plexmix.services import regenerate_service as rs
        from plexmix.services.providers import ProviderFailure

        write_config(data_dir, REGENERATE_CONFIG)
        make_old_library(data_dir, 4)
        before = stored_state(data_dir)
        if case != "no ai key":
            monkeypatch.setenv("OPENAI_API_KEY", DUMMY_KEY)
            ai: Any = _Answers()
            built: tuple[Any, Any] = (FakeEmbedder(), None)
            if case == "ai key rejected":
                ai = _Raises(RuntimeError(f"Error code: 401 - Incorrect API key: {DUMMY_KEY}"))
            elif case == "embedding provider unreachable":
                built = (_Raises(ConnectionError(f"Connection refused ({DUMMY_KEY})")), None)
            else:
                failure = ProviderFailure(
                    rd.Cause.MISSING_DEPENDENCY,
                    "openai",
                    package="openai",
                    detail="openai not installed. Run: pip install openai",
                )
                built = (None, failure)
            monkeypatch.setattr(rs, "build_ai_provider", lambda *args, **kwargs: ai)
            monkeypatch.setattr(rs, "build_embedding_generator_with_reason", lambda s: built)
        caplog.set_level(logging.DEBUG)
        state = library()
        entry = run_refused(state, FakePlex(range(4)))

        assert entry.status == "failed"
        assert entry.message == f"{message} Nothing was changed."
        assert stored_state(data_dir) == before
        # The refused provider is failing with the cause, for this configuration.
        record = readiness_store.get(component)
        assert record is not None
        assert (record.level, record.cause) == (rd.Level.FAILING, rd.Cause(cause))
        assert record.fingerprint == rd.current_fingerprint(component, Settings.load_from_file())
        # The other provider was not proved either way.
        assert readiness_store.get("embeddings" if component == "ai" else "ai") is None
        # No key and no terminal command, on screen or in the log.
        for text in (entry.message, record.message, caplog.text):
            assert DUMMY_KEY not in text
        assert "pip install" not in entry.message and "Run:" not in entry.message

    def test_provider_check_runs_off_the_event_loop_and_says_so(self, data_dir: Path) -> None:
        """The check sends requests and can load a model: an executor does it, after Plex."""
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, REGENERATE_CONFIG)  # no key: the real check refuses at once
        make_old_library(data_dir, 2)
        real_check = ls.check_regenerate_providers
        shown: list[str] = []

        def checking(settings: Any) -> Any:
            entry = task_store.get("sync")
            shown.append(entry.message if entry is not None else "")
            return real_check(settings)

        state = library()
        state.sync_mode = "regenerate"
        with patch.object(ls, "check_regenerate_providers", checking), patch(
            "plexmix.services.sync_service.connect_plex", lambda settings: FakePlex(range(2))
        ):
            handed_over = run_spying(state.start_sync())

        assert handed_over == [ls._connect_plex_for_sync, checking]  # and no engine run
        assert shown == [ls.REGENERATE_CHECKING_MESSAGE]
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "failed"

    def test_regenerate_sync_passes_the_embedding_generator(
        self, data_dir: Path, small_batches: None
    ) -> None:
        """The embeddings are replaced in the same job, by the generator that passed the check."""
        from plexmix.database.vector_index import VectorIndex
        from plexmix.plex.sync import SyncEngine

        write_config(data_dir, REGENERATE_CONFIG)
        ids = make_old_library(data_dir, 6)
        tagger, embedder = FakeTagger(), FakeEmbedder()
        built: list[dict[str, Any]] = []
        runs: list[dict[str, Any]] = []
        real_init, real_run = SyncEngine.__init__, SyncEngine.regenerate_sync

        def spy_init(self: Any, *args: Any, **kwargs: Any) -> None:
            built.append(kwargs)
            real_init(self, *args, **kwargs)

        def spy_run(self: Any, **kwargs: Any) -> Any:
            runs.append(kwargs)
            return real_run(self, **kwargs)

        state = library()
        with patch.object(SyncEngine, "__init__", spy_init), patch.object(
            SyncEngine, "regenerate_sync", spy_run
        ):
            entry = run_regenerate(state, FakePlex(range(6)), tagger, embedder)

        assert built[0]["ai_provider"] is tagger
        assert built[0]["embedding_generator"] is embedder
        index = built[0]["vector_index"]
        assert isinstance(index, VectorIndex) and index.dimension == 2
        assert runs[0]["generate_embeddings"] is True

        assert entry.status == "completed"
        assert entry.message == "Regenerated the tags of 6 tracks and the embeddings of 6 tracks."
        assert tags_of(data_dir) == {track_id: f"new-{track_id}" for track_id in ids}
        assert vectors_of(data_dir) == {track_id: NEW_VECTOR for track_id in ids}
        assert embedder.embedded == 6
        # Each embedding text was made after the track's new tags were saved.
        texts = [text for request in embedder.requests for text in request]
        assert all("new-" in text and "old-" not in text for text in texts)
        assert index_ids(data_dir) == ids
        assert poll(state) == ("success", entry.message)
        assert state.sync_error == ""

    def test_regenerate_sync_always_starts_a_new_run(
        self, data_dir: Path, small_batches: None
    ) -> None:
        """Spec "Web UI action after an interrupted run": every track, never only the rest."""
        from plexmix.config.settings import Settings
        from plexmix.database.sqlite_manager import SQLiteManager
        from plexmix.plex.sync import SyncEngine
        from plexmix.services import regenerate_service as rs

        write_config(data_dir, REGENERATE_CONFIG)
        ids = make_old_library(data_dir, 6)
        # An earlier run that stopped after three tracks: its marker, and their newer tags.
        marker_file(data_dir).write_text(
            json.dumps(
                {
                    "tags": {
                        "started_at": "2024-01-01T00:00:00Z",
                        "provider": "openai",
                        "model": "gpt-5-mini",
                    }
                }
            ),
            encoding="utf-8",
        )
        connection = sqlite3.connect(str(data_dir / "plexmix.db"))
        connection.executemany(
            "UPDATE tracks SET tags_generated_at = '2025-01-01 00:00:00' WHERE id = ?",
            [(track_id,) for track_id in ids[:3]],
        )
        connection.commit()
        connection.close()
        settings = Settings.load_from_file()
        with SQLiteManager(str(data_dir / "plexmix.db")) as db:
            earlier = rs.preview_run(db, rs.SCOPE_TAGS, rs.run_provider(settings, rs.SCOPE_TAGS))
        # A run that continues would send only the three tracks that are left.
        assert earlier.continued is True and list(earlier.track_ids) == ids[3:]

        runs: list[dict[str, Any]] = []
        real_run = SyncEngine.regenerate_sync

        def spy_run(self: Any, **kwargs: Any) -> Any:
            runs.append(kwargs)
            return real_run(self, **kwargs)

        tagger, embedder = FakeTagger(), FakeEmbedder()
        with patch.object(SyncEngine, "regenerate_sync", spy_run):
            entry = run_regenerate(library(), FakePlex(range(6)), tagger, embedder)

        assert runs[0]["continue_run"] is False
        assert tagger.sent == ids and embedder.embedded == 6
        assert entry.status == "completed"
        # The run is named as the settings name it, so the marker of a run that is
        # cancelled is one the next regenerate recognises.
        assert runs[0]["tags_provider"] == rs.run_provider(settings, rs.SCOPE_TAGS)
        assert runs[0]["embeddings_provider"] == rs.run_provider(settings, rs.SCOPE_EMBEDDINGS)
        assert not marker_file(data_dir).exists()  # a finished run leaves none

    def test_regenerate_sync_records_embedding_readiness(
        self, data_dir: Path, small_batches: None
    ) -> None:
        """A run that wrote rows proves both providers, for the configuration it ran with."""
        from plexmix.config.settings import Settings
        from plexmix.services import readiness as rd

        write_config(data_dir, REGENERATE_CONFIG)
        make_old_library(data_dir, 4)
        entry = run_regenerate(library(), FakePlex(range(4)), FakeTagger(), FakeEmbedder())

        assert entry.status == "completed"
        settings = Settings.load_from_file()
        for component in ("ai", "embeddings"):
            record = readiness_store.get(component)
            assert record is not None and record.level == rd.Level.READY, component
            assert record.fingerprint == rd.current_fingerprint(component, settings)

    def test_embedding_provider_failure_part_way_fails_and_keeps_every_embedding(
        self, data_dir: Path, small_batches: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec "Provider fails part-way": each track has its new or its previous embedding."""
        from plexmix.services import readiness as rd

        write_config(data_dir, REGENERATE_CONFIG)
        monkeypatch.setenv("OPENAI_API_KEY", DUMMY_KEY)
        ids = make_old_library(data_dir, 6)
        embedder = FakeEmbedder(
            fail_on=2, error=ConnectionError(f"Connection refused ({DUMMY_KEY})")
        )
        state = library()
        entry = run_regenerate(state, FakePlex(range(6)), FakeTagger(), embedder)

        assert entry.status == "failed"
        assert entry.message == (
            "Embedding generation stopped: Can't reach OpenAI. Regenerated the tags of "
            "6 tracks and the embeddings of 2 tracks. 4 tracks keep their previous embeddings."
        )
        vectors = vectors_of(data_dir)
        assert sorted(vectors) == ids  # no track is left without an embedding
        assert sorted(vectors.values()) == [OLD_VECTOR] * 4 + [NEW_VECTOR] * 2
        assert index_ids(data_dir) == ids  # the index follows the rows, new and previous
        assert readiness_of("embeddings") == (rd.Level.FAILING, rd.Cause.UNREACHABLE)
        assert readiness_of("ai") == (rd.Level.READY, None)  # the tags were written
        assert poll(state) == ("error", f"Sync failed: {entry.message}")
        assert state.sync_error_body == entry.message

    def test_ai_provider_failure_part_way_fails_and_leaves_the_embeddings(
        self, data_dir: Path, small_batches: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The embeddings wait for the tags: after a stopped tags pass none is replaced."""
        from plexmix.services import readiness as rd

        write_config(data_dir, REGENERATE_CONFIG)
        monkeypatch.setenv("OPENAI_API_KEY", DUMMY_KEY)
        ids = make_old_library(data_dir, 6)
        tagger = FakeTagger(
            fail_on=2, error=RuntimeError(f"Error code: 401 - Incorrect API key: {DUMMY_KEY}")
        )
        embedder = FakeEmbedder()
        entry = run_regenerate(library(), FakePlex(range(6)), tagger, embedder)

        assert entry.status == "failed"
        assert entry.message == (
            "AI tagging stopped: OpenAI rejected the API key. Regenerated the tags of "
            "2 tracks. 4 tracks keep their previous tags, and 6 tracks keep their "
            "previous embeddings."
        )
        tags = tags_of(data_dir)
        assert sum(tag.startswith("new-") for tag in tags.values()) == 2
        assert sum(tag.startswith("old-") for tag in tags.values()) == 4
        assert embedder.requests == []
        assert vectors_of(data_dir) == {track_id: OLD_VECTOR for track_id in ids}
        assert readiness_of("ai") == (rd.Level.FAILING, rd.Cause.INVALID_CREDENTIALS)
        assert readiness_of("embeddings") == (None, None)

    def test_regenerate_sync_states_how_many_tracks_keep_their_previous_tags(
        self, data_dir: Path, small_batches: None
    ) -> None:
        """Spec "Provider returns nothing usable for a track": it keeps what it had."""
        write_config(data_dir, REGENERATE_CONFIG)
        ids = make_old_library(data_dir, 6)
        state = library()
        entry = run_regenerate(state, FakePlex(range(6)), FakeTagger(omit=[ids[4]]), FakeEmbedder())

        assert entry.status == "completed"
        assert entry.message == (
            "Regenerated the tags of 5 tracks and the embeddings of 6 tracks. "
            "1 track keeps its previous tags."
        )
        assert stored_tags(data_dir, ids[4]) == {
            "tags": "old-4",
            "environments": "old-room",
            "instruments": "old-horn",
        }
        assert poll(state) == ("success", entry.message)

    @pytest.mark.parametrize("during", ["tags", "embeddings"])
    def test_cancelled_regenerate_sync_keeps_old_and_new(
        self, data_dir: Path, small_batches: None, during: str
    ) -> None:
        """Spec "Cancelled part-way": every track has its new or its previous tags and embedding."""
        from plexmix.services import readiness as rd

        write_config(data_dir, REGENERATE_CONFIG)
        ids = make_old_library(data_dir, 6)

        def cancel(request: int) -> None:
            if request == 1:  # Cancel arrives while the first batch is being answered
                task_store.cancel("sync")

        tagger = FakeTagger(before=cancel if during == "tags" else None)
        embedder = FakeEmbedder(before=cancel if during == "embeddings" else None)
        state = library()
        entry = run_regenerate(state, FakePlex(range(6)), tagger, embedder)

        assert entry.status == "cancelled"
        tags, vectors = tags_of(data_dir), vectors_of(data_dir)
        # No track lost its tags or its embedding.
        assert sorted(tags) == ids and all(tags.values())
        assert sorted(vectors) == ids
        new_tags = sum(tag.startswith("new-") for tag in tags.values())
        new_vectors = sum(vector == NEW_VECTOR for vector in vectors.values())
        if during == "tags":
            # The batch that was under way was saved, and the embeddings were not started.
            assert (new_tags, new_vectors) == (2, 0)
            assert embedder.requests == []
        else:
            assert (new_tags, new_vectors) == (6, 2)
            assert readiness_of("embeddings") == (rd.Level.READY, None)
        assert all(vector in (OLD_VECTOR, NEW_VECTOR) for vector in vectors.values())
        assert index_ids(data_dir) == ids
        assert readiness_of("ai") == (rd.Level.READY, None)
        # The toast says what the run had replaced by then, and what was kept.
        if during == "tags":
            said = (
                "Sync cancelled. Regenerated the tags of 2 tracks. 4 tracks keep their "
                "previous tags, and 6 tracks keep their previous embeddings."
            )
        else:
            said = (
                "Sync cancelled. Regenerated the tags of 6 tracks and the embeddings of "
                "2 tracks. 4 tracks keep their previous embeddings."
            )
        assert poll(state) == ("warning", said)

    @pytest.mark.parametrize("earlier", ["another provider", "another vector size"])
    @pytest.mark.parametrize("stop", ["provider error", "cancel"])
    def test_stopped_regenerate_sync_after_a_provider_change_keeps_the_saved_index(
        self, data_dir: Path, small_batches: None, earlier: str, stop: str
    ) -> None:
        """Spec: no regenerate action clears the search index before its replacement exists.

        The saved index was made before the embedding provider (or its vector size)
        was changed, and it covers every track. The run is stopped after one batch.
        An index of those two tracks must not take the place of the saved one: with
        it would go the dimension mismatch that makes every status surface say that
        the embeddings must be regenerated.
        """
        from plexmix.database.vector_index import VectorIndex

        write_config(data_dir, PLEX_CONFIG + OPENAI_AI + CUSTOM_EMBEDDINGS)
        # Rows of another provider, or rows of this one with the size it made before.
        ids = make_earlier_library(
            data_dir, 6, "cohere" if earlier == "another provider" else "custom"
        )
        before = stored_state(data_dir)

        def cancel(request: int) -> None:
            if request == 1:
                task_store.cancel("sync")

        embedder = CustomEmbedder(before=cancel) if stop == "cancel" else CustomEmbedder(fail_on=2)
        state = library()
        entry = run_regenerate(state, FakePlex(range(6)), FakeTagger(), embedder)

        assert entry.status == ("cancelled" if stop == "cancel" else "failed")
        after = stored_state(data_dir)
        # The index files are the ones from before the run, byte for byte.
        assert after["files"] == before["files"]
        assert sorted(after["files"]) == ["embeddings.index", "embeddings.metadata"]
        opened = VectorIndex(dimension=2, index_path=str(data_dir / "embeddings.index"))
        assert sorted(opened.track_ids) == ids and opened.dimension_mismatch is True
        # Every track still has an embedding, and two have a new one.
        rows = query(
            data_dir,
            "SELECT track_id, embedding_model, embedding_dim FROM embeddings "
            "ORDER BY track_id, embedding_model",
        )
        assert sorted({row[0] for row in rows}) == ids
        assert [row for row in rows if row[1:] == ("custom", 2)] == [
            (ids[0], "custom", 2),
            (ids[1], "custom", 2),
        ]
        assert len(rows) == (8 if earlier == "another provider" else 6)

    def test_finished_regenerate_sync_after_a_provider_change_replaces_the_index(
        self, data_dir: Path, small_batches: None
    ) -> None:
        """The run that reaches its end writes the replacement, and only then."""
        from plexmix.database.vector_index import VectorIndex

        write_config(data_dir, PLEX_CONFIG + OPENAI_AI + CUSTOM_EMBEDDINGS)
        ids = make_earlier_library(data_dir, 6, "cohere")

        entry = run_regenerate(library(), FakePlex(range(6)), FakeTagger(), CustomEmbedder())

        assert entry.status == "completed"
        opened = VectorIndex(dimension=2, index_path=str(data_dir / "embeddings.index"))
        assert sorted(opened.track_ids) == ids and opened.dimension_mismatch is False
        assert query(data_dir, "SELECT COUNT(*) FROM embeddings WHERE embedding_model = 'cohere'")[
            0
        ] == (0,)

    def test_regenerate_sync_without_any_new_tag_makes_no_embedding(
        self, data_dir: Path, small_batches: None
    ) -> None:
        """The AI provider answers and gives nothing usable for any track.

        The embeddings are left alone: each would be made again from the text it
        was made from. The task fails and says which of the two it was.
        """
        write_config(data_dir, REGENERATE_CONFIG)
        ids = make_old_library(data_dir, 4)
        before = stored_state(data_dir)
        tagger, embedder = FakeTagger(omit=ids), FakeEmbedder()

        entry = run_regenerate(library(), FakePlex(range(4)), tagger, embedder)

        assert entry.status == "failed"
        assert tagger.sent == ids and embedder.requests == []
        assert stored_state(data_dir) == before
        assert entry.message == (
            "The AI provider returned no tags, so the embeddings were left as they are. "
            "No tags or embeddings were regenerated. "
            "4 tracks keep their previous tags and embeddings."
        )

    def test_provider_that_stops_answering_fails_the_sync_and_leaves_the_embeddings(
        self, data_dir: Path, small_batches: None
    ) -> None:
        """Every tagging request after the first is refused, which is not fatal by itself.

        After three in a row the run stops: it does not call its tags finished and
        go on to make every embedding from tags that were not replaced.
        """
        from plexmix.services import readiness as rd

        write_config(data_dir, REGENERATE_CONFIG)
        ids = make_old_library(data_dir, 12)
        vectors_before = vectors_of(data_dir)
        refused = ConnectionError("Connection refused")

        class Tagger(FakeTagger):
            def complete(self, prompt: str, **options: Any) -> str:
                if len(self.requests) >= 1:
                    self.requests.append(_ids_in_prompt(prompt))
                    raise refused
                return super().complete(prompt, **options)

        tagger, embedder = Tagger(), FakeEmbedder()
        entry = run_regenerate(library(), FakePlex(range(12)), tagger, embedder)

        assert entry.status == "failed"
        assert entry.message.startswith("AI tagging stopped: ")
        assert "Regenerated the tags of 2 tracks." in entry.message
        # One answered request and three refused ones. The last two were never sent.
        assert tagger.sent == ids[:8]
        assert embedder.requests == [] and vectors_of(data_dir) == vectors_before
        level, cause = readiness_of("ai")
        assert level == rd.Level.FAILING and cause == rd.Cause.UNREACHABLE

    def test_regenerate_sync_analyses_audio_before_it_embeds(
        self, data_dir: Path, small_batches: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With "analyze on sync" on, the new embeddings hold the audio features.

        The order is tags, audio, embeddings. This mode makes its embeddings inside
        the engine run, so the audio analysis has to happen there too: the handler's
        own audio pass runs after the engine and would be too late for them.
        """
        import sys
        import types

        write_config(data_dir, REGENERATE_CONFIG + AUDIO_ON_SYNC)
        make_old_library(data_dir, 4)
        fake = types.ModuleType("plexmix.audio.analyzer")
        fake.EssentiaAnalyzer = FakeAnalyzer  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "plexmix.audio.analyzer", fake)
        embedder = FakeEmbedder()

        entry = run_regenerate(library(), PlexWithFiles(range(4)), FakeTagger(), embedder)

        assert entry.status == "completed", entry.message
        assert query(data_dir, "SELECT COUNT(*) FROM audio_features")[0] == (4,)
        texts = [text for request in embedder.requests for text in request]
        assert len(texts) == 4
        assert all("| audio:" in text for text in texts), texts[0]

    def test_other_sync_modes_keep_their_own_audio_pass(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A plain sync makes no embeddings, and analyses the audio after the engine."""
        import sys
        import types

        from plexmix.plex.sync import SyncEngine

        write_config(data_dir, REGENERATE_CONFIG + AUDIO_ON_SYNC)
        make_old_library(data_dir, 4)
        fake = types.ModuleType("plexmix.audio.analyzer")
        fake.EssentiaAnalyzer = FakeAnalyzer  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "plexmix.audio.analyzer", fake)
        built: list[Any] = []
        real_init = SyncEngine.__init__

        def spy(self: Any, *args: Any, **kwargs: Any) -> None:
            built.append(kwargs.get("audio_settings"))
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(SyncEngine, "__init__", spy)

        entry = run_sync(library(), PlexWithFiles(range(4)))

        assert entry.status == "completed", entry.message
        assert built == [None]
        assert query(data_dir, "SELECT COUNT(*) FROM audio_features")[0] == (4,)

    @pytest.mark.parametrize("end", ["failed", "cancelled"])
    def test_tab_is_refreshed_after_a_regenerate_sync_that_did_not_finish(
        self, data_dir: Path, small_batches: None, end: str
    ) -> None:
        """Such a run has usually replaced tags and embeddings already.

        The table, the status and the counts must follow, as after a completed sync.
        """
        write_config(data_dir, REGENERATE_CONFIG)
        make_old_library(data_dir, 6)

        def cancel(request: int) -> None:
            if request == 1:
                task_store.cancel("sync")

        embedder = FakeEmbedder(before=cancel) if end == "cancelled" else FakeEmbedder(fail_on=2)
        state = library()
        entry = run_regenerate(state, FakePlex(range(6)), FakeTagger(), embedder)
        assert entry.status == end
        assert all(tag.startswith("new-") for tag in tags_of(data_dir).values())

        toast, refreshed = spy_poll(state)

        assert refreshed == ["tracks", "status", "stats"]
        assert toast[0] == ("warning" if end == "cancelled" else "error")
        assert "Regenerated the tags of 6 tracks and the embeddings of 2 tracks." in toast[1]

    def test_sync_that_failed_before_it_read_plex_refreshes_nothing(self, data_dir: Path) -> None:
        """A connection failure wrote nothing, so there is nothing to reload."""
        state = library()
        state.is_syncing = True
        task_store.start("sync", message="Starting sync...")
        task_store.update("sync", extra={"plex_error_code": "unreachable"})
        task_store.complete("sync", status="failed", message="Can't reach Plex")

        toast, refreshed = spy_poll(state)

        assert refreshed == [] and toast[0] == "error"

    def test_plain_cancelled_sync_keeps_its_short_toast(self, data_dir: Path) -> None:
        state = library()
        state.is_syncing = True
        task_store.start("sync", message="Starting sync...")
        task_store.complete("sync", status="cancelled")

        toast, refreshed = spy_poll(state)

        assert toast == ("warning", "Sync cancelled")
        assert refreshed == ["tracks", "status", "stats"]

    def test_provider_error_text_with_the_key_reaches_neither_the_log_nor_the_history(
        self,
        data_dir: Path,
        small_batches: None,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The engine is handed the keys from the settings.

        Not every provider object keeps its key where the engine can find it, and a
        provider can repeat the key in its error text. That text goes into the log
        and into the sync history row.
        """
        write_config(data_dir, REGENERATE_CONFIG)
        monkeypatch.setenv("OPENAI_API_KEY", DUMMY_KEY)
        make_old_library(data_dir, 6)
        echo = f"Error code: 418 - upstream refused the credential {DUMMY_KEY} for this project"
        embedder = KeylessEmbedder(fail_on=2, error=RuntimeError(echo))
        state = library()

        with caplog.at_level(logging.DEBUG):
            entry = run_regenerate(state, FakePlex(range(6)), FakeTagger(), embedder)
        toast = poll(state)

        assert entry.status == "failed"
        history = query(data_dir, "SELECT status, error_message FROM sync_history ORDER BY id")
        assert history[-1][0] == "partial" and "[redacted]" in history[-1][1]
        leaks = [record.name for record in caplog.records if DUMMY_KEY in record.getMessage()]
        assert leaks == []
        for text in (entry.message, str(toast), state.sync_error_body, str(history)):
            assert DUMMY_KEY not in text

    def test_regenerate_sync_does_not_start_beside_another_embedding_writer(
        self, data_dir: Path, small_batches: None
    ) -> None:
        """The "embedding" task and Doctor's fixes write embeddings and save the index too."""
        from plexmix.ui.states import library_state as ls
        from plexmix.ui.states.doctor_state import FIX_JOB

        assert ls.DOCTOR_FIX_JOB == FIX_JOB
        write_config(data_dir, REGENERATE_CONFIG)
        make_old_library(data_dir, 4)
        before = stored_state(data_dir)

        for job in ("embedding", FIX_JOB):
            task_store.clear("sync")
            assert task_store.start(job, message="Working...") is not None
            try:
                state = library()
                tagger, embedder = FakeTagger(), FakeEmbedder()
                state.sync_mode = "regenerate"
                with patch(
                    "plexmix.services.sync_service.connect_plex",
                    lambda settings: FakePlex(range(4)),
                ):
                    run(state.start_sync())
            finally:
                task_store.clear(job)

            # No sync task was started, nothing was asked and nothing was changed.
            assert task_store.get("sync") is None and state.is_syncing is False
            assert tagger.requests == [] and embedder.requests == []
            assert stored_state(data_dir) == before
            assert state.sync_error == ls.REGENERATE_BUSY_TITLE
            assert state.sync_error_body.endswith("Nothing was changed.")
            assert state.show_regenerate_confirm is False

    def test_embedding_job_does_not_start_beside_a_regenerate_sync(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, REGENERATE_CONFIG)
        make_old_library(data_dir, 4)
        before = stored_state(data_dir)
        assert ls.regenerate_sync_running() is False
        task_store.start("sync", message="Starting sync...")
        # A sync of another mode is no reason to wait.
        assert ls.regenerate_sync_running() is False
        task_store.update("sync", extra={ls.REGENERATE_RUN_KEY: True})
        assert ls.regenerate_sync_running() is True
        try:
            with patch.object(ls, "run_embedding_job") as job:
                run(library()._run_embedding_job(None))
        finally:
            task_store.clear("sync")

        job.assert_not_called()
        entry = task_store.get("embedding")
        assert entry is not None and entry.status == "failed"
        assert entry.message.startswith("A Regenerate sync is running.")
        assert stored_state(data_dir) == before

    def test_regenerate_sync_marks_its_task_while_it_runs(
        self, data_dir: Path, small_batches: None
    ) -> None:
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, REGENERATE_CONFIG)
        make_old_library(data_dir, 4)
        seen: list[bool] = []
        tagger = FakeTagger(before=lambda request: seen.append(ls.regenerate_sync_running()))

        entry = run_regenerate(library(), FakePlex(range(4)), tagger, FakeEmbedder())

        assert entry.status == "completed" and seen == [True, True]
        # Not once it has ended.
        assert ls.regenerate_sync_running() is False

    def test_regenerate_sync_that_plex_stops_changes_nothing(self, data_dir: Path) -> None:
        """Nothing is cleared first, so "nothing was changed" is true without an addition.

        Until the engine regenerated in place, this mode had cleared the tags and
        embeddings before it read Plex, and the failure said so in a second sentence.
        """
        from plexmix.plex.client import PlexReadInterrupted
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, REGENERATE_CONFIG)
        make_old_library(data_dir, 6)
        before = stored_state(data_dir)
        tagger, embedder = FakeTagger(), FakeEmbedder()
        plex = FakePlex(range(6), read_error=PlexReadInterrupted("tracks", 3, 6))
        state = library()
        entry = run_regenerate(state, plex, tagger, embedder)

        sentence = "Plex stopped answering after 3 of 6 tracks were read, so nothing was changed."
        assert entry.status == "failed" and entry.message == sentence
        assert stored_state(data_dir) == before
        assert tagger.requests == [] and embedder.requests == []
        assert not hasattr(ls, "REGENERATE_CLEARED_MESSAGE")
        # The toast and the inline failure carry the same text.
        assert poll(state) == ("error", f"Sync failed: {sentence}")
        assert state.sync_error_body == sentence
        # And so does the history row.
        assert query(
            data_dir, "SELECT status, error_message FROM sync_history ORDER BY id DESC LIMIT 1"
        ) == [("failed", sentence)]

    def test_other_sync_modes_build_no_embedding_provider(self, data_dir: Path) -> None:
        """Only "Regenerate" checks the providers and embeds: the other modes are as before."""
        write_config(data_dir, REGENERATE_CONFIG)
        make_old_library(data_dir, 3)
        before = stored_state(data_dir)
        state = library()
        with patch("plexmix.services.regenerate_service.check_providers") as check:
            entry = run_sync(state, FakePlex(range(3)))  # incremental, no AI provider

        assert check.call_count == 0
        assert entry.status == "completed" and entry.message == TAGGING_SKIPPED
        assert stored_state(data_dir) == before
        assert readiness_of("embeddings") == (None, None)

    def test_run_whose_final_rebuild_is_refused_is_not_shown_as_a_clean_success(
        self, data_dir: Path, small_batches: None
    ) -> None:
        """Every pass reaches its end, and one track keeps the embedding of the earlier provider.

        The provider answers one request with a vector too few. An index of the other
        five would take the place of the library's index, so none is saved: the files
        stay as they are, byte for byte, and the sync is not a success.
        """
        from plexmix.ui.states import library_state as ls

        class OneShort(CustomEmbedder):
            def generate_batch_embeddings(
                self, texts: list[str], batch_size: int = 100
            ) -> list[list[float]]:
                made = super().generate_batch_embeddings(texts, batch_size)
                return made[:-1] if len(self.requests) == 2 else made

        write_config(data_dir, PLEX_CONFIG + OPENAI_AI + CUSTOM_EMBEDDINGS)
        ids = make_earlier_library(data_dir, 6, "cohere")
        before = stored_state(data_dir)["files"]
        state = library()

        entry = run_regenerate(state, FakePlex(range(6)), FakeTagger(), OneShort())

        assert entry.status == "failed"
        assert entry.message.startswith(f"{ls.INDEX_NOT_UPDATED_TITLE}. 1 track holds an embedding")
        assert "Rebuild the embeddings" in entry.message  # the mismatch's own words
        assert entry.message.endswith(
            "Regenerated the tags of 6 tracks and the embeddings of 5 tracks. "
            "1 track keeps its previous embedding."
        )
        assert not entry.message.startswith("Sync completed")
        assert stored_state(data_dir)["files"] == before
        assert_saved_index_still_mismatched(data_dir, ids)
        # Five tracks hold their new embedding, and the sixth the one it had.
        assert embedding_sizes(data_dir) == {
            **dict.fromkeys(ids, 3),
            **dict.fromkeys([i for i in ids if i != ids[3]], 2),
        }
        assert all(tag.startswith("new-") for tag in tags_of(data_dir).values())
        # The run is over, and the history row says it ended with a warning.
        status, error = query(data_dir, "SELECT status, error_message FROM sync_history")[0]
        assert status == "partial" and "The search index was left as it is" in error
        # The track the provider left out is still to do, so the embeddings entry of
        # the marker stays: the CLI's regenerate command continues with that track
        # only. (This action itself never continues an earlier run.)
        assert set(json.loads(marker_file(data_dir).read_text())) == {"embeddings"}

        toast, _refreshed = spy_poll(state)
        assert toast == ("error", f"Sync failed: {entry.message}")

    def test_regenerate_that_the_provider_check_refuses_keeps_a_waiting_removal(
        self, data_dir: Path
    ) -> None:
        """Nothing was compared with Plex, so the removal that waits still waits.

        The notice, the keys of the held tracks and the library they belong to are
        what the last sync left. "Review removal" removes them as it did before.
        """
        from plexmix.ui.states import library_state as ls

        write_config(data_dir, REGENERATE_CONFIG)  # OpenAI, and no key is stored
        make_library(data_dir, 120, embedded=120)
        plex = FakePlex(range(20), relisted=range(20))
        state = library()
        run_sync(state, plex)  # holds 100 of 120
        assert poll(state) == ("warning", HELD_MESSAGE)
        assert (state.removal_notice_count, state.removal_notice_total) == (100, 120)
        keys = ls.waiting_removal_keys(100, 120)
        scope = ls.waiting_removal_scope(100, 120)
        assert keys == frozenset(f"t{i}" for i in range(20, 120)) and scope != ""

        entry = run_refused(state, FakePlex(range(20), relisted=range(20)))

        assert entry.status == "failed"
        assert entry.message.startswith("The AI provider OpenAI is not ready")
        assert poll(state) == ("error", f"Sync failed: {entry.message}")
        # The notice, its keys and its library are what they were.
        assert (state.removal_notice_count, state.removal_notice_total) == (100, 120)
        assert ls.held_removal(entry) == (100, 120)
        assert ls.waiting_removal_keys(100, 120) == keys
        assert ls.waiting_removal_scope(100, 120) == scope
        # The task store drops a finished entry after an hour: the hold is remembered.
        forget_finished_sync()
        reopened = library()
        assert poll(reopened) is None
        assert (reopened.removal_notice_count, reopened.removal_notice_total) == (100, 120)

        reopened.open_removal_confirm()
        done = confirm(reopened, plex)
        tone, message = done.events[0]
        assert tone == "success" and message.startswith("Removed 100 tracks")
        assert track_count(data_dir) == 20
        assert (reopened.removal_notice_count, reopened.removal_notice_total) == (0, 0)

    @pytest.mark.parametrize("end", ["failed", "cancelled"])
    def test_a_caller_without_access_reloads_nothing_after_a_failed_or_cancelled_sync(
        self, data_dir: Path, end: str
    ) -> None:
        """The tracks, the status and the counts are not for a caller that is not signed in."""
        task_store.start("sync", message="Starting sync...")
        task_store.complete("sync", status=end, message="Stopped" if end == "failed" else "")
        stranger = library()
        stranger.is_syncing = True
        stranger.access_allowed = lambda: False  # type: ignore[method-assign]

        toast, refreshed = spy_poll(stranger)

        assert refreshed == []
        assert toast[0] == ("error" if end == "failed" else "warning")
        assert stranger.is_syncing is False  # the tab still learns that the sync ended

        # A caller that is signed in gets the reloads, so the check is the only difference.
        member = library()
        member.is_syncing = True
        _toast, reloaded = spy_poll(member)
        assert reloaded == ["tracks", "status", "stats"]


# ── Whole-library embedding from Library (spec scenario) ─────────────────


def embedding_sizes(data: Path) -> dict[int, int]:
    """The size of the vector of every track that has an embedding, by track id."""
    return {
        track_id: embedding_dim
        for track_id, embedding_dim in query(
            data, "SELECT track_id, embedding_dim FROM embeddings ORDER BY track_id"
        )
    }


def index_readiness() -> Any:
    """The readiness of the search index, read from the files and settings as they are now."""
    from plexmix.config.settings import Settings
    from plexmix.services import readiness as rd

    settings = Settings.load_from_file()
    return rd.evaluate(settings, rd.collect_facts(settings))["index"]


def assert_saved_index_still_mismatched(data: Path, ids: list[int]) -> None:
    """A fresh open of the saved index (3 numbers) still covers ``ids`` and reports the mismatch."""
    from plexmix.database.vector_index import VectorIndex

    opened = VectorIndex(dimension=2, index_path=str(data / "embeddings.index"))
    assert sorted(opened.track_ids) == sorted(ids)
    assert opened.dimension_mismatch is True


def use_embedder(embedder: Any) -> Any:
    """Hand ``embedder`` to the embedding job in place of a real provider."""
    return patch(
        "plexmix.services.providers.build_embedding_generator_with_reason",
        return_value=(embedder, None),
    )


class TestIndexLeftAsItIs:
    """Rows of two vector sizes under one model name: no job replaces the saved index.

    The provider ("custom") makes 2 numbers now, and every stored row and the saved
    index hold 3. A job that embeds a few tracks writes their rows, and an index of
    only those would take the place of the library's. So the saved files stay as
    they are, byte for byte, the job ends as failed, and it says that the search
    index was not updated, and what replaces the embeddings in the way.
    """

    def _earlier_library(self, data: Path, tracks: int = 6) -> list[int]:
        write_config(data, CUSTOM_EMBEDDINGS)
        return make_earlier_library(data, tracks, "custom")

    def test_embedding_job_for_one_track_leaves_the_saved_index(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        ids = self._earlier_library(data_dir)
        before = stored_state(data_dir)
        state = library()
        state.selected_tracks = [str(ids[0])]

        with use_embedder(CustomEmbedder()):
            run(state.generate_embeddings())

        entry = task_store.get("embedding")
        assert entry is not None and entry.status == "failed"
        # The new row is saved and the others keep theirs: no track lost its embedding.
        assert embedding_sizes(data_dir) == {ids[0]: 2, **dict.fromkeys(ids[1:], 3)}
        after = stored_state(data_dir)
        assert after["files"] == before["files"]  # byte for byte
        assert sorted(after["files"]) == ["embeddings.index", "embeddings.metadata"]
        assert_saved_index_still_mismatched(data_dir, ids)

        notice = entry.extra[ls.INDEX_NOTICE_KEY]
        assert entry.message == (
            "Generated embeddings for 1 track, but the search index was not updated. "
            f"{notice['body']}"
        )
        assert "5 tracks hold an embedding that was made with other embedding settings" in (
            notice["body"]
        )
        # The words of the mismatch every status surface uses, and its way out: Doctor.
        assert "Rebuild the embeddings" in notice["body"]
        assert (notice["title"], notice["action_label"], notice["action_href"]) == (
            ls.INDEX_NOT_UPDATED_TITLE,
            "Rebuild embeddings",
            "/doctor",
        )
        assert (notice["secondary_label"], notice["secondary_href"]) == (
            "Switch back",
            "/settings?section=embedding",
        )
        assert set(notice) == set(ls.INDEX_NOTICE_KEYS)

    def test_embedding_job_with_a_short_provider_reply_fails_like_the_doctor_fixes(
        self, data_dir: Path
    ) -> None:
        """The provider answers with one vector too few, in a library of two vector sizes.

        Doctor's "Rebuild embeddings" ends the same way in the same case (failed, "The
        search index was not updated", an error toast, the saved index untouched): the
        three web UI jobs that write embeddings end alike.
        """
        from plexmix.ui.states import library_state as ls

        class OneShort(CustomEmbedder):
            def generate_batch_embeddings(
                self, texts: list[str], batch_size: int = 100
            ) -> list[list[float]]:
                return super().generate_batch_embeddings(texts, batch_size)[:-1]

        ids = self._earlier_library(data_dir)
        before = stored_state(data_dir)
        state = library()
        state.selected_tracks = [str(track_id) for track_id in ids[:3]]

        with use_embedder(OneShort()):
            run(state.generate_embeddings())

        entry = task_store.get("embedding")
        assert entry is not None and entry.status == "failed"
        assert entry.message.startswith(
            "Generated embeddings for 2 tracks, but the search index was not updated. "
        )
        assert embedding_sizes(data_dir) == {
            **dict.fromkeys(ids[:2], 2),
            **dict.fromkeys(ids[2:], 3),
        }
        assert stored_state(data_dir)["files"] == before["files"]
        assert_saved_index_still_mismatched(data_dir, ids)
        toast, _refreshed = spy_poll(state)
        assert toast == ("error", entry.message)
        assert entry.extra[ls.INDEX_NOTICE_KEY]["title"] == ls.INDEX_NOT_UPDATED_TITLE

    def test_the_page_shows_no_success_and_keeps_the_way_out(self, data_dir: Path) -> None:
        from plexmix.ui.states import library_state as ls

        ids = self._earlier_library(data_dir)
        state = library()
        state.selected_tracks = [str(ids[0])]
        with use_embedder(CustomEmbedder()):
            run(state.generate_embeddings())
        entry = task_store.get("embedding")
        assert entry is not None
        assert state.is_embedding is True  # the poller reports the outcome

        toast, refreshed = spy_poll(state)

        # An error toast with the job's words: never "Embeddings generated successfully!".
        assert toast == ("error", entry.message)
        assert ls.ALL_EMBEDDED_MESSAGE not in toast[1]
        assert state.is_embedding is False
        # The notice stays on the page, in the warning tone, with the link to Doctor.
        assert state.embed_all_notice == entry.message
        assert state.embed_all_notice_tone == "warning"
        assert (state.embed_all_notice_action_label, state.embed_all_notice_action_href) == (
            "Rebuild embeddings",
            "/doctor",
        )
        assert (state.embed_all_notice_secondary_label, state.embed_all_notice_secondary_href) == (
            "Switch back",
            "/settings?section=embedding",
        )
        # The table and the counts follow the new row.
        assert refreshed == ["tracks", "stats"]

    def test_a_caller_without_access_gets_the_toast_and_no_notice_and_no_reload(
        self, data_dir: Path
    ) -> None:
        ids = self._earlier_library(data_dir)
        state = library()
        state.selected_tracks = [str(ids[0])]
        with use_embedder(CustomEmbedder()):
            run(state.generate_embeddings())
        entry = task_store.get("embedding")
        assert entry is not None
        stranger = library()
        stranger.is_embedding = True
        stranger.access_allowed = lambda: False  # type: ignore[method-assign]

        toast, refreshed = spy_poll(stranger)

        assert toast == ("error", entry.message)
        assert refreshed == [] and stranger.embed_all_notice == ""

    def test_the_library_still_reports_the_mismatch_afterwards(self, data_dir: Path) -> None:
        """No "All tracks already have embeddings.": the readiness row still fails."""
        from plexmix.services import readiness as rd
        from plexmix.ui.states import library_state as ls

        ids = self._earlier_library(data_dir)
        state = library()
        state.selected_tracks = [str(ids[0])]
        with use_embedder(CustomEmbedder()):
            run(state.generate_embeddings())

        index = index_readiness()
        assert (index.level, index.cause) == (rd.Level.FAILING, rd.Cause.DIMENSION_MISMATCH)
        preflight = ls.embed_all_preflight()
        assert preflight.state == "mismatch" and preflight.notice != ls.ALL_EMBEDDED_MESSAGE
        assert (preflight.action_label, preflight.action_href) == (
            "Rebuild embeddings",
            "/doctor",
        )
        # The embeddings were written, so the provider proved itself.
        assert readiness_of("embeddings")[0] == rd.Level.READY

    def test_whole_library_job_leaves_the_saved_index(self, data_dir: Path) -> None:
        """One track has no row at all: it is embedded, and the others stand in the way."""
        from plexmix.ui.states import library_state as ls

        ids = self._earlier_library(data_dir)
        with sqlite3.connect(str(data_dir / "plexmix.db")) as conn:
            conn.execute("DELETE FROM embeddings WHERE track_id = ?", (ids[0],))
        before = stored_state(data_dir)["files"]
        state = library()

        with use_embedder(CustomEmbedder()):
            run(state.embed_all_missing())

        entry = task_store.get("embedding")
        assert entry is not None and entry.status == "failed"
        assert entry.message.startswith(
            "Generated embeddings for 1 track, but the search index was not updated. "
            "5 tracks hold an embedding that was made with other embedding settings"
        )
        assert stored_state(data_dir)["files"] == before
        assert_saved_index_still_mismatched(data_dir, ids)
        assert entry.extra[ls.INDEX_NOTICE_KEY]["action_href"] == "/doctor"

    def test_job_with_nothing_to_embed_does_not_say_all_tracks_have_embeddings(
        self, data_dir: Path
    ) -> None:
        """Run directly (the page opens no confirmation here): it still builds nothing."""
        from plexmix.ui.states import library_state as ls

        ids = self._earlier_library(data_dir)
        before = stored_state(data_dir)["files"]
        embedder = CustomEmbedder()

        with use_embedder(embedder):
            status, message = ls.run_embedding_job(None, threading.Event())

        assert status == "failed" and embedder.requests == []
        assert message.startswith(f"{ls.INDEX_NOT_UPDATED_TITLE}. 6 tracks hold an embedding")
        assert ls.ALL_EMBEDDED_MESSAGE not in message
        assert "Rebuilt the search index" not in message
        assert stored_state(data_dir)["files"] == before
        assert_saved_index_still_mismatched(data_dir, ids)

    def test_a_cancelled_job_says_cancelled_and_builds_no_index(self, data_dir: Path) -> None:
        ids = self._earlier_library(data_dir)
        before = stored_state(data_dir)["files"]
        state = library()
        state.selected_tracks = [str(ids[0])]

        with use_embedder(CustomEmbedder(before=lambda request: task_store.cancel("embedding"))):
            run(state.generate_embeddings())

        entry = task_store.get("embedding")
        assert entry is not None and entry.status == "cancelled"
        assert stored_state(data_dir)["files"] == before
        toast, _refreshed = spy_poll(state)
        assert toast == ("warning", "Embedding generation cancelled")

    def test_a_stand_in_index_is_never_believed(self, data_dir: Path) -> None:
        """A stand-in index answers every attribute: only the rebuild's own result counts."""
        from plexmix.ui.states import library_state as ls

        assert ls.refused_rebuild(MagicMock()) == 0
        assert ls.refused_rebuild(SimpleNamespace(last_rebuild=None)) == 0
        assert ls.refused_rebuild(SimpleNamespace(last_rebuild=SimpleNamespace(left_out=4))) == 0

    def test_embedding_job_with_one_vector_size_ends_with_its_success_message(
        self, data_dir: Path
    ) -> None:
        """The healthy path: the index holds every embedded track, and nothing is warned."""
        from plexmix.database.vector_index import VectorIndex

        write_config(data_dir, REGENERATE_CONFIG)
        ids = make_old_library(data_dir, 4)  # "openai" rows of 2 numbers and their index
        state = library()
        state.selected_tracks = [str(ids[1])]

        with use_embedder(FakeEmbedder()):
            run(state.generate_embeddings())

        entry = task_store.get("embedding")
        assert entry is not None and entry.status == "completed"
        toast, refreshed = spy_poll(state)
        assert toast == ("success", "Embeddings generated successfully!")
        assert state.embed_all_notice == "" and "tracks" in refreshed
        opened = VectorIndex(dimension=2, index_path=str(data_dir / "embeddings.index"))
        assert sorted(opened.track_ids) == ids and opened.dimension_mismatch is False
        assert vectors_of(data_dir)[ids[1]] == [round(v, 4) for v in NEW_VECTOR]

    def test_whole_library_job_with_one_vector_size_rebuilds_a_missing_index(
        self, data_dir: Path
    ) -> None:
        write_config(data_dir, REGENERATE_CONFIG)
        ids = make_old_library(data_dir, 4)
        (data_dir / "embeddings.index").unlink()
        (data_dir / "embeddings.metadata").unlink()
        state = library()

        with use_embedder(FakeEmbedder()):
            run(state.embed_all_missing())

        entry = task_store.get("embedding")
        assert entry is not None and entry.status == "completed"
        assert entry.message == "Rebuilt the search index from 4 stored embeddings."
        assert index_ids(data_dir) == ids


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
