"""webui-honest-status-and-first-run group 8: Tagging scope (webui-tagging-scope).

- 8.1 ``normalize_tag_filters``, the preview signature (``preview_is_current``),
  ``preview_tagged_count``, ``tagging_notice`` and the folded tag-all-untagged flow.
- 8.2 "Tag tracks" opens the scope confirmation; ``start_tagging(only_untagged)``
  refuses a start whose filters differ from the previewed ones.
- 8.3 (state side) the count card's state words, the disabled reason, and the
  "Recently tagged" loading flag.

Reflex is mocked (tests/ui/conftest.py). Nothing here touches the network, the
real keyring or ~/.plexmix: every test runs on a scratch ``PLEXMIX_DATA_DIR`` with
its own config.yaml, the keyring lookup is patched out, providers are fakes, and
the readiness and task stores are cleared around each test.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from plexmix.services.readiness import readiness_store
from plexmix.ui.job_manager import task_store

SENTINEL_OPENAI = "sk-SENTINEL-honest-tagging-0001"

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
OPENAI_AI_YAML = "ai:\n  default_provider: openai\n  model: gpt-5-mini\n"
LOCAL_AI_YAML = "ai:\n  default_provider: local\n  local_mode: endpoint\n"
LOCAL_AI_YAML += "  local_endpoint: http://127.0.0.1:9/v1\n  model: llama3\n"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Scratch data dir with a config.yaml, no keyring, no env config, clean stores."""
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text("{}\n")  # never fall back to ~/.plexmix/config.yaml
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(data))
    monkeypatch.setattr("plexmix.config.credentials._get_keyring", lambda: None)
    readiness_store.clear()
    task_store.clear("tagging")
    yield data
    readiness_store.clear()
    task_store.clear("tagging")


# ── Helpers ──────────────────────────────────────────────────────────────


def write_config(data: Path, body: str) -> None:
    (data / "config.yaml").write_text(body or "{}\n")


def make_library(data: Path, artists: dict[str, tuple[int, int]]) -> None:
    """A real SQLite library: ``{artist: (tracks, tagged)}``."""
    from plexmix.database.models import Album, Artist, Track
    from plexmix.database.sqlite_manager import SQLiteManager

    with SQLiteManager(str(data / "plexmix.db")) as db:
        for a_idx, (name, (count, tagged)) in enumerate(artists.items()):
            artist = db.insert_artist(Artist(plex_key=f"a{a_idx}", name=name))
            album = db.insert_album(
                Album(plex_key=f"al{a_idx}", title=f"{name} album", artist_id=artist)
            )
            for i in range(count):
                tid = db.insert_track(
                    Track(
                        plex_key=f"t{a_idx}-{i}",
                        title=f"{name} song {i}",
                        artist_id=artist,
                        album_id=album,
                    )
                )
                if i < tagged:
                    db.update_track_tags(tid, tags="chill,warm")


def tagging_state() -> Any:
    """A TaggingState usable from background handlers (``async with``, access allowed)."""
    from plexmix.ui.states.tagging_state import TaggingState

    class _Harness(TaggingState):  # type: ignore[misc, valid-type]
        async def __aenter__(self) -> "_Harness":
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        def access_allowed(self) -> bool:
            return True

    state = _Harness()
    # Instance copies of the class-level dict defaults (the mock has no per-instance copy).
    from plexmix.ui.states.tagging_state import EMPTY_NOTICE, EMPTY_SCOPE

    state.tagging_notice = dict(EMPTY_NOTICE)
    state.tag_scope = dict(EMPTY_SCOPE)
    return state


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def preview(state: Any, **filters: Any) -> Any:
    """Commit ``filters`` to state the way the filter form does and run the preview."""
    for name, value in filters.items():
        setattr(state, name, value)
    run(state.preview_selection())
    return state


def mocked_db(tracks: Optional[list[Any]] = None) -> tuple[MagicMock, Any, Any]:
    """Patch the lazily imported Settings/SQLiteManager (nothing touches disk)."""
    db = MagicMock()
    db.get_tracks_by_filter.return_value = tracks if tracks is not None else []
    db.count_stale_tagged_tracks.return_value = 0
    settings_patch = patch(
        "plexmix.config.settings.Settings.load_from_file", return_value=MagicMock()
    )
    db_patch = patch("plexmix.database.sqlite_manager.SQLiteManager", return_value=db)
    return db, settings_patch, db_patch


def set_preview(state: Any, count: int, tagged: int = 0, **filters: Any) -> None:
    """Make a preview of ``filters`` (also committed to state) current."""
    from plexmix.ui.states.tagging_state import normalize_tag_filters, tag_filter_signature

    state.genre_filter = filters.get("genre", "")
    state.artist_filter = filters.get("artist", "")
    state.stale_days = filters.get("stale_days", "")
    state.year_min = filters.get("year_min", "")
    state.year_max = filters.get("year_max", "")
    state.has_no_tags = filters.get("has_no_tags", False)
    state.preview_count = count
    state.preview_tagged_count = tagged
    state._preview_signature = tag_filter_signature(
        normalize_tag_filters(
            state.genre_filter,
            state.year_min,
            state.year_max,
            state.artist_filter,
            state.has_no_tags,
            state.stale_days,
        )
    )


_CLI_OR_EMOJI = re.compile(r"plexmix config|Run:|pip install|[☀-➿\U0001f000-\U0001faff]")


# ── 8.1 normalize_tag_filters ────────────────────────────────────────────


class TestNormalizeTagFilters:
    def test_blank_values_mean_no_filter(self) -> None:
        from plexmix.ui.states.tagging_state import normalize_tag_filters

        assert normalize_tag_filters() == {
            "genre": None,
            "year_min": None,
            "year_max": None,
            "artist": None,
            "has_no_tags": False,
            "stale_days": None,
        }
        assert (
            normalize_tag_filters(stale_days="  ", year_min=None, year_max="")["stale_days"] is None
        )

    def test_stale_days_abc_raises_a_plain_message(self) -> None:
        from plexmix.ui.states.tagging_state import normalize_tag_filters

        with pytest.raises(ValueError, match="Stale days must be a whole number"):
            normalize_tag_filters(stale_days="abc")

    @pytest.mark.parametrize("bad", ["abc", "-3", "1.5", "1e3", "²", "3 days", True, -1])
    def test_any_other_non_whole_number_is_rejected(self, bad: Any) -> None:
        from plexmix.ui.states.tagging_state import normalize_tag_filters

        with pytest.raises(ValueError, match="Stale days must be a whole number"):
            normalize_tag_filters(stale_days=bad)

    def test_years_are_validated_with_their_own_label(self) -> None:
        from plexmix.ui.states.tagging_state import normalize_tag_filters

        with pytest.raises(ValueError, match="From year must be a whole number"):
            normalize_tag_filters(year_min="19x0")
        with pytest.raises(ValueError, match="To year must be a whole number"):
            normalize_tag_filters(year_max="2020.5")

    def test_whole_numbers_and_text(self) -> None:
        from plexmix.ui.states.tagging_state import normalize_tag_filters

        out = normalize_tag_filters(
            genre=" jazz ",
            year_min="1990",
            year_max=2000,
            artist="Yundi",
            has_no_tags=1,
            stale_days=" 030 ",
        )
        assert out == {
            "genre": "jazz",
            "year_min": 1990,
            "year_max": 2000,
            "artist": "Yundi",
            "has_no_tags": True,
            "stale_days": 30,
        }
        # An explicit 0 days is a real filter, not "blank".
        assert normalize_tag_filters(stale_days="0")["stale_days"] == 0

    def test_signature_round_trips_to_query_kwargs(self) -> None:
        from plexmix.ui.states.tagging_state import normalize_tag_filters, tag_filter_signature

        filters = normalize_tag_filters(artist="Yundi", stale_days="7")
        sig = tag_filter_signature(filters)
        assert json.loads(sig) == filters
        assert sig == tag_filter_signature(dict(reversed(list(filters.items()))))

    def test_invalid_current_filters_never_match_a_preview(self) -> None:
        from plexmix.ui.states.tagging_state import current_filter_signature

        sig = current_filter_signature("", "abc", "", "", False, "")
        assert sig.startswith("!invalid:")
        assert sig != current_filter_signature("", "", "", "", False, "")


# ── 8.1 preview signature ────────────────────────────────────────────────


class TestPreviewSignature:
    def test_before_any_preview_it_invites_a_preview(self) -> None:
        state = tagging_state()
        assert state.preview_state == "none"
        assert state.preview_is_current is False
        assert state.can_start_tagging is False
        assert state.tag_block_reason == "Preview first to see which tracks will be tagged."

    def test_changing_artist_after_a_preview_makes_it_out_of_date(self, _isolated: Path) -> None:
        make_library(_isolated, {"Yundi": (59, 40), "Other": (41, 0)})
        state = preview(tagging_state())
        assert state.preview_is_current is True
        assert state.preview_state == "current"
        assert (state.preview_count, state.preview_tagged_count) == (100, 40)
        assert state.preview_count_label == "100"
        assert state.can_start_tagging is True
        assert state.tag_block_reason == ""

        state.set_artist_filter("Yundi")  # the debounced commit of the field
        assert state.preview_is_current is False
        assert state.preview_state == "stale"
        assert state.can_start_tagging is False
        assert state.tag_block_reason == "Filters changed. Preview again before tagging."

        run(state.preview_selection())
        assert state.preview_is_current is True
        assert (state.preview_count, state.preview_tagged_count) == (59, 40)
        assert state.preview_tagged_note == "40 already have tags"

    @pytest.mark.parametrize(
        "change",
        [
            ("genre_filter", "jazz"),
            ("has_no_tags", True),
            ("stale_days", "30"),
            ("year_min", "1990"),
            ("year_max", "2001"),
        ],
    )
    def test_any_filter_change_makes_it_out_of_date(self, change: tuple[str, Any]) -> None:
        state = tagging_state()
        set_preview(state, 12)
        assert state.preview_is_current is True
        setattr(state, *change)
        assert state.preview_is_current is False

    def test_blank_and_whitespace_filters_share_a_signature(self) -> None:
        state = tagging_state()
        set_preview(state, 12)
        state.artist_filter = "   "
        assert state.preview_is_current is True

    def test_empty_stale_days_applies_no_stale_filter_on_a_real_library(
        self, _isolated: Path
    ) -> None:
        # Freshly tagged tracks are not stale: a "0 days" filter would match none.
        make_library(_isolated, {"Yundi": (5, 3)})
        state = preview(tagging_state(), stale_days="")
        assert state.preview_count == 5
        assert state.preview_state == "current"
        assert state.tagging_notice["text"] == ""

    def test_empty_stale_days_passes_none_to_the_query(self) -> None:
        db, settings_patch, db_patch = mocked_db([{"tags": ""}, {"tags": "x"}])
        state = tagging_state()
        state.stale_days = "30"
        state.apply_filter_form({"artist": "", "genre": "", "stale_days": ""})
        with settings_patch, db_patch:
            run(state.preview_selection())
        assert db.get_tracks_by_filter.call_args.kwargs["stale_days"] is None
        assert (state.preview_count, state.preview_tagged_count) == (2, 1)

    def test_an_invalid_value_reaching_state_sets_a_notice_and_counts_nothing(self) -> None:
        # set_stale_days keeps digits only; this covers a value set any other way.
        db, settings_patch, db_patch = mocked_db()
        state = tagging_state()
        state.stale_days = "abc"
        with settings_patch, db_patch:
            run(state.preview_selection())
        db.get_tracks_by_filter.assert_not_called()
        assert state.tagging_notice["level"] == "error"
        assert state.tagging_notice["text"] == "Stale days must be a whole number."
        assert state.preview_state == "none"
        assert state.can_start_tagging is False

    def test_tagged_count_follows_the_sql_definition(self) -> None:
        from types import SimpleNamespace

        rows = [{"tags": "a"}, {"tags": ""}, {"tags": None}, {"tags": " "}, SimpleNamespace()]
        db, settings_patch, db_patch = mocked_db(rows)
        state = tagging_state()
        with settings_patch, db_patch:
            run(state.preview_selection())
        # NULL and '' are untagged (count_untagged_tracks); a blank-but-set value is tagged.
        assert (state.preview_count, state.preview_tagged_count) == (5, 2)

    def test_zero_matches_is_empty_not_a_count(self, _isolated: Path) -> None:
        make_library(_isolated, {"Yundi": (3, 0)})
        state = preview(tagging_state(), artist_filter="Nobody")
        assert state.preview_state == "empty"
        assert state.can_start_tagging is False
        assert state.tag_block_reason == "No tracks match these filters."

    def test_singular_wording(self) -> None:
        state = tagging_state()
        set_preview(state, 1, 1)
        assert state.preview_match_text == "track matches the current filters"
        assert state.preview_tagged_note == "it already has tags"
        state.preview_count, state.preview_tagged_count = 5943, 1
        assert state.preview_count_label == "5,943"
        assert state.preview_tagged_note == "1 already has tags"

    def test_a_superseded_preview_is_ignored(self) -> None:
        db, settings_patch, db_patch = mocked_db([{"tags": ""}] * 3)
        state = tagging_state()

        async def two_previews() -> None:
            first = asyncio.ensure_future(state.preview_selection())
            await asyncio.sleep(0)  # the first has captured its filters and is counting
            state.artist_filter = "Yundi"
            await state.preview_selection()
            await first

        with settings_patch, db_patch:
            run(two_previews())
        assert state.preview_is_current is True
        assert json.loads(state._preview_signature)["artist"] == "Yundi"
        assert state.preview_running is False


class TestPreviewProblems:
    def test_missing_database_says_not_synced_and_offers_sync(self, _isolated: Path) -> None:
        state = preview(tagging_state())
        notice = state.tagging_notice
        assert notice["level"] == "error"
        assert notice["title"] == "Your library hasn't been synced yet"
        assert notice["action_label"] == "Sync library"
        assert notice["action_href"] == "/library"
        assert not _CLI_OR_EMOJI.search(json.dumps(notice))
        assert not (_isolated / "plexmix.db").exists()  # the preview never creates it
        # No count is claimed: never "0 tracks match".
        assert state.preview_state == "none"
        assert state.can_start_tagging is False

    def test_empty_library_with_no_filters_is_not_synced(self, _isolated: Path) -> None:
        make_library(_isolated, {})
        state = preview(tagging_state())
        assert state.tagging_notice["action_label"] == "Sync library"
        assert state.preview_state == "none"

    def test_database_error_is_shown_plainly(self) -> None:
        db, settings_patch, db_patch = mocked_db()
        db.get_tracks_by_filter.side_effect = RuntimeError("database disk image is malformed")
        state = tagging_state()
        with settings_patch, db_patch:
            run(state.preview_selection())
        assert state.tagging_notice["title"] == "The library database couldn't be read"
        assert "malformed" in state.tagging_notice["text"]
        assert state.preview_running is False
        assert state.preview_state == "none"

    def test_preview_clears_its_own_notice_but_keeps_a_job_failure(self) -> None:
        from plexmix.ui.states.tagging_state import _notice

        db, settings_patch, db_patch = mocked_db([{"tags": ""}])
        state = tagging_state()
        state.tagging_notice = _notice("warning", "old", source="start")
        with settings_patch, db_patch:
            run(state.preview_selection())
        assert state.tagging_notice["text"] == ""

        state.tagging_notice = _notice("error", "Invalid API key.", source="job")
        with settings_patch, db_patch:
            run(state.preview_selection())
        assert state.tagging_notice["text"] == "Invalid API key."

    def test_dismiss(self) -> None:
        from plexmix.ui.states.tagging_state import EMPTY_NOTICE, _notice

        state = tagging_state()
        state.tagging_notice = _notice("error", "x", source="job")
        state.dismiss_notice()
        assert state.tagging_notice == EMPTY_NOTICE


# ── 8.2 confirmation and start guard ─────────────────────────────────────


class TestRequestTagging:
    def test_opens_the_scope_with_overwrite_and_cloud_disclosure(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_config(_isolated, OPENAI_AI_YAML)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        state = tagging_state()
        set_preview(state, 59, 40, artist="Yundi")
        with patch.object(task_store, "start") as start:
            run(state.request_tagging())
        start.assert_not_called()
        assert state.tag_confirm_open is True
        assert state.tag_confirm_checking is False
        scope = state.tag_scope
        assert scope["title"] == "Tag 59 tracks?"
        assert scope["count_line"] == "59 tracks will be tagged."
        assert scope["replace_line"] == "40 existing tag sets will be replaced."
        assert scope["secondary_label"] == "Tag only the 19 untagged tracks"
        assert scope["danger"] == "true"
        assert scope["provider_label"] == "OpenAI"
        assert scope["model"] == "gpt-5-mini"
        assert scope["privacy_line"] == (
            "Track titles, artists, albums and genres are sent to OpenAI."
        )
        assert SENTINEL_OPENAI not in json.dumps(scope)
        assert set(scope) >= {"title", "count_line", "duration_line", "confirm_label"}

    def test_untagged_set_has_no_secondary_and_is_not_danger(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_config(_isolated, OPENAI_AI_YAML)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        state = tagging_state()
        set_preview(state, 19, 0)
        run(state.request_tagging())
        assert state.tag_confirm_open is True
        assert state.tag_scope["replace_line"] == ""
        assert state.tag_scope["secondary_label"] == ""
        assert state.tag_scope["danger"] == "false"

    def test_local_provider_runs_on_this_machine(self, _isolated: Path) -> None:
        write_config(_isolated, LOCAL_AI_YAML)
        state = tagging_state()
        set_preview(state, 3, 0)
        run(state.request_tagging())
        assert state.tag_confirm_open is True
        assert state.tag_scope["provider_label"] == "Local"
        assert "Runs on this machine" in state.tag_scope["privacy_line"]

    def test_missing_ai_key_opens_nothing_and_explains(self, _isolated: Path) -> None:
        write_config(_isolated, OPENAI_AI_YAML)  # no key anywhere
        state = tagging_state()
        set_preview(state, 3, 0)
        run(state.request_tagging())
        assert state.tag_confirm_open is False
        notice = state.tagging_notice
        assert notice["level"] == "error"
        assert notice["title"] == "No API key for OpenAI"
        assert notice["action_href"] == "/settings?section=ai"
        assert not _CLI_OR_EMOJI.search(json.dumps(notice))

    @pytest.mark.parametrize(
        ("setup", "expected"),
        [
            ("none", "Preview first"),
            ("stale", "The filters changed after the preview"),
            ("empty", "No tracks match these filters"),
        ],
    )
    def test_refuses_without_a_current_preview(self, setup: str, expected: str) -> None:
        state = tagging_state()
        if setup == "stale":
            set_preview(state, 59)
            state.artist_filter = "Yundi"
        elif setup == "empty":
            set_preview(state, 0)
        with patch("plexmix.ui.states.tagging_state.tagging_preflight") as preflight:
            run(state.request_tagging())
        preflight.assert_not_called()
        assert state.tag_confirm_open is False
        assert expected in state.tagging_notice["text"]

    def test_filters_changing_while_the_copy_is_built_open_nothing(self) -> None:
        from plexmix.ui.states.tagging_state import EMPTY_SCOPE, TagPreflight

        state = tagging_state()
        set_preview(state, 59)

        def preflight(count: int, tagged: int) -> TagPreflight:
            state.artist_filter = "Yundi"  # a debounced commit lands meanwhile
            return TagPreflight("ready", scope=dict(EMPTY_SCOPE))

        with patch("plexmix.ui.states.tagging_state.tagging_preflight", preflight):
            run(state.request_tagging())
        assert state.tag_confirm_open is False
        assert "Preview again" in state.tagging_notice["text"]

    def test_running_job_opens_nothing(self) -> None:
        state = tagging_state()
        set_preview(state, 3)
        task_store.start("tagging")
        run(state.request_tagging())
        assert state.tag_confirm_open is False
        assert "already running" in state.tagging_notice["text"]

    def test_second_activation_while_checking_is_ignored(self) -> None:
        state = tagging_state()
        set_preview(state, 3)
        state.tag_confirm_checking = True
        with patch("plexmix.ui.states.tagging_state.tagging_preflight") as preflight:
            run(state.request_tagging())
        preflight.assert_not_called()

    def test_denied_does_nothing(self) -> None:
        state = tagging_state()
        set_preview(state, 3)
        state.access_allowed = lambda: False  # type: ignore[method-assign]
        with patch("plexmix.ui.states.tagging_state.tagging_preflight") as preflight:
            run(state.request_tagging())
        preflight.assert_not_called()
        assert state.tag_confirm_open is False


class TestConfirmationButtons:
    def test_confirm_starts_once(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = tagging_state()
        state.tag_confirm_open = True
        assert state.confirm_tagging() is TaggingState.start_tagging
        assert state.tag_confirm_open is False
        assert state.confirm_tagging() is None  # a repeated click starts nothing

    def test_secondary_tags_only_the_untagged_without_touching_fields(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = tagging_state()
        set_preview(state, 59, 40, artist="Yundi")
        state.tag_confirm_open = True
        result = state.tag_all_untagged()
        assert result is TaggingState.start_untagged_tagging
        assert not isinstance(result, (list, tuple))  # no rx.set_value: no field is reset
        assert state.artist_filter == "Yundi" and state.has_no_tags is False
        assert state.preview_is_current is True
        assert state.tag_confirm_open is False
        assert state.tag_all_untagged() is None

    def test_secondary_with_nothing_untagged_starts_nothing(self) -> None:
        state = tagging_state()
        set_preview(state, 40, 40)
        state.tag_confirm_open = True
        assert state.tag_all_untagged() is None
        assert state.tag_confirm_open is False

    def test_open_change_is_idempotent_and_cancel_closes(self) -> None:
        state = tagging_state()
        state.tag_confirm_open = True
        state.set_tag_confirm_open(False)
        state.set_tag_confirm_open(False)
        assert state.tag_confirm_open is False
        state.set_tag_confirm_open(True)  # only request_tagging opens it
        assert state.tag_confirm_open is False
        state.tag_confirm_open = True
        state.cancel_tag_confirm()
        assert state.tag_confirm_open is False


class TestStartGuard:
    def test_changed_filters_set_the_notice_and_never_start(self) -> None:
        state = tagging_state()
        set_preview(state, 5943, 4120)
        state.set_artist_filter("Yundi")  # changed after the preview
        with patch.object(task_store, "start") as start:
            run(state.start_tagging())
        start.assert_not_called()
        assert state.is_tagging is False
        assert state.tagging_notice["level"] == "warning"
        assert "Preview again" in state.tagging_notice["text"]

    def test_no_preview_never_starts(self) -> None:
        state = tagging_state()
        with patch.object(task_store, "start") as start:
            run(state.start_tagging(True))
        start.assert_not_called()
        assert "Preview first" in state.tagging_notice["text"]

    def _start_and_capture(self, state: Any, only_untagged: bool) -> dict[str, Any]:
        db, settings_patch, db_patch = mocked_db([])  # nothing left: aborts before the AI
        with settings_patch, db_patch:
            run(state.start_tagging(only_untagged))
        return dict(db.get_tracks_by_filter.call_args.kwargs)

    def test_only_untagged_passes_has_no_tags_with_the_previewed_filters(self) -> None:
        state = tagging_state()
        set_preview(state, 59, 40, artist=" Yundi ", stale_days="")
        kwargs = self._start_and_capture(state, True)
        assert kwargs == {
            "genre": None,
            "year_min": None,
            "year_max": None,
            "artist": "Yundi",
            "has_no_tags": True,
            "stale_days": None,
        }
        assert state.has_no_tags is False  # state filters (and the preview) unchanged
        entry = task_store.get("tagging")
        assert entry is not None and entry.status == "failed"
        assert entry.message == "No tracks match the previewed filters any more. Preview again."

    def test_secondary_handler_runs_only_untagged(self) -> None:
        state = tagging_state()
        set_preview(state, 59, 40, artist="Yundi")
        db, settings_patch, db_patch = mocked_db([])
        with settings_patch, db_patch:
            run(state.start_untagged_tagging())
        assert db.get_tracks_by_filter.call_args.kwargs["has_no_tags"] is True

    def test_full_start_uses_exactly_the_previewed_filters(self) -> None:
        state = tagging_state()
        set_preview(state, 59, 40, artist="Yundi", genre="Classical", stale_days="30")
        kwargs = self._start_and_capture(state, False)
        assert kwargs["has_no_tags"] is False
        assert (kwargs["artist"], kwargs["genre"], kwargs["stale_days"]) == (
            "Yundi",
            "Classical",
            30,
        )

    def test_only_untagged_with_nothing_untagged_is_refused(self) -> None:
        state = tagging_state()
        set_preview(state, 40, 40)
        with patch.object(task_store, "start") as start:
            run(state.start_tagging(True))
        start.assert_not_called()
        assert state.tagging_notice["text"] == "Every previewed track already has tags."

    def test_start_runs_the_job_on_a_real_library(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_config(_isolated, OPENAI_AI_YAML)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        make_library(_isolated, {"Yundi": (3, 1), "Other": (2, 0)})
        state = preview(tagging_state(), artist_filter="Yundi")
        assert (state.preview_count, state.preview_tagged_count) == (3, 1)

        seen: list[Any] = []

        def factory(provider: Any) -> Any:
            gen = MagicMock()

            def batch(tracks: Any, **kwargs: Any) -> Any:
                seen.extend(t["title"] for t in tracks)
                return {
                    t["id"]: {"tags": ["bright"], "environments": [], "instruments": []}
                    for t in tracks
                }

            gen.generate_tags_batch.side_effect = batch
            return gen

        with patch("plexmix.services.providers.build_ai_provider", return_value=object()), patch(
            "plexmix.ai.tag_generator.TagGenerator", factory
        ):
            run(state.start_untagged_tagging())
        assert sorted(seen) == ["Yundi song 1", "Yundi song 2"]  # only the untagged ones
        entry = task_store.get("tagging")
        assert entry is not None and entry.status == "completed"
        assert entry.extra["tags_generated_count"] == 2

    @pytest.mark.parametrize(
        "reply",
        [
            {"tags": [""], "environments": [""], "instruments": [""]},
            {"tags": [" ", "\t"], "environments": [None], "instruments": [{"a": 1}, True]},
            {"tags": ["", ""], "environments": [], "instruments": ["  "]},
        ],
        ids=["empty-texts", "blanks-and-junk", "two-empty-items"],
    )
    def test_a_reply_of_blank_items_writes_nothing_and_blanks_nothing(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any]
    ) -> None:
        """A result that did not come through the parser is cleaned before it is saved."""
        from plexmix.database.sqlite_manager import SQLiteManager

        write_config(_isolated, OPENAI_AI_YAML)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        make_library(_isolated, {"Yundi": (2, 2)})
        with SQLiteManager(str(_isolated / "plexmix.db")) as db:
            ids = [t.id for t in db.get_all_tracks()]
            for track_id in ids:
                db.update_track_tags(track_id, environments="focus", instruments="piano")
            stamps = {t.id: t.tags_generated_at for t in db.get_all_tracks()}
        state = preview(tagging_state(), artist_filter="Yundi")

        def factory(provider: Any) -> Any:
            gen = MagicMock()
            gen.generate_tags_batch.side_effect = lambda tracks, **kw: {
                t["id"]: dict(reply) for t in tracks
            }
            return gen

        with patch("plexmix.services.providers.build_ai_provider", return_value=object()), patch(
            "plexmix.ai.tag_generator.TagGenerator", factory
        ):
            run(state.start_tagging(False))
        entry = task_store.get("tagging")
        assert entry is not None and entry.status == "completed"
        assert entry.extra["tags_generated_count"] == 0
        with SQLiteManager(str(_isolated / "plexmix.db")) as db:
            for track in db.get_all_tracks():
                assert (track.tags, track.environments, track.instruments) == (
                    "chill,warm",
                    "focus",
                    "piano",
                )
                assert track.tags_generated_at == stamps[track.id]

    def test_a_reply_without_instruments_leaves_the_instruments(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only a column whose list has content is written; the others stay as they are."""
        from plexmix.database.sqlite_manager import SQLiteManager

        write_config(_isolated, OPENAI_AI_YAML)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        make_library(_isolated, {"Yundi": (1, 1)})
        with SQLiteManager(str(_isolated / "plexmix.db")) as db:
            track_id = db.get_all_tracks()[0].id
            db.update_track_tags(track_id, environments="focus", instruments="piano")
        state = preview(tagging_state(), artist_filter="Yundi")

        def factory(provider: Any) -> Any:
            gen = MagicMock()
            gen.generate_tags_batch.side_effect = lambda tracks, **kw: {
                t["id"]: {"tags": ["bright", " "], "environments": [""], "instruments": [None]}
                for t in tracks
            }
            return gen

        with patch("plexmix.services.providers.build_ai_provider", return_value=object()), patch(
            "plexmix.ai.tag_generator.TagGenerator", factory
        ):
            run(state.start_tagging(False))
        entry = task_store.get("tagging")
        assert entry is not None and entry.extra["tags_generated_count"] == 1
        with SQLiteManager(str(_isolated / "plexmix.db")) as db:
            track = db.get_all_tracks()[0]
        assert (track.tags, track.environments, track.instruments) == ("bright", "focus", "piano")


# ── Job outcome notices and the re-count ────────────────────────────────


class TestJobOutcome:
    def _finished(self, status: str, message: str = "", **extra: Any) -> Any:
        task_store.start("tagging")
        if extra:
            task_store.update("tagging", extra=extra)
        task_store.complete("tagging", status=status, message=message)
        state = tagging_state()
        state.is_tagging = True
        return state

    def test_failure_stays_visible_with_the_ai_settings_action(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = self._finished(
            "failed", "Invalid API key. Please check your key and try again.", provider_failure=True
        )
        set_preview(state, 3)
        events = state.poll_task_progress()
        assert isinstance(events, list) and events[-1] is TaggingState.preview_selection
        notice = state.tagging_notice
        assert (notice["level"], notice["source"]) == ("error", "job")
        assert notice["title"] == "Tagging stopped"
        assert notice["text"].startswith("Invalid API key.")
        assert notice["action_href"] == "/settings?section=ai"
        assert state.is_tagging is False

    def test_non_provider_failure_has_no_settings_action(self) -> None:
        state = self._finished("failed", "No tracks match the previewed filters any more.")
        state.poll_task_progress()
        assert state.tagging_notice["action_href"] == ""
        assert state.tagging_notice["action_label"] == ""

    def test_completion_recounts_the_preview(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = self._finished("completed")
        set_preview(state, 19)
        events = state.poll_task_progress()
        assert events[-1] is TaggingState.preview_selection
        assert state.tagging_progress == 100

    def test_completion_without_a_preview_only_toasts(self) -> None:
        state = self._finished("cancelled")
        events = state.poll_task_progress()
        assert not isinstance(events, list)
        assert state.is_tagging is False

    def test_provider_exception_text_is_friendly(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        from plexmix.services import readiness as rd

        # The run's configured AI key: redacted from the log as well as the message.
        monkeypatch.setattr(rd, "resolve_secret", lambda component, settings: SENTINEL_OPENAI)
        caplog.set_level(logging.ERROR)
        state = tagging_state()
        set_preview(state, 3)
        db, settings_patch, db_patch = mocked_db()
        db.get_tracks_by_filter.side_effect = RuntimeError(
            "401 Unauthorized: key " + SENTINEL_OPENAI
        )
        with settings_patch, db_patch:
            run(state.start_tagging())
        entry = task_store.get("tagging")
        assert entry is not None and entry.status == "failed"
        assert SENTINEL_OPENAI not in entry.message
        assert "Tagging failed" in caplog.text
        assert SENTINEL_OPENAI not in caplog.text


# ── 8.3 loading flag and revisits ────────────────────────────────────────


class TestRecentlyTaggedLoading:
    def test_defaults_to_loading(self) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        assert TaggingState.recent_loaded is False
        assert tagging_state().recent_loaded is False

    def test_missing_database_loads_an_empty_list(self, _isolated: Path) -> None:
        state = tagging_state()
        state.recently_tagged_tracks = [{"id": "1"}]
        state.load_recently_tagged()
        assert state.recent_loaded is True
        assert state.recently_tagged_tracks == []
        assert state.recent_error == ""

    def test_rows_load(self, _isolated: Path) -> None:
        make_library(_isolated, {"Yundi": (3, 2)})
        state = tagging_state()
        state.load_recently_tagged()
        assert state.recent_loaded is True
        assert len(state.recently_tagged_tracks) == 2

    def test_failure_is_shown_and_retry_reloads(self) -> None:
        db, settings_patch, db_patch = mocked_db()
        db.get_recently_tagged_tracks.side_effect = RuntimeError("locked")
        state = tagging_state()
        with settings_patch, db_patch:
            state.load_recently_tagged()
        assert state.recent_loaded is True
        assert state.recent_error == "Couldn't load the recently tagged tracks."
        db.get_recently_tagged_tracks.side_effect = None
        db.get_recently_tagged_tracks.return_value = [{"id": 1, "title": "t"}]
        with settings_patch, db_patch:
            state.retry_recently_tagged()
        assert state.recent_error == ""
        assert state.recently_tagged_tracks == [{"id": "1", "title": "t"}]

    def test_revisit_recounts_an_earlier_preview(self, _isolated: Path) -> None:
        from plexmix.ui.states.tagging_state import TaggingState

        state = tagging_state()
        state.check_auth = lambda: True  # type: ignore[method-assign]
        state.load_app_data = lambda: None  # type: ignore[method-assign]
        assert state.on_load() is None  # first visit: nothing previewed, nothing counted
        set_preview(state, 12)
        assert state.on_load() is TaggingState.preview_selection
