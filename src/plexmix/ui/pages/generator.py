import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.progress_modal import progress_modal
from plexmix.ui.components.error import empty_state as shared_empty_state
from plexmix.ui.states.generator_state import GeneratorState


# ── Example mood pills ────────────────────────────────────────────────

def _example_pills() -> rx.Component:
    """Clickable pill badges showing example mood queries."""
    return rx.hstack(
        rx.foreach(
            GeneratorState.mood_examples,
            lambda example: rx.badge(
                example,
                on_click=lambda e=example: GeneratorState.use_example(e),
                variant="surface",
                color_scheme="orange",
                cursor="pointer",
                size="2",
                style={
                    "borderRadius": "var(--radius-xl)",
                    "padding": "6px 14px",
                    "transition": "background-color var(--duration-fast) var(--ease-default)",
                },
                _hover={"background_color": "accent.4"},
            ),
        ),
        wrap="wrap",
        spacing="2",
        width="100%",
    )


# ── Advanced options accordion ────────────────────────────────────────

def _advanced_options() -> rx.Component:
    """Collapsible advanced filters (general + audio)."""
    return rx.accordion.root(
        rx.accordion.item(
            header=rx.accordion.header(
                rx.hstack(
                    rx.icon("sliders-horizontal", size=16, color="gray.9"),
                    rx.text("Advanced Options", size="2", weight="medium"),
                    spacing="2",
                    align="center",
                ),
            ),
            content=rx.accordion.content(
                rx.vstack(
                    # ── General filters ──
                    rx.text("General Filters", size="3", weight="bold"),
                    rx.hstack(
                        rx.text("Max Tracks:", size="3"),
                        rx.slider(
                            default_value=[GeneratorState.max_tracks],
                            on_change=lambda val: GeneratorState.set_max_tracks(val[0]),
                            min=10,
                            max=100,
                            step=5,
                            width="200px",
                        ),
                        rx.text(
                            GeneratorState.max_tracks,
                            size="3",
                            weight="bold",
                            style={"fontFamily": "var(--font-mono)"},
                        ),
                        spacing="3",
                        align="center",
                    ),
                    rx.hstack(
                        rx.text("Candidate Pool Multiplier:", size="3"),
                        rx.slider(
                            default_value=[GeneratorState.candidate_pool_multiplier],
                            on_change=lambda val: GeneratorState.set_candidate_pool_multiplier(
                                val[0]
                            ),
                            min=5,
                            max=100,
                            step=5,
                            width="200px",
                        ),
                        rx.text(
                            f"{GeneratorState.candidate_pool_multiplier}x",
                            size="3",
                            weight="bold",
                            style={"fontFamily": "var(--font-mono)"},
                        ),
                        rx.tooltip(
                            rx.icon("info", size=16, color="gray.9"),
                            content="Multiplier for the candidate pool size. Higher values search more tracks for better matches.",
                        ),
                        spacing="3",
                        align="center",
                    ),
                    rx.input(
                        placeholder="Genre filter (e.g., rock, jazz)",
                        value=GeneratorState.genre_filter,
                        on_change=GeneratorState.set_genre_filter,
                        width="100%",
                    ),
                    rx.hstack(
                        rx.text("Year Range:", size="3"),
                        rx.input(
                            placeholder="Min",
                            type="number",
                            value=GeneratorState.year_min,
                            on_change=GeneratorState.set_year_min,
                            width="100px",
                        ),
                        rx.text("-", size="3", color="gray.9"),
                        rx.input(
                            placeholder="Max",
                            type="number",
                            value=GeneratorState.year_max,
                            on_change=GeneratorState.set_year_max,
                            width="100px",
                        ),
                        spacing="2",
                        align="center",
                    ),

                    rx.separator(size="4", color_scheme="gray"),

                    # ── Audio filters ──
                    rx.text("Audio Filters", size="3", weight="bold"),
                    rx.cond(
                        GeneratorState.audio_analyzed_count > 0,
                        rx.vstack(
                            rx.text(
                                GeneratorState.audio_analyzed_count.to(str)
                                + " tracks with audio analysis",
                                size="1",
                                color="gray.9",
                            ),
                            rx.hstack(
                                rx.text("Tempo (BPM):", size="3"),
                                rx.input(
                                    placeholder="Min",
                                    type="number",
                                    value=GeneratorState.tempo_min,
                                    on_change=GeneratorState.set_tempo_min,
                                    width="100px",
                                ),
                                rx.text("-", size="3", color="gray.9"),
                                rx.input(
                                    placeholder="Max",
                                    type="number",
                                    value=GeneratorState.tempo_max,
                                    on_change=GeneratorState.set_tempo_max,
                                    width="100px",
                                ),
                                spacing="2",
                                align="center",
                            ),
                            rx.hstack(
                                rx.text("Energy Level:", size="3"),
                                rx.select(
                                    ["Any", "low", "medium", "high"],
                                    value=rx.cond(
                                        GeneratorState.energy_level,
                                        GeneratorState.energy_level,
                                        "Any",
                                    ),
                                    on_change=GeneratorState.set_energy_level,
                                    width="150px",
                                ),
                                spacing="2",
                                align="center",
                            ),
                            rx.hstack(
                                rx.text("Musical Key:", size="3"),
                                rx.select(
                                    [
                                        "Any", "C", "C#", "D", "D#", "E", "F",
                                        "F#", "G", "G#", "A", "A#", "B",
                                    ],
                                    value=rx.cond(
                                        GeneratorState.key_filter,
                                        GeneratorState.key_filter,
                                        "Any",
                                    ),
                                    on_change=GeneratorState.set_key_filter,
                                    width="150px",
                                ),
                                spacing="2",
                                align="center",
                            ),
                            rx.hstack(
                                rx.text("Min Danceability:", size="3"),
                                rx.input(
                                    placeholder="0.0-1.0",
                                    type="number",
                                    value=GeneratorState.danceability_min,
                                    on_change=GeneratorState.set_danceability_min,
                                    width="100px",
                                ),
                                rx.tooltip(
                                    rx.icon("info", size=16, color="gray.9"),
                                    content="Minimum danceability score from 0.0 to 1.0",
                                ),
                                spacing="2",
                                align="center",
                            ),
                            spacing="4",
                            width="100%",
                        ),
                        rx.callout(
                            "No tracks have been analyzed yet. Run audio analysis from the Library or Doctor page to enable tempo, energy, key, and danceability filters.",
                            icon="info",
                            color_scheme="gray",
                            size="2",
                        ),
                    ),
                    spacing="4",
                    width="100%",
                ),
            ),
        ),
        width="100%",
        collapsible=True,
    )


# ── Generation progress (inline) ─────────────────────────────────────

def _generation_progress() -> rx.Component:
    """Inline progress card with bar, percentage, status, and scrollable log."""
    return rx.cond(
        GeneratorState.is_generating | (GeneratorState.generation_message != ""),
        rx.vstack(
            # Progress bar + percentage
            rx.hstack(
                rx.progress(
                    value=GeneratorState.generation_progress,
                    max=100,
                    width="100%",
                    color_scheme="orange",
                ),
                rx.text(
                    f"{GeneratorState.generation_progress}%",
                    size="2",
                    weight="bold",
                    style={"fontFamily": "var(--font-mono)", "minWidth": "40px"},
                    text_align="right",
                ),
                align="center",
                spacing="3",
                width="100%",
            ),
            # Status message
            rx.text(
                GeneratorState.generation_message,
                size="2",
                color="gray.11",
                weight="medium",
            ),
            # Scrollable log
            rx.cond(
                GeneratorState.generation_log.length() > 0,
                rx.box(
                    rx.vstack(
                        rx.foreach(
                            GeneratorState.generation_log,
                            lambda entry: rx.text(
                                entry,
                                size="1",
                                color="gray.9",
                                style={"fontFamily": "var(--font-mono)"},
                            ),
                        ),
                        spacing="1",
                        width="100%",
                        padding="12px",
                    ),
                    style={
                        "maxHeight": "180px",
                        "overflowY": "auto",
                        "width": "100%",
                        "borderRadius": "var(--radius-md)",
                        "backgroundColor": "var(--pm-gray-2)",
                        "border": "1px solid var(--pm-gray-4)",
                    },
                ),
                rx.fragment(),
            ),
            spacing="3",
            width="100%",
            padding="16px",
            border_radius="var(--radius-lg)",
            background_color="gray.2",
            class_name="animate-fade-in",
        ),
        rx.fragment(),
    )


# ── Hero input section (centered, single column) ─────────────────────

def _hero_input() -> rx.Component:
    """The main mood input section -- hero element of the page."""
    return rx.vstack(
        # Page header
        rx.vstack(
            rx.heading("Playlist Generator", size="8"),
            rx.text("Describe a vibe and let AI curate the perfect mix", size="3", color="gray.9"),
            spacing="1",
            align="center",
        ),

        # Hero text area
        rx.text_area(
            placeholder="What's the vibe?",
            value=GeneratorState.mood_query,
            on_change=GeneratorState.set_mood_query,
            rows="5",
            width="100%",
            size="3",
            style={
                "fontSize": "16px",
                "padding": "16px",
                "borderRadius": "var(--radius-lg)",
            },
        ),

        # Example mood pills
        rx.vstack(
            rx.text("Try:", size="1", color="gray.9", weight="medium"),
            _example_pills(),
            spacing="2",
            width="100%",
        ),

        # Advanced options
        _advanced_options(),

        # Generate button
        rx.button(
            rx.icon("sparkles", size=18),
            "Generate Playlist",
            on_click=GeneratorState.generate_playlist,
            disabled=GeneratorState.is_generating | (GeneratorState.mood_query == ""),
            loading=GeneratorState.is_generating,
            color_scheme="orange",
            size="4",
            width="100%",
            title=rx.cond(
                GeneratorState.mood_query == "", "Enter a mood description", ""
            ),
            class_name="pm-button pm-glow",
        ),

        # Generation progress (inline)
        _generation_progress(),

        spacing="5",
        width="100%",
        max_width="680px",
        margin_x="auto",
        align="center",
    )


# ── Compact mood summary (shown after generation) ────────────────────

def _compact_mood_summary() -> rx.Component:
    """Collapsed summary of the mood input, shown above results."""
    return rx.hstack(
        rx.box(
            rx.icon("sparkles", size=16, color="accent.9"),
            padding="8px",
            border_radius="var(--radius-md)",
            background_color="accent.3",
            flex_shrink="0",
        ),
        rx.vstack(
            rx.text(
                GeneratorState.mood_query,
                size="3",
                weight="medium",
                style={"fontStyle": "italic"},
                no_of_lines=1,
            ),
            rx.hstack(
                rx.text(
                    f"{GeneratorState.generated_playlist.length()} tracks",
                    size="1",
                    color="gray.9",
                ),
                rx.text(
                    f"{GeneratorState.total_duration_ms // 60000} min",
                    size="1",
                    color="gray.9",
                    style={"fontFamily": "var(--font-mono)"},
                ),
                spacing="3",
            ),
            spacing="0",
            align="start",
        ),
        rx.spacer(),
        rx.button(
            rx.icon("pencil", size=14),
            "New Query",
            on_click=GeneratorState.regenerate,
            variant="soft",
            size="2",
            color_scheme="gray",
        ),
        spacing="3",
        align="center",
        width="100%",
        padding="12px 16px",
        border_radius="var(--radius-lg)",
        background_color="gray.2",
    )


# ── Playlist metadata heading ────────────────────────────────────────

def _playlist_metadata() -> rx.Component:
    total_minutes = GeneratorState.total_duration_ms // 60000
    return rx.vstack(
        rx.heading("Generated Playlist", size="6"),
        rx.hstack(
            rx.badge(
                f"{GeneratorState.generated_playlist.length()} tracks",
                variant="surface",
                color_scheme="gray",
                size="1",
            ),
            rx.badge(
                f"{total_minutes} min",
                variant="surface",
                color_scheme="gray",
                size="1",
            ),
            rx.badge(
                GeneratorState.mood_query,
                variant="surface",
                color_scheme="orange",
                size="1",
                max_width="300px",
                style={"overflow": "hidden", "textOverflow": "ellipsis"},
            ),
            spacing="2",
            wrap="wrap",
        ),
        spacing="2",
        width="100%",
    )


# ── Playlist track table ─────────────────────────────────────────────

def _playlist_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("#", style={"width": "48px"}),
                rx.table.column_header_cell("Title"),
                rx.table.column_header_cell("Artist"),
                rx.table.column_header_cell("Album", class_name="hide-mobile"),
                rx.table.column_header_cell(
                    "Duration", style={"width": "80px", "textAlign": "right"}
                ),
                rx.table.column_header_cell("", style={"width": "80px"}),
            )
        ),
        rx.table.body(
            rx.foreach(
                GeneratorState.generated_playlist,
                lambda track, index: rx.table.row(
                    rx.table.cell(
                        rx.text(
                            index + 1,
                            size="2",
                            color="gray.9",
                            style={"fontFamily": "var(--font-mono)"},
                        ),
                    ),
                    rx.table.cell(
                        rx.text(track["title"], size="2", weight="medium"),
                    ),
                    rx.table.cell(
                        rx.text(track["artist"], size="2", color="gray.11"),
                    ),
                    rx.table.cell(
                        rx.text(track["album"], size="2", color="gray.9"),
                        class_name="hide-mobile",
                    ),
                    rx.table.cell(
                        rx.text(
                            track["duration_formatted"],
                            size="2",
                            color="gray.9",
                            text_align="right",
                            style={"fontFamily": "var(--font-mono)"},
                        ),
                    ),
                    rx.table.cell(
                        rx.button(
                            rx.icon("x", size=14),
                            on_click=lambda t=track: GeneratorState.remove_track(t["id"]),
                            variant="ghost",
                            color_scheme="red",
                            size="1",
                        ),
                    ),
                ),
            )
        ),
        variant="surface",
        size="2",
        width="100%",
    )


# ── Playlist action bar ──────────────────────────────────────────────

def _playlist_actions() -> rx.Component:
    return rx.vstack(
        # Primary row: name input + save buttons
        rx.hstack(
            rx.input(
                placeholder="Enter playlist name...",
                value=GeneratorState.playlist_name,
                on_change=GeneratorState.set_playlist_name,
                flex="1",
            ),
            rx.button(
                rx.icon("server", size=16),
                "Save to Plex",
                on_click=GeneratorState.save_to_plex,
                disabled=GeneratorState.playlist_name == "",
                color_scheme="blue",
                size="3",
                title=rx.cond(
                    GeneratorState.playlist_name == "", "Enter a playlist name", ""
                ),
                class_name="pm-button",
            ),
            rx.button(
                rx.icon("hard-drive", size=16),
                "Save Locally",
                on_click=GeneratorState.save_locally,
                disabled=GeneratorState.playlist_name == "",
                color_scheme="green",
                size="3",
                title=rx.cond(
                    GeneratorState.playlist_name == "", "Enter a playlist name", ""
                ),
                class_name="pm-button",
            ),
            spacing="3",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        # Secondary row: regenerate + export
        rx.hstack(
            rx.button(
                rx.icon("refresh-cw", size=16),
                "Regenerate",
                on_click=GeneratorState.regenerate,
                variant="soft",
                size="3",
            ),
            rx.button(
                rx.icon("download", size=16),
                "Export M3U",
                on_click=GeneratorState.export_m3u,
                variant="soft",
                size="3",
            ),
            spacing="3",
        ),
        spacing="4",
        width="100%",
    )


# ── Results section (full width) ─────────────────────────────────────

def _results_section() -> rx.Component:
    """Full-width results with compact summary, table, and actions."""
    return rx.vstack(
        _compact_mood_summary(),
        _playlist_metadata(),
        _playlist_table(),
        _playlist_actions(),
        # Generation progress (also shown here during regeneration)
        _generation_progress(),
        spacing="5",
        width="100%",
        class_name="animate-fade-in-up",
    )


# ── Empty state ──────────────────────────────────────────────────────

def _empty_state() -> rx.Component:
    return shared_empty_state(
        icon="sparkles",
        title="No playlist generated yet",
        description="Enter a mood query and click 'Generate Playlist' to get started.",
    )


# ══════════════════════════════════════════════════════════════════════
#  Generator Page
# ══════════════════════════════════════════════════════════════════════

def generator() -> rx.Component:
    content = rx.vstack(
        rx.cond(
            # Has a generated playlist -> full-width results mode
            GeneratorState.generated_playlist.length() > 0,
            _results_section(),
            # No playlist yet (fresh, generating, or error) -> centered hero input
            _hero_input(),
        ),
        spacing="6",
        width="100%",
        class_name="animate-fade-in-up",
    )

    return layout(content)
