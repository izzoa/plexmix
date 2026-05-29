"""Sync call-site test for fail-fast AI tagging.

A fatal provider error during the tagging phase must stop cleanly, persist any
partial results, and surface the reason once — without crashing the rest of sync.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from plexmix.ai.errors import FatalProviderError
from plexmix.database.models import Album, Artist, Track
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.plex.sync import SyncEngine


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    manager = SQLiteManager(db_path)
    manager.connect()
    yield manager
    manager.close()
    Path(db_path).unlink(missing_ok=True)


def test_sync_tagging_fatal_error_saves_partial_and_does_not_crash(db):
    from rich.progress import Progress

    artist_id = db.insert_artist(Artist(plex_key="a1", name="Artist"))
    album_id = db.insert_album(Album(plex_key="al1", title="Album", artist_id=artist_id))
    track_id = db.insert_track(
        Track(
            plex_key="t1",
            title="Song",
            artist_id=artist_id,
            album_id=album_id,
            genre="rock",
        )
    )

    engine = SyncEngine(plex_client=MagicMock(), db_manager=db)
    engine.tag_generator = MagicMock()
    engine.tag_generator.generate_tags_batch.side_effect = FatalProviderError(
        "boom",
        user_message="Your API key looks invalid. Re-enter it in Settings.",
        partial_results={
            track_id: {"tags": ["calm"], "environments": ["focus"], "instruments": ["piano"]}
        },
    )

    messages: list[str] = []
    with Progress() as progress:
        task = progress.add_task("tagging", total=1)
        # Must NOT raise — sync should continue past a fatal tagging error.
        engine._generate_tags_for_untagged_tracks(
            progress, task, progress_callback=lambda p, m: messages.append(m)
        )

    saved = {t.id: t for t in db.get_all_tracks()}[track_id]
    assert saved.tags  # partial result was persisted
    assert any("AI tagging stopped" in m for m in messages)  # surfaced to the user once
