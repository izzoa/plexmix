import reflex as rx
from plexmix.ui.states.app_state import AppState


def login_page() -> rx.Component:
    """Full-viewport login screen with glass card and branded styling.

    One ``main`` landmark and one level-1 heading ("PlexMix"). The prompt line is
    the password field's ``<label>``. The ``#login-error`` alert region stays
    mounted (empty until a failed attempt), so the error is announced when its
    text appears, and the field is marked invalid and described by it.
    """
    return rx.el.main(
        rx.center(
            rx.vstack(
                # Logo above card
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
                # Card
                rx.card(
                    rx.vstack(
                        rx.heading(
                            "PlexMix",
                            size="6",
                            align="center",
                            width="100%",
                        ),
                        rx.el.label(
                            "Enter the password to continue",
                            html_for="login-password",
                            style={
                                "display": "block",
                                "width": "100%",
                                "textAlign": "center",
                                "fontSize": "var(--body-size)",
                                "lineHeight": "var(--body-line)",
                                "color": "var(--fg-2)",
                            },
                        ),
                        # Login form
                        rx.form(
                            rx.vstack(
                                # Field and its alert region form one stack item, so the
                                # always-mounted (often empty) region adds no extra gap.
                                rx.box(
                                    rx.input(
                                        id="login-password",
                                        name="password",
                                        type="password",
                                        placeholder="Password",
                                        size="3",
                                        width="100%",
                                        auto_focus=True,
                                        aria_invalid=rx.cond(
                                            AppState.login_error != "", "true", "false"
                                        ),
                                        aria_describedby="login-error",
                                        style={"height": "44px"},
                                    ),
                                    rx.box(
                                        rx.cond(
                                            AppState.login_error != "",
                                            rx.text(
                                                AppState.login_error,
                                                color="var(--error-fg)",
                                                size="2",
                                                style={"marginTop": "var(--space-3)"},
                                                # New node per failed attempt, so the
                                                # same message is announced again.
                                                key=AppState.login_failures,
                                            ),
                                            rx.fragment(),
                                        ),
                                        id="login-error",
                                        role="alert",
                                    ),
                                    width="100%",
                                ),
                                rx.button(
                                    "Sign In",
                                    type="submit",
                                    size="3",
                                    width="100%",
                                    class_name="pm-button",
                                    style={"height": "44px"},
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
                    style={
                        "boxShadow": "var(--shadow-lg)",
                        # Let the Sign In button's glow show past the card edge.
                        "overflow": "visible",
                    },
                ),
                spacing="6",
                align="center",
            ),
            min_height="100vh",
            width="100%",
            style={
                "background": "radial-gradient(ellipse at 50% 0%, rgba(249, 115, 22, 0.08) 0%, transparent 60%)",
            },
        ),
    )
