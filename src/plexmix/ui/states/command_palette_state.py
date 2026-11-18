"""State for the ⌘K command palette.

The palette is an uncontrolled Radix dialog (``navbar._command_palette``): the
browser owns its open state, its search text, the filtering and the highlight
(``window.__pmPalette`` in ``assets/plexmix.js``), so typing never contacts the
server. This state only holds the data that comes from the server, refreshed
each time the palette opens (``on_open_change``), and the handlers for the
commands that need the server.

The static commands are module data rendered by a Python loop: page jumps and
route actions redirect on the client, and the theme row toggles on the client.
"""

import asyncio
import logging
from typing import Any, Optional, cast

import reflex as rx

logger = logging.getLogger(__name__)

# How many distinct recent prompts the palette lists.
RECENT_PROMPTS_LIMIT = 5

# Sub-text of a recent prompt whose source playlist is named after the prompt itself.
RECENT_PROMPT_SAME_NAME_SUB = "Use this prompt"

NOTHING_RUNNING_MESSAGE = "Nothing is running right now."

# Page-jump commands. ``gkey`` is the second key of the ``g`` chord (``g d`` …),
# matching the rail's hints in ``navbar._NAV_GROUPS``. ``keywords`` only widen
# the browser-side search (``data-search``); keep "hist" unique to History.
NAV_COMMANDS: list[dict[str, str]] = [
    {
        "id": "dashboard",
        "title": "Dashboard",
        "sub": "Go to page",
        "icon": "layout-dashboard",
        "href": "/dashboard",
        "gkey": "d",
        "keywords": "home overview stats",
    },
    {
        "id": "generator",
        "title": "Generator",
        "sub": "Go to page",
        "icon": "sparkles",
        "href": "/generator",
        "gkey": "g",
        "keywords": "create mix playlist vibe prompt",
    },
    {
        "id": "library",
        "title": "Library",
        "sub": "Go to page",
        "icon": "library",
        "href": "/library",
        "gkey": "l",
        "keywords": "tracks songs music browse search",
    },
    {
        "id": "tagging",
        "title": "Tagging",
        "sub": "Go to page",
        "icon": "tags",
        "href": "/tagging",
        "gkey": "t",
        "keywords": "tags mood ai",
    },
    {
        "id": "history",
        "title": "History",
        "sub": "Go to page",
        "icon": "history",
        "href": "/history",
        "gkey": "h",
        "keywords": "saved past playlists",
    },
    {
        "id": "doctor",
        "title": "Doctor",
        "sub": "Go to page",
        "icon": "stethoscope",
        "href": "/doctor",
        "gkey": "x",
        "keywords": "diagnostics health check",
    },
    {
        "id": "settings",
        "title": "Settings",
        "sub": "Go to page",
        "icon": "settings",
        "href": "/settings",
        "gkey": "s",
        "keywords": "preferences config providers plex api keys",
    },
]

# Action commands. A non-empty ``href`` redirects on the client. ``theme`` toggles
# the colour mode on the client (its title names the next theme) and ``cancel`` is
# listed only while something is running (``has_running_job``).
ACTION_COMMANDS: list[dict[str, str]] = [
    {
        "id": "generate",
        "title": "Generate a playlist",
        "sub": "Start a new mix",
        "icon": "sparkles",
        "href": "/generator",
        "keywords": "new create vibe",
    },
    {
        "id": "sync",
        "title": "Sync library",
        "sub": "Pull latest from Plex",
        "icon": "refresh-cw",
        "href": "/library",
        "keywords": "refresh import update",
    },
    {
        "id": "tag",
        "title": "Tag untagged tracks",
        "sub": "Open AI tagging",
        "icon": "tags",
        "href": "/tagging",
        "keywords": "mood",
    },
    {
        "id": "doctor",
        "title": "Run diagnostics",
        "sub": "Open Doctor",
        "icon": "stethoscope",
        "href": "/doctor",
        "keywords": "health check",
    },
    {
        "id": "theme",
        "title": "Toggle dark mode",
        "sub": "Appearance",
        "icon": "moon",
        "href": "",
        "keywords": "switch light theme color colour",
    },
    {
        "id": "cancel",
        "title": "Cancel running task",
        "sub": "Stop active jobs",
        "icon": "ban",
        "href": "",
        "keywords": "stop abort job",
    },
]


def _normalize_prompt(text: str) -> str:
    """Dedupe key for a prompt: whitespace collapsed, case folded."""
    return " ".join(text.split()).casefold()


def recent_prompt_entries(
    playlists: list[Any], limit: int = RECENT_PROMPTS_LIMIT
) -> list[dict[str, str]]:
    """Up to ``limit`` distinct non-empty prompts from ``playlists`` (newest first).

    ``playlists`` must already be ordered newest first, as
    ``SQLiteManager.get_playlists()`` returns them; each item needs ``mood_query``
    and ``name`` attributes. Prompts are deduplicated ignoring case and
    whitespace, keeping the newest spelling. Each entry has ``prompt``, ``sub``
    (the source playlist's name, or "Use this prompt" when the name is the
    prompt) and ``search`` (lower-cased text the browser filters on).
    """
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for playlist in playlists:
        if len(entries) >= limit:
            break
        prompt = str(getattr(playlist, "mood_query", None) or "").strip()
        key = _normalize_prompt(prompt)
        if not key or key in seen:
            continue
        seen.add(key)
        name = str(getattr(playlist, "name", None) or "").strip()
        sub = name if name and _normalize_prompt(name) != key else RECENT_PROMPT_SAME_NAME_SUB
        entries.append(
            {
                "prompt": prompt,
                "sub": sub,
                "search": f"{prompt} {sub} recent prompt".lower(),
            }
        )
    return entries


def load_recent_prompts(limit: int = RECENT_PROMPTS_LIMIT) -> list[dict[str, str]]:
    """Read the recent prompts from the local database (blocking; run off the loop).

    Returns ``[]`` when the database does not exist yet (it is never created
    here) or cannot be read; errors are logged at debug level.
    """
    try:
        from plexmix.config.settings import Settings
        from plexmix.database.sqlite_manager import SQLiteManager

        db_path = Settings.load_from_file().database.get_db_path()
        if not db_path.exists():
            return []
        with SQLiteManager(str(db_path)) as db:
            return recent_prompt_entries(db.get_playlists(), limit)
    except Exception as exc:
        logger.debug("Could not load recent prompts for the command palette: %s", exc)
        return []


def _task_store_busy() -> bool:
    """Whether any TaskStore-backed job (sync, tagging, embeddings, …) is running."""
    from plexmix.ui.job_manager import task_store

    return bool(task_store.running_job_types())


class CommandPaletteState(rx.State):
    """Server-side data for the command palette: recent prompts and running work."""

    # Newest first; each item has "prompt", "sub" and "search" (see recent_prompt_entries).
    recent_prompts: list[dict[str, str]] = []
    # Whether "Cancel running task" is listed (refreshed on open).
    has_running_job: bool = False

    async def _is_work_running(self) -> bool:
        """Any running TaskStore job, or this session's playlist generation."""
        if _task_store_busy():
            return True
        from plexmix.ui.states.generator_state import GeneratorState

        generator = await self.get_state(GeneratorState)
        return bool(generator.is_generating)

    @rx.event
    async def on_open_change(self, is_open: bool) -> None:
        """Refresh recent prompts and the running flag when the palette opens.

        Never gates rendering: the palette is already open in the browser and
        rows that arrive with this update are picked up by its controller. On
        close the running flag is dropped, so the next open never lists "Cancel
        running task" from a stale check: the row appears only once this open's
        fresh check finds running work.
        """
        if not is_open:
            self.has_running_job = False
            return
        loop = asyncio.get_running_loop()
        self.recent_prompts = await loop.run_in_executor(None, load_recent_prompts)
        self.has_running_job = await self._is_work_running()

    @rx.event
    def use_recent_prompt(self, prompt: str) -> list[Any]:
        """Prefill the Generator with ``prompt`` and open it, without generating."""
        from plexmix.ui.states.generator_state import GeneratorState

        return [cast(Any, GeneratorState).prefill_prompt(prompt), rx.redirect("/generator")]

    @rx.event
    def generate_from_query(self, query: str) -> Optional[list[Any]]:
        """Open the Generator and generate from the palette's query.

        Goes through ``GeneratorState.submit_prompt``, the Generate button's own
        entry point, so its blank-prompt and readiness rules apply. The trailing
        ``rx.set_value`` runs after ``submit_prompt`` has stored the prompt, so a
        prompt field that stays mounted (the Generator was already open) shows the
        query too (design D3). A blank query does nothing.
        """
        from plexmix.ui.states.generator_state import (
            PROMPT_INPUT_ID,
            PROMPT_MAX_CHARS,
            GeneratorState,
        )

        text = (query or "").strip()
        if not text:
            return None
        return [
            rx.redirect("/generator"),
            cast(Any, GeneratorState).submit_prompt({"prompt": text}),
            rx.set_value(PROMPT_INPUT_ID, text[:PROMPT_MAX_CHARS]),
        ]

    @rx.event
    async def request_global_cancel(self) -> Any:
        """Ask to cancel all running work, or say that nothing is running.

        Re-checks at activation: work listed when the palette opened may have
        finished since, and then no confirmation is shown.
        """
        running = await self._is_work_running()
        self.has_running_job = running
        if running:
            from plexmix.ui.states.app_state import AppState

            return cast(Any, AppState).request_cancel("__all__")
        return rx.toast.info(NOTHING_RUNNING_MESSAGE)
