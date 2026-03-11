import reflex as rx
from plexmix.ui.states.app_state import AppState
from plexmix.ui.components.login import login_page


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


def mobile_navbar_link(text: str, href: str, icon_name: str) -> rx.Component:
    """Create a mobile navbar link that closes the nav on click."""
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
        on_click=[AppState.set_page_loading(True), AppState.close_mobile_nav],
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


def _nav_links() -> list:
    """Return the list of navigation links for reuse."""
    return [
        ("Dashboard", "/dashboard", "layout-dashboard"),
        ("Generate", "/generator", "sparkles"),
        ("Library", "/library", "library"),
        ("Tagging", "/tagging", "tags"),
        ("History", "/history", "history"),
        ("Doctor", "/doctor", "stethoscope"),
        ("Settings", "/settings", "settings"),
    ]


def _theme_toggle() -> rx.Component:
    """Theme toggle switch."""
    return rx.hstack(
        rx.icon("sun", size=16),
        rx.switch(
            on_change=rx.toggle_color_mode,
            size="2",
        ),
        rx.icon("moon", size=16),
        spacing="2",
        align="center",
        title="Toggle dark mode",
    )


def _logout_button() -> rx.Component:
    """Logout button, only shown when auth is required."""
    return rx.cond(
        AppState.auth_required,
        rx.button(
            rx.icon("log-out", size=16),
            rx.text("Logout", size="2"),
            on_click=AppState.logout,
            variant="ghost",
            color_scheme="gray",
            size="2",
            width="100%",
            cursor="pointer",
            title="Log out",
        ),
        rx.fragment(),
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
            *[navbar_link(text, href, icon) for text, href, icon in _nav_links()],
            rx.spacer(),
            _logout_button(),
            _theme_toggle(),
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
        class_name="hide-mobile",
    )


def mobile_top_bar() -> rx.Component:
    """Fixed bar at top with hamburger + logo for mobile."""
    return rx.box(
        rx.hstack(
            rx.button(
                rx.icon("menu", size=20),
                on_click=AppState.toggle_mobile_nav,
                variant="ghost",
                size="2",
                title="Open navigation",
            ),
            rx.link(
                rx.color_mode_cond(
                    light=rx.image(
                        src="/logo-light.svg",
                        alt="PlexMix",
                        width="36px",
                        height="36px",
                    ),
                    dark=rx.image(
                        src="/logo-dark.svg",
                        alt="PlexMix",
                        width="36px",
                        height="36px",
                    ),
                ),
                href="/dashboard",
            ),
            rx.spacer(),
            _theme_toggle(),
            spacing="3",
            align="center",
            width="100%",
            padding_x="4",
            padding_y="2",
        ),
        position="fixed",
        top="0",
        left="0",
        right="0",
        height="56px",
        background_color="gray.2",
        border_bottom="1px solid",
        border_color="gray.4",
        z_index="200",
        display="flex",
        align_items="center",
        class_name="hide-desktop",
    )


def mobile_sidebar() -> rx.Component:
    """Slide-out sidebar overlay for mobile navigation."""
    return rx.box(
        rx.vstack(
            # Close button
            rx.hstack(
                rx.spacer(),
                rx.button(
                    rx.icon("x", size=20),
                    on_click=AppState.close_mobile_nav,
                    variant="ghost",
                    size="2",
                    title="Close navigation",
                ),
                width="100%",
                padding="2",
            ),
            rx.divider(),
            # Navigation links
            *[mobile_navbar_link(text, href, icon) for text, href, icon in _nav_links()],
            rx.spacer(),
            _logout_button(),
            spacing="2",
            align="start",
            padding="16px",
            width="100%",
            height="100%",
        ),
        position="fixed",
        top="0",
        left="0",
        width="240px",
        height="100vh",
        background_color="gray.2",
        border_right="1px solid",
        border_color="gray.4",
        z_index="301",
        transform=rx.cond(
            AppState.is_mobile_nav_open,
            "translateX(0)",
            "translateX(-100%)",
        ),
        transition="transform 200ms ease",
        class_name="hide-desktop",
    )


def mobile_nav_backdrop() -> rx.Component:
    """Semi-transparent overlay behind mobile sidebar, closes nav on click."""
    return rx.cond(
        AppState.is_mobile_nav_open,
        rx.box(
            position="fixed",
            top="0",
            left="0",
            right="0",
            bottom="0",
            background="rgba(0, 0, 0, 0.5)",
            z_index="300",
            on_click=AppState.close_mobile_nav,
            class_name="hide-desktop",
        ),
        rx.fragment(),
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
        left=rx.breakpoints(initial="0px", md="240px"),
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
    return rx.cond(
        AppState.is_authenticated,
        rx.fragment(
            rx.toast.provider(),
            rx.box(
                navbar(),
                mobile_top_bar(),
                mobile_sidebar(),
                mobile_nav_backdrop(),
                rx.cond(
                    AppState.is_page_loading,
                    loading_overlay(),
                    rx.fragment(),
                ),
                rx.box(
                    content,
                    margin_left=rx.breakpoints(initial="0px", md="240px"),
                    padding_top=rx.breakpoints(initial="56px", md="0px"),
                    padding="6",
                    min_height="100vh",
                ),
            ),
        ),
        login_page(),
    )
