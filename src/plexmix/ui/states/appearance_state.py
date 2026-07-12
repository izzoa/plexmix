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
    def hydrate_appearance(self):
        """Read persisted density/accent from localStorage into state so the
        segmented controls highlight the actually-saved option after a reload
        (the state otherwise stayed at defaults while the DOM was restored)."""
        return rx.call_script(
            "[localStorage.getItem('pm_density') || 'comfortable',"
            " localStorage.getItem('pm_accent') || 'balanced']",
            callback=AppearanceState.apply_appearance,
        )

    @rx.event
    def apply_appearance(self, values: list):
        try:
            self.density = values[0] or "comfortable"
            self.accent = values[1] or "balanced"
        except (IndexError, TypeError):
            pass

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
