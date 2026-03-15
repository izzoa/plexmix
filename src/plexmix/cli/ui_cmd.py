import os
import typer
from typing import Optional
from rich.console import Console

console = Console()


def launch_ui(
    host: str = typer.Option(
        os.getenv("PLEXMIX_UI_HOST", "127.0.0.1"),
        help="Host IP address for the backend server",
    ),
    port: int = typer.Option(
        int(os.getenv("PLEXMIX_UI_PORT", "3000")),
        help="Port for the UI frontend",
    ),
    backend_port: int = typer.Option(
        int(os.getenv("PLEXMIX_BACKEND_PORT", "8000")),
        help="Port for the Reflex backend server",
    ),
    api_url: Optional[str] = typer.Option(
        os.getenv("PLEXMIX_API_URL"),
        help="Public backend URL for WebSocket connections (for reverse proxy/Docker port mapping)",
    ),
    prod: bool = typer.Option(
        False, "--prod", help="Run in production mode (disables hot-reloading)"
    ),
) -> None:
    try:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

        import reflex as rx  # noqa: F401

        console.print("[bold green]Launching PlexMix Web UI...[/bold green]")
        console.print(f"Frontend: http://localhost:{port}")
        console.print(f"Backend: http://{host}:{backend_port}")
        if api_url:
            console.print(f"API URL: {api_url}")
        if not prod:
            console.print("[dim]Hot-reloading enabled (dev mode)[/dim]")

        import subprocess

        env = os.environ.copy()
        if api_url:
            env["PLEXMIX_API_URL"] = api_url

        # Allow custom hostnames (reverse proxy / custom domains)
        allowed_hosts = os.getenv("PLEXMIX_ALLOWED_HOSTS", "")
        if allowed_hosts:
            env["__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"] = allowed_hosts

        cmd = [
            sys.executable,
            "-m",
            "reflex",
            "run",
            "--frontend-port",
            str(port),
            "--backend-port",
            str(backend_port),
            "--backend-host",
            host,
        ]
        if prod:
            cmd.extend(["--env", "prod"])

        subprocess.run(cmd, cwd=str(Path(__file__).parent.parent.parent.parent), env=env)

    except ImportError:
        console.print("[red]Reflex is not installed.[/red]")
        console.print("\nTo use the web UI, install PlexMix with UI extras:")
        console.print("  [bold]pip install plexmix[ui][/bold]")
        console.print("or")
        console.print("  [bold]poetry install -E ui[/bold]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Failed to launch UI: {e}[/red]")
        raise typer.Exit(1)
