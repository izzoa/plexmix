"""History — saved & generated playlists as album-mosaic cards + detail modal."""

import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.states.history_state import HistoryState

_COVER_GRADIENTS = [
    "linear-gradient(135deg, #F97316, #EA580C)",
    "linear-gradient(135deg, #e94560, #f39c12)",
    "linear-gradient(135deg, #d68034, #5c2d0f)",
    "linear-gradient(135deg, #f3c9a8, #b55f18)",
]


def _pl_card(pl: rx.Var) -> rx.Component:
    return rx.box(
        rx.box(
            *[rx.box(style={"background": g}) for g in _COVER_GRADIENTS],
            rx.box(
                rx.box(rx.icon("play", size=18), class_name="pob"),
                class_name="pl-overlay",
            ),
            class_name="pl-cover",
        ),
        rx.box(
            rx.box(pl["name"], class_name="pl-name"),
            rx.box(pl["track_count"], " tracks", class_name="pl-meta"),
            class_name="pl-body",
        ),
        class_name="card pl-card hover-lift",
        on_click=HistoryState.select_playlist(pl["id"]),
    )


def _detail_track(track: rx.Var) -> rx.Component:
    return rx.box(
        rx.box(
            rx.box(track["title"], style={"fontWeight": "500", "fontSize": "14px"}),
            rx.box(track["artist"], class_name="fg3", style={"fontSize": "13px"}),
            style={"flex": "1", "minWidth": "0"},
        ),
        style={"padding": "8px 4px", "borderBottom": "1px solid var(--border-subtle)"},
    )


def _detail_modal() -> rx.Component:
    return rx.cond(
        HistoryState.is_detail_modal_open,
        rx.box(
            rx.box(
                rx.box(
                    rx.el.h2(
                        HistoryState.selected_playlist["name"],
                        style={"fontSize": "23px", "fontWeight": "700"},
                    ),
                    rx.el.button(
                        rx.icon("x", size=18),
                        class_name="icon-btn",
                        on_click=HistoryState.close_detail_modal,
                        type="button",
                    ),
                    class_name="modal-head",
                ),
                rx.box(
                    rx.foreach(HistoryState.selected_playlist_tracks, _detail_track),
                    class_name="modal-body",
                ),
                rx.box(
                    rx.el.button(
                        rx.icon("refresh-cw", size=16),
                        "Rerun",
                        class_name="btn btn-3 btn-primary",
                        on_click=HistoryState.rerun_playlist,
                        type="button",
                    ),
                    rx.el.button(
                        rx.icon("server", size=16),
                        "Export to Plex",
                        class_name="btn btn-3 btn-blue",
                        on_click=HistoryState.export_to_plex(HistoryState.selected_playlist["id"]),
                        type="button",
                    ),
                    rx.el.button(
                        rx.icon("download", size=16),
                        "Export M3U",
                        class_name="btn btn-3 btn-soft",
                        on_click=HistoryState.export_to_m3u(HistoryState.selected_playlist["id"]),
                        type="button",
                    ),
                    class_name="modal-foot",
                ),
                class_name="modal",
            ),
            class_name="modal-backdrop",
        ),
        rx.fragment(),
    )


def history() -> rx.Component:
    content = rx.vstack(
        rx.box(
            rx.box(
                rx.icon("search", size=15, color="var(--fg-3)"),
                rx.el.input(
                    placeholder="Search playlists…",
                    value=HistoryState.search_query,
                    on_change=HistoryState.set_search_query,
                ),
                class_name="search",
            ),
            rx.el.button(
                rx.icon("arrow-up-down", size=14),
                HistoryState.sort_by_label,
                class_name="btn btn-3 btn-soft",
                on_click=HistoryState.toggle_sort_order,
                type="button",
            ),
            class_name="filterbar",
        ),
        rx.cond(
            HistoryState.filtered_playlists.length() > 0,
            rx.box(
                rx.foreach(HistoryState.filtered_playlists, _pl_card),
                class_name="pl-grid",
                style={"width": "100%"},
            ),
            rx.box(
                rx.box(rx.icon("inbox", size=22, color="var(--fg-3)"), class_name="e-ico"),
                rx.box("No playlists yet", class_name="e-title"),
                rx.box(
                    "Generate a playlist to see it here.",
                    class_name="e-desc",
                ),
                class_name="empty",
                style={"width": "100%"},
            ),
        ),
        _detail_modal(),
        spacing="4",
        width="100%",
        on_mount=HistoryState.on_load,
    )
    return layout(content)
