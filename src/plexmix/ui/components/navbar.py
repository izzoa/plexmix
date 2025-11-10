import reflex as rx


def navbar_link(text: str, href: str) -> rx.Component:
    return rx.link(
        rx.text(text, size="4"),
        href=href,
        underline="none",
        color_scheme="gray",
        _hover={"color": "orange.10"},
    )


def navbar() -> rx.Component:
    return rx.box(
        rx.vstack(
            # Logo - switch based on theme
            rx.link(
                rx.color_mode_cond(
                    light=rx.image(
                        src="/logo-light.png",
                        alt="PlexMix",
                        width="120px",
                        height="120px",
                    ),
                    dark=rx.image(
                        src="/logo-dark.png",
                        alt="PlexMix",
                        width="120px",
                        height="120px",
                    ),
                ),
                href="/dashboard",
                _hover={"opacity": 0.8},
            ),
            rx.divider(margin_y="3"),
            navbar_link("Dashboard", "/dashboard"),
            navbar_link("Generate", "/generator"),
            navbar_link("Library", "/library"),
            navbar_link("Tagging", "/tagging"),
            navbar_link("History", "/history"),
            navbar_link("Doctor", "/doctor"),
            navbar_link("Settings", "/settings"),
            rx.spacer(),
            rx.hstack(
                rx.icon("sun", size=16),
                rx.switch(
                    on_change=rx.toggle_color_mode,
                    size="2",
                ),
                rx.icon("moon", size=16),
                spacing="2",
                align="center",
            ),
            spacing="3",
            align="start",
            padding="4",
        ),
        position="fixed",
        left="0",
        top="0",
        height="100vh",
        width="200px",
        background_color="gray.2",
        border_right="1px solid",
        border_color="gray.4",
    )


def layout(content: rx.Component) -> rx.Component:
    return rx.box(
        navbar(),
        rx.box(
            content,
            margin_left="200px",
            padding="6",
        ),
    )
