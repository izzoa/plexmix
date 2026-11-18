"""State tests for the Tagging inline editor's keyboard flow and Doctor fix progress.

Runs under the Reflex mock from ``tests/ui/conftest.py``. ``rx.call_script`` is
patched per test to return an inspectable ``("call_script", code)`` tuple.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_FOCUS_42 = "window.pmFocus&&pmFocus.when('#tag-edit-btn-42',{force:true})"


def _patch_call_script(monkeypatch: pytest.MonkeyPatch) -> None:
    import reflex as rx  # the MagicMock installed by tests/ui/conftest.py

    monkeypatch.setattr(rx, "call_script", lambda code: ("call_script", code), raising=False)


def _async_state() -> Any:
    """A TaggingState usable with ``async with`` whose list reload is a no-op."""
    from plexmix.ui.states.tagging_state import TaggingState

    async def _aenter(self: Any) -> Any:
        return self

    async def _aexit(self: Any, *exc: Any) -> bool:
        return False

    def _no_reload(self: Any) -> None:
        return None

    cls = type(
        "IsolatedTaggingState",
        (TaggingState,),
        {"__aenter__": _aenter, "__aexit__": _aexit, "load_recently_tagged": _no_reload},
    )
    return cls()


def _drain(agen: Any) -> list[Any]:
    async def _collect() -> list[Any]:
        return [event async for event in agen]

    return asyncio.run(_collect())


def _patched_db(db: MagicMock) -> tuple[Any, Any]:
    """Patch the lazily imported Settings/SQLiteManager so nothing touches disk."""
    settings_patch = patch(
        "plexmix.config.settings.Settings.load_from_file", return_value=MagicMock()
    )
    db_patch = patch("plexmix.database.sqlite_manager.SQLiteManager", return_value=db)
    return settings_patch, db_patch


class TestTaggingKeyboardEditing:
    """Inline tag editor: form submit, focus return to the row's Edit button."""

    def test_submit_form_data_overrides_state(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()
        state.start_edit_tag("42", "old moods", "old envs", "old instr")
        result = state.submit_tag_edit(
            {
                "tags": "chill, warm",
                "environments": "study",
                "instruments": "piano",
                "tag_edit_moods": "ref-keyed duplicate, ignored",
            }
        )
        assert state.edit_tags == "chill, warm"
        assert state.edit_environments == "study"
        assert state.edit_instruments == "piano"
        assert state.editing_track_id == "42"  # the save clears it, not the submit
        assert result is TaggingState.save_tag_edit

    def test_submit_keeps_state_for_missing_fields(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()
        state.start_edit_tag("42", "moods", "envs", "instr")
        state.submit_tag_edit({"tags": "new moods"})
        assert state.edit_tags == "new moods"
        assert state.edit_environments == "envs"
        assert state.edit_instruments == "instr"

    def test_submit_coerces_empty_and_none_to_empty_string(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()
        state.start_edit_tag("42", "moods", "envs", "instr")
        state.submit_tag_edit({"tags": "", "environments": None, "instruments": "x"})
        assert state.edit_tags == ""
        assert state.edit_environments == ""
        assert state.edit_instruments == "x"

    def test_cancel_returns_focus_to_the_row_edit_button(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        _patch_call_script(monkeypatch)
        state = TaggingState()
        state.start_edit_tag("42", "chill", "study", "piano")
        result = state.cancel_edit()
        assert result == ("call_script", _FOCUS_42)
        assert state.editing_track_id == ""
        assert state.edit_tags == state.edit_environments == state.edit_instruments == ""

    def test_cancel_without_an_open_editor_moves_no_focus(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        _patch_call_script(monkeypatch)
        assert TaggingState().cancel_edit() is None

    def test_unsafe_track_id_never_reaches_a_script(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        _patch_call_script(monkeypatch)
        state = TaggingState()
        state.start_edit_tag("1');alert(1)//", "", "", "")
        assert state.cancel_edit() is None
        assert state.editing_track_id == ""

    def test_save_yields_focus_after_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_call_script(monkeypatch)
        state = _async_state()
        state.start_edit_tag("42", "chill", "study", "piano")
        state.submit_tag_edit({"tags": "warm", "environments": "night", "instruments": "sax"})
        db = MagicMock()
        settings_patch, db_patch = _patched_db(db)
        with settings_patch, db_patch:
            events = _drain(state.save_tag_edit())
        db.update_track_tags.assert_called_once_with(
            42, tags="warm", environments="night", instruments="sax"
        )
        assert events[0] == ("call_script", _FOCUS_42)
        assert len(events) == 2  # focus, then the success toast
        assert state.editing_track_id == ""

    @staticmethod
    def _listed_state(before: list[str], after: list[str]) -> Any:
        """An isolated state listing ``before``; the post-save reload lists ``after``."""
        state = _async_state()
        state.recently_tagged_tracks = [{"id": i, "title": f"T{i}"} for i in before]

        def _reload() -> None:
            state.recently_tagged_tracks = [{"id": i, "title": f"T{i}"} for i in after]

        state.load_recently_tagged = _reload
        return state

    def _save(self, state: Any, track_id: str, tags: str) -> list[Any]:
        state.start_edit_tag(track_id, "chill", "", "")
        state.submit_tag_edit({"tags": tags, "environments": "", "instruments": ""})
        settings_patch, db_patch = _patched_db(MagicMock())
        with settings_patch, db_patch:
            return _drain(state.save_tag_edit())

    def test_saved_track_still_listed_keeps_its_own_edit_button(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_call_script(monkeypatch)
        # A save stamps tags_generated_at, so the track moves to the top.
        state = self._listed_state(["7", "42", "9"], ["42", "7", "9"])
        events = self._save(state, "42", "warm")
        assert events[0] == ("call_script", _FOCUS_42)

    def test_cleared_tags_drop_the_row_so_focus_goes_to_its_successor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_call_script(monkeypatch)
        state = self._listed_state(["7", "42", "9"], ["7", "9"])
        events = self._save(state, "42", "")
        assert events[0] == (
            "call_script",
            "window.pmFocus&&pmFocus.when('#tag-edit-btn-9',{force:true})",
        )

    def test_cleared_last_row_focuses_the_new_last_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_call_script(monkeypatch)
        state = self._listed_state(["7", "42"], ["7"])
        events = self._save(state, "42", "")
        assert events[0] == (
            "call_script",
            "window.pmFocus&&pmFocus.when('#tag-edit-btn-7',{force:true})",
        )

    def test_cleared_only_row_focuses_the_main_heading(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_call_script(monkeypatch)
        state = self._listed_state(["42"], [])
        events = self._save(state, "42", "")
        assert events[0] == (
            "call_script",
            "window.pmFocus&&pmFocus.when('#main-heading',{force:true})",
        )
        assert len(events) == 2  # focus, then the success toast

    def test_save_failure_keeps_editor_and_moves_no_focus(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_call_script(monkeypatch)
        state = _async_state()
        state.start_edit_tag("42", "chill", "study", "piano")
        db = MagicMock()
        db.update_track_tags.side_effect = RuntimeError("disk full")
        settings_patch, db_patch = _patched_db(db)
        with settings_patch, db_patch:
            events = _drain(state.save_tag_edit())
        assert not any(isinstance(e, tuple) and e[:1] == ("call_script",) for e in events)
        assert len(events) == 1  # the error toast only
        assert state.editing_track_id == "42"

    def test_save_without_an_open_editor_does_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_call_script(monkeypatch)
        state = _async_state()
        db = MagicMock()
        settings_patch, db_patch = _patched_db(db)
        with settings_patch, db_patch:
            events = _drain(state.save_tag_edit())
        assert events == []
        db.update_track_tags.assert_not_called()


class TestTaggingFilterFormPreview:
    """Filter form submit -> Preview, with Stale days sanitized (webui-input-responsiveness 7.1)."""

    @staticmethod
    def _preview(state: Any, tracks: list[Any]) -> MagicMock:
        db = MagicMock()
        db.get_tracks_by_filter.return_value = tracks
        db.count_stale_tagged_tracks.return_value = 0
        settings_patch, db_patch = _patched_db(db)
        with settings_patch, db_patch:
            asyncio.run(state.preview_selection())
        return db

    def test_invalid_stale_days_previews_without_a_stale_filter(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = _async_state()
        assert (
            state.apply_filter_form({"artist": "", "genre": "jazz", "stale_days": "abc"})
            is TaggingState.preview_selection
        )
        db = self._preview(state, [object(), object(), object()])
        kwargs = db.get_tracks_by_filter.call_args.kwargs
        assert kwargs["stale_days"] is None
        assert kwargs["genre"] == "jazz"
        assert kwargs["artist"] is None
        assert state.preview_count == 3
        assert state.tagging_message == "3 tracks match your filters"

    def test_valid_stale_days_is_applied_as_a_number(self) -> None:
        state = _async_state()
        state.apply_filter_form({"artist": "Miles", "genre": "", "stale_days": "30"})
        db = self._preview(state, [object()])
        kwargs = db.get_tracks_by_filter.call_args.kwargs
        assert kwargs["stale_days"] == 30
        assert kwargs["artist"] == "Miles"
        assert kwargs["genre"] is None
        assert state.preview_count == 1

    def test_empty_stale_days_is_a_normal_preview(self) -> None:
        state = _async_state()
        state.set_stale_days("30")
        state.apply_filter_form({"artist": "", "genre": "", "stale_days": ""})
        db = self._preview(state, [object(), object()])
        assert db.get_tracks_by_filter.call_args.kwargs["stale_days"] is None
        assert state.tagging_message == "2 tracks match your filters"

    def test_save_right_after_typing_uses_the_text_on_screen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An older blur commit is in state; Save submits the field's current text."""
        _patch_call_script(monkeypatch)
        state = _async_state()
        state.start_edit_tag("42", "chill", "study", "piano")
        state.set_edit_tags("mel")  # a blur commit of a partly typed value
        state.submit_tag_edit(
            {"tags": "melancholy, rainy", "environments": "study", "instruments": "piano"}
        )
        db = MagicMock()
        settings_patch, db_patch = _patched_db(db)
        with settings_patch, db_patch:
            _drain(state.save_tag_edit())
        db.update_track_tags.assert_called_once_with(
            42, tags="melancholy, rainy", environments="study", instruments="piano"
        )


class TestDoctorFixProgress:
    """DoctorState.fix_percent drives the fix progress bar fill."""

    def test_percent_from_tracks(self) -> None:
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.fix_progress = 1204
        state.fix_total = 5807
        assert state.fix_percent == 20
        assert state.fix_progress_label == "1,204 / 5,807"

    def test_unknown_total_is_zero(self) -> None:
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.fix_progress = 3
        state.fix_total = 0
        assert state.fix_percent == 0

    def test_percent_is_clamped(self) -> None:
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.fix_progress = 12
        state.fix_total = 10
        assert state.fix_percent == 100
        state.fix_progress = -5
        assert state.fix_percent == 0
