import typer
from typing import Dict, Any
from rich.console import Console

from ..config.settings import Settings
from ..database.sqlite_manager import SQLiteManager
from ..database.vector_index import VectorIndex
from ..services.providers import build_embedding_generator as _build_embedding_generator

console = Console()


def doctor(
    force: bool = typer.Option(False, "--force", help="Force regenerate all tags and embeddings"),
) -> None:
    console.print("[bold]🩺 PlexMix Doctor - Database Health Check[/bold]")

    settings = Settings.load_from_file()
    preferred_provider = settings.ai.default_provider or "gemini"
    db_path = settings.database.get_db_path()

    if force:
        console.print(
            "\n[yellow]⚠️  FORCE MODE: Will delete all tags and embeddings"
            " and regenerate everything[/yellow]"
        )
        if not typer.confirm("Are you sure you want to continue?", default=False):
            console.print("[yellow]Operation cancelled.[/yellow]")
            return

        with SQLiteManager(str(db_path)) as db:
            cursor = db.get_connection().cursor()

            cursor.execute("SELECT COUNT(*) FROM tracks")
            total_tracks = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM embeddings")
            total_embeddings = cursor.fetchone()[0]

            console.print("\n[cyan]Current state:[/cyan]")
            console.print(f"  Total tracks: {total_tracks}")
            console.print(f"  Current embeddings: {total_embeddings}")

            console.print("\n[yellow]Deleting all tags and embeddings...[/yellow]")
            cursor.execute("UPDATE tracks SET tags = NULL, environments = NULL, instruments = NULL")
            cursor.execute("DELETE FROM embeddings")
            db._commit()
            console.print("[green]✓ Deleted all tags and embeddings[/green]")

        from .tags_cmd import tags_generate
        from .embeddings_cmd import embeddings_generate

        console.print("\n[bold]Step 1: Generating tags for all tracks...[/bold]")
        tags_generate(provider=preferred_provider, regenerate_embeddings=False)

        console.print("\n[bold]Step 2: Generating embeddings for all tracks...[/bold]")
        embeddings_generate(regenerate=False)

        console.print("\n[green]✓ Force regeneration complete![/green]")
        return

    with SQLiteManager(str(db_path)) as db:
        cursor = db.get_connection().cursor()

        cursor.execute("SELECT COUNT(*) FROM tracks")
        total_tracks = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT track_id) FROM embeddings")
        tracks_with_embeddings = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*) FROM embeddings
            WHERE track_id NOT IN (SELECT id FROM tracks)
        """
        )
        orphaned_count = cursor.fetchone()[0]

        console.print("\n[cyan]Database Status:[/cyan]")
        console.print(f"  Total tracks: {total_tracks}")
        console.print(f"  Tracks with embeddings: {tracks_with_embeddings}")
        console.print(f"  Orphaned embeddings: {orphaned_count}")

        missing_embeddings = total_tracks - tracks_with_embeddings

        if orphaned_count == 0 and missing_embeddings == 0:
            console.print(
                "\n[green]✓ No orphaned embeddings found. All tracks have embeddings."
                " Database is healthy![/green]"
            )

            if typer.confirm("\nRun a sync to check for deleted tracks in Plex?", default=False):
                from .sync_cmd import sync_incremental

                console.print("\n[bold]Running sync...[/bold]")
                sync_incremental(embeddings=False)
                console.print("\n[green]✓ Sync completed![/green]")
            return

        if orphaned_count == 0 and missing_embeddings > 0:
            console.print(
                f"\n[yellow]⚠ Found {missing_embeddings} tracks without embeddings[/yellow]"
            )

        should_delete_orphaned = orphaned_count > 0
        should_generate_tags = False

        if should_delete_orphaned and typer.confirm(
            f"\nDelete {orphaned_count} orphaned embeddings?", default=True
        ):
            cursor.execute("DELETE FROM embeddings WHERE track_id NOT IN (SELECT id FROM tracks)")
            deleted = cursor.rowcount
            db._commit()
            console.print(f"[green]Deleted {deleted} orphaned embeddings[/green]")
        elif should_delete_orphaned:
            console.print("[yellow]Operation cancelled.[/yellow]")
            return

        # Check for untagged tracks first (tags should be generated before embeddings)
        cursor.execute('SELECT COUNT(*) FROM tracks WHERE tags IS NULL OR tags = ""')
        untagged_count = cursor.fetchone()[0]

        if untagged_count > 0:
            console.print(f"\n[cyan]Found {untagged_count} tracks without tags[/cyan]")
            if typer.confirm(
                "\nGenerate AI tags for untagged tracks first?" " (recommended before embeddings)",
                default=True,
            ):
                should_generate_tags = True
            else:
                console.print(
                    "\n[yellow]Skipping tag generation." " Tags improve embedding quality![/yellow]"
                )

        cursor.execute(
            "SELECT COUNT(*) FROM tracks"
            " WHERE id NOT IN (SELECT DISTINCT track_id FROM embeddings)"
        )
        tracks_needing_embeddings = cursor.fetchone()[0]

        console.print(f"\n[cyan]Tracks needing embeddings: {tracks_needing_embeddings}[/cyan]")

        if tracks_needing_embeddings > 0:
            if typer.confirm("\nRegenerate embeddings now?", default=True):
                console.print("\n[bold]Regenerating embeddings...[/bold]")

                embedding_generator = _build_embedding_generator(settings)
                if not embedding_generator:
                    console.print("[red]API key required for embedding provider.[/red]")
                    console.print("Run: plexmix config init")
                    raise typer.Exit(1)

                index_path = settings.database.get_index_path()
                vector_index = VectorIndex(
                    dimension=embedding_generator.get_dimension(),
                    index_path=str(index_path),
                )

                from ..utils.embeddings import create_track_text
                from ..database.models import Embedding
                from rich.progress import (
                    Progress,
                    SpinnerColumn,
                    TextColumn,
                    BarColumn,
                    TaskProgressColumn,
                    TimeRemainingColumn,
                )

                all_tracks = db.get_all_tracks()
                tracks_to_embed = [
                    t
                    for t in all_tracks
                    if t.id is not None and not db.get_embedding_by_track_id(t.id)
                ]

                try:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        TaskProgressColumn(),
                        TimeRemainingColumn(),
                    ) as progress:
                        task = progress.add_task(
                            "Generating embeddings...", total=len(tracks_to_embed)
                        )

                        batch_size = 50
                        embeddings_saved = 0

                        for i in range(0, len(tracks_to_embed), batch_size):
                            batch_tracks = tracks_to_embed[i : i + batch_size]

                            track_data_list: list[Dict[str, Any]] = []
                            for track in batch_tracks:
                                artist = db.get_artist_by_id(track.artist_id)
                                album = db.get_album_by_id(track.album_id)

                                assert track.id is not None
                                track_data: Dict[str, Any] = {
                                    "id": track.id,
                                    "title": track.title,
                                    "artist": artist.name if artist else "Unknown",
                                    "album": album.title if album else "Unknown",
                                    "genre": track.genre or "",
                                    "year": track.year or "",
                                    "tags": track.tags or "",
                                }
                                track_data_list.append(track_data)

                            texts = [create_track_text(td) for td in track_data_list]
                            embeddings = embedding_generator.generate_batch_embeddings(
                                texts, batch_size=batch_size
                            )

                            for track_data, embedding_vector in zip(track_data_list, embeddings):
                                embedding = Embedding(
                                    track_id=track_data["id"],
                                    embedding_model=embedding_generator.provider_name,
                                    embedding_dim=embedding_generator.get_dimension(),
                                    vector=embedding_vector,
                                )
                                db.insert_embedding(embedding)
                                embeddings_saved += 1
                                progress.update(task, advance=1)

                    all_embeddings = db.get_all_embeddings()
                    track_ids = [emb[0] for emb in all_embeddings]
                    vectors = [emb[1] for emb in all_embeddings]

                    vector_index.build_index(vectors, track_ids)
                    vector_index.save_index(str(index_path))

                    console.print(
                        f"\n[green]✓ Successfully generated"
                        f" {embeddings_saved} embeddings![/green]"
                    )

                except KeyboardInterrupt:
                    console.print(
                        f"\n[yellow]⚠ Interrupted."
                        f" Saved {embeddings_saved} embeddings.[/yellow]"
                    )
                    console.print("[yellow]Run 'plexmix doctor' again to continue.[/yellow]")
                    raise typer.Exit(130)
            else:
                console.print(
                    "\n[yellow]Run 'plexmix sync' later" " to generate embeddings.[/yellow]"
                )

        console.print("\n[cyan]Checking for deleted tracks in Plex...[/cyan]")
        if typer.confirm("\nRun a sync to remove deleted tracks from database?", default=True):
            from .sync_cmd import sync_incremental

            console.print("\n[bold]Running sync...[/bold]")
            sync_incremental(embeddings=False)
            console.print("\n[green]✓ Sync completed![/green]")

    # Generate tags outside the database context
    if should_generate_tags:
        from .tags_cmd import tags_generate

        tags_generate(provider=preferred_provider, regenerate_embeddings=True)
        console.print("\n[green]✓ Tags generated![/green]")
