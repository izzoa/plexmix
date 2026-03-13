import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.states.dashboard_state import DashboardState


# ── Stat tile ─────────────────────────────────────────────────────────

def _stat_tile(
    label: str,
    value,
    icon_name: str,
    icon_color: str = "accent.9",
    icon_bg: str = "accent.3",
    stagger: str = "",
) -> rx.Component:
    """Single metric tile — large number, muted label, tinted icon."""
    return rx.hstack(
        rx.box(
            rx.icon(icon_name, size=20, color=icon_color),
            padding="8px",
            border_radius="var(--radius-md)",
            background_color=icon_bg,
            flex_shrink="0",
        ),
        rx.vstack(
            rx.text(
                value,
                size="6",
                weight="bold",
                style={"fontFamily": "var(--font-mono)"},
            ),
            rx.text(label, size="1", color="gray.9", weight="medium"),
            spacing="0",
            align="start",
        ),
        spacing="3",
        align="center",
        class_name=f"animate-fade-in-up {stagger}".strip(),
    )


# ── Config status bar ────────────────────────────────────────────────

def _status_item(label: str, configured, detail, icon_name: str) -> rx.Component:
    """Inline config status: dot + icon + label + detail or 'Not configured' link."""
    return rx.hstack(
        rx.box(
            class_name=rx.cond(
                configured,
                "status-dot status-dot-success",
                "status-dot status-dot-error status-dot-pulse",
            ),
        ),
        rx.icon(icon_name, size=14, color="gray.9"),
        rx.cond(
            configured,
            rx.text(
                rx.text(label + ": ", weight="medium", as_="span"),
                rx.text(detail, as_="span"),
                size="2",
                color="gray.11",
            ),
            rx.link(
                rx.text(label + ": Configure →", size="2", color="accent.11"),
                href="/settings",
                underline="none",
                _hover={"opacity": 0.8},
            ),
        ),
        spacing="2",
        align="center",
    )


def _config_status_bar() -> rx.Component:
    """Compact horizontal status bar for Plex / AI / Embeddings."""
    return rx.hstack(
        _status_item(
            "Plex",
            DashboardState.plex_configured,
            DashboardState.plex_library_name,
            "server",
        ),
        rx.separator(orientation="vertical", size="1", style={"height": "20px"}),
        _status_item(
            "AI",
            DashboardState.ai_provider_configured,
            DashboardState.ai_provider_name,
            "brain",
        ),
        rx.separator(orientation="vertical", size="1", style={"height": "20px"}),
        _status_item(
            "Embeddings",
            DashboardState.embedding_provider_configured,
            DashboardState.embedding_provider_name,
            "layers",
        ),
        spacing="4",
        align="center",
        wrap="wrap",
        padding_y="12px",
        padding_x="16px",
        border_radius="var(--radius-lg)",
        background_color="gray.2",
        width="100%",
        class_name="animate-fade-in-up stagger-2",
    )


# ── Dimension warning banner ─────────────────────────────────────────

def _dimension_warning() -> rx.Component:
    return rx.cond(
        DashboardState.embedding_dimension_warning != "",
        rx.hstack(
            rx.icon("triangle-alert", size=14, color="yellow.9"),
            rx.text(
                DashboardState.embedding_dimension_warning,
                size="2",
                color="yellow.11",
            ),
            spacing="2",
            align="center",
            padding_x="16px",
            padding_y="10px",
            border_radius="var(--radius-md)",
            background_color="yellow.3",
            width="100%",
        ),
        rx.fragment(),
    )


# ── Quick action card ────────────────────────────────────────────────

def _action_card(
    title: str,
    description: str,
    icon_name: str,
    href: str,
    disabled,
    disabled_tooltip: str,
    accent: bool = False,
) -> rx.Component:
    """Clickable action card with icon, description, left accent border, and hover lift."""
    border_color = "accent.9" if accent else "gray.6"
    icon_color = "accent.9" if accent else "gray.9"

    card = rx.link(
        rx.card(
            rx.hstack(
                rx.box(
                    rx.icon(icon_name, size=22, color=icon_color),
                    padding="10px",
                    border_radius="var(--radius-md)",
                    background_color="accent.3" if accent else "gray.3",
                    flex_shrink="0",
                ),
                rx.vstack(
                    rx.text(title, size="3", weight="bold"),
                    rx.text(description, size="2", color="gray.9"),
                    spacing="1",
                    align="start",
                ),
                spacing="4",
                align="center",
                width="100%",
            ),
            width="100%",
            style={
                "borderLeft": f"3px solid var(--{border_color.replace('.', '-')})"
                if not accent
                else "3px solid var(--accent-9)",
                "cursor": "pointer",
            },
            class_name="hover-lift",
        ),
        href=href,
        underline="none",
        width="100%",
        style={
            "opacity": rx.cond(disabled, "0.5", "1"),
            "pointerEvents": rx.cond(disabled, "none", "auto"),
        },
        title=rx.cond(disabled, disabled_tooltip, ""),
    )
    return card


# ── Recent playlists ─────────────────────────────────────────────────

def _recent_playlist_row(playlist: dict) -> rx.Component:
    return rx.hstack(
        rx.icon("list-music", size=14, color="gray.9"),
        rx.text(playlist["name"], size="2", weight="medium", flex="1"),
        rx.text(
            playlist["track_count"] + " tracks",
            size="1",
            color="gray.9",
        ),
        rx.text(
            playlist["created_at"],
            size="1",
            color="gray.9",
            style={"fontFamily": "var(--font-mono)"},
        ),
        spacing="3",
        align="center",
        width="100%",
        padding_y="6px",
        padding_x="8px",
        border_radius="var(--radius-sm)",
        _hover={"background_color": "gray.3"},
        transition="background-color 150ms ease",
    )


def _recent_playlists_section() -> rx.Component:
    return rx.cond(
        DashboardState.recent_playlists.length() > 0,
        rx.vstack(
            rx.hstack(
                rx.text("Recent Playlists", size="4", weight="bold"),
                rx.spacer(),
                rx.link(
                    rx.text("View all →", size="2", color="accent.11"),
                    href="/history",
                    underline="none",
                    _hover={"opacity": 0.8},
                ),
                width="100%",
                align="center",
            ),
            rx.vstack(
                rx.foreach(
                    DashboardState.recent_playlists[:5],
                    _recent_playlist_row,
                ),
                spacing="0",
                width="100%",
            ),
            spacing="3",
            width="100%",
            class_name="animate-fade-in-up stagger-4",
        ),
        rx.fragment(),
    )


# ══════════════════════════════════════════════════════════════════════
#  Dashboard Page
# ══════════════════════════════════════════════════════════════════════

def dashboard() -> rx.Component:
    content = rx.vstack(
        # ── Page header ───────────────────────────────────────────
        rx.vstack(
            rx.heading("Dashboard", size="8"),
            rx.text("Your library at a glance", size="3", color="gray.9"),
            spacing="1",
            align="start",
        ),

        # ── Stats row ─────────────────────────────────────────────
        rx.grid(
            _stat_tile(
                "Total Tracks",
                DashboardState.total_tracks,
                "music",
                icon_color="accent.9",
                icon_bg="accent.3",
                stagger="stagger-1",
            ),
            _stat_tile(
                "Embedded",
                DashboardState.embedded_tracks,
                "cpu",
                icon_color="blue.9",
                icon_bg="blue.3",
                stagger="stagger-2",
            ),
            _stat_tile(
                "Audio Analyzed",
                DashboardState.audio_analyzed_tracks,
                "audio-waveform",
                icon_color="green.9",
                icon_bg="green.3",
                stagger="stagger-3",
            ),
            _stat_tile(
                "Last Sync",
                rx.cond(DashboardState.last_sync, DashboardState.last_sync, "Never"),
                "clock",
                icon_color="gray.9",
                icon_bg="gray.3",
                stagger="stagger-4",
            ),
            columns=rx.breakpoints(initial="2", md="4"),
            spacing="6",
            width="100%",
        ),

        # ── Divider ──────────────────────────────────────────────
        rx.separator(size="4", color_scheme="gray"),

        # ── Config status bar ────────────────────────────────────
        _config_status_bar(),

        # ── Dimension warning ────────────────────────────────────
        _dimension_warning(),

        # ── Quick actions ────────────────────────────────────────
        rx.text("Quick Actions", size="4", weight="bold"),
        rx.grid(
            _action_card(
                "Generate Playlist",
                "Create an AI-powered playlist from a mood",
                "sparkles",
                "/generator",
                disabled=~(
                    DashboardState.plex_configured
                    & DashboardState.ai_provider_configured
                ),
                disabled_tooltip="Configure Plex and AI provider first",
                accent=True,
            ),
            _action_card(
                "Sync Library",
                "Pull latest tracks from your Plex server",
                "refresh-cw",
                "/library",
                disabled=~DashboardState.plex_configured,
                disabled_tooltip="Configure Plex first",
                accent=False,
            ),
            columns=rx.breakpoints(initial="1", md="2"),
            spacing="4",
            width="100%",
            class_name="animate-fade-in-up stagger-3",
        ),

        # ── Recent playlists ─────────────────────────────────────
        _recent_playlists_section(),

        spacing="6",
        width="100%",
    )
    return layout(content)
