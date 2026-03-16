"""
Tests for the embedding pipeline: generation → DB storage → FAISS index → search.

Covers:
- EmbeddingGenerator.generate_embedding() with all provider types (mocked)
- Dimension mismatch detection end-to-end
- Embedding → vector index → search pipeline
- Tagging service integration (generate_embeddings_for_tracks, rebuild_vector_index)
- Batch processing edge cases
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from plexmix.database.models import Artist, Album, Track
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.vector_index import VectorIndex
from plexmix.services.tagging_service import (
    build_track_embedding_data,
    generate_embeddings_for_tracks,
    rebuild_vector_index,
)
from plexmix.utils.embeddings import EmbeddingGenerator, create_track_text

DIM = 8  # Small dimension for fast tests


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    """Create a temporary SQLite database with all tables via connect()."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    manager = SQLiteManager(db_path)
    manager.connect()

    yield manager

    manager.close()
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def seeded_db(db):
    """DB with 5 tracks across 2 artists and 2 albums."""
    a1_id = db.insert_artist(Artist(plex_key="a1", name="Miles Davis"))
    a2_id = db.insert_artist(Artist(plex_key="a2", name="John Coltrane"))
    al1_id = db.insert_album(Album(plex_key="al1", title="Kind of Blue", artist_id=a1_id))
    al2_id = db.insert_album(Album(plex_key="al2", title="A Love Supreme", artist_id=a2_id))

    tracks = []
    for i, (title, aid, alid, genre) in enumerate(
        [
            ("So What", a1_id, al1_id, "jazz"),
            ("Blue in Green", a1_id, al1_id, "jazz"),
            ("Freddie Freeloader", a1_id, al1_id, "jazz, blues"),
            ("Acknowledgement", a2_id, al2_id, "jazz"),
            ("Resolution", a2_id, al2_id, "jazz, spiritual"),
        ],
        start=1,
    ):
        tid = db.insert_track(
            Track(
                plex_key=f"t{i}",
                title=title,
                artist_id=aid,
                album_id=alid,
                genre=genre,
                year=1959 if alid == al1_id else 1965,
            )
        )
        tracks.append(tid)

    db._tracks = tracks
    db._artist_ids = (a1_id, a2_id)
    db._album_ids = (al1_id, al2_id)
    return db


def _random_vector(dim: int = DIM) -> list:
    return np.random.randn(dim).astype(np.float32).tolist()


def _mock_generator(dim: int = DIM, provider_name: str = "test") -> MagicMock:
    """Create a mock EmbeddingGenerator returning deterministic vectors."""
    gen = MagicMock(spec=EmbeddingGenerator)
    gen.provider_name = provider_name
    gen.get_dimension.return_value = dim
    gen.generate_embedding.side_effect = lambda text: _random_vector(dim)
    gen.generate_batch_embeddings.side_effect = lambda texts, **kw: [
        _random_vector(dim) for _ in texts
    ]
    return gen


# ---------------------------------------------------------------------------
# 1. EmbeddingGenerator — provider-specific generate_embedding() paths
# ---------------------------------------------------------------------------


class TestGenerateEmbeddingProviders:
    """Test generate_embedding() through each provider with mocked backends."""

    def test_gemini_generate_embedding(self):
        """Gemini provider generates a 3072-dim vector."""
        mock_client = MagicMock()
        mock_embedding = MagicMock()
        mock_embedding.values = [0.1] * 3072
        mock_response = MagicMock()
        mock_response.embeddings = [mock_embedding]
        mock_client.models.embed_content.return_value = mock_response

        with patch("google.genai.Client", return_value=mock_client):
            gen = EmbeddingGenerator(provider="gemini", api_key="test-key")
            result = gen.generate_embedding("test text")

        assert len(result) == 3072
        assert all(isinstance(x, float) for x in result)
        mock_client.models.embed_content.assert_called_once()

    def test_openai_generate_embedding(self):
        """OpenAI provider generates a 1536-dim vector."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_datum = MagicMock()
        mock_datum.embedding = [0.2] * 1536
        mock_response.data = [mock_datum]
        mock_client.embeddings.create.return_value = mock_response

        with patch("openai.OpenAI", return_value=mock_client):
            gen = EmbeddingGenerator(provider="openai", api_key="test-key")
            result = gen.generate_embedding("test text")

        assert len(result) == 1536
        mock_client.embeddings.create.assert_called_once()

    def test_cohere_generate_embedding(self):
        """Cohere provider generates a 1024-dim vector."""
        import sys

        mock_cohere = MagicMock()
        mock_client = MagicMock()
        mock_cohere.ClientV2.return_value = mock_client

        mock_response = MagicMock()
        mock_embedding = MagicMock()
        mock_embedding.float_ = [0.3] * 1024
        mock_response.embeddings = MagicMock()
        mock_response.embeddings.float_ = [[0.3] * 1024]
        mock_client.embed.return_value = mock_response

        sys.modules["cohere"] = mock_cohere
        try:
            gen = EmbeddingGenerator(provider="cohere", api_key="test-key")
            result = gen.generate_embedding("test text")

            assert len(result) == 1024
            mock_client.embed.assert_called_once()
        finally:
            sys.modules.pop("cohere", None)

    def test_custom_generate_embedding(self):
        """Custom provider uses OpenAI-compatible endpoint."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_datum = MagicMock()
        mock_datum.embedding = [0.4] * 768
        mock_response.data = [mock_datum]
        mock_client.embeddings.create.return_value = mock_response

        with patch("openai.OpenAI", return_value=mock_client):
            gen = EmbeddingGenerator(
                provider="custom",
                model="nomic-embed-text",
                custom_endpoint="http://localhost:11434/v1",
                custom_dimension=768,
            )
            result = gen.generate_embedding("test text")

        assert len(result) == 768
        mock_client.embeddings.create.assert_called_once()

    def test_gemini_batch_embeddings(self):
        """Gemini batch returns multiple vectors."""
        mock_client = MagicMock()
        mock_embeddings = [MagicMock(values=[0.1] * 3072) for _ in range(3)]
        mock_response = MagicMock()
        mock_response.embeddings = mock_embeddings
        mock_client.models.embed_content.return_value = mock_response

        with patch("google.genai.Client", return_value=mock_client):
            gen = EmbeddingGenerator(provider="gemini", api_key="test-key")
            results = gen.generate_batch_embeddings(["text1", "text2", "text3"])

        assert len(results) == 3
        assert all(len(v) == 3072 for v in results)

    def test_openai_batch_embeddings(self):
        """OpenAI batch returns multiple vectors."""
        mock_client = MagicMock()
        mock_data = [MagicMock(embedding=[0.2] * 1536) for _ in range(3)]
        mock_response = MagicMock()
        mock_response.data = mock_data
        mock_client.embeddings.create.return_value = mock_response

        with patch("openai.OpenAI", return_value=mock_client):
            gen = EmbeddingGenerator(provider="openai", api_key="test-key")
            results = gen.generate_batch_embeddings(["text1", "text2", "text3"])

        assert len(results) == 3
        assert all(len(v) == 1536 for v in results)


# ---------------------------------------------------------------------------
# 2. Dimension mismatch detection end-to-end
# ---------------------------------------------------------------------------


class TestDimensionMismatch:
    """Test dimension mismatch detection and handling."""

    def test_mismatch_detected_on_provider_change(self, tmp_path):
        """Changing embedding provider dimension flags mismatch on index load."""
        index_path = str(tmp_path / "test.index")

        # Create and save index with dimension 8
        vi1 = VectorIndex(dimension=8)
        embeddings = np.random.randn(5, 8).astype(np.float32).tolist()
        vi1.build_index(embeddings, [1, 2, 3, 4, 5])
        vi1.save_index(index_path)

        # Load with dimension 16 (simulating provider change)
        vi2 = VectorIndex(dimension=16, index_path=index_path)
        assert vi2.dimension_mismatch is True
        assert vi2.loaded_dimension == 8

    def test_no_mismatch_when_dimension_matches(self, tmp_path):
        """Same dimension on save/load does not flag mismatch."""
        index_path = str(tmp_path / "test.index")

        vi1 = VectorIndex(dimension=DIM)
        vi1.build_index(np.random.randn(3, DIM).astype(np.float32).tolist(), [1, 2, 3])
        vi1.save_index(index_path)

        vi2 = VectorIndex(dimension=DIM, index_path=index_path)
        assert vi2.dimension_mismatch is False
        assert vi2.loaded_dimension is None

    def test_mismatch_blocks_search_at_new_dimension(self, tmp_path):
        """A mismatched index raises when querying at the new dimension.

        The dimension validation passes (query matches self.dimension), but
        FAISS rejects the query because the internal index is still at the old
        dimension.
        """
        index_path = str(tmp_path / "test.index")

        vi1 = VectorIndex(dimension=DIM)
        embeddings = np.random.randn(5, DIM).astype(np.float32).tolist()
        vi1.build_index(embeddings, [10, 20, 30, 40, 50])
        vi1.save_index(index_path)

        # Load with different expected dimension
        vi2 = VectorIndex(dimension=DIM * 2, index_path=index_path)
        assert vi2.dimension_mismatch is True

        # Search at new dimension raises because FAISS index is still old dim
        query = np.random.randn(DIM * 2).astype(np.float32).tolist()
        with pytest.raises((AssertionError, ValueError)):
            vi2.search(query, k=3)

    def test_verify_dimension_detects_actual_mismatch(self):
        """EmbeddingGenerator.verify_dimension() catches actual vs expected mismatch."""
        mock_client = MagicMock()
        mock_embedding = MagicMock()
        # Return 1536-dim vector (expected) but claim 3072
        mock_embedding.values = [0.1] * 1536
        mock_response = MagicMock()
        mock_response.embeddings = [mock_embedding]
        mock_client.models.embed_content.return_value = mock_response

        with patch("google.genai.Client", return_value=mock_client):
            gen = EmbeddingGenerator(provider="gemini", api_key="test-key")
            matches, actual = gen.verify_dimension()

        # Gemini expects 3072 but got 1536
        assert matches is False
        assert actual == 1536

    def test_verify_dimension_matches(self):
        """verify_dimension() passes when actual matches expected."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_datum = MagicMock()
        mock_datum.embedding = [0.1] * 1536
        mock_response.data = [mock_datum]
        mock_client.embeddings.create.return_value = mock_response

        with patch("openai.OpenAI", return_value=mock_client):
            gen = EmbeddingGenerator(provider="openai", api_key="test-key")
            matches, actual = gen.verify_dimension()

        assert matches is True
        assert actual == 1536

    def test_full_rebuild_after_mismatch(self, seeded_db, tmp_path):
        """After dimension mismatch, full rebuild creates correct index."""
        index_path = str(tmp_path / "test.index")

        # Create index at old dimension (4)
        old_vi = VectorIndex(dimension=4)
        old_vi.build_index(np.random.randn(3, 4).astype(np.float32).tolist(), [1, 2, 3])
        old_vi.save_index(index_path)

        # Load at new dimension — mismatch detected
        new_vi = VectorIndex(dimension=DIM, index_path=index_path)
        assert new_vi.dimension_mismatch is True

        # Generate embeddings at new dimension and store in DB
        gen = _mock_generator(dim=DIM)
        tracks = seeded_db.get_all_tracks()
        generate_embeddings_for_tracks(seeded_db, gen, tracks)

        # Rebuild index from DB
        rebuild_vi = VectorIndex(dimension=DIM)
        count = rebuild_vector_index(seeded_db, rebuild_vi, index_path)

        assert count == 5
        assert rebuild_vi.dimension_mismatch is False
        assert rebuild_vi.index.ntotal == 5

        # Verify search works at new dimension
        query = _random_vector(DIM)
        results = rebuild_vi.search(query, k=3)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# 3. Embedding → vector index → search pipeline
# ---------------------------------------------------------------------------


class TestEmbeddingSearchPipeline:
    """End-to-end: track → text → embedding → DB → FAISS → search."""

    def test_end_to_end_pipeline(self, seeded_db, tmp_path):
        """Full pipeline: generate embeddings, build index, search for similar."""
        gen = _mock_generator(dim=DIM)
        tracks = seeded_db.get_all_tracks()

        # Step 1: Generate embeddings for all tracks
        count = generate_embeddings_for_tracks(seeded_db, gen, tracks)
        assert count == 5

        # Step 2: Verify embeddings stored in DB
        for track in tracks:
            emb = seeded_db.get_embedding_by_track_id(track.id)
            assert emb is not None
            assert len(emb.vector) == DIM

        # Step 3: Build FAISS index
        index_path = str(tmp_path / "test.index")
        vi = VectorIndex(dimension=DIM)
        rebuild_count = rebuild_vector_index(seeded_db, vi, index_path)
        assert rebuild_count == 5

        # Step 4: Search for similar tracks
        query = seeded_db.get_embedding_by_track_id(tracks[0].id).vector
        results = vi.search(query, k=3)
        assert len(results) == 3
        # The query track itself should be the top result (cosine sim = 1.0)
        assert results[0][0] == tracks[0].id

    def test_search_with_track_id_filter(self, seeded_db, tmp_path):
        """Search respects track_id_filter to exclude certain tracks."""
        gen = _mock_generator(dim=DIM)
        tracks = seeded_db.get_all_tracks()
        generate_embeddings_for_tracks(seeded_db, gen, tracks)

        vi = VectorIndex(dimension=DIM)
        index_path = str(tmp_path / "test.index")
        rebuild_vector_index(seeded_db, vi, index_path)

        # Filter to only tracks 1 and 2
        allowed_ids = [tracks[0].id, tracks[1].id]
        results = vi.search(_random_vector(DIM), k=5, track_id_filter=allowed_ids)
        returned_ids = [r[0] for r in results]
        assert all(tid in allowed_ids for tid in returned_ids)

    def test_incremental_add_to_index(self, seeded_db, tmp_path):
        """Adding new vectors incrementally to existing index."""
        gen = _mock_generator(dim=DIM)
        tracks = seeded_db.get_all_tracks()

        # Generate for first 3 tracks
        generate_embeddings_for_tracks(seeded_db, gen, tracks[:3])

        vi = VectorIndex(dimension=DIM)
        index_path = str(tmp_path / "test.index")
        rebuild_vector_index(seeded_db, vi, index_path)
        assert vi.index.ntotal == 3

        # Generate for remaining 2 tracks
        generate_embeddings_for_tracks(seeded_db, gen, tracks[3:])

        # Incremental add
        new_embeddings = [seeded_db.get_embedding_by_track_id(t.id) for t in tracks[3:]]
        vi.add_vectors(
            [e.vector for e in new_embeddings],
            [e.track_id for e in new_embeddings],
        )
        assert vi.index.ntotal == 5

    def test_update_vectors_deduplicates(self, seeded_db, tmp_path):
        """update_vectors removes old entries before adding new ones."""
        gen = _mock_generator(dim=DIM)
        tracks = seeded_db.get_all_tracks()
        generate_embeddings_for_tracks(seeded_db, gen, tracks)

        vi = VectorIndex(dimension=DIM)
        index_path = str(tmp_path / "test.index")
        rebuild_vector_index(seeded_db, vi, index_path)
        assert vi.index.ntotal == 5

        # Update first 2 tracks with new vectors
        new_vectors = [_random_vector(DIM), _random_vector(DIM)]
        update_ids = [tracks[0].id, tracks[1].id]
        vi.update_vectors(new_vectors, update_ids)

        # Total should still be 5 (removed old + added new)
        assert vi.index.ntotal == 5

    def test_remove_vectors_from_index(self, seeded_db, tmp_path):
        """Removing vectors reduces index size."""
        gen = _mock_generator(dim=DIM)
        tracks = seeded_db.get_all_tracks()
        generate_embeddings_for_tracks(seeded_db, gen, tracks)

        vi = VectorIndex(dimension=DIM)
        index_path = str(tmp_path / "test.index")
        rebuild_vector_index(seeded_db, vi, index_path)

        # Remove 2 tracks
        vi.remove_vectors([tracks[0].id, tracks[1].id])
        assert vi.index.ntotal == 3

        # Removed tracks should not appear in search
        results = vi.search(_random_vector(DIM), k=5)
        returned_ids = [r[0] for r in results]
        assert tracks[0].id not in returned_ids
        assert tracks[1].id not in returned_ids

    def test_save_load_preserves_search(self, seeded_db, tmp_path):
        """Index persisted to disk returns same search results after reload."""
        gen = _mock_generator(dim=DIM)
        tracks = seeded_db.get_all_tracks()
        generate_embeddings_for_tracks(seeded_db, gen, tracks)

        index_path = str(tmp_path / "test.index")
        vi1 = VectorIndex(dimension=DIM)
        rebuild_vector_index(seeded_db, vi1, index_path)

        query = _random_vector(DIM)
        results_before = vi1.search(query, k=3)

        # Reload from disk
        vi2 = VectorIndex(dimension=DIM, index_path=index_path)
        results_after = vi2.search(query, k=3)

        assert [r[0] for r in results_before] == [r[0] for r in results_after]


# ---------------------------------------------------------------------------
# 4. Tagging service integration
# ---------------------------------------------------------------------------


class TestTaggingServiceIntegration:
    """Tests for generate_embeddings_for_tracks and build_track_embedding_data."""

    def test_build_track_embedding_data_resolves_names(self, seeded_db):
        """build_track_embedding_data resolves artist/album names from DB."""
        track = seeded_db.get_all_tracks()[0]
        data = build_track_embedding_data(seeded_db, track)

        assert data["title"] == track.title
        assert data["artist"] == "Miles Davis"
        assert data["album"] == "Kind of Blue"
        assert data["genre"] == "jazz"
        assert data["year"] == 1959

    def test_build_track_embedding_data_missing_artist(self, db):
        """Missing artist resolves to 'Unknown'."""
        # Use a stub Track whose artist_id doesn't exist in the DB.
        # We can't insert such a row with FKs on, so we pass a Track object
        # directly to build_track_embedding_data (it only reads the object, not DB).
        a_id = db.insert_artist(Artist(plex_key="a1", name="Test"))
        al_id = db.insert_album(Album(plex_key="al1", title="Album", artist_id=a_id))
        stub_track = Track(id=9999, plex_key="t1", title="Track", artist_id=99999, album_id=al_id)
        data = build_track_embedding_data(db, stub_track)
        assert data["artist"] == "Unknown"

    def test_build_track_embedding_data_includes_musicbrainz(self, db):
        """MusicBrainz genres and recording type are included."""
        a_id = db.insert_artist(Artist(plex_key="a1", name="Artist"))
        al_id = db.insert_album(Album(plex_key="al1", title="Album", artist_id=a_id))
        track_id = db.insert_track(
            Track(
                plex_key="t1",
                title="Track",
                artist_id=a_id,
                album_id=al_id,
            )
        )
        # MusicBrainz fields are set via update_track_musicbrainz, not insert_track
        db.update_track_musicbrainz(
            track_id,
            recording_id="mb-123",
            genres="shoegaze, dream pop",
            recording_type="live",
        )
        track = db.get_track_by_plex_key("t1")
        data = build_track_embedding_data(db, track)
        assert data["musicbrainz_genres"] == "shoegaze, dream pop"
        assert data["recording_type"] == "live"

    def test_generate_embeddings_stores_in_db(self, seeded_db):
        """generate_embeddings_for_tracks persists embeddings to DB."""
        gen = _mock_generator(dim=DIM)
        tracks = seeded_db.get_all_tracks()

        count = generate_embeddings_for_tracks(seeded_db, gen, tracks)
        assert count == 5

        for track in tracks:
            emb = seeded_db.get_embedding_by_track_id(track.id)
            assert emb is not None
            assert emb.embedding_model == "test"
            assert emb.embedding_dim == DIM
            assert len(emb.vector) == DIM

    def test_generate_embeddings_progress_callback(self, seeded_db):
        """Progress callback is invoked with (generated, total)."""
        gen = _mock_generator(dim=DIM)
        tracks = seeded_db.get_all_tracks()
        callback = MagicMock()

        generate_embeddings_for_tracks(
            seeded_db, gen, tracks, batch_size=2, progress_callback=callback
        )

        assert callback.call_count >= 1
        # Last call should show all generated
        last_args = callback.call_args_list[-1][0]
        assert last_args[0] == 5  # generated
        assert last_args[1] == 5  # total

    def test_generate_embeddings_batching(self, seeded_db):
        """Batch processing processes all tracks across batches with correct sizes."""
        gen = _mock_generator(dim=DIM)
        tracks = seeded_db.get_all_tracks()

        # Use batch_size=2 for 5 tracks → 3 batches
        count = generate_embeddings_for_tracks(seeded_db, gen, tracks, batch_size=2)
        assert count == 5
        # Verify batch calls: 3 calls with [2, 2, 1] texts
        assert gen.generate_batch_embeddings.call_count == 3
        # Verify exact batch sizes passed to the generator
        batch_sizes = [len(call.args[0]) for call in gen.generate_batch_embeddings.call_args_list]
        assert batch_sizes == [2, 2, 1]

    def test_generate_embeddings_empty_list(self, seeded_db):
        """Empty track list returns 0 without calling generator."""
        gen = _mock_generator(dim=DIM)
        count = generate_embeddings_for_tracks(seeded_db, gen, [])
        assert count == 0
        gen.generate_batch_embeddings.assert_not_called()

    def test_rebuild_vector_index_from_db(self, seeded_db, tmp_path):
        """rebuild_vector_index creates FAISS index from all DB embeddings."""
        gen = _mock_generator(dim=DIM)
        tracks = seeded_db.get_all_tracks()
        generate_embeddings_for_tracks(seeded_db, gen, tracks)

        index_path = str(tmp_path / "test.index")
        vi = VectorIndex(dimension=DIM)
        count = rebuild_vector_index(seeded_db, vi, index_path)

        assert count == 5
        assert vi.index.ntotal == 5
        assert Path(index_path).exists()
        assert Path(index_path.replace(".index", ".metadata")).exists()

    def test_rebuild_vector_index_empty_db(self, db, tmp_path):
        """rebuild_vector_index with no embeddings creates empty index."""
        index_path = str(tmp_path / "test.index")
        vi = VectorIndex(dimension=DIM)
        count = rebuild_vector_index(db, vi, index_path)

        assert count == 0
        assert vi.index.ntotal == 0

    def test_embedding_upsert_overwrites(self, seeded_db):
        """Inserting embedding for same track_id overwrites the old one."""
        tracks = seeded_db.get_all_tracks()
        tid = tracks[0].id

        # First write: deterministic vector of 1.0s
        gen1 = _mock_generator(dim=DIM)
        gen1.generate_batch_embeddings.side_effect = lambda texts, **kw: [
            [1.0] * DIM for _ in texts
        ]
        generate_embeddings_for_tracks(seeded_db, gen1, tracks[:1])
        emb1 = seeded_db.get_embedding_by_track_id(tid)
        assert emb1 is not None
        assert emb1.vector == [1.0] * DIM

        # Second write: deterministic vector of 2.0s
        gen2 = _mock_generator(dim=DIM)
        gen2.generate_batch_embeddings.side_effect = lambda texts, **kw: [
            [2.0] * DIM for _ in texts
        ]
        generate_embeddings_for_tracks(seeded_db, gen2, tracks[:1])
        emb2 = seeded_db.get_embedding_by_track_id(tid)
        assert emb2 is not None
        assert emb2.vector == [2.0] * DIM  # Proves overwrite, not no-op

        # Should still be exactly one embedding row for this track
        cursor = seeded_db.get_connection().cursor()
        cursor.execute("SELECT COUNT(*) FROM embeddings WHERE track_id = ?", (tid,))
        assert cursor.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# 5. Embedding text enrichment
# ---------------------------------------------------------------------------


class TestEmbeddingTextEnrichment:
    """Test that embedding text includes all metadata for semantic search."""

    def test_musicbrainz_genres_in_text(self):
        """MusicBrainz genres are appended to embedding text."""
        data = {
            "title": "Track",
            "artist": "Artist",
            "album": "Album",
            "musicbrainz_genres": "shoegaze, dream pop, post-punk",
        }
        text = create_track_text(data)
        assert "shoegaze" in text
        assert "dream pop" in text

    def test_recording_type_in_text(self):
        """Recording type (live/remix/cover) is in embedding text."""
        data = {
            "title": "Track",
            "artist": "Artist",
            "album": "Album",
            "recording_type": "live",
        }
        text = create_track_text(data)
        assert "live" in text

    def test_tags_and_environments_in_text(self):
        """AI-generated tags and environments enrich the text."""
        data = {
            "title": "Chill Song",
            "artist": "Ambient Artist",
            "album": "Relax Album",
            "tags": "ambient, downtempo, atmospheric",
            "environments": "study, relax, focus",
            "instruments": "synthesizer, piano",
        }
        text = create_track_text(data)
        assert "ambient" in text
        assert "study" in text
        assert "synthesizer" in text

    def test_combined_enrichment(self):
        """All enrichment sources appear in a single text."""
        data = {
            "title": "Blue Train",
            "artist": "John Coltrane",
            "album": "Blue Train",
            "genre": "jazz, hard bop",
            "year": 1957,
            "tags": "energetic, bold",
            "environments": "driving",
            "instruments": "saxophone, trumpet",
            "musicbrainz_genres": "hard bop, post-bop",
            "recording_type": "studio",
        }
        audio = {
            "tempo": 140.0,
            "key": "Bb",
            "scale": "major",
            "energy_level": "high",
            "danceability": 0.65,
        }
        text = create_track_text(data, audio)

        # Core metadata
        assert "Blue Train" in text
        assert "John Coltrane" in text
        # Genre
        assert "jazz" in text
        # Year
        assert "1957" in text
        # Tags
        assert "energetic" in text
        # Environment
        assert "driving" in text
        # Instruments
        assert "saxophone" in text
        # MusicBrainz
        assert "hard bop" in text
        # Audio features
        assert "140 bpm" in text
        assert "Bb major" in text
        assert "high energy" in text
