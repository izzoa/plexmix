"""Server-side changelog loader for the web UI's changelog dialog.

The release notes are rendered to HTML on the server when the dialog opens, so
no client-side markdown, math or syntax-highlighting bundle ships with any
route. The file is found by absolute path, independent of the working
directory:

1. ``importlib.resources.files("plexmix") / "CHANGELOG.md"`` — the copy that
   the publish workflow places inside the package before building the wheel.
2. ``Path(plexmix.__file__).resolve().parents[2] / "CHANGELOG.md"`` — the repo
   root of a source checkout (and ``/app`` in the Docker image).
3. Otherwise the notes are unavailable and the dialog shows a fallback.

Rendering uses markdown-it's CommonMark preset with raw HTML disabled (it is
escaped) plus tables; markdown-it's default link validation drops
``javascript:``, ``vbscript:`` and ``file:`` links.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CHANGELOG_FILENAME = "CHANGELOG.md"
ONLINE_CHANGELOG_URL = "https://github.com/izzoa/plexmix/blob/master/CHANGELOG.md"

_FIRST_SECTION_RE = re.compile(r"^## ", re.MULTILINE)


@dataclass(frozen=True)
class ChangelogView:
    """What the changelog dialog shows: the running version and rendered notes."""

    version: str
    html: str
    available: bool


_lock = threading.Lock()
# Cache keyed by (resolved path, mtime_ns); a changed mtime triggers a re-render.
_cache: dict[tuple[str, Optional[int]], ChangelogView] = {}


def _running_version() -> str:
    from plexmix import __version__

    return __version__


def _packaged_candidate() -> Optional[Path]:
    """Return the in-package CHANGELOG.md (wheel installs), if it exists on disk."""
    try:
        resource = resources.files("plexmix") / CHANGELOG_FILENAME
    except Exception:  # pragma: no cover - defensive: odd loaders
        return None
    try:
        if not resource.is_file():
            return None
    except OSError:
        return None
    # Only filesystem installs are supported (wheels unpack to site-packages);
    # a zipimport resource has no stat() for the mtime-keyed cache.
    return resource if isinstance(resource, Path) else None


def _checkout_candidate() -> Optional[Path]:
    """Return ``<repo root>/CHANGELOG.md`` for a source checkout or ``/app``, if present."""
    import plexmix

    package_file = getattr(plexmix, "__file__", None)
    if not package_file:
        return None
    try:
        path = Path(package_file).resolve().parents[2] / CHANGELOG_FILENAME
    except (IndexError, OSError):
        return None
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def find_changelog() -> Optional[Path]:
    """Return the changelog path to use, or None when no notes are bundled."""
    for candidate in (_packaged_candidate(), _checkout_candidate()):
        if candidate is not None:
            return candidate
    return None


def release_notes_markdown(text: str) -> str:
    """Return ``text`` from its first ``## `` heading on.

    This drops the "Changelog" H1 and the Keep a Changelog preamble so the
    dialog begins at ``[Unreleased]`` or the newest release.
    """
    match = _FIRST_SECTION_RE.search(text)
    return text[match.start() :] if match else text


def render_markdown(text: str) -> str:
    """Render untrusted-ish markdown to HTML with raw HTML escaped."""
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    rendered: str = md.render(text)
    return rendered


def load_changelog() -> ChangelogView:
    """Load and render the bundled release notes, cached by path and mtime."""
    version = _running_version()
    path = find_changelog()
    if path is None:
        return ChangelogView(version=version, html="", available=False)

    try:
        mtime: Optional[int] = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    key = (str(path), mtime)
    with _lock:
        cached = _cache.get(key)
        if cached is not None and cached.version == version:
            return cached
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.warning("Could not read the changelog at %s", path)
            return ChangelogView(version=version, html="", available=False)
        view = ChangelogView(
            version=version,
            html=render_markdown(release_notes_markdown(text)),
            available=True,
        )
        # Keep only the latest render; stale mtimes are dropped.
        _cache.clear()
        _cache[key] = view
        return view


def clear_cache() -> None:
    """Drop cached renders (used by tests)."""
    with _lock:
        _cache.clear()
