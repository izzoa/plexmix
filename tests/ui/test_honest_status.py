"""webui-honest-status-and-first-run: app-wide readiness wiring (group 3), the shared
scope confirmation (4.1) and the Library whole-library embedding flow (part of 9.2).

Reflex is mocked (tests/ui/conftest.py). Nothing here touches the network, the
real keyring or ~/.plexmix: every test runs on a scratch ``PLEXMIX_DATA_DIR`` with
its own config.yaml, the keyring lookup is patched out, Plex/providers are fakes,
and the readiness and task stores are cleared around each test.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from plexmix.services import readiness as rd
from plexmix.services.readiness import readiness_store
from plexmix.ui.job_manager import task_store

SENTINEL_TOKEN = "plexTOKEN-SENTINEL-honest-0001"
SENTINEL_OPENAI = "sk-SENTINEL-honest-openai-0001"

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
_JOBS = ("sync", "embedding", "tagging")


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
    for job in _JOBS:
        task_store.clear(job)
    yield data
    readiness_store.clear()
    for job in _JOBS:
        task_store.clear(job)


# ── Fixtures and helpers ─────────────────────────────────────────────────

PLEX_YAML = "plex:\n  url: http://plex.local:32400\n  library_name: Music\n"
OPENAI_EMBED_YAML = "embedding:\n  default_provider: openai\n  model: text-embedding-3-small\n"
LOCAL_EMBED_YAML = "embedding:\n  default_provider: local\n  model: all-MiniLM-L6-v2\n"


def write_config(data: Path, body: str) -> None:
    (data / "config.yaml").write_text(body or "{}\n")


def make_db(
    data: Path,
    *,
    tracks: int = 3,
    tagged: int = 0,
    embedded: int = 0,
    synced: bool = True,
    model: str = "openai",
) -> list[int]:
    """A real SQLite library in the scratch data dir; returns the track ids."""
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
        for tid in ids[:tagged]:
            db.update_track_tags(tid, tags="chill")
        for tid in ids[:embedded]:
            db.insert_embedding(
                Embedding(track_id=tid, embedding_model=model, embedding_dim=2, vector=[0.1, 0.2])
            )
        if synced:
            db.insert_sync_record(
                SyncHistory(
                    tracks_added=tracks, tracks_updated=0, tracks_removed=0, status="success"
                )
            )
    return ids


def write_index_metadata(data: Path, dimension: int, track_ids: list[int]) -> None:
    """The FAISS index file (readiness checks that it exists) and its metadata."""
    (data / "embeddings.index").write_bytes(b"faiss")
    with open(data / "embeddings.metadata", "wb") as f:
        pickle.dump({"dimension": dimension, "track_ids": list(track_ids)}, f)


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


def app_state() -> Any:
    from plexmix.ui.states.app_state import AppState

    return _harness(AppState)


def library_state() -> Any:
    from plexmix.ui.states.library_state import LibraryState

    state = _harness(LibraryState)
    state.selected_tracks = []
    return state


async def _drain(gen: Any) -> list[Any]:
    """Run a background handler (an async generator or a coroutine) to the end."""
    if hasattr(gen, "__aiter__"):
        return [event async for event in gen]
    await gen
    return []


def run(gen: Any) -> list[Any]:
    return asyncio.run(_drain(gen))


def fake_plex_server(libraries: list[str], calls: Optional[list[Any]] = None) -> Any:
    """A PlexServer stand-in whose library lists ``libraries`` as music sections."""

    def factory(baseurl: Any = None, token: Any = None, session: Any = None, timeout: Any = None):
        if calls is not None:
            calls.append({"url": baseurl, "token": token, "timeout": timeout})
        sections = [SimpleNamespace(title=t, type="artist") for t in libraries]
        sections.append(SimpleNamespace(title="Movies", type="movie"))
        return SimpleNamespace(library=SimpleNamespace(sections=lambda: sections))

    return factory


def snapshot_text(state: Any) -> str:
    return json.dumps(state.readiness, sort_keys=True)


# ── 3.1 AppState.readiness ───────────────────────────────────────────────


class TestReadinessSnapshot:
    def test_key_present_but_untested_is_unchecked_with_display_label(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_config(_isolated, "ai:\n  default_provider: openai\n  model: gpt-5-mini\n")
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        state = app_state()
        state.check_configuration_status()

        ai = state.readiness["ai"]
        assert ai["level"] == "unchecked"
        assert ai["state"] == "Set up · not checked"
        assert ai["label"] == "OpenAI"
        assert ai["model"] == "gpt-5-mini"
        assert ai["tone"] != "success"
        assert state.ai_provider_name == "OpenAI"
        assert state.ai_model_name == "gpt-5-mini"
        assert state.ai_provider_configured is True
        text = snapshot_text(state)
        assert "Openai" not in text
        assert SENTINEL_OPENAI not in text

    def test_every_entry_has_the_same_string_keys(self, _isolated: Path) -> None:
        state = app_state()
        state.check_configuration_status()
        assert set(state.readiness) == set(rd.COMPONENTS)
        keys = {frozenset(entry) for entry in state.readiness.values()}
        assert len(keys) == 1
        for entry in state.readiness.values():
            assert {"level", "cause", "label", "model", "title", "body", "action_label"} <= set(
                entry
            )
            assert {"action_href", "checked_at", "checked_ago", "checked_title"} <= set(entry)
            assert all(isinstance(v, str) for v in entry.values())

    def test_local_without_deps_shows_the_badge(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_config(_isolated, LOCAL_EMBED_YAML)
        monkeypatch.setattr(rd, "_module_available", lambda name: False)
        state = app_state()
        state.check_configuration_status()

        emb = state.readiness["embeddings"]
        assert emb["level"] == "failing"
        assert emb["cause"] == "missing_dependency"
        assert emb["blocking"] == "true"
        assert emb["label"] == "Local"
        assert "plexmix[local]" in emb["body"]
        assert state.embedding_provider_configured is False
        assert state.show_doctor_badge is True

    def test_everything_unchecked_with_a_usable_index_shows_no_badge(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_config(_isolated, PLEX_YAML + OPENAI_EMBED_YAML)
        monkeypatch.setenv("PLEX_TOKEN", SENTINEL_TOKEN)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        ids = make_db(_isolated, tracks=3, embedded=3)
        write_index_metadata(_isolated, 1536, ids)
        state = app_state()
        state.load_app_data_for_test = None  # harmless attribute on the harness
        state.check_configuration_status()

        assert state.readiness["plex"]["level"] == "unchecked"
        assert state.readiness["embeddings"]["level"] == "unchecked"
        assert state.readiness["library"]["level"] == "ready"
        assert state.readiness["index"]["level"] == "ready"
        assert state.plex_configured is True
        assert state.embedding_provider_configured is True
        assert state.show_doctor_badge is False
        text = snapshot_text(state)
        assert SENTINEL_TOKEN not in text and SENTINEL_OPENAI not in text

    def test_plex_not_set_up_is_not_configured_and_raises_the_badge(self, _isolated: Path) -> None:
        state = app_state()
        state.check_configuration_status()
        plex = state.readiness["plex"]
        assert plex["level"] == "not_set_up"
        assert plex["action_href"] == "/settings?section=plex"
        assert "plexmix config" not in snapshot_text(state)
        assert state.plex_configured is False
        assert state.show_doctor_badge is True

    def test_stats_are_ints_and_loaded(self, _isolated: Path) -> None:
        make_db(_isolated, tracks=5943 // 1000, tagged=1, embedded=2)
        state = app_state()
        assert state.library_stats_loaded is False
        state.load_library_stats()

        assert state.library_stats_loaded is True
        assert state.stat_db_exists is True
        assert (state.stat_total, state.stat_tagged, state.stat_untagged) == (5, 1, 4)
        assert (state.stat_embedded, state.stat_missing_embeddings) == (2, 3)
        assert state.total_tracks == "5" and state.embedded_tracks == "2"
        assert state.audio_analyzed_tracks == "0"
        # sync_date is stored as naive UTC: a sync just now reads "just now" in any zone.
        assert state.last_sync_at
        assert state.last_sync_ago == "just now"
        assert "·" in state.last_sync_title
        library = state.readiness["library"]
        assert library["checked_ago"] == "just now"
        assert library["checked_at"] == state.last_sync_at
        assert abs(int(library["checked_ts"]) - time.time()) < 120

    def test_stored_utc_reads_naive_values_as_utc(self) -> None:
        from datetime import datetime, timedelta, timezone

        from plexmix.ui.states.app_state import stored_utc

        naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        parsed = stored_utc(str(naive))
        assert parsed is not None and parsed.tzinfo is not None
        from plexmix.ui.utils.helpers import format_relative

        assert format_relative(parsed) == "2 h ago"
        assert stored_utc(None) is None and stored_utc("not a date") is None

    def test_missing_database_is_loaded_as_not_synced(self, _isolated: Path) -> None:
        state = app_state()
        state.load_library_stats()
        assert state.library_stats_loaded is True
        assert state.stat_db_exists is False
        assert state.stat_total == 0
        assert state.total_tracks == "0"
        assert state.last_sync is None and state.last_sync_ago == ""
        assert state.readiness["library"]["cause"] == "not_synced"
        assert not (_isolated / "plexmix.db").exists()

    def test_dimension_mismatch_warning_names_both_sizes_without_emoji(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_config(_isolated, OPENAI_EMBED_YAML)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        ids = make_db(_isolated, tracks=2, embedded=2)
        write_index_metadata(_isolated, 1024, ids)
        state = app_state()
        state.check_configuration_status()

        warning = state.embedding_dimension_warning
        assert "1,024" in warning and "1,536" in warning
        assert "⚠" not in warning
        assert state.readiness["index"]["cause"] == "dimension_mismatch"
        assert state.stat_index_dimension == 1024
        assert state.stat_expected_dimension == 1536
        assert state.show_doctor_badge is True

    def test_optional_enrichment_flags(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_config(_isolated, "audio:\n  enabled: true\nmusicbrainz:\n  enabled: true\n")
        monkeypatch.setattr(rd, "_module_available", lambda name: name == "essentia")
        state = app_state()
        state.check_configuration_status()
        assert state.audio_available is True
        assert state.audio_analysis_enabled is True
        assert state.mb_enabled is True

    def test_unreadable_settings_fail_without_raising(self) -> None:
        state = app_state()
        with patch(
            "plexmix.config.settings.Settings.load_from_file",
            side_effect=ValueError("broken yaml with secret sk-SENTINEL-x"),
        ):
            state.check_configuration_status()
        assert state.library_stats_loaded is True
        assert all(e["level"] == "failing" for e in state.readiness.values())
        assert all(e["blocking"] == "false" for e in state.readiness.values())
        assert state.show_doctor_badge is True
        assert "sk-SENTINEL-x" not in snapshot_text(state)

    def test_load_app_data_reads_the_facts_once(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui import session_liveness

        monkeypatch.setattr(session_liveness, "sweep_disconnected_sessions", lambda: [])
        monkeypatch.setattr(session_liveness, "ensure_periodic_sweep", lambda: False)
        calls: list[int] = []
        real = rd.collect_facts
        monkeypatch.setattr(rd, "collect_facts", lambda s: calls.append(1) or real(s))
        state = app_state()
        state.load_app_data()
        assert len(calls) == 1
        assert state.library_stats_loaded is True
        state.load_library_stats()  # a later reload (e.g. after a job) reads again
        assert len(calls) == 2


# ── 3.2 refresh_plex_readiness ───────────────────────────────────────────


@pytest.fixture
def plex_configured(_isolated: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    write_config(_isolated, PLEX_YAML)
    monkeypatch.setenv("PLEX_TOKEN", SENTINEL_TOKEN)
    return _isolated


class TestRefreshPlexReadiness:
    def test_two_calls_within_the_ttl_probe_once(self, plex_configured: Path) -> None:
        from plexmix.config.constants import PLEX_READINESS_TIMEOUT_S

        calls: list[Any] = []
        state = app_state()
        with patch("plexapi.server.PlexServer", fake_plex_server(["Music"], calls)):
            run(state.refresh_plex_readiness())
            run(state.refresh_plex_readiness())
        assert len(calls) == 1
        assert calls[0]["timeout"] == PLEX_READINESS_TIMEOUT_S
        assert calls[0]["url"] == "http://plex.local:32400"
        plex = state.readiness["plex"]
        assert plex["level"] == "ready" and plex["tone"] == "success"
        assert plex["checked_ago"] == "just now"
        assert SENTINEL_TOKEN not in snapshot_text(state)

    def test_force_skips_only_the_age_check(self, plex_configured: Path) -> None:
        calls: list[Any] = []
        state = app_state()
        with patch("plexapi.server.PlexServer", fake_plex_server(["Music"], calls)):
            run(state.refresh_plex_readiness())
            run(state.refresh_plex_readiness(force=True))
        assert len(calls) == 2

    def test_an_old_result_is_checked_again(self, plex_configured: Path) -> None:
        from plexmix.config.constants import PLEX_READINESS_TTL_S

        calls: list[Any] = []
        state = app_state()
        with patch("plexapi.server.PlexServer", fake_plex_server(["Music"], calls)):
            run(state.refresh_plex_readiness())
            rec = readiness_store.get("plex")
            assert rec is not None
            readiness_store.record(
                "plex",
                rec.level,
                rec.cause,
                fingerprint=rec.fingerprint,
                checked_at=rec.checked_at - PLEX_READINESS_TTL_S - 1,
            )
            run(state.refresh_plex_readiness())
        assert len(calls) == 2

    def test_a_configuration_change_discards_the_result(
        self, plex_configured: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[Any] = []
        state = app_state()
        with patch("plexapi.server.PlexServer", fake_plex_server(["Music"], calls)):
            run(state.refresh_plex_readiness())
            monkeypatch.setenv("PLEX_TOKEN", SENTINEL_TOKEN + "-new")
            state.check_configuration_status()
            assert state.readiness["plex"]["level"] == "unchecked"
            run(state.refresh_plex_readiness())
        assert len(calls) == 2

    @pytest.mark.parametrize(
        "make_exc,cause",
        [
            (lambda: __import__("requests").exceptions.ConnectTimeout("slow"), "timeout"),
            (lambda: __import__("requests").exceptions.ReadTimeout("slow"), "timeout"),
            (lambda: TimeoutError("slow"), "timeout"),
            (lambda: __import__("requests").exceptions.ConnectionError("refused"), "unreachable"),
            (
                lambda: __import__("plexapi.exceptions").exceptions.Unauthorized("(401)"),
                "invalid_credentials",
            ),
            (lambda: __import__("plexapi.exceptions").exceptions.NotFound("(404)"), "unreachable"),
            (lambda: RuntimeError("something odd"), "unknown"),
        ],
    )
    def test_exceptions_map_to_their_cause(
        self, plex_configured: Path, make_exc: Any, cause: str
    ) -> None:
        def boom(*args: Any, **kwargs: Any) -> Any:
            raise make_exc()

        state = app_state()
        with patch("plexapi.server.PlexServer", boom):
            run(state.refresh_plex_readiness())
        plex = state.readiness["plex"]
        assert plex["level"] == "failing"
        assert plex["cause"] == cause
        assert plex["tone"] == "error"
        assert plex["blocking"] == "false"  # network failures never lock anyone out

    def test_unreachable_names_the_server(self, plex_configured: Path) -> None:
        import requests

        def boom(*args: Any, **kwargs: Any) -> Any:
            raise requests.exceptions.ConnectionError("refused")

        state = app_state()
        with patch("plexapi.server.PlexServer", boom):
            run(state.refresh_plex_readiness())
        assert state.readiness["plex"]["title"] == (
            "Can't reach your Plex server at http://plex.local:32400"
        )
        assert state.readiness["plex"]["action_href"] == "/settings?section=plex"

    def test_missing_library_is_library_not_found(self, plex_configured: Path) -> None:
        state = app_state()
        with patch("plexapi.server.PlexServer", fake_plex_server(["Audiobooks"])):
            run(state.refresh_plex_readiness())
        assert state.readiness["plex"]["cause"] == "library_not_found"
        assert 'named "Music"' in state.readiness["plex"]["body"]

    def test_library_match_ignores_case(self, plex_configured: Path) -> None:
        state = app_state()
        with patch("plexapi.server.PlexServer", fake_plex_server([" music "])):
            run(state.refresh_plex_readiness())
        assert state.readiness["plex"]["level"] == "ready"

    def test_the_token_is_never_logged_or_shown(
        self, plex_configured: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        def boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(f"server said no to token {SENTINEL_TOKEN}")

        caplog.set_level(logging.DEBUG)
        state = app_state()
        with patch("plexapi.server.PlexServer", boom):
            run(state.refresh_plex_readiness())
        assert state.readiness["plex"]["cause"] == "unknown"
        assert SENTINEL_TOKEN not in caplog.text
        assert SENTINEL_TOKEN not in snapshot_text(state)
        rec = readiness_store.get("plex")
        assert rec is not None and SENTINEL_TOKEN not in rec.message
        assert SENTINEL_TOKEN not in rec.fingerprint

    def test_not_set_up_sends_nothing(self, _isolated: Path) -> None:
        calls: list[Any] = []
        state = app_state()
        with patch("plexapi.server.PlexServer", fake_plex_server(["Music"], calls)):
            run(state.refresh_plex_readiness())
        assert calls == []
        assert readiness_store.get("plex") is None

    def test_unauthorized_session_sends_nothing(self, plex_configured: Path) -> None:
        calls: list[Any] = []
        state = app_state()
        state.access_allowed = lambda: False
        with patch("plexapi.server.PlexServer", fake_plex_server(["Music"], calls)):
            run(state.refresh_plex_readiness())
        assert calls == []

    def test_a_check_in_flight_is_shared(self, plex_configured: Path) -> None:
        from plexmix.config.settings import Settings

        calls: list[Any] = []
        state = app_state()
        fp = rd.current_fingerprint("plex", Settings.load_from_file())
        assert readiness_store.begin_check("plex")  # another tab is checking

        async def scenario() -> None:
            async def other_tab_finishes() -> None:
                await asyncio.sleep(0.3)
                readiness_store.record("plex", "ready", fingerprint=fp)
                readiness_store.end_check("plex")

            await asyncio.gather(_drain(state.refresh_plex_readiness()), other_tab_finishes())

        with patch("plexapi.server.PlexServer", fake_plex_server(["Music"], calls)):
            asyncio.run(scenario())
        assert calls == []
        assert state.readiness["plex"]["level"] == "ready"

    def test_checking_is_shown_while_the_probe_runs(self, plex_configured: Path) -> None:
        seen: list[str] = []
        state = app_state()

        def slow(*args: Any, **kwargs: Any) -> Any:
            seen.append(state.readiness["plex"]["level"])
            return fake_plex_server(["Music"])(*args, **kwargs)

        with patch("plexapi.server.PlexServer", slow):
            run(state.refresh_plex_readiness())
        assert seen == ["checking"]
        assert state.readiness["plex"]["level"] == "ready"
        assert not readiness_store.is_checking("plex")


class TestPlexReadinessPublishesCurrentFacts:
    """Review fixes: a tab never keeps a stale "Checking…" or stale facts."""

    def test_a_result_recorded_by_another_tab_replaces_checking(
        self, plex_configured: Path
    ) -> None:
        from plexmix.config.settings import Settings

        calls: list[Any] = []
        state = app_state()
        # This tab's snapshot caught another tab's check in flight...
        assert readiness_store.begin_check("plex")
        state.check_configuration_status()
        assert state.readiness["plex"]["level"] == "checking"
        # ...which recorded its result before this tab's refresh ran.
        fp = rd.current_fingerprint("plex", Settings.load_from_file())
        readiness_store.record("plex", "ready", fingerprint=fp)
        readiness_store.end_check("plex")
        with patch("plexapi.server.PlexServer", fake_plex_server(["Music"], calls)):
            run(state.refresh_plex_readiness())
        assert calls == []  # the fresh result is reused, not re-probed
        assert state.readiness["plex"]["level"] == "ready"

    def test_the_final_publish_rereads_the_facts(self, plex_configured: Path) -> None:
        """A sync that finished while Plex was being probed is not overwritten."""
        state = app_state()

        def probe_while_a_sync_lands(*args: Any, **kwargs: Any) -> Any:
            make_db(plex_configured, tracks=4)  # the library changes mid-probe
            return fake_plex_server(["Music"])(*args, **kwargs)

        with patch("plexapi.server.PlexServer", probe_while_a_sync_lands):
            run(state.refresh_plex_readiness())
        assert state.readiness["plex"]["level"] == "ready"
        assert state.stat_total == 4
        assert state.readiness["library"]["level"] == "ready"


class TestUnreadableSettings:
    def test_unreadable_settings_are_flagged_not_zero(self) -> None:
        state = app_state()
        with patch(
            "plexmix.config.settings.Settings.load_from_file",
            side_effect=ValueError("broken yaml"),
        ):
            state.check_configuration_status()
        assert state.settings_unreadable is True
        assert state.library_stats_loaded is True

    def test_a_good_read_clears_the_flag(self, _isolated: Path) -> None:
        state = app_state()
        state.settings_unreadable = True
        state.check_configuration_status()
        assert state.settings_unreadable is False


class TestPlexReadinessOnLoad:
    """Dashboard, Doctor and Generator on_load return the refresh when it is due."""

    @staticmethod
    def _entry(level: str, checked_ts: str = "") -> dict[str, dict[str, str]]:
        return {"plex": {"level": level, "checked_ts": checked_ts}}

    @pytest.mark.parametrize(
        "level,age,due",
        [
            ("unchecked", None, True),
            ("checking", None, True),
            ("not_set_up", None, False),
            ("ready", 5.0, False),
            ("failing", 5.0, False),
            ("ready", 10_000.0, True),
            ("failing", 10_000.0, True),
        ],
    )
    def test_due_rule(self, level: str, age: Optional[float], due: bool) -> None:
        import time

        from plexmix.ui.states.app_state import AppState

        state = app_state()
        ts = "" if age is None else str(int(time.time() - age))
        state.readiness = self._entry(level, ts)
        events = state._plex_readiness_events()
        assert (events == [AppState.refresh_plex_readiness]) is due
        if not due:
            assert events == []

    def _loaded(self, cls: Any, level: str) -> Any:
        state = cls()
        state.check_auth = MagicMock(return_value=True)

        def fake_load() -> None:
            state.readiness = self._entry(level)

        state.load_app_data = fake_load
        return state

    def test_dashboard(self) -> None:
        from plexmix.ui.states.app_state import AppState
        from plexmix.ui.states.dashboard_state import DashboardState

        state = self._loaded(DashboardState, "unchecked")
        state.load_recent_playlists = MagicMock()
        assert AppState.refresh_plex_readiness in state.on_load()
        state = self._loaded(DashboardState, "not_set_up")
        state.load_recent_playlists = MagicMock()
        assert AppState.refresh_plex_readiness not in state.on_load()

    def test_doctor(self) -> None:
        from plexmix.ui.states.app_state import AppState
        from plexmix.ui.states.doctor_state import DoctorState

        state = self._loaded(DoctorState, "unchecked")
        state.poll_task_progress = MagicMock()
        events = state.on_load()
        assert DoctorState.run_health_check in events
        assert AppState.refresh_plex_readiness in events

    def test_generator(self) -> None:
        from plexmix.ui.states.app_state import AppState
        from plexmix.ui.states.generator_state import GeneratorState

        state = self._loaded(GeneratorState, "unchecked")
        state._load_audio_stats = MagicMock()
        state._load_templates = MagicMock()
        state._rerun_generation_config = ""
        assert state.on_load() == [AppState.refresh_plex_readiness]
        state = self._loaded(GeneratorState, "not_set_up")
        state._load_audio_stats = MagicMock()
        state._load_templates = MagicMock()
        state._rerun_generation_config = ""
        assert state.on_load() is None


# ── 3.3 Real outcomes recorded: Settings tests ──────────────────────────


class _SettingsForm:
    """A SettingsState stand-in for the ``_settings_testing`` helpers."""

    def __init__(self, secrets: Optional[dict[str, str]] = None, **values: Any) -> None:
        self._secrets = secrets or {}
        self.testing_connection = False
        self.plex_test_status = self.ai_test_status = self.embedding_test_status = ""
        self.plex_url = "http://plex.local:32400"
        self.plex_library = "Music"
        self.plex_libraries: list[str] = []
        self.ai_provider = "openai"
        self.ai_model = "gpt-5-mini"
        self.ai_custom_endpoint = self.ai_custom_model = self.ai_local_endpoint = ""
        self.ai_local_mode = "builtin"
        self.embedding_provider = "openai"
        self.embedding_model = "text-embedding-3-small"
        self.embedding_dimension = 1536
        self.embedding_custom_endpoint = self.embedding_custom_model = ""
        self.embedding_custom_dimension = 1536
        for key, value in values.items():
            setattr(self, key, value)

    def _resolve_secret(self, field: str) -> str:
        return self._secrets.get(field, "")

    async def __aenter__(self) -> "_SettingsForm":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _saved_fingerprint(data: Path, component: str, body: str) -> str:
    from plexmix.config.settings import Settings

    write_config(data, body)
    return rd.current_fingerprint(component, Settings.load_from_file())


class TestSettingsTestsRecord:
    def test_plex_ready_only_when_the_saved_library_is_listed(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.states._settings_testing import test_plex_connection_impl

        form = _SettingsForm({"plex_token": SENTINEL_TOKEN})
        with patch("plexapi.server.PlexServer", fake_plex_server(["Music", "Jazz"])):
            asyncio.run(test_plex_connection_impl(form))
        rec = readiness_store.get("plex")
        assert rec is not None and rec.level == rd.Level.READY
        # Saving exactly the tested values keeps the verification.
        monkeypatch.setenv("PLEX_TOKEN", SENTINEL_TOKEN)
        assert rec.fingerprint == _saved_fingerprint(_isolated, "plex", PLEX_YAML)
        assert form.plex_test_status.startswith("✓")

    def test_plex_connected_but_library_missing_is_failing(self) -> None:
        from plexmix.ui.states._settings_testing import test_plex_connection_impl

        form = _SettingsForm({"plex_token": SENTINEL_TOKEN}, plex_library="Music")
        with patch("plexapi.server.PlexServer", fake_plex_server(["Jazz"])):
            asyncio.run(test_plex_connection_impl(form))
        rec = readiness_store.get("plex")
        assert rec is not None
        assert (rec.level, rec.cause) == (rd.Level.FAILING, rd.Cause.LIBRARY_NOT_FOUND)
        assert form.plex_libraries == ["Jazz"]  # the picker still gets the list

    def test_plex_without_a_chosen_library_records_nothing(self) -> None:
        from plexmix.ui.states._settings_testing import test_plex_connection_impl

        form = _SettingsForm({"plex_token": SENTINEL_TOKEN}, plex_library="")
        with patch("plexapi.server.PlexServer", fake_plex_server(["Music"])):
            asyncio.run(test_plex_connection_impl(form))
        assert readiness_store.get("plex") is None
        assert form.plex_libraries == ["Music"]

    def test_plex_failure_records_the_cause_without_the_token(self) -> None:
        import requests

        from plexmix.ui.states._settings_testing import test_plex_connection_impl

        def boom(*args: Any, **kwargs: Any) -> Any:
            raise requests.exceptions.ConnectionError(f"refused {SENTINEL_TOKEN}")

        form = _SettingsForm({"plex_token": SENTINEL_TOKEN})
        with patch("plexapi.server.PlexServer", boom):
            asyncio.run(test_plex_connection_impl(form))
        rec = readiness_store.get("plex")
        assert rec is not None
        assert (rec.level, rec.cause) == (rd.Level.FAILING, rd.Cause.UNREACHABLE)
        assert SENTINEL_TOKEN not in rec.message
        assert SENTINEL_TOKEN not in form.plex_test_status

    def test_ai_success_is_ready_for_the_saved_values(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.states._settings_testing import test_ai_provider_impl

        form = _SettingsForm({"ai_api_key": SENTINEL_OPENAI})
        with patch("openai.OpenAI") as client:
            client.return_value.chat.completions.create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="test"))]
            )
            asyncio.run(test_ai_provider_impl(form))
        rec = readiness_store.get("ai")
        assert rec is not None and rec.level == rd.Level.READY
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        body = "ai:\n  default_provider: openai\n  model: gpt-5-mini\n"
        assert rec.fingerprint == _saved_fingerprint(_isolated, "ai", body)
        assert form.ai_test_status == "✓ OpenAI provider test successful"

    def test_ai_rejected_key_is_invalid_credentials(self) -> None:
        from plexmix.ui.states._settings_testing import test_ai_provider_impl

        form = _SettingsForm({"ai_api_key": SENTINEL_OPENAI})
        with patch("openai.OpenAI") as client:
            client.return_value.chat.completions.create.side_effect = RuntimeError(
                f"Error code: 401 - invalid api key {SENTINEL_OPENAI}"
            )
            asyncio.run(test_ai_provider_impl(form))
        rec = readiness_store.get("ai")
        assert rec is not None
        assert (rec.level, rec.cause) == (rd.Level.FAILING, rd.Cause.INVALID_CREDENTIALS)
        assert SENTINEL_OPENAI not in rec.message

    def test_ai_without_a_key_is_missing_api_key(self) -> None:
        from plexmix.ui.states._settings_testing import test_ai_provider_impl

        form = _SettingsForm({})
        with patch("openai.OpenAI") as client:
            client.return_value.chat.completions.create.side_effect = RuntimeError("401")
            asyncio.run(test_ai_provider_impl(form))
        rec = readiness_store.get("ai")
        assert rec is not None and rec.cause == rd.Cause.MISSING_API_KEY

    def test_embedding_success_is_ready_even_with_another_index_size(self) -> None:
        from plexmix.ui.states._settings_testing import test_embedding_provider_impl

        form = _SettingsForm({"embedding_api_key": SENTINEL_OPENAI}, embedding_dimension=1024)
        with patch("openai.OpenAI") as client:
            client.return_value.embeddings.create.return_value = SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.0] * 1536)]
            )
            asyncio.run(test_embedding_provider_impl(form))
        rec = readiness_store.get("embeddings")
        assert rec is not None and rec.level == rd.Level.READY
        assert "dimension mismatch" in form.embedding_test_status

    def test_local_embedding_without_the_package_is_missing_dependency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.states._settings_testing import test_embedding_provider_impl

        monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # import fails
        form = _SettingsForm(
            embedding_provider="local", embedding_model="all-MiniLM-L6-v2", embedding_dimension=384
        )
        asyncio.run(test_embedding_provider_impl(form))
        rec = readiness_store.get("embeddings")
        assert rec is not None
        assert (rec.level, rec.cause) == (rd.Level.FAILING, rd.Cause.MISSING_DEPENDENCY)
        assert form.embedding_test_status.startswith("✗")

    def test_unknown_embedding_provider_is_never_ready(self) -> None:
        from plexmix.ui.states._settings_testing import test_embedding_provider_impl

        form = _SettingsForm(embedding_provider="nope")
        asyncio.run(test_embedding_provider_impl(form))
        rec = readiness_store.get("embeddings")
        assert rec is not None and rec.level == rd.Level.FAILING
        assert form.embedding_test_status.startswith("✗")


# ── 3.3 Real outcomes recorded: sync and tagging ────────────────────────


class TestSyncRecords:
    def _run_sync(self) -> Any:
        state = library_state()
        state.sync_mode = "incremental"
        run(state.start_sync())
        return state

    @pytest.mark.parametrize(
        "code,cause,recorded,title",
        [
            ("unreachable", "unreachable", True, "Can't reach your Plex server at"),
            ("library_missing", "library_not_found", True, "Music library not found"),
            ("not_configured", "missing_config", False, "Plex isn't connected yet"),
            ("no_library", "missing_config", False, "No music library chosen"),
        ],
    )
    def test_connection_error_code(
        self, plex_configured: Path, code: str, cause: str, recorded: bool, title: str
    ) -> None:
        from plexmix.services.sync_service import PlexConnectionError

        def fail(settings: Any) -> Any:
            raise PlexConnectionError("Plex not configured. Run 'plexmix config init' first.", code=code)  # type: ignore[arg-type]

        with patch("plexmix.services.sync_service.connect_plex", fail):
            self._run_sync()
        entry = task_store.get("sync")
        assert entry is not None and entry.status == "failed"
        assert entry.message.startswith(title)
        assert "plexmix config" not in entry.message
        assert entry.extra["plex_error_code"] == code
        assert entry.extra["readiness_cause"] == cause
        rec = readiness_store.get("plex")
        if recorded:
            assert rec is not None and rec.level == rd.Level.FAILING and rec.cause == cause
        else:
            assert rec is None

    def test_connect_success_verifies_plex(
        self, plex_configured: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.config.settings import Settings

        engine = MagicMock()
        with patch(
            "plexmix.services.sync_service.connect_plex", lambda s: SimpleNamespace()
        ), patch("plexmix.plex.sync.SyncEngine", return_value=engine), patch(
            "plexmix.services.providers.build_ai_provider", return_value=None
        ):
            self._run_sync()
        engine.incremental_sync.assert_called_once()
        rec = readiness_store.get("plex")
        assert rec is not None and rec.level == rd.Level.READY
        assert rec.fingerprint == rd.current_fingerprint("plex", Settings.load_from_file())
        state = app_state()
        state.check_configuration_status()
        assert state.readiness["plex"]["level"] == "ready"


class TestTaggingRecords:
    @pytest.fixture
    def tagging(self, _isolated: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        write_config(_isolated, "ai:\n  default_provider: openai\n  model: gpt-5-mini\n")
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        make_db(_isolated, tracks=2)
        from plexmix.ui.states.tagging_state import TaggingState

        from plexmix.ui.states.tagging_state import normalize_tag_filters, tag_filter_signature

        state = _harness(TaggingState)
        state.preview_count = 2
        state.genre_filter = state.year_min = state.year_max = ""
        state.artist_filter = state.stale_days = ""
        state.has_no_tags = False
        # A preview of these (blank) filters is current, so the start is allowed (8.2).
        state._preview_signature = tag_filter_signature(normalize_tag_filters())
        return state

    def _fake_generator(self, *, results: Any = None, error: Any = None) -> Any:
        def factory(provider: Any) -> Any:
            gen = MagicMock()
            if error is not None:
                gen.generate_tags_batch.side_effect = error
            else:
                gen.generate_tags_batch.return_value = results
            return gen

        return factory

    def test_success_verifies_the_ai_provider(self, tagging: Any) -> None:
        from plexmix.config.settings import Settings

        results = {
            1: {"tags": ["chill"], "environments": [], "instruments": []},
            2: {"tags": [], "environments": [], "instruments": []},
        }
        with patch("plexmix.services.providers.build_ai_provider", return_value=object()), patch(
            "plexmix.ai.tag_generator.TagGenerator", self._fake_generator(results=results)
        ):
            run(tagging.start_tagging())
        rec = readiness_store.get("ai")
        assert rec is not None and rec.level == rd.Level.READY
        assert rec.fingerprint == rd.current_fingerprint("ai", Settings.load_from_file())

    def test_rejected_credentials_are_invalid_credentials(self, tagging: Any) -> None:
        from plexmix.ai.errors import FatalProviderError, classify_provider_error

        auth = RuntimeError("401 Unauthorized")
        _, _, user_message = classify_provider_error(auth)
        error = FatalProviderError("auth", user_message=user_message, cause=auth)
        with patch("plexmix.services.providers.build_ai_provider", return_value=object()), patch(
            "plexmix.ai.tag_generator.TagGenerator", self._fake_generator(error=error)
        ):
            run(tagging.start_tagging())
        rec = readiness_store.get("ai")
        assert rec is not None
        assert (rec.level, rec.cause) == (rd.Level.FAILING, rd.Cause.INVALID_CREDENTIALS)
        entry = task_store.get("tagging")
        assert entry is not None and entry.status == "failed"

    def test_provider_that_cannot_start_is_failing(self, tagging: Any) -> None:
        with patch("plexmix.services.providers.build_ai_provider", return_value=None):
            run(tagging.start_tagging())
        rec = readiness_store.get("ai")
        assert rec is not None
        assert (rec.level, rec.cause) == (rd.Level.FAILING, rd.Cause.UNKNOWN)
        assert rec.message

    def test_failure_cause_mapping(self) -> None:
        from plexmix.ai.errors import FatalProviderError
        from plexmix.ui.states.tagging_state import tagging_failure_cause

        quota = FatalProviderError("q", user_message="Your quota or billing is exhausted.")
        assert tagging_failure_cause(quota) == ("unknown", "Your quota or billing is exhausted.")
        timeout = FatalProviderError("t", user_message="x", cause=TimeoutError("slow"))
        assert tagging_failure_cause(timeout) == ("timeout", "")


# ── 4.1 scope_confirm ───────────────────────────────────────────────────


@pytest.fixture
def dialog_mocks(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Recording MagicMocks for the Reflex parts scope_confirm builds with.

    tests/ui/conftest.py maps several of them to the ``Mock`` class, which can't
    take a component as a positional child, so they are swapped per test (as
    tests/ui/test_inputs.py does).
    """
    import reflex as rx

    parts = SimpleNamespace(
        root=MagicMock(name="alert_dialog.root"),
        content=MagicMock(name="alert_dialog.content"),
        title=MagicMock(name="alert_dialog.title"),
        description=MagicMock(name="alert_dialog.description"),
        cancel=MagicMock(name="alert_dialog.cancel"),
        action=MagicMock(name="alert_dialog.action"),
        button=MagicMock(name="el.button"),
        span=MagicMock(name="el.span"),
        box=MagicMock(name="box"),
        cond=MagicMock(name="cond"),
        fragment=MagicMock(name="fragment"),
        call_script=MagicMock(name="call_script"),
    )
    for name in ("root", "content", "title", "description", "cancel", "action"):
        monkeypatch.setattr(rx.alert_dialog, name, getattr(parts, name))
    monkeypatch.setattr(rx.el, "button", parts.button)
    monkeypatch.setattr(rx.el, "span", parts.span)
    monkeypatch.setattr(rx, "box", parts.box)
    monkeypatch.setattr(rx, "cond", parts.cond)
    monkeypatch.setattr(rx, "fragment", parts.fragment)
    monkeypatch.setattr(rx, "call_script", parts.call_script)
    return parts


def _buttons(parts: Any) -> dict[str, dict[str, Any]]:
    """Each ``rx.el.button`` call by label: its kwargs."""
    return {call.args[0]: dict(call.kwargs) for call in parts.button.call_args_list}


class TestScopeConfirm:
    def test_structure_follows_the_alert_dialog_confirm_pattern(self, dialog_mocks: Any) -> None:
        from plexmix.ui.components import scope_confirm as sc

        on_open_change, on_confirm, on_secondary = MagicMock(), MagicMock(), MagicMock()
        sc.scope_confirm(
            open_var=True,
            on_open_change=on_open_change,
            title="Tag 59 tracks?",
            lines=["59 tracks will be tagged.", "", "40 existing tag sets will be replaced."],
            privacy_note="Track titles, artists, albums and genres are sent to OpenAI.",
            confirm_label="Tag tracks",
            on_confirm=on_confirm,
            secondary_label="Tag only the 19 untagged tracks",
            on_secondary=on_secondary,
            danger=True,
            id_prefix="tag-confirm",
        )
        root = dialog_mocks.root.call_args
        assert root.kwargs == {"open": True, "on_open_change": on_open_change}
        content = dialog_mocks.content.call_args.kwargs
        assert content["aria_modal"] == "true"
        assert content["id"] == "tag-confirm"
        dialog_mocks.call_script.assert_called_with("window.pmFocus&&pmFocus.restore()")
        assert content["on_close_auto_focus"] is dialog_mocks.call_script.return_value
        dialog_mocks.title.assert_called_once_with("Tag 59 tracks?")

        buttons = _buttons(dialog_mocks)
        assert buttons["Cancel"]["class_name"] == "btn btn-3 btn-soft"
        assert buttons["Tag only the 19 untagged tracks"]["class_name"] == "btn btn-3 btn-soft"
        assert buttons["Tag only the 19 untagged tracks"]["on_click"] is on_secondary
        assert buttons["Tag tracks"]["class_name"] == "btn btn-3 btn-danger"
        assert buttons["Tag tracks"]["on_click"] is on_confirm
        assert buttons["Tag tracks"]["id"] == "tag-confirm-confirm"
        assert all(b["type"] == "button" for b in buttons.values())
        # Only the safe button is an AlertDialog part; the confirm never is an Action.
        dialog_mocks.cancel.assert_called_once()
        assert dialog_mocks.cancel.call_args.args[0] is dialog_mocks.button.return_value
        dialog_mocks.action.assert_not_called()
        # The empty line renders nothing; the others are block lines in the description.
        texts = [c.args[0] for c in dialog_mocks.span.call_args_list]
        assert texts == [
            "59 tracks will be tagged.",
            "40 existing tag sets will be replaced.",
            "Track titles, artists, albums and genres are sent to OpenAI.",
        ]

    def test_primary_confirm_and_no_secondary(self, dialog_mocks: Any) -> None:
        from plexmix.ui.components import scope_confirm as sc

        sc.scope_confirm(True, MagicMock(), "Enrich?", ["x"], None, "Enrich tracks", MagicMock())
        buttons = _buttons(dialog_mocks)
        assert set(buttons) == {"Cancel", "Enrich tracks"}
        assert buttons["Enrich tracks"]["class_name"] == "btn btn-3 btn-primary"
        assert "id" not in dialog_mocks.content.call_args.kwargs

    def test_builds_under_the_mocked_reflex(self, dialog_mocks: Any) -> None:
        from plexmix.ui.components import scope_confirm as sc

        dialog = sc.scope_confirm(
            open_var=True,
            on_open_change=MagicMock(),
            title="Tag 59 tracks?",
            lines=["59 tracks will be tagged.", "", sc.scope_provider_line("OpenAI", "gpt-5-mini")],
            privacy_note="Track titles, artists, albums and genres are sent to OpenAI.",
            confirm_label="Tag tracks",
            on_confirm=MagicMock(),
            secondary_label="Tag only the 19 untagged tracks",
            on_secondary=MagicMock(),
            danger=True,
            id_prefix="tag-confirm",
        )
        assert dialog is not None

    def test_embed_all_dialog_is_bound_to_library_state(
        self, dialog_mocks: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.components import scope_confirm as sc
        from plexmix.ui.states.library_state import LibraryState

        # Under the mock a state var is its plain default; give it every scope key.
        keys = ("title", "count_line", "provider_label", "model", "duration_line")
        scope = {k: f"<{k}>" for k in keys}
        scope.update(privacy_line="<privacy>", confirm_label="Generate embeddings", danger="false")
        monkeypatch.setattr(LibraryState, "embed_all_scope", scope)
        sc.embed_all_confirm_dialog()
        root = dialog_mocks.root.call_args.kwargs
        assert root["open"] is LibraryState.embed_all_open
        assert root["on_open_change"] is LibraryState.set_embed_all_open
        buttons = _buttons(dialog_mocks)
        assert buttons["Generate embeddings"]["on_click"] is LibraryState.confirm_embed_all
        assert buttons["Generate embeddings"]["class_name"] == "btn btn-3 btn-primary"
        assert dialog_mocks.content.call_args.kwargs["id"] == "embed-all-confirm"

    def test_confirm_class_follows_danger(self) -> None:
        from plexmix.ui.components.scope_confirm import _confirm_class

        assert _confirm_class(False) == "btn btn-3 btn-primary"
        assert _confirm_class(True) == "btn btn-3 btn-danger"

    def test_restores_focus_on_close(self) -> None:
        from plexmix.ui.components.scope_confirm import RESTORE_FOCUS_JS

        assert RESTORE_FOCUS_JS == "window.pmFocus&&pmFocus.restore()"

    def test_no_colour_literals(self) -> None:
        src = Path(__file__).resolve().parents[2] / "src/plexmix/ui/components/scope_confirm.py"
        assert re.search(r"color:|#[0-9a-fA-F]{3,6}", src.read_text()) is None


# ── 9.2 Library: embed every track without an embedding ─────────────────


@pytest.fixture
def openai_embeddings(_isolated: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    write_config(_isolated, OPENAI_EMBED_YAML)
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
    return _isolated


def _toasts(lib: Any) -> MagicMock:
    toast = MagicMock()
    toast.info.side_effect = lambda msg: ("info", msg)
    toast.error.side_effect = lambda msg: ("error", msg)
    toast.warning.side_effect = lambda msg: ("warning", msg)
    toast.success.side_effect = lambda msg: ("success", msg)
    return toast


class TestEmbedAll:
    def test_no_selection_opens_the_confirmation_with_the_scope(
        self, openai_embeddings: Path
    ) -> None:
        from plexmix.ui.states import library_state as ls

        make_db(openai_embeddings, tracks=3, embedded=1)
        state = library_state()
        with patch.object(ls.rx, "toast", _toasts(ls)):
            events = run(state.generate_embeddings())
        assert events == []
        assert state.embed_all_open is True
        assert state.embed_all_count == 2
        scope = state.embed_all_scope
        assert scope["title"] == "Generate embeddings for 2 tracks?"
        assert scope["provider_label"] == "OpenAI"
        assert scope["model"] == "text-embedding-3-small"
        assert scope["provider_line"] == "OpenAI · text-embedding-3-small"
        assert "sent to OpenAI" in scope["privacy_line"]
        assert scope["confirm_label"] == "Generate embeddings"
        assert scope["danger"] == "false"
        assert scope["count"] == "2"
        assert all(isinstance(v, str) for v in scope.values())
        assert state.embed_all_checking is False
        assert state.is_embedding is False  # nothing starts before the confirmation

    def test_request_embed_all_is_the_same_flow(self, openai_embeddings: Path) -> None:
        from plexmix.ui.states import library_state as ls

        make_db(openai_embeddings, tracks=4, embedded=0)
        state = library_state()
        state.selected_tracks = ["1"]  # the explicit request ignores the selection
        with patch.object(ls.rx, "toast", _toasts(ls)):
            run(state.request_embed_all())
        assert state.embed_all_open is True and state.embed_all_count == 4

    def test_everything_embedded_is_reported(self, openai_embeddings: Path) -> None:
        from plexmix.ui.states import library_state as ls

        ids = make_db(openai_embeddings, tracks=3, embedded=3)
        write_index_metadata(openai_embeddings, 1536, ids)
        state = library_state()
        with patch.object(ls.rx, "toast", _toasts(ls)):
            events = run(state.generate_embeddings())
        assert events == [("info", "All tracks already have embeddings.")]
        assert "all tracks already have embeddings" in state.embed_all_notice.lower()
        assert state.embed_all_open is False
        assert state.embed_all_notice_action_href == ""

    def test_missing_index_file_offers_to_rebuild_it(self, openai_embeddings: Path) -> None:
        """Every track has an embedding but the FAISS file is gone: rebuild the index."""
        from plexmix.ui.states import library_state as ls

        make_db(openai_embeddings, tracks=3, embedded=3)
        state = library_state()
        with patch.object(ls.rx, "toast", _toasts(ls)):
            events = run(state.request_embed_all())
        assert events == []
        assert state.embed_all_open is True and state.embed_all_count == 3
        scope = state.embed_all_scope
        assert scope["title"] == "Rebuild the search index?"
        assert "3 tracks already have" in scope["count_line"]
        assert scope["privacy_line"].startswith("Runs on this machine")
        assert scope["confirm_label"] == "Build search index"

    def test_empty_index_metadata_offers_to_rebuild_it(self, openai_embeddings: Path) -> None:
        from plexmix.ui.states import library_state as ls

        make_db(openai_embeddings, tracks=2, embedded=2)
        write_index_metadata(openai_embeddings, 1536, [])
        state = library_state()
        with patch.object(ls.rx, "toast", _toasts(ls)):
            run(state.request_embed_all())
        assert state.embed_all_open is True
        assert state.embed_all_scope["title"] == "Rebuild the search index?"

    def test_after_a_provider_switch_old_embeddings_do_not_count(
        self, openai_embeddings: Path
    ) -> None:
        """Embedded only by the previous provider: the index needs every track again."""
        from plexmix.ui.states import library_state as ls

        make_db(openai_embeddings, tracks=3, embedded=3, model="gemini")
        state = library_state()
        with patch.object(ls.rx, "toast", _toasts(ls)):
            run(state.request_embed_all())
        assert state.embed_all_open is True and state.embed_all_count == 3

    def test_same_provider_other_index_size_points_to_the_rebuild(
        self, openai_embeddings: Path
    ) -> None:
        from plexmix.ui.states import library_state as ls

        ids = make_db(openai_embeddings, tracks=2, embedded=2)
        write_index_metadata(openai_embeddings, 1024, ids)
        state = library_state()
        with patch.object(ls.rx, "toast", _toasts(ls)):
            events = run(state.request_embed_all())
        assert len(events) == 1 and events[0][0] == "warning"
        assert "doesn't match your embedding provider" in events[0][1]
        assert "1,024" in events[0][1] and "1,536" in events[0][1]
        assert "already have embeddings" not in state.embed_all_notice
        assert state.embed_all_open is False
        # Not a dead end: the notice links to both ways out.
        assert state.embed_all_notice_action_label == "Rebuild embeddings"
        assert state.embed_all_notice_action_href == "/doctor"
        assert state.embed_all_notice_secondary_label == "Switch back"
        assert state.embed_all_notice_secondary_href == "/settings?section=embedding"
        state.dismiss_embed_all_notice()
        assert state.embed_all_notice_action_href == ""
        assert state.embed_all_notice_secondary_href == ""

    def test_nothing_synced_says_so(self, openai_embeddings: Path) -> None:
        from plexmix.ui.states import library_state as ls

        state = library_state()
        with patch.object(ls.rx, "toast", _toasts(ls)):
            events = run(state.generate_embeddings())
        assert events == [("info", ls.NO_TRACKS_TO_EMBED_MESSAGE)]
        assert state.embed_all_open is False

    def test_blocked_provider_names_the_real_cause(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.states import library_state as ls

        write_config(_isolated, LOCAL_EMBED_YAML)
        make_db(_isolated, tracks=2)
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        monkeypatch.setattr("importlib.util.find_spec", lambda name, *a: None)
        state = library_state()
        with patch.object(ls.rx, "toast", _toasts(ls)):
            events = run(state.request_embed_all())
        assert len(events) == 1 and events[0][0] == "error"
        assert "Local models aren't installed" in events[0][1]
        assert "API key" not in events[0][1]
        assert state.embed_all_open is False
        assert state.embed_all_notice_action_label == "Switch provider"
        assert state.embed_all_notice_action_href == "/settings?section=embedding"

    def test_a_running_job_opens_nothing(self, openai_embeddings: Path) -> None:
        from plexmix.ui.states import library_state as ls

        make_db(openai_embeddings, tracks=2)
        assert task_store.start("embedding") is not None
        state = library_state()
        with patch.object(ls.rx, "toast", _toasts(ls)):
            events = run(state.request_embed_all())
        assert events == [("info", ls.EMBED_ALL_RUNNING_MESSAGE)]
        assert state.embed_all_open is False

    def test_dialog_open_change_is_idempotent(self) -> None:
        state = library_state()
        state.embed_all_open, state.embed_all_count = True, 5
        state.embed_all_scope = {"title": "Generate embeddings for 5 tracks?"}
        state.set_embed_all_open(True)  # no-op
        assert state.embed_all_open is True
        state.set_embed_all_open(False)
        state.set_embed_all_open(False)
        assert (state.embed_all_open, state.embed_all_count) == (False, 0)
        assert state.embed_all_scope["title"]  # kept for the closing animation

    def test_cancel_starts_nothing(self) -> None:
        state = library_state()
        state.embed_all_open, state.embed_all_count = True, 5
        state.cancel_embed_all()
        assert state.embed_all_open is False
        assert state.confirm_embed_all() is None
        assert task_store.get("embedding") is None

    def test_confirm_closes_and_starts_once(self) -> None:
        from plexmix.ui.states.library_state import LibraryState

        state = library_state()
        state.embed_all_open, state.embed_all_count = True, 5
        assert state.confirm_embed_all() == LibraryState.embed_all_missing
        assert state.embed_all_open is False
        assert state.confirm_embed_all() is None  # double click

    def _fake_generator(self, dim: int = 2) -> Any:
        gen = MagicMock()
        gen.provider_name = "openai"  # EmbeddingGenerator stores the provider id
        gen.get_dimension.return_value = dim
        gen.generate_batch_embeddings.side_effect = lambda texts, batch_size=0: [
            [0.5] * dim for _ in texts
        ]
        return gen

    def test_embed_all_missing_embeds_every_missing_track(self, openai_embeddings: Path) -> None:
        from plexmix.config.settings import Settings
        from plexmix.database.sqlite_manager import SQLiteManager

        make_db(openai_embeddings, tracks=4, embedded=1)
        gen = self._fake_generator()
        rebuilt = MagicMock(return_value=4)
        state = library_state()
        with patch(
            "plexmix.services.providers.build_embedding_generator_with_reason",
            return_value=(gen, None),
        ), patch("plexmix.services.sync_service.build_vector_index") as build_index, patch(
            "plexmix.services.tagging_service.rebuild_vector_index", rebuilt
        ):
            run(state.embed_all_missing())
        assert state.is_embedding is True  # the poller reports the outcome
        entry = task_store.get("embedding")
        assert entry is not None and entry.status == "completed"
        with SQLiteManager(str(openai_embeddings / "plexmix.db")) as db:
            assert db.count_tracks_without_embeddings() == 0
        texts = [t for call in gen.generate_batch_embeddings.call_args_list for t in call.args[0]]
        assert len(texts) == 3
        build_index.assert_called_once()
        rebuilt.assert_called_once()
        assert rebuilt.call_args.args[3] == "openai"
        rec = readiness_store.get("embeddings")
        assert rec is not None and rec.level == rd.Level.READY
        assert rec.fingerprint == rd.current_fingerprint("embeddings", Settings.load_from_file())

    def test_nothing_missing_rebuilds_the_index_from_stored_embeddings(
        self, openai_embeddings: Path
    ) -> None:
        """The confirmed "Rebuild the search index": no embedding is generated,
        the index is rebuilt from the current provider's stored vectors."""
        make_db(openai_embeddings, tracks=3, embedded=3)
        gen = self._fake_generator()
        rebuilt = MagicMock(return_value=3)
        state = library_state()
        with patch(
            "plexmix.services.providers.build_embedding_generator_with_reason",
            return_value=(gen, None),
        ), patch("plexmix.services.sync_service.build_vector_index") as build_index, patch(
            "plexmix.services.tagging_service.rebuild_vector_index", rebuilt
        ):
            run(state.embed_all_missing())
        gen.generate_batch_embeddings.assert_not_called()
        build_index.assert_called_once()
        rebuilt.assert_called_once()
        assert rebuilt.call_args.args[3] == "openai"
        entry = task_store.get("embedding")
        assert entry is not None and entry.status == "completed"
        assert entry.message == "Rebuilt the search index from 3 stored embeddings."

    def test_a_provider_that_cannot_start_fails_with_its_cause(self, _isolated: Path) -> None:
        from plexmix.services.providers import ProviderFailure

        write_config(_isolated, LOCAL_EMBED_YAML)
        make_db(_isolated, tracks=2)
        failure = ProviderFailure(
            rd.Cause.MISSING_DEPENDENCY, "local", package="sentence-transformers"
        )
        state = library_state()
        with patch(
            "plexmix.services.providers.build_embedding_generator_with_reason",
            return_value=(None, failure),
        ):
            run(state.embed_all_missing())
        entry = task_store.get("embedding")
        assert entry is not None and entry.status == "failed"
        assert entry.message == "Local models aren't installed"
        rec = readiness_store.get("embeddings")
        assert rec is not None and rec.cause == rd.Cause.MISSING_DEPENDENCY

    def test_a_failing_provider_records_the_cause_without_the_key(
        self, openai_embeddings: Path
    ) -> None:
        make_db(openai_embeddings, tracks=2)
        gen = self._fake_generator()
        gen.generate_batch_embeddings.side_effect = RuntimeError(
            f"Error code: 401 invalid api key {SENTINEL_OPENAI}"
        )
        state = library_state()
        with patch(
            "plexmix.services.providers.build_embedding_generator_with_reason",
            return_value=(gen, None),
        ):
            run(state.embed_all_missing())
        entry = task_store.get("embedding")
        assert entry is not None and entry.status == "failed"
        assert SENTINEL_OPENAI not in entry.message
        rec = readiness_store.get("embeddings")
        assert rec is not None and rec.cause == rd.Cause.INVALID_CREDENTIALS

    def test_selection_still_embeds_only_the_selection(self, openai_embeddings: Path) -> None:
        ids = make_db(openai_embeddings, tracks=3)
        gen = self._fake_generator()
        state = library_state()
        state.selected_tracks = [str(ids[1])]
        with patch(
            "plexmix.services.providers.build_embedding_generator_with_reason",
            return_value=(gen, None),
        ), patch("plexmix.services.sync_service.build_vector_index"), patch(
            "plexmix.services.tagging_service.rebuild_vector_index"
        ):
            run(state.generate_embeddings())
        assert state.embed_all_open is False
        texts = [t for call in gen.generate_batch_embeddings.call_args_list for t in call.args[0]]
        assert len(texts) == 1
        entry = task_store.get("embedding")
        assert entry is not None and entry.status == "completed"
