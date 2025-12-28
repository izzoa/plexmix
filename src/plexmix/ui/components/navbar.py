import reflex as rx
from plexmix.ui.states.app_state import AppState


def navbar_link(text: str, href: str, icon_name: str) -> rx.Component:
    """Create a navbar link with icon and active state highlighting."""
    is_active = AppState.router.page.path == href

    return rx.link(
        rx.hstack(
            rx.icon(icon_name, size=18),
            rx.text(text, size="3", weight="medium"),
            spacing="3",
            align="center",
            width="100%",
        ),
        href=href,
        on_click=AppState.set_page_loading(True),
        underline="none",
        padding_y="2",
        padding_x="3",
        border_radius="md",
        width="100%",
        background_color=rx.cond(is_active, "accent.3", "transparent"),
        color=rx.cond(is_active, "accent.11", "gray.11"),
        _hover={
            "background_color": rx.cond(is_active, "accent.4", "gray.3"),
            "color": rx.cond(is_active, "accent.11", "gray.12"),
        },
        transition="all 150ms ease",
    )


def navbar() -> rx.Component:
    return rx.box(
        rx.vstack(
            # Logo - switch based on theme (centered)
            rx.center(
                rx.link(
                    rx.color_mode_cond(
                        light=rx.image(
                            src="/logo-light.svg",
                            alt="PlexMix",
                            width="120px",
                            height="120px",
                        ),
                        dark=rx.image(
                            src="/logo-dark.svg",
                            alt="PlexMix",
                            width="120px",
                            height="120px",
                        ),
                    ),
                    href="/dashboard",
                    _hover={"opacity": 0.8},
                    transition="opacity 150ms ease",
                ),
                width="100%",
            ),
            rx.divider(margin_y="3"),
            # Navigation links with icons
            navbar_link("Dashboard", "/dashboard", "layout-dashboard"),
            navbar_link("Generate", "/generator", "sparkles"),
            navbar_link("Library", "/library", "library"),
            navbar_link("Tagging", "/tagging", "tags"),
            navbar_link("History", "/history", "history"),
            navbar_link("Doctor", "/doctor", "stethoscope"),
            navbar_link("Settings", "/settings", "settings"),
            rx.spacer(),
            # Theme toggle
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
            spacing="2",
            align="start",
            padding_top="16px",
            padding_bottom="16px",
            padding_left="24px",
            padding_right="16px",
            width="100%",
        ),
        position="fixed",
        left="0",
        top="0",
        height="100vh",
        width="240px",
        padding_left="16px",
        padding_right="12px",
        background_color="gray.2",
        border_right="1px solid",
        border_color="gray.4",
        z_index="100",
    )


def loading_overlay() -> rx.Component:
    """Loading overlay for page transitions."""
    return rx.box(
        rx.vstack(
            rx.spinner(size="3"),
            rx.text("Loading...", size="4", color="gray.11"),
            spacing="4",
            align="center",
            justify="center",
        ),
        position="fixed",
        top="0",
        left="240px",  # Account for navbar
        right="0",
        bottom="0",
        background="rgba(0, 0, 0, 0.7)",
        backdrop_filter="blur(4px)",
        z_index="999",
        display="flex",
        align_items="center",
        justify_content="center",
    )


def layout(content: rx.Component) -> rx.Component:
    return rx.box(
        navbar(),
        rx.cond(
            AppState.is_page_loading,
            loading_overlay(),
            rx.fragment(),
        ),
        rx.box(
            content,
            margin_left="240px",
            padding="6",
            min_height="100vh",
        ),
    )
