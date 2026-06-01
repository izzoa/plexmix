"""State for the ⌘K command palette.

Holds open/query/selection state and the command lists (page jumps, actions,
quick vibes). Navigation uses real route redirects so the multi-route model and
per-page ``on_load`` are preserved.
"""

import reflex as rx


# Page-jump commands (id, title, subtitle, icon, route).
NAV_COMMANDS: list[dict[str, str]] = [
    {
        "id": "dashboard",
        "title": "Dashboard",
        "sub": "Go to page",
        "icon": "layout-dashboard",
        "href": "/dashboard",
    },
    {
        "id": "generator",
        "title": "Generator",
        "sub": "Go to page",
        "icon": "sparkles",
        "href": "/generator",
    },
    {
        "id": "library",
        "title": "Library",
        "sub": "Go to page",
        "icon": "library",
        "href": "/library",
    },
    {"id": "tagging", "title": "Tagging", "sub": "Go to page", "icon": "tags", "href": "/tagging"},
    {
        "id": "history",
        "title": "History",
        "sub": "Go to page",
        "icon": "history",
        "href": "/history",
    },
    {
        "id": "doctor",
        "title": "Doctor",
        "sub": "Go to page",
        "icon": "stethoscope",
        "href": "/doctor",
    },
    {
        "id": "settings",
        "title": "Settings",
        "sub": "Go to page",
        "icon": "settings",
        "href": "/settings",
    },
]

# Action commands (id, title, subtitle, icon). Behavior resolved in run_action.
ACTION_COMMANDS: list[dict[str, str]] = [
    {
        "id": "generate",
        "title": "Generate a playlist",
        "sub": "Start a new mix",
        "icon": "sparkles",
    },
    {"id": "sync", "title": "Sync library", "sub": "Pull latest from Plex", "icon": "refresh-cw"},
    {"id": "tag", "title": "Tag untagged tracks", "sub": "Open AI tagging", "icon": "tags"},
    {"id": "doctor", "title": "Run diagnostics", "sub": "Open Doctor", "icon": "stethoscope"},
    {"id": "theme", "title": "Toggle dark mode", "sub": "Appearance", "icon": "moon"},
    {"id": "cancel", "title": "Cancel running task", "sub": "Stop active jobs", "icon": "ban"},
]

# Quick vibes — seed the Generator with a mood.
VIBE_COMMANDS: list[str] = [
    "late night coding focus",
    "rainy day melancholy",
    "sunday morning coffee",
    "high energy workout",
]

_ACTION_ROUTES: dict[str, str] = {
    "generate": "/generator",
    "sync": "/library",
    "tag": "/tagging",
    "doctor": "/doctor",
}


class CommandPaletteState(rx.State):
    """Open/close, query, and selection for the command palette."""

    is_open: bool = False
    query: str = ""
    selected_index: int = 0

    @rx.event
    def open_palette(self):
        self.is_open = True
        self.query = ""
        self.selected_index = 0

    @rx.event
    def close_palette(self):
        self.is_open = False

    @rx.event
    def stop(self):
        """No-op; used with .stop_propagation so clicks inside the panel don't close it."""

    @rx.event
    def set_query(self, value: str):
        self.query = value
        self.selected_index = 0

    @rx.var
    def nav_results(self) -> list[dict[str, str]]:
        q = self.query.strip().lower()
        if not q:
            return NAV_COMMANDS
        return [c for c in NAV_COMMANDS if q in (c["title"] + " " + c["sub"]).lower()]

    @rx.var
    def action_results(self) -> list[dict[str, str]]:
        q = self.query.strip().lower()
        if not q:
            return ACTION_COMMANDS
        return [c for c in ACTION_COMMANDS if q in (c["title"] + " " + c["sub"]).lower()]

    @rx.var
    def vibe_results(self) -> list[str]:
        q = self.query.strip().lower()
        if not q:
            return VIBE_COMMANDS
        return [v for v in VIBE_COMMANDS if q in v.lower()]

    @rx.var
    def has_results(self) -> bool:
        return bool(self.nav_results or self.action_results or self.vibe_results)

    @rx.event
    def goto(self, href: str):
        """Navigate to a route and close the palette."""
        self.is_open = False
        return rx.redirect(href)

    @rx.event
    def run_action(self, action_id: str):
        """Run an action command; resolve to a redirect, theme toggle, or cancel."""
        self.is_open = False
        if action_id == "theme":
            return rx.toggle_color_mode
        if action_id == "cancel":
            from plexmix.ui.states.app_state import AppState

            return AppState.request_cancel("__all__")
        route = _ACTION_ROUTES.get(action_id)
        if route:
            return rx.redirect(route)
        return None

    @rx.event
    def run_vibe(self, vibe: str):
        """Seed the Generator with a vibe and navigate there."""
        from plexmix.ui.states.generator_state import GeneratorState

        self.is_open = False
        return [
            GeneratorState.set_mood_query(vibe),
            rx.redirect("/generator"),
        ]
