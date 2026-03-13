import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.loading import skeleton_card, loading_spinner
from plexmix.ui.components.error import error_message, empty_state
from plexmix.ui.states.history_state import HistoryState


# ── Skeleton loading rows ────────────────────────────────────────────

def _skeleton_row() -> rx.Component:
    """Skeleton row that mimics the playlist list layout."""
    return rx.hstack(
        # Left side: name + mood placeholder
        rx.vstack(
            rx.box(
                width="180px",
                height="18px",
                border_radius="var(--radius-sm)",
                class_name="skeleton",
            ),
            rx.box(
                width="260px",
                height="14px",
                border_radius="var(--radius-sm)",
                class_name="skeleton",
            ),
            spacing="2",
            align="start",
            flex="1",
        ),
        # Right side: pill + date + icons placeholder
        rx.hstack(
            rx.box(
                width="70px",
                height="22px",
                border_radius="var(--radius-xl)",
                class_name="skeleton",
            ),
            rx.box(
                width="100px",
                height="14px",
                border_radius="var(--radius-sm)",
                class_name="skeleton",
            ),
            rx.hstack(
                rx.box(width="28px", height="28px", border_radius="var(--radius-md)", class_name="skeleton"),
                rx.box(width="28px", height="28px", border_radius="var(--radius-md)", class_name="skeleton"),
                rx.box(width="28px", height="28px", border_radius="var(--radius-md)", class_name="skeleton"),
                spacing="1",
            ),
            spacing="4",
            align="center",
        ),
        align="center",
        width="100%",
        padding_x="16px",
        padding_y="14px",
        border_bottom="1px solid var(--gray-a4)",
    )


def _skeleton_list() -> rx.Component:
    """Show 6 skeleton rows mimicking the list layout."""
    return rx.vstack(
        *[_skeleton_row() for _ in range(6)],
        spacing="0",
        width="100%",
    )


# ── Playlist row ─────────────────────────────────────────────────────

def _playlist_row(playlist: dict[str, str]) -> rx.Component:
    """Single playlist row: name + mood on left, pill + date + actions on right."""
    return rx.hstack(
        # Left side: name + mood query
        rx.vstack(
            rx.text(
                rx.cond(
                    playlist["name"],
                    playlist["name"],
                    "Unnamed Playlist",
                ),
                size="3",
                weight="bold",
                truncate=True,
            ),
            rx.text(
                rx.cond(
                    playlist["mood_query"],
                    playlist["mood_query"],
                    "No description",
                ),
                size="2",
                color="gray.9",
                truncate=True,
                style={"fontStyle": "italic"},
            ),
            spacing="1",
            align="start",
            flex="1",
            min_width="0",
        ),
        # Right side: track count pill + date + action buttons
        rx.hstack(
            # Track count pill
            rx.badge(
                rx.cond(
                    playlist["track_count"],
                    playlist["track_count"] + " tracks",
                    "0 tracks",
                ),
                variant="surface",
                size="1",
                color_scheme="gray",
            ),
            # Date
            rx.text(
                rx.cond(
                    playlist["created_at"],
                    playlist["created_at"],
                    "Unknown date",
                ),
                size="1",
                color="gray.9",
                style={"fontFamily": "var(--font-mono)", "whiteSpace": "nowrap"},
            ),
            # Action icon buttons
            rx.hstack(
                rx.icon_button(
                    rx.icon("eye", size=14),
                    on_click=lambda: HistoryState.select_playlist(playlist["id"]),
                    variant="ghost",
                    size="1",
                    color_scheme="gray",
                    title="View details",
                    cursor="pointer",
                ),
                rx.icon_button(
                    rx.icon("upload", size=14),
                    on_click=lambda: HistoryState.export_to_plex(playlist["id"]),
                    variant="ghost",
                    size="1",
                    color_scheme="gray",
                    title="Export to Plex",
                    cursor="pointer",
                ),
                rx.icon_button(
                    rx.icon("download", size=14),
                    on_click=lambda: HistoryState.export_to_m3u(playlist["id"]),
                    variant="ghost",
                    size="1",
                    color_scheme="gray",
                    title="Export M3U",
                    cursor="pointer",
                ),
                rx.icon_button(
                    rx.icon("trash-2", size=14),
                    on_click=lambda: HistoryState.show_delete_confirmation(playlist["id"]),
                    variant="ghost",
                    size="1",
                    color_scheme="red",
                    title="Delete",
                    cursor="pointer",
                ),
                spacing="1",
            ),
            spacing="4",
            align="center",
            flex_shrink="0",
        ),
        align="center",
        width="100%",
        padding_x="16px",
        padding_y="12px",
        border_bottom="1px solid var(--gray-a4)",
        border_radius="var(--radius-sm)",
        cursor="pointer",
        on_click=lambda: HistoryState.select_playlist(playlist["id"]),
        _hover={"background_color": "gray.3"},
        transition="background-color 150ms ease",
    )


# ── Playlist list ────────────────────────────────────────────────────

def _playlist_list() -> rx.Component:
    """Main list of playlists with loading / empty states."""
    return rx.cond(
        HistoryState.loading_playlists,
        _skeleton_list(),
        rx.cond(
            HistoryState.filtered_playlists.length() > 0,
            rx.vstack(
                rx.foreach(
                    HistoryState.filtered_playlists,
                    _playlist_row,
                ),
                spacing="0",
                width="100%",
                class_name="animate-fade-in-up",
            ),
            _playlist_empty_state(),
        ),
    )


def _playlist_empty_state() -> rx.Component:
    return empty_state(
        icon="music",
        title="No playlists saved yet",
        description="Generate your first playlist from the Generator page",
        action_text="Go to Generator",
        on_action=lambda: rx.redirect("/generator"),
    )


# ── Search & sort controls ───────────────────────────────────────────

def _search_sort_bar() -> rx.Component:
    """Search input on the left, sort controls on the right."""
    return rx.hstack(
        # Search with icon
        rx.el.div(
            rx.icon(
                "search",
                size=14,
                color="gray.9",
                style={
                    "position": "absolute",
                    "left": "10px",
                    "top": "50%",
                    "transform": "translateY(-50%)",
                    "pointerEvents": "none",
                },
            ),
            rx.input(
                placeholder="Search playlists...",
                value=HistoryState.search_query,
                on_change=HistoryState.set_search_query,
                size="2",
                style={"paddingLeft": "32px"},
                width="100%",
            ),
            style={"position": "relative"},
            width=rx.breakpoints(initial="100%", md="320px"),
        ),
        rx.spacer(),
        # Sort controls
        rx.hstack(
            rx.text("Sort by:", size="2", color="gray.9", weight="medium"),
            rx.select(
                ["Date Created", "Name", "Track Count"],
                value=HistoryState.sort_by_label,
                on_change=HistoryState.sort_playlists_by_label,
                size="2",
            ),
            rx.icon_button(
                rx.cond(
                    HistoryState.sort_descending,
                    rx.icon("arrow-down", size=14),
                    rx.icon("arrow-up", size=14),
                ),
                on_click=HistoryState.toggle_sort_order,
                variant="ghost",
                size="1",
                color_scheme="gray",
                title="Toggle sort order",
                cursor="pointer",
            ),
            spacing="2",
            align="center",
        ),
        spacing="4",
        align="center",
        width="100%",
        wrap="wrap",
    )


# ── Detail modal ─────────────────────────────────────────────────────

def _detail_modal() -> rx.Component:
    """Polished detail dialog for viewing a selected playlist."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                # Header: playlist name + close button
                rx.hstack(
                    rx.text(
                        rx.cond(
                            HistoryState.selected_playlist,
                            rx.cond(
                                HistoryState.selected_playlist["name"],
                                HistoryState.selected_playlist["name"],
                                "Playlist",
                            ),
                            "Playlist",
                        ),
                        size="5",
                        weight="bold",
                        style={"fontFamily": "var(--font-display)"},
                    ),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.icon_button(
                            rx.icon("x", size=16),
                            variant="ghost",
                            size="1",
                            color_scheme="gray",
                            title="Close",
                            cursor="pointer",
                        ),
                    ),
                    width="100%",
                    align="center",
                ),

                # Metadata as inline pills
                rx.cond(
                    HistoryState.selected_playlist,
                    rx.vstack(
                        # Mood query (italic)
                        rx.cond(
                            HistoryState.selected_playlist["mood_query"],
                            rx.text(
                                HistoryState.selected_playlist["mood_query"],
                                size="2",
                                color="gray.9",
                                style={"fontStyle": "italic"},
                            ),
                            rx.fragment(),
                        ),
                        # Metadata pills
                        rx.hstack(
                            rx.badge(
                                rx.hstack(
                                    rx.icon("music", size=12),
                                    rx.text(
                                        HistoryState.selected_playlist_tracks.length().to(str)
                                        + " tracks",
                                    ),
                                    spacing="1",
                                    align="center",
                                ),
                                variant="surface",
                                size="1",
                                color_scheme="blue",
                            ),
                            rx.badge(
                                rx.hstack(
                                    rx.icon("clock", size=12),
                                    rx.text(
                                        rx.cond(
                                            HistoryState.selected_playlist[
                                                "total_duration_formatted"
                                            ],
                                            HistoryState.selected_playlist[
                                                "total_duration_formatted"
                                            ],
                                            "--",
                                        ),
                                    ),
                                    spacing="1",
                                    align="center",
                                ),
                                variant="surface",
                                size="1",
                                color_scheme="gray",
                            ),
                            rx.badge(
                                rx.hstack(
                                    rx.icon("calendar", size=12),
                                    rx.text(
                                        rx.cond(
                                            HistoryState.selected_playlist["created_at"],
                                            HistoryState.selected_playlist["created_at"],
                                            "Unknown",
                                        ),
                                    ),
                                    spacing="1",
                                    align="center",
                                ),
                                variant="surface",
                                size="1",
                                color_scheme="gray",
                            ),
                            spacing="2",
                            wrap="wrap",
                        ),
                        spacing="3",
                        width="100%",
                    ),
                    rx.fragment(),
                ),

                # Separator
                rx.separator(size="4", color_scheme="gray"),

                # Track list table
                rx.box(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell("#"),
                                rx.table.column_header_cell("Title"),
                                rx.table.column_header_cell("Artist"),
                                rx.table.column_header_cell("Album"),
                                rx.table.column_header_cell("Duration"),
                            )
                        ),
                        rx.table.body(
                            rx.foreach(
                                HistoryState.selected_playlist_tracks,
                                lambda track: rx.table.row(
                                    rx.table.cell(track["position"]),
                                    rx.table.cell(
                                        rx.text(track["title"], weight="medium"),
                                    ),
                                    rx.table.cell(track["artist"]),
                                    rx.table.cell(track["album"]),
                                    rx.table.cell(
                                        rx.text(
                                            track["duration_formatted"],
                                            style={"fontFamily": "var(--font-mono)"},
                                        ),
                                    ),
                                ),
                            )
                        ),
                        variant="surface",
                        size="2",
                        width="100%",
                    ),
                    max_height="400px",
                    overflow_x="auto",
                    overflow_y="auto",
                    width="100%",
                ),

                # Action buttons at bottom
                rx.hstack(
                    rx.button(
                        rx.hstack(
                            rx.icon("upload", size=14),
                            rx.text("Export to Plex"),
                            spacing="2",
                            align="center",
                        ),
                        on_click=lambda: HistoryState.export_to_plex(
                            HistoryState.selected_playlist["id"]
                        ),
                        color_scheme="blue",
                        size="2",
                    ),
                    rx.button(
                        rx.hstack(
                            rx.icon("download", size=14),
                            rx.text("Export M3U"),
                            spacing="2",
                            align="center",
                        ),
                        on_click=lambda: HistoryState.export_to_m3u(
                            HistoryState.selected_playlist["id"]
                        ),
                        variant="soft",
                        size="2",
                    ),
                    rx.button(
                        rx.hstack(
                            rx.icon("trash-2", size=14),
                            rx.text("Delete"),
                            spacing="2",
                            align="center",
                        ),
                        on_click=lambda: HistoryState.show_delete_confirmation(
                            HistoryState.selected_playlist["id"]
                        ),
                        color_scheme="red",
                        variant="soft",
                        size="2",
                    ),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.button(
                            "Close",
                            variant="outline",
                            color_scheme="gray",
                            size="2",
                        ),
                    ),
                    spacing="3",
                    width="100%",
                    wrap="wrap",
                ),

                spacing="4",
                width="100%",
            ),
            max_width=rx.breakpoints(initial="95vw", md="900px"),
            padding="24px",
        ),
        open=HistoryState.is_detail_modal_open,
        on_open_change=HistoryState.set_detail_modal_open,
    )


# ── Delete confirmation dialog ───────────────────────────────────────

def _delete_confirmation_dialog() -> rx.Component:
    """Clean delete confirmation alert dialog."""
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.vstack(
                rx.hstack(
                    rx.box(
                        rx.icon("triangle-alert", size=18, color="red.9"),
                        padding="8px",
                        border_radius="var(--radius-md)",
                        background_color="red.3",
                        flex_shrink="0",
                    ),
                    rx.vstack(
                        rx.alert_dialog.title(
                            "Delete Playlist",
                            size="4",
                            weight="bold",
                        ),
                        rx.alert_dialog.description(
                            "Are you sure you want to delete this playlist? This action cannot be undone.",
                            size="2",
                            color="gray.9",
                        ),
                        spacing="1",
                    ),
                    spacing="3",
                    align="start",
                    width="100%",
                ),
                rx.hstack(
                    rx.alert_dialog.cancel(
                        rx.button(
                            "Cancel",
                            variant="outline",
                            color_scheme="gray",
                            size="2",
                        ),
                    ),
                    rx.alert_dialog.action(
                        rx.button(
                            "Delete",
                            on_click=HistoryState.confirm_delete,
                            color_scheme="red",
                            size="2",
                        ),
                    ),
                    spacing="3",
                    justify="end",
                    width="100%",
                ),
                spacing="5",
            ),
            padding="24px",
        ),
        open=HistoryState.is_delete_confirmation_open,
        on_open_change=HistoryState.set_delete_confirmation_open,
    )


# ══════════════════════════════════════════════════════════════════════
#  History Page
# ══════════════════════════════════════════════════════════════════════

def history() -> rx.Component:
    content = rx.vstack(
        # ── Page header ───────────────────────────────────────────
        rx.hstack(
            rx.vstack(
                rx.heading("Playlist History", size="8"),
                rx.text(
                    rx.cond(
                        HistoryState.filtered_playlists.length() > 0,
                        HistoryState.filtered_playlists.length().to(str)
                        + " playlists",
                        "",
                    ),
                    size="3",
                    color="gray.9",
                ),
                spacing="1",
                align="start",
            ),
            width="100%",
        ),

        # ── Error message ─────────────────────────────────────────
        rx.cond(
            HistoryState.error_message != "",
            error_message(
                HistoryState.error_message,
                on_dismiss=lambda: HistoryState.set_error_message(""),
            ),
            rx.fragment(),
        ),

        # ── Search and sort controls ──────────────────────────────
        _search_sort_bar(),

        # ── Separator ─────────────────────────────────────────────
        rx.separator(size="4", color_scheme="gray"),

        # ── Playlist list ─────────────────────────────────────────
        _playlist_list(),

        # ── Modals ────────────────────────────────────────────────
        _detail_modal(),
        _delete_confirmation_dialog(),

        spacing="6",
        width="100%",
    )

    return layout(content)
