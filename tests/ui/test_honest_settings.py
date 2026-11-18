"""Settings changes model (webui-honest-status-and-first-run, tasks 10.1-10.3).

One Save/Discard bar fed by a per-field snapshot taken at the end of ``on_load``;
edits survive leaving and returning; Discard restores everything (staged secrets
included); a pending embedding change that doesn't fit the search index warns and
needs confirmation on Save; the AI model select always shows the saved model.

Reflex is mocked (``tests/ui/conftest.py``). Nothing touches the real keyring or
``~/.plexmix``: ``isolate`` points the data dir at ``tmp_path`` (with its own
config.yaml) and the keyring at an in-memory fake. Secrets are fake sentinels.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, Iterable, List, Optional
from unittest.mock import MagicMock

import pytest
import yaml

from plexmix.services.readiness import readiness_store
from plexmix.ui.secret_drafts import secret_drafts
from tests.ui._secrets_support import (
    FakeKeyring,
    isolate,
    make_state,
    patch_toast,
    read_config,
    staged_sentinel,
    write_config,
)

LOCAL_MXBAI = {
    "plex": {"url": "http://localhost:32400", "library_name": "Music"},
    "embedding": {
        "default_provider": "local",
        "model": "mixedbread-ai/mxbai-embed-large-v1",
        "dimension": 1024,
    },
}


@pytest.fixture(autouse=True)
def _clean_stores() -> Any:
    readiness_store.clear()
    secret_drafts.discard("tok-A")
    yield
    readiness_store.clear()
    secret_drafts.discard("tok-A")


def _set_value_refs(events: Optional[Iterable[Any]]) -> set:
    return {e[1] for e in (events or []) if isinstance(e, tuple) and e[0] == "set_value"}


def _set_values(events: Optional[Iterable[Any]]) -> dict:
    return {e[1]: e[2] for e in (events or []) if isinstance(e, tuple) and e[0] == "set_value"}


def _write_index(data: Path, dimension: int, tracks: int) -> None:
    """The FAISS ``.metadata`` pickle ``readiness.read_index_metadata`` reads."""
    with open(data / "embeddings.metadata", "wb") as f:
        pickle.dump({"dimension": dimension, "track_ids": list(range(1, tracks + 1))}, f)


def _loaded_state(monkeypatch: Any, tmp_path: Path, config: Optional[dict] = None) -> Any:
    """A Settings state after a real ``on_load`` (auth and shared app data stubbed)."""
    if config is not None:
        write_config(tmp_path, config)
    isolate(monkeypatch, tmp_path)
    patch_toast(monkeypatch)
    state = make_state()
    monkeypatch.setattr(type(state), "check_auth", lambda self: True)
    monkeypatch.setattr(type(state), "load_app_data", lambda self: None)
    state.on_load()
    return state


# ── 10.1 snapshot timing, changed sections, leave and return, Discard ──


class TestSnapshotTiming:
    def test_fresh_on_load_with_empty_saved_model_is_clean(self, monkeypatch, tmp_path):
        """update_model_lists fills the empty model before the snapshot is taken."""
        state = _loaded_state(monkeypatch, tmp_path, {"ai": {"default_provider": "openai"}})
        assert read_config(tmp_path)["ai"].get("model") is None  # saved model is empty
        assert state.ai_model == "gpt-5.4-mini"  # filled with the provider default
        assert state.has_unsaved_changes is False
        assert state.changed_sections == []
        assert state.unsaved_from_earlier is False

    def test_snapshot_before_the_fill_was_the_false_unsaved_bug(self, monkeypatch, tmp_path):
        """The old order (snapshot in load_settings, then the fill) reads as dirty."""
        write_config(tmp_path, {"ai": {"default_provider": "openai"}})
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.load_settings()
        state.update_model_lists()
        assert state.has_unsaved_changes is True  # what on_load's final snapshot fixes
        assert state.changed_sections == ["AI Provider"]

    def test_field_section_map_matches_the_snapshot(self):
        from plexmix.ui.states.settings_state import (
            _FIELD_SECTION,
            SECRET_FIELDS,
            SECTION_LABELS,
            SettingsState,
        )
        from plexmix.ui.utils.a11y import SETTINGS_SECTIONS

        state = SettingsState()
        assert set(state._get_settings_snapshot()) == set(_FIELD_SECTION)
        assert set(_FIELD_SECTION.values()) <= set(SETTINGS_SECTIONS)
        assert tuple(SECTION_LABELS) == SETTINGS_SECTIONS
        for field, spec in SECRET_FIELDS.items():
            assert _FIELD_SECTION[f"secret:{field}"] == spec.scope

    def test_snapshot_holds_no_secret_value(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path)
        value = staged_sentinel("plex_token")
        state.stage_secret("plex_token", value)
        snapshot = state._get_settings_snapshot()
        assert snapshot["secret:plex_token"] == "replace"
        assert value not in repr(snapshot) and value not in repr(state._settings_snapshot)


class TestChangedSections:
    def test_embedding_provider_and_audio_duration(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path)
        state.set_embedding_provider("openai")
        state.set_audio_duration_limit("90")
        assert state.has_unsaved_changes is True
        assert state.changed_sections == ["Embeddings", "Audio Analysis"]
        assert state.changed_sections_label == "Embeddings and Audio Analysis"

    def test_sections_in_tab_order(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path)
        state.set_log_level("DEBUG")
        state.set_musicbrainz_enabled(True)
        state.set_plex_url("http://plex.example:32400")
        assert state.changed_sections == ["Plex Server", "MusicBrainz", "Advanced"]
        assert state.changed_sections_label == "Plex Server, MusicBrainz and Advanced"

    def test_staged_secret_marks_its_section(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path)
        state.stage_secret("plex_token", staged_sentinel("plex_token"))
        assert state.changed_sections == ["Plex Server"]
        state.clear_secret("ai_custom_api_key")  # nothing stored, but a staged clear
        assert state.changed_sections == ["Plex Server", "AI Provider"]
        state.discard_secret("plex_token")
        state.discard_secret("ai_custom_api_key")
        assert state.has_unsaved_changes is False

    def test_editing_back_to_the_saved_value_is_clean(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path)
        saved = state.audio_duration_limit
        state.set_audio_duration_limit("15")
        assert state.has_unsaved_changes is True
        state.set_audio_duration_limit(str(saved))
        assert state.has_unsaved_changes is False

    def test_no_snapshot_means_no_changes(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        state.audio_duration_limit = 5
        assert state.has_unsaved_changes is False
        assert state.changed_sections == []


class TestLeaveAndReturn:
    def test_edits_survive_and_the_bar_says_from_earlier(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path)
        state.set_plex_url("http://edited.example:32400")
        state.set_audio_duration_limit("30")
        # Another page; the file changes meanwhile (e.g. the CLI saved something).
        write_config(tmp_path, {"plex": {"url": "http://cli.example:32400"}})
        events = state.on_load()
        assert state.plex_url == "http://edited.example:32400"
        assert state.audio_duration_limit == 30
        assert state.has_unsaved_changes is True
        assert state.unsaved_from_earlier is True
        assert state.changed_sections == ["Plex Server", "Audio Analysis"]
        assert _set_value_refs(events) == set()  # nothing on screen is overwritten

    def test_staged_secret_is_kept_and_a_reaped_one_is_emptied(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path)
        state.stage_secret("plex_token", staged_sentinel("plex_token"))
        state.stage_secret("ai_api_key", staged_sentinel("ai_api_key"))
        # The session was swept while disconnected: one draft is gone.
        secret_drafts.discard("tok-A", "ai_api_key")
        events = state.on_load()
        assert state.unsaved_from_earlier is True
        assert secret_drafts.action("tok-A", "plex_token") == "replace"
        assert state.secret_staged["plex_token"] == "replace"
        assert state.secret_staged["ai_api_key"] == ""
        assert _set_value_refs(events) == {"secret-ai_api_key"}

    def test_clean_return_reloads_the_file(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path)
        write_config(tmp_path, {"plex": {"url": "http://cli.example:32400"}})
        state.on_load()
        assert state.plex_url == "http://cli.example:32400"
        assert state.has_unsaved_changes is False
        assert state.unsaved_from_earlier is False

    def test_on_load_clears_an_old_save_result(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path)
        state.set_log_level("DEBUG")
        state.save_all_settings()
        assert state.save_status_message.startswith("✓")
        state.on_load()
        assert state.save_status == "" and state.save_status_message == ""


class TestDiscard:
    def test_discard_restores_every_field_and_hides_the_bar(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path, LOCAL_MXBAI)
        state.set_plex_url("http://edited.example:32400")
        state.set_embedding_provider("gemini")
        state.set_audio_duration_limit("15")
        state.set_ai_model_text("my-own-model")
        assert state.has_unsaved_changes is True
        events = state.discard_changes()
        assert state.plex_url == "http://localhost:32400"
        assert state.embedding_provider == "local"
        assert state.embedding_model == "mixedbread-ai/mxbai-embed-large-v1"
        assert state.embedding_dimension == 1024
        assert state.audio_duration_limit == 60
        assert state.ai_model == "gemini-3.5-flash"
        assert state.has_unsaved_changes is False
        assert state.changed_sections == []
        assert state.unsaved_from_earlier is False
        # The client-owned model field shows the saved model again ("" when listed).
        assert _set_values(events)["set-ai-model-custom"] == ""

    def test_discard_drops_every_staged_secret_through_discard_secret(self, monkeypatch, tmp_path):
        from plexmix.ui.states.settings_state import SECRET_FIELDS

        state = _loaded_state(monkeypatch, tmp_path)
        state.stage_secret("plex_token", staged_sentinel("plex_token"))
        state.stage_secret("ai_api_key", staged_sentinel("ai_api_key"))
        state.clear_secret("embedding_custom_api_key")
        calls: List[str] = []
        original = type(state).discard_secret

        def _spy(self: Any, field: str) -> Any:
            calls.append(field)
            return original(self, field)

        monkeypatch.setattr(type(state), "discard_secret", _spy)
        events = state.discard_changes()
        assert sorted(calls) == ["ai_api_key", "embedding_custom_api_key", "plex_token"]
        assert secret_drafts.staged_fields("tok-A") == {}
        assert all(v == "" for v in state.secret_staged.values())
        # Every secret input is emptied, each exactly once.
        refs = [e[1] for e in events if isinstance(e, tuple) and e[0] == "set_value"]
        secret_refs = [r for r in refs if r.startswith("secret-")]
        assert sorted(secret_refs) == sorted(f"secret-{f}" for f in SECRET_FIELDS)
        assert all(_set_values(events)[r] == "" for r in secret_refs)
        assert state.has_unsaved_changes is False

    def test_discard_after_return_reloads_the_file(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path)
        state.set_plex_url("http://edited.example:32400")
        write_config(tmp_path, {"plex": {"url": "http://cli.example:32400"}})
        state.on_load()
        assert state.unsaved_from_earlier is True
        state.discard_changes()
        assert state.plex_url == "http://cli.example:32400"
        assert state.unsaved_from_earlier is False
        assert state.has_unsaved_changes is False

    def test_discard_clears_format_warnings_and_returns_focus(self, monkeypatch, tmp_path):
        import reflex as rx

        state = _loaded_state(monkeypatch, tmp_path)
        state.validate_plex_url("not a url")
        assert state.plex_url_error
        script = MagicMock(return_value=("call_script",))
        monkeypatch.setattr(rx, "call_script", script)
        events = state.discard_changes()
        assert state.plex_url_error == ""
        assert ("call_script",) in events
        assert "pmFocus.when" in script.call_args[0][0]
        assert "settings-panel" in script.call_args[0][0]

    def test_unauthenticated_discard_does_nothing(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state(allowed=False)
        state.plex_url = "http://x"
        assert state.discard_changes() is None
        assert state.plex_url == "http://x"


class TestSaveResult:
    def test_saved_result_shows_until_the_next_edit(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path)
        state.set_log_level("DEBUG")
        events = state.save_all_settings()
        assert events[0][:2] == ("toast", "success")
        assert read_config(tmp_path)["logging"]["level"] == "DEBUG"
        assert state.has_unsaved_changes is False
        assert state.save_status == "✓ Settings saved."
        assert state.save_status_message == "✓ Settings saved."
        state.set_log_level("INFO")
        assert state.save_status_message == ""  # stale once there is something new to save

    def test_failed_save_keeps_its_message_and_the_bar(self, monkeypatch, tmp_path):
        from plexmix.config.settings import Settings

        state = _loaded_state(monkeypatch, tmp_path)
        state.set_log_level("DEBUG")

        def _boom(self: Any, *a: Any, **k: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Settings, "save_to_file", _boom)
        event = state.save_all_settings()
        assert event[:2] == ("toast", "error")
        assert state.has_unsaved_changes is True
        assert state.save_status_message.startswith("✗ Failed to save settings")
        assert "disk full" in state.save_status_message


# ── 10.2 the embedding change against the search index ──────────────


class TestEmbeddingChangeWarning:
    def test_local_mxbai_to_gemini_names_both_sizes_and_the_tracks(self, monkeypatch, tmp_path):
        _write_index(tmp_path, 1024, 5943)
        state = _loaded_state(monkeypatch, tmp_path, LOCAL_MXBAI)
        assert (state.index_dimension, state.index_track_count) == (1024, 5943)
        assert state.embedding_change_warning == ""  # the saved model fits the index
        state.set_embedding_provider_from_display("Gemini")
        warning = state.embedding_change_warning
        assert "1,024" in warning and "3,072" in warning
        assert "5,943 tracks" in warning and "Gemini" in warning
        assert "won't work" in warning
        assert "rebuild embeddings or switch back" in warning
        for banned in ("plexmix", "Run:", "pip install", "⚠"):
            assert banned not in warning

    def test_matching_dimension_shows_no_warning(self, monkeypatch, tmp_path):
        _write_index(tmp_path, 1024, 10)
        state = _loaded_state(monkeypatch, tmp_path, LOCAL_MXBAI)
        state.set_embedding_provider("cohere")  # embed-v4.0: 1024 dimensions
        assert state.embedding_dimension == 1024
        assert state.embedding_change_warning == ""

    def test_no_index_shows_no_warning(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path, LOCAL_MXBAI)
        assert state.index_dimension == 0
        state.set_embedding_provider("gemini")
        assert state.embedding_change_warning == ""

    def test_custom_dimension_change_is_checked_too(self, monkeypatch, tmp_path):
        _write_index(tmp_path, 1024, 3)
        state = _loaded_state(monkeypatch, tmp_path, LOCAL_MXBAI)
        state.set_embedding_provider("custom")
        state.set_embedding_custom_dimension("1024")
        assert state.embedding_change_warning == ""
        state.set_embedding_custom_dimension("768")
        assert "custom endpoint" in state.embedding_change_warning
        assert "768" in state.embedding_change_warning

    def test_pure_warning_helper(self):
        from plexmix.ui.states.settings_state import embedding_mismatch_warning

        assert embedding_mismatch_warning(0, 10, 3072, "gemini") == ""
        assert embedding_mismatch_warning(1024, 10, 1024, "local") == ""
        assert embedding_mismatch_warning(1024, 10, 0, "gemini") == ""
        text = embedding_mismatch_warning(384, 1, 1536, "openai")
        assert text.startswith("Your 384-dimension search index of 1 track won't work")
        assert "1,536-dimension OpenAI embeddings" in text
        assert "of " not in embedding_mismatch_warning(384, 0, 1536, "openai").split("won't")[0]


class TestEmbeddingChangeConfirm:
    def _switched(self, monkeypatch: Any, tmp_path: Path) -> Any:
        _write_index(tmp_path, 1024, 5943)
        state = _loaded_state(monkeypatch, tmp_path, LOCAL_MXBAI)
        state.set_embedding_provider_from_display("Gemini")
        return state

    def test_save_opens_the_confirmation_without_writing(self, monkeypatch, tmp_path):
        from plexmix.config.settings import Settings

        state = self._switched(monkeypatch, tmp_path)
        before = (tmp_path / "config.yaml").read_text()
        save = MagicMock()
        monkeypatch.setattr(Settings, "save_to_file", save)
        assert state.save_all_settings() is None
        assert state.embedding_confirm_open is True
        save.assert_not_called()
        assert (tmp_path / "config.yaml").read_text() == before
        assert state.has_unsaved_changes is True

    def test_keep_current_provider_reverts_and_saves_nothing(self, monkeypatch, tmp_path):
        from plexmix.config.settings import Settings

        state = self._switched(monkeypatch, tmp_path)
        state.set_audio_duration_limit("30")  # another pending edit stays pending
        state.save_all_settings()
        save = MagicMock()
        monkeypatch.setattr(Settings, "save_to_file", save)
        state.revert_embedding_change()
        assert state.embedding_confirm_open is False
        assert state.embedding_provider == "local"
        assert state.embedding_model == "mixedbread-ai/mxbai-embed-large-v1"
        assert state.embedding_dimension == 1024
        assert state.embedding_provider_display == "Local (Offline)"
        assert state.embedding_change_warning == ""
        save.assert_not_called()
        assert state.changed_sections == ["Audio Analysis"]

    def test_revert_drops_a_key_typed_for_the_other_provider(self, monkeypatch, tmp_path):
        state = self._switched(monkeypatch, tmp_path)
        state.stage_secret("embedding_api_key", staged_sentinel("embedding_api_key"))
        assert state.secret_staged["embedding_api_key"] == "replace"
        state.save_all_settings()
        events = state.revert_embedding_change()
        assert secret_drafts.action("tok-A", "embedding_api_key") == ""
        assert state.secret_staged["embedding_api_key"] == ""
        assert _set_value_refs(events) == {
            "secret-embedding_api_key",
            "secret-embedding_custom_api_key",
        }
        assert state.has_unsaved_changes is False

    def test_revert_of_a_model_only_change_keeps_the_providers_key(self, monkeypatch, tmp_path):
        _write_index(tmp_path, 1536, 3)
        config = {"embedding": {"default_provider": "openai", "model": "text-embedding-3-small"}}
        state = _loaded_state(monkeypatch, tmp_path, config)
        state.stage_secret("embedding_api_key", staged_sentinel("embedding_api_key"))
        state.set_embedding_model("text-embedding-3-large")  # 3072
        assert state.embedding_change_warning
        state.save_all_settings()
        assert state.embedding_confirm_open is True
        assert state.revert_embedding_change() is None
        assert state.embedding_model == "text-embedding-3-small"
        assert secret_drafts.action("tok-A", "embedding_api_key") == "replace"

    def test_escape_or_cancel_closes_and_keeps_the_edit(self, monkeypatch, tmp_path):
        state = self._switched(monkeypatch, tmp_path)
        state.save_all_settings()
        state.set_embedding_confirm_open(False)
        state.set_embedding_confirm_open(False)  # idempotent
        assert state.embedding_confirm_open is False
        assert state.embedding_provider == "gemini"
        state.set_embedding_confirm_open(True)  # only Save opens it
        assert state.embedding_confirm_open is False

    def test_confirm_saves_and_readiness_asks_for_a_rebuild(self, monkeypatch, tmp_path):
        state = self._switched(monkeypatch, tmp_path)
        _make_library(tmp_path, tracks=3, embedded=3)
        state.save_all_settings()
        events = state.confirm_embedding_change()
        assert events[0][:2] == ("toast", "success")
        assert state.embedding_confirm_open is False
        saved = read_config(tmp_path)["embedding"]
        assert saved["default_provider"] == "gemini" and saved["dimension"] == 3072
        assert state.has_unsaved_changes is False
        # Dashboard / Generator read AppState.readiness: the index needs a rebuild.
        index = state.readiness["index"]
        assert index["cause"] == "dimension_mismatch"
        assert index["blocking"] == "true"
        assert "1,024" in index["body"] and "3,072" in index["body"]
        assert state.embedding_dimension_warning
        # The warning stays until the embeddings are rebuilt or the provider goes back.
        assert state.embedding_change_warning

    def test_saving_other_changes_with_an_already_saved_mismatch(self, monkeypatch, tmp_path):
        _write_index(tmp_path, 1024, 3)
        config = {"embedding": {"default_provider": "gemini", "model": "gemini-embedding-001"}}
        state = _loaded_state(monkeypatch, tmp_path, config)
        assert state.embedding_change_warning  # saved earlier, still mismatched
        state.set_log_level("DEBUG")
        events = state.save_all_settings()
        assert state.embedding_confirm_open is False
        assert events[0][:2] == ("toast", "success")

    def test_unauthenticated_confirm_and_revert(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        toasts = patch_toast(monkeypatch)
        before = (tmp_path / "config.yaml").read_text()
        state = make_state(allowed=False)
        state.embedding_provider = "gemini"
        event = state.confirm_embedding_change()
        assert event[:2] == ("toast", "error") and toasts.calls
        assert state.revert_embedding_change() is None
        assert state.embedding_provider == "gemini"
        assert (tmp_path / "config.yaml").read_text() == before


def _make_library(data: Path, *, tracks: int, embedded: int) -> None:
    """A real SQLite library with ``embedded`` tracks embedded by the old provider."""
    from plexmix.database.models import Album, Artist, Embedding, SyncHistory, Track
    from plexmix.database.sqlite_manager import SQLiteManager

    with SQLiteManager(str(data / "plexmix.db")) as db:
        artist = db.insert_artist(Artist(plex_key="a1", name="Artist"))
        album = db.insert_album(Album(plex_key="al1", title="Album", artist_id=artist))
        ids = [
            db.insert_track(
                Track(plex_key=f"t{i}", title=f"Song {i}", artist_id=artist, album_id=album)
            )
            for i in range(tracks)
        ]
        for tid in ids[:embedded]:
            db.insert_embedding(
                Embedding(track_id=tid, embedding_model="local", embedding_dim=2, vector=[0.1, 0.2])
            )
        db.insert_sync_record(
            SyncHistory(tracks_added=tracks, tracks_updated=0, tracks_removed=0, status="success")
        )


# ── 10.3 model choices ───────────────────────────────────────────────


class TestModelChoices:
    OPENAI_OLD = {"ai": {"default_provider": "openai", "model": "gpt-5-mini"}}

    def test_saved_unlisted_model_is_a_choice_and_marked(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path, self.OPENAI_OLD)
        assert "gpt-5-mini" not in state.ai_models
        assert state.ai_model == "gpt-5-mini"
        assert "gpt-5-mini" in state.ai_model_choices
        assert state.ai_model_choices[: len(state.ai_models)] == state.ai_models
        assert state.ai_model_unlisted is True
        assert state.ai_model_text == "gpt-5-mini"
        assert state.has_unsaved_changes is False

    def test_choosing_a_listed_model_drops_the_marker(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path, self.OPENAI_OLD)
        events = state.set_ai_model("gpt-5.5")
        assert state.ai_model == "gpt-5.5"
        assert state.ai_model_unlisted is False
        assert "gpt-5-mini" not in state.ai_model_choices
        assert state.ai_model_choices == state.ai_models
        # The client-owned field is emptied (a server write: webui-input-responsiveness D3).
        assert _set_values(events) == {"set-ai-model-custom": ""}
        # Picking another listed model changes nothing on that field.
        assert state.set_ai_model("gpt-5.4-nano") == []

    def test_typed_model_commits_without_an_echo(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path, {"ai": {"default_provider": "openai"}})
        assert state.set_ai_model_text("  my-model  ") is None
        assert state.ai_model == "my-model"
        assert state.ai_model_unlisted is True
        assert state.ai_model_choices[-1] == "my-model"
        assert state.changed_sections == ["AI Provider"]
        state.set_ai_model_text("my-model")  # the blur after a debounced commit
        assert state.ai_model == "my-model"
        state.set_ai_model_text("")  # an emptied field keeps the current model
        assert state.ai_model == "my-model"

    def test_provider_switch_resets_the_field_only_when_its_text_changes(
        self, monkeypatch, tmp_path
    ):
        state = _loaded_state(monkeypatch, tmp_path, self.OPENAI_OLD)
        events = state.set_ai_provider_from_display("Google")
        assert _set_values(events)["set-ai-model-custom"] == ""
        assert state.ai_model_unlisted is False
        events = state.set_ai_provider_from_display("Anthropic")
        assert "set-ai-model-custom" not in _set_value_refs(events)

    def test_local_endpoint_model_shows_in_the_local_select(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path, {"ai": {"default_provider": "local"}})
        assert state.ai_model_unlisted is False
        events = state.select_discovered_model("llama3:8b")
        assert state.ai_model == "llama3:8b"
        assert state.ai_model_unlisted is True
        assert "llama3:8b" in state.ai_model_choices
        assert _set_values(events) == {"set-ai-model-custom": "llama3:8b"}

    def test_custom_provider_has_no_list_and_no_marker(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path, self.OPENAI_OLD)
        state.set_ai_provider("custom")
        assert state.ai_models == []
        assert state.ai_model_unlisted is False
        assert state.ai_model_choices == []

    def test_on_load_writes_the_field_only_when_the_saved_model_changed_it(
        self, monkeypatch, tmp_path
    ):
        write_config(tmp_path, self.OPENAI_OLD)
        isolate(monkeypatch, tmp_path)
        patch_toast(monkeypatch)
        state = make_state()
        monkeypatch.setattr(type(state), "check_auth", lambda self: True)
        monkeypatch.setattr(type(state), "load_app_data", lambda self: None)
        events = state.on_load()
        assert _set_values(events)["set-ai-model-custom"] == "gpt-5-mini"
        events = state.on_load()  # same saved model: nothing to write
        assert "set-ai-model-custom" not in _set_value_refs(events)


class TestHelpers:
    def test_join_names(self):
        from plexmix.ui.states.settings_state import join_names

        assert join_names([]) == ""
        assert join_names(["Embeddings"]) == "Embeddings"
        assert join_names(["A", "B"]) == "A and B"
        assert join_names(["A", "B", "C"]) == "A, B and C"

    def test_saved_config_keeps_the_unlisted_model(self, monkeypatch, tmp_path):
        state = _loaded_state(monkeypatch, tmp_path, TestModelChoices.OPENAI_OLD)
        state.set_log_level("DEBUG")
        state.save_all_settings()
        assert yaml.safe_load((tmp_path / "config.yaml").read_text())["ai"]["model"] == (
            "gpt-5-mini"
        )

    def test_secrets_never_reach_state_or_events(self, monkeypatch, tmp_path):
        from tests.ui._secrets_support import assert_state_clean

        kr = FakeKeyring({"google_api_key": "SENTINEL-GOOGLE-KEYRING-0000"})
        write_config(tmp_path, LOCAL_MXBAI)
        isolate(monkeypatch, tmp_path, kr)
        patch_toast(monkeypatch)
        _write_index(tmp_path, 1024, 4)
        state = make_state()
        monkeypatch.setattr(type(state), "check_auth", lambda self: True)
        monkeypatch.setattr(type(state), "load_app_data", lambda self: None)
        state.on_load()
        typed = staged_sentinel("embedding_api_key")
        state.set_embedding_provider("gemini")
        state.stage_secret("embedding_api_key", typed)
        events: List[Any] = []
        events.append(state.save_all_settings())
        events.append(state.revert_embedding_change())
        events.append(state.discard_changes())
        sentinels = ["SENTINEL-GOOGLE-KEYRING-0000", typed]
        assert_state_clean(state, sentinels, "settings changes flow")
        for sentinel in sentinels:
            assert sentinel not in repr(events)
