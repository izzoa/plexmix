"""Unit tests for UI state management - Fixed version."""
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
        """Test form validity checking."""
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()

        # Initially invalid (missing required fields)
        assert state.is_form_valid() is False

        # Set required fields
        state.plex_url = "http://localhost:32400"
        state.plex_token = "test_token"

        # Should be valid now
        assert state.is_form_valid() is True

        # Add validation error
        state.plex_url_error = "Invalid URL"

        # Should be invalid
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
        track = {
            "id": "1",
            "tags": "chill,relaxing",
            "environments": "study",
            "instruments": "piano",
        }
        state.start_edit_tag(track)

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
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.set_embedding_provider("openai")
        assert state.embedding_dimension == 1536

    def test_set_ai_provider_loads_key_for_new_provider(self):
        from unittest.mock import patch
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.ai_api_key = "old_gemini_key"
        # When no key is available for the new provider, field should be empty
        with patch(
            "plexmix.ui.states.settings_state.SettingsState._load_ai_api_key_for_provider"
        ) as mock_load:
            mock_load.side_effect = lambda p: setattr(state, "ai_api_key", "")
            state.set_ai_provider("openai")
            mock_load.assert_called_once_with("openai")
        assert state.ai_api_key == ""

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
        assert state.orphaned_embeddings_label == "5 Orphaned Embeddings"

    def test_missing_embeddings_label(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.doctor_tracks_needing_embeddings = 10
        assert state.missing_embeddings_label == "10 Tracks Need Embeddings"

    def test_missing_audio_label(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.doctor_tracks_without_audio = 7
        assert state.missing_audio_label == "7 Tracks Need Audio Analysis"

    def test_fix_progress_label_zero(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.fix_total = 0
        state.fix_progress = 0
        assert state.fix_progress_label == "0 / 0"

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
        state.doctor_untagged_tracks = 15
        msg = state.untagged_tracks_message
        assert "15 tracks" in msg
        assert "don't have AI-generated tags" in msg

    def test_untagged_tracks_message_all_tagged(self):
        from plexmix.ui.states.doctor_state import DoctorState

        state = DoctorState()
        state.doctor_untagged_tracks = 0
        msg = state.untagged_tracks_message
        assert "All tracks" in msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
