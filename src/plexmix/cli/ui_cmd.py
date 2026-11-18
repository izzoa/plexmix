"""``plexmix ui``: launch the Reflex web UI.

The production build (bundled, minified, no hot reload) is served by default; ``--dev``
opts into the hot-reloading development server and ``--prod`` is still accepted.
"""

import os
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional

import typer
from rich.console import Console
from rich.markup import escape

console = Console()

# How to get the web UI dependencies. Escaped wherever it is printed, because Rich
# would otherwise swallow ``[ui]`` as a markup tag.
UI_INSTALL_HINT = "pip install 'plexmix[ui]'"
UI_INSTALL_HINT_SOURCE = "poetry install -E ui"

# Shown before ``reflex run --env prod`` compiles and bundles the frontend.
PROD_BUILD_NOTICE = "Building the web UI (first start can take a few minutes)…"
DEV_MODE_NOTICE = "Hot-reloading enabled (dev mode)"

# Job progress, cancellation signals, staged secrets and the session-liveness registry
# all live in the backend process, so the backend must run as exactly one worker and
# must not be pointed at Redis (which is how Reflex scales out to several workers).
SINGLE_WORKER_ENV = {"GRANIAN_WORKERS": "1"}
UNSUPPORTED_REDIS_ENV_VARS = ("REFLEX_REDIS_URL", "REDIS_URL")


def _env_int(name: str, default: int) -> int:
    """Parse an int env var, falling back to *default* on missing/blank/invalid.

    These are evaluated at import time as Typer option defaults, so a malformed
    value (e.g. ``PLEXMIX_UI_PORT=``) previously raised ValueError and crashed the
    entire CLI — not just the ``ui`` command.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _app_dir_candidates(module_file: Path) -> List[Path]:
    """Directories that may hold the Reflex app, in lookup order.

    - ``parents[3]``: a source checkout or the Docker image
      (``<root>/src/plexmix/cli/ui_cmd.py`` → ``<root>``).
    - ``parents[2]``: a wheel install, where ``rxconfig.py`` and ``plexmix_ui/`` are
      installed at the site-packages root (``<site-packages>/plexmix/cli/ui_cmd.py``).
    """
    parents = module_file.resolve().parents
    return [parents[index] for index in (3, 2) if index < len(parents)]


def _is_app_dir(path: Path) -> bool:
    """True when *path* holds ``rxconfig.py`` plus the ``plexmix_ui/`` package."""
    return (path / "rxconfig.py").is_file() and (path / "plexmix_ui").is_dir()


def _resolve_app_dir(module_file: Optional[Path] = None) -> Path:
    """Locate the bundled Reflex app directory for every install layout.

    Checks ``parents[3]`` (source checkout / Docker) and then ``parents[2]`` (wheel:
    site-packages root) of this module for ``rxconfig.py`` plus ``plexmix_ui/``.
    *module_file* defaults to this module's path and exists so tests can supply
    synthetic layouts.

    Raises:
        typer.Exit: (code 1) with an actionable message when no candidate holds the
            app, or when the chosen directory is not writable (Reflex creates
            ``.web/`` and ``.states/`` inside it).
    """
    source = Path(__file__) if module_file is None else module_file
    candidates = _app_dir_candidates(source)
    app_dir = next((candidate for candidate in candidates if _is_app_dir(candidate)), None)

    if app_dir is None:
        searched = ", ".join(str(candidate) for candidate in candidates) or "(none)"
        console.print(
            "[red]Cannot locate the PlexMix web UI app (rxconfig.py and plexmix_ui/ "
            "not found).[/red]"
        )
        console.print(f"[dim]Searched: {escape(searched)}[/dim]")
        console.print("\nInstall (or reinstall) PlexMix with the UI extra:")
        console.print(f"  [bold]{escape(UI_INSTALL_HINT)}[/bold]")
        console.print("or, from a source checkout:")
        console.print(f"  [bold]{escape(UI_INSTALL_HINT_SOURCE)}[/bold]")
        raise typer.Exit(1)

    if not os.access(app_dir, os.W_OK):
        console.print(
            f"[red]The PlexMix web UI directory is not writable: {escape(str(app_dir))}[/red]"
        )
        console.print(
            "Reflex creates its build folders (.web/ and .states/) next to rxconfig.py, "
            "so the UI must be installed somewhere your user can write to."
        )
        console.print(
            "Install PlexMix into a virtual environment you own, for example:\n"
            "  [bold]python -m venv ~/.venvs/plexmix[/bold]\n"
            f"  [bold]~/.venvs/plexmix/bin/{escape(UI_INSTALL_HINT)}[/bold]\n"
            "or run it from a source checkout you own "
            f"([bold]{escape(UI_INSTALL_HINT_SOURCE)}[/bold])."
        )
        raise typer.Exit(1)

    return app_dir


def _app_import_path(app_dir: Path) -> Path:
    """The ``sys.path`` entry that makes ``plexmix`` importable for *app_dir*.

    A checkout keeps the package under ``src/``; a wheel installs it at the
    site-packages root, which is the app directory itself.
    """
    src_dir = app_dir / "src"
    return src_dir if (src_dir / "plexmix").is_dir() else app_dir


def _build_child_env(api_url: Optional[str]) -> Dict[str, str]:
    """Environment for the ``reflex run`` child process.

    Pins a single backend worker and drops Redis settings (with a warning naming the
    variable, never its value, since Redis URLs can carry passwords).
    """
    env = os.environ.copy()
    if api_url:
        env["PLEXMIX_API_URL"] = api_url

    # Allow custom hostnames (reverse proxy / custom domains). Only the Vite dev
    # server checks the Host header; the production static server does not.
    allowed_hosts = os.getenv("PLEXMIX_ALLOWED_HOSTS", "")
    if allowed_hosts:
        env["__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"] = allowed_hosts

    env.update(SINGLE_WORKER_ENV)
    for name in UNSUPPORTED_REDIS_ENV_VARS:
        if name in env:
            console.print(
                f"[yellow]Warning: {name} is set, but PlexMix supports only a single "
                "backend worker without Redis (job progress, cancellation and sessions "
                f"are kept in process). Ignoring {name} for the web UI.[/yellow]"
            )
            del env[name]

    # The production backend is started as a bare ``granian`` command, found via
    # PATH. With an interpreter that is not activated (pipx, ``/path/venv/bin/plexmix``,
    # ``python -m plexmix``) its scripts dir is not on PATH and the prod launch failed
    # with "No such file or directory: 'granian'". Put this interpreter's dir first.
    scripts_dir = str(Path(sys.executable).parent)
    path_entries = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    if scripts_dir not in path_entries:
        env["PATH"] = os.pathsep.join([scripts_dir, *path_entries])
    return env


def _states_dir(app_dir: Path, env: Mapping[str, str]) -> Path:
    """Where Reflex's disk state manager will pickle session state for this launch.

    ``REFLEX_STATES_WORKDIR`` (default ``.states``; blank counts as unset, and the
    value is used verbatim, as Reflex does), relative to the child's cwd, which is
    *app_dir*.
    """
    configured = env.get("REFLEX_STATES_WORKDIR", "")
    path = Path(configured if configured.strip() else ".states")
    return path if path.is_absolute() else app_dir / path


def _make_states_dir_private(app_dir: Path, env: Mapping[str, str]) -> None:
    """Create Reflex's state directory owner-only (0700) before the server starts.

    Reflex pickles every session's state there with the default umask, and that
    state includes the signed ``plexmix_session`` sign-in cookie: a bearer
    credential for up to 7 days. Reflex empties the directory on each start but
    keeps the directory itself, so its 0700 mode keeps other local accounts from
    reading the pickles. Failure only warns: the UI still works.
    """
    states_dir = _states_dir(app_dir, env)
    try:
        states_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(states_dir, 0o700)
    except OSError as e:
        console.print(
            f"[yellow]Warning: could not restrict access to {escape(str(states_dir))} "
            f"({escape(type(e).__name__)}). Sign-in sessions saved there may be "
            "readable by other local users.[/yellow]"
        )


def launch_ui(
    host: str = typer.Option(
        os.getenv("PLEXMIX_UI_HOST", "127.0.0.1"),
        help="Host IP address for the backend server",
    ),
    port: int = typer.Option(
        _env_int("PLEXMIX_UI_PORT", 3000),
        help="Port for the UI frontend",
    ),
    backend_port: int = typer.Option(
        _env_int("PLEXMIX_BACKEND_PORT", 8000),
        help="Port for the Reflex backend server",
    ),
    api_url: Optional[str] = typer.Option(
        os.getenv("PLEXMIX_API_URL"),
        help="Public backend URL for WebSocket connections (for reverse proxy/Docker port mapping)",
    ),
    dev: bool = typer.Option(
        False,
        "--dev/--prod",
        help=(
            "--dev runs the hot-reloading development server; --prod (the default) "
            "serves the optimized production build"
        ),
    ),
) -> None:
    try:
        # Resolve the app first: it is both the child's cwd and the import path.
        app_dir = _resolve_app_dir()
        import_path = str(_app_import_path(app_dir))
        if import_path not in sys.path:
            sys.path.insert(0, import_path)

        import reflex as rx  # type: ignore[import-not-found]  # noqa: F401

        console.print("[bold green]Launching PlexMix Web UI...[/bold green]")
        console.print(f"Frontend: http://localhost:{port}")
        console.print(f"Backend: http://{host}:{backend_port}")
        if api_url:
            console.print(f"API URL: {escape(api_url)}")
        if dev:
            console.print(f"[dim]{DEV_MODE_NOTICE}[/dim]")
        else:
            console.print(f"[bold]{PROD_BUILD_NOTICE}[/bold]")

        import subprocess

        env = _build_child_env(api_url)
        _make_states_dir_private(app_dir, env)

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
        if not dev:
            cmd.extend(["--env", "prod"])

        # Reflex must run from the directory containing rxconfig.py + plexmix_ui/.
        result = subprocess.run(cmd, cwd=str(app_dir), env=env)
        # Propagate the child's exit status instead of always returning 0.
        if result.returncode != 0:
            raise typer.Exit(result.returncode)

    except ImportError:
        console.print("[red]Reflex is not installed.[/red]")
        console.print("\nTo use the web UI, install PlexMix with UI extras:")
        console.print(f"  [bold]{escape(UI_INSTALL_HINT)}[/bold]")
        console.print("or")
        console.print(f"  [bold]{escape(UI_INSTALL_HINT_SOURCE)}[/bold]")
        raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Failed to launch UI: {escape(str(e))}[/red]")
        raise typer.Exit(1)
