import reflex as rx
from plexmix.ui.states.app_state import AppState


def login_page() -> rx.Component:
    """Full-viewport login form shown when password protection is enabled."""
    return rx.center(
        rx.card(
            rx.vstack(
                # Logo
                rx.center(
                    rx.color_mode_cond(
                        light=rx.image(
                            src="/logo-light.svg",
                            alt="PlexMix",
                            width="100px",
                            height="100px",
                        ),
                        dark=rx.image(
                            src="/logo-dark.svg",
                            alt="PlexMix",
                            width="100px",
                            height="100px",
                        ),
                    ),
                    width="100%",
                ),
                rx.heading("PlexMix", size="6", align="center", width="100%"),
                rx.text(
                    "Enter the password to continue",
                    size="2",
                    color="gray.11",
                    align="center",
                    width="100%",
                ),
                rx.divider(),
                # Login form
                rx.form(
                    rx.vstack(
                        rx.input(
                            name="password",
                            type="password",
                            placeholder="Password",
                            size="3",
                            width="100%",
                            auto_focus=True,
                        ),
                        rx.cond(
                            AppState.login_error != "",
                            rx.text(
                                AppState.login_error,
                                color="red.9",
                                size="2",
                            ),
                            rx.fragment(),
                        ),
                        rx.button(
                            "Sign In",
                            type="submit",
                            size="3",
                            width="100%",
                        ),
                        spacing="3",
                        width="100%",
                    ),
                    on_submit=AppState.attempt_login,
                    width="100%",
                ),
                spacing="4",
                width="100%",
                align="center",
            ),
            width="100%",
            max_width="380px",
            padding="6",
        ),
        min_height="100vh",
        width="100%",
    )
