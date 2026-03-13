import reflex as rx
from plexmix.ui.states.app_state import AppState
from plexmix.ui.components.login import login_page


# ── Navigation structure ──────────────────────────────────────────────
# Grouped with section labels per the revamp plan.

_MAIN_LINKS = [
    ("Dashboard", "/dashboard", "layout-dashboard"),
    ("Generate", "/generator", "sparkles"),
    ("Library", "/library", "library"),
]

_TOOLS_LINKS = [
    ("Tagging", "/tagging", "tags"),
    ("History", "/history", "history"),
    ("Doctor", "/doctor", "stethoscope"),
]

_SYSTEM_LINKS = [
    ("Settings", "/settings", "settings"),
]


def _nav_links() -> list:
    """Return the flat list of all navigation links (backward compat)."""
    return _MAIN_LINKS + _TOOLS_LINKS + _SYSTEM_LINKS


# ── Section label ─────────────────────────────────────────────────────

def _section_label(text: str) -> rx.Component:
    """Uppercase muted section label for nav grouping."""
    return rx.text(
        text,
        size="1",
        weight="medium",
        color="gray.9",
        style={
            "textTransform": "uppercase",
            "letterSpacing": "0.05em",
            "fontSize": "11px",
        },
        padding_left="12px",
        margin_top="16px",
        margin_bottom="4px",
    )


# ── Nav link (desktop) ────────────────────────────────────────────────

def navbar_link(text: str, href: str, icon_name: str) -> rx.Component:
    """Create a navbar link with icon, active highlight, and left accent bar."""
    is_active = AppState.router.page.path == href

    return rx.link(
        rx.hstack(
            # Left accent bar (2px orange, only on active item)
            rx.box(
                width="2px",
                height="20px",
                border_radius="1px",
                background_color=rx.cond(is_active, "accent.9", "transparent"),
                transition="background-color 200ms ease",
                flex_shrink="0",
            ),
            rx.icon(icon_name, size=18),
            rx.text(text, size="2", weight="medium"),
            spacing="3",
            align="center",
            width="100%",
        ),
        href=href,
        on_click=AppState.set_page_loading(True),
        underline="none",
        padding_y="8px",
        padding_x="8px",
        border_radius="var(--radius-md)",
        width="100%",
        background_color=rx.cond(is_active, "accent.3", "transparent"),
        color=rx.cond(is_active, "accent.11", "gray.11"),
        _hover={
            "background_color": rx.cond(is_active, "accent.4", "gray.3"),
            "color": rx.cond(is_active, "accent.11", "gray.12"),
        },
        transition="all 150ms ease",
    )


# ── Nav link (mobile) ────────────────────────────────────────────────

def mobile_navbar_link(text: str, href: str, icon_name: str) -> rx.Component:
    """Create a mobile navbar link that closes the nav on click."""
    is_active = AppState.router.page.path == href

    return rx.link(
        rx.hstack(
            rx.box(
                width="2px",
                height="20px",
                border_radius="1px",
                background_color=rx.cond(is_active, "accent.9", "transparent"),
                transition="background-color 200ms ease",
                flex_shrink="0",
            ),
            rx.icon(icon_name, size=18),
            rx.text(text, size="2", weight="medium"),
            spacing="3",
            align="center",
            width="100%",
        ),
        href=href,
        on_click=[AppState.set_page_loading(True), AppState.close_mobile_nav],
        underline="none",
        padding_y="8px",
        padding_x="8px",
        border_radius="var(--radius-md)",
        width="100%",
        background_color=rx.cond(is_active, "accent.3", "transparent"),
        color=rx.cond(is_active, "accent.11", "gray.11"),
        _hover={
            "background_color": rx.cond(is_active, "accent.4", "gray.3"),
            "color": rx.cond(is_active, "accent.11", "gray.12"),
        },
        transition="all 150ms ease",
    )


# ── Theme toggle ─────────────────────────────────────────────────────

def _theme_toggle() -> rx.Component:
    """Segmented-style theme toggle (sun / switch / moon)."""
    return rx.hstack(
        rx.icon("sun", size=14, color="gray.9"),
        rx.switch(
            on_change=rx.toggle_color_mode,
            size="1",
        ),
        rx.icon("moon", size=14, color="gray.9"),
        spacing="2",
        align="center",
        title="Toggle dark mode",
    )


# ── Logout button ────────────────────────────────────────────────────

def _logout_button() -> rx.Component:
    """Icon-only logout button, shown when auth is required."""
    return rx.cond(
        AppState.auth_required,
        rx.tooltip(
            rx.button(
                rx.icon("log-out", size=16),
                on_click=AppState.logout,
                variant="ghost",
                color_scheme="gray",
                size="2",
                cursor="pointer",
            ),
            content="Log out",
        ),
        rx.fragment(),
    )


# ── Logo ──────────────────────────────────────────────────────────────

def _logo(size: str = "80px") -> rx.Component:
    """Theme-aware logo with hover glow."""
    return rx.link(
        rx.color_mode_cond(
            light=rx.image(
                src="/logo-light.svg",
                alt="PlexMix",
                width=size,
                height=size,
            ),
            dark=rx.image(
                src="/logo-dark.svg",
                alt="PlexMix",
                width=size,
                height=size,
            ),
        ),
        href="/dashboard",
        _hover={"opacity": 0.85},
        transition="opacity 150ms ease",
    )


# ── Grouped nav links ────────────────────────────────────────────────

def _desktop_nav_groups() -> list:
    """Return nav links with section labels for the desktop sidebar."""
    items: list[rx.Component] = []
    items.append(_section_label("Main"))
    for text, href, icon in _MAIN_LINKS:
        items.append(navbar_link(text, href, icon))
    items.append(_section_label("Tools"))
    for text, href, icon in _TOOLS_LINKS:
        items.append(navbar_link(text, href, icon))
    items.append(_section_label("System"))
    for text, href, icon in _SYSTEM_LINKS:
        items.append(navbar_link(text, href, icon))
    return items


def _mobile_nav_groups() -> list:
    """Return nav links with section labels for the mobile sidebar."""
    items: list[rx.Component] = []
    items.append(_section_label("Main"))
    for text, href, icon in _MAIN_LINKS:
        items.append(mobile_navbar_link(text, href, icon))
    items.append(_section_label("Tools"))
    for text, href, icon in _TOOLS_LINKS:
        items.append(mobile_navbar_link(text, href, icon))
    items.append(_section_label("System"))
    for text, href, icon in _SYSTEM_LINKS:
        items.append(mobile_navbar_link(text, href, icon))
    return items


# ══════════════════════════════════════════════════════════════════════
#  Desktop Sidebar
# ══════════════════════════════════════════════════════════════════════

def navbar() -> rx.Component:
    return rx.box(
        rx.vstack(
            # Logo — smaller, centered
            rx.center(
                _logo("80px"),
                width="100%",
                padding_top="20px",
                padding_bottom="8px",
            ),
            # Grouped navigation
            rx.vstack(
                *_desktop_nav_groups(),
                spacing="1",
                width="100%",
            ),
            rx.spacer(),
            # Bottom controls
            rx.hstack(
                _logout_button(),
                rx.spacer(),
                _theme_toggle(),
                width="100%",
                align="center",
                padding_x="8px",
                padding_bottom="16px",
            ),
            spacing="1",
            align="start",
            width="100%",
            height="100%",
        ),
        position="fixed",
        left="0",
        top="0",
        height="100vh",
        width="220px",
        padding_x="12px",
        class_name="glass hide-mobile",
        z_index="100",
        overflow_y="auto",
    )


# ══════════════════════════════════════════════════════════════════════
#  Mobile Top Bar
# ══════════════════════════════════════════════════════════════════════

def mobile_top_bar() -> rx.Component:
    """Fixed top bar with hamburger + centered logo + theme toggle."""
    return rx.box(
        rx.hstack(
            rx.button(
                rx.icon("menu", size=20),
                on_click=AppState.toggle_mobile_nav,
                variant="ghost",
                size="2",
                title="Open navigation",
            ),
            rx.spacer(),
            _logo("32px"),
            rx.spacer(),
            _theme_toggle(),
            spacing="3",
            align="center",
            width="100%",
            padding_x="12px",
        ),
        position="fixed",
        top="0",
        left="0",
        right="0",
        height="48px",
        z_index="200",
        display="flex",
        align_items="center",
        class_name="glass hide-desktop",
    )


# ══════════════════════════════════════════════════════════════════════
#  Mobile Slide-out Sidebar
# ══════════════════════════════════════════════════════════════════════

def mobile_sidebar() -> rx.Component:
    """Slide-out sidebar overlay for mobile navigation."""
    return rx.box(
        rx.vstack(
            # Close row
            rx.hstack(
                _logo("36px"),
                rx.spacer(),
                rx.button(
                    rx.icon("x", size=20),
                    on_click=AppState.close_mobile_nav,
                    variant="ghost",
                    size="2",
                    title="Close navigation",
                ),
                width="100%",
                align="center",
                padding_x="12px",
                padding_top="12px",
                padding_bottom="8px",
            ),
            # Grouped navigation
            rx.vstack(
                *_mobile_nav_groups(),
                spacing="1",
                width="100%",
                padding_x="4px",
            ),
            rx.spacer(),
            # Bottom
            rx.hstack(
                _logout_button(),
                width="100%",
                padding_x="12px",
                padding_bottom="16px",
            ),
            spacing="1",
            align="start",
            width="100%",
            height="100%",
        ),
        position="fixed",
        top="0",
        left="0",
        width="220px",
        height="100vh",
        z_index="301",
        class_name="glass hide-desktop",
        transform=rx.cond(
            AppState.is_mobile_nav_open,
            "translateX(0)",
            "translateX(-100%)",
        ),
        transition="transform 400ms cubic-bezier(0.16, 1, 0.3, 1)",
    )


# ══════════════════════════════════════════════════════════════════════
#  Mobile Backdrop
# ══════════════════════════════════════════════════════════════════════

def mobile_nav_backdrop() -> rx.Component:
    """Semi-transparent blurred overlay behind mobile sidebar."""
    return rx.cond(
        AppState.is_mobile_nav_open,
        rx.box(
            position="fixed",
            top="0",
            left="0",
            right="0",
            bottom="0",
            background="rgba(0, 0, 0, 0.6)",
            backdrop_filter="blur(4px)",
            z_index="300",
            on_click=AppState.close_mobile_nav,
            class_name="hide-desktop",
        ),
        rx.fragment(),
    )


# ══════════════════════════════════════════════════════════════════════
#  Page Loading Indicator
# ══════════════════════════════════════════════════════════════════════

def loading_overlay() -> rx.Component:
    """Thin progress bar at the top of the content area (replaces old full-screen overlay)."""
    return rx.box(
        rx.box(
            class_name="pm-progress-bar-indeterminate",
        ),
        position="fixed",
        top=rx.breakpoints(initial="48px", md="0px"),
        left=rx.breakpoints(initial="0px", md="220px"),
        right="0",
        height="2px",
        background_color="gray.3",
        z_index="999",
        overflow="hidden",
        border_radius="1px",
    )


# ══════════════════════════════════════════════════════════════════════
#  Layout Shell
# ══════════════════════════════════════════════════════════════════════

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
                    margin_left=rx.breakpoints(initial="0px", md="220px"),
                    padding_top=rx.breakpoints(initial="48px", md="0px"),
                    padding_x=rx.breakpoints(initial="16px", md="40px"),
                    padding_y=rx.breakpoints(initial="20px", md="32px"),
                    min_height="100vh",
                    max_width=rx.breakpoints(initial="100%", md="calc(1200px + 80px)"),
                    class_name="animate-fade-in-up",
                ),
            ),
        ),
        login_page(),
    )
