"""Dashboard — library at a glance: stat tiles, config status, quick actions, recent."""

import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.states.dashboard_state import DashboardState


def _stat_tile(label: str, value, icon: str, color: str, bg: str) -> rx.Component:
    return rx.box(
        rx.box(
            rx.icon(icon, size=20, color=color),
            class_name="ico",
            style={"background": bg},
        ),
        rx.box(
            rx.box(value, class_name="num"),
            rx.box(label, class_name="lab"),
        ),
        class_name="tile stat-tile",
    )


def _status_item(label: str, configured, detail, icon: str) -> rx.Component:
    return rx.box(
        rx.box(
            class_name=rx.cond(configured, "dot dot-success", "dot dot-error pulse"),
        ),
        rx.icon(icon, size=14, color="var(--fg-3)"),
        rx.cond(
            configured,
            rx.el.span(
                rx.el.span(label + ": ", style={"color": "var(--fg-3)"}),
                detail,
                style={"fontSize": "13px"},
            ),
            rx.link(
                label + ": Configure →",
                href="/settings",
                style={"fontSize": "13px", "color": "var(--accent-fg)"},
                underline="none",
            ),
        ),
        class_name="item",
    )


def _status_bar() -> rx.Component:
    return rx.box(
        _status_item(
            "Plex", DashboardState.plex_configured, DashboardState.plex_library_name, "server"
        ),
        rx.box(class_name="vsep"),
        _status_item(
            "AI", DashboardState.ai_provider_configured, DashboardState.ai_provider_name, "brain"
        ),
        rx.box(class_name="vsep"),
        _status_item(
            "Embeddings",
            DashboardState.embedding_provider_configured,
            DashboardState.embedding_provider_name,
            "layers",
        ),
        class_name="card statusbar",
    )


def _quick_action(
    title: str, desc: str, icon: str, href: str, color: str, bg: str, primary: bool
) -> rx.Component:
    return rx.link(
        rx.box(
            rx.box(rx.icon(icon, size=22, color=color), class_name="ico", style={"background": bg}),
            rx.box(
                rx.box(title, class_name="qt"),
                rx.box(desc, class_name="qd"),
                style={"flex": "1"},
            ),
            rx.icon("arrow-right", size=18, class_name="arrow"),
            class_name="card qa primary hover-lift" if primary else "card qa hover-lift",
        ),
        href=href,
        underline="none",
    )


def _recent_row(playlist: rx.Var) -> rx.Component:
    return rx.box(
        rx.icon("list-music", size=14, color="var(--fg-3)"),
        rx.el.span(playlist["name"], style={"flex": "1", "fontWeight": "500", "fontSize": "14px"}),
        rx.el.span(
            playlist["track_count"], " tracks", class_name="fg3", style={"fontSize": "13px"}
        ),
        rx.el.span(playlist["created_at"], class_name="mono fg3", style={"fontSize": "12px"}),
        style={
            "display": "flex",
            "alignItems": "center",
            "gap": "12px",
            "padding": "10px 12px",
            "borderRadius": "var(--radius-sm)",
        },
        class_name="hover-row",
    )


def dashboard() -> rx.Component:
    content = rx.vstack(
        rx.box(
            _stat_tile(
                "Total Tracks",
                DashboardState.total_tracks,
                "music",
                "var(--brand-9)",
                "var(--brand-3)",
            ),
            _stat_tile(
                "Embedded",
                DashboardState.embedded_tracks,
                "cpu",
                "var(--pm-info)",
                "var(--info-bg)",
            ),
            _stat_tile(
                "Audio Analyzed",
                DashboardState.audio_analyzed_tracks,
                "audio-waveform",
                "var(--pm-success)",
                "var(--success-bg)",
            ),
            _stat_tile(
                "MB Enriched",
                DashboardState.musicbrainz_enriched_tracks,
                "disc",
                "var(--pm-purple)",
                "var(--purple-bg)",
            ),
            class_name="stat-grid",
            style={"width": "100%"},
        ),
        _status_bar(),
        rx.cond(
            DashboardState.embedding_dimension_warning != "",
            rx.box(
                rx.box(rx.icon("triangle-alert", size=16), class_name="c-ico"),
                rx.box(DashboardState.embedding_dimension_warning, class_name="c-body"),
                class_name="callout callout-warning",
            ),
            rx.fragment(),
        ),
        rx.box(
            rx.el.h2("Quick Actions", style={"fontSize": "17px", "fontWeight": "700"}),
            class_name="section-head",
            style={"width": "100%"},
        ),
        rx.box(
            _quick_action(
                "Generate Playlist",
                "Create an AI-powered playlist from a mood",
                "sparkles",
                "/generator",
                "var(--brand-9)",
                "var(--brand-3)",
                True,
            ),
            _quick_action(
                "Sync Library",
                "Pull latest tracks from your Plex server",
                "refresh-cw",
                "/library",
                "var(--fg-2)",
                "var(--surface-sunken)",
                False,
            ),
            class_name="qa-grid",
            style={"width": "100%"},
        ),
        rx.cond(
            DashboardState.recent_playlists.length() > 0,
            rx.box(
                rx.box(
                    rx.el.h2("Recent Playlists", style={"fontSize": "17px", "fontWeight": "700"}),
                    rx.link("View all →", href="/history", class_name="more", underline="none"),
                    class_name="section-head",
                ),
                rx.box(
                    rx.foreach(DashboardState.recent_playlists[:5], _recent_row),
                    class_name="card",
                    style={"padding": "6px"},
                ),
                style={"width": "100%"},
            ),
            rx.fragment(),
        ),
        spacing="5",
        width="100%",
        on_mount=DashboardState.on_load,
    )
    return layout(content)
