"""webui-honest-status-and-first-run, group 6: Doctor health (tasks 6.1–6.4).

Covers the scan state (no health before a scan, one scan at a time, a single
"No library yet" state), rows and verdict from ``readiness.doctor_rows``/``verdict``,
fixes behind a scope confirmation (``start_fix`` → ``confirm_fix``), real causes
for provider failures, fix failures kept on their row, and progress labels.

Reflex is mocked (tests/ui/conftest.py). Nothing here touches the network, the
real keyring or ~/.plexmix: every test runs on a scratch ``PLEXMIX_DATA_DIR`` with
its own config.yaml, the keyring lookup is patched out, providers are fakes, and
the readiness and task stores are cleared around each test.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sqlite3
import time
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
