import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.progress_modal import progress_modal
from plexmix.ui.states.generator_state import GeneratorState


def mood_query_input() -> rx.Component:
    return rx.vstack(
        rx.text("Describe Your Mood", size="5", weight="bold"),
        rx.text_area(
            placeholder="Describe your mood or vibe... (e.g., 'Chill rainy day vibes with acoustic guitar')",
            value=GeneratorState.mood_query,
            on_change=GeneratorState.set_mood_query,
            rows="6",
            width="100%",
        ),
        spacing="2",
        width="100%",
    )


def example_queries() -> rx.Component:
    return rx.vstack(
        rx.text("Example Queries", size="4", weight="bold"),
        rx.vstack(
            rx.foreach(
                GeneratorState.mood_examples,
                lambda example: rx.button(
                    example,
                    on_click=lambda e=example: GeneratorState.use_example(e),
                    variant="soft",
                    size="2",
                    width="100%",
                ),
            ),
            spacing="2",
            width="100%",
        ),
        spacing="3",
        width="100%",
    )


def advanced_options() -> rx.Component:
    return rx.accordion.root(
        rx.accordion.item(
            header=rx.accordion.header("Advanced Options"),
            content=rx.accordion.content(
                rx.vstack(
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
                        rx.text(GeneratorState.max_tracks, size="3", weight="bold"),
                        spacing="3",
                        align="center",
                    ),
                    rx.hstack(
                        rx.text("Candidate Pool Multiplier:", size="3"),
                        rx.slider(
                            default_value=[GeneratorState.candidate_pool_multiplier],
                            on_change=lambda val: GeneratorState.set_candidate_pool_multiplier(val[0]),
                            min=5,
                            max=100,
                            step=5,
                            width="200px",
                        ),
                        rx.text(f"{GeneratorState.candidate_pool_multiplier}x", size="3", weight="bold"),
                        rx.tooltip(
                            rx.icon("info", size=16),
                            content="Multiplier for the candidate pool size. Higher values search more tracks for better matches."
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
                        rx.text("-", size="3"),
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
                    spacing="4",
                    width="100%",
                ),
            ),
        ),
        width="100%",
        collapsible=True,
    )


def input_section() -> rx.Component:
    return rx.vstack(
        mood_query_input(),
        example_queries(),
        advanced_options(),
        rx.button(
            "Generate Playlist",
            on_click=GeneratorState.generate_playlist,
            disabled=GeneratorState.is_generating | (GeneratorState.mood_query == ""),
            loading=GeneratorState.is_generating,
            color_scheme="orange",
            size="4",
            width="100%",
        ),
        rx.cond(
            GeneratorState.is_generating | (GeneratorState.generation_message != ""),
            rx.card(
                rx.vstack(
                    rx.hstack(
                        rx.progress(value=GeneratorState.generation_progress, max=100, width="100%"),
                        rx.text(f"{GeneratorState.generation_progress}%", size="2", color_scheme="gray"),
                        align="center",
                        spacing="3",
                        width="100%",
                    ),
                    rx.text(GeneratorState.generation_message, size="2", color_scheme="gray"),
                    rx.box(
                        rx.vstack(
                            rx.foreach(
                                GeneratorState.generation_log,
                                lambda entry: rx.text(entry, size="2", color_scheme="gray"),
                            ),
                            spacing="1",
                            width="100%",
                        ),
                        style={"maxHeight": "200px", "overflowY": "auto", "width": "100%"},
                    ),
                    spacing="3",
                    width="100%",
                ),
                width="100%",
                variant="surface",
            ),
            rx.box(),
        ),
        spacing="6",
        width="100%",
        padding="4",
    )


def playlist_metadata() -> rx.Component:
    total_minutes = GeneratorState.total_duration_ms // 60000
    return rx.vstack(
        rx.text("Generated Playlist", size="6", weight="bold"),
        rx.hstack(
            rx.text(f"Tracks: {GeneratorState.generated_playlist.length()}", size="3", color_scheme="gray"),
            rx.text(f"Duration: {total_minutes} min", size="3", color_scheme="gray"),
            spacing="4",
        ),
        rx.text(f"Mood: {GeneratorState.mood_query}", size="2", color_scheme="gray", style={"fontStyle": "italic"}),
        spacing="2",
        width="100%",
    )


def playlist_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("#"),
                rx.table.column_header_cell("Title"),
                rx.table.column_header_cell("Artist"),
                rx.table.column_header_cell("Album"),
                rx.table.column_header_cell("Duration"),
                rx.table.column_header_cell(""),
            )
        ),
        rx.table.body(
            rx.foreach(
                GeneratorState.generated_playlist,
                lambda track, index: rx.table.row(
                    rx.table.cell(index + 1),
                    rx.table.cell(track["title"]),
                    rx.table.cell(track["artist"]),
                    rx.table.cell(track["album"]),
                    rx.table.cell(track["duration_formatted"]),
                    rx.table.cell(
                        rx.button(
                            "Remove",
                            on_click=lambda t=track: GeneratorState.remove_track(t["id"]),
                            variant="soft",
                            color_scheme="red",
                            size="1",
                        )
                    ),
                ),
            )
        ),
        variant="surface",
        size="3",
        width="100%",
    )


def playlist_actions() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.input(
                placeholder="Enter playlist name...",
                value=GeneratorState.playlist_name,
                on_change=GeneratorState.set_playlist_name,
                width="300px",
            ),
            rx.button(
                "Save to Plex",
                on_click=GeneratorState.save_to_plex,
                disabled=GeneratorState.playlist_name == "",
                color_scheme="blue",
                size="3",
            ),
            rx.button(
                "Save Locally",
                on_click=GeneratorState.save_locally,
                disabled=GeneratorState.playlist_name == "",
                color_scheme="green",
                size="3",
            ),
            spacing="3",
            align="center",
        ),
        rx.hstack(
            rx.button(
                "Regenerate",
                on_click=GeneratorState.regenerate,
                variant="soft",
                size="3",
            ),
            rx.button(
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


def loading_state() -> rx.Component:
    return rx.vstack(
        rx.spinner(size="3", color="orange"),
        rx.text("Generating your playlist...", size="5", weight="bold"),
        rx.progress(
            value=GeneratorState.generation_progress,
            max=100,
            width="100%",
            color_scheme="orange",
        ),
        rx.text(
            f"{GeneratorState.generation_progress}% - {GeneratorState.generation_message}",
            size="3",
            color_scheme="gray",
        ),
        spacing="4",
        align="center",
        justify="center",
        min_height="400px",
        width="100%",
    )


def empty_state() -> rx.Component:
    return rx.vstack(
        rx.text("No playlist generated yet", size="5", weight="bold", color_scheme="gray"),
        rx.text("Enter a mood query and click 'Generate Playlist' to get started", size="3", color_scheme="gray"),
        spacing="2",
        align="center",
        justify="center",
        height="400px",
    )


def results_section() -> rx.Component:
    return rx.vstack(
        rx.cond(
            GeneratorState.is_generating,
            loading_state(),
            rx.cond(
                GeneratorState.generated_playlist.length() > 0,
                rx.vstack(
                    playlist_metadata(),
                    playlist_table(),
                    playlist_actions(),
                    spacing="4",
                    width="100%",
                ),
                empty_state(),
            ),
        ),
        width="100%",
        padding="4",
    )


def generator() -> rx.Component:
    content = rx.container(
        rx.vstack(
            rx.heading("Playlist Generator", size="8", margin_bottom="6"),
            rx.grid(
                rx.box(
                    input_section(),
                    width="100%",
                ),
                rx.box(
                    results_section(),
                    width="100%",
                ),
                columns=rx.breakpoints(initial="1", md="2"),
                spacing="6",
                width="100%",
            ),
            spacing="4",
            width="100%",
        ),
        size="4",
    )

    return layout(content)
