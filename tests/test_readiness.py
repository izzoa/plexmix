"""Tests for the shared readiness model (src/plexmix/services/readiness.py)."""

import ast
import json
import logging
import pickle
import re
import threading
from pathlib import Path

import pytest

from plexmix.config import constants
from plexmix.config.settings import Settings
from plexmix.services import readiness
from plexmix.services.providers import ProviderFailure
from plexmix.services.readiness import (
    Cause,
    Facts,
    Level,
    ReadinessStore,
    can_generate,
    cause_for_plex_error_code,
    collect_facts,
    describe,
    describe_failure,
    describe_job_scope,
    doctor_rows,
    evaluate,
    fingerprint,
    fingerprint_values,
    format_duration,
    generation_blockers,
    is_blocking,
    needs_attention,
    read_index_metadata,
    readiness_store,
    setup_incomplete,
    setup_steps,
    status_snapshot,
    verdict,
)

SENTINEL_TOKEN = "plexTOKEN-SENTINEL-0001"
SENTINEL_OPENAI = "sk-SENTINEL-openai-0001"
SENTINEL_GOOGLE = "AIza-SENTINEL-google-0001"

# The ?section= ids owned by webui-keyboard-and-semantics (SETTINGS_SECTIONS).
SETTINGS_SECTION_IDS = {"plex", "ai", "embedding", "audio", "musicbrainz", "advanced", "appearance"}

_ENV_KEYS = [
    "PLEX_TOKEN",
    "PLEX_URL",
    "PLEX_LIBRARY_NAME",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "COHERE_API_KEY",
    "CUSTOM_AI_API_KEY",
    "CUSTOM_EMBEDDING_API_KEY",
    "AI_DEFAULT_PROVIDER",
    "AI_MODEL",
    "AI_LOCAL_MODE",
    "AI_LOCAL_ENDPOINT",
    "EMBEDDING_DEFAULT_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION",
    "DATABASE_PATH",
    "AUDIO_ENABLED",
    "MUSICBRAINZ_ENABLED",
]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """No keyring, no ~/.plexmix, no leaked env config; a fresh readiness store."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("plexmix.config.credentials._get_keyring", lambda: None)
    readiness_store.clear()
    yield
    readiness_store.clear()


def make_settings(tmp_path: Path, yaml_body: str = "") -> Settings:
    config = tmp_path / "config.yaml"
    config.write_text(
        "database:\n"
        f"  path: {tmp_path / 'plexmix.db'}\n"
        f"  faiss_index_path: {tmp_path / 'embeddings.index'}\n" + yaml_body
    )
    return Settings.load_from_file(str(config))


PLEX_OK = "plex:\n  url: http://plex.local:32400\n  library_name: Music\n"


def facts_for(settings: Settings, secrets: dict | None = None, **overrides) -> Facts:
    """Facts with fingerprints computed exactly like collect_facts does."""
    secrets = secrets or {}
    facts = Facts(**overrides)
    facts.fingerprints = {
        "plex": fingerprint("plex", settings, secrets.get("plex")),
        "ai": fingerprint("ai", settings, secrets.get("ai")),
        "embeddings": fingerprint("embeddings", settings, secrets.get("embeddings")),
        "musicbrainz": fingerprint("musicbrainz", settings, None),
    }
    return facts


def ready_library(**overrides) -> dict:
    base = dict(
        db_exists=True,
        total_tracks=5943,
        untagged_tracks=1,
        embedded_tracks=5943,
        index_exists=True,
        index_dimension=384,
        index_vector_count=5943,
        expected_dimension=384,
        local_deps_available=True,
        plex_token_present=True,
        ai_key_present=True,
        embedding_key_present=True,
    )
    base.update(overrides)
    return base


# ===========================================================================
# 2.1 store, enums, fingerprints, constants
# ===========================================================================


class TestEnumsAndConstants:
    def test_level_values(self):
        assert [lvl.value for lvl in Level] == [
            "not_set_up",
            "unchecked",
            "checking",
            "ready",
            "failing",
            "off",
            "not_installed",
        ]

    def test_cause_values(self):
        assert {c.value for c in Cause} == {
            "missing_config",
            "missing_api_key",
            "missing_dependency",
            "model_load_failed",
            "unreachable",
            "timeout",
            "invalid_credentials",
            "library_not_found",
            "not_synced",
            "index_empty",
            "dimension_mismatch",
            "unknown",
        }

    def test_enums_format_as_their_value(self):
        assert str(Level.READY) == "ready"
        assert f"{Cause.TIMEOUT}" == "timeout"

    def test_blocking_causes(self):
        for cause in (
            Cause.MISSING_CONFIG,
            Cause.MISSING_API_KEY,
            Cause.MISSING_DEPENDENCY,
            Cause.NOT_SYNCED,
            Cause.INDEX_EMPTY,
            Cause.DIMENSION_MISMATCH,
        ):
            assert is_blocking(cause)
            assert is_blocking(cause.value)
        for cause in (
            Cause.TIMEOUT,
            Cause.UNREACHABLE,
            Cause.INVALID_CREDENTIALS,
            Cause.MODEL_LOAD_FAILED,
            Cause.LIBRARY_NOT_FOUND,
            Cause.UNKNOWN,
        ):
            assert not is_blocking(cause)
        assert not is_blocking(None)
        assert not is_blocking("")
        assert not is_blocking("nonsense")

    def test_plex_readiness_constants(self):
        assert constants.PLEX_READINESS_TTL_S == 600
        assert constants.PLEX_READINESS_TIMEOUT_S == 5


class TestReadinessStore:
    def test_concurrent_begin_check_returns_true_once(self):
        store = ReadinessStore()
        barrier = threading.Barrier(16)
        results: list[bool] = []
        lock = threading.Lock()

        def claim() -> None:
            barrier.wait()
            got = store.begin_check("plex")
            with lock:
                results.append(got)

        threads = [threading.Thread(target=claim) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(True) == 1
        assert results.count(False) == 15
        assert store.is_checking("plex")
        store.end_check("plex")
        assert not store.is_checking("plex")
        assert store.begin_check("plex") is True

    def test_abandoned_claim_expires(self):
        store = ReadinessStore()
        assert store.begin_check("plex", now=1000.0)
        assert not store.begin_check("plex", now=1010.0)
        assert store.begin_check("plex", now=1000.0 + 121)

    def test_claims_are_per_component(self):
        store = ReadinessStore()
        assert store.begin_check("plex")
        assert store.begin_check("ai")

    def test_record_and_get(self):
        store = ReadinessStore()
        rec = store.record("plex", "ready", fingerprint="fp1", checked_at=10.0)
        assert rec.level == Level.READY and rec.cause is None
        assert store.get("plex") == rec
        assert store.get_current("plex", "fp1") == rec
        assert store.get_current("plex", "fp2") is None

    def test_embedding_alias(self):
        store = ReadinessStore()
        store.record("embedding", Level.READY, fingerprint="x")
        assert store.get("embeddings") is not None

    def test_failing_without_cause_is_unknown(self):
        rec = ReadinessStore().record("ai", Level.FAILING)
        assert rec.cause == Cause.UNKNOWN

    def test_checking_is_not_a_record(self):
        with pytest.raises(ValueError):
            ReadinessStore().record("plex", Level.CHECKING)

    def test_needs_check_uses_ttl_and_fingerprint(self):
        store = ReadinessStore()
        assert store.needs_check("plex", "fp", now=0.0)
        store.record("plex", Level.READY, fingerprint="fp", checked_at=1000.0)
        assert not store.needs_check("plex", "fp", now=1000.0 + 599)
        assert store.needs_check("plex", "fp", now=1000.0 + 600)
        assert store.needs_check("plex", "other-fp", now=1001.0)

    def test_clear_and_discard(self):
        store = ReadinessStore()
        store.record("plex", Level.READY)
        store.record("ai", Level.READY)
        store.begin_check("embeddings")
        store.discard("plex")
        assert store.get("plex") is None and store.get("ai") is not None
        store.clear()
        assert store.get("ai") is None
        assert not store.is_checking("embeddings")

    def test_record_repr_hides_fingerprint(self):
        rec = ReadinessStore().record("plex", Level.READY, fingerprint="deadbeef" * 8)
        assert "deadbeef" not in repr(rec)

    def test_module_singleton(self):
        assert isinstance(readiness_store, ReadinessStore)
        assert readiness.readiness_store is readiness_store


class TestFingerprint:
    def test_changes_with_key_and_never_contains_it(self, tmp_path):
        settings = make_settings(tmp_path, "embedding:\n  default_provider: openai\n")
        fp1 = fingerprint("embeddings", settings, SENTINEL_OPENAI)
        fp2 = fingerprint("embeddings", settings, SENTINEL_OPENAI + "-rotated")
        assert fp1 != fp2
        assert fp1 != fingerprint("embeddings", settings, None)
        for fp in (fp1, fp2):
            assert re.fullmatch(r"[0-9a-f]{64}", fp)
            assert SENTINEL_OPENAI not in fp
            assert "SENTINEL" not in fp

    def test_same_config_same_fingerprint(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK)
        assert fingerprint("plex", settings, SENTINEL_TOKEN) == fingerprint(
            "plex", settings, SENTINEL_TOKEN
        )

    def test_changes_with_non_secret_fields(self, tmp_path):
        a = make_settings(tmp_path, "embedding:\n  default_provider: local\n")
        b = make_settings(
            tmp_path,
            "embedding:\n  default_provider: local\n  model: mixedbread-ai/mxbai-embed-large-v1\n",
        )
        assert fingerprint("embeddings", a, None) != fingerprint("embeddings", b, None)
        p1 = make_settings(tmp_path, PLEX_OK)
        p2 = make_settings(tmp_path, PLEX_OK.replace("Music", "Jazz"))
        assert fingerprint("plex", p1, SENTINEL_TOKEN) != fingerprint("plex", p2, SENTINEL_TOKEN)

    def test_components_differ(self, tmp_path):
        settings = make_settings(tmp_path)
        assert fingerprint("ai", settings, None) != fingerprint("embeddings", settings, None)

    def test_form_values_match_settings(self, tmp_path):
        settings = make_settings(
            tmp_path, "ai:\n  default_provider: anthropic\n  model: claude-sonnet-4-6\n"
        )
        from_form = fingerprint_values(
            "ai", provider="Claude", model="claude-sonnet-4-6 ", endpoint="", secret="k1"
        )
        assert fingerprint("ai", settings, "k1") == from_form
        assert fingerprint_values("plex", url="http://h:32400/") == fingerprint_values(
            "plex", url="http://h:32400"
        )


# ===========================================================================
# 2.2 facts
# ===========================================================================


def _populate_db(db_path: Path) -> None:
    from datetime import datetime

    from plexmix.database.models import Album, Artist, Embedding, SyncHistory, Track
    from plexmix.database.sqlite_manager import SQLiteManager

    with SQLiteManager(str(db_path)) as db:
        artist_id = db.insert_artist(Artist(plex_key="a1", name="Artist"))
        album_id = db.insert_album(Album(plex_key="al1", title="Album", artist_id=artist_id))
        ids = [
            db.insert_track(
                Track(
                    plex_key=f"t{i}",
                    title=f"Song {i}",
                    artist_id=artist_id,
                    album_id=album_id,
                    tags="chill,piano" if i == 0 else None,
                )
            )
            for i in range(3)
        ]
        for track_id in ids[:2]:
            db.insert_embedding(
                Embedding(
                    track_id=track_id, embedding_model="m", embedding_dim=3, vector=[0.1, 0.2, 0.3]
                )
            )
        db.insert_sync_record(SyncHistory(sync_date=datetime(2025, 12, 27, 20, 35, 19)))


class TestCollectFacts:
    def test_missing_db_reports_no_db_and_zero_counts(self, tmp_path, monkeypatch):
        settings = make_settings(tmp_path)
        db_path = settings.database.get_db_path()
        assert not db_path.exists()
        facts = collect_facts(settings)
        assert facts.db_exists is False
        assert facts.db_error is False
        for name in (
            "total_tracks",
            "untagged_tracks",
            "tagged_tracks",
            "embedded_tracks",
            "missing_embeddings",
            "audio_tracks",
            "musicbrainz_tracks",
            "index_dimension",
            "index_vector_count",
        ):
            assert getattr(facts, name) == 0, name
        assert facts.last_sync is None
        assert not db_path.exists(), "collect_facts must never create the database"

    def test_populated_db_counts(self, tmp_path):
        settings = make_settings(tmp_path)
        _populate_db(settings.database.get_db_path())
        meta = settings.database.get_index_path().with_suffix(".metadata")
        with open(meta, "wb") as f:
            pickle.dump({"track_ids": [1, 2], "dimension": 384}, f)
        settings.database.get_index_path().write_bytes(b"faiss")
        facts = collect_facts(settings)
        assert facts.db_exists and not facts.db_error
        assert facts.total_tracks == 3
        assert facts.untagged_tracks == 2
        assert facts.tagged_tracks == 1
        assert facts.embedded_tracks == 2
        assert facts.missing_embeddings == 1
        assert facts.audio_tracks == 0
        assert facts.musicbrainz_tracks == 0
        assert facts.last_sync and facts.last_sync.startswith("2025-12-27")
        assert facts.index_exists and facts.index_dimension == 384
        assert facts.index_vector_count == 2

    def test_dependency_and_flag_facts(self, tmp_path, monkeypatch):
        seen = []

        def fake_available(name):
            seen.append(name)
            return name == "essentia"

        monkeypatch.setattr(readiness, "_module_available", fake_available)
        settings = make_settings(
            tmp_path, "audio:\n  enabled: true\nmusicbrainz:\n  enabled: false\n"
        )
        facts = collect_facts(settings)
        assert set(seen) >= {"sentence_transformers", "essentia"}
        assert facts.local_deps_available is False
        assert facts.essentia_available is True
        assert facts.audio_enabled is True
        assert facts.musicbrainz_enabled is False

    def test_expected_dimension_follows_provider(self, tmp_path):
        local = make_settings(
            tmp_path, "embedding:\n  default_provider: local\n  model: all-MiniLM-L6-v2\n"
        )
        assert collect_facts(local).expected_dimension == 384
        gemini = make_settings(tmp_path, "embedding:\n  default_provider: gemini\n")
        assert collect_facts(gemini).expected_dimension == 3072

    def test_key_presence_and_no_secrets_in_dict(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        monkeypatch.setenv("PLEX_TOKEN", SENTINEL_TOKEN)
        settings = make_settings(
            tmp_path,
            PLEX_OK + "ai:\n  default_provider: openai\nembedding:\n  default_provider: gemini\n",
        )
        facts = collect_facts(settings)
        assert facts.plex_token_present is True
        assert facts.ai_key_present is True
        assert facts.embedding_key_present is False
        assert "provider_keys" not in facts.as_dict()
        assert facts.fingerprints["plex"] == fingerprint("plex", settings, SENTINEL_TOKEN)
        dumped = json.dumps(facts.as_dict())
        assert "fingerprints" not in facts.as_dict()
        for secret in (SENTINEL_OPENAI, SENTINEL_TOKEN):
            assert secret not in dumped
            assert secret not in repr(facts)
            for fp in facts.fingerprints.values():
                assert secret not in fp

    def test_keyless_providers_count_as_present(self, tmp_path):
        settings = make_settings(
            tmp_path,
            "ai:\n  default_provider: local\nembedding:\n  default_provider: local\n",
        )
        facts = collect_facts(settings)
        assert facts.ai_key_present is True
        assert facts.embedding_key_present is True


class TestReadIndexMetadata:
    def test_missing(self, tmp_path):
        assert read_index_metadata(make_settings(tmp_path)) == {
            "exists": 0,
            "dimension": 0,
            "vector_count": 0,
        }

    def test_corrupt_file_does_not_raise(self, tmp_path):
        settings = make_settings(tmp_path)
        settings.database.get_index_path().with_suffix(".metadata").write_bytes(b"not a pickle")
        meta = read_index_metadata(settings)
        assert meta["exists"] == 1 and meta["dimension"] == 0


# ===========================================================================
# 2.3 evaluate / describe
# ===========================================================================


class TestEvaluate:
    def test_keys_present_but_untested_are_unchecked(self, tmp_path):
        settings = make_settings(
            tmp_path,
            PLEX_OK
            + "ai:\n  default_provider: openai\n  model: gpt-5-mini\n"
            + "embedding:\n  default_provider: openai\n  model: text-embedding-3-small\n",
        )
        status = evaluate(settings, facts_for(settings, **ready_library(expected_dimension=1536)))
        ai = status["ai"]
        assert ai.level == Level.UNCHECKED
        assert ai.label == "OpenAI" and ai.model == "gpt-5-mini"
        assert ai.action_label == "Check" and ai.action_href == "/settings?section=ai"
        assert status["embeddings"].level == Level.UNCHECKED
        assert status["plex"].level == Level.UNCHECKED
        snap = status_snapshot(status)
        assert snap["ai"]["state"] == "Set up · not checked"
        assert snap["ai"]["tone"] != "success"
        assert "Openai" not in json.dumps(snap)

    def test_missing_plex_config(self, tmp_path):
        settings = make_settings(tmp_path)
        plex = evaluate(settings, facts_for(settings))["plex"]
        assert plex.level == Level.NOT_SET_UP
        assert plex.cause == Cause.MISSING_CONFIG
        assert plex.action_label == "Set up"
        assert plex.action_href == "/settings?section=plex"
        assert plex.title == "Plex isn't connected yet"

    def test_plex_without_library(self, tmp_path):
        settings = make_settings(tmp_path, "plex:\n  url: http://plex.local:32400\n")
        plex = evaluate(settings, facts_for(settings, plex_token_present=True))["plex"]
        assert plex.level == Level.NOT_SET_UP and plex.cause == Cause.MISSING_CONFIG
        assert "library" in plex.title.lower()

    def test_local_without_deps_is_failing_and_blocking(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        status = evaluate(
            settings, facts_for(settings, **ready_library(local_deps_available=False))
        )
        emb = status["embeddings"]
        assert emb.level == Level.FAILING
        assert emb.cause == Cause.MISSING_DEPENDENCY
        assert emb.blocking is True
        assert ":latest-local" in emb.body and "plexmix[local]" in emb.body
        assert "doesn't include local models" in emb.body
        assert "api key" not in (emb.title + emb.body).lower()
        assert emb.action_href == "/settings?section=embedding"
        assert emb.fix_label == "Switch provider"
        assert emb.label == "Local"
        assert can_generate(status) is False
        assert generation_blockers(status) == ["embeddings"]
        assert needs_attention(status) is True
        assert status_snapshot(status)["embeddings"]["blocking"] == "true"

    def test_matching_record_is_ready(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK)
        facts = facts_for(settings, {"plex": SENTINEL_TOKEN}, **ready_library())
        readiness_store.record(
            "plex", Level.READY, fingerprint=facts.fingerprints["plex"], checked_at=1000.0
        )
        plex = evaluate(settings, facts, now=1001.0)["plex"]
        assert plex.level == Level.READY
        assert plex.checked_at == 1000.0
        assert status_snapshot({"plex": plex})["plex"]["tone"] == "success"

    def test_stale_fingerprint_is_ignored(self, tmp_path):
        settings = make_settings(tmp_path, "embedding:\n  default_provider: openai\n")
        old = fingerprint("embeddings", settings, SENTINEL_OPENAI)
        readiness_store.record("embeddings", Level.READY, fingerprint=old)
        facts = facts_for(settings, {"embeddings": SENTINEL_OPENAI + "-new"}, **ready_library())
        assert evaluate(settings, facts)["embeddings"].level == Level.UNCHECKED

    def test_timeout_record_is_failing_but_not_blocking(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        facts = facts_for(settings, {"plex": SENTINEL_TOKEN}, **ready_library())
        readiness_store.record(
            "plex", Level.FAILING, Cause.TIMEOUT, fingerprint=facts.fingerprints["plex"]
        )
        readiness_store.record(
            "embeddings", Level.FAILING, Cause.TIMEOUT, fingerprint=facts.fingerprints["embeddings"]
        )
        status = evaluate(settings, facts)
        assert status["plex"].level == Level.FAILING
        assert status["plex"].blocking is False
        assert status["plex"].title == "Your Plex server didn't answer in time"
        assert status["embeddings"].level == Level.FAILING
        assert status["embeddings"].blocking is False
        assert can_generate(status) is True

    def test_blocking_kind_from_a_record_never_blocks(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        facts = facts_for(settings, **ready_library())
        readiness_store.record(
            "embeddings",
            Level.FAILING,
            Cause.MISSING_DEPENDENCY,
            fingerprint=facts.fingerprints["embeddings"],
        )
        status = evaluate(settings, facts)
        assert status["embeddings"].level == Level.FAILING
        assert status["embeddings"].blocking is False
        assert can_generate(status) is True

    def test_check_in_flight_shows_checking(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK)
        facts = facts_for(settings, **ready_library())
        readiness_store.record("plex", Level.READY, fingerprint=facts.fingerprints["plex"])
        assert readiness_store.begin_check("plex")
        plex = evaluate(settings, facts)["plex"]
        assert plex.level == Level.CHECKING
        assert plex.action_label == ""

    def test_unreachable_plex_record(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK)
        facts = facts_for(settings, **ready_library())
        readiness_store.record(
            "plex", Level.FAILING, Cause.UNREACHABLE, fingerprint=facts.fingerprints["plex"]
        )
        plex = evaluate(settings, facts)["plex"]
        assert plex.title == "Can't reach your Plex server at http://plex.local:32400"
        assert plex.action_label == "Fix" and plex.action_href == "/settings?section=plex"
        assert status_snapshot({"plex": plex})["plex"]["tone"] == "error"

    def test_dimension_mismatch_names_both_dimensions(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: gemini\n")
        status = evaluate(
            settings,
            facts_for(settings, **ready_library(index_dimension=384, expected_dimension=3072)),
        )
        idx = status["index"]
        assert idx.level == Level.FAILING and idx.cause == Cause.DIMENSION_MISMATCH
        assert "384" in idx.body and "3,072" in idx.body
        assert idx.secondary_label == "Switch back"
        assert idx.secondary_href == "/settings?section=embedding"
        assert idx.fix_label == "Rebuild embeddings"
        assert can_generate(status) is False
        assert needs_attention(status) is True

    def test_index_empty(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        status = evaluate(settings, facts_for(settings, **ready_library(embedded_tracks=0)))
        idx = status["index"]
        assert idx.level == Level.FAILING and idx.cause == Cause.INDEX_EMPTY
        assert "hasn't been built" in idx.title
        assert idx.fix_label == "Build search index"
        assert generation_blockers(status) == ["index"]

    def test_no_database_is_not_synced(self, tmp_path):
        settings = make_settings(tmp_path, "embedding:\n  default_provider: local\n")
        status = evaluate(settings, facts_for(settings, local_deps_available=True))
        assert status["library"].level == Level.NOT_SET_UP
        assert status["library"].cause == Cause.NOT_SYNCED
        assert status["index"].cause == Cause.NOT_SYNCED
        assert status["library"].action_href == "/dashboard"
        assert set(generation_blockers(status)) == {"library", "index"}

    def test_db_error_is_failing(self, tmp_path):
        settings = make_settings(tmp_path)
        lib = evaluate(settings, facts_for(settings, db_exists=True, db_error=True))["library"]
        assert lib.level == Level.FAILING and lib.cause == Cause.UNKNOWN

    def test_optional_features(self, tmp_path):
        settings = make_settings(tmp_path)
        status = evaluate(settings, facts_for(settings, **ready_library()))
        assert status["audio"].level == Level.NOT_INSTALLED
        assert "plexmix[audio]" in status["audio"].body
        assert status["musicbrainz"].level == Level.OFF
        assert status["musicbrainz"].action_label == "Turn on"
        assert status["musicbrainz"].action_href == "/settings?section=musicbrainz"

        status = evaluate(
            settings,
            facts_for(
                settings,
                **ready_library(essentia_available=True, audio_enabled=False),
                musicbrainz_enabled=True,
            ),
        )
        assert status["audio"].level == Level.OFF
        assert status["musicbrainz"].level == Level.UNCHECKED
        status = evaluate(
            settings,
            facts_for(settings, **ready_library(essentia_available=True, audio_enabled=True)),
        )
        assert status["audio"].level == Level.READY

    def test_ai_local_builtin_without_deps(self, tmp_path):
        settings = make_settings(tmp_path, "ai:\n  default_provider: local\n")
        ai = evaluate(settings, facts_for(settings, local_deps_available=False))["ai"]
        assert ai.level == Level.NOT_SET_UP and ai.cause == Cause.MISSING_DEPENDENCY
        assert ":latest-local" in ai.body
        assert ai.action_href == "/settings?section=ai"

    def test_ai_missing_key_does_not_raise_the_badge(self, tmp_path):
        settings = make_settings(
            tmp_path,
            PLEX_OK + "ai:\n  default_provider: openai\nembedding:\n  default_provider: local\n",
        )
        status = evaluate(settings, facts_for(settings, **ready_library(ai_key_present=False)))
        assert status["ai"].level == Level.NOT_SET_UP
        assert status["ai"].cause == Cause.MISSING_API_KEY
        assert status["ai"].title == "No API key for OpenAI"
        assert needs_attention(status) is False
        assert can_generate(status) is True

    def test_badge_false_when_unchecked_and_index_present(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        status = evaluate(settings, facts_for(settings, **ready_library()))
        assert status["plex"].level == Level.UNCHECKED
        assert status["embeddings"].level == Level.UNCHECKED
        assert needs_attention(status) is False
        assert needs_attention(status_snapshot(status)) is False

    def test_claude_alias_label(self, tmp_path):
        settings = make_settings(tmp_path, "ai:\n  default_provider: anthropic\n")
        assert evaluate(settings, facts_for(settings))["ai"].label == "Claude"

    def test_snapshot_is_strings_only_and_secret_free(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv("PLEX_TOKEN", SENTINEL_TOKEN)
        monkeypatch.setenv("GOOGLE_API_KEY", SENTINEL_GOOGLE)
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: gemini\n")
        with caplog.at_level(logging.DEBUG):
            facts = collect_facts(settings)
            readiness_store.record(
                "plex", Level.READY, fingerprint=facts.fingerprints["plex"], checked_at=1.0e9
            )
            snap = status_snapshot(evaluate(settings, facts))
        assert snap["plex"]["level"] == "ready"
        assert snap["plex"]["checked_ts"] == "1000000000"
        for comp, entry in snap.items():
            assert all(isinstance(v, str) for v in entry.values()), comp
        dumped = json.dumps(snap)
        for secret in (SENTINEL_TOKEN, SENTINEL_GOOGLE):
            assert secret not in dumped
            assert secret not in caplog.text
        for fp in facts.fingerprints.values():
            assert fp not in dumped

    def test_snapshot_keys(self, tmp_path):
        settings = make_settings(tmp_path)
        snap = status_snapshot(evaluate(settings, facts_for(settings)))
        assert set(snap) == set(readiness.COMPONENTS)
        assert set(snap["plex"]) == {
            "component",
            "level",
            "cause",
            "state",
            "tone",
            "label",
            "provider",
            "model",
            "detail",
            "title",
            "body",
            "message",
            "action_label",
            "action_href",
            "fix_label",
            "secondary_label",
            "secondary_href",
            "sends_to",
            "checked_at",
            "checked_ts",
            "blocking",
        }


_RAW_CLI_DETAIL = (
    "Plex not configured. Run 'plexmix config init' first. ⚠️ Check your API keys. "
    "Install with: pip install plexmix[local]"
)


def _all_descriptions():
    contexts = {
        "plex": {"url": "http://127.0.0.1:9", "library": "Music"},
        "ai": {"label": "OpenAI", "provider": "openai", "model": "gpt-5-mini"},
        "embeddings": {"label": "Local", "provider": "local", "model": "all-MiniLM-L6-v2"},
        "library": {"total_tracks": 5943},
        "index": {"label": "Gemini", "index_dimension": 384, "expected_dimension": 3072},
        "audio": {},
        "musicbrainz": {},
    }
    for component, ctx in contexts.items():
        for cause in Cause:
            yield component, cause, describe(component, cause, dict(ctx, detail=_RAW_CLI_DETAIL))


class TestDescribe:
    def test_return_keys(self):
        assert set(describe("plex", Cause.UNREACHABLE)) == {
            "title",
            "body",
            "action_label",
            "action_href",
            "secondary_label",
            "secondary_href",
        }

    def test_no_cli_commands_emoji_or_generic_key_advice(self):
        key_causes = {Cause.MISSING_API_KEY, Cause.INVALID_CREDENTIALS}
        emoji = re.compile("[☀-➿️\U0001f000-\U0001faff]")
        for component, cause, out in _all_descriptions():
            text = " ".join(out.values())
            assert "plexmix config" not in text, (component, cause)
            assert "Run:" not in text and "pip install" not in text, (component, cause)
            assert not emoji.search(text), (component, cause)
            assert "Check your API keys" not in text, (component, cause)
            if cause not in key_causes:
                assert "check your api key" not in text.lower(), (component, cause)
            assert out["title"], (component, cause)
            assert out["body"], (component, cause)

    def test_settings_hrefs_use_section_ids(self):
        for component, cause, out in _all_descriptions():
            for key in ("action_href", "secondary_href"):
                href = out[key]
                if href.startswith("/settings"):
                    match = re.fullmatch(r"/settings\?section=([a-z]+)", href)
                    assert match, (component, cause, href)
                    assert match.group(1) in SETTINGS_SECTION_IDS, (component, cause, href)
        assert describe("embeddings", Cause.MISSING_API_KEY)["action_href"] == (
            "/settings?section=embedding"
        )

    def test_section_hrefs_constant(self):
        for href in readiness.SETTINGS_SECTION_HREFS.values():
            assert href.split("=", 1)[1] in SETTINGS_SECTION_IDS

    def test_missing_key_names_provider_and_embeddings_section(self):
        out = describe("embeddings", Cause.MISSING_API_KEY, {"label": "OpenAI"})
        assert out["title"] == "No API key for OpenAI"
        assert out["action_href"] == "/settings?section=embedding"

    def test_local_missing_dependency_names_remedy(self):
        out = describe(
            "embeddings",
            Cause.MISSING_DEPENDENCY,
            {"label": "Local", "provider": "local", "package": "sentence-transformers"},
        )
        assert out["title"] == "Local models aren't installed"
        assert "sentence-transformers" in out["body"]
        assert ":latest-local" in out["body"] and "plexmix[local]" in out["body"]
        assert out["action_label"] == "Switch provider"

    def test_plex_not_configured_uses_ui_words(self):
        out = describe("plex", cause_for_plex_error_code("not_configured"))
        assert out["title"] == "Plex isn't connected yet"
        assert out["action_href"] == "/settings?section=plex"

    def test_not_synced_points_to_the_setup_checklist(self):
        out = describe("library", Cause.NOT_SYNCED)
        assert out["action_label"] == "Finish setup"
        assert out["action_href"] == "/dashboard"

    def test_unknown_uses_cleaned_detail(self):
        out = describe("embeddings", Cause.UNKNOWN, {"label": "Gemini", "detail": "Quota hit."})
        assert out["title"] == "Gemini couldn't start"
        assert out["body"] == "Quota hit."
        out = describe("embeddings", Cause.UNKNOWN, {"detail": "Run: plexmix config init"})
        assert "Run:" not in out["body"] and out["body"]

    def test_plex_error_code_mapping(self):
        assert cause_for_plex_error_code("not_configured") == Cause.MISSING_CONFIG
        assert cause_for_plex_error_code("no_library") == Cause.MISSING_CONFIG
        assert cause_for_plex_error_code("unreachable") == Cause.UNREACHABLE
        assert cause_for_plex_error_code("library_missing") == Cause.LIBRARY_NOT_FOUND
        assert cause_for_plex_error_code(None) == Cause.UNKNOWN

    def test_describe_failure_for_missing_local_package(self):
        failure = ProviderFailure(
            Cause.MISSING_DEPENDENCY,
            "local",
            package="sentence-transformers",
            detail="sentence-transformers not installed. Run: pip install sentence-transformers",
        )
        out = describe_failure("embeddings", failure)
        assert out["title"] == "Local models aren't installed"
        assert "API key" not in out["body"]
        assert "Run:" not in " ".join(out.values())

    def test_describe_failure_uses_display_names(self):
        out = describe_failure("ai", ProviderFailure(Cause.MISSING_API_KEY, "openai"))
        assert out["title"] == "No API key for OpenAI"


class TestNoUiImports:
    def test_readiness_imports_neither_reflex_nor_ui(self):
        source = Path(readiness.__file__).read_text()
        tree = ast.parse(source)
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        assert not any(m == "reflex" or m.startswith("reflex.") for m in modules)
        assert not any(m.startswith("plexmix.ui") for m in modules)


# ===========================================================================
# 2.4 job scope, Doctor rows/verdict, setup steps
# ===========================================================================


class TestJobScope:
    def test_musicbrainz_duration(self):
        scope = describe_job_scope("musicbrainz", 5807, "MusicBrainz", "", True)
        assert "up to about 1 h 37 min" in scope["duration_line"]
        assert "about one lookup per second" in scope["duration_line"]
        assert "cancel" in scope["duration_line"]
        assert "5,807 tracks" in scope["count_line"]

    def test_cloud_tagging_names_provider_and_data(self):
        scope = describe_job_scope("tagging", 59, "OpenAI", "gpt-5-mini", True)
        assert "sent to OpenAI" in scope["privacy_line"]
        assert "titles, artists, albums and genres" in scope["privacy_line"]
        assert scope["provider_line"] == "OpenAI · gpt-5-mini"

    def test_local_runs_on_this_machine(self):
        scope = describe_job_scope("embeddings", 712, "Local", "all-MiniLM-L6-v2", False)
        assert "runs on this machine" in scope["privacy_line"].lower()
        assert "nothing is sent to a cloud provider" in scope["privacy_line"].lower()
        assert "712 tracks" in scope["count_line"]

    def test_tagging_replace_disclosure(self):
        scope = describe_job_scope(
            "tagging", 59, "OpenAI", "gpt-5-mini", True, {"replace_count": 40}
        )
        assert scope["count_line"] == "59 tracks will be tagged."
        assert scope["replace_line"] == "40 existing tag sets will be replaced."
        assert scope["secondary_label"] == "Tag only the 19 untagged tracks"
        assert scope["danger"] == "true"
        none_tagged = describe_job_scope("tagging", 1, "OpenAI", "", True)
        assert none_tagged["count_line"] == "1 track will be tagged."
        assert none_tagged["replace_line"] == "" and none_tagged["secondary_label"] == ""

    def test_no_money_estimates(self):
        for kind in ("tagging", "embeddings", "musicbrainz", "audio", "cleanup"):
            text = " ".join(describe_job_scope(kind, 100, "OpenAI", "m", True).values())
            assert "$" not in text and "cost" not in text.lower()

    def test_keys(self):
        assert set(describe_job_scope("audio", 3)) == {
            "title",
            "count_line",
            "replace_line",
            "provider_line",
            "privacy_line",
            "duration_line",
            "confirm_label",
            "secondary_label",
            "danger",
        }

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (5, "less than a minute"),
            (60, "1 min"),
            (61, "2 min"),
            (3600, "1 h"),
            (5807, "1 h 37 min"),
            (7260, "2 h 1 min"),
        ],
    )
    def test_format_duration(self, seconds, expected):
        assert format_duration(seconds) == expected


def _stats(**overrides) -> dict:
    base = {
        "total_tracks": 5943,
        "tracks_with_embeddings": 5943,
        "orphaned_count": 0,
        "untagged_count": 0,
        "tracks_needing_embeddings": 0,
        "audio_features_count": 0,
        "tracks_without_audio": 0,
        "mb_enriched": 136,
        "mb_without": 0,
    }
    base.update(overrides)
    return base


FLAGS_ALL_ON = {"audio_available": True, "audio_enabled": True, "musicbrainz_enabled": True}


def _library_rows(rows):
    return [r for r in rows if r["group"] == "library"]


class TestDoctorRows:
    def test_row_keys(self):
        rows = doctor_rows(_stats(), {}, FLAGS_ALL_ON)
        assert set(rows[0]) == {
            "id",
            "group",
            "tier",
            "state",
            "state_word",
            "tone",
            "title",
            "count",
            "model",
            "summary",
            "body",
            "why",
            "action_label",
            "action_href",
            "check_label",
            "check_href",
            "secondary_label",
            "secondary_href",
            "fix",
            "fix_label",
        }
        assert [r["id"] for r in rows] == [
            "library",
            "embeddings",
            "orphans",
            "tags",
            "audio",
            "musicbrainz",
        ]

    def test_recommended_fixes_are_not_all_healthy(self):
        rows = doctor_rows(_stats(untagged_count=1, mb_without=5807), {}, FLAGS_ALL_ON)
        result = verdict(rows)
        assert result["title"] != "All systems healthy"
        assert result["state"] == "fixes_available"
        assert result["title"] == "Search is ready"
        assert result["sub"] == "1 recommended fix and 1 optional fix available."
        tags = next(r for r in rows if r["id"] == "tags")
        assert tags["summary"] == "1 track without tags"
        mb = next(r for r in rows if r["id"] == "musicbrainz")
        assert mb["count"] == "5,807" and mb["fix"] == "musicbrainz"

    def test_required_row_failing(self):
        rows = doctor_rows(_stats(tracks_needing_embeddings=1204), {}, FLAGS_ALL_ON)
        result = verdict(rows)
        assert result["state"] == "attention"
        assert result["title"] == "Attention needed"
        assert "1,204 tracks without embeddings" in result["sub"]

    def test_never_all_healthy_with_required_or_recommended_attention(self):
        for field in ("tracks_needing_embeddings", "orphaned_count", "untagged_count"):
            for count in (1, 2, 5943):
                rows = doctor_rows(_stats(**{field: count}), {}, FLAGS_ALL_ON)
                assert "All systems healthy" not in verdict(rows)["title"], (field, count)

    def test_optional_off_does_not_affect_verdict(self):
        rows = doctor_rows(
            _stats(),
            {},
            {"audio_available": False, "audio_enabled": False, "musicbrainz_enabled": False},
        )
        mb = next(r for r in rows if r["id"] == "musicbrainz")
        assert mb["state"] == "off" and mb["state_word"] == "Off"
        assert mb["fix"] == "" and mb["action_href"] == "/settings?section=musicbrainz"
        audio = next(r for r in rows if r["id"] == "audio")
        assert audio["state"] == "not_installed" and audio["fix"] == ""
        assert "plexmix[audio]" in audio["summary"]
        assert verdict(rows)["title"] == "All systems healthy"

    def test_missing_db_is_one_no_database_state(self):
        for stats, flags in ((None, {"scan_state": "no_database"}), (_stats(total_tracks=0), {})):
            rows = doctor_rows(stats, {}, flags)
            lib = _library_rows(rows)
            assert len(lib) == 1
            assert lib[0]["state"] == "no_database"
            assert lib[0]["action_href"] == "/settings?section=plex"
            assert lib[0]["secondary_label"] == "Sync library"
            assert lib[0]["secondary_href"] == "/library"
            assert all(r["state_word"] != "Healthy" for r in rows)
            assert "All tracks" not in json.dumps(rows)
            assert verdict(rows)["state"] == "no_database"

    def test_before_the_scan_everything_is_checking(self):
        rows = doctor_rows(None, {}, {"scan_state": "checking"})
        lib = _library_rows(rows)
        assert len(lib) == 6
        assert all(r["state"] == "checking" for r in lib)
        assert "Healthy" not in json.dumps(rows)
        assert verdict(rows)["title"] == "Checking your library…"
        assert verdict(doctor_rows(None))["state"] == "checking"

    def test_why_it_matters(self):
        rows = doctor_rows(_stats(orphaned_count=3), {}, FLAGS_ALL_ON)
        for row in rows:
            assert row["why"], row["id"]
        orphans = next(r for r in rows if r["id"] == "orphans")
        assert "no longer in your library" in orphans["why"]
        assert "safe" in orphans["why"]
        assert orphans["summary"] == "3 orphaned embeddings to clean up"

    def test_dimension_mismatch_offers_rebuild(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: gemini\n")
        status = evaluate(
            settings,
            facts_for(settings, **ready_library(index_dimension=384, expected_dimension=3072)),
        )
        rows = doctor_rows(_stats(), status, FLAGS_ALL_ON)
        emb = next(r for r in rows if r["id"] == "embeddings")
        assert emb["state"] == "attention" and emb["fix"] == "embeddings_full"

    def test_connection_rows_from_readiness(self, tmp_path):
        settings = make_settings(
            tmp_path, PLEX_OK + "ai:\n  default_provider: openai\n  model: gpt-5-mini\n"
        )
        facts = facts_for(settings, **ready_library())
        readiness_store.record(
            "plex", Level.FAILING, Cause.UNREACHABLE, fingerprint=facts.fingerprints["plex"]
        )
        status = evaluate(settings, facts)
        for form in (status, status_snapshot(status)):
            rows = doctor_rows(_stats(), form, FLAGS_ALL_ON)
            conn = {r["id"]: r for r in rows if r["group"] == "connections"}
            assert list(conn) == ["conn_plex", "conn_ai", "conn_embeddings"]
            plex = conn["conn_plex"]
            assert plex["state"] == "attention" and plex["tier"] == "required"
            assert plex["summary"].startswith("Can't reach your Plex server")
            assert plex["action_label"] == "Fix"
            assert plex["action_href"] == "/settings?section=plex"
            assert plex["check_label"] == "Check"
            ai = conn["conn_ai"]
            assert ai["state"] == "unchecked" and ai["title"] == "AI provider: OpenAI"
            assert ai["model"] == "gpt-5-mini" and ai["state_word"] == "Set up · not checked"
            assert verdict(rows)["state"] == "attention"
            assert "Healthy" not in [r["state_word"] for r in conn.values()]

    def test_unchecked_connections_read_ready_not_healthy(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        status = evaluate(settings, facts_for(settings, **ready_library()))
        rows = doctor_rows(_stats(), status, FLAGS_ALL_ON)
        result = verdict(rows)
        assert result["state"] == "ready"
        assert result["title"] == "Search is ready"


def _status(tmp_path, yaml_body: str, **facts):
    settings = make_settings(tmp_path, yaml_body)
    return evaluate(settings, facts_for(settings, **facts))


class TestSetupSteps:
    def _current(self, steps):
        return [s["id"] for s in steps if s["state"] == "current"]

    def test_fresh_install_connect_plex_is_current(self, tmp_path):
        status = _status(tmp_path, "")
        steps = setup_steps(status, {"total_tracks": 0}, {})
        assert [s["id"] for s in steps] == ["plex", "providers", "sync", "index", "vibe"]
        assert self._current(steps) == ["plex"]
        assert [s["id"] for s in steps if s["primary"] == "true"] == ["plex"]
        assert steps[0]["href"] == "/settings?section=plex"
        assert steps[1]["href"] == "/settings?section=embedding"
        assert setup_incomplete(status) is True

    def test_synced_without_index_build_index_is_current(self, tmp_path):
        status = _status(
            tmp_path,
            PLEX_OK + "embedding:\n  default_provider: local\n",
            **ready_library(embedded_tracks=0),
        )
        steps = setup_steps(
            status_snapshot(status), {"total_tracks": 5943, "missing_embeddings": 5943}
        )
        states = {s["id"]: s["state"] for s in steps}
        assert states["plex"] == "done" and states["sync"] == "done"
        assert self._current(steps) == ["index"]
        index = next(s for s in steps if s["id"] == "index")
        assert index["action"] == "embed_all" and index["href"] == ""
        assert "runs on this machine" in index["note"]
        assert "5,943 tracks need embeddings" in index["desc"]

    def test_complete_setup(self, tmp_path):
        status = _status(
            tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n", **ready_library()
        )
        steps = setup_steps(status, {"total_tracks": 5943})
        assert self._current(steps) == ["vibe"]
        assert setup_incomplete(status) is False

    def test_running_sync_shows_progress(self, tmp_path):
        status = _status(
            tmp_path,
            PLEX_OK + "embedding:\n  default_provider: local\n",
            plex_token_present=True,
            local_deps_available=True,
        )
        steps = setup_steps(
            status, {}, {"sync": {"status": "running", "progress": 42, "message": "Syncing…"}}
        )
        sync = next(s for s in steps if s["id"] == "sync")
        assert sync["state"] == "running" and sync["progress"] == "42"
        assert sync["message"] == "Syncing…" and sync["primary"] == "false"
        assert self._current(steps) == []

    def test_ai_notes(self, tmp_path):
        status = _status(
            tmp_path,
            PLEX_OK + "ai:\n  default_provider: openai\nembedding:\n  default_provider: local\n",
            plex_token_present=True,
            local_deps_available=True,
            ai_key_present=False,
        )
        steps = {s["id"]: s for s in setup_steps(status)}
        assert steps["providers"]["state"] == "done"
        assert "optional" in steps["providers"]["note"]
        assert steps["sync"]["note"] == ""

        status = _status(
            tmp_path,
            PLEX_OK + "ai:\n  default_provider: openai\nembedding:\n  default_provider: local\n",
            plex_token_present=True,
            local_deps_available=True,
            ai_key_present=True,
        )
        steps = {s["id"]: s for s in setup_steps(status)}
        assert steps["sync"]["note"] == (
            "Sync also tags new tracks with OpenAI · track metadata is sent to OpenAI"
        )

    def test_providers_not_done_while_blocked(self, tmp_path):
        status = _status(
            tmp_path,
            PLEX_OK + "embedding:\n  default_provider: local\n",
            plex_token_present=True,
            local_deps_available=False,
        )
        assert self._current(setup_steps(status)) == ["providers"]

    def test_step_values_are_strings(self, tmp_path):
        for step in setup_steps(_status(tmp_path, "")):
            assert all(isinstance(v, str) for v in step.values())


class TestSettingsDeepLinksUseSectionIds:
    """Task 5.6: every Settings link readiness hands out uses a ``?section=`` id the
    Settings page understands (``SETTINGS_SECTIONS`` in ui/utils/a11y.py)."""

    @staticmethod
    def _section_ids() -> set:
        from plexmix.ui.utils.a11y import SETTINGS_SECTIONS

        return set(SETTINGS_SECTIONS)

    def _assert_settings_href(self, href: str, where: object) -> None:
        if not href.startswith("/settings"):
            return
        match = re.fullmatch(r"/settings\?section=([a-z]+)", href)
        assert match, (where, href)
        assert match.group(1) in self._section_ids(), (where, href)

    def test_local_copy_matches_the_ui_contract(self):
        assert SETTINGS_SECTION_IDS == self._section_ids()

    def test_describe_hrefs(self):
        for component, cause, out in _all_descriptions():
            for key in ("action_href", "secondary_href"):
                self._assert_settings_href(out[key], (component, cause, key))

    def test_section_href_constant(self):
        for component, href in readiness.SETTINGS_SECTION_HREFS.items():
            self._assert_settings_href(href, component)
        # The Embeddings component links to the "embedding" section, not "embeddings".
        assert readiness.SETTINGS_SECTION_HREFS["embeddings"] == "/settings?section=embedding"

    def test_evaluated_actions_and_setup_steps(self, tmp_path):
        bodies = [
            "",
            PLEX_OK + "ai:\n  default_provider: openai\nembedding:\n  default_provider: openai\n",
            PLEX_OK + "embedding:\n  default_provider: local\n",
            "musicbrainz:\n  enabled: false\naudio:\n  enabled: false\n",
        ]
        for i, body in enumerate(bodies):
            (tmp_path / str(i)).mkdir()
            status = _status(tmp_path / str(i), body)
            for name, entry in status_snapshot(status).items():
                for key in ("action_href", "secondary_href"):
                    self._assert_settings_href(entry[key], (body, name, key))
            for step in setup_steps(status):
                self._assert_settings_href(step["href"], (body, step["id"]))
            for row in doctor_rows(None, status):
                for key in ("action_href", "check_href", "secondary_href"):
                    self._assert_settings_href(row.get(key, ""), (body, row["id"], key))


class TestStoredTimestampsAreUtc:
    """``sync_history.sync_date`` is naive UTC; the library's checked time must agree
    with the UI's ``stored_utc`` reading (integration fix)."""

    def test_naive_value_is_read_as_utc(self):
        from datetime import datetime, timezone

        expected = datetime(2025, 12, 27, 20, 35, 19, tzinfo=timezone.utc).timestamp()
        assert readiness._parse_ts("2025-12-27 20:35:19") == expected
        assert readiness._parse_ts("2025-12-27T20:35:19.327064") == pytest.approx(
            expected + 0.327064
        )

    def test_aware_values_keep_their_offset(self):
        from datetime import datetime, timezone

        expected = datetime(2025, 12, 27, 20, 35, 19, tzinfo=timezone.utc).timestamp()
        assert readiness._parse_ts("2025-12-27T20:35:19Z") == expected
        assert readiness._parse_ts("2025-12-27T21:35:19+01:00") == expected

    @pytest.mark.parametrize("value", [None, "", "not a date"])
    def test_missing_or_bad_values_give_none(self, value):
        assert readiness._parse_ts(value) is None


# ===========================================================================
# Fix step (review findings)
# ===========================================================================


class _SpoofingProxy:
    """Stands in for Reflex's ``MutableProxy``: wraps a dict and reports its
    ``__class__`` as the wrapped type, so ``typing.Mapping`` checks fail while
    ``collections.abc.Mapping`` checks pass."""

    def __init__(self, wrapped):
        object.__setattr__(self, "_wrapped", wrapped)

    @property  # type: ignore[misc]
    def __class__(self):
        return type(self._wrapped)

    def __getitem__(self, key):
        value = self._wrapped[key]
        return _SpoofingProxy(value) if isinstance(value, dict) else value

    def get(self, key, default=None):
        return self[key] if key in self._wrapped else default

    def __contains__(self, key):
        return key in self._wrapped

    def __iter__(self):
        return iter(self._wrapped)

    def __len__(self):
        return len(self._wrapped)

    def items(self):
        return [(k, self[k]) for k in self._wrapped]


class TestProxiedStateEntries:
    """Readiness helpers read state dicts that Reflex hands over as proxies."""

    def _broken(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        return status_snapshot(
            evaluate(settings, facts_for(settings, **ready_library(local_deps_available=False)))
        )

    def test_blocking_snapshot_reads_the_same_through_a_proxy(self, tmp_path):
        snap = self._broken(tmp_path)
        proxied = _SpoofingProxy(snap)
        assert isinstance(proxied, dict)  # the proxy's spoofed class
        assert can_generate(snap) is False and can_generate(proxied) is False
        assert generation_blockers(proxied) == generation_blockers(snap) == ["embeddings"]
        assert needs_attention(proxied) is True
        assert setup_incomplete(proxied) is True

    def test_complete_snapshot_reads_the_same_through_a_proxy(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        snap = status_snapshot(evaluate(settings, facts_for(settings, **ready_library())))
        proxied = _SpoofingProxy(snap)
        assert setup_incomplete(proxied) is False
        assert can_generate(proxied) is True
        assert needs_attention(proxied) is False
        jobs = _SpoofingProxy({"sync": {"status": "running", "progress": "5", "message": "x"}})
        steps = setup_steps(proxied, {"total_tracks": 5943}, jobs)
        assert [s["state"] for s in steps] == ["done", "done", "done", "done", "current"]


class TestSetupStepsRunningOutOfOrder:
    def test_sync_running_while_providers_is_open(self, tmp_path):
        # Plex set up, embedding provider still Gemini without a key, sync running.
        status = _status(
            tmp_path,
            PLEX_OK + "embedding:\n  default_provider: gemini\n",
            plex_token_present=True,
            embedding_key_present=False,
        )
        jobs = {"sync": {"status": "running", "progress": 7, "message": "Syncing…"}}
        steps = {s["id"]: s for s in setup_steps(status_snapshot(status), {}, jobs)}
        assert steps["plex"]["state"] == "done"
        assert steps["providers"]["state"] == "current"
        assert steps["providers"]["primary"] == "true"
        assert steps["sync"]["state"] == "running"
        assert steps["sync"]["progress"] == "7" and steps["sync"]["message"] == "Syncing…"
        assert steps["sync"]["primary"] == "false"
        assert steps["index"]["state"] == "todo"
        assert [s["id"] for s in steps.values() if s["primary"] == "true"] == ["providers"]

    def test_running_first_open_step_holds_the_current_slot(self, tmp_path):
        status = _status(
            tmp_path,
            PLEX_OK + "embedding:\n  default_provider: local\n",
            plex_token_present=True,
            local_deps_available=True,
        )
        jobs = {"sync": {"status": "running", "progress": 1, "message": ""}}
        states = [s["state"] for s in setup_steps(status, {}, jobs)]
        assert states == ["done", "done", "running", "todo", "todo"]


class TestIndexFileMissing:
    def test_embeddings_without_an_index_file_are_not_ready(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        status = evaluate(settings, facts_for(settings, **ready_library(index_exists=False)))
        idx = status["index"]
        assert idx.level == Level.FAILING and idx.cause == Cause.INDEX_EMPTY
        assert idx.blocking is True
        assert idx.title == "The search index needs rebuilding"
        assert "already have" in idx.body
        assert idx.fix_label == "Build search index" and idx.action_href == "/dashboard"
        snap = status_snapshot(status)
        assert can_generate(snap) is False
        assert needs_attention(snap) is True
        assert setup_incomplete(snap) is True

    def test_metadata_with_no_vectors_is_not_ready(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        status = evaluate(settings, facts_for(settings, **ready_library(index_vector_count=0)))
        assert status["index"].cause == Cause.INDEX_EMPTY

    def test_legacy_index_without_metadata_stays_ready(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        facts = facts_for(settings, **ready_library(index_dimension=0, index_vector_count=0))
        assert evaluate(settings, facts)["index"].level == Level.READY

    def test_collect_facts_checks_the_index_file(self, tmp_path):
        settings = make_settings(tmp_path)
        _populate_db(settings.database.get_db_path())
        meta = settings.database.get_index_path().with_suffix(".metadata")
        with open(meta, "wb") as f:
            pickle.dump({"track_ids": [1, 2], "dimension": 384}, f)
        assert collect_facts(settings).index_exists is False
        settings.database.get_index_path().write_bytes(b"faiss")
        assert collect_facts(settings).index_exists is True

    def test_setup_step_and_doctor_row_point_to_the_rebuild(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        snap = status_snapshot(
            evaluate(settings, facts_for(settings, **ready_library(index_exists=False)))
        )
        steps = {s["id"]: s for s in setup_steps(snap, {"total_tracks": 5943})}
        assert steps["index"]["state"] == "current"
        assert steps["index"]["desc"] == "The search index needs rebuilding."
        rows = {r["id"]: r for r in doctor_rows(_stats(), snap, FLAGS_ALL_ON)}
        emb = rows["embeddings"]
        assert emb["state"] == "attention" and emb["fix"] == ""
        assert emb["action_label"] == "Build search index"
        assert emb["action_href"] == "/dashboard"
        assert verdict(list(rows.values()))["state"] == "attention"

    def test_index_scope_copy(self):
        scope = describe_job_scope("index", 5943, "OpenAI", "text-embedding-3-small", True)
        assert scope["title"] == "Rebuild the search index?"
        assert "5,943 tracks already have" in scope["count_line"]
        assert "No embeddings are generated" in scope["count_line"]
        assert scope["privacy_line"].startswith("Runs on this machine")
        assert scope["confirm_label"] == "Build search index"
        assert scope["danger"] == "false"
        one = describe_job_scope("index", 1)
        assert "1 track already has" in one["count_line"]


class TestMismatchStepCopy:
    def test_mismatch_step_names_the_problem(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        snap = status_snapshot(
            evaluate(
                settings,
                facts_for(settings, **ready_library(index_dimension=1024, expected_dimension=384)),
            )
        )
        steps = {s["id"]: s for s in setup_steps(snap, {"total_tracks": 5943})}
        assert steps["index"]["state"] == "current"
        assert steps["index"]["desc"] == ("The search index doesn't match your embedding provider.")
        idx = snap["index"]
        assert idx["fix_label"] == "Rebuild embeddings" and idx["action_href"] == "/doctor"
        assert idx["secondary_label"] == "Switch back"
        assert idx["secondary_href"] == "/settings?section=embedding"


class TestDoctorNotInstalledFixes:
    def test_local_embeddings_without_the_package(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        snap = status_snapshot(
            evaluate(
                settings,
                facts_for(
                    settings, **ready_library(local_deps_available=False, embedded_tracks=4000)
                ),
            )
        )
        rows = {
            r["id"]: r
            for r in doctor_rows(_stats(tracks_needing_embeddings=1943), snap, FLAGS_ALL_ON)
        }
        emb = rows["embeddings"]
        assert emb["state"] == "not_installed" and emb["state_word"] == "Not installed"
        assert emb["fix"] == "" and emb["fix_label"] == ""
        assert "local models" in emb["summary"]
        assert emb["action_label"] == "Switch provider"
        assert emb["action_href"] == "/settings?section=embedding"
        # The required connection row still makes the verdict honest.
        assert rows["conn_embeddings"]["state"] == "attention"
        assert verdict(list(rows.values()))["state"] == "attention"

    def test_builtin_local_ai_without_the_package(self, tmp_path):
        settings = make_settings(
            tmp_path,
            PLEX_OK + "ai:\n  default_provider: local\nembedding:\n  default_provider: openai\n",
        )
        snap = status_snapshot(
            evaluate(
                settings,
                facts_for(
                    settings,
                    **ready_library(
                        local_deps_available=False,
                        index_dimension=1536,
                        expected_dimension=1536,
                    ),
                ),
            )
        )
        rows = {r["id"]: r for r in doctor_rows(_stats(untagged_count=12), snap, FLAGS_ALL_ON)}
        tags = rows["tags"]
        assert tags["state"] == "not_installed" and tags["fix"] == ""
        assert tags["action_href"] == "/settings?section=ai"
        assert rows["embeddings"]["state"] == "ok"

    def test_fix_buttons_stay_when_the_package_is_there(self, tmp_path):
        settings = make_settings(tmp_path, PLEX_OK + "embedding:\n  default_provider: local\n")
        snap = status_snapshot(evaluate(settings, facts_for(settings, **ready_library())))
        rows = {
            r["id"]: r
            for r in doctor_rows(
                _stats(tracks_needing_embeddings=3, untagged_count=2), snap, FLAGS_ALL_ON
            )
        }
        assert rows["embeddings"]["fix"] == "embeddings_incremental"
        assert rows["tags"]["fix"] == "tags"


class TestDataDestination:
    def _ai(self, tmp_path, body: str):
        settings = make_settings(tmp_path, PLEX_OK + body)
        return evaluate(settings, facts_for(settings, **ready_library()))["ai"]

    def test_local_endpoint_on_another_host_leaves_the_machine(self, tmp_path):
        ai = self._ai(
            tmp_path,
            "ai:\n  default_provider: local\n  local_mode: endpoint\n"
            "  local_endpoint: http://192.168.1.20:11434/v1\n",
        )
        assert ai.sends_to == "192.168.1.20"
        assert readiness.data_destination(ai) == "192.168.1.20"
        assert readiness.data_destination(ai.as_dict()) == "192.168.1.20"
        scope = describe_job_scope(
            "tagging", 5, ai.label, ai.model, True, {"sends_to": readiness.data_destination(ai)}
        )
        assert scope["privacy_line"] == (
            "Track titles, artists, albums and genres are sent to 192.168.1.20."
        )

    def test_loopback_endpoint_stays_on_this_machine(self, tmp_path):
        ai = self._ai(
            tmp_path,
            "ai:\n  default_provider: local\n  local_mode: endpoint\n"
            "  local_endpoint: http://localhost:11434\n",
        )
        assert ai.sends_to == ""

    def test_builtin_local_and_cloud(self, tmp_path):
        assert self._ai(tmp_path, "ai:\n  default_provider: local\n").sends_to == ""
        assert self._ai(tmp_path, "ai:\n  default_provider: openai\n").sends_to == "OpenAI"

    def test_custom_endpoint_names_its_host_only(self, tmp_path):
        ai = self._ai(
            tmp_path,
            "ai:\n  default_provider: custom\n"
            "  custom_endpoint: https://user:pw@llm.example.net:8443/v1\n"
            "  custom_model: m\n",
        )
        assert ai.sends_to == "llm.example.net"
        settings = make_settings(
            tmp_path,
            "embedding:\n  default_provider: custom\n"
            "  custom_endpoint: http://127.0.0.1:8080/v1\n",
        )
        assert readiness.embedding_sends_to(settings) == ""

    def test_entries_without_the_key_fall_back_to_the_provider(self):
        assert readiness.data_destination({"provider": "openai", "label": "OpenAI"}) == "OpenAI"
        assert readiness.data_destination({"provider": "local", "label": "Local"}) == ""
        assert readiness.data_destination(None) == ""

    def test_sync_note_names_the_endpoint_host(self, tmp_path):
        settings = make_settings(
            tmp_path,
            PLEX_OK
            + "ai:\n  default_provider: local\n  local_mode: endpoint\n"
            + "  local_endpoint: http://nas.lan:11434\n"
            + "embedding:\n  default_provider: local\n",
        )
        status = evaluate(
            settings,
            facts_for(settings, plex_token_present=True, local_deps_available=True),
        )
        steps = {s["id"]: s for s in setup_steps(status_snapshot(status))}
        assert steps["sync"]["note"] == (
            "Sync also tags new tracks with Local · track metadata is sent to nas.lan"
        )
        assert steps["index"]["note"].endswith("runs on this machine")


# ===========================================================================
# Carry-over: a model-less provider is named once in its scope confirmation
# ===========================================================================


class TestScopeProviderLine:
    def test_provider_and_model(self):
        assert readiness.scope_provider_text("OpenAI", "gpt-5-mini") == "OpenAI · gpt-5-mini"

    def test_no_model_keeps_the_bare_name(self):
        assert readiness.scope_provider_text("Custom (OpenAI-compatible)", "") == (
            "Custom (OpenAI-compatible)"
        )

    def test_a_provider_the_copy_already_names_gets_no_line(self):
        assert "MusicBrainz" in readiness.SCOPE_COPY_NAMES_PROVIDER
        assert readiness.scope_provider_text("MusicBrainz", "") == ""
        assert readiness.scope_provider_text("MusicBrainz") == ""
        assert readiness.scope_provider_text("", "") == ""

    def test_musicbrainz_scope_names_the_service_once_per_line(self):
        scope = describe_job_scope("musicbrainz", 5807, "MusicBrainz", "", True)
        assert scope["provider_line"] == ""
        assert scope["count_line"] == "5,807 tracks will be looked up on MusicBrainz."
        assert scope["duration_line"].startswith("MusicBrainz allows about one lookup per second")
        text = " ".join(
            line
            for line in (
                scope["count_line"],
                scope["replace_line"],
                scope["provider_line"],
                scope["duration_line"],
                scope["privacy_line"],
            )
            if line
        )
        assert "MusicBrainz MusicBrainz" not in text
        assert text.count("MusicBrainz") == 3  # count, duration and privacy sentences

    def test_scope_provider_line_matches_the_helper(self):
        for label, model in (("OpenAI", "gpt-5-mini"), ("Local", ""), ("MusicBrainz", "")):
            scope = describe_job_scope("tagging", 3, label, model, False)
            assert scope["provider_line"] == readiness.scope_provider_text(label, model)
