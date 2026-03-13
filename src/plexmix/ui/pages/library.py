import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.track_table import track_table
from plexmix.ui.components.progress_modal import progress_modal
from plexmix.ui.components.loading import skeleton_table
from plexmix.ui.components.error import empty_state
from plexmix.ui.states.library_state import LibraryState


# ── Page header ──────────────────────────────────────────────────────

def _page_header() -> rx.Component:
    """Page title with muted track count."""
    return rx.vstack(
        rx.heading("Library", size="8"),
        rx.text(
            f"{LibraryState.total_filtered_tracks} tracks",
            size="3",
            color="gray.9",
        ),
        spacing="1",
        align="start",
        class_name="animate-fade-in-up",
    )


# ── Command bar (sync controls + filter toggle) ─────────────────────

def _command_bar() -> rx.Component:
    """Top command bar: sync mode + sync button on left, select-page on right."""
    return rx.hstack(
        # Left: sync controls
        rx.hstack(
            rx.select(
                ["incremental", "regenerate"],
                value=LibraryState.sync_mode,
                on_change=LibraryState.set_sync_mode,
                placeholder="Sync Mode",
                size="2",
            ),
            rx.cond(
                LibraryState.sync_mode == "regenerate",
                rx.button(
                    rx.icon("triangle-alert", size=14),
                    "Regenerate",
                    on_click=LibraryState.confirm_regenerate_sync,
                    disabled=LibraryState.is_syncing | ~LibraryState.plex_configured,
                    loading=LibraryState.is_syncing,
                    color_scheme="red",
                    size="2",
                    title=rx.cond(
                        ~LibraryState.plex_configured, "Configure Plex first", ""
                    ),
                ),
                rx.button(
                    rx.icon("refresh-cw", size=14),
                    "Sync Library",
                    on_click=LibraryState.start_sync,
                    disabled=LibraryState.is_syncing | ~LibraryState.plex_configured,
                    loading=LibraryState.is_syncing,
                    color_scheme="blue",
                    size="2",
                    title=rx.cond(
                        ~LibraryState.plex_configured, "Configure Plex first", ""
                    ),
                ),
            ),
            spacing="2",
            align="center",
        ),
        rx.spacer(),
        # Right: select page button
        rx.button(
            "Select Page",
            on_click=LibraryState.select_all_tracks,
            variant="soft",
            size="2",
        ),
        align="center",
        width="100%",
        class_name="animate-fade-in-up stagger-1",
    )


# ── Search and filter row ────────────────────────────────────────────

def _filter_row() -> rx.Component:
    """Horizontal search and filter controls."""
    return rx.hstack(
        rx.el.div(
            rx.input(
                placeholder="Search tracks, artists, albums...",
                value=LibraryState.search_query,
                on_change=LibraryState.set_search_query,
                size="2",
                width="100%",
            ),
            style={"flex": "1", "minWidth": "200px"},
        ),
        rx.input(
            placeholder="Filter by genre",
            value=LibraryState.genre_filter,
            on_change=LibraryState.set_genre_filter,
            size="2",
            width="180px",
        ),
        rx.hstack(
            rx.text("Year:", size="2", color="gray.9", white_space="nowrap"),
            rx.input(
                placeholder="Min",
                type="number",
                value=LibraryState.year_min,
                on_change=LibraryState.set_year_min,
                size="2",
                width="90px",
            ),
            rx.text("-", size="2", color="gray.9"),
            rx.input(
                placeholder="Max",
                type="number",
                value=LibraryState.year_max,
                on_change=LibraryState.set_year_max,
                size="2",
                width="90px",
            ),
            spacing="2",
            align="center",
        ),
        rx.button(
            rx.icon("x", size=14),
            "Clear",
            on_click=LibraryState.clear_filters,
            variant="ghost",
            size="2",
            color_scheme="gray",
        ),
        spacing="3",
        align="center",
        width="100%",
        wrap="wrap",
        padding_y="12px",
        padding_x="16px",
        border_radius="var(--radius-lg)",
        background_color="gray.2",
        class_name="animate-fade-in-up stagger-2",
    )


# ── Pagination controls ──────────────────────────────────────────────

def _pagination_controls() -> rx.Component:
    """Previous / page indicator / Next with showing-count text."""
    total_pages = rx.cond(
        LibraryState.total_filtered_tracks > 0,
        (LibraryState.total_filtered_tracks - 1) // LibraryState.page_size + 1,
        1,
    )

    # Calculate the range being shown
    range_start = (LibraryState.current_page - 1) * LibraryState.page_size + 1
    range_end = rx.cond(
        LibraryState.current_page * LibraryState.page_size
        < LibraryState.total_filtered_tracks,
        LibraryState.current_page * LibraryState.page_size,
        LibraryState.total_filtered_tracks,
    )

    return rx.hstack(
        rx.button(
            rx.icon("chevron-left", size=14),
            "Previous",
            on_click=LibraryState.previous_page,
            disabled=LibraryState.current_page == 1,
            variant="soft",
            size="2",
        ),
        rx.vstack(
            rx.text(
                f"Page {LibraryState.current_page} of {total_pages}",
                size="2",
                weight="medium",
            ),
            rx.text(
                f"Showing {range_start}\u2013{range_end} of {LibraryState.total_filtered_tracks} tracks",
                size="1",
                color="gray.9",
            ),
            spacing="0",
            align="center",
        ),
        rx.button(
            "Next",
            rx.icon("chevron-right", size=14),
            on_click=LibraryState.next_page,
            disabled=LibraryState.current_page >= total_pages,
            variant="soft",
            size="2",
        ),
        justify="center",
        align="center",
        spacing="4",
        width="100%",
        padding_y="8px",
    )


# ── Floating bulk actions bar ────────────────────────────────────────

def _floating_actions_bar() -> rx.Component:
    """Glass-morphism floating bar at the bottom when tracks are selected."""
    return rx.cond(
        LibraryState.selected_tracks.length() > 0,
        rx.box(
            rx.hstack(
                rx.text(
                    LibraryState.selected_tracks.length().to(str) + " selected",
                    size="2",
                    weight="bold",
                    color="gray.12",
                    white_space="nowrap",
                ),
                rx.separator(
                    orientation="vertical", size="1", style={"height": "20px"}
                ),
                rx.button(
                    rx.icon("cpu", size=14),
                    "Generate Embeddings",
                    on_click=LibraryState.generate_embeddings,
                    disabled=LibraryState.selected_tracks.length() == 0,
                    loading=LibraryState.is_embedding,
                    color_scheme="orange",
                    size="2",
                    title=rx.cond(
                        LibraryState.selected_tracks.length() == 0,
                        "Select tracks first",
                        "",
                    ),
                ),
                rx.button(
                    rx.icon("audio-waveform", size=14),
                    "Analyze Audio",
                    on_click=LibraryState.analyze_audio,
                    disabled=LibraryState.is_analyzing_audio,
                    loading=LibraryState.is_analyzing_audio,
                    color_scheme="purple",
                    size="2",
                    title=rx.cond(
                        LibraryState.is_analyzing_audio,
                        "Analysis in progress",
                        "",
                    ),
                ),
                rx.button(
                    rx.icon("x", size=14),
                    "Clear Selection",
                    on_click=LibraryState.clear_selection,
                    variant="ghost",
                    size="2",
                ),
                spacing="3",
                align="center",
                wrap="wrap",
                justify="center",
            ),
            class_name="glass animate-slide-up",
            position="fixed",
            bottom="24px",
            left="50%",
            transform="translateX(-50%)",
            z_index="50",
            padding_x="24px",
            padding_y="14px",
            border_radius="var(--radius-xl)",
            box_shadow="var(--shadow-lg)",
            max_width="90vw",
        ),
        rx.fragment(),
    )


# ── Modals (preserved exactly) ───────────────────────────────────────

def _sync_modal() -> rx.Component:
    return progress_modal(
        is_open=LibraryState.is_syncing,
        progress=LibraryState.sync_progress,
        message=LibraryState.sync_message,
        on_cancel=LibraryState.request_cancel_sync,
    )


def _cancel_confirm_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Cancel Sync?"),
            rx.alert_dialog.description(
                "Progress will be lost. Are you sure you want to cancel?"
            ),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.button("Continue Sync", variant="soft"),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Yes, Cancel",
                        on_click=LibraryState.cancel_sync,
                        color_scheme="red",
                    ),
                ),
                spacing="3",
                justify="end",
            ),
        ),
        open=LibraryState.show_cancel_confirm,
        on_open_change=LibraryState.dismiss_cancel_confirm,
    )


def _embedding_modal() -> rx.Component:
    return progress_modal(
        is_open=LibraryState.is_embedding,
        progress=LibraryState.embedding_progress,
        message=LibraryState.embedding_message,
        on_cancel=None,
    )


def _audio_modal() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                rx.dialog.title(
                    "Audio Analysis",
                    size="5",
                    weight="bold",
                ),
                rx.text(
                    LibraryState.audio_analysis_message,
                    size="3",
                    color="var(--pm-gray-11)",
                ),
                rx.vstack(
                    rx.progress(
                        value=LibraryState.audio_analysis_progress,
                        max=100,
                        width="100%",
                    ),
                    rx.hstack(
                        rx.text(
                            f"{LibraryState.audio_analysis_progress}%",
                            size="3",
                            weight="medium",
                            font_family="var(--font-mono)",
                            color="var(--pm-gray-11)",
                        ),
                        rx.spacer(),
                        rx.text(
                            LibraryState.audio_analysis_eta,
                            size="2",
                            color="gray.9",
                        ),
                        width="100%",
                    ),
                    spacing="2",
                    width="100%",
                ),
                rx.hstack(
                    rx.cond(
                        LibraryState.audio_analysis_paused,
                        rx.button(
                            rx.icon("play", size=14),
                            "Resume",
                            on_click=LibraryState.resume_audio_analysis,
                            color_scheme="green",
                            variant="soft",
                            size="2",
                        ),
                        rx.button(
                            rx.icon("pause", size=14),
                            "Pause",
                            on_click=LibraryState.pause_audio_analysis,
                            color_scheme="yellow",
                            variant="soft",
                            size="2",
                        ),
                    ),
                    rx.button(
                        rx.icon("square", size=14),
                        "Stop",
                        on_click=LibraryState.request_cancel_audio,
                        color_scheme="red",
                        variant="soft",
                        size="2",
                    ),
                    spacing="3",
                    justify="end",
                    width="100%",
                ),
                spacing="4",
                width=rx.breakpoints(initial="90vw", sm="420px"),
                padding="4",
            ),
            class_name="animate-scale-in",
        ),
        open=LibraryState.is_analyzing_audio,
    )


def _audio_cancel_confirm_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Stop Audio Analysis?"),
            rx.alert_dialog.description(
                "Progress so far will be saved. You can resume analysis later."
            ),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.button("Continue", variant="soft"),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Yes, Stop",
                        on_click=LibraryState.cancel_audio_analysis,
                        color_scheme="red",
                    ),
                ),
                spacing="3",
                justify="end",
            ),
        ),
        open=LibraryState.show_audio_cancel_confirm,
        on_open_change=LibraryState.dismiss_audio_cancel_confirm,
    )


def _confirm_regenerate_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Confirm Regenerate Sync"),
            rx.alert_dialog.description(
                rx.vstack(
                    rx.text(
                        "This will DELETE ALL existing tags and embeddings!",
                        color="red",
                        weight="bold",
                    ),
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


# ══════════════════════════════════════════════════════════════════════
#  Library Page
# ══════════════════════════════════════════════════════════════════════

def library() -> rx.Component:
    content = rx.vstack(
        # ── Page header ──────────────────────────────────────────
        _page_header(),

        # ── Command bar (sync + select) ──────────────────────────
        _command_bar(),

        # ── Search / filters ─────────────────────────────────────
        _filter_row(),

        # ── Separator ────────────────────────────────────────────
        rx.separator(size="4", color_scheme="gray"),

        # ── Table or loading/empty states ────────────────────────
        rx.cond(
            LibraryState.is_page_loading,
            skeleton_table(rows=10),
            rx.cond(
                LibraryState.tracks.length() > 0,
                rx.vstack(
                    track_table(
                        LibraryState.tracks,
                        LibraryState.selected_tracks,
                        LibraryState.toggle_track_selection,
                        sort_column=LibraryState.sort_column,
                        sort_ascending=LibraryState.sort_ascending,
                        on_sort=LibraryState.set_sort,
                        all_selected=LibraryState.all_page_selected,
                        on_toggle_all=LibraryState.toggle_select_all,
                    ),
                    _pagination_controls(),
                    spacing="4",
                    width="100%",
                    class_name="animate-fade-in-up stagger-3",
                ),
                empty_state(
                    icon="library",
                    title="No tracks found",
                    description="Sync your library from Plex, or adjust your search filters.",
                    action_text="Go to Settings",
                    on_action=lambda: rx.redirect("/settings"),
                ),
            ),
        ),

        # ── Floating selection bar ───────────────────────────────
        _floating_actions_bar(),

        spacing="6",
        width="100%",
        # Add bottom padding so table content isn't hidden behind floating bar
        padding_bottom="80px",
    )

    return layout(
        rx.fragment(
            content,
            _sync_modal(),
            _cancel_confirm_dialog(),
            _embedding_modal(),
            _audio_modal(),
            _audio_cancel_confirm_dialog(),
            _confirm_regenerate_dialog(),
        )
    )
