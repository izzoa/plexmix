import typer
from rich.console import Console
from rich.table import Table

from ..config.settings import Settings
from ..database.sqlite_manager import SQLiteManager

musicbrainz_app = typer.Typer(name="musicbrainz", help="MusicBrainz metadata enrichment")
console = Console()


def _run_musicbrainz_enrichment(
    db: SQLiteManager,
    settings: Settings,
    force: bool = False,
) -> None:
    """Run MusicBrainz enrichment on tracks in the database."""
    from ..services.musicbrainz_service import get_enrichable_tracks, enrich_tracks

    tracks = get_enrichable_tracks(db, force=force)

    if not tracks:
        console.print("[green]All tracks already have MusicBrainz metadata.[/green]")
        return

    console.print(f"[bold]Enriching {len(tracks)} tracks with MusicBrainz data...[/bold]")

    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
        BarColumn,
        TaskProgressColumn,
        TimeRemainingColumn,
    )

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task("Enriching...", total=len(tracks))

            def on_progress(enriched: int, cached: int, mb_errors: int, total: int) -> None:
                progress.update(task, completed=enriched + cached + mb_errors)

            try:
                enriched, cached, errors = enrich_tracks(
                    db,
                    settings.musicbrainz,
                    tracks,
                    progress_callback=on_progress,
                )
            except ImportError:
                console.print(
                    "[yellow]musicbrainzngs not installed. Run: pip install musicbrainzngs[/yellow]"
                )
                return

    except KeyboardInterrupt:
        console.print("\n[yellow]Enrichment interrupted.[/yellow]")
        return

    console.print(
        f"[green]Enriched {enriched} tracks, {cached} from cache ({errors} errors).[/green]"
    )


@musicbrainz_app.command("enrich")
def musicbrainz_enrich(
    force: bool = typer.Option(False, "--force", help="Re-enrich all tracks"),
) -> None:
    """Enrich tracks with MusicBrainz metadata."""
    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()

    with SQLiteManager(str(db_path)) as db:
        _run_musicbrainz_enrichment(db, settings, force=force)


@musicbrainz_app.command("info")
def musicbrainz_info() -> None:
    """Show MusicBrainz enrichment statistics."""
    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()

    with SQLiteManager(str(db_path)) as db:
        total_tracks = len(db.get_all_tracks())
        enriched_count = db.get_musicbrainz_enrichment_count()
        pending_count = len(db.get_tracks_without_musicbrainz())

        table = Table(title="MusicBrainz Enrichment Stats")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Total tracks", str(total_tracks))
        table.add_row("Tracks enriched", str(enriched_count))
        table.add_row("Tracks pending", str(pending_count))

        pct = f"{enriched_count / total_tracks * 100:.0f}%" if total_tracks > 0 else "N/A"
        table.add_row("Enrichment coverage", pct)

        table.add_row("Confidence threshold", f"{settings.musicbrainz.confidence_threshold}%")
        table.add_row("Contact email", settings.musicbrainz.contact_email or "(not set)")

        console.print(table)


@musicbrainz_app.command("clear-cache")
def musicbrainz_clear_cache() -> None:
    """Clear expired MusicBrainz cache entries."""
    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()

    with SQLiteManager(str(db_path)) as db:
        deleted = db.clear_expired_musicbrainz_cache()
        console.print(f"[green]Cleared {deleted} expired cache entries.[/green]")
