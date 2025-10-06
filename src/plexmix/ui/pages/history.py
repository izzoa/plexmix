import reflex as rx
from typing import Dict
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.loading import skeleton_card, loading_spinner
from plexmix.ui.components.error import error_message, empty_state
from plexmix.ui.states.history_state import HistoryState


def playlist_card(playlist: Dict) -> rx.Component:
    # In Reflex, we access dictionary values directly using bracket notation
    # and use rx.cond for default values

    return rx.card(
        rx.vstack(
            # Thumbnail placeholder
            rx.box(
                rx.icon("music", size=48, color="gray.500"),
                width="100%",
                height="150px",
                background="gray.100",
                border_radius="8px",
                display="flex",
                align_items="center",
                justify_content="center",
            ),

            # Playlist info
            rx.vstack(
                rx.text(
                    rx.cond(
                        playlist['name'],
                        playlist['name'],
                        'Unnamed Playlist'
                    ),
                    size="4",
                    weight="bold",
                    truncate=True,
                ),
                rx.text(
                    rx.cond(
                        playlist['mood_query'],
                        playlist['mood_query'],
                        'No description'
                    ),
                    size="2",
                    color_scheme="gray",
                    truncate=True,
                    max_lines=2,
                ),
                rx.hstack(
                    rx.text(
                        rx.cond(
                            playlist['track_count'],
                            playlist['track_count'].to_string() + " tracks",
                            "0 tracks"
                        ),
                        size="2",
                        color_scheme="gray",
                    ),
                    rx.text("•", size="2", color_scheme="gray"),
                    rx.text(
                        rx.cond(
                            playlist['created_at'],
                            playlist['created_at'],
                            'Unknown date'
                        ),
                        size="2",
                        color_scheme="gray",
                    ),
                    spacing="1",
                ),
                spacing="2",
                align="start",
                width="100%",
            ),

            # Action buttons (shown on hover)
            rx.hstack(
                rx.button(
                    "View",
                    on_click=lambda: HistoryState.select_playlist(playlist['id']),
                    variant="soft",
                    size="2",
                ),
                rx.button(
                    rx.icon("upload", size=16),
                    on_click=lambda: HistoryState.export_to_plex(playlist['id']),
                    variant="soft",
                    size="2",
                    title="Export to Plex",
                ),
                rx.button(
                    rx.icon("download", size=16),
                    on_click=lambda: HistoryState.export_to_m3u(playlist['id']),
                    variant="soft",
                    size="2",
                    title="Export M3U",
                ),
                rx.button(
                    rx.icon("trash-2", size=16),
                    on_click=lambda: HistoryState.show_delete_confirmation(playlist['id']),
                    variant="soft",
                    color_scheme="red",
                    size="2",
                    title="Delete",
                ),
                spacing="2",
                width="100%",
            ),

            spacing="3",
            padding="4",
            width="100%",
        ),
        width="100%",
        _hover={
            "box_shadow": "0 4px 12px rgba(0, 0, 0, 0.1)",
            "transform": "translateY(-2px)",
            "transition": "all 0.2s ease",
        },
    )


def playlist_grid() -> rx.Component:
    return rx.cond(
        HistoryState.loading_playlists,
        # Show skeleton cards while loading
        rx.grid(
            *[skeleton_card() for _ in range(4)],
            columns=rx.breakpoints(
                initial="1",
                sm="2",
                md="3",
                lg="4",
            ),
            spacing="4",
            width="100%",
        ),
        rx.cond(
            HistoryState.playlists.length() > 0,
            rx.grid(
                rx.foreach(
                    HistoryState.playlists,
                    playlist_card,
                ),
                columns=rx.breakpoints(
                    initial="1",
                    sm="2",
                    md="3",
                    lg="4",
                ),
                spacing="4",
                width="100%",
            ),
            playlist_empty_state(),
        ),
    )


def playlist_empty_state() -> rx.Component:
    return empty_state(
        icon="music",
        title="No playlists saved yet",
        description="Generate your first playlist from the Generator page",
        action_text="Go to Generator",
        on_action=lambda: rx.redirect("/generator"),
    )


def detail_modal() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                # Header
                rx.hstack(
                    rx.text(
                        rx.cond(
                            HistoryState.selected_playlist,
                            rx.cond(
                                HistoryState.selected_playlist['name'],
                                HistoryState.selected_playlist['name'],
                                'Playlist'
                            ),
                            "Playlist",
                        ),
                        size="6",
                        weight="bold",
                    ),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.button(
                            rx.icon("x", size=20),
                            variant="ghost",
                            size="2",
                        ),
                    ),
                    width="100%",
                    align="center",
                ),

                # Metadata
                rx.cond(
                    HistoryState.selected_playlist,
                    rx.vstack(
                        rx.text(
                            rx.cond(
                                HistoryState.selected_playlist['mood_query'],
                                HistoryState.selected_playlist['mood_query'],
                                ''
                            ),
                            size="3",
                            color_scheme="gray",
                            style={"fontStyle": "italic"},
                        ),
                        rx.hstack(
                            rx.text(
                                f"{HistoryState.selected_playlist_tracks.length()} tracks",
                                size="3",
                            ),
                            rx.text("•", size="3"),
                            rx.text(
                                rx.cond(
                                    HistoryState.selected_playlist['total_duration_formatted'],
                                    HistoryState.selected_playlist['total_duration_formatted'],
                                    "--",
                                ),
                                size="3",
                            ),
                            rx.text("•", size="3"),
                            rx.text(
                                "Created: ",
                                rx.cond(
                                    HistoryState.selected_playlist['created_at'],
                                    HistoryState.selected_playlist['created_at'],
                                    "Unknown"
                                ),
                                size="3",
                            ),
                            spacing="2",
                            color_scheme="gray",
                        ),
                        spacing="2",
                        width="100%",
                    ),
                    rx.text(),
                ),

                # Track list
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
                                    rx.table.cell(track['position']),
                                    rx.table.cell(track['title']),
                                    rx.table.cell(track['artist']),
                                    rx.table.cell(track['album']),
                                    rx.table.cell(track['duration_formatted']),
                                ),
                            )
                        ),
                        variant="surface",
                        size="2",
                        width="100%",
                    ),
                    max_height="400px",
                    overflow_y="auto",
                    width="100%",
                ),

                # Actions
                rx.hstack(
                    rx.button(
                        "Export to Plex",
                        on_click=lambda: HistoryState.export_to_plex(
                            HistoryState.selected_playlist['id']
                        ),
                        color_scheme="blue",
                        size="3",
                    ),
                    rx.button(
                        "Export M3U",
                        on_click=lambda: HistoryState.export_to_m3u(
                            HistoryState.selected_playlist['id']
                        ),
                        variant="soft",
                        size="3",
                    ),
                    rx.button(
                        "Delete Playlist",
                        on_click=lambda: HistoryState.show_delete_confirmation(
                            HistoryState.selected_playlist['id']
                        ),
                        color_scheme="red",
                        variant="soft",
                        size="3",
                    ),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.button(
                            "Close",
                            variant="soft",
                            size="3",
                        ),
                    ),
                    spacing="3",
                    width="100%",
                ),

                spacing="4",
                width="100%",
            ),
            max_width="900px",
            padding="6",
        ),
        open=HistoryState.is_detail_modal_open,
        on_open_change=HistoryState.set_detail_modal_open,
    )


def delete_confirmation_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Delete Playlist"),
            rx.alert_dialog.description(
                "Are you sure you want to delete this playlist? This action cannot be undone.",
            ),
            rx.flex(
                rx.alert_dialog.cancel(
                    rx.button(
                        "Cancel",
                        variant="soft",
                        color_scheme="gray",
                    ),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Delete",
                        on_click=HistoryState.confirm_delete,
                        color_scheme="red",
                    ),
                ),
                spacing="3",
                margin_top="4",
                justify="end",
            ),
        ),
        open=HistoryState.is_delete_confirmation_open,
    )


def history() -> rx.Component:
    content = rx.container(
        rx.vstack(
            rx.heading("Playlist History", size="8", margin_bottom="6"),

            # Error message
            rx.cond(
                HistoryState.error_message != "",
                error_message(
                    HistoryState.error_message,
                    on_dismiss=lambda: HistoryState.set_error_message(""),
                ),
                rx.box(),
            ),

            # Action message
            rx.cond(
                HistoryState.action_message != "",
                rx.callout.root(
                    rx.callout.text(HistoryState.action_message),
                    color="blue",
                ),
                rx.box(),
            ),

            # Sort controls
            rx.hstack(
                rx.text("Sort by:", size="3"),
                rx.select(
                    items=["created_date", "name", "track_count"],
                    placeholder="Sort by",
                    value=HistoryState.sort_by,
                    on_change=HistoryState.sort_playlists,
                    size="2",
                ),
                rx.button(
                    rx.cond(
                        HistoryState.sort_descending,
                        rx.icon("arrow_down", size=16),
                        rx.icon("arrow_up", size=16),
                    ),
                    on_click=HistoryState.toggle_sort_order,
                    variant="soft",
                    size="2",
                ),
                spacing="3",
                align="center",
            ),

            # Playlist grid
            playlist_grid(),

            # Detail modal
            detail_modal(),

            # Delete confirmation dialog
            delete_confirmation_dialog(),

            spacing="6",
            width="100%",
        ),
        size="4",
    )

    return layout(content)