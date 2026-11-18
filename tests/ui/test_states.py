"""Unit tests for UI state management - Fixed version."""
from pathlib import Path
from typing import Any

import pytest

# Mock Reflex must happen before imports - handled in conftest.py


class TestAppState:
    """Test cases for AppState base class."""

    @pytest.mark.skip(
        reason="Reflex state management makes mocking difficult - test UI functionality manually"
    )
    def test_on_load_checks_configuration(self):
        """Test that on_load properly checks configuration status."""
        pass


class TestHistoryState:
    """Test cases for HistoryState."""

    def test_show_and_cancel_delete_confirmation(self):
        """Test showing and canceling delete confirmation."""
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()

        # Show confirmation
        state.show_delete_confirmation("5")
        assert state.playlist_to_delete == "5"
        assert state.is_delete_confirmation_open is True

        # Cancel confirmation
        state.cancel_delete()
        assert state.playlist_to_delete == ""
        assert state.is_delete_confirmation_open is False

    def test_sort_playlists_by_name(self):
        """Test sorting playlists by name."""
        from plexmix.ui.states.history_state import HistoryState

        # Create test data
        state = HistoryState()
        state.playlists = [
            {"name": "Zebra", "track_count": "10", "created_at": "2024-01-01"},
            {"name": "Alpha", "track_count": "20", "created_at": "2024-01-02"},
            {"name": "Beta", "track_count": "15", "created_at": "2024-01-03"},
        ]

        # Sort by name (descending by default)
        state.sort_descending = True
        state.sort_playlists("name")

        # Verify sorting
        assert state.playlists[0]["name"] == "Zebra"
        assert state.playlists[1]["name"] == "Beta"
        assert state.playlists[2]["name"] == "Alpha"

    def test_sort_playlists_by_track_count(self):
        """Test sorting playlists by track count."""
        from plexmix.ui.states.history_state import HistoryState

        # Create test data
        state = HistoryState()
        state.playlists = [
            {"name": "A", "track_count": "10", "created_at": "2024-01-01"},
            {"name": "B", "track_count": "20", "created_at": "2024-01-02"},
            {"name": "C", "track_count": "15", "created_at": "2024-01-03"},
        ]

        # Sort by track count
        state.sort_playlists("track_count")

        # Verify sorting (descending by default)
        assert state.playlists[0]["track_count"] == "20"
        assert state.playlists[1]["track_count"] == "15"
        assert state.playlists[2]["track_count"] == "10"

    def test_format_date(self):
        """Test date formatting."""
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()

        # Test valid ISO format
        result = state.format_date("2024-01-15T10:30:00Z")
        assert "January 15, 2024" in result

        # Test invalid format returns original
        result = state.format_date("invalid date")
        assert result == "invalid date"

    def test_format_duration(self):
        """Test duration formatting."""
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()

        # Test zero duration
        assert state.format_duration(0) == "0:00"
        assert state.format_duration(None) == "0:00"

        # Test minutes only
        assert state.format_duration(180000) == "3:00"  # 3 minutes

        # Test hours
        assert state.format_duration(7380000) == "2:03:00"  # 2 hours 3 minutes

        # Test seconds formatting
        assert state.format_duration(65000) == "1:05"  # 1 minute 5 seconds

    def test_toggle_sort_order(self):
        """Test toggling sort order."""
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()
        assert state.sort_descending is True  # Default

        state.toggle_sort_order()
        assert state.sort_descending is False

        state.toggle_sort_order()
        assert state.sort_descending is True

    def test_close_detail_modal(self):
        """Test closing detail modal clears selected data."""
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()
        # Set some data
        state.is_detail_modal_open = True
        state.selected_playlist = {"id": "1", "name": "Test"}
        state.selected_playlist_tracks = [{"id": "1"}]

        # Close modal
        state.close_detail_modal()

        # Verify cleared
        assert state.is_detail_modal_open is False
        assert state.selected_playlist == {}
        assert state.selected_playlist_tracks == []

    def test_setter_methods(self):
        """Test the setter methods."""
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()

        # Test error message setter
        state.set_error_message("Test error")
        assert state.error_message == "Test error"

        # Test action message setter
        state.set_action_message("Test action")
        assert state.action_message == "Test action"

        # Test detail modal setter
        state.selected_playlist = {"id": "1"}
        state.selected_playlist_tracks = [{"id": "1"}]
        state.set_detail_modal_open(False)
        assert state.is_detail_modal_open is False
        assert state.selected_playlist == {}
        assert state.selected_playlist_tracks == []

    # ── Client-owned search field (webui-input-responsiveness 7.3) ──

    @staticmethod
    def _counting_history_state() -> Any:
        """A HistoryState that counts assignments to ``search_query``."""
        from plexmix.ui.states.history_state import HistoryState

        class _Counting(HistoryState):
            writes = 0

            def __setattr__(self, name: str, value: Any) -> None:
                if name == "search_query":
                    type(self).writes += 1
                super().__setattr__(name, value)

        _Counting.writes = 0
        return _Counting()

    def test_set_search_query_returns_early_when_unchanged(self) -> None:
        state = self._counting_history_state()
        state.set_search_query("rainy")
        assert state.search_query == "rainy"
        writes = type(state).writes
        # The blur commit after a debounced one (same text) is a no-op.
        state.set_search_query("rainy")
        assert type(state).writes == writes
        state.set_search_query("rainy day")
        assert state.search_query == "rainy day"
        assert type(state).writes == writes + 1

    def test_apply_search_form_reads_q_from_form_data(self) -> None:
        from plexmix.ui.states.history_state import (
            SEARCH_FIELD_NAME,
            SEARCH_INPUT_ID,
            HistoryState,
        )

        assert SEARCH_FIELD_NAME == "q"
        assert SEARCH_INPUT_ID == "hist-search"
        state = HistoryState()
        state.playlists = [
            {"name": "Rainy Day Melancholy", "mood_query": "slow piano"},
            {"name": "Workout", "mood_query": "rainy day melancholy beats"},
            {"name": "Party", "mood_query": "upbeat"},
        ]
        # State still holds an older debounced commit; the form's text wins.
        state.search_query = "rainy"
        assert state.apply_search_form({"q": "rainy day melancholy"}) is None
        assert state.search_query == "rainy day melancholy"
        assert [p["name"] for p in state.filtered_playlists] == ["Rainy Day Melancholy", "Workout"]

    def test_apply_search_form_goes_through_the_setter(self, monkeypatch: Any) -> None:
        from plexmix.ui.states.history_state import HistoryState

        calls: list[str] = []
        monkeypatch.setattr(
            HistoryState, "set_search_query", lambda self, q: calls.append(q), raising=True
        )
        HistoryState().apply_search_form({"q": "jazz", "hist_search": "ref duplicate"})
        assert calls == ["jazz"]

    def test_apply_search_form_blank_and_missing_values(self) -> None:
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()
        state.search_query = "jazz"
        state.apply_search_form({})  # no field: search unchanged
        assert state.search_query == "jazz"
        state.apply_search_form({"q": None})
        assert state.search_query == ""
        state.playlists = [{"name": "A", "mood_query": ""}]
        assert state.filtered_playlists == state.playlists


class TestSettingsState:
    """Test cases for SettingsState."""

    def test_validate_plex_url(self):
        """Test Plex URL validation."""
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()

        # Valid URL
        state.validate_plex_url("http://localhost:32400")
        assert state.plex_url_error == ""

        # Invalid URL
        state.validate_plex_url("not a url")
        assert state.plex_url_error != ""

        # Empty URL
        state.validate_plex_url("")
        assert state.plex_url_error != ""

    def test_validate_temperature(self):
        """Test temperature validation."""
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()

        # Valid temperature
        state.validate_temperature(0.5)
        assert state.temperature_error == ""

        # Too high
        state.validate_temperature(1.5)
        assert state.temperature_error != ""

        # Too low
        state.validate_temperature(-0.1)
        assert state.temperature_error != ""

    def test_is_form_valid(self):
        """Form validity uses the Plex token's stored/staged status, never its value."""
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()

        # Initially invalid (missing required fields)
        assert state.is_form_valid() is False

        # URL set, but no Plex token stored or staged yet
        state.plex_url = "http://localhost:32400"
        assert state.is_form_valid() is False

        # A stored token makes it valid
        state.secret_stored = {**state.secret_stored, "plex_token": True}
        assert state.is_form_valid() is True

        # Add validation error
        state.plex_url_error = "Invalid URL"

        # Should be invalid
        assert state.is_form_valid() is False

    def test_is_form_valid_with_staged_plex_token(self):
        """A staged (not yet saved) Plex token also satisfies the requirement."""
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.plex_url = "http://localhost:32400"
        state.secret_staged = {**state.secret_staged, "plex_token": "replace"}
        assert state.is_form_valid() is True
        state.secret_staged = {**state.secret_staged, "plex_token": "clear"}
        assert state.is_form_valid() is False

    def test_update_model_lists(self):
        """Test model list updates."""
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()

        # Test Gemini models
        state.ai_provider = "gemini"
        state.update_model_lists()

        # Should have added some models
        assert len(state.ai_models) > 0
        assert any("gemini" in model.lower() for model in state.ai_models)

        # Test OpenAI models
        state.ai_provider = "openai"
        state.update_model_lists()
        assert len(state.ai_models) > 0
        assert any("gpt" in model.lower() for model in state.ai_models)

    def test_update_model_lists_preselects_registry_default(self):
        """An empty model auto-selects the registry default, not sorted-first."""
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.ai_provider = "gemini"
        state.ai_model = ""
        state.update_model_lists()
        assert state.ai_model == "gemini-3.5-flash"

    def test_set_ai_provider_selects_registry_default(self):
        """Switching providers selects that provider's registry default."""
        from unittest.mock import patch
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.ai_model = "gemini-3.5-flash"  # a previous provider's selection
        with patch("plexmix.ui.states.settings_state.SettingsState._refresh_secret_status"):
            state.set_ai_provider("anthropic")
        assert state.ai_model == "claude-sonnet-4-6"

    # ── Section deep links (?section=) and in-place address updates ──

    @staticmethod
    def _with_query(state: Any, params: Any) -> Any:
        from types import SimpleNamespace

        state.router = SimpleNamespace(url=SimpleNamespace(query_parameters=params))
        return state

    def _run_on_load(self, state: Any) -> Any:
        """Run on_load with loading stubbed; return the tab seen when settings load."""
        from unittest.mock import patch
        from plexmix.ui.states.settings_state import SettingsState

        seen = {}

        def _fake_load(self_: Any) -> list[Any]:
            seen["tab_at_load"] = self_.active_tab
            return []

        with patch.object(SettingsState, "check_auth", return_value=True), patch.object(
            SettingsState, "load_app_data"
        ), patch.object(SettingsState, "_detect_local_deps"), patch.object(
            SettingsState, "load_settings", _fake_load
        ), patch.object(
            SettingsState, "update_model_lists"
        ):
            state.on_load()
        return seen.get("tab_at_load")

    def test_on_load_section_param_selects_section(self) -> None:
        from plexmix.ui.states.settings_state import SettingsState

        state = self._with_query(SettingsState(), {"section": "ai"})
        tab_at_load = self._run_on_load(state)
        assert state.active_tab == "ai"
        # Applied before the form loads.
        assert tab_at_load == "ai"

    def test_on_load_unknown_section_keeps_current(self) -> None:
        from plexmix.ui.states.settings_state import SettingsState

        state = self._with_query(SettingsState(), {"section": "bogus"})
        self._run_on_load(state)
        assert state.active_tab == "plex"  # first visit default

        state = self._with_query(SettingsState(), {"section": "bogus"})
        state.active_tab = "musicbrainz"
        self._run_on_load(state)
        assert state.active_tab == "musicbrainz"

    def test_on_load_missing_section_keeps_current(self) -> None:
        from plexmix.ui.states.settings_state import SettingsState

        state = self._with_query(SettingsState(), {})
        state.active_tab = "audio"
        self._run_on_load(state)
        assert state.active_tab == "audio"

    def test_on_load_without_router_keeps_current(self) -> None:
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()  # the mocked State has no router at all
        state.active_tab = "advanced"
        self._run_on_load(state)
        assert state.active_tab == "advanced"

    def test_on_load_every_section_id_is_accepted(self) -> None:
        from plexmix.ui.states.settings_state import SettingsState
        from plexmix.ui.utils.a11y import SETTINGS_SECTIONS

        for section in SETTINGS_SECTIONS:
            state = self._with_query(SettingsState(), {"section": section})
            self._run_on_load(state)
            assert state.active_tab == section

    def test_on_load_repeated_section_param_uses_first(self) -> None:
        from plexmix.ui.states.settings_state import SettingsState

        state = self._with_query(SettingsState(), {"section": ["embedding", "ai"]})
        self._run_on_load(state)
        assert state.active_tab == "embedding"

    def test_set_active_tab_rewrites_address_in_place(self) -> None:
        from unittest.mock import patch
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        with patch("plexmix.ui.states.settings_state.rx.call_script") as call_script, patch(
            "plexmix.ui.states.settings_state.rx.redirect"
        ) as redirect:
            result = state.set_active_tab("embedding")
        assert state.active_tab == "embedding"
        call_script.assert_called_once()
        script = call_script.call_args[0][0]
        assert "section=embedding" in script
        assert "history.replaceState" in script
        assert "history.state" in script
        assert result is call_script.return_value
        redirect.assert_not_called()

    def test_set_active_tab_ignores_unknown_ids(self) -> None:
        from unittest.mock import patch
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.active_tab = "ai"
        with patch("plexmix.ui.states.settings_state.rx.call_script") as call_script:
            for bogus in ("x", "", "AI", "plex'); alert(1); ('"):
                assert state.set_active_tab(bogus) is None
        assert state.active_tab == "ai"
        call_script.assert_not_called()

    def test_set_active_tab_keeps_unsaved_edits(self) -> None:
        """Switching sections only sets the tab: nothing is reloaded from disk."""
        from unittest.mock import patch
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.plex_url = "http://edited:32400"
        with patch.object(SettingsState, "load_settings") as load, patch(
            "plexmix.ui.states.settings_state.rx.call_script"
        ):
            state.set_active_tab("ai")
            state.set_active_tab("plex")
        load.assert_not_called()
        assert state.plex_url == "http://edited:32400"

    # ── Native range setters ──

    def test_set_ai_temperature_from_range_string(self) -> None:
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.set_ai_temperature("0.7")
        assert state.ai_temperature == 0.7
        state.set_ai_temperature(0.3)
        assert state.ai_temperature == 0.3

    def test_set_ai_temperature_clamps(self) -> None:
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.set_ai_temperature("5")
        assert state.ai_temperature == 1.0
        state.set_ai_temperature("-2")
        assert state.ai_temperature == 0.0

    def test_set_ai_temperature_ignores_garbage(self) -> None:
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.ai_temperature = 0.4
        garbage_values: list[Any] = ["abc", "", None, "nan", "inf", True, [], {}]
        for garbage in garbage_values:
            state.set_ai_temperature(garbage)
            assert state.ai_temperature == 0.4

    def test_set_confidence_threshold_shapes(self) -> None:
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.set_musicbrainz_confidence_threshold(["80"])
        assert state.musicbrainz_confidence_threshold == 80.0
        state.set_musicbrainz_confidence_threshold("65")
        assert state.musicbrainz_confidence_threshold == 65.0
        state.set_musicbrainz_confidence_threshold(55)
        assert state.musicbrainz_confidence_threshold == 55.0
        state.set_musicbrainz_confidence_threshold([90.0])
        assert state.musicbrainz_confidence_threshold == 90.0

    def test_set_confidence_threshold_clamps_and_ignores_garbage(self) -> None:
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.set_musicbrainz_confidence_threshold("250")
        assert state.musicbrainz_confidence_threshold == 100.0
        state.set_musicbrainz_confidence_threshold(["-5"])
        assert state.musicbrainz_confidence_threshold == 0.0
        state.musicbrainz_confidence_threshold = 70.0
        garbage_values: list[Any] = ["high", [], ["x"], None, "nan", False]
        for garbage in garbage_values:
            state.set_musicbrainz_confidence_threshold(garbage)
            assert state.musicbrainz_confidence_threshold == 70.0


class TestGeneratorState:
    """Test cases for GeneratorState."""

    def test_use_example(self):
        """Test setting example query."""
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()

        # Test setting example query
        state.use_example("Chill rainy day vibes")
        assert state.mood_query == "Chill rainy day vibes"

    def test_remove_track(self):
        """Test removing track from playlist."""
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.generated_playlist = [
            {"id": "1", "title": "Track 1", "duration_ms": "180000"},
            {"id": "2", "title": "Track 2", "duration_ms": "200000"},
            {"id": "3", "title": "Track 3", "duration_ms": "150000"},
        ]
        state.total_duration_ms = 530000

        state.remove_track("2")
        assert len(state.generated_playlist) == 2
        assert state.generated_playlist[0]["id"] == "1"
        assert state.generated_playlist[1]["id"] == "3"
        assert state.total_duration_ms == 330000

    def test_set_max_tracks_clamps(self):
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.set_max_tracks(5)
        assert state.max_tracks == 10  # clamped to min 10
        state.set_max_tracks(200)
        assert state.max_tracks == 100  # clamped to max 100
        state.set_max_tracks(50)
        assert state.max_tracks == 50

    def test_set_candidate_pool_multiplier_clamps(self):
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.set_candidate_pool_multiplier(0)
        assert state.candidate_pool_multiplier == 1
        state.set_candidate_pool_multiplier(200)
        assert state.candidate_pool_multiplier == 100
        state.set_candidate_pool_multiplier(25)
        assert state.candidate_pool_multiplier == 25

    def test_set_year_min_max(self):
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.set_year_min("2000")
        assert state.year_min == "2000"
        state.set_year_min("")
        assert state.year_min == ""
        state.set_year_max("2020")
        assert state.year_max == "2020"
        state.set_year_max("")
        assert state.year_max == ""

    def test_audio_setters(self):
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.set_tempo_min("80")
        assert state.tempo_min == "80"
        state.set_tempo_max("140")
        assert state.tempo_max == "140"
        state.set_energy_level("high")
        assert state.energy_level == "high"
        state.set_key_filter("C")
        assert state.key_filter == "C"
        state.set_danceability_min("0.5")
        assert state.danceability_min == "0.5"

    def test_format_duration(self):
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        assert state.format_duration(0) == "0:00"
        assert state.format_duration(185000) == "3:05"
        assert state.format_duration(60000) == "1:00"

    # ── Prompt entry and submission (webui-input-responsiveness 5.1) ──────────

    @staticmethod
    def _loadable_state(rerun_config: str = "") -> Any:
        """A GeneratorState whose on_load touches no config, DB or session sweep."""
        from unittest.mock import MagicMock

        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.check_auth = MagicMock(return_value=True)
        state.load_app_data = MagicMock()
        state._load_audio_stats = MagicMock()
        state._load_templates = MagicMock()
        state._rerun_generation_config = rerun_config
        return state

    @staticmethod
    def _set_values(events: Any) -> list[tuple[str, str, str]]:
        """The ``rx.set_value`` markers (``("set_value", ref, value)``) in ``events``."""
        return [e for e in (events or []) if isinstance(e, tuple) and e[0] == "set_value"]

    def test_prompt_constants(self) -> None:
        from plexmix.ui.states import generator_state as gs

        assert gs.PROMPT_MAX_CHARS == 280
        assert gs.PROMPT_INPUT_ID == "gen-prompt"
        assert gs.GENRE_INPUT_ID == "gen-genre"

    def test_set_mood_query_cuts_to_limit(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.set_mood_query("a" * 300)
        assert state.mood_query == "a" * 280
        state.set_mood_query("b" * 280)
        assert state.mood_query == "b" * 280
        state.set_mood_query("")
        assert state.mood_query == ""

    def test_set_mood_query_clears_error_only_for_text(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.prompt_error = "Describe a vibe first."
        state.set_mood_query("   ")
        assert state.prompt_error == "Describe a vibe first."
        state.set_mood_query("late night drive")
        assert state.prompt_error == ""

    def test_set_mood_query_returns_no_echo(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        assert state.set_mood_query("rainy day") is None

    def test_set_genre_filter_unchanged_is_noop(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        class Recording(GeneratorState):
            writes: list[str]

            def __setattr__(self, name: str, value: Any) -> None:
                if name == "genre_filter":
                    self.__dict__.setdefault("writes", []).append(value)
                super().__setattr__(name, value)

        state = Recording()
        state.set_genre_filter("jazz")
        state.set_genre_filter("jazz")
        assert state.genre_filter == "jazz"
        assert state.__dict__["writes"] == ["jazz"]
        state.set_genre_filter("")
        assert state.genre_filter == ""

    def test_prompt_count_label(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        assert state.prompt_count_label == "0/280"
        state.set_mood_query("x" * 12)
        assert state.prompt_count_label == "12/280"
        state.set_mood_query("x" * 400)
        assert state.prompt_count_label == "280/280"

    @pytest.mark.parametrize("prompt", ["", "   ", "\n\t  \n"])
    def test_submit_blank_prompt_is_rejected(self, prompt: str) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        result = state.submit_prompt({"prompt": prompt})
        assert result is None  # no generate event
        assert state.prompt_error == "Describe a vibe first."

    def test_submit_missing_prompt_key_is_blank(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.mood_query = "stale committed text"
        assert state.submit_prompt({}) is None
        assert state.prompt_error == "Describe a vibe first."

    def test_submit_uses_form_text_not_stale_state(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.mood_query = "late night dr"  # the blur commit lagged behind
        result = state.submit_prompt({"prompt": "late night drive", "genre": "synthwave"})
        assert result is GeneratorState.generate_playlist
        assert state.mood_query == "late night drive"
        assert state.genre_filter == "synthwave"
        assert state.prompt_error == ""

    def test_submit_clears_previous_blank_message(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        assert state.submit_prompt({"prompt": " "}) is None
        assert state.prompt_error == "Describe a vibe first."
        assert state.submit_prompt({"prompt": "sunday morning coffee"}) is (
            GeneratorState.generate_playlist
        )
        assert state.prompt_error == ""

    def test_submit_cuts_prompt_to_limit(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        result = state.submit_prompt({"prompt": "y" * 300})
        assert result is GeneratorState.generate_playlist
        assert state.mood_query == "y" * 280

    def test_submit_without_genre_key_keeps_genre(self) -> None:
        """The palette's Generate row sends only the prompt."""
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.genre_filter = "jazz"
        assert state.submit_prompt({"prompt": "jazz for cooking"}) is (
            GeneratorState.generate_playlist
        )
        assert state.genre_filter == "jazz"

    def test_submit_while_generating_starts_nothing(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.mood_query = "first vibe"
        state.is_generating = True
        assert state.submit_prompt({"prompt": "second vibe"}) is None
        assert state.mood_query == "first vibe"
        assert state.prompt_error == ""

    def test_prefill_prompt_returns_set_value_with_cut_text(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.prompt_error = "Describe a vibe first."
        event = state.prefill_prompt("z" * 320)
        assert event == ("set_value", "gen-prompt", "z" * 280)
        assert state.mood_query == "z" * 280
        assert state.prompt_error == ""
        # Prefill only fills the prompt: it never starts a generation.
        assert state.is_generating is False

    def test_prefill_prompt_replaces_draft(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.mood_query = "half-typed dra"
        assert state.prefill_prompt("rainy day melancholy") == (
            "set_value",
            "gen-prompt",
            "rainy day melancholy",
        )
        assert state.mood_query == "rainy day melancholy"

    def test_use_example_updates_the_field(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        event = state.use_example("Chill rainy day vibes")
        assert state.mood_query == "Chill rainy day vibes"
        assert event == ("set_value", "gen-prompt", "Chill rainy day vibes")

    def test_on_load_without_rerun_returns_no_set_value(self) -> None:
        state = self._loadable_state()
        state.mood_query = "draft being typed"
        state.genre_filter = "jazz"
        result = state.on_load()
        assert self._set_values(result) == []
        assert result is None
        assert state.mood_query == "draft being typed"
        assert state.genre_filter == "jazz"
        assert state.is_page_loading is False

    def test_on_load_unauthenticated_returns_nothing(self) -> None:
        import json

        state = self._loadable_state(json.dumps({"mood_query": "x"}))
        state.check_auth.return_value = False
        assert state.on_load() is None
        state.load_app_data.assert_not_called()
        assert state.mood_query == ""

    def test_on_load_rerun_sets_prompt_and_genre(self) -> None:
        import json

        state = self._loadable_state(
            json.dumps({"mood_query": "rainy day melancholy", "genre_filter": "indie"})
        )
        result = state.on_load()
        assert self._set_values(result) == [
            ("set_value", "gen-prompt", "rainy day melancholy"),
            ("set_value", "gen-genre", "indie"),
        ]
        assert state._rerun_generation_config == ""
        assert state.is_page_loading is False

    def test_on_load_long_rerun_prompt_is_cut(self) -> None:
        import json

        long_prompt = "w" * 320
        state = self._loadable_state(json.dumps({"mood_query": long_prompt}))
        result = state.on_load()
        assert state.mood_query == "w" * 280
        assert self._set_values(result) == [("set_value", "gen-prompt", "w" * 280)]

    def test_on_load_rerun_writes_only_changed_fields(self) -> None:
        import json

        state = self._loadable_state(
            json.dumps({"mood_query": "same prompt", "genre_filter": "rock"})
        )
        state.mood_query = "same prompt"
        result = state.on_load()
        assert self._set_values(result) == [("set_value", "gen-genre", "rock")]

    def test_on_load_rerun_without_changes_returns_nothing(self) -> None:
        import json

        state = self._loadable_state(json.dumps({"mood_query": "same", "genre_filter": ""}))
        state.mood_query = "same"
        assert state.on_load() is None

    def test_apply_template_sets_changed_fields(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.templates = [
            {"id": "1", "mood_query": "q" * 300, "genre_filter": "jazz", "max_tracks": "30"},
            {"id": "2", "mood_query": "q" * 280, "genre_filter": "jazz"},
        ]
        result = state.apply_template("1")
        assert state.mood_query == "q" * 280
        assert state.max_tracks == 30
        assert self._set_values(result) == [
            ("set_value", "gen-prompt", "q" * 280),
            ("set_value", "gen-genre", "jazz"),
        ]
        # Same prompt and genre again: nothing to write.
        assert state.apply_template("2") is None
        assert state.apply_template("missing") is None

    # ── Prompt set by the app while results show or a generation runs (fix step) ──

    @staticmethod
    def _with_results(state: Any, prompt: str = "rainy day jazz") -> Any:
        """Put a finished result on screen for ``prompt`` (Save does not clear it)."""
        state.mood_query = prompt
        state.generated_playlist = [{"id": "1", "title": "Blue in Green"}]
        state.total_duration_ms = 337000
        state.generation_progress = 100
        state.generation_message = "Generated 1 tracks!"
        state.generation_log = ["Starting playlist generation...", "Generated 1 tracks!"]
        return state

    @staticmethod
    def _assert_idle_hero(state: Any) -> None:
        """Results gone: the Generator renders the idle hero and its prompt field."""
        assert state.generated_playlist == []
        assert state.total_duration_ms == 0
        assert state.generation_progress == 0
        assert state.generation_message == ""
        assert state.generation_log == []

    def test_prefill_with_results_returns_to_the_prompt(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = self._with_results(GeneratorState())
        event = state.prefill_prompt("upbeat 2000s party")
        self._assert_idle_hero(state)
        assert state.mood_query == "upbeat 2000s party"
        assert event == ("set_value", "gen-prompt", "upbeat 2000s party")
        assert state.is_generating is False

    def test_prefill_while_generating_keeps_the_running_prompt(self) -> None:
        from unittest.mock import patch

        from plexmix.ui.states import generator_state as gs

        state = gs.GeneratorState()
        state.mood_query = "rainy day jazz"
        state.is_generating = True
        with patch.object(gs.rx, "toast") as toast:
            event = state.prefill_prompt("upbeat 2000s party")
        assert state.mood_query == "rainy day jazz"
        toast.info.assert_called_once_with(gs.GENERATION_BUSY_MESSAGE)
        assert event is toast.info.return_value

    def test_prefill_while_generating_with_the_same_prompt_is_silent(self) -> None:
        """A double-clicked vibe pill: its second prefill neither toasts nor changes anything."""
        from unittest.mock import patch

        from plexmix.ui.states import generator_state as gs

        state = gs.GeneratorState()
        state.mood_query = "Chill rainy day vibes"
        state.is_generating = True
        with patch.object(gs.rx, "toast") as toast:
            assert state.prefill_prompt("Chill rainy day vibes") is None
        toast.info.assert_not_called()
        assert state.mood_query == "Chill rainy day vibes"

    def test_prefill_without_results_keeps_an_error_message(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = GeneratorState()
        state.generation_message = "Vector index not found or empty."
        state.prefill_prompt("late night drive")
        assert state.generation_message == "Vector index not found or empty."
        assert state.mood_query == "late night drive"

    def test_on_load_rerun_with_results_returns_to_the_prompt(self) -> None:
        import json

        state = self._with_results(
            self._loadable_state(json.dumps({"mood_query": "sunday morning coffee"}))
        )
        result = state.on_load()
        self._assert_idle_hero(state)
        assert state.mood_query == "sunday morning coffee"
        assert self._set_values(result) == [("set_value", "gen-prompt", "sunday morning coffee")]
        assert state._rerun_generation_config == ""

    def test_on_load_rerun_of_the_shown_prompt_still_shows_the_prompt(self) -> None:
        import json

        state = self._with_results(
            self._loadable_state(json.dumps({"mood_query": "rainy day jazz"})), "rainy day jazz"
        )
        assert state.on_load() is None  # same text: the remounted field already shows it
        self._assert_idle_hero(state)
        assert state.mood_query == "rainy day jazz"

    def test_on_load_rerun_while_generating_is_refused(self) -> None:
        import json
        from unittest.mock import patch

        from plexmix.ui.states import generator_state as gs

        state = self._loadable_state(
            json.dumps({"mood_query": "sunday morning coffee", "genre_filter": "folk"})
        )
        state.mood_query = "rainy day jazz"
        state.is_generating = True
        with patch.object(gs.rx, "toast") as toast:
            result = state.on_load()
        assert result == [toast.info.return_value]
        toast.info.assert_called_once_with(gs.GENERATION_BUSY_MESSAGE)
        assert state.mood_query == "rainy day jazz"
        assert state.genre_filter == ""
        assert state._rerun_generation_config == ""  # not applied later by surprise
        assert state.is_page_loading is False

    def test_apply_template_with_results_and_while_generating(self) -> None:
        from unittest.mock import patch

        from plexmix.ui.states import generator_state as gs

        state = self._with_results(gs.GeneratorState())
        state.templates = [{"id": "1", "mood_query": "focus", "genre_filter": "ambient"}]
        state.is_generating = True
        with patch.object(gs.rx, "toast") as toast:
            assert state.apply_template("1") == [toast.info.return_value]
        assert state.mood_query == "rainy day jazz"
        assert state.genre_filter == ""
        state.is_generating = False
        state.apply_template("1")
        self._assert_idle_hero(state)
        assert state.mood_query == "focus"

    def test_new_playlist_clears_results(self) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        state = self._with_results(GeneratorState())
        state.new_playlist()
        self._assert_idle_hero(state)
        assert state.mood_query == "rainy day jazz"


class TestLibraryState:
    """Test cases for LibraryState."""

    def test_pagination(self):
        """Test pagination methods."""
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()
        state.current_page = 1  # Start at page 1
        state.total_tracks = 100
        state.total_filtered_tracks = 100  # Set filtered tracks too
        state.page_size = 20

        # Mock load_tracks to prevent actual loading
        state.load_tracks = lambda: None

        # Test next page
        state.next_page()
        assert state.current_page == 2

        # Test another next page
        state.next_page()
        assert state.current_page == 3

        # Test prev page
        state.previous_page()
        assert state.current_page == 2

        # Go back to first page
        state.previous_page()
        assert state.current_page == 1

        # Test can't go below page 1
        state.previous_page()
        assert state.current_page == 1

    def test_filter_settings(self):
        """Test filter state management."""
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()

        # Set filters
        state.search_query = "test"
        state.genre_filter = "rock"
        state.year_min = "2000"
        state.year_max = "2020"

        # Verify filters are set
        assert state.search_query == "test"
        assert state.genre_filter == "rock"
        assert state.year_min == "2000"
        assert state.year_max == "2020"

    def test_regenerate_confirm_flow(self):
        """Test regenerate confirmation dialog flow."""
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()

        # Initially not showing
        assert state.show_regenerate_confirm is False

        # Show confirmation
        state.confirm_regenerate_sync()
        assert state.show_regenerate_confirm is True

        # Cancel confirmation
        state.cancel_regenerate_confirm()
        assert state.show_regenerate_confirm is False

    def test_sync_mode_setter(self):
        """Test sync mode setter."""
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()

        # Default mode
        assert state.sync_mode == "incremental"

        # Set to regenerate
        state.set_sync_mode("regenerate")
        assert state.sync_mode == "regenerate"

        # Set back to incremental
        state.set_sync_mode("incremental")
        assert state.sync_mode == "incremental"

    def test_track_selection(self):
        """Test track selection toggle and clear."""
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()

        # Initially empty
        assert state.selected_tracks == []

        # Toggle a track on
        state.toggle_track_selection("1")
        assert "1" in state.selected_tracks

        # Toggle another track on
        state.toggle_track_selection("2")
        assert "1" in state.selected_tracks
        assert "2" in state.selected_tracks

        # Toggle first track off
        state.toggle_track_selection("1")
        assert "1" not in state.selected_tracks
        assert "2" in state.selected_tracks

        # Clear selection
        state.clear_selection()
        assert state.selected_tracks == []

    def test_select_all_tracks(self):
        """Test selecting all tracks on current page."""
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()
        state.tracks = [
            {"id": "1", "title": "Track 1"},
            {"id": "2", "title": "Track 2"},
            {"id": "3", "title": "Track 3"},
        ]

        # Select all
        state.select_all_tracks()
        assert "1" in state.selected_tracks
        assert "2" in state.selected_tracks
        assert "3" in state.selected_tracks

    # ── Library filters (webui-input-responsiveness, task 6.1) ─────────

    @staticmethod
    def _counting_state() -> tuple[Any, list[int]]:
        """A LibraryState whose load_tracks only counts its calls."""
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()
        calls: list[int] = []
        state.load_tracks = lambda: calls.append(state.current_page)  # type: ignore[method-assign]
        return state, calls

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("19", ""),
            ("1995", "1995"),
            ("abcd", ""),
            ("", ""),
            (" 1995 ", "1995"),
            ("19955", ""),
            ("1e3", ""),
            ("-1995", ""),
            ("199.5", ""),
            (None, ""),
        ],
    )
    def test_year_or_blank(self, raw: Any, expected: str) -> None:
        from plexmix.ui.states.library_state import _year_or_blank

        assert _year_or_blank(raw) == expected

    def test_partial_year_is_ignored_without_reload(self) -> None:
        state, calls = self._counting_state()
        state.current_page = 3

        state.set_year_min("19")  # still typing: no bound, list unchanged
        assert state.year_min == "" and calls == [] and state.current_page == 3
        state.set_year_min("1995")
        assert state.year_min == "1995" and calls == [1]
        state.set_year_min("1995")  # blur after the debounced commit: no-op
        assert calls == [1]
        state.set_year_min("199")  # bound deleted back to a partial year: removed
        assert state.year_min == "" and calls == [1, 1]

        state.set_year_max("abcd")
        assert state.year_max == "" and len(calls) == 2
        state.set_year_max("1999")
        assert state.year_max == "1999" and len(calls) == 3

    @pytest.mark.parametrize(
        "setter,attr,value",
        [
            ("set_search_query", "search_query", "coltrane"),
            ("set_genre_filter", "genre_filter", "jazz"),
            ("set_tag_filter", "tag_filter", "melancholy"),
            ("set_year_min", "year_min", "1990"),
            ("set_year_max", "year_max", "1999"),
        ],
    )
    def test_text_filter_setters_are_idempotent(self, setter: str, attr: str, value: str) -> None:
        """A changed value returns to page 1 and reloads once; an unchanged one is a no-op."""
        state, calls = self._counting_state()
        state.current_page = 4

        getattr(state, setter)(value)
        assert getattr(state, attr) == value
        assert calls == [1]  # reloaded once, on page 1
        state.current_page = 2
        getattr(state, setter)(value)  # blur after the debounced commit
        assert calls == [1] and state.current_page == 2

    def test_set_search_query_is_a_plain_ordered_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No background task, no server-side sleep, no session-scoped 'search' job."""
        import inspect
        from unittest.mock import MagicMock

        from plexmix.ui import job_manager
        from plexmix.ui.states.library_state import LibraryState

        handler = LibraryState.set_search_query
        assert not inspect.iscoroutinefunction(handler)
        assert not getattr(handler, "_background", False)
        register, cancel = MagicMock(), MagicMock()
        monkeypatch.setattr(job_manager.jobs, "register_task", register)
        monkeypatch.setattr(job_manager.jobs, "cancel_task", cancel)
        state, calls = self._counting_state()
        assert state.set_search_query("night") is None  # applied synchronously, in order
        assert state.search_query == "night" and calls == [1]
        register.assert_not_called()
        cancel.assert_not_called()

    def test_no_session_search_task_remains(self) -> None:
        """Nothing in the UI registers or cancels a debounced 'search' job any more."""
        import re

        ui_root = Path(__file__).resolve().parents[2] / "src" / "plexmix" / "ui"
        pattern = re.compile(r"(register_task|cancel_task)\([^)]*[\"']search[\"']")
        offenders = [
            str(path.relative_to(ui_root))
            for path in ui_root.rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == []

    def test_toggle_has_audio_is_idempotent(self) -> None:
        state, calls = self._counting_state()
        state.current_page = 5
        state.toggle_has_audio(True)
        assert state.has_audio is True and calls == [1]
        state.toggle_has_audio(True)
        assert calls == [1]
        state.toggle_has_audio(False)
        assert state.has_audio is False and calls == [1, 1]

    def test_clear_filters_resets_every_filter_without_writing_the_fields(self) -> None:
        """The Clear button empties the fields in the browser at click time
        (``data-clear-form``); a ``set_value`` arriving a round trip later would
        wipe text typed after the click, so none is returned."""
        from plexmix.ui.states.library_state import LIBRARY_FILTER_IDS

        state, calls = self._counting_state()
        state.search_query = "coltrane"
        state.genre_filter = "jazz"
        state.year_min = "1960"
        state.year_max = "1969"
        state.tag_filter = "melancholy"
        state.has_audio = True
        state.current_page = 3

        assert state.clear_filters() is None

        assert set(LIBRARY_FILTER_IDS) == {
            "lib-search",
            "lib-genre",
            "lib-year-min",
            "lib-year-max",
            "lib-tag",
        }
        assert (state.search_query, state.genre_filter, state.tag_filter) == ("", "", "")
        assert (state.year_min, state.year_max) == ("", "")
        assert state.has_audio is False
        assert state.current_page == 1 and calls == [1]

    def test_apply_filter_form_applies_every_field_with_one_reload(self) -> None:
        state, calls = self._counting_state()
        state.search_query = "colt"  # a debounced commit still pending: form wins
        state.has_audio = True
        state.current_page = 6
        form_data = {
            "search": "coltrane",
            "genre": "jazz",
            "year_min": "1960",
            "year_max": "19",  # partial: no upper bound
            "tag": "melancholy",
            # Reflex also adds each field's ref value under its id; they are ignored.
            "lib_search": "coltrane",
            "lib_year_max": "19",
        }

        assert state.apply_filter_form(form_data) is None

        assert calls == [1]
        assert state.search_query == "coltrane"
        assert state.genre_filter == "jazz"
        assert (state.year_min, state.year_max) == ("1960", "")
        assert state.tag_filter == "melancholy"
        assert state.has_audio is True  # the checkbox commits on its own

    def test_apply_filter_form_unchanged_or_missing_keys_do_not_reload(self) -> None:
        state, calls = self._counting_state()
        state.search_query = "night"
        state.genre_filter = "jazz"
        state.current_page = 2

        state.apply_filter_form({"search": "night", "genre": "jazz"})
        assert calls == [] and state.current_page == 2
        state.apply_filter_form({"tag": "chill"})  # other fields keep their values
        assert (state.search_query, state.genre_filter, state.tag_filter) == (
            "night",
            "jazz",
            "chill",
        )
        assert calls == [1]

    def test_submit_bulk_tags_uses_form_data(self) -> None:
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()
        state.show_bulk_tag_dialog = True
        state.bulk_tag_input = "chil"  # the blur commit had not arrived yet

        result = state.submit_bulk_tags({"tags": "chill, focus", "lib_bulk_tags": "x"})

        assert state.bulk_tag_input == "chill, focus"
        assert result is LibraryState.apply_bulk_tags
        state.submit_bulk_tags({})
        assert state.bulk_tag_input == ""

    def test_bulk_tag_blur_commit_ignored_after_close(self) -> None:
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()
        state.show_bulk_tag_dialog = True
        state.set_bulk_tag_input("chill")
        assert state.bulk_tag_input == "chill"
        state.set_bulk_tag_dialog_open(False)
        state.set_bulk_tag_input("chill")  # late blur as the dialog unmounts
        assert state.bulk_tag_input == ""

    @pytest.mark.parametrize("analyzed,expected", [("0", False), ("12", True)])
    def test_on_load_drops_hidden_audio_filter(self, analyzed: str, expected: bool) -> None:
        """The audio checkbox is hidden with no analyzed tracks; a stale 'on' is dropped."""
        state, calls = self._counting_state()
        state.check_auth = lambda: True  # type: ignore[method-assign]
        state.load_app_data = lambda: None  # type: ignore[method-assign]
        state.poll_task_progress = lambda: None  # type: ignore[method-assign]
        state.audio_analyzed_tracks = analyzed
        state.has_audio = True

        state.on_load()

        assert state.has_audio is expected
        assert len(calls) == 1

    # ── Library selection (webui-honest-status-and-first-run, task 9.2) ──

    def test_offpage_selected_count_after_paging(self) -> None:
        """50 selected on page 1, then page 2 shows "50 selected · 50 on other pages"."""
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()
        state.page_size = 50
        state.total_filtered_tracks = 120
        state.tracks = [{"id": str(i)} for i in range(1, 51)]
        state.selected_tracks = []
        state.toggle_select_all(True)
        assert state.offpage_selected_count == 0

        def page_two() -> None:
            state.tracks = [{"id": str(i)} for i in range(51, 101)]

        state.load_tracks = page_two  # type: ignore[method-assign]
        state.next_page()
        assert state.offpage_selected_count == 50
        assert state.selection_label == "50 selected · 50 on other pages"

    def test_bulk_tag_merge_add_versus_replace(self) -> None:
        """Add merges case-insensitively and keeps order; Replace is the entered text."""
        from plexmix.ui.states.library_state import merge_tags, parse_tag_text

        merged = merge_tags(parse_tag_text("chill,piano"), parse_tag_text("Piano, focus"))
        assert ",".join(merged) == "chill,piano,focus"
        assert parse_tag_text("Piano, focus") == ["Piano", "focus"]

    def test_table_starts_as_a_loading_placeholder(self) -> None:
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()
        assert state.tracks_loaded is False and state.table_view == "loading"
        state.tracks_loaded = True
        state.tracks = []
        state.library_has_tracks = True
        assert state.table_view == "no_match"
        state.library_has_tracks = False
        assert state.table_view == "not_synced"


class TestTaggingState:
    """Test cases for TaggingState."""

    def test_filter_criteria(self):
        """Test tagging filter criteria."""
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()

        # Set filter criteria
        state.genre_filter = "jazz"
        state.year_min = "2010"
        state.year_max = "2020"
        state.artist_filter = "Miles"
        state.has_no_tags = True

        # Verify filters
        assert state.genre_filter == "jazz"
        assert state.year_min == "2010"
        assert state.year_max == "2020"
        assert state.artist_filter == "Miles"
        assert state.has_no_tags is True

    def test_filter_setters(self):
        """Test filter setter methods."""
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()

        # Test set_genre_filter
        state.set_genre_filter("rock")
        assert state.genre_filter == "rock"

        # Test set_artist_filter
        state.set_artist_filter("Beatles")
        assert state.artist_filter == "Beatles"

        # Test set_year_min
        state.set_year_min("1990")
        assert state.year_min == "1990"

        state.set_year_min("")
        assert state.year_min == ""

        # Test set_year_max
        state.set_year_max("2020")
        assert state.year_max == "2020"

        state.set_year_max("")
        assert state.year_max == ""

        # Test toggle_has_no_tags
        assert state.has_no_tags is False
        state.toggle_has_no_tags()
        assert state.has_no_tags is True
        state.toggle_has_no_tags()
        assert state.has_no_tags is False

    def test_tag_editing(self):
        """Test inline tag editing state."""
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()

        # Start editing a track
        state.start_edit_tag("1", "chill,relaxing", "study", "piano")

        # Verify editing state
        assert state.editing_track_id == "1"
        assert state.edit_tags == "chill,relaxing"
        assert state.edit_environments == "study"
        assert state.edit_instruments == "piano"

        # Cancel editing
        state.cancel_edit()
        assert state.editing_track_id == ""
        assert state.edit_tags == ""
        assert state.edit_environments == ""
        assert state.edit_instruments == ""

    def test_edit_setters(self):
        """Test inline edit setter methods."""
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()

        # Test set_edit_tags
        state.set_edit_tags("new,tags")
        assert state.edit_tags == "new,tags"

        # Test set_edit_environments
        state.set_edit_environments("party,workout")
        assert state.edit_environments == "party,workout"

        # Test set_edit_instruments
        state.set_edit_instruments("guitar,drums")
        assert state.edit_instruments == "guitar,drums"

    def test_cancel_tagging(self):
        """Test cancel tagging sets cancellation event via task store."""
        from plexmix.ui.states.tagging_state import TaggingState
        from plexmix.ui.job_manager import task_store

        state = TaggingState()

        # Simulate that a persistent task was started
        task_store.start("tagging")

        # Initially not cancelled
        assert not task_store.is_cancelled("tagging")

        # Cancel should set the event
        state.cancel_tagging()
        assert task_store.is_cancelled("tagging")
        assert state.tagging_message == "Cancelling tagging..."

        # Clean up
        task_store.clear("tagging")

    # ── Client-owned filter fields and forms (webui-input-responsiveness 7.1) ──

    @staticmethod
    def _counting_tagging_state() -> Any:
        """A TaggingState that counts assignments to the artist and genre filters."""
        from plexmix.ui.states.tagging_state import TaggingState

        class _Counting(TaggingState):
            writes: dict[str, int] = {}

            def __setattr__(self, name: str, value: Any) -> None:
                if name in ("artist_filter", "genre_filter", "stale_days"):
                    type(self).writes[name] = type(self).writes.get(name, 0) + 1
                super().__setattr__(name, value)

        _Counting.writes = {}
        return _Counting()

    def test_artist_and_genre_setters_return_early_when_unchanged(self) -> None:
        state = self._counting_tagging_state()
        writes = type(state).writes
        state.set_artist_filter("Miles")
        state.set_genre_filter("jazz")
        assert (state.artist_filter, state.genre_filter) == ("Miles", "jazz")
        before = dict(writes)
        # A blur commit after the debounced one carries the same text: no-op.
        state.set_artist_filter("Miles")
        state.set_genre_filter("jazz")
        assert writes == before
        state.set_genre_filter("jazz fusion")
        assert state.genre_filter == "jazz fusion"
        assert writes["genre_filter"] == before["genre_filter"] + 1

    @pytest.mark.parametrize(
        ("typed", "stored"),
        [
            ("30", "30"),
            (" 45 ", "45"),
            ("0", "0"),
            ("007", "007"),
            ("abc", ""),
            ("3a", ""),
            ("-3", ""),
            ("1.5", ""),
            ("1e3", ""),
            ("\u00b2", ""),  # a Unicode digit int() rejects
            ("", ""),
            (None, ""),
        ],
    )
    def test_set_stale_days_keeps_digits_only(self, typed: Any, stored: str) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()
        state.stale_days = "99"
        state.set_stale_days(typed)
        assert state.stale_days == stored
        if stored:
            assert int(state.stale_days) >= 0  # Preview's int() can't raise

    def test_invalid_stale_days_after_a_valid_one_means_no_filter(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()
        state.set_stale_days("30")
        assert state.stale_days == "30"
        state.set_stale_days("abc")
        assert state.stale_days == ""

    def test_apply_filter_form_returns_preview_selection(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()
        # State holds older commits; the submitted form's text wins.
        state.artist_filter = "Mil"
        state.genre_filter = ""
        result = state.apply_filter_form(
            {
                "artist": "Miles Davis",
                "genre": "jazz",
                "stale_days": "30",
                "tag_filter_artist": "ref-keyed duplicate, ignored",
            }
        )
        assert result is TaggingState.preview_selection
        assert state.artist_filter == "Miles Davis"
        assert state.genre_filter == "jazz"
        assert state.stale_days == "30"

    def test_apply_filter_form_sanitizes_stale_days_and_blanks(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()
        state.artist_filter = "Miles"
        state.stale_days = "30"
        result = state.apply_filter_form({"artist": None, "genre": "", "stale_days": "abc"})
        assert result is TaggingState.preview_selection
        assert (state.artist_filter, state.genre_filter, state.stale_days) == ("", "", "")

    def test_apply_filter_form_missing_keys_keep_state(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()
        state.artist_filter = "Miles"
        state.genre_filter = "jazz"
        state.stale_days = "30"
        assert state.apply_filter_form({}) is TaggingState.preview_selection
        assert (state.artist_filter, state.genre_filter, state.stale_days) == (
            "Miles",
            "jazz",
            "30",
        )

    def test_apply_filter_form_goes_through_the_setters(self, monkeypatch: Any) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        calls: list[tuple[str, str]] = []
        for name in ("set_artist_filter", "set_genre_filter", "set_stale_days"):
            monkeypatch.setattr(
                TaggingState,
                name,
                (lambda n: lambda self, v: calls.append((n, v)))(name),
                raising=True,
            )
        TaggingState().apply_filter_form({"artist": "a", "genre": "g", "stale_days": "7"})
        assert calls == [
            ("set_artist_filter", "a"),
            ("set_genre_filter", "g"),
            ("set_stale_days", "7"),
        ]

    def test_submit_tag_edit_takes_values_from_form_data(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()
        state.start_edit_tag("7", "old", "old", "old")
        # Typed but not yet committed (compose fields commit on blur only).
        result = state.submit_tag_edit(
            {"tags": "dreamy, warm", "environments": "night drive", "instruments": "synth"}
        )
        assert result is TaggingState.save_tag_edit
        assert (state.edit_tags, state.edit_environments, state.edit_instruments) == (
            "dreamy, warm",
            "night drive",
            "synth",
        )

    def test_tag_all_untagged_keeps_the_previewed_filter_fields(self) -> None:
        # webui-honest-status-and-first-run 8.1 folded tag_all_untagged into the scope
        # confirmation: it is the "Tag only the N untagged tracks" action and runs the
        # previewed filters plus has_no_tags, so no client-owned field is reset (and
        # no rx.set_value is needed). It used to clear Artist/Genre and start at once.
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()
        state.access_allowed = lambda: True  # type: ignore[method-assign]
        state.artist_filter = "Miles"
        state.genre_filter = "jazz"
        state.preview_count = 59
        state.preview_tagged_count = 40
        state.tag_confirm_open = True
        assert state.tag_all_untagged() is TaggingState.start_untagged_tagging
        assert (state.artist_filter, state.genre_filter) == ("Miles", "jazz")
        assert state.has_no_tags is False
        assert state.tag_confirm_open is False

    def test_tag_all_untagged_denied_returns_nothing(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = TaggingState()
        state.access_allowed = lambda: False  # type: ignore[method-assign]
        state.artist_filter = "Miles"
        assert state.tag_all_untagged() is None
        assert state.artist_filter == "Miles"

    def test_filter_and_editor_ids(self) -> None:
        from plexmix.ui.states import tagging_state

        assert tagging_state.ARTIST_FILTER_ID == "tag-filter-artist"
        assert tagging_state.GENRE_FILTER_ID == "tag-filter-genre"
        assert tagging_state.STALE_DAYS_ID == "tag-filter-stale-days"
        # The editor ids keyboard-and-semantics gave the fields (labels point at them).
        assert tagging_state.EDIT_MOODS_ID == "tag-edit-moods"
        assert tagging_state.EDIT_ENVIRONMENTS_ID == "tag-edit-envs"
        assert tagging_state.EDIT_INSTRUMENTS_ID == "tag-edit-instr"

    def test_recently_tagged_rows_carry_raw_and_display_tags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # webui-zoom-and-narrow-fit 7.2: each row keeps the stored ``tags`` (the Edit
        # button prefills the editor with it) and adds ``tags_display`` ("a, b") so a
        # long list can wrap. No real DB and no ~/.plexmix: both are patched.
        from unittest.mock import MagicMock

        from plexmix.config import settings as settings_module
        from plexmix.database import sqlite_manager as sqlite_module
        from plexmix.ui.states.tagging_state import TaggingState

        db_path = MagicMock()
        db_path.exists.return_value = True
        fake_settings = MagicMock()
        fake_settings.database.get_db_path.return_value = db_path
        monkeypatch.setattr(
            settings_module.Settings, "load_from_file", staticmethod(lambda: fake_settings)
        )
        db = MagicMock()
        db.get_recently_tagged_tracks.return_value = [
            {"id": 7, "title": "Rumours", "tags": "indie pop,dreamy,atmospheric", "album": None},
            {"id": 8, "title": "Spaced", "tags": " a , ,b,", "album": "X"},
            {"id": 9, "title": "Bare", "tags": None, "album": "Y"},
        ]
        manager = MagicMock(return_value=db)
        monkeypatch.setattr(sqlite_module, "SQLiteManager", manager)

        state = TaggingState()
        state.load_recently_tagged()

        manager.assert_called_once_with(str(db_path))
        db.get_recently_tagged_tracks.assert_called_once_with(limit=100)
        assert state.recent_error == ""
        assert state.recent_loaded is True
        rows = state.recently_tagged_tracks
        assert [r["tags"] for r in rows] == ["indie pop,dreamy,atmospheric", " a , ,b,", ""]
        assert [r["tags_display"] for r in rows] == ["indie pop, dreamy, atmospheric", "a, b", ""]
        # Every value is still a string for Reflex (None becomes "").
        assert rows[0] == {
            "id": "7",
            "title": "Rumours",
            "tags": "indie pop,dreamy,atmospheric",
            "album": "",
            "tags_display": "indie pop, dreamy, atmospheric",
        }


class TestValidation:
    """Test validation utility functions."""

    def test_validate_url(self):
        """Test URL validation."""
        from plexmix.ui.utils.validation import validate_url

        # Valid URLs
        valid, error = validate_url("http://localhost:32400")
        assert valid is True
        assert error is None

        valid, error = validate_url("https://example.com")
        assert valid is True
        assert error is None

        # Invalid URLs
        valid, error = validate_url("not a url")
        assert valid is False
        assert error is not None

        valid, error = validate_url("")
        assert valid is False
        assert error is not None

        valid, error = validate_url("ftp://example.com")
        assert valid is False
        assert "http or https" in error

    def test_validate_api_key(self):
        """Test API key validation."""
        from plexmix.ui.utils.validation import validate_api_key

        # OpenAI key
        valid, error = validate_api_key("sk-" + "a" * 48, "openai")
        assert valid is True

        valid, error = validate_api_key("wrong-prefix", "openai")
        assert valid is False

        # Gemini key
        valid, error = validate_api_key("a" * 39, "gemini")
        assert valid is True

        valid, error = validate_api_key("short", "gemini")
        assert valid is False

    def test_validate_playlist_name(self):
        """Test playlist name validation."""
        from plexmix.ui.utils.validation import validate_playlist_name

        # Valid names
        valid, error = validate_playlist_name("My Playlist")
        assert valid is True

        # Invalid names
        valid, error = validate_playlist_name("")
        assert valid is False

        valid, error = validate_playlist_name("Playlist/With/Slashes")
        assert valid is False

        valid, error = validate_playlist_name("a" * 256)
        assert valid is False

    def test_validate_year(self):
        """Test year validation."""
        from plexmix.ui.utils.validation import validate_year

        # Valid years
        valid, error = validate_year(2020)
        assert valid is True

        valid, error = validate_year(None)  # Optional
        assert valid is True

        # Invalid years
        valid, error = validate_year(1899)
        assert valid is False

        valid, error = validate_year(2101)
        assert valid is False

        valid, error = validate_year("not a year")
        assert valid is False

    def test_validate_batch_size(self):
        """Test batch size validation."""
        from plexmix.ui.utils.validation import validate_batch_size

        # Valid sizes
        valid, error = validate_batch_size(50)
        assert valid is True

        # Invalid sizes
        valid, error = validate_batch_size(0)
        assert valid is False

        valid, error = validate_batch_size(101)
        assert valid is False

        valid, error = validate_batch_size("not a number")
        assert valid is False


class TestSettingsStateExpanded:
    """Expanded tests for SettingsState setters and sync logic."""

    def test_set_audio_enabled(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.set_audio_enabled(True)
        assert state.audio_enabled is True
        state.set_audio_enabled(False)
        assert state.audio_enabled is False

    def test_set_audio_analyze_on_sync(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.set_audio_analyze_on_sync(True)
        assert state.audio_analyze_on_sync is True

    def test_set_audio_duration_limit_valid(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.set_audio_duration_limit("30")
        assert state.audio_duration_limit == 30

    def test_set_audio_duration_limit_clamp_high(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.set_audio_duration_limit("999")
        assert state.audio_duration_limit == 300

    def test_set_audio_duration_limit_clamp_low(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.set_audio_duration_limit("-5")
        assert state.audio_duration_limit == 0

    def test_set_audio_duration_limit_invalid(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.set_audio_duration_limit("abc")
        assert state.audio_duration_limit == 60  # fallback

    def test_sync_embedding_dimension_gemini(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.embedding_provider = "gemini"
        state._sync_embedding_dimension()
        assert state.embedding_dimension == 3072

    def test_sync_embedding_dimension_openai(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.embedding_provider = "openai"
        state._sync_embedding_dimension()
        assert state.embedding_dimension == 1536

    def test_sync_embedding_dimension_cohere(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.embedding_provider = "cohere"
        state._sync_embedding_dimension()
        assert state.embedding_dimension == 1024

    def test_sync_embedding_dimension_local_known(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.embedding_provider = "local"
        state.embedding_model = "all-MiniLM-L6-v2"
        state._sync_embedding_dimension()
        assert state.embedding_dimension == 384

    def test_sync_embedding_dimension_local_unknown(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.embedding_provider = "local"
        state.embedding_model = "unknown-model"
        state._sync_embedding_dimension()
        assert state.embedding_dimension == 384

    def test_set_embedding_provider_triggers_sync(self):
        from unittest.mock import patch
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        with patch("plexmix.ui.states.settings_state.SettingsState._refresh_secret_status"):
            state.set_embedding_provider("openai")
        assert state.embedding_dimension == 1536

    def test_set_ai_provider_custom_has_empty_model_list(self):
        from unittest.mock import patch
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        with patch("plexmix.ui.states.settings_state.SettingsState._refresh_secret_status"):
            state.set_ai_provider("custom")
        # Custom has no catalog models; the dropdown is empty and nothing crashes.
        assert state.ai_models == []

    def test_set_ai_provider_local_selects_default_model(self):
        from unittest.mock import patch
        from plexmix.ui.states.settings_state import SettingsState
        from plexmix.ai.local_provider import LOCAL_LLM_DEFAULT_MODEL

        state = SettingsState()
        state.ai_model = "gpt-5.4-mini"  # a previous provider's selection
        with patch("plexmix.ui.states.settings_state.SettingsState._refresh_secret_status"):
            state.set_ai_provider("local")
        assert state.ai_model == LOCAL_LLM_DEFAULT_MODEL
        assert len(state.ai_models) > 0

    def test_set_ai_provider_refreshes_key_status_for_new_provider(self):
        """Switching provider refreshes the new provider's key *status* (no value)."""
        from unittest.mock import patch
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.secret_stored = {**state.secret_stored, "ai_api_key": True}
        state.secret_source = {**state.secret_source, "ai_api_key": "keyring"}
        # No key stored for the new provider: the status reads "not set".
        with patch.object(SettingsState, "access_allowed", return_value=True), patch(
            "plexmix.config.credentials.get_credential_source", return_value=None
        ) as mock_source:
            state.set_ai_provider("openai")
            mock_source.assert_called_once_with("openai_api_key")
        assert state.secret_stored["ai_api_key"] is False
        assert state.secret_source["ai_api_key"] == ""
        assert not hasattr(state, "ai_api_key")

    def test_set_ai_local_mode_clears_endpoint_error(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.local_endpoint_error = "some error"
        state.set_ai_local_mode("builtin")
        assert state.local_endpoint_error == ""

    def test_set_ai_local_mode_keeps_endpoint_error(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.local_endpoint_error = "some error"
        state.set_ai_local_mode("endpoint")
        assert state.local_endpoint_error == "some error"


class TestDoctorStateExpanded:
    """Expanded tests for DoctorState computed vars."""

    def test_orphaned_embeddings_label(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.doctor_orphaned_embeddings = 5
        assert state.orphaned_embeddings_label == "5 orphaned embeddings"
        state.doctor_orphaned_embeddings = 1
        assert state.orphaned_embeddings_label == "1 orphaned embedding"

    def test_missing_embeddings_label(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.doctor_tracks_needing_embeddings = 10
        assert state.missing_embeddings_label == "10 tracks need embeddings"
        state.doctor_tracks_needing_embeddings = 1
        assert state.missing_embeddings_label == "1 track needs embeddings"
        state.doctor_tracks_needing_embeddings = 1204
        assert state.missing_embeddings_label == "1,204 tracks need embeddings"

    def test_missing_audio_label(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.doctor_tracks_without_audio = 7
        assert state.missing_audio_label == "7 tracks need audio analysis"

    def test_fix_progress_label_zero(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.fix_total = 0
        state.fix_progress = 0
        # Unknown total: no "0 / 0" (the page shows an indeterminate bar instead).
        assert state.fix_progress_label == ""

    def test_fix_progress_label_nonzero(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.fix_total = 20
        state.fix_progress = 5
        assert state.fix_progress_label == "5 / 20"

    def test_embedding_job_running(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.current_fix_target = "embeddings_incremental"
        assert state.embedding_job_running is True
        state.current_fix_target = "embeddings_full"
        assert state.embedding_job_running is True
        state.current_fix_target = "tags"
        assert state.embedding_job_running is False

    def test_incremental_embedding_running(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.current_fix_target = "embeddings_incremental"
        assert state.incremental_embedding_running is True
        state.current_fix_target = ""
        assert state.incremental_embedding_running is False

    def test_full_embedding_running(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.current_fix_target = "embeddings_full"
        assert state.full_embedding_running is True
        state.current_fix_target = ""
        assert state.full_embedding_running is False

    def test_tag_job_running(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.current_fix_target = "tags"
        assert state.tag_job_running is True
        state.current_fix_target = ""
        assert state.tag_job_running is False

    def test_audio_job_running(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.current_fix_target = "audio_analysis"
        assert state.audio_job_running is True
        state.current_fix_target = ""
        assert state.audio_job_running is False

    def test_untagged_tracks_message_with_untagged(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.stats_loaded = True
        state.doctor_total_tracks = 40
        state.doctor_untagged_tracks = 15
        msg = state.untagged_tracks_message
        assert "15 tracks" in msg
        assert "don't have AI-generated tags" in msg
        state.doctor_untagged_tracks = 1
        assert state.untagged_tracks_message == "1 track doesn't have AI-generated tags."

    def test_untagged_tracks_message_all_tagged(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.doctor_untagged_tracks = 0
        # Before a scan measured the library, nothing claims coverage.
        assert "All" not in state.untagged_tracks_message
        state.stats_loaded = True
        state.doctor_total_tracks = 5943
        assert state.untagged_tracks_message == "All 5,943 tracks have AI-generated tags."


class TestLoadAppDataSessionReaping:
    """``load_app_data`` reaps only disconnected sessions' work (job-cancellation)."""

    @staticmethod
    def _state(token: str):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from plexmix.ui.states.app_state import AppState

        state = AppState()
        state.router = SimpleNamespace(session=SimpleNamespace(client_token=token))
        state.check_configuration_status = MagicMock()
        state.load_library_stats = MagicMock()
        return state

    @pytest.fixture
    def fresh_jobs(self, monkeypatch):
        import plexmix.ui.job_manager as job_manager
        from plexmix.ui.job_manager import JobManager

        manager = JobManager()
        monkeypatch.setattr(job_manager, "jobs", manager)
        return manager

    def test_two_live_owners_cancel_neither(self, fresh_jobs, monkeypatch):
        import plexmix.ui.session_liveness as session_liveness

        monkeypatch.setattr(
            session_liveness, "connected_client_tokens", lambda: frozenset({"tok-a", "tok-b"})
        )
        event_a = fresh_jobs.start("tok-a", "generation")
        event_b = fresh_jobs.start("tok-b", "generation")

        # Each tab loads pages repeatedly; neither load cancels the other tab's work.
        for token in ("tok-a", "tok-b", "tok-a", "tok-b"):
            state = self._state(token)
            state.load_app_data()
            state.check_configuration_status.assert_called_once()
            state.load_library_stats.assert_called_once()

        assert not event_a.is_set()
        assert not event_b.is_set()
        assert fresh_jobs.active_job_count == 2

    def test_reaps_disconnected_owner_after_grace(self, fresh_jobs, monkeypatch):
        import time

        import plexmix.ui.session_liveness as session_liveness
        from plexmix.ui.job_manager import SESSION_REAP_GRACE_S

        monkeypatch.setattr(
            session_liveness, "connected_client_tokens", lambda: frozenset({"tok-b"})
        )
        clock = [500.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        orphan = fresh_jobs.start("tok-a", "generation")
        live = fresh_jobs.start("tok-b", "generation")
        state_b = self._state("tok-b")

        state_b.load_app_data()
        clock[0] += SESSION_REAP_GRACE_S / 2
        state_b.load_app_data()
        assert not orphan.is_set()

        clock[0] += SESSION_REAP_GRACE_S / 2
        state_b.load_app_data()
        assert orphan.is_set()
        assert not live.is_set()

    def test_unknown_liveness_cancels_nothing(self, fresh_jobs, monkeypatch):
        import plexmix.ui.session_liveness as session_liveness

        monkeypatch.setattr(session_liveness, "connected_client_tokens", lambda: None)
        event_a = fresh_jobs.start("tok-a", "generation")
        self._state("tok-b").load_app_data()
        assert not event_a.is_set()

    @pytest.fixture
    def fresh_drafts(self, monkeypatch):
        import plexmix.ui.secret_drafts as secret_drafts_module
        from plexmix.ui.secret_drafts import SecretDrafts

        drafts = SecretDrafts()
        monkeypatch.setattr(secret_drafts_module, "secret_drafts", drafts)
        return drafts

    def test_reaped_session_drafts_are_discarded(self, fresh_jobs, fresh_drafts, monkeypatch):
        """Integration: tokens returned by ``reap_disconnected`` feed ``secret_drafts.reap``."""
        import time

        import plexmix.ui.session_liveness as session_liveness
        from plexmix.ui.job_manager import SESSION_REAP_GRACE_S

        monkeypatch.setattr(
            session_liveness, "connected_client_tokens", lambda: frozenset({"tok-b"})
        )
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        orphan = fresh_jobs.start("tok-a", "generation")
        live = fresh_jobs.start("tok-b", "generation")
        fresh_drafts.stage("tok-a", "ai_api_key", "SENTINEL-OPENAI-0000")
        fresh_drafts.mark_clear("tok-a", "ai_custom_api_key")
        fresh_drafts.stage("tok-b", "plex_token", "SENTINEL-PLEX-0000")
        state_b = self._state("tok-b")

        # First sighting of the disconnected tab: inside the grace period, nothing is dropped.
        state_b.load_app_data()
        assert fresh_drafts.staged_fields("tok-a") == {
            "ai_api_key": "replace",
            "ai_custom_api_key": "clear",
        }

        clock[0] += SESSION_REAP_GRACE_S
        state_b.load_app_data()

        assert orphan.is_set()
        assert not live.is_set()
        # The reaped session's drafts are gone; the connected session's draft survives.
        assert fresh_drafts.action("tok-a", "ai_api_key") == ""
        assert fresh_drafts.action("tok-a", "ai_custom_api_key") == ""
        assert fresh_drafts.get("tok-a", "ai_api_key") is None
        assert fresh_drafts.action("tok-b", "plex_token") == "replace"
        assert fresh_drafts.get("tok-b", "plex_token") == "SENTINEL-PLEX-0000"
        assert len(fresh_drafts) == 1

    def test_unknown_liveness_keeps_drafts(self, fresh_jobs, fresh_drafts, monkeypatch):
        import plexmix.ui.session_liveness as session_liveness

        monkeypatch.setattr(session_liveness, "connected_client_tokens", lambda: None)
        fresh_jobs.start("tok-a", "generation")
        fresh_drafts.stage("tok-a", "ai_api_key", "SENTINEL-OPENAI-0000")
        self._state("tok-b").load_app_data()
        assert fresh_drafts.action("tok-a", "ai_api_key") == "replace"


class TestAppStateAuthSession:
    """Auth gate: unknown state, cross-tab session cookie, logout (D6/D7)."""

    PASSWORD = "SENTINEL-UIPW-0000"

    @pytest.fixture(autouse=True)
    def _session_env(self, tmp_path, monkeypatch):
        from plexmix.ui import auth_session

        monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(auth_session, "_file_keys", {})
        monkeypatch.setattr(auth_session, "_memory_keys", {})

    @staticmethod
    def _configure_password(monkeypatch, password):
        from plexmix.ui.states.app_state import AppState

        monkeypatch.setattr(AppState, "_configured_ui_password", staticmethod(lambda: password))

    def test_initial_state_is_unknown(self):
        from plexmix.ui.states.app_state import AppState

        state = AppState()
        assert state.auth_checked is False
        assert state.is_authenticated is False
        assert state.auth_session == ""

    def test_no_password_is_authenticated_and_checked(self, monkeypatch):
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, None)
        state = AppState()
        assert state.check_auth() is True
        assert state.auth_checked is True
        assert state.is_authenticated is True
        assert state.auth_required is False

    def test_password_without_cookie_requires_login(self, monkeypatch):
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        state = AppState()
        assert state.check_auth() is False
        assert state.auth_checked is True
        assert state.auth_required is True
        assert state.is_authenticated is False

    def test_password_with_valid_cookie_authenticates_without_login(self, monkeypatch):
        from plexmix.ui import auth_session
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        state = AppState()
        state.auth_session = auth_session.issue(self.PASSWORD)
        assert state.check_auth() is True
        assert state.is_authenticated is True
        assert state.auth_checked is True

    def test_login_in_one_tab_lets_a_new_tab_in(self, monkeypatch):
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        tab_a = AppState()
        tab_a.attempt_login({"password": self.PASSWORD})
        assert tab_a.is_authenticated is True
        assert tab_a.auth_checked is True
        assert tab_a.auth_session
        assert self.PASSWORD not in tab_a.auth_session
        assert tab_a.login_error == ""

        # A new tab has fresh per-tab state; Reflex hydrates the cookie into it.
        tab_b = AppState()
        tab_b.auth_session = tab_a.auth_session
        assert tab_b.check_auth() is True

    def test_wrong_password_issues_no_cookie(self, monkeypatch):
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        state = AppState()
        state.attempt_login({"password": "SENTINEL-UIPW-1111"})
        assert state.is_authenticated is False
        assert state.auth_session == ""
        assert state.login_error == "Incorrect password"
        assert state.login_password == ""
        assert state.auth_checked is True

    def test_each_wrong_password_counts_a_new_failure(self, monkeypatch):
        """The login view keys its alert text by login_failures, so a repeated
        "Incorrect password" is re-inserted (and re-announced) on every attempt."""
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        state = AppState()
        state.attempt_login({"password": "SENTINEL-UIPW-1111"})
        first = state.login_failures
        state.attempt_login({"password": "SENTINEL-UIPW-2222"})
        assert state.login_error == "Incorrect password"
        assert state.login_failures == first + 1 == 2
        state.attempt_login({"password": self.PASSWORD})
        assert state.login_error == ""
        assert state.login_failures == 2  # success does not count

    def test_login_fails_when_no_password_configured(self, monkeypatch):
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, None)
        state = AppState()
        state.attempt_login({"password": ""})
        assert state.is_authenticated is False
        assert state.auth_session == ""

    def test_stale_cookie_after_password_change(self, monkeypatch):
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        state = AppState()
        state.attempt_login({"password": self.PASSWORD})
        assert state.check_auth() is True

        self._configure_password(monkeypatch, "SENTINEL-UIPW-2222")
        # Same tab reloads (its per-tab state still says authenticated) and a new tab
        # opens with the old cookie: neither is let in.
        assert state.check_auth() is False
        assert state.is_authenticated is False
        new_tab = AppState()
        new_tab.auth_session = state.auth_session
        assert new_tab.check_auth() is False

    def test_expired_cookie_requires_login(self, monkeypatch):
        import time

        from plexmix.ui import auth_session
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        state = AppState()
        state.auth_session = auth_session.issue(
            self.PASSWORD, now=time.time() - auth_session.SESSION_MAX_AGE_S - 60
        )
        assert state.check_auth() is False

    def test_logout_clears_cookie(self, monkeypatch):
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        state = AppState()
        state.attempt_login({"password": self.PASSWORD})
        result = state.logout()
        assert result == ("remove_cookie", "plexmix_session")
        assert state.auth_session == ""
        assert state.is_authenticated is False
        # The tab re-checks on its next load and stays signed out.
        assert state.check_auth() is False

    def test_reloaded_tab_after_logout_elsewhere_requires_login(self, monkeypatch):
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        tab_a = AppState()
        tab_a.attempt_login({"password": self.PASSWORD})
        tab_b = AppState()
        tab_b.auth_session = tab_a.auth_session
        assert tab_b.check_auth() is True

        tab_a.logout()
        # Reflex resets cookie vars on hydrate and re-reads the (now removed) cookie.
        tab_b.auth_session = ""
        assert tab_b.check_auth() is False

    def test_hydrate_shows_boot_screen_not_the_stale_shell(self, monkeypatch):
        """Reflex calls ``_reset_client_storage`` on every hydrate (reload, reconnect).

        The replayed per-tab state must not say "authenticated" before ``check_auth``
        has re-verified the cookie, or a reload after a logout elsewhere would flash
        the shell and the page's cached content.
        """
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        reflex_base = AppState.__bases__[0]  # rx.State (mocked)
        super_calls = []

        def _base_reset(self):
            super_calls.append(self)
            self.auth_session = ""  # what Reflex does to cookie vars on hydrate

        monkeypatch.setattr(reflex_base, "_reset_client_storage", _base_reset, raising=False)
        tab_b = AppState()
        tab_b.attempt_login({"password": self.PASSWORD})
        assert tab_b.is_authenticated is True and tab_b.auth_checked is True

        tab_b._reset_client_storage()  # the reload's hydrate (cookie removed by a logout)

        assert super_calls == [tab_b]
        assert tab_b.auth_checked is False  # neutral boot screen
        assert tab_b.is_authenticated is False
        assert tab_b.check_auth() is False  # on_load: the login view
        assert tab_b.auth_checked is True

    def test_hydrate_override_carries_reflex_marker(self):
        """Without it, real Reflex refuses the class (EventHandlerShadowsBuiltInStateMethodError)."""
        from plexmix.ui.states.app_state import AppState

        assert AppState.__dict__["_reset_client_storage"].__override_base_method__ is True

    def test_hydrate_then_valid_cookie_lets_the_tab_back_in(self, monkeypatch):
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        monkeypatch.setattr(
            AppState.__bases__[0], "_reset_client_storage", lambda self: None, raising=False
        )
        tab = AppState()
        tab.attempt_login({"password": self.PASSWORD})
        tab._reset_client_storage()
        assert tab.auth_checked is False
        assert tab.check_auth() is True  # the browser re-sent the still-valid cookie
        assert tab.is_authenticated is True

    def test_cookie_declaration(self):
        """The cookie is declared with the session max age and SameSite=Strict."""
        import inspect

        from plexmix.ui.states import app_state

        source = inspect.getsource(app_state.AppState)
        assert "rx.Cookie(" in source
        assert "name=COOKIE_NAME" in source
        assert "max_age=SESSION_MAX_AGE_S" in source
        assert 'same_site="strict"' in source
        assert app_state.COOKIE_NAME == "plexmix_session"

    # --- access_allowed: the server-side gate trusts only the signed session ---

    def test_access_allowed_without_password(self, monkeypatch):
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, None)
        assert AppState().access_allowed() is True

    def test_access_allowed_ignores_forged_flag(self, monkeypatch):
        """``set_is_authenticated`` is a Reflex auto-setter any client can dispatch."""
        from plexmix.ui.states.app_state import AppState
        from plexmix.ui.states.settings_state import SettingsState

        self._configure_password(monkeypatch, self.PASSWORD)
        for cls in (AppState, SettingsState):
            state = cls()
            state.is_authenticated = True  # what a forged set_is_authenticated(True) does
            assert state.access_allowed() is False
            state.auth_session = "v1.0.forged.deadbeef"  # a forged token fails the HMAC
            assert state.access_allowed() is False

    def test_access_allowed_after_login(self, monkeypatch):
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        state = AppState()
        assert state.access_allowed() is False
        state.attempt_login({"password": self.PASSWORD})
        assert state.access_allowed() is True
        state.logout()
        assert state.access_allowed() is False

    def test_access_allowed_ends_on_password_change_mid_page(self, monkeypatch):
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        state = AppState()
        state.attempt_login({"password": self.PASSWORD})
        assert state.access_allowed() is True

        self._configure_password(monkeypatch, "SENTINEL-UIPW-3333")
        assert state.is_authenticated is True  # the tab has not reloaded yet
        assert state.access_allowed() is False

    def test_access_allowed_rejects_expired_session(self, monkeypatch):
        import time

        from plexmix.ui import auth_session
        from plexmix.ui.states.app_state import AppState

        self._configure_password(monkeypatch, self.PASSWORD)
        state = AppState()
        state.is_authenticated = True
        state.auth_session = auth_session.issue(
            self.PASSWORD, now=time.time() - auth_session.SESSION_MAX_AGE_S - 60
        )
        assert state.access_allowed() is False


class TestAppStateChangelog:
    """Changelog dialog state is filled from the server-side loader (D5)."""

    @pytest.fixture
    def changelog_file(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from plexmix.ui import changelog

        pkg = tmp_path / "plexmix"
        pkg.mkdir()
        path = pkg / "CHANGELOG.md"
        path.write_text("# Changelog\n\nPreamble.\n\n## [Unreleased]\n\n- first\n")
        monkeypatch.setattr(changelog, "resources", SimpleNamespace(files=lambda name: pkg))
        changelog.clear_cache()
        yield path
        changelog.clear_cache()

    @pytest.fixture
    def render_calls(self, monkeypatch):
        from plexmix.ui import changelog

        calls = []
        real = changelog.render_markdown

        def counting(text):
            calls.append(text)
            return real(text)

        monkeypatch.setattr(changelog, "render_markdown", counting)
        return calls

    def test_initial_vars_empty(self):
        from plexmix.ui.states.app_state import AppState

        state = AppState()
        assert state.changelog_html == ""
        assert state.changelog_version == ""
        assert state.changelog_available is False
        assert not hasattr(AppState, "changelog_content")

    def test_open_fills_vars_once(self, changelog_file, render_calls):
        import plexmix
        from plexmix.ui.states.app_state import AppState

        state = AppState()
        state.open_changelog()
        assert state.show_changelog is True
        assert state.changelog_available is True
        assert state.changelog_version == plexmix.__version__
        assert state.changelog_html.startswith("<h2>")
        assert "Preamble" not in state.changelog_html
        assert len(render_calls) == 1

        state.close_changelog()
        assert state.show_changelog is False
        first_html = state.changelog_html
        state.open_changelog()
        assert state.show_changelog is True
        assert len(render_calls) == 1
        assert state.changelog_html == first_html

    def test_second_open_reloads_after_mtime_change(self, changelog_file, render_calls):
        import os

        from plexmix.ui.states.app_state import AppState

        state = AppState()
        state.open_changelog()
        changelog_file.write_text("## [Unreleased]\n\n- second\n")
        st = changelog_file.stat()
        os.utime(changelog_file, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        state.open_changelog()
        assert len(render_calls) == 2
        assert "second" in state.changelog_html

    def test_open_when_unavailable(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        import plexmix
        from plexmix.ui import changelog
        from plexmix.ui.states.app_state import AppState

        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr(changelog, "resources", SimpleNamespace(files=lambda name: empty))
        monkeypatch.setattr(changelog, "_checkout_candidate", lambda: None)
        changelog.clear_cache()
        state = AppState()
        state.open_changelog()
        assert state.show_changelog is True
        assert state.changelog_available is False
        assert state.changelog_html == ""
        assert state.changelog_version == plexmix.__version__


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestLibraryStateTableSemantics:
    """Library enrichment flags, page bounds, sorting and dialog setters (change 2, §7)."""

    @staticmethod
    def _stub_db(monkeypatch: pytest.MonkeyPatch, rows: Any, total: int) -> Any:
        """Patch Settings + SQLiteManager so load_tracks reads ``rows`` and ``total``."""
        from unittest.mock import MagicMock

        from plexmix.config import settings as settings_module
        from plexmix.database import sqlite_manager as sqlite_module

        db_path = MagicMock()
        db_path.exists.return_value = True
        fake_settings = MagicMock()
        fake_settings.database.get_db_path.return_value = db_path
        monkeypatch.setattr(
            settings_module.Settings, "load_from_file", staticmethod(lambda: fake_settings)
        )

        db = MagicMock()
        db.get_tracks.return_value = rows
        db.count_tracks.return_value = total
        manager = MagicMock()
        manager.return_value.__enter__.return_value = db
        manager.return_value.__exit__.return_value = False
        monkeypatch.setattr(sqlite_module, "SQLiteManager", manager)
        return db

    def test_load_tracks_merges_enrichment_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plexmix.ui.states.library_state import LibraryState

        rows = [
            {
                "id": 1,
                "title": "Partly",
                "artist_name": "A",
                "album_title": "X",
                "tags": "chill,focus",
                "has_embedding": 1,
                "has_audio_features": 0,
                "has_musicbrainz": 0,
            },
            {
                "id": 2,
                "title": "Bare",
                "artist_name": "B",
                "album_title": "Y",
                "tags": None,
                "has_embedding": 0,
                "has_audio_features": 1,
                "has_musicbrainz": 1,
            },
        ]
        self._stub_db(monkeypatch, rows, total=2)
        state = LibraryState()
        state.load_tracks()

        assert state.total_filtered_tracks == 2
        first, second = state.tracks
        assert first["id"] == "1" and first["title"] == "Partly"
        assert (first["enr_tags"], first["enr_embedded"], first["enr_audio"], first["enr_mb"]) == (
            "1",
            "1",
            "0",
            "0",
        )
        assert first["enr_label"] == (
            "Tagged, embedded, audio not analyzed, MusicBrainz not matched"
        )
        assert second["tags"] == ""
        assert (second["enr_tags"], second["enr_embedded"]) == ("0", "0")
        assert (second["enr_audio"], second["enr_mb"]) == ("1", "1")
        assert second["enr_label"] == (
            "Not tagged, not embedded, audio analyzed, MusicBrainz matched"
        )
        assert all(isinstance(v, str) for row in state.tracks for v in row.values())

    def test_load_tracks_without_db_rows_stays_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plexmix.ui.states.library_state import LibraryState

        self._stub_db(monkeypatch, [], total=0)
        state = LibraryState()
        state.load_tracks()
        assert state.tracks == []
        assert state.total_filtered_tracks == 0
        assert state.has_next_page is False

    @pytest.mark.parametrize(
        "page,page_size,total,expected",
        [
            (1, 50, 0, False),
            (1, 50, 49, False),
            (1, 50, 50, False),
            (1, 50, 51, True),
            (2, 50, 100, False),
            (2, 50, 101, True),
            (3, 50, 101, False),
        ],
    )
    def test_has_next_page(self, page: int, page_size: int, total: int, expected: bool) -> None:
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()
        state.current_page = page
        state.page_size = page_size
        state.total_filtered_tracks = total
        assert state.has_next_page is expected

    def test_has_next_page_agrees_with_next_page(self) -> None:
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()
        state.page_size = 20
        state.total_filtered_tracks = 45
        state.load_tracks = lambda: None  # type: ignore[method-assign]
        seen = []
        while state.has_next_page:
            state.next_page()
            seen.append(state.current_page)
        assert seen == [2, 3]
        state.next_page()  # already on the last page: no-op
        assert state.current_page == 3

    def test_reaching_a_bound_moves_focus_to_the_other_pager_button(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The activated pager button becomes disabled at a bound; focus must not
        fall to <body>, so the handler focuses the opposite (enabled) button."""
        from plexmix.ui.states import library_state
        from plexmix.ui.states.library_state import LibraryState

        monkeypatch.setattr(library_state.rx, "call_script", lambda js: ("call_script", js))
        scroll = ("call_script", "window.scrollTo({top: 0, behavior: 'smooth'})")
        focus_prev = (
            "call_script",
            "window.pmFocus&&pmFocus.when('#lib-page-prev',{force:true})",
        )
        focus_next = (
            "call_script",
            "window.pmFocus&&pmFocus.when('#lib-page-next',{force:true})",
        )
        state = LibraryState()
        state.page_size = 20
        state.total_filtered_tracks = 45  # 3 pages
        state.load_tracks = lambda: None  # type: ignore[method-assign]

        assert state.next_page() == [scroll]  # page 2: both buttons stay enabled
        assert state.next_page() == [scroll, focus_prev]  # last page
        assert state.next_page() is None  # no-op at the bound
        assert state.previous_page() == [scroll]  # page 2
        assert state.previous_page() == [scroll, focus_next]  # first page
        assert state.previous_page() is None
        assert state.current_page == 1

    def test_set_sort_returns_to_page_one_and_toggles_direction(self) -> None:
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()
        calls = []
        state.load_tracks = lambda: calls.append(  # type: ignore[method-assign]
            (state.sort_column, state.sort_ascending)
        )
        state.current_page = 4

        state.set_sort("artist")
        assert (state.sort_column, state.sort_ascending, state.current_page) == ("artist", True, 1)
        state.current_page = 2
        state.set_sort("artist")
        assert (state.sort_column, state.sort_ascending, state.current_page) == (
            "artist",
            False,
            1,
        )
        state.set_sort("album")
        assert (state.sort_column, state.sort_ascending) == ("album", True)
        assert calls == [("artist", True), ("artist", False), ("album", True)]

    def test_set_sort_keeps_filters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plexmix.ui.states.library_state import LibraryState

        db = self._stub_db(monkeypatch, [], total=0)
        state = LibraryState()
        state.search_query = "night"
        state.genre_filter = "jazz"
        state.set_sort("artist")

        kwargs = db.get_tracks.call_args.kwargs
        assert kwargs["search"] == "night" and kwargs["genre"] == "jazz"
        assert kwargs["sort_column"] == "artist" and kwargs["sort_ascending"] is True
        assert kwargs["offset"] == 0
        count_kwargs = db.count_tracks.call_args.kwargs
        assert count_kwargs["search"] == "night" and count_kwargs["genre"] == "jazz"

    @pytest.mark.parametrize(
        "setter,flag",
        [
            ("set_delete_confirm_open", "show_delete_confirm"),
            ("set_regenerate_confirm_open", "show_regenerate_confirm"),
            ("set_bulk_tag_dialog_open", "show_bulk_tag_dialog"),
        ],
    )
    def test_dialog_setters_idempotent_on_false(self, setter: str, flag: str) -> None:
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()
        setattr(state, flag, True)
        state.bulk_tag_input = "chill"

        getattr(state, setter)(True)  # opening goes through state; True is a no-op
        assert getattr(state, flag) is True

        getattr(state, setter)(False)
        assert getattr(state, flag) is False
        getattr(state, setter)(False)  # a second close (Escape after confirm) is harmless
        assert getattr(state, flag) is False
        if setter == "set_bulk_tag_dialog_open":
            assert state.bulk_tag_input == ""

    def test_alert_dialog_cancel_closes_regenerate_without_syncing(self) -> None:
        """AlertDialog Cancel/Escape route through on_open_change(False) only."""
        from plexmix.ui.states.library_state import LibraryState

        state = LibraryState()
        state.sync_mode = "regenerate"
        assert state.start_sync_or_confirm() is None
        assert state.show_regenerate_confirm is True
        state.set_regenerate_confirm_open(False)
        assert state.show_regenerate_confirm is False
        assert state.is_syncing is False


class TestAppStateCancelDialog:
    """Shell cancel AlertDialog: ``set_cancel_dialog_open`` (job-cancellation)."""

    @pytest.fixture
    def stores(self, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
        """Replace the task store and the session job manager with recording mocks."""
        from unittest.mock import MagicMock

        import plexmix.ui.job_manager as job_manager

        task_store = MagicMock()
        jobs = MagicMock()
        monkeypatch.setattr(job_manager, "task_store", task_store)
        monkeypatch.setattr(job_manager, "jobs", jobs)
        return task_store, jobs

    @staticmethod
    def _state(pending: str = "") -> Any:
        from plexmix.ui.states.app_state import AppState

        state = AppState()
        state.pending_cancel_job = pending
        return state

    def test_closing_clears_pending_job(self) -> None:
        state = self._state("tagging")
        state.set_cancel_dialog_open(False)
        assert state.pending_cancel_job == ""

    def test_closing_twice_is_idempotent(self) -> None:
        state = self._state("__all__")
        state.set_cancel_dialog_open(False)
        state.set_cancel_dialog_open(False)
        assert state.pending_cancel_job == ""

    def test_opening_does_not_change_pending_job(self) -> None:
        state = self._state("sync")
        state.set_cancel_dialog_open(True)
        assert state.pending_cancel_job == "sync"
        empty = self._state("")
        empty.set_cancel_dialog_open(True)
        assert empty.pending_cancel_job == ""

    @pytest.mark.parametrize("job", ["tagging", "__all__"])
    def test_confirm_after_dismiss_cancels_nothing(self, stores: tuple[Any, Any], job: str) -> None:
        task_store, jobs = stores
        state = self._state()
        state.request_cancel(job)
        assert state.pending_cancel_job == job
        state.set_cancel_dialog_open(False)  # Escape / "Keep running"
        assert state.confirm_cancel() is None
        task_store.cancel.assert_not_called()
        task_store.cancel_all_running.assert_not_called()
        assert jobs.method_calls == []
        assert state.pending_cancel_job == ""

    def test_confirm_without_dismiss_still_cancels(self, stores: tuple[Any, Any]) -> None:
        task_store, _jobs = stores
        state = self._state()
        state.request_cancel("tagging")
        state.set_cancel_dialog_open(True)
        assert state.confirm_cancel() is None
        task_store.cancel.assert_called_once_with("tagging")
        assert state.pending_cancel_job == ""


# ============================================================================
# History: playlist identity, keyboard reorder, delete confirm, sort name
# (webui-keyboard-and-semantics, tasks 5.2 / 5.6 / 5.7 / 5.8)
# ============================================================================


def _history_harness() -> type:
    """HistoryState plus the bits of rx.State a background handler needs."""
    from plexmix.ui.states.history_state import HistoryState

    class _HistoryHarness(HistoryState):  # type: ignore[misc, valid-type]
        async def __aenter__(self) -> "_HistoryHarness":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        def access_allowed(self) -> bool:
            return True

    return _HistoryHarness


def _history_tracks(n: int) -> list:
    return [
        {"position": str(i + 1), "id": str(100 + i), "title": f"Song {i + 1}"} for i in range(n)
    ]


@pytest.fixture
def history_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A temp SQLite DB that ``Settings.load_from_file()`` points at (never ~/.plexmix)."""
    from types import SimpleNamespace

    from plexmix.config import settings as settings_mod
    from plexmix.database.models import Album, Artist, Playlist, Track
    from plexmix.database.sqlite_manager import SQLiteManager

    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path))
    db_path = tmp_path / "plexmix.db"
    fake_settings = SimpleNamespace(database=SimpleNamespace(get_db_path=lambda: db_path))
    monkeypatch.setattr(
        settings_mod.Settings, "load_from_file", classmethod(lambda cls, *a, **k: fake_settings)
    )

    with SQLiteManager(str(db_path)) as db:
        artist_ids = {}
        for name in ("Bill Evans", "Chet Baker", "Miles Davis"):
            aid = db.insert_artist(Artist(plex_key=f"a-{name}", name=name))
            alb = db.insert_album(Album(plex_key=f"al-{name}", title=name, artist_id=aid))
            artist_ids[name] = (aid, alb)
        track_ids = []
        for name, count, dur in (("Bill Evans", 3, 61000), ("Miles Davis", 2, 3600000)):
            aid, alb = artist_ids[name]
            for i in range(count):
                track_ids.append(
                    db.insert_track(
                        Track(
                            plex_key=f"t-{name}-{i}",
                            title=f"{name} {i}",
                            artist_id=aid,
                            album_id=alb,
                            duration_ms=dur,
                        )
                    )
                )
        jazz = db.insert_playlist(
            Playlist(name="Late Night Jazz", mood_query="smoky late night jazz", created_by_ai=True)
        )
        db.add_tracks_to_playlist(jazz, track_ids)
        imported = db.insert_playlist(Playlist(name="Imported", created_by_ai=False))
        cur = db.get_connection().cursor()
        cur.execute(
            "UPDATE playlists SET created_at = ? WHERE id = ?", ("2025-12-27 20:35:19.327064", jazz)
        )
        cur.execute(
            "UPDATE playlists SET created_at = ? WHERE id = ?", ("2024-01-02 08:00:00", imported)
        )
        db.get_connection().commit()
    return SimpleNamespace(path=db_path, jazz=str(jazz), imported=str(imported))


@pytest.fixture
def call_script_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``rx.call_script`` return an inspectable ``("call_script", js)`` tuple."""
    from plexmix.ui.states import history_state

    monkeypatch.setattr(history_state.rx, "call_script", lambda js: ("call_script", js))


class TestHistoryPlaylistIdentity:
    """``HistoryState._decorate_playlists`` and its use in load/select (task 5.2)."""

    def test_keys_are_added_without_mutating_input(self) -> None:
        from plexmix.ui.states.history_state import HistoryState

        rows = [
            {
                "id": "7",
                "name": "Late Night Jazz",
                "mood_query": "smoky late night jazz",
                "created_at": "2025-12-27 20:35:19.327064",
            }
        ]
        out = HistoryState._decorate_playlists(
            rows, {7: ["Bill Evans", "Chet Baker", "Miles Davis", "Extra"]}
        )
        assert {"cover_variant", "created_date", "top_artists"} <= set(out[0])
        assert out[0]["created_date"] == "2025-12-27"
        assert out[0]["top_artists"] == "Bill Evans, Chet Baker, Miles Davis"
        assert out[0]["name"] == "Late Night Jazz"  # original keys kept
        assert "cover_variant" not in rows[0]  # input untouched
        assert all(isinstance(v, str) for v in out[0].values())

    def test_variant_matches_shared_helper(self) -> None:
        from plexmix.ui.states.history_state import HistoryState
        from plexmix.ui.utils.playlist_identity import cover_seed, cover_variant

        prompts = ["smoky late night jazz", "rainy day acoustic", "gym bangers", "focus"]
        rows = [
            {"id": str(i), "name": f"P{i}", "mood_query": p, "created_at": ""}
            for i, p in enumerate(prompts)
        ]
        out = HistoryState._decorate_playlists(rows, {})
        for row, decorated in zip(rows, out):
            expected = cover_variant(cover_seed(row["mood_query"], row["name"]))
            assert decorated["cover_variant"] == str(expected)
            assert 0 <= int(decorated["cover_variant"]) < 6

    def test_missing_prompt_falls_back_to_name(self) -> None:
        from plexmix.ui.states.history_state import HistoryState
        from plexmix.ui.utils.playlist_identity import cover_variant

        out = HistoryState._decorate_playlists(
            [
                {"id": "1", "name": "Imported Mix", "mood_query": "", "created_at": ""},
                {"id": "2", "name": "Other"},  # no mood_query key at all
            ],
            {},
        )
        assert out[0]["cover_variant"] == str(cover_variant("Imported Mix"))
        assert out[1]["cover_variant"] == str(cover_variant("Other"))

    def test_missing_date_and_artists_give_empty_strings(self) -> None:
        from plexmix.ui.states.history_state import HistoryState

        out = HistoryState._decorate_playlists(
            [
                {"id": "1", "name": "A", "created_at": ""},
                {"id": "2", "name": "B", "created_at": "not a date"},
                {"id": "x", "name": "C"},  # unparseable id: no artists, no error
            ],
            {1: [], 2: ["Someone"]},
        )
        assert [row["created_date"] for row in out] == ["", "", ""]
        assert [row["top_artists"] for row in out] == ["", "Someone", ""]

    def test_load_playlists_decorates_and_keeps_chosen_order(self, history_db: Any) -> None:
        import asyncio

        state = _history_harness()()
        state.sort_descending = False  # the user chose oldest first
        asyncio.run(state.load_playlists())

        assert [p["name"] for p in state.playlists] == ["Imported", "Late Night Jazz"]
        jazz = state.playlists[1]
        assert jazz["created_date"] == "2025-12-27"
        assert jazz["top_artists"] == "Bill Evans, Miles Davis"
        assert jazz["track_count"] == "5"
        imported = state.playlists[0]
        assert imported["top_artists"] == ""  # no tracks: omitted, not invented
        assert imported["mood_query"] == ""
        assert state.sort_button_label == "Sort playlists by Date Created, oldest first"

    def test_select_playlist_sets_dialog_identity(self, history_db: Any) -> None:
        state = _history_harness()()
        state.reorder_announcement = "Moved X to position 1 of 2"
        assert state.select_playlist(history_db.jazz) is None

        assert state.is_detail_modal_open is True
        assert state.selected_playlist["name"] == "Late Night Jazz"
        assert state.selected_playlist["mood_query"] == "smoky late night jazz"
        assert state.selected_playlist["track_count"] == "5"
        assert state.selected_playlist["created_date"] == "2025-12-27"
        # 3 x 1:01 + 2 x 1:00:00 = 2:03:03
        assert state.selected_playlist["total_duration_formatted"] == "2:03:03"
        assert [t["position"] for t in state.selected_playlist_tracks] == ["1", "2", "3", "4", "5"]
        assert state.reorder_announcement == ""


class TestHistoryReorder:
    """Named reorder controls keep focus and announce moves (task 5.6)."""

    def _state(self, n: int = 5) -> Any:
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()
        state.selected_playlist_tracks = _history_tracks(n)
        state.reorder_announcement = ""
        return state

    def test_move_up_announces_and_keeps_focus_on_move_up(self, call_script_marker: None) -> None:
        state = self._state(5)
        result = state.move_track_up("4")  # "Song 5", 0-based index 4

        assert [t["title"] for t in state.selected_playlist_tracks] == [
            "Song 1",
            "Song 2",
            "Song 3",
            "Song 5",
            "Song 4",
        ]
        assert [t["position"] for t in state.selected_playlist_tracks] == ["1", "2", "3", "4", "5"]
        assert state.reorder_announcement == "Moved Song 5 to position 4 of 5"
        assert result == (
            "call_script",
            "window.pmFocus&&pmFocus.when('#hist-move-up-3',{force:true})",
        )

    def test_move_up_to_top_targets_move_down(self, call_script_marker: None) -> None:
        state = self._state(3)
        result = state.move_track_up("1")
        assert state.selected_playlist_tracks[0]["title"] == "Song 2"
        assert state.reorder_announcement == "Moved Song 2 to position 1 of 3"
        # Move up is disabled at the top, so focus goes to the opposite control.
        assert result is not None and "'#hist-move-down-0'" in result[1]

    def test_move_down_announces_and_keeps_focus_on_move_down(
        self, call_script_marker: None
    ) -> None:
        state = self._state(5)
        result = state.move_track_down("1")
        assert state.selected_playlist_tracks[2]["title"] == "Song 2"
        assert state.reorder_announcement == "Moved Song 2 to position 3 of 5"
        assert result is not None and "'#hist-move-down-2'" in result[1]

    def test_move_down_to_bottom_targets_move_up(self, call_script_marker: None) -> None:
        state = self._state(4)
        result = state.move_track_down("2")
        assert state.selected_playlist_tracks[3]["title"] == "Song 3"
        assert state.reorder_announcement == "Moved Song 3 to position 4 of 4"
        assert result is not None and "'#hist-move-up-3'" in result[1]

    def test_two_tracks_flip_both_ways(self, call_script_marker: None) -> None:
        state = self._state(2)
        down = state.move_track_down("0")
        assert down is not None and "'#hist-move-up-1'" in down[1]
        up = state.move_track_up("1")
        assert up is not None and "'#hist-move-down-0'" in up[1]
        assert [t["title"] for t in state.selected_playlist_tracks] == ["Song 1", "Song 2"]

    @pytest.mark.parametrize(
        "method,index",
        [
            ("move_track_up", "0"),
            ("move_track_down", "4"),
            ("move_track_up", "-1"),
            ("move_track_down", "-1"),
            ("move_track_up", "5"),
            ("move_track_down", "9"),
            ("move_track_up", "abc"),
            ("move_track_down", ""),
        ],
    )
    def test_out_of_range_moves_change_nothing(
        self, call_script_marker: None, method: str, index: str
    ) -> None:
        state = self._state(5)
        before = [dict(t) for t in state.selected_playlist_tracks]
        assert getattr(state, method)(index) is None
        assert state.selected_playlist_tracks == before
        assert state.reorder_announcement == ""

    def test_empty_playlist_moves_change_nothing(self, call_script_marker: None) -> None:
        state = self._state(0)
        assert state.move_track_up("0") is None
        assert state.move_track_down("0") is None
        assert state.reorder_announcement == ""

    def test_moves_do_not_mutate_previous_rows(self, call_script_marker: None) -> None:
        state = self._state(3)
        original = state.selected_playlist_tracks
        state.move_track_down("0")
        assert [t["position"] for t in original] == ["1", "2", "3"]
        assert original[0]["title"] == "Song 1"

    def test_closing_clears_announcement(self) -> None:
        state = self._state(3)
        state.reorder_announcement = "Moved Song 2 to position 1 of 3"
        state.set_detail_modal_open(False)
        assert state.reorder_announcement == ""
        state.reorder_announcement = "Moved Song 2 to position 1 of 3"
        state.close_detail_modal()
        assert state.reorder_announcement == ""

    def test_selected_track_count(self) -> None:
        state = self._state(4)
        assert state.selected_track_count == 4
        state.selected_playlist_tracks = []
        assert state.selected_track_count == 0


class TestHistoryDeleteConfirm:
    """The delete alert dialog closes by state and its setter is idempotent (5.7)."""

    def test_set_delete_confirmation_open_false_is_idempotent(self) -> None:
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()
        state.show_delete_confirmation("5")
        state.set_delete_confirmation_open(False)
        assert state.is_delete_confirmation_open is False
        assert state.playlist_to_delete == ""
        state.set_delete_confirmation_open(False)  # e.g. Radix after a state close
        assert state.is_delete_confirmation_open is False
        assert state.playlist_to_delete == ""

    def test_opening_does_not_clear_pending(self) -> None:
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()
        state.show_delete_confirmation("5")
        state.set_delete_confirmation_open(True)
        assert state.playlist_to_delete == "5"
        assert state.is_delete_confirmation_open is True

    def test_confirm_delete_removes_card_and_closes_both_dialogs(
        self, history_db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        from plexmix.database.sqlite_manager import SQLiteManager
        from plexmix.ui.states.history_state import HistoryState

        monkeypatch.setattr(HistoryState, "load_playlists", staticmethod(lambda: "reload"))
        state = _history_harness()()
        state.playlists = [
            {"id": history_db.jazz, "name": "Late Night Jazz"},
            {"id": history_db.imported, "name": "Imported"},
        ]
        state.select_playlist(history_db.jazz)
        state.reorder_announcement = "Moved X to position 1 of 5"
        state.show_delete_confirmation(history_db.jazz)

        async def run() -> list:
            return [event async for event in state.confirm_delete()]

        events = asyncio.run(run())

        assert state.is_delete_confirmation_open is False
        assert state.playlist_to_delete == ""
        # The card and the dialog go together, so focus restore falls back to the h1.
        assert [p["id"] for p in state.playlists] == [history_db.imported]
        assert state.is_detail_modal_open is False
        assert state.selected_playlist == {}
        assert state.selected_playlist_tracks == []
        assert state.reorder_announcement == ""
        assert events[-1] == "reload"
        with SQLiteManager(str(history_db.path)) as db:
            assert db.get_playlist_by_id(int(history_db.jazz)) is None
            assert db.get_playlist_by_id(int(history_db.imported)) is not None

    def test_confirm_delete_without_pending_id_does_nothing(self, history_db: Any) -> None:
        import asyncio

        from plexmix.database.sqlite_manager import SQLiteManager

        state = _history_harness()()
        state.playlists = [{"id": history_db.jazz, "name": "Late Night Jazz"}]
        state.playlist_to_delete = ""

        async def run() -> list:
            return [event async for event in state.confirm_delete()]

        assert asyncio.run(run()) == []
        assert [p["id"] for p in state.playlists] == [history_db.jazz]
        with SQLiteManager(str(history_db.path)) as db:
            assert db.get_playlist_by_id(int(history_db.jazz)) is not None


class TestHistorySortLabel:
    """``sort_button_label`` names the sort button with its key and order (5.8)."""

    def test_date_both_directions(self) -> None:
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()
        state.sort_by = "created_date"
        state.sort_descending = True
        assert state.sort_button_label == "Sort playlists by Date Created, newest first"
        state.toggle_sort_order()
        assert state.sort_button_label == "Sort playlists by Date Created, oldest first"

    def test_label_contains_visible_text(self) -> None:
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()
        for key in ("created_date", "name", "track_count"):
            state.sort_by = key
            assert state.sort_by_label in state.sort_button_label

    def test_other_keys_and_unknown_fallback(self) -> None:
        from plexmix.ui.states.history_state import HistoryState

        state = HistoryState()
        state.sort_by = "name"
        state.sort_descending = False
        assert state.sort_button_label == "Sort playlists by Name, A to Z"
        state.sort_by = "track_count"
        state.sort_descending = True
        assert state.sort_button_label == "Sort playlists by Track Count, most tracks first"
        state.sort_by = "bogus"
        assert state.sort_button_label == "Sort playlists by Date Created, newest first"
