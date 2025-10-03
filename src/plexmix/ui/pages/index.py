import reflex as rx
from plexmix.ui.states.app_state import AppState


class IndexState(AppState):
    def redirect_to_dashboard(self):
        return rx.redirect("/dashboard")


def index() -> rx.Component:
    # Use Reflex redirect to maintain routing context
    return rx.center(
        rx.vstack(
            rx.heading("Redirecting to dashboard..."),
            rx.spinner(),
        ),
        height="100vh",
        on_mount=IndexState.redirect_to_dashboard,
    )
