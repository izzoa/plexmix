import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.track_table import track_table
from plexmix.ui.components.progress_modal import progress_modal
from plexmix.ui.states.library_state import LibraryState


def action_bar() -> rx.Component:
    return rx.hstack(
        rx.hstack(
            rx.select(
                ["incremental", "regenerate"],
                value=LibraryState.sync_mode,
                on_change=LibraryState.set_sync_mode,
                placeholder="Sync Mode",
                size="3",
            ),
            rx.cond(
                LibraryState.sync_mode == "regenerate",
                rx.button(
                    "⚠️ Regenerate (Destructive)",
                    on_click=LibraryState.confirm_regenerate_sync,
                    disabled=LibraryState.is_syncing | ~LibraryState.plex_configured,
                    loading=LibraryState.is_syncing,
                    color_scheme="red",
                    size="3",
                ),
                rx.button(
                    "Sync Library",
                    on_click=LibraryState.start_sync,
                    disabled=LibraryState.is_syncing | ~LibraryState.plex_configured,
                    loading=LibraryState.is_syncing,
                    color_scheme="blue",
                    size="3",
                ),
            ),
            rx.button(
                "Clear Filters",
                on_click=LibraryState.clear_filters,
                variant="soft",
                size="3",
            ),
            spacing="3",
        ),
        rx.hstack(
            rx.text(
                f"Selected: {LibraryState.selected_tracks.length()} tracks",
                size="3",
                color_scheme="gray",
            ),
            rx.button(
                "Select Page",
                on_click=LibraryState.select_all_tracks,
                variant="soft",
                size="2",
            ),
            rx.button(
                "Clear Selection",
                on_click=LibraryState.clear_selection,
                variant="soft",
                size="2",
            ),
            rx.button(
                "Generate Embeddings",
                on_click=LibraryState.generate_embeddings,
                disabled=LibraryState.selected_tracks.length() == 0,
                loading=LibraryState.is_embedding,
                color_scheme="orange",
                size="3",
            ),
            spacing="3",
        ),
        justify="between",
        align="center",
        width="100%",
        padding="4",
    )


def search_and_filters() -> rx.Component:
    return rx.hstack(
        rx.input(
            placeholder="Search tracks, artists, albums...",
            value=LibraryState.search_query,
            on_change=LibraryState.set_search_query,
            width="400px",
        ),
        rx.input(
            placeholder="Filter by genre",
            value=LibraryState.genre_filter,
            on_change=LibraryState.set_genre_filter,
            width="200px",
        ),
        rx.hstack(
            rx.text("Year:", size="3"),
            rx.input(
                placeholder="Min",
                type="number",
                value=LibraryState.year_min,
                on_change=LibraryState.set_year_min,
                width="100px",
            ),
            rx.text("-", size="3"),
            rx.input(
                placeholder="Max",
                type="number",
                value=LibraryState.year_max,
                on_change=LibraryState.set_year_max,
                width="100px",
            ),
            spacing="2",
        ),
        spacing="4",
        align="center",
        width="100%",
        padding="4",
    )


def pagination_controls() -> rx.Component:
    total_pages = rx.cond(
        LibraryState.total_filtered_tracks > 0,
        (LibraryState.total_filtered_tracks - 1) // LibraryState.page_size + 1,
        1
    )

    return rx.hstack(
        rx.button(
            "Previous",
            on_click=LibraryState.previous_page,
            disabled=LibraryState.current_page == 1,
            variant="soft",
            size="2",
        ),
        rx.text(
            f"Page {LibraryState.current_page} of {total_pages}",
            size="3",
        ),
        rx.text(
            f"({LibraryState.total_filtered_tracks} total tracks)",
            size="2",
            color_scheme="gray",
        ),
        rx.button(
            "Next",
            on_click=LibraryState.next_page,
            disabled=LibraryState.current_page >= total_pages,
            variant="soft",
            size="2",
        ),
        justify="center",
        align="center",
        spacing="4",
        padding="4",
        width="100%",
    )


def library() -> rx.Component:
    content = rx.container(
        rx.vstack(
            rx.heading("Library", size="8", margin_bottom="6"),
            action_bar(),
            search_and_filters(),
            rx.divider(),
            rx.cond(
                LibraryState.tracks.length() > 0,
                rx.vstack(
                    track_table(LibraryState.tracks, LibraryState.selected_tracks, LibraryState.toggle_track_selection),
                    pagination_controls(),
                    spacing="4",
                    width="100%",
                ),
                rx.text(
                    "No tracks found. Try syncing your library or adjusting filters.",
                    size="4",
                    color_scheme="gray",
                    text_align="center",
                    padding="8",
                ),
            ),
            spacing="4",
            width="100%",
        ),
        size="4",
    )

    sync_modal = progress_modal(
        is_open=LibraryState.is_syncing,
        progress=LibraryState.sync_progress,
        message=LibraryState.sync_message,
        on_cancel=LibraryState.cancel_sync,
    )

    embedding_modal = progress_modal(
        is_open=LibraryState.is_embedding,
        progress=LibraryState.embedding_progress,
        message=LibraryState.embedding_message,
        on_cancel=None,
    )

    confirm_regenerate_dialog = rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("⚠️ Confirm Regenerate Sync"),
            rx.alert_dialog.description(
                rx.vstack(
                    rx.text("This will DELETE ALL existing tags and embeddings!", color="red", weight="bold"),
                    rx.text("This operation will:"),
                    rx.unordered_list(
                        rx.list_item("Clear all AI-generated tags"),
                        rx.list_item("Delete all embeddings"),
                        rx.list_item("Regenerate everything from scratch"),
                    ),
                    rx.text("Are you sure you want to continue?", weight="bold"),
                    spacing="2",
                    align_items="start",
                )
            ),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.button(
                        "Cancel",
                        variant="soft",
                        on_click=LibraryState.cancel_regenerate_confirm,
                    ),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Yes, Regenerate",
                        on_click=LibraryState.start_sync,
                        color_scheme="red",
                    ),
                ),
                spacing="3",
                justify="end",
            ),
        ),
        open=LibraryState.show_regenerate_confirm,
    )

    return layout(rx.fragment(content, sync_modal, embedding_modal, confirm_regenerate_dialog))
