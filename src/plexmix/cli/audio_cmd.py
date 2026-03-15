import typer
from rich.console import Console
from rich.table import Table

from ..config.settings import Settings
from ..database.sqlite_manager import SQLiteManager

audio_app = typer.Typer(name="audio", help="Audio feature analysis")
console = Console()


def _run_audio_analysis(
    db: SQLiteManager,
    settings: Settings,
    force: bool = False,
) -> None:
    """Run audio feature analysis on tracks in the database."""
    from ..services.audio_service import get_analyzable_tracks, analyze_tracks

    tracks = get_analyzable_tracks(db, force=force)

    if not tracks:
        console.print("[green]All eligible tracks already have audio features.[/green]")
        return

    console.print(f"[bold]Analyzing audio features for {len(tracks)} tracks...[/bold]")

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
            task = progress.add_task("Analyzing audio...", total=len(tracks))

            def on_progress(analyzed: int, errors: int, total: int) -> None:
                progress.update(task, completed=analyzed + errors)

            try:
                analyzed, errors = analyze_tracks(
                    db, settings, tracks, progress_callback=on_progress
                )
            except ImportError:
                console.print("[yellow]Essentia not installed. Skipping audio analysis.[/yellow]")
                console.print("Install with: poetry install -E audio")
                return

    except KeyboardInterrupt:
        console.print("\n[yellow]Analysis interrupted.[/yellow]")
        return

    console.print(f"[green]Analyzed {analyzed} tracks ({errors} errors).[/green]")


@audio_app.command("analyze")
def audio_analyze(
    force: bool = typer.Option(False, "--force", help="Re-analyze all tracks"),
) -> None:
    """Analyze audio features for tracks in the library."""
    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()

    with SQLiteManager(str(db_path)) as db:
        _run_audio_analysis(db, settings, force=force)


@audio_app.command("info")
def audio_info() -> None:
    """Show audio analysis statistics."""
    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()

    with SQLiteManager(str(db_path)) as db:
        total_tracks = len(db.get_all_tracks())
        tracks_with_path = len([t for t in db.get_all_tracks() if t.file_path])
        features_count = db.get_audio_features_count()
        tracks_without = len(db.get_tracks_without_audio_features())

        table = Table(title="Audio Analysis Stats")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Total tracks", str(total_tracks))
        table.add_row("Tracks with file path", str(tracks_with_path))
        table.add_row("Tracks analyzed", str(features_count))
        table.add_row("Tracks pending analysis", str(tracks_without))

        pct = f"{features_count / tracks_with_path * 100:.0f}%" if tracks_with_path > 0 else "N/A"
        table.add_row("Analysis coverage", pct)

        console.print(table)
