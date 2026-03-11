import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.track_table import tag_badges
from plexmix.ui.components.error import empty_state
from plexmix.ui.states.tagging_state import TaggingState


def selection_panel() -> rx.Component:
    return rx.vstack(
        rx.heading("Tag Selection", size="5", weight="bold"),

        # Quick action button
        rx.button(
            "Tag All Untagged Tracks",
            on_click=TaggingState.show_tag_all_confirmation,
            color_scheme="orange",
            size="3",
            width="100%",
            disabled=TaggingState.is_tagging,
            title=rx.cond(TaggingState.is_tagging, "Tagging in progress", ""),
        ),

        rx.divider(margin_y="4"),
        rx.text("OR", size="3", weight="bold", text_align="center"),
        rx.divider(margin_y="4"),

        # Custom filters
        rx.vstack(
            rx.text("Custom Filters", size="4", weight="bold"),

            # Genre filter
            rx.input(
                placeholder="Filter by genre (e.g., rock, jazz)",
                value=TaggingState.genre_filter,
                on_change=TaggingState.set_genre_filter,
                width="100%",
            ),

            # Year range
            rx.hstack(
                rx.text("Year Range:", size="3"),
                rx.input(
                    placeholder="Min",
                    type="number",
                    value=TaggingState.year_min,
                    on_change=TaggingState.set_year_min,
                    width="100px",
                ),
                rx.text("-", size="3"),
                rx.input(
                    placeholder="Max",
                    type="number",
                    value=TaggingState.year_max,
                    on_change=TaggingState.set_year_max,
                    width="100px",
                ),
                spacing="2",
                align="center",
            ),

            # Artist filter
            rx.input(
                placeholder="Filter by artist",
                value=TaggingState.artist_filter,
                on_change=TaggingState.set_artist_filter,
                width="100%",
            ),

            # Has no tags checkbox
            rx.checkbox(
                "Only tracks without tags",
                checked=TaggingState.has_no_tags,
                on_change=TaggingState.toggle_has_no_tags,
            ),

            spacing="3",
            width="100%",
        ),

        # Preview and start buttons
        rx.hstack(
            rx.button(
                "Preview Selection",
                on_click=TaggingState.preview_selection,
                variant="soft",
                size="3",
            ),
            rx.cond(
                TaggingState.preview_count > 0,
                rx.text(
                    f"{TaggingState.preview_count} tracks match",
                    size="3",
                    weight="bold",
                    color_scheme="green",
                ),
                rx.text(),
            ),
            spacing="3",
            align="center",
        ),

        rx.button(
            "Start Tagging",
            on_click=TaggingState.start_tagging,
            disabled=(TaggingState.preview_count == 0) | TaggingState.is_tagging,
            loading=TaggingState.is_tagging,
            color_scheme="blue",
            size="4",
            width="100%",
            title=rx.cond(
                TaggingState.is_tagging,
                "Tagging in progress",
                rx.cond(TaggingState.preview_count == 0, "Preview selection first", ""),
            ),
        ),

        spacing="4",
        width="100%",
        padding="4",
        border="1px solid",
        border_color="gray.200",
        border_radius="8px",
    )


def progress_section() -> rx.Component:
    return rx.cond(
        TaggingState.is_tagging,
        rx.vstack(
            rx.heading("Tagging Progress", size="5", weight="bold"),

            # Overall progress bar
            rx.progress(
                value=TaggingState.tagging_progress,
                width="100%",
            ),

            # Batch info
            rx.hstack(
                rx.text(
                    f"Batch {TaggingState.current_batch}/{TaggingState.total_batches}",
                    size="3",
                ),
                rx.text(
                    f"• {TaggingState.tags_generated_count} tracks tagged",
                    size="3",
                ),
                rx.cond(
                    TaggingState.estimated_time_remaining > 0,
                    rx.text(
                        f"• ~{TaggingState.estimated_time_remaining}s remaining",
                        size="3",
                    ),
                    rx.text(),
                ),
                spacing="3",
            ),

            # Status message
            rx.text(
                TaggingState.tagging_message,
                size="3",
                color_scheme="gray",
            ),

            # Cancel button
            rx.button(
                "Cancel",
                on_click=TaggingState.cancel_tagging,
                variant="soft",
                color_scheme="red",
                size="3",
            ),

            spacing="3",
            width="100%",
            padding="4",
            border="1px solid",
            border_color="blue.6",
            border_radius="8px",
            background_color=rx.color_mode_cond(light="blue.2", dark="blue.3"),
            class_name="fade-in",
        ),
        rx.box(),  # Empty when not tagging
    )


def recent_tags_table() -> rx.Component:
    return rx.vstack(
        rx.heading("Recently Tagged Tracks", size="5", weight="bold"),

        rx.cond(
            TaggingState.recently_tagged_tracks.length() > 0,
            rx.box(
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell("Title"),
                            rx.table.column_header_cell("Artist"),
                            rx.table.column_header_cell("Tags"),
                            rx.table.column_header_cell("Environments", class_name="hide-mobile"),
                            rx.table.column_header_cell("Instruments", class_name="hide-mobile"),
                            rx.table.column_header_cell("Actions"),
                        )
                    ),
                    rx.table.body(
                        rx.foreach(
                            TaggingState.recently_tagged_tracks,
                            lambda track: rx.cond(
                                TaggingState.editing_track_id == track["id"],
                                # Edit mode
                                rx.table.row(
                                    rx.table.cell(track["title"]),
                                    rx.table.cell(track["artist"]),
                                    rx.table.cell(
                                        rx.input(
                                            value=TaggingState.edit_tags,
                                            on_change=TaggingState.set_edit_tags,
                                            size="1",
                                            width="150px",
                                        )
                                    ),
                                    rx.table.cell(
                                        rx.input(
                                            value=TaggingState.edit_environments,
                                            on_change=TaggingState.set_edit_environments,
                                            size="1",
                                            width="150px",
                                        ),
                                        class_name="hide-mobile",
                                    ),
                                    rx.table.cell(
                                        rx.input(
                                            value=TaggingState.edit_instruments,
                                            on_change=TaggingState.set_edit_instruments,
                                            size="1",
                                            width="150px",
                                        ),
                                        class_name="hide-mobile",
                                    ),
                                    rx.table.cell(
                                        rx.hstack(
                                            rx.button(
                                                "Save",
                                                on_click=TaggingState.save_tag_edit,
                                                variant="soft",
                                                color_scheme="green",
                                                size="1",
                                            ),
                                            rx.button(
                                                "Cancel",
                                                on_click=TaggingState.cancel_edit,
                                                variant="soft",
                                                size="1",
                                            ),
                                            spacing="1",
                                        )
                                    ),
                                ),
                                # View mode
                                rx.table.row(
                                    rx.table.cell(track["title"]),
                                    rx.table.cell(track["artist"]),
                                    rx.table.cell(tag_badges(track["tags"])),
                                    rx.table.cell(tag_badges(track["environments"]), class_name="hide-mobile"),
                                    rx.table.cell(tag_badges(track["instruments"]), class_name="hide-mobile"),
                                    rx.table.cell(
                                        rx.button(
                                            "Edit",
                                            on_click=lambda t=track: TaggingState.start_edit_tag(t),
                                            variant="soft",
                                            size="1",
                                        )
                                    ),
                                ),
                            ),
                        )
                    ),
                    variant="surface",
                    size="2",
                    width="100%",
                ),
                overflow_x="auto",
                width="100%",
            ),
            empty_state(
                icon="tags",
                title="No recently tagged tracks",
                description="Use the filters above to select tracks and generate AI tags.",
            ),
        ),

        spacing="4",
        width="100%",
    )


def tag_all_confirm_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Tag All Untagged Tracks"),
            rx.alert_dialog.description(
                rx.vstack(
                    rx.text(
                        rx.cond(
                            TaggingState.untagged_track_count > 0,
                            f"This will generate AI tags for {TaggingState.untagged_track_count} untagged tracks. "
                            "Depending on library size, this may take a while and consume API credits.",
                            "There are no untagged tracks in your library.",
                        ),
                    ),
                    rx.cond(
                        TaggingState.untagged_track_count > 0,
                        rx.text("Are you sure you want to continue?", weight="bold"),
                        rx.box(),
                    ),
                    spacing="2",
                    align_items="start",
                ),
            ),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.button(
                        "Cancel",
                        variant="soft",
                        color_scheme="gray",
                        on_click=TaggingState.cancel_tag_all_confirm,
                    ),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Yes, Start Tagging",
                        on_click=TaggingState.tag_all_untagged,
                        color_scheme="orange",
                        disabled=TaggingState.untagged_track_count == 0,
                    ),
                ),
                spacing="3",
                margin_top="4",
                justify="end",
            ),
        ),
        open=TaggingState.show_tag_all_confirm,
        on_open_change=TaggingState.set_tag_all_confirm_open,
    )


def tagging() -> rx.Component:
    content = rx.container(
        rx.vstack(
            rx.heading("AI Tagging", size="8", margin_bottom="6"),

            # Main layout
            rx.vstack(
                selection_panel(),
                progress_section(),
                recent_tags_table(),
                spacing="6",
                width="100%",
            ),

            spacing="4",
            width="100%",
        ),
        size="4",
    )

    return layout(rx.fragment(content, tag_all_confirm_dialog()))