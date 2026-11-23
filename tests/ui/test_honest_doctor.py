"""webui-honest-status-and-first-run, group 6: Doctor health (tasks 6.1–6.4).

Covers the scan state (no health before a scan, one scan at a time, a single
"No library yet" state), rows and verdict from ``readiness.doctor_rows``/``verdict``,
fixes behind a scope confirmation (``start_fix`` → ``confirm_fix``), real causes
for provider failures, fix failures kept on their row, and progress labels.

fix-cli-secrets-and-data-loss, task 3.10 (the last section): "Rebuild embeddings"
replaces every embedding in place. Nothing is cleared first, the provider is checked
before any change, and a refused, failed or cancelled run leaves every track with
its new or its previous embedding.

Reflex is mocked (tests/ui/conftest.py). Nothing here touches the network, the
real keyring or ~/.plexmix: every test runs on a scratch ``PLEXMIX_DATA_DIR`` with
its own config.yaml, the keyring lookup is patched out, providers are fakes, and
the readiness and task stores are cleared around each test.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import pickle
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from plexmix.services import readiness as rd
from plexmix.services.readiness import readiness_store
from plexmix.ui.job_manager import task_store

FIX_JOB = "doctor_fix"

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

LOCAL_EMBED_YAML = "embedding:\n  default_provider: local\n  model: all-MiniLM-L6-v2\n"
OPENAI_EMBED_YAML = "embedding:\n  default_provider: openai\n  model: text-embedding-3-small\n"
OPENAI_AI_YAML = "ai:\n  default_provider: openai\n  model: gpt-5-mini\n"


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
    task_store.clear(FIX_JOB)
    yield data
    readiness_store.clear()
    task_store.clear(FIX_JOB)


@pytest.fixture
def toast(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """A recording ``rx.toast`` (the mocked Reflex module is shared by every test)."""
    import reflex as rx

    mock = MagicMock(name="toast")
    monkeypatch.setattr(rx, "toast", mock)
    return mock


# ── Helpers ──────────────────────────────────────────────────────────────


def write_config(data: Path, body: str) -> None:
    (data / "config.yaml").write_text(body or "{}\n")


def make_db(
    data: Path,
    *,
    tracks: int = 3,
    tagged: int = 0,
    embedded: int = 0,
    orphans: int = 0,
) -> list[int]:
    """A real SQLite library in the scratch data dir; returns the track ids."""
    from plexmix.database.models import Album, Artist, Embedding, SyncHistory, Track
    from plexmix.database.sqlite_manager import SQLiteManager

    db_path = data / "plexmix.db"
    with SQLiteManager(str(db_path)) as db:
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
                Embedding(
                    track_id=tid, embedding_model="openai", embedding_dim=2, vector=[0.1, 0.2]
                )
            )
        db.insert_sync_record(
            SyncHistory(tracks_added=tracks, tracks_updated=0, tracks_removed=0, status="success")
        )
    if orphans:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            for n in range(orphans):
                conn.execute(
                    "INSERT INTO embeddings (track_id, embedding_model, embedding_dim, vector) "
                    "VALUES (?, 'openai', 2, '[0.1, 0.2]')",
                    (90_000 + n,),
                )
            conn.commit()
        finally:
            conn.close()
    return ids


def doctor() -> Any:
    from plexmix.ui.states.doctor_state import DoctorState

    return DoctorState()


def harness() -> Any:
    """A DoctorState plus what a background handler needs from rx.State."""
    from plexmix.ui.states.doctor_state import DoctorState

    class _Harness(DoctorState):  # type: ignore[misc, valid-type]
        async def __aenter__(self) -> "_Harness":
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        def access_allowed(self) -> bool:
            return True

    return _Harness()


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def entry(level: str, **fields: str) -> dict[str, str]:
    """A readiness snapshot entry (the keys doctor_rows and start_fix read)."""
    base = {
        "level": level,
        "cause": "",
        "state": rd.LEVEL_WORDS[rd.Level(level)],
        "tone": rd.LEVEL_TONES[rd.Level(level)],
        "label": "",
        "provider": "",
        "model": "",
        "title": "",
        "body": "",
        "action_label": "",
        "action_href": "",
        "fix_label": "",
        "blocking": "false",
        "checked_ago": "",
    }
    base.update(fields)
    return base


def readiness(**overrides: dict[str, str]) -> dict[str, dict[str, str]]:
    """Everything set up and verified, with MusicBrainz enabled; override per component."""
    snap = {
        "plex": entry("ready", label="Plex", model="Music", checked_ago="2 min ago"),
        "ai": entry("ready", label="OpenAI", provider="openai", model="gpt-5-mini"),
        "embeddings": entry("ready", label="Local", provider="local", model="all-MiniLM-L6-v2"),
        "library": entry("ready", label="Library"),
        "index": entry("ready", label="Search index"),
        "audio": entry("ready", label="Audio analysis"),
        "musicbrainz": entry("unchecked", label="MusicBrainz"),
    }
    snap.update(overrides)
    return snap


STATS = {
    "doctor_total_tracks": 5943,
    "doctor_tracks_with_embeddings": 5943,
    "doctor_orphaned_embeddings": 0,
    "doctor_untagged_tracks": 0,
    "doctor_tracks_needing_embeddings": 0,
    "doctor_audio_features_count": 5943,
    "doctor_tracks_without_audio": 0,
    "doctor_musicbrainz_enriched": 5943,
    "doctor_tracks_without_musicbrainz": 0,
}


def loaded(state: Optional[Any] = None, snapshot: Optional[dict] = None, **counts: int) -> Any:
    """A Doctor state after a finished scan with ``counts`` over STATS."""
    state = state if state is not None else doctor()
    for key, value in {**STATS, **counts}.items():
        setattr(state, key, value)
    state.stats_loaded = True
    state.scan_state = "done"
    state.readiness = snapshot if snapshot is not None else readiness()
    return state


def all_words(state: Any) -> list[str]:
    return [row["state_word"] for row in state.rows]


# ── 6.1 Scan state, rows and verdict ─────────────────────────────────────


class TestBeforeTheScan:
    def test_default_state_renders_checking_never_healthy(self) -> None:
        state = doctor()
        assert state.scan_state == "idle"
        verdict = state.verdict
        assert verdict["state"] == "checking"
        assert verdict["title"] == "Checking your library…"
        library = [r for r in state.rows if r["group"] == "library"]
        assert [r["id"] for r in library] == [
            "library",
            "embeddings",
            "orphans",
            "tags",
            "audio",
            "musicbrainz",
        ]
        assert {r["state"] for r in library} == {"checking"}
        assert "Healthy" not in all_words(state)
        assert all(r["count"] == "" for r in library)  # no zeros from defaults
        assert state.row_map["tags"]["state_word"] == "Checking…"
        assert state.untagged_tracks_message == "Checking…"

    def test_connections_before_the_scan_are_not_healthy(self) -> None:
        state = doctor()
        state.readiness = readiness(
            plex=entry("unchecked", label="Plex"),
            ai=entry("unchecked", label="OpenAI", provider="openai", model="gpt-5-mini"),
            embeddings=entry("unchecked", label="Local", provider="local"),
        )
        words = all_words(state)
        assert "Healthy" not in words
        assert state.row_map["conn_ai"]["state_word"] == "Set up · not checked"
        assert state.row_map["conn_ai"]["title"] == "AI provider: OpenAI"
        assert state.verdict["state"] == "checking"

    def test_every_row_id_is_present_for_the_page(self) -> None:
        from plexmix.ui.states.doctor_state import ROW_EXTRA_KEYS, ROW_IDS, ROW_KEYS

        row_map = doctor().row_map
        assert set(row_map) == set(ROW_IDS)
        for row in row_map.values():
            assert set(row) >= set(ROW_KEYS) | set(ROW_EXTRA_KEYS)
        # No readiness yet: the connection rows are hidden, not blank "healthy" rows.
        assert row_map["conn_plex"]["state"] == "hidden"


class TestRunHealthCheck:
    def test_missing_database_gives_no_database(self, _isolated: Path) -> None:
        state = harness()
        run(state.run_health_check())
        assert state.scan_state == "no_database"
        assert state.stats_loaded is False
        assert state.library_collapsed is True
        assert state.verdict["state"] == "no_database"
        assert state.verdict["title"] == "No library yet"
        library = state.row_map["library"]
        assert library["title"] == "No library yet"
        assert library["action_label"] == "Connect Plex"
        assert library["action_href"] == "/settings?section=plex"
        assert library["secondary_label"] == "Sync library"
        assert "Healthy" not in all_words(state)
        assert [r["id"] for r in state.rows if r["group"] == "library"] == ["library"]
        assert "All" not in state.untagged_tracks_message
        assert not (_isolated / "plexmix.db").exists()  # the scan never creates the DB
        # The scan refreshed the shared readiness (fresh profile: nothing set up).
        assert state.readiness["plex"]["level"] == "not_set_up"
        assert state.row_map["conn_plex"]["state_word"] == "Not set up"

    def test_populated_library(self, _isolated: Path) -> None:
        make_db(_isolated, tracks=3, tagged=2, embedded=1, orphans=2)
        state = harness()
        run(state.run_health_check())
        assert state.scan_state == "done"
        assert state.stats_loaded is True
        assert state.doctor_total_tracks == 3
        assert state.doctor_untagged_tracks == 1
        assert state.doctor_tracks_needing_embeddings == 2
        assert state.doctor_orphaned_embeddings == 2
        assert state.row_map["tags"]["summary"] == "1 track without tags"
        assert state.row_map["embeddings"]["fix"] == "embeddings_incremental"
        assert state.row_map["orphans"]["fix"] == "cleanup"
        assert state.verdict["state"] == "attention"
        assert state.is_page_loading is False

    def test_a_scan_in_progress_is_not_started_twice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scan = MagicMock()
        monkeypatch.setattr("plexmix.ui.states.doctor_state.scan_library", scan)
        state = harness()
        state.scan_state = "checking"
        state._scan_started = time.time()
        run(state.run_health_check())
        scan.assert_not_called()

    def test_a_lost_scan_does_not_block_forever(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plexmix.ui.states.doctor_state import ScanResult

        scan = MagicMock(return_value=ScanResult(None, None, None, None, 1.0))
        monkeypatch.setattr("plexmix.ui.states.doctor_state.scan_library", scan)
        state = harness()
        state.scan_state = "checking"
        state._scan_started = time.time() - 10_000
        run(state.run_health_check())
        scan.assert_called_once()
        assert state.scan_state == "no_database"

    def test_load_and_mount_triggers_run_one_scan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plexmix.ui.states.doctor_state import ScanResult

        calls: list[int] = []

        def slow_scan() -> ScanResult:
            calls.append(1)
            time.sleep(0.05)
            return ScanResult(None, None, None, None, 1.0)

        monkeypatch.setattr("plexmix.ui.states.doctor_state.scan_library", slow_scan)
        state = harness()

        async def both() -> None:
            await asyncio.gather(state.run_health_check(), state.run_health_check())

        run(both())
        assert len(calls) == 1
        assert state.scan_state == "no_database"

    def test_scan_error_is_plain_and_never_healthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plexmix.ui.states.doctor_state import SCAN_ERROR_MESSAGE, DoctorState

        def broken() -> None:
            raise sqlite3.DatabaseError("file is not a database /secret/path")

        monkeypatch.setattr(DoctorState, "_compute_health_stats", staticmethod(broken))
        state = loaded(harness())  # earlier results must not mask the failure
        run(state.run_health_check())
        assert state.scan_state == "error"
        assert state.stats_loaded is False
        assert state.scan_summary == SCAN_ERROR_MESSAGE
        assert state.library_collapsed is True
        assert state.verdict["state"] == "attention"
        assert "secret" not in state.scan_summary
        assert "Healthy" not in all_words(state)

    def test_a_scan_clears_fix_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plexmix.ui.states.doctor_state import ScanResult

        monkeypatch.setattr(
            "plexmix.ui.states.doctor_state.scan_library",
            lambda: ScanResult(None, None, None, None, 1.0),
        )
        state = harness()
        state.fix_errors = {"tags": {"title": "No API key for OpenAI"}}
        run(state.run_health_check())
        assert state.fix_errors == {}

    def test_rescan_keeps_showing_the_last_real_data(self) -> None:
        state = loaded(doctor_untagged_tracks=4)
        state.scan_state = "checking"
        assert state.row_map["tags"]["summary"] == "4 tracks without tags"
        assert state.is_checking is True


class TestVerdictMatchesRows:
    def test_recommended_fixes_never_say_all_healthy(self) -> None:
        state = loaded(doctor_untagged_tracks=1, doctor_tracks_without_musicbrainz=5807)
        verdict = state.verdict
        assert verdict["title"] != "All systems healthy"
        assert verdict["state"] == "fixes_available"
        assert verdict["title"] == "Search is ready"
        assert "1 recommended fix" in verdict["sub"]
        assert "1 optional fix" in verdict["sub"]
        assert state.scan_summary == verdict["sub"]
        tags = state.row_map["tags"]
        assert tags["state"] == "attention"
        assert tags["summary"] == "1 track without tags"  # singular, never "1 tracks"
        assert (tags["lead"], tags["num"], tags["tail"]) == ("", "1", " track without tags")
        musicbrainz = state.row_map["musicbrainz"]
        assert musicbrainz["summary"] == "5,807 tracks without MusicBrainz data"
        assert musicbrainz["num"] == "5,807"
        assert musicbrainz["fix_label"] == "Enrich"

    def test_required_gap_names_the_embeddings(self) -> None:
        state = loaded(doctor_tracks_needing_embeddings=1204)
        verdict = state.verdict
        assert verdict["state"] == "attention"
        assert verdict["title"] == "Attention needed"
        assert "1,204 tracks without embeddings" in verdict["sub"]
        row = state.row_map["embeddings"]
        assert row["badge"] == "badge badge-red"
        assert row["fix"] == "embeddings_incremental"

    def test_musicbrainz_off_is_off_and_does_not_count(self) -> None:
        state = loaded(
            snapshot=readiness(musicbrainz=entry("off", label="MusicBrainz")),
            doctor_tracks_without_musicbrainz=5807,
        )
        row = state.row_map["musicbrainz"]
        assert row["state"] == "off"
        assert row["state_word"] == "Off"
        assert row["fix"] == ""  # no fix button
        assert row["action_label"] == "Turn on"
        assert row["action_href"] == "/settings?section=musicbrainz"
        assert state.verdict["state"] == "healthy"
        # Connections not checked yet: still ready, never "All systems healthy".
        state.readiness = readiness(
            musicbrainz=entry("off", label="MusicBrainz"),
            plex=entry("unchecked", label="Plex"),
        )
        assert state.verdict["state"] == "ready"
        assert state.verdict["title"] == "Search is ready"

    def test_audio_without_the_package_is_not_installed(self) -> None:
        state = loaded(
            snapshot=readiness(audio=entry("not_installed", label="Audio analysis")),
            doctor_tracks_without_audio=200,
        )
        row = state.row_map["audio"]
        assert row["state_word"] == "Not installed"
        assert row["fix"] == ""  # no Analyze button
        assert "plexmix[audio]" in row["summary"]
        assert state.verdict["state"] == "healthy"

    def test_connection_row_shows_the_cause_and_fix(self) -> None:
        plex = entry(
            "failing",
            label="Plex",
            model="Music",
            cause="unreachable",
            title="Can't reach your Plex server at http://plex.local:32400",
            body="Check that Plex is running and that the server address in Settings is right.",
            action_label="Fix",
            action_href="/settings?section=plex",
            fix_label="Fix connection",
        )
        state = loaded(snapshot=readiness(plex=plex))
        row = state.row_map["conn_plex"]
        assert row["state"] == "attention"
        assert row["state_word"] == "Failing"
        assert row["summary"].startswith("Can't reach your Plex server")
        assert row["action_label"] == "Fix"
        assert row["action_href"] == "/settings?section=plex"
        assert row["check_label"] == "Check"
        assert row["badge"] == "badge badge-red"
        assert state.verdict["state"] == "attention"

    def test_connection_rows_use_display_names(self) -> None:
        state = loaded()
        assert state.row_map["conn_ai"]["title"] == "AI provider: OpenAI"
        assert state.row_map["conn_ai"]["model"] == "gpt-5-mini"
        assert state.row_map["conn_plex"]["checked_ago"] == "2 min ago"
        assert not any("Openai" in v for row in state.rows for v in row.values())

    def test_why_it_matters_lines(self) -> None:
        row_map = loaded().row_map
        for row_id in ("embeddings", "orphans", "tags", "audio", "musicbrainz"):
            assert row_map[row_id]["why"], row_id
        assert "safe" in row_map["orphans"]["why"]
        assert "no longer in your library" in row_map["orphans"]["why"]


class TestLabels:
    def test_labels_are_pluralised_and_grouped(self) -> None:
        state = doctor()
        state.doctor_tracks_without_musicbrainz = 5807
        assert state.missing_musicbrainz_label == "5,807 tracks need MusicBrainz enrichment"
        state.doctor_tracks_without_audio = 1
        assert state.missing_audio_label == "1 track needs audio analysis"
        state.doctor_orphaned_embeddings = 1204
        assert state.orphaned_embeddings_label == "1,204 orphaned embeddings"

    def test_untagged_message(self) -> None:
        state = loaded(doctor_untagged_tracks=1)
        assert state.untagged_tracks_message == "1 track doesn't have AI-generated tags."
        state.scan_state = "no_database"
        state.stats_loaded = False
        assert state.untagged_tracks_message == ""

    @pytest.mark.parametrize(
        "summary,count,expected",
        [
            (
                "1,204 tracks without embeddings",
                "1,204",
                ("", "1,204", " tracks without embeddings"),
            ),
            ("All 5,943 tracks have tags", "5,943", ("All ", "5,943", " tracks have tags")),
            ("The 1 track has tags", "1", ("The ", "1", " track has tags")),
            ("No leftover search entries", "0", ("No leftover search entries", "", "")),
            ("11 tracks", "1", ("11 tracks", "", "")),
            ("", "3", ("", "", "")),
        ],
    )
    def test_split_count(self, summary: str, count: str, expected: tuple[str, str, str]) -> None:
        from plexmix.ui.states.doctor_state import split_count

        assert split_count(summary, count) == expected


# ── 6.3 Fixes disclose their scope before starting ───────────────────────


class TestStartFix:
    def test_musicbrainz_scope_and_nothing_starts(
        self, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        start = MagicMock(wraps=task_store.start)
        monkeypatch.setattr(task_store, "start", start)
        state = loaded(doctor_tracks_without_musicbrainz=5807)
        assert state.start_fix("musicbrainz") is None
        assert state.fix_confirm_open is True
        assert state.pending_fix == "musicbrainz"
        scope = state.fix_scope
        assert "5,807 tracks" in scope["count_line"]
        assert "about one lookup per second" in scope["duration_line"]
        assert "up to about 1 h 37 min" in scope["duration_line"]
        assert "cancel" in scope["duration_line"]
        assert scope["provider_label"] == "MusicBrainz"
        assert scope["danger"] == "false"
        start.assert_not_called()
        assert task_store.get(FIX_JOB) is None

    def test_musicbrainz_rate_comes_from_settings(self) -> None:
        state = loaded(doctor_tracks_without_musicbrainz=100)
        state._mb_rate_s = 2.0
        state.start_fix("musicbrainz")
        assert "about one lookup every 2 seconds" in state.fix_scope["duration_line"]

    def test_confirming_starts_the_existing_handler(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        from plexmix.ui.states.doctor_state import DoctorState

        make_db(_isolated, tracks=3)
        start = MagicMock(wraps=task_store.start)
        monkeypatch.setattr(task_store, "start", start)
        enrich = MagicMock(return_value=(2, 1, 0))
        monkeypatch.setattr("plexmix.services.musicbrainz_service.enrich_tracks", enrich)

        state = loaded(harness(), doctor_tracks_without_musicbrainz=3)
        state.start_fix("musicbrainz")
        handler = state.confirm_fix()
        assert handler is DoctorState.enrich_missing_musicbrainz
        assert state.fix_confirm_open is False
        assert state.pending_fix == ""
        start.assert_not_called()  # confirming only dispatches the handler

        run(handler(state))
        start.assert_called_once()
        enrich.assert_called_once()
        done = task_store.get(FIX_JOB)
        assert done is not None and done.status == "completed"
        assert done.extra["result_msg"].startswith("Enriched 2 tracks from MusicBrainz")
        events = state.poll_task_progress()
        assert DoctorState.run_health_check in events  # the counts changed: re-scan
        assert state.is_fixing is False

    def test_cancelling_starts_nothing(
        self, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        start = MagicMock(wraps=task_store.start)
        monkeypatch.setattr(task_store, "start", start)
        state = loaded(doctor_untagged_tracks=19)
        state.start_fix("tags")
        assert state.fix_confirm_open is True
        state.set_fix_confirm_open(False)  # Escape / Cancel
        state.set_fix_confirm_open(False)  # idempotent
        assert state.fix_confirm_open is False
        assert state.pending_fix == ""
        assert state.confirm_fix() is None  # a late confirm starts nothing
        state.set_fix_confirm_open(True)  # no-op: opens only through start_fix
        assert state.fix_confirm_open is False
        state.start_fix("tags")
        state.cancel_fix()
        assert state.confirm_fix() is None
        start.assert_not_called()

    def test_cloud_tagging_names_the_provider_and_the_data(self) -> None:
        state = loaded(doctor_untagged_tracks=19)
        state.ai_provider_name = "OpenAI"
        state.ai_model_name = "gpt-5-mini"
        state.start_fix("tags")
        scope = state.fix_scope
        assert scope["provider_label"] == "OpenAI"
        assert scope["model"] == "gpt-5-mini"
        assert (
            scope["privacy_line"] == "Track titles, artists, albums and genres are sent to OpenAI."
        )
        assert "19 tracks" in scope["count_line"]
        assert not any("Openai" in v for v in scope.values())

    def test_local_endpoint_on_another_machine_is_named(self) -> None:
        """Local in endpoint mode (Ollama on a NAS): metadata goes to that host."""
        snap = readiness(
            ai=entry(
                "unchecked",
                label="Local",
                provider="local",
                model="llama3.2",
                sends_to="192.168.1.20",
            )
        )
        state = loaded(snapshot=snap, doctor_untagged_tracks=19)
        state.ai_provider_name = "Local"
        state.ai_model_name = "llama3.2"
        state.start_fix("tags")
        scope = state.fix_scope
        assert scope["privacy_line"] == (
            "Track titles, artists, albums and genres are sent to 192.168.1.20."
        )
        assert "Runs on this machine" not in scope["privacy_line"]

    def test_builtin_local_ai_stays_on_this_machine(self) -> None:
        snap = readiness(
            ai=entry("unchecked", label="Local", provider="local", model="m", sends_to="")
        )
        state = loaded(snapshot=snap, doctor_untagged_tracks=3)
        state.start_fix("tags")
        assert state.fix_scope["privacy_line"].startswith("Runs on this machine")

    def test_local_embeddings_stay_on_this_machine(self) -> None:
        state = loaded(doctor_tracks_needing_embeddings=1204)
        state.embedding_provider_name = "Local"
        state.embedding_model_name = "all-MiniLM-L6-v2"
        state.start_fix("embeddings_incremental")
        scope = state.fix_scope
        assert scope["privacy_line"] == "Runs on this machine. Nothing is sent to a cloud provider."
        assert scope["provider_label"] == "Local"
        assert "1,204 tracks" in scope["count_line"]

    def test_rebuild_replaces_data(self) -> None:
        state = loaded()
        state.start_fix("embeddings_full")
        assert state.fix_scope["danger"] == "true"
        assert "5,943 tracks" in state.fix_scope["count_line"]

    def test_cleanup_and_audio_scopes(self) -> None:
        state = loaded(doctor_orphaned_embeddings=1, doctor_tracks_without_audio=12)
        state.start_fix("cleanup")
        assert state.fix_scope["title"] == "Remove 1 orphaned embedding?"
        assert state.fix_scope["provider_label"] == ""
        state.cancel_fix()
        state.start_fix("audio_analysis")
        assert "12 tracks" in state.fix_scope["count_line"]

    def test_missing_ai_key_goes_on_the_tags_row(self, toast: MagicMock) -> None:
        ai = entry(
            "not_set_up",
            label="OpenAI",
            provider="openai",
            cause="missing_api_key",
            title="No API key for OpenAI",
            body="Add your OpenAI API key in Settings, or pick a different AI provider.",
            fix_label="Add API key",
            action_label="Set up",
            action_href="/settings?section=ai",
            blocking="true",
        )
        state = loaded(snapshot=readiness(ai=ai), doctor_untagged_tracks=19)
        state.start_fix("tags")
        assert state.fix_confirm_open is False
        toast.error.assert_called_once_with("No API key for OpenAI")
        assert state.fix_errors["tags"] == {
            "title": "No API key for OpenAI",
            "body": "Add your OpenAI API key in Settings, or pick a different AI provider.",
            "action_label": "Add API key",
            "action_href": "/settings?section=ai",
        }
        row = state.row_map["tags"]
        assert row["error_title"] == "No API key for OpenAI"
        assert row["error_action_href"] == "/settings?section=ai"

    def test_missing_local_models_block_embedding_fixes(self, toast: MagicMock) -> None:
        emb = entry(
            "failing",
            label="Local",
            provider="local",
            cause="missing_dependency",
            title="Local models aren't installed",
            body="This install doesn't include local models.",
            fix_label="Switch provider",
            action_href="/settings?section=embedding",
            blocking="true",
        )
        state = loaded(snapshot=readiness(embeddings=emb), doctor_tracks_needing_embeddings=5)
        state.start_fix("embeddings_incremental")
        assert state.fix_confirm_open is False
        error = state.fix_errors["embeddings_incremental"]
        assert error["title"] == "Local models aren't installed"
        assert error["action_href"] == "/settings?section=embedding"
        assert "API key" not in error["title"]
        assert state.row_map["embeddings"]["error_title"] == "Local models aren't installed"

    def test_only_one_fix_at_a_time(self, toast: MagicMock) -> None:
        state = loaded(doctor_tracks_without_musicbrainz=10, doctor_untagged_tracks=2)
        state.is_fixing = True
        state.current_fix_target = "tags"
        state.start_fix("musicbrainz")
        assert state.fix_confirm_open is False
        toast.info.assert_called_once_with("Another fix is running")
        toast.reset_mock()
        assert state.start_fix("tags") is None  # the running fix's own button
        toast.info.assert_not_called()

    def test_a_fix_running_elsewhere_is_shown(self, toast: MagicMock) -> None:
        task_store.start(FIX_JOB, message="x")
        task_store.update(FIX_JOB, extra={"fix_target": "musicbrainz", "fix_total": 10})
        state = loaded(doctor_untagged_tracks=2)
        state.start_fix("tags")
        assert state.fix_confirm_open is False
        assert state.is_fixing is True
        assert state.current_fix_target == "musicbrainz"

    @pytest.mark.parametrize("target", ["", "nope", "tags_all"])
    def test_unknown_targets_do_nothing(self, target: str) -> None:
        state = loaded(doctor_untagged_tracks=2)
        assert state.start_fix(target) is None
        assert state.fix_confirm_open is False

    def test_nothing_to_fix(self, toast: MagicMock) -> None:
        state = loaded()
        state.start_fix("tags")
        assert state.fix_confirm_open is False
        toast.info.assert_called_once()
        unscanned = doctor()
        unscanned.start_fix("cleanup")
        assert unscanned.fix_confirm_open is False


# ── 6.3 Fix handlers report the real cause ───────────────────────────────


def _failed() -> Any:
    done = task_store.get(FIX_JOB)
    assert done is not None
    assert done.status == "failed"
    return done


class TestFixHandlers:
    def test_missing_local_package_names_the_remedy(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        from plexmix.services.providers import ProviderFailure

        write_config(_isolated, LOCAL_EMBED_YAML)
        make_db(_isolated, tracks=2)
        failure = ProviderFailure(rd.Cause.MISSING_DEPENDENCY, "local", "sentence-transformers")
        monkeypatch.setattr(
            "plexmix.services.providers.build_embedding_generator_with_reason",
            lambda settings: (None, failure),
        )
        state = harness()
        run(state.generate_missing_embeddings())
        done = _failed()
        assert done.message == "Local models aren't installed"
        assert "API key" not in done.message
        assert ":latest-local" in done.extra["error_body"]
        assert done.extra["error_action_href"] == "/settings?section=embedding"
        record = readiness_store.get("embeddings")
        assert record is not None and record.cause == rd.Cause.MISSING_DEPENDENCY

        state.poll_task_progress()
        error = state.fix_errors["embeddings_incremental"]
        assert error["title"] == "Local models aren't installed"
        assert error["action_href"] == "/settings?section=embedding"
        toast.error.assert_called_once_with("Local models aren't installed")

    def test_missing_cloud_key_names_the_provider(self, _isolated: Path, toast: MagicMock) -> None:
        write_config(_isolated, OPENAI_EMBED_YAML)
        make_db(_isolated, tracks=2)
        state = harness()
        run(state.generate_missing_embeddings())
        done = _failed()
        assert done.message == "No API key for OpenAI"
        assert done.extra["error_action_href"] == "/settings?section=embedding"

    def test_rebuild_checks_the_provider_before_deleting_the_index(
        self, _isolated: Path, toast: MagicMock
    ) -> None:
        write_config(_isolated, OPENAI_EMBED_YAML)
        make_db(_isolated, tracks=2, embedded=2)
        index = _isolated / "embeddings.index"
        index.write_bytes(b"index")
        state = harness()
        run(state.regenerate_all_embeddings())
        assert _failed().message == "No API key for OpenAI"
        assert index.exists()
        conn = sqlite3.connect(str(_isolated / "plexmix.db"))
        try:
            assert conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 2
        finally:
            conn.close()

    def test_tag_fix_without_an_ai_key_points_to_ai_settings(
        self, _isolated: Path, toast: MagicMock
    ) -> None:
        write_config(_isolated, OPENAI_AI_YAML)
        make_db(_isolated, tracks=2)
        state = harness()
        run(state.regenerate_missing_tags())
        done = _failed()
        assert done.message == "No API key for OpenAI"
        assert done.extra["error_action_href"] == "/settings?section=ai"
        state.poll_task_progress()
        assert state.fix_errors["tags"]["action_label"] == "Add API key"

    def test_retag_all_never_blanks_a_column_the_reply_left_empty(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        """An empty list in the AI provider's reply is no replacement for what a track has."""
        import re

        write_config(_isolated, OPENAI_AI_YAML)
        ids = make_db(_isolated, tracks=3)
        conn = sqlite3.connect(str(_isolated / "plexmix.db"))
        try:
            conn.execute(
                "UPDATE tracks SET tags = 'old-tag', environments = 'old-room', "
                "instruments = 'old-horn'"
            )
            conn.commit()
        finally:
            conn.close()
        tags_only, no_tags, nothing = ids

        class Provider:
            def complete(self, prompt: str, **options: Any) -> str:
                listed = prompt.split("Assign tags to the following songs:")[1]
                sent = [int(found) for found in re.findall(r'"id": (\d+)', listed)]
                assert sorted(sent) == ids
                return json.dumps(
                    {
                        str(tags_only): {"tags": ["new"], "environments": [], "instruments": []},
                        str(no_tags): {"tags": [], "environments": ["focus"], "instruments": []},
                        str(nothing): {"tags": [], "environments": [], "instruments": []},
                    }
                )

        monkeypatch.setattr(
            "plexmix.services.providers.build_ai_provider", lambda *args, **kwargs: Provider()
        )

        run(harness().regenerate_all_tags())

        done = task_store.get(FIX_JOB)
        assert done is not None and done.status == "completed", done
        conn = sqlite3.connect(str(_isolated / "plexmix.db"))
        try:
            rows = dict(
                (row[0], row[1:])
                for row in conn.execute("SELECT id, tags, environments, instruments FROM tracks")
            )
        finally:
            conn.close()
        assert rows[tags_only] == ("new", "old-room", "old-horn")
        assert rows[no_tags] == ("old-tag", "focus", "old-horn")
        assert rows[nothing] == ("old-tag", "old-room", "old-horn")

    @pytest.mark.parametrize("column", ["tags", "environments", "instruments"])
    @pytest.mark.parametrize(
        "blank", [[""], [" "], ["", ""], [None], ["", " ", None], [{"a": 1}], [True]]
    )
    @pytest.mark.parametrize("through", ["results of any source", "the tag generator's parser"])
    def test_retag_all_cleans_the_lists_before_its_per_column_rule(
        self,
        _isolated: Path,
        monkeypatch: pytest.MonkeyPatch,
        toast: MagicMock,
        column: str,
        blank: list[Any],
        through: str,
    ) -> None:
        """A list of blanks in one column blanks nothing, writes no "," and no "None".

        The handler applies ``clean_tag_list`` itself: it can be handed results that
        did not come through the parser. The other two columns, which hold real
        items, are still written.
        """
        import re

        from plexmix.ai.tag_generator import TagGenerator

        write_config(_isolated, OPENAI_AI_YAML)
        (track,) = make_db(_isolated, tracks=1)
        conn = sqlite3.connect(str(_isolated / "plexmix.db"))
        try:
            conn.execute(
                "UPDATE tracks SET tags = 'old-tag', environments = 'old-room', "
                "instruments = 'old-horn'"
            )
            conn.commit()
        finally:
            conn.close()
        reply: dict[str, Any] = {
            "tags": ["new"],
            "environments": ["focus"],
            "instruments": ["lute"],
        }
        reply[column] = blank
        monkeypatch.setattr(
            "plexmix.services.providers.build_ai_provider", lambda *args, **kwargs: object()
        )
        if through == "results of any source":
            monkeypatch.setattr(
                TagGenerator, "generate_tags_batch", lambda self, tracks, **kw: {track: reply}
            )
        else:

            class Provider:
                def complete(self, prompt: str, **options: Any) -> str:
                    listed = prompt.split("Assign tags to the following songs:")[1]
                    assert [int(n) for n in re.findall(r'"id": (\d+)', listed)] == [track]
                    return json.dumps({str(track): reply})

            monkeypatch.setattr(
                "plexmix.services.providers.build_ai_provider", lambda *args, **kwargs: Provider()
            )

        run(harness().regenerate_all_tags())

        done = task_store.get(FIX_JOB)
        assert done is not None and done.status == "completed", done
        conn = sqlite3.connect(str(_isolated / "plexmix.db"))
        try:
            row = conn.execute(
                "SELECT tags, environments, instruments FROM tracks WHERE id = ?", (track,)
            ).fetchone()
        finally:
            conn.close()
        expected = {"tags": "new", "environments": "focus", "instruments": "lute"}
        previous = {"tags": "old-tag", "environments": "old-room", "instruments": "old-horn"}
        expected[column] = previous[column]  # the blank list is no result for this column
        assert dict(zip(("tags", "environments", "instruments"), row)) == expected
        assert done.extra["result_msg"] == "Tagged 1 track."

    def test_retag_all_writes_nothing_for_a_track_that_got_only_blanks(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        """Three lists of blanks are no result: nothing is written and nothing is counted."""
        from plexmix.ai.tag_generator import TagGenerator

        write_config(_isolated, OPENAI_AI_YAML)
        (track,) = make_db(_isolated, tracks=1)
        conn = sqlite3.connect(str(_isolated / "plexmix.db"))
        try:
            conn.execute(
                "UPDATE tracks SET tags = 'old-tag', environments = 'old-room', "
                "instruments = 'old-horn', tags_generated_at = '2020-01-01 00:00:00'"
            )
            conn.commit()
        finally:
            conn.close()
        reply = {"tags": [""], "environments": [" ", " "], "instruments": [None]}
        monkeypatch.setattr(
            "plexmix.services.providers.build_ai_provider", lambda *args, **kwargs: object()
        )
        monkeypatch.setattr(
            TagGenerator, "generate_tags_batch", lambda self, tracks, **kw: {track: reply}
        )

        run(harness().regenerate_all_tags())

        done = task_store.get(FIX_JOB)
        assert done is not None and done.status == "completed", done
        assert done.extra["result_msg"] == "Tagged 0 tracks."
        conn = sqlite3.connect(str(_isolated / "plexmix.db"))
        try:
            row = conn.execute(
                "SELECT tags, environments, instruments, tags_generated_at FROM tracks"
            ).fetchone()
        finally:
            conn.close()
        assert tuple(row) == ("old-tag", "old-room", "old-horn", "2020-01-01 00:00:00")

    def test_audio_fix_without_essentia(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        real = importlib.util.find_spec

        def find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
            return None if name == "essentia" else real(name, *args, **kwargs)

        monkeypatch.setattr(importlib.util, "find_spec", find_spec)
        make_db(_isolated, tracks=2)
        state = harness()
        run(state.analyze_missing_audio())
        done = _failed()
        assert done.message == "Audio analysis isn't installed"
        text = done.message + done.extra["error_body"]
        assert "Run:" not in text and "poetry" not in text and "pip install" not in text
        assert "plexmix[audio]" in done.extra["error_body"]

    def test_fix_without_a_library(self, _isolated: Path, toast: MagicMock) -> None:
        state = harness()
        run(state.enrich_missing_musicbrainz())
        done = _failed()
        assert done.message == "Your library hasn't been synced yet"
        assert done.extra["error_action_href"] == "/dashboard"

    def test_cleanup_removes_orphans(self, _isolated: Path, toast: MagicMock) -> None:
        from plexmix.ui.states.doctor_state import DoctorState

        make_db(_isolated, tracks=2, embedded=1, orphans=1)
        state = harness()
        run(state.delete_orphaned_embeddings())
        done = task_store.get(FIX_JOB)
        assert done is not None and done.status == "completed"
        assert done.extra["result_msg"] == "Removed 1 orphaned embedding."
        events = state.poll_task_progress()
        assert DoctorState.run_health_check in events
        toast.success.assert_called_once_with("Removed 1 orphaned embedding.")

    def test_a_running_fix_is_never_doubled(self, toast: MagicMock) -> None:
        task_store.start(FIX_JOB, message="Enriching")
        task_store.update(FIX_JOB, extra={"fix_target": "musicbrainz", "fix_total": 10})
        state = harness()
        run(state.delete_orphaned_embeddings())
        running = task_store.get(FIX_JOB)
        assert running is not None and running.status == "running"
        assert state.is_fixing is True
        assert state.current_fix_target == "musicbrainz"
        assert state.fix_total == 10

    def test_a_new_fix_clears_old_failures(self, _isolated: Path, toast: MagicMock) -> None:
        make_db(_isolated, tracks=1, orphans=1)
        state = harness()
        state.fix_errors = {"tags": {"title": "No API key for OpenAI"}}
        run(state.delete_orphaned_embeddings())
        assert state.fix_errors == {}


# ── 6.4 Progress and failures ────────────────────────────────────────────


class TestPollTaskProgress:
    def test_failure_is_stored_and_still_toasted(self, toast: MagicMock) -> None:
        from plexmix.ui.states.doctor_state import fix_failure

        task_store.start(FIX_JOB, message="Tagging")
        fix_failure(
            "tags",
            {
                "title": "No API key for OpenAI",
                "body": "Add your OpenAI API key in Settings.",
                "action_label": "Add API key",
                "action_href": "/settings?section=ai",
            },
        )
        state = loaded(doctor_untagged_tracks=3)
        state.is_fixing = True
        state.current_fix_target = "tags"
        result = state.poll_task_progress()
        toast.error.assert_called_once_with("No API key for OpenAI")
        assert result is toast.error.return_value
        assert state.is_fixing is False
        assert state.fix_errors["tags"] == {
            "title": "No API key for OpenAI",
            "body": "Add your OpenAI API key in Settings.",
            "action_label": "Add API key",
            "action_href": "/settings?section=ai",
        }
        row = state.row_map["tags"]
        assert row["error_title"] == "No API key for OpenAI"
        assert row["error_action_label"] == "Add API key"
        # A later poll (the poller keeps running) leaves the message on the row.
        state.poll_task_progress()
        assert "tags" in state.fix_errors

    def test_failure_without_a_target_still_toasts(self, toast: MagicMock) -> None:
        task_store.start(FIX_JOB, message="x")
        task_store.complete(FIX_JOB, status="failed", message="")
        state = doctor()
        state.is_fixing = True
        state.poll_task_progress()
        toast.error.assert_called_once()
        assert state.fix_errors == {}

    def test_running_entry_drives_the_callout(self) -> None:
        task_store.start(FIX_JOB, message="Generating embeddings…")
        task_store.update(FIX_JOB, extra={"fix_target": "embeddings_incremental"})
        state = doctor()
        state.poll_task_progress()
        assert state.is_fixing is True
        assert state.current_fix_target == "embeddings_incremental"
        assert state.fix_title == "Generating embeddings"
        assert state.fix_total == 0
        assert state.fix_progress_label == ""  # indeterminate, never "0 / 0"
        assert state.fix_percent == 0
        task_store.update(FIX_JOB, progress=1204, extra={"fix_total": 5807})
        state.poll_task_progress()
        assert state.fix_progress_label == "1,204 / 5,807"
        assert state.fix_percent == 20

    def test_cancelled_rescans(self, toast: MagicMock) -> None:
        from plexmix.ui.states.doctor_state import DoctorState

        task_store.start(FIX_JOB, message="x")
        task_store.complete(FIX_JOB, status="cancelled")
        state = doctor()
        state.is_fixing = True
        events = state.poll_task_progress()
        assert DoctorState.run_health_check in events
        toast.warning.assert_called_once_with("Fix cancelled")

    @pytest.mark.parametrize(
        "progress,total,label",
        [(0, 0, ""), (5, 0, ""), (0, -1, ""), (0, 3, "0 / 3"), (1204, 5807, "1,204 / 5,807")],
    )
    def test_fix_progress_label_never_shows_0_of_0(
        self, progress: int, total: int, label: str
    ) -> None:
        state = doctor()
        state.fix_progress = progress
        state.fix_total = total
        assert state.fix_progress_label == label
        assert state.fix_progress_label != "0 / 0"

    def test_on_load_still_scans_and_checks_plex(self) -> None:
        from plexmix.ui.states.doctor_state import DoctorState

        state = doctor()
        state.check_auth = MagicMock(return_value=True)
        state.load_app_data = MagicMock()
        state._plex_readiness_events = MagicMock(return_value=["plex-check"])
        events = state.on_load()
        assert events == [DoctorState.run_health_check, "plex-check"]

    def test_a_fix_that_failed_while_away_is_still_toasted(self, toast: MagicMock) -> None:
        from plexmix.ui.states.doctor_state import DoctorState, fix_failure

        task_store.start(FIX_JOB, message="Enriching")
        fix_failure("musicbrainz", {"title": "Can't reach MusicBrainz"})
        state = doctor()
        state.is_fixing = True  # this session started it, then left the page
        state.check_auth = MagicMock(return_value=True)
        state.load_app_data = MagicMock()
        state._plex_readiness_events = MagicMock(return_value=[])
        events = state.on_load()
        assert events == [toast.error.return_value, DoctorState.run_health_check]
        toast.error.assert_called_once_with("Can't reach MusicBrainz")
        assert state.fix_errors["musicbrainz"]["title"] == "Can't reach MusicBrainz"

    def test_a_fix_that_finished_while_away_rescans_once(self, toast: MagicMock) -> None:
        from plexmix.ui.states.doctor_state import DoctorState, finish_fix

        cancel_event = task_store.start(FIX_JOB, message="Cleaning up")
        assert cancel_event is not None
        finish_fix("cleanup", cancel_event, "Removed 3 orphaned embeddings.")
        state = doctor()
        state.is_fixing = True
        state.check_auth = MagicMock(return_value=True)
        state.load_app_data = MagicMock()
        state._plex_readiness_events = MagicMock(return_value=[])
        events = state.on_load()
        assert events == [toast.success.return_value, DoctorState.run_health_check]


# ── Carry-over: the Enrich confirmation names MusicBrainz once ───────────


def _fix_description(scope: dict[str, str]) -> str:
    """The Doctor fix dialog's description as a screen reader reads it.

    Mirrors ``pages/doctor.py`` ``_fix_confirm_dialog``: count, replace, provider line
    (only while ``provider_label`` is set; ``scope_provider_line`` renders
    ``readiness.scope_provider_text``), duration, then the privacy note. Each is a
    block line, read as one run of text; empty lines render nothing.
    """
    provider = (
        rd.scope_provider_text(scope["provider_label"], scope["model"])
        if scope["provider_label"]
        else ""
    )
    lines = (
        scope["count_line"],
        scope["replace_line"],
        provider,
        scope["duration_line"],
        scope["privacy_line"],
    )
    return " ".join(line for line in lines if line)


class TestEnrichConfirmationCopy:
    def test_musicbrainz_description_does_not_repeat_the_name(self, toast: MagicMock) -> None:
        state = loaded(doctor_tracks_without_musicbrainz=5807)
        state.start_fix("musicbrainz")
        scope = state.fix_scope
        assert scope["provider_label"] == "MusicBrainz" and scope["model"] == ""
        assert _fix_description(scope) == (
            "5,807 tracks will be looked up on MusicBrainz. "
            "MusicBrainz allows about one lookup per second, so this can take up to about "
            "1 h 37 min. You can cancel at any time. "
            "Track titles, artists and albums are sent to MusicBrainz, a free public "
            "music database."
        )
        assert "MusicBrainz MusicBrainz" not in _fix_description(scope)

    def test_provider_with_a_model_still_gets_its_line(self, toast: MagicMock) -> None:
        state = loaded(doctor_tracks_without_musicbrainz=5807)
        state.ai_provider_name, state.ai_model_name = "OpenAI", "gpt-5-mini"
        scope = state._fix_scope("tags", 59)
        assert scope["provider_label"] == "OpenAI" and scope["model"] == "gpt-5-mini"
        assert "59 tracks will be tagged. OpenAI · gpt-5-mini " in _fix_description(scope)

    def test_the_component_renders_no_line_for_musicbrainz(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import reflex as rx

        from plexmix.ui.components import scope_confirm as sc

        span = MagicMock(name="el.span")
        fragment = MagicMock(name="fragment")
        monkeypatch.setattr(rx.el, "span", span)
        monkeypatch.setattr(rx, "fragment", fragment)
        # Static text (what the mocked Reflex hands over): no line at all.
        assert sc.scope_provider_line("MusicBrainz", "") is fragment.return_value
        span.assert_not_called()
        # A model-less provider the copy doesn't name keeps its bare name, once.
        sc.scope_provider_line("Custom (OpenAI-compatible)", "")
        span.assert_called_once_with("Custom (OpenAI-compatible)")


# ── Rebuild embeddings replaces in place (fix-cli-secrets-and-data-loss 3.10) ──

DUMMY_KEY = "dummy-api-key-0000"
CUSTOM_EMBED_YAML = (
    "embedding:\n"
    "  default_provider: custom\n"
    "  custom_endpoint: http://127.0.0.1:9/v1\n"
    "  custom_model: standin\n"
    "  custom_dimension: 4\n"
    f"  custom_api_key: {DUMMY_KEY}\n"
)
OLD = [0.1, 0.2, 0.3, 0.4]
NEW = [0.9, 0.1, 0.0, 0.0]
OTHER_MODEL = [0.1, 0.2]  # what make_db(embedded=...) writes for "openai"
LONG_AGO = datetime(2020, 1, 1)
CANNOT_CONNECT = "Could not connect to the server. Is the URL correct and the service running?"
NOTHING_CHANGED = "No embedding was changed."


class FakeGenerator:
    """An in-process embedding provider that records what it was asked to embed.

    ``fail_test`` is raised by the single test request of the provider check,
    ``fail_on_batch`` makes that batch raise ``batch_error``, and
    ``cancel_after_batch`` requests a cancel once that batch has been answered, and
    ``short_on_batch`` answers that batch with one vector too few.
    """

    provider_name = "custom"

    def __init__(
        self,
        *,
        dimension: int = 4,
        fail_test: Optional[BaseException] = None,
        fail_on_batch: int = 0,
        batch_error: Optional[BaseException] = None,
        cancel_after_batch: int = 0,
        short_on_batch: int = 0,
    ) -> None:
        self.dimension = dimension
        self.fail_test = fail_test
        self.fail_on_batch = fail_on_batch
        self.short_on_batch = short_on_batch
        self.batch_error = batch_error or ConnectionError("Connection refused")
        self.cancel_after_batch = cancel_after_batch
        self.test_texts: list[str] = []
        self.batches: list[list[str]] = []
        self.calls = 0
        self.on_main_thread: list[bool] = []

    @property
    def embedded(self) -> int:
        return sum(len(batch) for batch in self.batches)

    def get_dimension(self) -> int:
        return self.dimension

    def _vector(self) -> list[float]:
        return list(NEW[: self.dimension])

    def generate_embedding(self, text: str) -> list[float]:
        self.on_main_thread.append(threading.current_thread() is threading.main_thread())
        self.test_texts.append(text)
        if self.fail_test is not None:
            raise self.fail_test
        return self._vector()

    def generate_batch_embeddings(
        self, texts: list[str], batch_size: int = 50
    ) -> list[list[float]]:
        self.on_main_thread.append(threading.current_thread() is threading.main_thread())
        self.calls += 1
        if self.calls == self.fail_on_batch:
            raise self.batch_error
        self.batches.append(list(texts))
        if self.calls == self.cancel_after_batch:
            task_store.cancel(FIX_JOB)
        vectors = [self._vector() for _ in texts]
        return vectors[:-1] if self.calls == self.short_on_batch else vectors


def use_generator(monkeypatch: pytest.MonkeyPatch, generator: FakeGenerator) -> FakeGenerator:
    """Hand ``generator`` to the real provider check in place of a real provider."""
    monkeypatch.setattr(
        "plexmix.services.regenerate_service.build_embedding_generator_with_reason",
        lambda settings: (generator, None),
    )
    return generator


@pytest.fixture
def pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Embed two tracks per batch, so five tracks take three batches."""
    monkeypatch.setattr("plexmix.ui.states.doctor_state.EMBEDDING_BATCH_SIZE", 2)


def seed_embeddings(
    data: Path, ids: list[int], vector: list[float] = OLD, model: str = "custom"
) -> None:
    """Give ``ids`` an embedding of ``model`` (the configured one), written long ago."""
    from plexmix.database.models import Embedding
    from plexmix.database.sqlite_manager import SQLiteManager

    with SQLiteManager(str(data / "plexmix.db")) as db:
        for tid in ids:
            db.insert_embedding(
                Embedding(
                    track_id=tid,
                    embedding_model=model,
                    embedding_dim=len(vector),
                    vector=list(vector),
                    created_at=LONG_AGO,
                    updated_at=LONG_AGO,
                )
            )


def index_files(data: Path) -> dict[str, bytes]:
    """Every file of the search index (moved-aside copies included), by name."""
    return {path.name: path.read_bytes() for path in sorted(data.glob("embeddings.*"))}


def save_index(data: Path, ids: list[int], vector: list[float] = OLD) -> dict[str, bytes]:
    """Save a real search index over ``ids``; returns its files."""
    from plexmix.database.vector_index import VectorIndex

    path = data / "embeddings.index"
    index = VectorIndex(dimension=len(vector), index_path=str(path))
    index.build_index([list(vector) for _ in ids], list(ids))
    index.save_index(str(path))
    return index_files(data)


def index_metadata(data: Path) -> dict[str, Any]:
    with open(data / "embeddings.metadata", "rb") as handle:
        loaded_metadata: dict[str, Any] = pickle.load(handle)
    return loaded_metadata


def embedding_rows(data: Path) -> dict[tuple[int, str], list[float]]:
    """Every embedding row: (track id, model) → vector."""
    conn = sqlite3.connect(str(data / "plexmix.db"))
    try:
        rows = conn.execute("SELECT track_id, embedding_model, vector FROM embeddings").fetchall()
    finally:
        conn.close()
    return {(int(tid), str(model)): json.loads(vector) for tid, model, vector in rows}


def library(data: Path, tracks: int = 5) -> list[int]:
    """Five tracks that all hold an embedding of the configured model, and their index."""
    write_config(data, CUSTOM_EMBED_YAML)
    ids = make_db(data, tracks=tracks)
    seed_embeddings(data, ids)
    save_index(data, ids)
    return ids


def library_of_another_model(data: Path, tracks: int = 5) -> list[int]:
    """Five tracks embedded by another provider (2 dimensions), and that provider's index."""
    write_config(data, CUSTOM_EMBED_YAML)
    ids = make_db(data, tracks=tracks, embedded=tracks)
    save_index(data, ids, OTHER_MODEL)
    return ids


def library_of_two_sizes(data: Path, tracks: int = 5, replaced: int = 0) -> list[int]:
    """Rows of two vector sizes under one model name, and the index of the earlier size.

    The configured provider ("custom") makes 4 numbers. Every track holds a "custom" row
    of 2 numbers, as after a change of ``custom_dimension``, and the saved index holds
    those. The first ``replaced`` tracks already hold a row of 4 numbers (a run that
    was stopped part-way wrote them).
    """
    write_config(data, CUSTOM_EMBED_YAML)
    ids = make_db(data, tracks=tracks)
    seed_embeddings(data, ids, OTHER_MODEL)
    save_index(data, ids, OTHER_MODEL)
    seed_embeddings(data, ids[:replaced], NEW)
    return ids


INDEX_NOT_UPDATED = "The search index was not updated"
POINTS_TO_REBUILD = "Rebuild the embeddings"


def index_status() -> Any:
    """The readiness of the search index, read from the files and settings as they are now."""
    from plexmix.config.settings import Settings

    settings = Settings.load_from_file()
    return rd.evaluate(settings, rd.collect_facts(settings))["index"]


def sizes_of(data: Path) -> dict[int, int]:
    """The size of the "custom" vector of every track that has one."""
    return {tid: len(vector) for (tid, model), vector in embedding_rows(data).items()}


def assert_index_not_updated(state: Any, toast: MagicMock, done: Any) -> None:
    """The finished job says that the search index was not updated, never that it was.

    ``done`` is the finished Doctor task. Nothing the page is handed claims a success,
    and the words say what replaces the embeddings in the way.
    """
    said = shown(done) + " " + " ".join(str(call) for call in toast.method_calls)
    for row in state.row_map.values():
        said += " " + row["error_title"] + " " + row["error_body"]
    assert INDEX_NOT_UPDATED in said and POINTS_TO_REBUILD in said
    toast.success.assert_not_called()
    assert "Rebuilt the search index" not in said
    assert "index was updated" not in said.lower()


def assert_saved_index_still_mismatched(data: Path, ids: list[int], dimension: int = 4) -> None:
    """A fresh open of the saved index still covers ``ids`` and reports the mismatch."""
    from plexmix.database.vector_index import VectorIndex

    opened = VectorIndex(dimension=dimension, index_path=str(data / "embeddings.index"))
    assert sorted(opened.track_ids) == sorted(ids)
    assert opened.dimension_mismatch is True


def rebuild(state: Optional[Any] = None) -> Any:
    """Run the "Rebuild embeddings" handler to its end; returns the finished task."""
    state = state if state is not None else harness()
    run(state.regenerate_all_embeddings())
    done = task_store.get(FIX_JOB)
    assert done is not None
    return done


def shown(done: Any) -> str:
    """Everything a finished fix hands to the page."""
    return " ".join([str(done.message), *(str(value) for value in done.extra.values())])


def engine_spies(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Spies on the two sync engine functions that close a pass: finish and follow-the-rows."""
    from plexmix.plex import sync

    finish = MagicMock(wraps=sync.finish_embeddings_pass)
    follow = MagicMock(wraps=sync.follow_rows_after_stopped_pass)
    monkeypatch.setattr(sync, "finish_embeddings_pass", finish)
    monkeypatch.setattr(sync, "follow_rows_after_stopped_pass", follow)
    return finish, follow


class TestRebuildEmbeddings:
    @pytest.mark.parametrize("fail_on_batch,replaced", [(1, 0), (2, 2)])
    def test_rebuild_embeddings_deletes_nothing_first(
        self,
        _isolated: Path,
        monkeypatch: pytest.MonkeyPatch,
        toast: MagicMock,
        pairs: None,
        caplog: pytest.LogCaptureFixture,
        fail_on_batch: int,
        replaced: int,
    ) -> None:
        ids = library(_isolated)
        before_rows, before_index = embedding_rows(_isolated), index_files(_isolated)
        error = ConnectionError(f"Connection refused (Authorization: Bearer {DUMMY_KEY})")
        generator = use_generator(
            monkeypatch, FakeGenerator(fail_on_batch=fail_on_batch, batch_error=error)
        )
        state = loaded(harness(), doctor_total_tracks=5)

        with caplog.at_level(logging.DEBUG):
            done = rebuild(state)

        assert done.status == "failed"
        rows = embedding_rows(_isolated)
        # Every track still has its embedding: the new one or the previous one.
        assert set(rows) == {(tid, "custom") for tid in ids}
        assert [tid for tid in ids if rows[(tid, "custom")] == NEW] == ids[:replaced]
        assert [tid for tid in ids if rows[(tid, "custom")] == OLD] == ids[replaced:]
        # The index files are still there, and the index still covers every track.
        assert set(index_files(_isolated)) == {"embeddings.index", "embeddings.metadata"}
        assert sorted(index_metadata(_isolated)["track_ids"]) == ids
        if not replaced:
            assert rows == before_rows
            assert index_files(_isolated) == before_index

        assert done.message == CANNOT_CONNECT
        note = (
            f"{replaced} of 5 tracks got a new embedding. The others keep the embedding they had."
            if replaced
            else NOTHING_CHANGED
        )
        assert done.extra["error_body"].endswith(note)
        record = readiness_store.get("embeddings")
        assert record is not None
        assert (record.level, record.cause) == (rd.Level.FAILING, rd.Cause.UNREACHABLE)
        state.poll_task_progress()
        assert state.row_map["embeddings"]["error_title"] == CANNOT_CONNECT
        assert state.row_map["embeddings"]["error_body"].endswith(note)
        assert generator.embedded == replaced
        assert f"Doctor embedding rebuild stopped after {replaced} of 5 tracks" in caplog.text
        assert DUMMY_KEY not in shown(done) + caplog.text

    @pytest.mark.parametrize(
        "error,cause,title",
        [
            (
                ConnectionError(f"Connection refused by http://user:{DUMMY_KEY}@127.0.0.1:9/v1"),
                rd.Cause.UNREACHABLE,
                CANNOT_CONNECT,
            ),
            (
                RuntimeError(f"Error code: 401 - Incorrect API key provided: {DUMMY_KEY}"),
                rd.Cause.INVALID_CREDENTIALS,
                "Invalid API key. Please check your key and try again.",
            ),
        ],
    )
    def test_rebuild_is_refused_when_the_provider_does_not_answer(
        self,
        _isolated: Path,
        monkeypatch: pytest.MonkeyPatch,
        toast: MagicMock,
        caplog: pytest.LogCaptureFixture,
        error: BaseException,
        cause: rd.Cause,
        title: str,
    ) -> None:
        from plexmix.config.settings import Settings

        library(_isolated)
        before_rows, before_index = embedding_rows(_isolated), index_files(_isolated)
        generator = use_generator(monkeypatch, FakeGenerator(fail_test=error))
        state = loaded(harness(), doctor_total_tracks=5)

        with caplog.at_level(logging.DEBUG):
            done = rebuild(state)

        # One test request, then nothing: no track was sent and nothing was changed.
        assert len(generator.test_texts) == 1
        assert generator.batches == []
        assert embedding_rows(_isolated) == before_rows
        assert index_files(_isolated) == before_index

        # The Embeddings row shows the cause, and says that nothing was changed.
        assert done.status == "failed"
        assert done.message == title
        assert done.extra["error_body"].endswith(NOTHING_CHANGED)
        assert done.extra["error_action_href"] == "/settings?section=embedding"
        state.poll_task_progress()
        row = state.row_map["embeddings"]
        assert row["error_title"] == title
        assert row["error_body"].endswith(NOTHING_CHANGED)
        assert row["error_action_href"] == "/settings?section=embedding"
        toast.error.assert_called_once_with(title)

        # Readiness is failing with that cause, on every status surface.
        record = readiness_store.get("embeddings")
        assert record is not None
        assert (record.level, record.cause) == (rd.Level.FAILING, cause)
        settings = Settings.load_from_file()
        status = rd.evaluate(settings, rd.collect_facts(settings))
        assert status["embeddings"].level == rd.Level.FAILING
        assert status["embeddings"].cause == cause
        assert "it did not answer the test request" in caplog.text
        assert DUMMY_KEY not in shown(done) + caplog.text + record.message

    def test_rebuild_that_cannot_start_leaves_an_unreadable_index_alone(
        self, _isolated: Path, toast: MagicMock
    ) -> None:
        """The check comes before the index is opened, so nothing is moved aside either."""
        write_config(_isolated, OPENAI_EMBED_YAML)  # no key is stored
        make_db(_isolated, tracks=2, embedded=2)
        (_isolated / "embeddings.index").write_bytes(b"not an index")
        before_rows, before_index = embedding_rows(_isolated), index_files(_isolated)

        done = rebuild()

        assert done.status == "failed"
        assert done.message == "No API key for OpenAI"
        assert done.extra["error_body"].endswith(NOTHING_CHANGED)
        assert embedding_rows(_isolated) == before_rows
        assert index_files(_isolated) == before_index
        record = readiness_store.get("embeddings")
        assert record is not None
        assert (record.level, record.cause) == (rd.Level.FAILING, rd.Cause.MISSING_API_KEY)

    def test_rebuild_processes_every_track_after_an_interrupted_run(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock, pairs: None
    ) -> None:
        from plexmix.config.settings import Settings
        from plexmix.database.models import Embedding
        from plexmix.database.sqlite_manager import SQLiteManager
        from plexmix.services import regenerate_service as rs

        ids = library(_isolated)
        # An earlier "embeddings generate --regenerate" replaced two tracks and stopped.
        provider = rs.run_provider(Settings.load_from_file(), rs.SCOPE_EMBEDDINGS)
        with SQLiteManager(str(_isolated / "plexmix.db")) as db:
            earlier = rs.open_run(db, rs.SCOPE_EMBEDDINGS, provider)
            for tid in ids[:2]:
                db.insert_embedding(
                    Embedding(track_id=tid, embedding_model="custom", embedding_dim=4, vector=OLD)
                )
            # A command that continues that run would do only the other three.
            assert rs.preview_run(db, rs.SCOPE_EMBEDDINGS, provider).continued is True
            assert rs.remaining_track_ids(db, earlier) == ids[2:]
            marker = rs.marker_path(db)
        marker_before = marker.read_bytes()

        # The confirmation states every track, and the fix embeds every track.
        state = loaded(harness(), doctor_total_tracks=5)
        state.start_fix("embeddings_full")
        assert "5 tracks" in state.fix_scope["count_line"]
        assert state.confirm_fix() is type(state).regenerate_all_embeddings
        generator = use_generator(monkeypatch, FakeGenerator())

        done = rebuild(state)

        assert done.status == "completed"
        assert done.extra["result_msg"] == "Rebuilt the search index with 5 tracks."
        assert generator.embedded == 5
        assert embedding_rows(_isolated) == {(tid, "custom"): NEW for tid in ids}
        assert done.extra["fix_total"] == 5 and done.progress == 5
        # The marker belongs to the CLI commands: it was not read and not written.
        assert marker.read_bytes() == marker_before

    def test_cancelled_rebuild_keeps_old_and_new(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock, pairs: None
    ) -> None:
        from plexmix.ui.states.doctor_state import DoctorState

        ids = library(_isolated)
        generator = use_generator(monkeypatch, FakeGenerator(cancel_after_batch=1))
        finish, follow = engine_spies(monkeypatch)
        state = loaded(harness(), doctor_total_tracks=5)

        done = rebuild(state)

        assert done.status == "cancelled"
        assert generator.embedded == 2  # the cancel request stopped the pass after one batch
        # A stopped pass is not closed: nothing is removed, the index follows the rows.
        finish.assert_not_called()
        follow.assert_called_once()
        rows = embedding_rows(_isolated)
        assert set(rows) == {(tid, "custom") for tid in ids}  # no track lost its embedding
        assert [rows[(tid, "custom")] for tid in ids] == [NEW, NEW, OLD, OLD, OLD]
        # The index follows the rows: every track, the new vectors and the previous ones.
        assert set(index_files(_isolated)) == {"embeddings.index", "embeddings.metadata"}
        assert sorted(index_metadata(_isolated)["track_ids"]) == ids
        events = state.poll_task_progress()
        assert DoctorState.run_health_check in events
        toast.warning.assert_called_once_with(
            "Fix cancelled. 2 of 5 tracks got a new embedding. "
            "The others keep the embedding they had."
        )

    def test_cancel_before_the_first_batch_changes_nothing(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        library(_isolated)
        before_rows, before_index = embedding_rows(_isolated), index_files(_isolated)
        generator = FakeGenerator()

        def answer_then_cancel(text: str) -> list[float]:
            task_store.cancel(FIX_JOB)  # the user cancels while the provider is checked
            return list(NEW)

        monkeypatch.setattr(generator, "generate_embedding", answer_then_cancel)
        use_generator(monkeypatch, generator)
        state = loaded(harness(), doctor_total_tracks=5)

        done = rebuild(state)

        assert done.status == "cancelled"
        assert generator.batches == []
        assert embedding_rows(_isolated) == before_rows
        assert index_files(_isolated) == before_index
        # Readiness records what a run did: no row was written, so nothing is claimed.
        assert readiness_store.get("embeddings") is None
        state.poll_task_progress()
        toast.warning.assert_called_once_with(f"Fix cancelled. {NOTHING_CHANGED}")

    def test_finished_rebuild_removes_other_models_and_rebuilds_the_index(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock, pairs: None
    ) -> None:
        """The usual way in: the provider changed, so the index has another dimension."""
        ids = library_of_another_model(_isolated)
        assert index_metadata(_isolated)["dimension"] == 2
        generator = use_generator(monkeypatch, FakeGenerator())
        finish, _follow = engine_spies(monkeypatch)

        done = rebuild()

        assert done.status == "completed"
        assert done.extra["result_msg"] == "Rebuilt the search index with 5 tracks."
        assert generator.embedded == 5
        # The pass is closed by the sync engine's own code, for the model of the new rows.
        finish.assert_called_once()
        assert finish.call_args.args[2:] == ("custom",)
        # Only now, with a replacement for every track, are the other model's rows gone.
        assert embedding_rows(_isolated) == {(tid, "custom"): NEW for tid in ids}
        metadata = index_metadata(_isolated)
        assert metadata["dimension"] == 4
        assert sorted(metadata["track_ids"]) == ids
        record = readiness_store.get("embeddings")
        assert record is not None and record.level == rd.Level.READY

    @pytest.mark.parametrize("stop", ["cancel", "provider"])
    def test_stopped_rebuild_after_a_provider_change_keeps_rows_and_index(
        self,
        _isolated: Path,
        monkeypatch: pytest.MonkeyPatch,
        toast: MagicMock,
        pairs: None,
        stop: str,
    ) -> None:
        from plexmix.config.settings import Settings

        ids = library_of_another_model(_isolated)
        before_rows, before_index = embedding_rows(_isolated), index_files(_isolated)
        fake = (
            FakeGenerator(cancel_after_batch=1)
            if stop == "cancel"
            else FakeGenerator(fail_on_batch=2)
        )
        use_generator(monkeypatch, fake)
        finish, follow = engine_spies(monkeypatch)

        state = loaded(harness(), doctor_total_tracks=5)
        done = rebuild(state)

        assert done.status == ("cancelled" if stop == "cancel" else "failed")
        # A stopped pass is not closed. The index is offered the rows, and the engine
        # refuses to build it: three tracks hold nothing but the other model's row.
        finish.assert_not_called()
        follow.assert_called_once()
        rows = embedding_rows(_isolated)
        # No row of the other model was removed: three tracks have nothing else yet.
        assert {key: rows[key] for key in before_rows} == before_rows
        assert {key for key in rows if key[1] == "custom"} == {(tid, "custom") for tid in ids[:2]}
        # Two new vectors must not pass for the library's index: the saved one stays,
        # so Doctor still reports the mismatch and still offers "Rebuild embeddings".
        assert index_files(_isolated) == before_index
        settings = Settings.load_from_file()
        status = rd.evaluate(settings, rd.collect_facts(settings))
        assert status["index"].cause == rd.Cause.DIMENSION_MISMATCH
        stats = {**dict.fromkeys(_stat_keys(), 0), "total_tracks": 5, "tracks_with_embeddings": 5}
        rows_shown = rd.doctor_rows(stats, rd.status_snapshot(status), {"scan_state": "done"})
        embeddings_row = next(row for row in rows_shown if row["id"] == "embeddings")
        assert embeddings_row["fix"] == "embeddings_full"
        # The job says so: the index was not updated, and what replaces the embeddings.
        state.poll_task_progress()
        assert_index_not_updated(state, toast, done)
        # The saved index is the one of the earlier provider, and a fresh open says so.
        assert_saved_index_still_mismatched(_isolated, ids)

    def test_the_rebuild_closes_a_pass_only_through_the_engine_s_own_functions(self) -> None:
        """One rule for every regenerate action: the Library sync, the CLI and this fix.

        The engine's two functions go through ``index_rebuild.rebuild_index``, which
        builds and saves the index only when it is complete. This module never builds
        or saves an index by itself, except through that function.
        """
        import inspect

        from plexmix.plex import sync
        from plexmix.ui.states import doctor_state

        source = inspect.getsource(doctor_state.rebuild_embeddings)
        assert "finish_embeddings_pass" in source and "follow_rows_after_stopped_pass" in source
        assert callable(sync.finish_embeddings_pass)
        assert callable(sync.follow_rows_after_stopped_pass)
        assert not hasattr(doctor_state, "index_may_follow_rows")
        assert not hasattr(doctor_state, "rebuild_index_from_rows")
        whole = Path(doctor_state.__file__).read_text(encoding="utf-8")
        assert ".build_index(" not in whole and ".save_index(" not in whole
        assert "skipped_vectors" not in whole and "_vectors_that_fit" not in whole

    @pytest.mark.parametrize("fix", ["regenerate_all_embeddings", "generate_missing_embeddings"])
    def test_embedding_fix_does_not_start_beside_a_regenerate_sync(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock, fix: str
    ) -> None:
        """The Library's "Regenerate" sync writes embeddings and saves the index itself.

        Two writers at once would pay the provider twice for the same tracks and
        save the same index files over each other.
        """
        from plexmix.ui.states import library_state as ls

        library(_isolated)
        before_rows, before_index = embedding_rows(_isolated), index_files(_isolated)
        generator = use_generator(monkeypatch, FakeGenerator())
        assert task_store.start("sync", message="Starting sync...") is not None
        task_store.update("sync", extra={ls.REGENERATE_RUN_KEY: True})
        try:
            run(getattr(harness(), fix)())
        finally:
            task_store.clear("sync")

        done = task_store.get(FIX_JOB)
        assert done is not None and done.status == "failed"
        assert done.message == "A Regenerate sync is running"
        assert done.extra["error_body"] == (
            "It is replacing tags and embeddings. Wait for it to finish, then try again. "
            "No embedding was changed."
        )
        # The provider was not asked, and nothing was written.
        assert generator.calls == 0 and generator.test_texts == []
        assert embedding_rows(_isolated) == before_rows
        assert index_files(_isolated) == before_index

    def test_embedding_fix_runs_beside_a_sync_of_another_mode(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        ids = library(_isolated)
        generator = use_generator(monkeypatch, FakeGenerator())
        assert task_store.start("sync", message="Starting sync...") is not None
        try:
            done = rebuild()
        finally:
            task_store.clear("sync")

        assert done.status == "completed" and generator.embedded == len(ids)

    @pytest.mark.parametrize("saved_index", ["same dimension", "no metadata file"])
    def test_stopped_rebuild_never_shrinks_the_saved_index(
        self,
        _isolated: Path,
        monkeypatch: pytest.MonkeyPatch,
        toast: MagicMock,
        pairs: None,
        saved_index: str,
    ) -> None:
        """Two indexes of another model that the dimension in the metadata does not give away."""
        write_config(_isolated, CUSTOM_EMBED_YAML)
        ids = make_db(_isolated, tracks=5)
        if saved_index == "same dimension":
            seed_embeddings(_isolated, ids, model="cohere")
            save_index(_isolated, ids)
        else:
            seed_embeddings(_isolated, ids, OTHER_MODEL, model="cohere")
            save_index(_isolated, ids, OTHER_MODEL)
            (_isolated / "embeddings.metadata").unlink()  # the index alone tells its dimension
        before_index = index_files(_isolated)
        use_generator(monkeypatch, FakeGenerator(cancel_after_batch=1))
        finish, follow = engine_spies(monkeypatch)

        done = rebuild()

        assert done.status == "cancelled"
        rows = embedding_rows(_isolated)
        assert {key for key in rows if key[1] == "cohere"} == {(tid, "cohere") for tid in ids}
        assert {key for key in rows if key[1] == "custom"} == {(tid, "custom") for tid in ids[:2]}
        # An index of the two new vectors would cover fewer tracks than the saved one.
        finish.assert_not_called()
        follow.assert_called_once()  # offered the rows, and refused: three are in the way
        assert index_files(_isolated) == before_index

    def test_rebuild_moves_an_unreadable_index_aside_and_builds_a_new_one(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        write_config(_isolated, CUSTOM_EMBED_YAML)
        ids = make_db(_isolated, tracks=3)
        seed_embeddings(_isolated, ids)
        (_isolated / "embeddings.index").write_bytes(b"not an index")
        use_generator(monkeypatch, FakeGenerator())

        done = rebuild()

        assert done.status == "completed"
        files = index_files(_isolated)
        moved = [name for name in files if ".unreadable-" in name]
        assert len(moved) == 1 and files[moved[0]] == b"not an index"  # kept, not deleted
        assert sorted(index_metadata(_isolated)["track_ids"]) == ids

    def test_rebuild_stops_before_any_batch_when_the_index_cannot_be_replaced(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        """An unreadable index that cannot be moved aside is never written over."""
        import plexmix.database.vector_index as vector_index_module

        write_config(_isolated, CUSTOM_EMBED_YAML)
        ids = make_db(_isolated, tracks=3)
        seed_embeddings(_isolated, ids)
        index = _isolated / "embeddings.index"
        index.write_bytes(b"not an index")
        before_rows, before_index = embedding_rows(_isolated), index_files(_isolated)
        generator = use_generator(monkeypatch, FakeGenerator())
        monkeypatch.setattr(
            vector_index_module.os, "rename", MagicMock(side_effect=PermissionError(13, "denied"))
        )

        done = rebuild()

        assert done.status == "failed"
        assert done.message == "The search index file couldn't be read or moved aside"
        assert str(index) in done.extra["error_body"]
        assert done.extra["error_body"].endswith(NOTHING_CHANGED)
        assert generator.batches == []  # no track was sent to the provider
        assert embedding_rows(_isolated) == before_rows
        assert index_files(_isolated) == before_index
        # The provider answered its test request: this is not a provider failure.
        assert readiness_store.get("embeddings") is None

    def test_rebuild_work_stays_off_the_event_loop(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock, pairs: None
    ) -> None:
        library(_isolated)
        generator = use_generator(monkeypatch, FakeGenerator())

        assert rebuild().status == "completed"

        # The test request and every batch ran in a worker thread, the check first.
        assert len(generator.test_texts) == 1 and generator.calls == 3
        assert generator.on_main_thread == [False, False, False, False]

    def test_rebuild_of_an_empty_library(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        write_config(_isolated, CUSTOM_EMBED_YAML)
        make_db(_isolated, tracks=0)
        generator = use_generator(monkeypatch, FakeGenerator())

        done = rebuild()

        assert done.status == "completed"
        assert done.extra["result_msg"] == "No tracks available to embed."
        assert generator.batches == []
        assert index_files(_isolated) == {}

    def test_incremental_fix_with_one_vector_size_ends_as_it_always_did(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        """The "Generate embeddings" fix sends no test request and embeds only what is missing.

        With one provider and one vector size the index holds every embedded track and
        the job ends with its success message.
        """
        write_config(_isolated, CUSTOM_EMBED_YAML)
        ids = make_db(_isolated, tracks=5)
        seed_embeddings(_isolated, ids[:2], OLD)  # two tracks hold a row of the same size
        save_index(_isolated, ids[:2])
        generator = FakeGenerator()
        monkeypatch.setattr(
            "plexmix.services.providers.build_embedding_generator_with_reason",
            lambda settings: (generator, None),
        )
        state = harness()

        run(state.generate_missing_embeddings())

        done = task_store.get(FIX_JOB)
        assert done is not None and done.status == "completed"
        assert done.extra["result_msg"] == "Generated embeddings for 3 tracks."
        assert generator.test_texts == []
        assert generator.embedded == 3
        rows = embedding_rows(_isolated)
        assert {key for key in rows if key[1] == "custom"} == {(tid, "custom") for tid in ids}
        assert [rows[(tid, "custom")] for tid in ids] == [OLD, OLD, NEW, NEW, NEW]
        # The index now holds every embedded track, and no mismatch is left.
        from plexmix.database.vector_index import VectorIndex

        opened = VectorIndex(dimension=4, index_path=str(_isolated / "embeddings.index"))
        assert sorted(opened.track_ids) == ids and opened.dimension_mismatch is False
        state.poll_task_progress()
        assert state.fix_errors == {}

    def test_incremental_fix_after_a_provider_change_says_what_is_in_the_way(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        """Two tracks hold only the embedding of another provider: no index is built."""
        write_config(_isolated, CUSTOM_EMBED_YAML)
        ids = make_db(_isolated, tracks=5, embedded=2)  # two tracks hold another model's row
        generator = FakeGenerator()
        monkeypatch.setattr(
            "plexmix.services.providers.build_embedding_generator_with_reason",
            lambda settings: (generator, None),
        )
        state = harness()

        run(state.generate_missing_embeddings())

        done = task_store.get(FIX_JOB)
        assert done is not None and done.status == "failed"
        assert done.message == INDEX_NOT_UPDATED
        body = done.extra["error_body"]
        assert body.startswith("Generated embeddings for 3 tracks. ")
        assert "2 tracks hold an embedding that was made with other embedding settings" in body
        # Nothing mismatches (there is no saved index): Generate embeddings in the
        # Library embeds those two for this provider, or switching back keeps them.
        assert "Generate embeddings" in body
        assert (done.extra["error_action_label"], done.extra["error_action_href"]) == (
            "Switch back",
            "/settings?section=embedding",
        )
        assert generator.embedded == 3
        rows = embedding_rows(_isolated)
        assert {key for key in rows if key[1] == "custom"} == {(tid, "custom") for tid in ids[2:]}
        assert {key for key in rows if key[1] == "openai"} == {(tid, "openai") for tid in ids[:2]}
        assert index_files(_isolated) == {}  # nothing was built, nothing saved
        record = readiness_store.get("embeddings")
        assert record is not None and record.level == rd.Level.READY  # the 3 rows were written
        state.poll_task_progress()
        toast.success.assert_not_called()
        toast.error.assert_called_once_with(INDEX_NOT_UPDATED)
        assert state.row_map["embeddings"]["error_title"] == INDEX_NOT_UPDATED

    def test_incremental_fix_leaves_the_saved_index_of_two_vector_sizes(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        """Rows of two sizes under one model name: the fix saves its rows and not the index.

        An index of the vectors that fit would take the place of the library's index and
        cover one track. The saved files stay as they are, byte for byte, the job is
        no success, and Doctor still reports the mismatch and offers the full rebuild.
        """
        write_config(_isolated, CUSTOM_EMBED_YAML)
        ids = make_db(_isolated, tracks=5)
        seed_embeddings(_isolated, ids[1:], OTHER_MODEL)  # the first track has no row at all
        before_index = save_index(_isolated, ids[1:], OTHER_MODEL)
        generator = FakeGenerator()
        monkeypatch.setattr(
            "plexmix.services.providers.build_embedding_generator_with_reason",
            lambda settings: (generator, None),
        )
        state = loaded(harness(), doctor_total_tracks=5, doctor_tracks_needing_embeddings=1)

        run(state.generate_missing_embeddings())

        done = task_store.get(FIX_JOB)
        assert done is not None and done.status == "failed"
        assert done.message == INDEX_NOT_UPDATED
        assert done.extra["error_body"].startswith("Generated embeddings for 1 track. ")
        assert "4 tracks hold an embedding that was made with other embedding settings" in (
            done.extra["error_body"]
        )
        assert generator.embedded == 1
        # The new row is saved, the others keep theirs, and the index files are untouched.
        assert sizes_of(_isolated) == {ids[0]: 4, ids[1]: 2, ids[2]: 2, ids[3]: 2, ids[4]: 2}
        assert index_files(_isolated) == before_index
        assert_saved_index_still_mismatched(_isolated, ids[1:])
        state.poll_task_progress()
        assert_index_not_updated(state, toast, done)
        toast.error.assert_called_once_with(INDEX_NOT_UPDATED)

        # The next scan still reports the mismatch and still offers the full rebuild.
        run(state.run_health_check())
        row = state.row_map["embeddings"]
        assert row["fix"] == "embeddings_full" and row["fix_label"] == "Rebuild embeddings"
        assert index_status().level == rd.Level.FAILING
        assert index_status().cause == rd.Cause.DIMENSION_MISMATCH

    def test_cancelled_incremental_fix_that_leaves_the_index_is_cancelled_and_says_so(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, toast: MagicMock
    ) -> None:
        write_config(_isolated, CUSTOM_EMBED_YAML)
        ids = make_db(_isolated, tracks=5)
        seed_embeddings(_isolated, ids[1:], OTHER_MODEL)
        before_index = save_index(_isolated, ids[1:], OTHER_MODEL)
        generator = FakeGenerator(cancel_after_batch=1)
        monkeypatch.setattr(
            "plexmix.services.providers.build_embedding_generator_with_reason",
            lambda settings: (generator, None),
        )
        state = harness()

        run(state.generate_missing_embeddings())

        done = task_store.get(FIX_JOB)
        assert done is not None and done.status == "cancelled"
        assert index_files(_isolated) == before_index
        state.poll_task_progress()
        assert_index_not_updated(state, toast, done)
        (message,) = [call.args[0] for call in toast.warning.call_args_list]
        assert message.startswith("Fix cancelled. Generated embeddings for 1 track. ")
        toast.error.assert_not_called()

    @pytest.mark.parametrize("stop", ["cancel", "provider"])
    def test_stopped_rebuild_of_two_vector_sizes_leaves_the_saved_index(
        self,
        _isolated: Path,
        monkeypatch: pytest.MonkeyPatch,
        toast: MagicMock,
        pairs: None,
        stop: str,
    ) -> None:
        """A rebuild that stops after two tracks must not save an index of those two.

        Rows of two sizes sit under one model name. The saved files stay as they are,
        byte for byte; the job is no success and says that the index was not updated;
        the next scan reports the mismatch and offers "Rebuild embeddings" again.
        """
        ids = library_of_two_sizes(_isolated)
        before_rows, before_index = embedding_rows(_isolated), index_files(_isolated)
        fake = (
            FakeGenerator(cancel_after_batch=1)
            if stop == "cancel"
            else FakeGenerator(fail_on_batch=2)
        )
        use_generator(monkeypatch, fake)
        finish, follow = engine_spies(monkeypatch)
        state = loaded(harness(), doctor_total_tracks=5)

        done = rebuild(state)

        assert done.status == ("cancelled" if stop == "cancel" else "failed")
        finish.assert_not_called()
        follow.assert_called_once()  # offered the rows, and refused: three are in the way
        assert sizes_of(_isolated) == {ids[0]: 4, ids[1]: 4, ids[2]: 2, ids[3]: 2, ids[4]: 2}
        assert {key for key in embedding_rows(_isolated)} == set(before_rows)  # none lost
        assert index_files(_isolated) == before_index
        assert_saved_index_still_mismatched(_isolated, ids)
        state.poll_task_progress()
        assert_index_not_updated(state, toast, done)
        if stop == "provider":
            # The provider's own cause leads, and the note says how far the rebuild got.
            assert done.message == CANNOT_CONNECT
            assert "2 of 5 tracks got a new embedding." in done.extra["error_body"]
        else:
            toast.warning.assert_called_once()

        run(state.run_health_check())
        assert state.row_map["embeddings"]["fix"] == "embeddings_full"
        assert index_status().cause == rd.Cause.DIMENSION_MISMATCH

    def test_rebuild_that_reaches_its_end_and_cannot_build_the_index_is_no_success(
        self,
        _isolated: Path,
        monkeypatch: pytest.MonkeyPatch,
        toast: MagicMock,
        pairs: None,
    ) -> None:
        """The provider hands back vectors of another size than it states.

        Every track gets its new row, so the pass reaches its end. The rows cannot go
        into an index of 4 numbers, so none is built: the saved files stay as they are,
        and the job fails and says so, instead of "Rebuilt the search index".
        """
        ids = library(_isolated)
        before_index = index_files(_isolated)
        generator = use_generator(monkeypatch, FakeGenerator())
        monkeypatch.setattr(generator, "_vector", lambda: [0.9, 0.1, 0.0])  # 3 of 4 numbers
        state = loaded(harness(), doctor_total_tracks=5)

        done = rebuild(state)

        assert done.status == "failed" and done.message == INDEX_NOT_UPDATED
        assert done.extra["error_body"].startswith("All 5 tracks got a new embedding. ")
        assert "5 tracks hold an embedding that was made with other embedding settings" in (
            done.extra["error_body"]
        )
        assert generator.embedded == 5 and len(embedding_rows(_isolated)) == len(ids)
        assert index_files(_isolated) == before_index
        record = readiness_store.get("embeddings")
        assert record is not None and record.level == rd.Level.READY  # the provider answered
        state.poll_task_progress()
        toast.success.assert_not_called()
        toast.error.assert_called_once_with(INDEX_NOT_UPDATED)

    def test_rebuild_with_a_short_provider_reply_and_a_track_in_the_way_is_no_success(
        self,
        _isolated: Path,
        monkeypatch: pytest.MonkeyPatch,
        toast: MagicMock,
        pairs: None,
    ) -> None:
        """The provider answers one request with a vector too few, in a library of two sizes.

        Four of the five tracks get their new row, and the fifth still holds its row of
        the earlier size, so no index is built. The job ends as the Library's
        "Generate embeddings" and Doctor's other fixes do: failed, with "The search index
        was not updated", never "completed" with a success toast. The saved index is
        untouched, so the mismatch and the fix that clears it stay in view.
        """
        ids = library_of_two_sizes(_isolated)
        before_index = index_files(_isolated)
        generator = use_generator(monkeypatch, FakeGenerator(short_on_batch=2))
        state = loaded(harness(), doctor_total_tracks=5)

        done = rebuild(state)

        assert generator.embedded == 5 and len(embedding_rows(_isolated)) == len(ids)
        assert done.status == "failed" and done.message == INDEX_NOT_UPDATED
        body = done.extra["error_body"]
        assert body.startswith("4 of 5 tracks got a new embedding. The others keep the embedding ")
        assert "1 track holds an embedding that was made with other embedding settings" in body
        assert sum(1 for size in sizes_of(_isolated).values() if size == 4) == 4
        assert index_files(_isolated) == before_index
        assert_saved_index_still_mismatched(_isolated, ids)
        record = readiness_store.get("embeddings")
        assert record is not None and record.level == rd.Level.READY  # the provider answered
        state.poll_task_progress()
        assert_index_not_updated(state, toast, done)
        toast.error.assert_called_once_with(INDEX_NOT_UPDATED)

        run(state.run_health_check())
        assert state.row_map["embeddings"]["fix"] == "embeddings_full"
        assert index_status().cause == rd.Cause.DIMENSION_MISMATCH

    @pytest.mark.parametrize("replaced", [0, 2])
    def test_rebuild_that_reaches_its_end_replaces_the_index_of_two_vector_sizes(
        self,
        _isolated: Path,
        monkeypatch: pytest.MonkeyPatch,
        toast: MagicMock,
        pairs: None,
        replaced: int,
    ) -> None:
        """The full rebuild is the way out: every track at the new size, the mismatch gone."""
        from plexmix.database.vector_index import VectorIndex

        ids = library_of_two_sizes(_isolated, replaced=replaced)
        generator = use_generator(monkeypatch, FakeGenerator())
        finish, follow = engine_spies(monkeypatch)
        state = loaded(harness(), doctor_total_tracks=5)

        done = rebuild(state)

        assert done.status == "completed"
        assert done.extra["result_msg"] == "Rebuilt the search index with 5 tracks."
        assert generator.embedded == 5
        finish.assert_called_once()
        follow.assert_not_called()
        assert sizes_of(_isolated) == dict.fromkeys(ids, 4)
        assert embedding_rows(_isolated) == {(tid, "custom"): NEW for tid in ids}
        opened = VectorIndex(dimension=4, index_path=str(_isolated / "embeddings.index"))
        assert sorted(opened.track_ids) == ids and opened.dimension_mismatch is False
        assert index_metadata(_isolated)["dimension"] == 4
        state.poll_task_progress()
        toast.success.assert_called_once_with("Rebuilt the search index with 5 tracks.")
        assert index_status().cause != rd.Cause.DIMENSION_MISMATCH
        assert index_status().level == rd.Level.READY

    def test_doctor_state_holds_no_clearing_code(self) -> None:
        """Only the orphan clean-up deletes embedding rows, and no file is deleted."""
        import plexmix.ui.states.doctor_state as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        deletes = [line.strip() for line in source.splitlines() if "DELETE FROM embeddings" in line]
        assert len(deletes) == 1
        assert "WHERE track_id NOT IN (SELECT id FROM tracks)" in deletes[0]
        assert "unlink(" not in source


def _stat_keys() -> tuple[str, ...]:
    from plexmix.ui.states.doctor_state import _STAT_KEYS

    return _STAT_KEYS
