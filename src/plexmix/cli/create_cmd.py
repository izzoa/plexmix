import typer
from typing import Optional
from rich.console import Console
from rich.table import Table

from ..config.settings import Settings
from ..database.index_rebuild import REGENERATE_COMMAND
from ..database.sqlite_manager import SQLiteManager
from ..playlist.generator import PlaylistGenerator
from ..services.providers import (
    build_embedding_generator_with_reason as _build_embedding_generator_with_reason,
)
from ..services.sync_service import PlexConnectionError, connect_plex, build_vector_index
from ..services.playlist_service import build_generation_filters
from .console_helpers import print_unreadable_index
from .embedding_failure import print_embedding_failure

console = Console()


def create_playlist(
    mood: str = typer.Argument(..., help="Mood description for playlist"),
    limit: Optional[int] = typer.Option(None, help="Number of tracks"),
    name: Optional[str] = typer.Option(None, help="Playlist name"),
    genre: Optional[str] = typer.Option(None, help="Filter by genre"),
    year: Optional[int] = typer.Option(None, help="Filter by year"),
    environment: Optional[str] = typer.Option(
        None,
        help="Filter by environment (work, study, focus, relax, party, workout, sleep, driving, social)",
    ),
    instrument: Optional[str] = typer.Option(
        None,
        help="Filter by instrument (piano, guitar, saxophone, trumpet, drums, bass, synth, vocals, strings, orchestra)",
    ),
    tempo_min: Optional[int] = typer.Option(None, help="Minimum BPM"),
    tempo_max: Optional[int] = typer.Option(None, help="Maximum BPM"),
    energy: Optional[str] = typer.Option(None, help="Energy level: low, medium, high"),
    key: Optional[str] = typer.Option(None, help="Musical key (e.g., C, D, Eb)"),
    danceable: Optional[float] = typer.Option(None, help="Minimum danceability (0-1)"),
    pool_multiplier: Optional[int] = typer.Option(
        None, help="Candidate pool multiplier (default: 25x playlist length)"
    ),
    shuffle: str = typer.Option(
        "similarity",
        "--shuffle",
        "-s",
        help="Track ordering: similarity (default), random, alternating_artists, energy_curve",
    ),
    avoid_recent: int = typer.Option(
        0,
        help="Exclude tracks used in the N most recent playlists (0 = no exclusion)",
    ),
    create_in_plex: bool = typer.Option(True, help="Create playlist in Plex"),
) -> None:
    """
    Create a playlist based on mood using semantic similarity search.

    Playlist generation uses FAISS vector search with pre-computed embeddings.
    No AI provider API key is required for creation (embeddings are generated during sync).
    """
    console.print(f"[bold]Creating playlist for mood: {mood}[/bold]")

    settings = Settings.load_from_file()

    if limit is None:
        limit = typer.prompt("How many tracks?", type=int, default=settings.playlist.default_length)

    db_path = settings.database.get_db_path()

    with SQLiteManager(str(db_path)) as db:
        embedding_generator, failure = _build_embedding_generator_with_reason(settings)
        if not embedding_generator:
            print_embedding_failure(console, failure)
            raise typer.Exit(1)

        vector_index = build_vector_index(settings, embedding_generator)
        print_unreadable_index(console, vector_index)

        # Check for dimension mismatch
        if vector_index.dimension_mismatch:
            console.print("[red]⚠️ Embedding dimension mismatch![/red]")
            console.print(
                f"[yellow]Existing embeddings are {vector_index.loaded_dimension}D but current provider '{settings.embedding.default_provider}' uses {embedding_generator.get_dimension()}D.[/yellow]"
            )
            # A plain sync embeds only tracks that have no embedding of this
            # provider, so it cannot replace vectors of another size. This does.
            console.print("\n[cyan]You must regenerate embeddings to use the new provider:[/cyan]")
            console.print(f"  {REGENERATE_COMMAND}")
            raise typer.Exit(1)

        generator = PlaylistGenerator(db, vector_index, embedding_generator)

        filters = build_generation_filters(
            genre=genre,
            year=year,
            environment=environment,
            instrument=instrument,
            tempo_min=tempo_min,
            tempo_max=tempo_max,
            energy_level=energy,
            key=key,
            danceability_min=danceable,
        )

        tracks = generator.generate(
            mood,
            max_tracks=limit,
            candidate_pool_size=settings.playlist.candidate_pool_size,
            candidate_pool_multiplier=pool_multiplier
            if pool_multiplier is not None
            else settings.playlist.candidate_pool_multiplier,
            filters=filters if filters else None,
            shuffle_mode=shuffle,
            avoid_recent=avoid_recent,
        )

        if not tracks:
            console.print("[yellow]No tracks found matching criteria.[/yellow]")
            raise typer.Exit(1)

        table = Table(title=f"Generated Playlist: {mood}")
        table.add_column("#", style="cyan", width=4)
        table.add_column("Title", style="green")
        table.add_column("Artist", style="blue")
        table.add_column("Album", style="magenta")

        for idx, track in enumerate(tracks, 1):
            table.add_row(str(idx), track["title"], track["artist"], track["album"])

        console.print(table)

        if name is None:
            name = typer.prompt("Playlist name", default=f"{mood} - {len(tracks)} tracks")

        # Validate the name BEFORE any side effect (Plex creation / DB save) so a
        # blank/whitespace name fails cleanly instead of raising a ValidationError
        # after the Plex playlist has already been created.
        name = (name or "").strip()
        if not name:
            console.print("[red]Playlist name cannot be empty.[/red]")
            raise typer.Exit(1)

        track_ids = [t["id"] for t in tracks]

        plex_key = None
        if create_in_plex and settings.plex.library_name:
            try:
                plex_client = connect_plex(settings)
            except PlexConnectionError as e:
                plex_client = None
                console.print(
                    f"[yellow]Could not connect to Plex ({e}); saving locally only.[/yellow]"
                )
            if plex_client:
                plex_rating_keys: list[str] = []
                for track_id in track_ids:
                    db_track = db.get_track_by_id(track_id)
                    if db_track and db_track.plex_key:
                        plex_rating_keys.append(db_track.plex_key)

                playlist = plex_client.create_playlist(
                    name, plex_rating_keys, f"AI-generated playlist: {mood}"
                )
                if playlist:
                    plex_key = str(playlist.ratingKey)
                    console.print("[green]Created playlist in Plex![/green]")
                else:
                    # Don't report success for a Plex creation that failed.
                    console.print(
                        "[yellow]Failed to create the playlist in Plex; saving locally only.[/yellow]"
                    )

        generator.save_playlist(name, track_ids, mood, plex_key=plex_key)
        console.print(f"[green]Playlist '{name}' saved with {len(tracks)} tracks![/green]")
