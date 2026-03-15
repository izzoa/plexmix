"""Playlist export/import CLI commands."""

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..config.settings import Settings
from ..database.sqlite_manager import SQLiteManager

playlist_app = typer.Typer(name="playlist", help="Playlist management (export/import)")
console = Console()


def _format_duration(ms: int) -> str:
    """Format milliseconds to M:SS or H:MM:SS."""
    total_sec = ms // 1000
    hours, remainder = divmod(total_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@playlist_app.command("export")
def playlist_export(
    playlist_id: int = typer.Argument(..., help="Playlist ID to export"),
    format: str = typer.Option("json", "--format", "-f", help="Output format: json or m3u"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output file (default: stdout)"
    ),
) -> None:
    """Export a playlist to JSON or M3U format."""
    fmt = format.lower()
    if fmt not in ("json", "m3u"):
        console.print(f"[red]Unknown format '{format}'. Use 'json' or 'm3u'.[/red]")
        raise typer.Exit(1)

    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()

    if not db_path.exists():
        console.print("[red]Database not found. Run 'plexmix sync' first.[/red]")
        raise typer.Exit(1)

    with SQLiteManager(str(db_path)) as db:
        playlist = db.get_playlist_by_id(playlist_id)
        if not playlist:
            console.print(f"[red]Playlist {playlist_id} not found.[/red]")
            raise typer.Exit(1)

        tracks = db.get_playlist_tracks(playlist_id)

        if fmt == "json":
            content = _export_json(playlist, tracks)
        else:
            content = _export_m3u(playlist, tracks)

    if output:
        Path(output).write_text(content, encoding="utf-8")
        console.print(f"[green]Exported to {output}[/green]")
    else:
        sys.stdout.write(content)


def _export_json(playlist: "object", tracks: list) -> str:
    """Build JSON export string from a Playlist model and track dicts."""
    from ..database.models import Playlist as PlaylistModel

    assert isinstance(playlist, PlaylistModel)
    data = {
        "plexmix_version": "1",
        "playlist": {
            "name": playlist.name,
            "description": playlist.description,
            "mood_query": playlist.mood_query,
            "created_at": playlist.created_at.isoformat() if playlist.created_at else None,
            "created_by_ai": playlist.created_by_ai,
        },
        "tracks": [
            {
                "title": t.get("title", ""),
                "artist": t.get("artist_name", ""),
                "album": t.get("album_title", ""),
                "genre": t.get("genre", ""),
                "year": t.get("year"),
                "duration_ms": t.get("duration_ms"),
                "plex_key": t.get("plex_key", ""),
                "position": t.get("position", 0),
            }
            for t in tracks
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _export_m3u(playlist: "object", tracks: list) -> str:
    """Build M3U export string. Uses file_path if available, falls back to metadata."""
    from ..database.models import Playlist as PlaylistModel

    assert isinstance(playlist, PlaylistModel)
    lines = ["#EXTM3U", f"#PLAYLIST:{playlist.name or 'PlexMix Playlist'}"]
    for t in tracks:
        dur_sec = (t.get("duration_ms") or 0) // 1000
        artist = t.get("artist_name", "Unknown")
        title = t.get("title", "Unknown")
        lines.append(f"#EXTINF:{dur_sec},{artist} - {title}")

        # Prefer real file path; fall back to a descriptive placeholder
        file_path = t.get("file_path")
        if file_path:
            lines.append(file_path)
        else:
            lines.append(f"{artist} - {title}.mp3")
    lines.append("")  # trailing newline
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@playlist_app.command("import")
def playlist_import(
    file: str = typer.Argument(..., help="File to import (JSON or M3U)"),
    format: Optional[str] = typer.Option(
        None, "--format", "-f", help="Force format: json or m3u (auto-detected from extension)"
    ),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Override playlist name"),
) -> None:
    """Import a playlist from a JSON or M3U file."""
    path = Path(file)
    if not path.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    fmt = format.lower() if format else None
    if fmt is None:
        ext = path.suffix.lower()
        if ext == ".json":
            fmt = "json"
        elif ext in (".m3u", ".m3u8"):
            fmt = "m3u"
        else:
            console.print(
                f"[red]Cannot auto-detect format from '{ext}'. Use --format json or --format m3u.[/red]"
            )
            raise typer.Exit(1)

    content = path.read_text(encoding="utf-8")

    if fmt == "json":
        playlist_name, tracks_meta = _parse_json_import(content)
    else:
        playlist_name, tracks_meta = _parse_m3u_import(content)

    if name:
        playlist_name = name

    if not playlist_name:
        playlist_name = path.stem

    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()

    if not db_path.exists():
        console.print("[red]Database not found. Run 'plexmix sync' first.[/red]")
        raise typer.Exit(1)

    with SQLiteManager(str(db_path)) as db:
        matched_ids, unmatched = _match_tracks(db, tracks_meta)

        if not matched_ids:
            console.print("[red]No tracks matched any entries in the database.[/red]")
            raise typer.Exit(1)

        if unmatched:
            console.print(f"[yellow]Could not match {len(unmatched)} track(s):[/yellow]")
            for u in unmatched[:10]:
                console.print(f"  • {u}")
            if len(unmatched) > 10:
                console.print(f"  ... and {len(unmatched) - 10} more")

        from ..database.models import Playlist as PlaylistModel

        pl = PlaylistModel(name=playlist_name, created_by_ai=False)
        pid = db.insert_playlist(pl)
        db.add_tracks_to_playlist(pid, matched_ids)

    console.print(
        f"[green]Imported playlist '{playlist_name}' with {len(matched_ids)} tracks (ID: {pid})[/green]"
    )


def _parse_json_import(content: str) -> tuple:
    """Parse a PlexMix JSON export. Returns (name, list of track metadata dicts)."""
    data = json.loads(content)
    playlist_info = data.get("playlist", {})
    playlist_name = playlist_info.get("name", "")
    tracks = data.get("tracks", [])
    return playlist_name, tracks


def _parse_m3u_import(content: str) -> tuple:
    """Parse an M3U file. Returns (name, list of track metadata dicts)."""
    lines = content.strip().splitlines()
    playlist_name = ""
    tracks = []
    current_extinf = None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#PLAYLIST:"):
            playlist_name = line[len("#PLAYLIST:") :]
        elif line.startswith("#EXTINF:"):
            # Format: #EXTINF:duration,Artist - Title
            info = line[len("#EXTINF:") :]
            current_extinf = info
        elif line.startswith("#"):
            continue
        else:
            # This is a file path or identifier line
            track_meta: dict = {"file_path": line}
            if current_extinf:
                parts = current_extinf.split(",", 1)
                if len(parts) == 2:
                    display = parts[1]
                    if " - " in display:
                        artist, title = display.split(" - ", 1)
                        track_meta["artist"] = artist.strip()
                        track_meta["title"] = title.strip()
                    else:
                        track_meta["title"] = display.strip()
            current_extinf = None
            tracks.append(track_meta)

    return playlist_name, tracks


def _match_tracks(db: SQLiteManager, tracks_meta: list) -> tuple:
    """Match imported track metadata to database tracks. Returns (matched_ids, unmatched_labels)."""
    matched_ids = []
    unmatched = []

    for tm in tracks_meta:
        track_id = _find_track(db, tm)
        if track_id:
            matched_ids.append(track_id)
        else:
            label = tm.get("title", "") or tm.get("file_path", "unknown")
            artist = tm.get("artist", "")
            if artist:
                label = f"{artist} - {label}"
            unmatched.append(label)

    return matched_ids, unmatched


def _find_track(db: SQLiteManager, meta: dict) -> Optional[int]:
    """Try to find a track in the DB by plex_key, file_path, or title+artist match."""
    # 1. By plex_key (most reliable for PlexMix JSON exports)
    plex_key = meta.get("plex_key")
    if plex_key:
        track = db.get_track_by_plex_key(plex_key)
        if track and track.id is not None:
            return track.id

    # 2. By file_path
    file_path = meta.get("file_path")
    if file_path:
        track = db.get_track_by_file_path(file_path)
        if track and track.id is not None:
            return track.id

    # 3. By title + artist fuzzy match via FTS
    title = meta.get("title", "")
    artist = meta.get("artist", "")
    if title:
        query = title
        if artist:
            query = f"{title} {artist}"
        try:
            results = db.search_tracks_fts(query)
            if results:
                return results[0].id
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@playlist_app.command("list")
def playlist_list() -> None:
    """List all saved playlists."""
    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()

    if not db_path.exists():
        console.print("[yellow]No database found. Run 'plexmix sync' first.[/yellow]")
        raise typer.Exit(0)

    with SQLiteManager(str(db_path)) as db:
        playlists = db.get_playlists()

    if not playlists:
        console.print("[yellow]No playlists found.[/yellow]")
        raise typer.Exit(0)

    table = Table(title="Saved Playlists")
    table.add_column("ID", style="cyan", width=5)
    table.add_column("Name", style="green")
    table.add_column("Tracks", style="blue", width=7)
    table.add_column("Mood/Query", style="magenta")
    table.add_column("Created", style="dim")

    for p in playlists:
        created = p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else ""
        table.add_row(
            str(p.id or ""),
            p.name,
            str(p.track_count),
            p.mood_query or "",
            created,
        )

    console.print(table)
