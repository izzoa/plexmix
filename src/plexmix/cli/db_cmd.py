import typer
from rich.console import Console

from ..config.settings import Settings
from ..database.sqlite_manager import SQLiteManager

db_app = typer.Typer(name="db", help="Database management")
console = Console()


@db_app.command("info")
def db_info() -> None:
    """Show database information and statistics."""
    settings = Settings()
    db_path = settings.database.get_db_path()
    index_path = settings.database.get_index_path()

    console.print("\n[bold cyan]Database Information[/bold cyan]\n")

    console.print("[bold]File Locations:[/bold]")
    console.print(f"  Database: {db_path}")
    console.print(f"  Embeddings: {index_path}")

    if db_path.exists():
        db_size = db_path.stat().st_size / (1024 * 1024)
        console.print(f"  Database size: {db_size:.2f} MB")
    else:
        console.print("  [yellow]Database file does not exist[/yellow]")

    if index_path.exists():
        index_size = index_path.stat().st_size / (1024 * 1024)
        console.print(f"  Index size: {index_size:.2f} MB")
    else:
        console.print("  [yellow]Embeddings index does not exist[/yellow]")

    if db_path.exists():
        try:
            with SQLiteManager(str(db_path)) as db:
                console.print("\n[bold]Statistics:[/bold]")

                track_count = db.count_tracks()
                embedding_count = db.count_tracks_with_embeddings()
                artist_count = len(db.get_all_artists())
                album_count = len(db.get_all_albums())
                untagged_count = db.count_untagged_tracks()
                playlists = db.get_playlists()

                console.print(f"  Tracks: {track_count:,}")
                console.print(f"  Artists: {artist_count:,}")
                console.print(f"  Albums: {album_count:,}")
                console.print(f"  Embeddings: {embedding_count:,}")
                console.print(f"  Untagged tracks: {untagged_count:,}")
                console.print(f"  Playlists: {len(playlists):,}")

                if track_count > 0:
                    embedding_coverage = (embedding_count / track_count) * 100
                    console.print(f"\n  Embedding coverage: {embedding_coverage:.1f}%")

                last_sync = db.get_last_sync_time()
                if last_sync:
                    console.print(f"\n[bold]Last Sync:[/bold] {last_sync}")
                else:
                    console.print("\n[yellow]No sync history found[/yellow]")

        except Exception as e:
            console.print(f"\n[red]Error reading database: {e}[/red]")
    else:
        console.print(
            "\n[yellow]Database not initialized. Run 'plexmix sync' to create it.[/yellow]"
        )

    console.print()


@db_app.command("reset")
def db_reset(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
    backup: bool = typer.Option(True, "--backup/--no-backup", help="Create backup before reset"),
) -> None:
    """
    Reset the PlexMix database and embeddings.

    This will delete all synced data, embeddings, playlists, and tags.
    Your Plex library and configuration will not be affected.
    """
    settings = Settings()
    db_path = settings.database.get_db_path()
    index_path = settings.database.get_index_path()

    console.print("\n[bold red]⚠️  Database Reset[/bold red]\n")
    console.print("This will permanently delete:")
    console.print("  • SQLite database:", str(db_path))
    console.print("  • FAISS embeddings index:", str(index_path))
    console.print("\n[yellow]What will be lost:[/yellow]")
    console.print("  • All synced music metadata from Plex")
    console.print("  • AI-generated embeddings for tracks")
    console.print("  • User-applied tags (moods, environments, instruments)")
    console.print("  • Playlist history")
    console.print("  • Sync history")
    console.print("\n[green]What will be preserved:[/green]")
    console.print("  • Your actual music files on Plex server")
    console.print("  • Plex server metadata")
    console.print("  • PlexMix configuration (.env and config.yaml)")
    console.print("  • API keys")

    if db_path.exists() or index_path.exists():
        if db_path.exists():
            try:
                with SQLiteManager(str(db_path)) as db:
                    track_count = db.count_tracks()
                    embedding_count = db.count_tracks_with_embeddings()
                    console.print("\n[dim]Current database contains:[/dim]")
                    console.print(f"  • {track_count:,} tracks")
                    console.print(f"  • {embedding_count:,} embeddings")
            except Exception:
                pass
    else:
        console.print("\n[yellow]Database files don't exist. Nothing to reset.[/yellow]")
        raise typer.Exit(0)

    if not force:
        console.print()
        confirm = typer.confirm("Are you sure you want to reset the database?", default=False)
        if not confirm:
            console.print("[yellow]Reset cancelled.[/yellow]")
            raise typer.Exit(0)

    backup_dir = None
    if backup:
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = db_path.parent / "backups" / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)

        console.print(f"\n[cyan]Creating backup in {backup_dir}...[/cyan]")

        if db_path.exists():
            import shutil

            backup_db = backup_dir / db_path.name
            shutil.copy2(db_path, backup_db)
            console.print(f"  ✓ Database backed up to {backup_db}")

        if index_path.exists():
            import shutil

            backup_index = backup_dir / index_path.name
            shutil.copy2(index_path, backup_index)
            console.print(f"  ✓ Embeddings backed up to {backup_index}")

    console.print("\n[cyan]Deleting database files...[/cyan]")

    if db_path.exists():
        db_path.unlink()
        console.print(f"  ✓ Deleted {db_path}")

    if index_path.exists():
        index_path.unlink()
        console.print(f"  ✓ Deleted {index_path}")

    console.print("\n[bold green]✓ Database reset complete![/bold green]")

    if backup and backup_dir:
        console.print(f"\n[dim]Backup saved to: {backup_dir}[/dim]")

    console.print("\n[yellow]Next steps:[/yellow]")
    console.print("  1. Run 'plexmix sync' to re-sync your Plex library")
    console.print("  2. (Optional) Run 'plexmix tags generate' to re-tag tracks")
