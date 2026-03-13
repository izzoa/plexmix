import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.track_table import tag_badges
from plexmix.ui.components.error import empty_state
from plexmix.ui.states.tagging_state import TaggingState


# ── Filter accordion (expandable) ────────────────────────────────────


def _filter_section() -> rx.Component:
    """Expandable filter inputs inside an accordion."""
    return rx.accordion.root(
        rx.accordion.item(
            header="Or filter tracks",
            content=rx.vstack(
                    # Row 1: genre + artist
                    rx.grid(
                        rx.vstack(
                            rx.text("Genre", size="2", weight="medium", color="gray.11"),
                            rx.input(
                                placeholder="e.g., rock, jazz",
                                value=TaggingState.genre_filter,
                                on_change=TaggingState.set_genre_filter,
                                width="100%",
                            ),
                            spacing="1",
                            width="100%",
                        ),
                        rx.vstack(
                            rx.text("Artist", size="2", weight="medium", color="gray.11"),
                            rx.input(
                                placeholder="Filter by artist",
                                value=TaggingState.artist_filter,
                                on_change=TaggingState.set_artist_filter,
                                width="100%",
                            ),
                            spacing="1",
                            width="100%",
                        ),
                        columns=rx.breakpoints(initial="1", sm="2"),
                        spacing="4",
                        width="100%",
                    ),
                    # Row 2: year range + untagged switch
                    rx.grid(
                        rx.vstack(
                            rx.text("Year Range", size="2", weight="medium", color="gray.11"),
                            rx.hstack(
                                rx.input(
                                    placeholder="Min",
                                    type="number",
                                    value=TaggingState.year_min,
                                    on_change=TaggingState.set_year_min,
                                    width="100%",
                                ),
                                rx.text("-", size="3", color="gray.9"),
                                rx.input(
                                    placeholder="Max",
                                    type="number",
                                    value=TaggingState.year_max,
                                    on_change=TaggingState.set_year_max,
                                    width="100%",
                                ),
                                spacing="2",
                                align="center",
                                width="100%",
                            ),
                            spacing="1",
                            width="100%",
                        ),
                        rx.vstack(
                            rx.text("Options", size="2", weight="medium", color="gray.11"),
                            rx.hstack(
                                rx.checkbox(
                                    checked=TaggingState.has_no_tags,
                                    on_change=TaggingState.toggle_has_no_tags,
                                ),
                                rx.text("Untagged only", size="2"),
                                spacing="2",
                                align="center",
                                padding_top="4px",
                            ),
                            spacing="1",
                            width="100%",
                        ),
                        columns=rx.breakpoints(initial="1", sm="2"),
                        spacing="4",
                        width="100%",
                    ),
                    # Preview + Start
                    rx.hstack(
                        rx.button(
                            "Preview Selection",
                            on_click=TaggingState.preview_selection,
                            variant="soft",
                            size="2",
                        ),
                        rx.cond(
                            TaggingState.preview_count > 0,
                            rx.badge(
                                f"{TaggingState.preview_count} tracks match",
                                color_scheme="green",
                                variant="soft",
                                size="2",
                            ),
                            rx.fragment(),
                        ),
                        spacing="3",
                        align="center",
                    ),
                    rx.cond(
                        TaggingState.preview_count > 0,
                        rx.button(
                            "Start Tagging",
                            on_click=TaggingState.start_tagging,
                            disabled=TaggingState.is_tagging,
                            loading=TaggingState.is_tagging,
                            color_scheme="blue",
                            size="3",
                            width="100%",
                        ),
                        rx.fragment(),
                    ),
                    spacing="4",
                    width="100%",
                ),
            value="filters",
        ),
        width="100%",
        type="single",
        collapsible=True,
    )


# ── Tag Generator card ───────────────────────────────────────────────


def _tag_generator_card() -> rx.Component:
    """Primary CTA + expandable filter accordion."""
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.box(
                    rx.icon("wand-sparkles", size=20, color="accent.9"),
                    padding="8px",
                    border_radius="var(--radius-md)",
                    background_color="accent.3",
                    flex_shrink="0",
                ),
                rx.text("Tag Generator", size="5", weight="bold"),
                spacing="3",
                align="center",
            ),
            rx.text(
                "Generate AI tags for your music library",
                size="2",
                color="gray.9",
            ),
            # Primary CTA
            rx.button(
                "Tag All Untagged Tracks",
                on_click=TaggingState.show_tag_all_confirmation,
                color_scheme="orange",
                size="3",
                width="100%",
                disabled=TaggingState.is_tagging,
                loading=TaggingState.is_tagging,
                class_name="pm-glow",
            ),
            # Expandable filters
            _filter_section(),
            spacing="4",
            width="100%",
        ),
        width="100%",
        class_name="animate-fade-in-up",
    )


# ── Progress section (inline, shown when tagging) ────────────────────


def _progress_section() -> rx.Component:
    """Thin progress bar + mono stats + cancel button."""
    return rx.cond(
        TaggingState.is_tagging,
        rx.card(
            rx.vstack(
                # Thin progress bar
                rx.progress(
                    value=TaggingState.tagging_progress,
                    width="100%",
                    color_scheme="orange",
                    size="1",
                ),
                # Stats row in mono font
                rx.hstack(
                    rx.text(
                        f"Batch {TaggingState.current_batch}/{TaggingState.total_batches}",
                        size="2",
                        style={"fontFamily": "var(--font-mono)"},
                    ),
                    rx.separator(orientation="vertical", size="1", style={"height": "14px"}),
                    rx.text(
                        f"{TaggingState.tags_generated_count} tagged",
                        size="2",
                        style={"fontFamily": "var(--font-mono)"},
                    ),
                    rx.cond(
                        TaggingState.estimated_time_remaining > 0,
                        rx.hstack(
                            rx.separator(
                                orientation="vertical",
                                size="1",
                                style={"height": "14px"},
                            ),
                            rx.text(
                                f"~{TaggingState.estimated_time_remaining}s left",
                                size="2",
                                style={"fontFamily": "var(--font-mono)"},
                            ),
                            spacing="3",
                            align="center",
                        ),
                        rx.fragment(),
                    ),
                    spacing="3",
                    align="center",
                    wrap="wrap",
                ),
                # Status message
                rx.text(
                    TaggingState.tagging_message,
                    size="2",
                    color="gray.9",
                ),
                # Cancel button
                rx.button(
                    "Cancel",
                    on_click=TaggingState.cancel_tagging,
                    variant="outline",
                    color_scheme="red",
                    size="2",
                ),
                spacing="3",
                width="100%",
            ),
            width="100%",
            class_name="animate-fade-in-up",
        ),
        rx.fragment(),
    )


# ── Recently Tagged table ────────────────────────────────────────────


def _recent_tags_table() -> rx.Component:
    """Table of recently tagged tracks with inline edit support."""
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.box(
                    rx.icon("tags", size=20, color="accent.9"),
                    padding="8px",
                    border_radius="var(--radius-md)",
                    background_color="accent.3",
                    flex_shrink="0",
                ),
                rx.text("Recently Tagged", size="5", weight="bold"),
                spacing="3",
                align="center",
            ),
            rx.cond(
                TaggingState.recently_tagged_tracks.length() > 0,
                rx.box(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell("Title"),
                                rx.table.column_header_cell("Artist"),
                                rx.table.column_header_cell("Tags"),
                                rx.table.column_header_cell(
                                    "Environments", class_name="hide-mobile"
                                ),
                                rx.table.column_header_cell(
                                    "Instruments", class_name="hide-mobile"
                                ),
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
                                        rx.table.cell(
                                            tag_badges(track["environments"]),
                                            class_name="hide-mobile",
                                        ),
                                        rx.table.cell(
                                            tag_badges(track["instruments"]),
                                            class_name="hide-mobile",
                                        ),
                                        rx.table.cell(
                                            rx.button(
                                                "Edit",
                                                on_click=lambda t=track: TaggingState.start_edit_tag(
                                                    t
                                                ),
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
                    description="Use the Tag Generator above to select tracks and generate AI tags.",
                ),
            ),
            spacing="4",
            width="100%",
        ),
        width="100%",
        class_name="animate-fade-in-up stagger-2",
    )


# ── Tag All confirmation dialog ──────────────────────────────────────


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


# ══════════════════════════════════════════════════════════════════════
#  Tagging Page
# ══════════════════════════════════════════════════════════════════════


def tagging() -> rx.Component:
    content = rx.vstack(
        # ── Page header ───────────────────────────────────────────
        rx.vstack(
            rx.heading("AI Tagging", size="8"),
            rx.text(
                "Generate and manage AI-powered tags for your music library",
                size="3",
                color="gray.9",
            ),
            spacing="1",
            align="start",
        ),
        # ── Tag Generator (top section) ───────────────────────────
        _tag_generator_card(),
        # ── Progress (inline, when active) ────────────────────────
        _progress_section(),
        # ── Recently Tagged (bottom section) ──────────────────────
        _recent_tags_table(),
        spacing="6",
        width="100%",
    )

    return layout(rx.fragment(content, tag_all_confirm_dialog()))
