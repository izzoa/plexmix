"""Appearance preferences — density & accent intensity.

Theme (light/dark) is handled by Reflex's color mode. Density and accent are
applied via ``data-density`` / ``data-accent`` on the document root and
persisted in ``localStorage``; a small restore script in the shell re-applies
them on load.
"""

import reflex as rx


class AppearanceState(rx.State):
    density: str = "comfortable"
    accent: str = "balanced"

    @rx.event
    def set_density(self, value: str):
        self.density = value
        return rx.call_script(
            "document.documentElement.setAttribute('data-density','" + value + "');"
            "try{localStorage.setItem('pm_density','" + value + "');}catch(e){}"
        )

    @rx.event
    def set_accent(self, value: str):
        self.accent = value
        return rx.call_script(
            "document.documentElement.setAttribute('data-accent','" + value + "');"
            "try{localStorage.setItem('pm_accent','" + value + "');}catch(e){}"
        )
