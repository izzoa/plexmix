import reflex as rx

from plexmix.ui.components.navbar import layout
from plexmix.ui.pages._settings_sections import (
    _ai_provider_section,
    _button_row,
    _embedding_section,
    _field_group,
    _field_label,
    _help_text,
    _input_40,
    _section_heading,
    _status_text,
)
from plexmix.ui.states.settings_state import SettingsState


# ── Sidebar navigation ──────────────────────────────────────────────

_NAV_ITEMS = [
    ("plex", "server", "Plex Server"),
    ("ai", "brain", "AI Provider"),
    ("embedding", "layers", "Embeddings"),
    ("audio", "audio-waveform", "Audio Analysis"),
    ("musicbrainz", "disc", "MusicBrainz"),
    ("advanced", "settings", "Advanced"),
]


def _settings_nav_item(value: str, icon_name: str, label: str) -> rx.Component:
    """Single sidebar navigation item."""
    is_active = SettingsState.active_tab == value
    return rx.hstack(
        rx.icon(
            icon_name,
            size=16,
            color=rx.cond(is_active, "accent.9", "gray.9"),
        ),
        rx.text(
            label,
            size="2",
            weight=rx.cond(is_active, "bold", "medium"),
            color=rx.cond(is_active, "accent.11", "gray.11"),
        ),
        spacing="3",
        align="center",
        padding_x="12px",
        padding_y="8px",
        border_radius="var(--radius-md)",
        background_color=rx.cond(is_active, "accent.3", "transparent"),
        cursor="pointer",
        width="100%",
        on_click=SettingsState.set_active_tab(value),
        _hover={"background_color": rx.cond(is_active, "accent.3", "gray.3")},
        transition="background-color 150ms ease",
    )


def _settings_sidebar() -> rx.Component:
    """Vertical sidebar listing all setting categories."""
    return rx.vstack(
        *[_settings_nav_item(val, icon, label) for val, icon, label in _NAV_ITEMS],
        spacing="1",
        width="180px",
        flex_shrink="0",
        padding_top="2px",
    )


# ── Mobile tabs (fallback for small screens) ────────────────────────


def _mobile_tabs() -> rx.Component:
    """Tabs-based navigation for mobile devices."""
    return rx.tabs.root(
        rx.tabs.list(
            rx.tabs.trigger("Plex", value="plex"),
            rx.tabs.trigger("AI", value="ai"),
            rx.tabs.trigger("Embed", value="embedding"),
            rx.tabs.trigger("Audio", value="audio"),
            rx.tabs.trigger("MB", value="musicbrainz"),
            rx.tabs.trigger("Adv", value="advanced"),
        ),
        rx.tabs.content(_plex_section(), value="plex"),
        rx.tabs.content(_ai_provider_section(), value="ai"),
        rx.tabs.content(_embedding_section(), value="embedding"),
        rx.tabs.content(_audio_section(), value="audio"),
        rx.tabs.content(_musicbrainz_section(), value="musicbrainz"),
        rx.tabs.content(_advanced_section(), value="advanced"),
        value=SettingsState.active_tab,
        on_change=SettingsState.set_active_tab,
        width="100%",
    )


# ── Plex Server section ─────────────────────────────────────────────


def _plex_section() -> rx.Component:
    return rx.vstack(
        _section_heading("Plex Server"),
        _field_group(
            _field_label("Server URL"),
            _input_40(
                placeholder="http://localhost:32400",
                value=SettingsState.plex_url,
                on_change=SettingsState.set_plex_url,
                width="100%",
            ),
            _help_text("e.g., http://localhost:32400"),
        ),
        _field_group(
            _field_label("Plex Token"),
            _input_40(
                type="password",
                placeholder="Enter your Plex token",
                value=SettingsState.plex_token,
                on_change=SettingsState.set_plex_token,
                width="100%",
            ),
            _help_text(
                "Find at app.plex.tv/desktop \u2192 Settings \u2192 Account, or in Plex app XML settings"
            ),
        ),
        _field_group(
            _field_label("Music Library"),
            rx.select(
                SettingsState.plex_libraries,
                value=SettingsState.plex_library,
                on_change=SettingsState.set_plex_library,
                placeholder="Select library...",
                width="100%",
            ),
            _help_text("Test your connection first to load available libraries"),
        ),
        _button_row(
            "Test Connection",
            SettingsState.test_plex_connection,
            SettingsState.testing_connection,
            SettingsState.save_all_settings,
        ),
        _status_text(SettingsState.plex_test_status),
        spacing="4",
        width="100%",
    )


# ── Audio Analysis section ───────────────────────────────────────────


def _audio_section() -> rx.Component:
    return rx.vstack(
        _section_heading("Audio Analysis"),
        rx.callout(
            "Audio analysis extracts tempo, key, energy, and danceability from your tracks "
            "using Essentia. These features enrich embeddings and enable audio-based playlist filters.",
            icon="music",
            color_scheme="blue",
            size="2",
        ),
        _field_group(
            rx.hstack(
                rx.switch(
                    checked=SettingsState.audio_enabled,
                    on_change=SettingsState.set_audio_enabled,
                ),
                rx.text("Enable Audio Analysis", size="2", weight="medium", color="gray.11"),
                spacing="3",
                align="center",
            ),
            rx.hstack(
                rx.switch(
                    checked=SettingsState.audio_analyze_on_sync,
                    on_change=SettingsState.set_audio_analyze_on_sync,
                ),
                rx.text("Analyze Audio on Sync", size="2", weight="medium", color="gray.11"),
                spacing="3",
                align="center",
            ),
            _help_text("When enabled, new tracks will be analyzed during sync"),
        ),
        _field_group(
            _field_label("Duration Limit (seconds)"),
            rx.hstack(
                _input_40(
                    type="number",
                    value=SettingsState.audio_duration_limit,
                    on_change=SettingsState.set_audio_duration_limit,
                    width="120px",
                ),
                rx.tooltip(
                    rx.icon("info", size=16),
                    content="Seconds of audio to analyze per track. 0 = full track.",
                ),
                spacing="2",
                align="center",
            ),
        ),
        # Save button (no test for audio)
        rx.hstack(
            rx.spacer(),
            rx.button(
                "Save",
                on_click=SettingsState.save_all_settings,
                color_scheme="orange",
            ),
            width="100%",
            margin_top="4",
        ),
        _status_text(SettingsState.save_status),
        spacing="4",
        width="100%",
    )


# ── MusicBrainz section ──────────────────────────────────────────────


def _musicbrainz_section() -> rx.Component:
    return rx.vstack(
        _section_heading("MusicBrainz"),
        rx.callout(
            "MusicBrainz enriches your tracks with community-curated genres, canonical artist IDs, "
            "and recording type annotations (live, remix, etc.). These improve embedding quality "
            "and playlist diversity.",
            icon="disc",
            color_scheme="blue",
            size="2",
        ),
        _field_group(
            rx.hstack(
                rx.switch(
                    checked=SettingsState.musicbrainz_enabled,
                    on_change=SettingsState.set_musicbrainz_enabled,
                ),
                rx.text(
                    "Enable MusicBrainz Enrichment", size="2", weight="medium", color="gray.11"
                ),
                spacing="3",
                align="center",
            ),
            rx.hstack(
                rx.switch(
                    checked=SettingsState.musicbrainz_enrich_on_sync,
                    on_change=SettingsState.set_musicbrainz_enrich_on_sync,
                ),
                rx.text("Enrich on Sync", size="2", weight="medium", color="gray.11"),
                spacing="3",
                align="center",
            ),
            _help_text("When enabled, new tracks will be enriched during sync"),
        ),
        _field_group(
            _field_label("Confidence Threshold"),
            rx.hstack(
                rx.slider(
                    value=[SettingsState.musicbrainz_confidence_threshold],
                    on_change=SettingsState.set_musicbrainz_confidence_threshold,
                    min=0,
                    max=100,
                    step=5,
                    width="200px",
                ),
                rx.text(
                    SettingsState.musicbrainz_confidence_threshold.to(str) + "%",
                    size="2",
                    color="gray.11",
                    font_family="var(--font-mono)",
                ),
                spacing="3",
                align="center",
            ),
            _help_text("Minimum match score (0-100) for accepting MusicBrainz results"),
        ),
        _field_group(
            _field_label("Contact Email"),
            _input_40(
                placeholder="your@email.com",
                value=SettingsState.musicbrainz_contact_email,
                on_change=SettingsState.set_musicbrainz_contact_email,
                width="100%",
            ),
            _help_text("Required by MusicBrainz Terms of Service for API usage"),
        ),
        rx.hstack(
            rx.spacer(),
            rx.button(
                "Save",
                on_click=SettingsState.save_all_settings,
                color_scheme="orange",
            ),
            width="100%",
            margin_top="4",
        ),
        _status_text(SettingsState.save_status),
        spacing="4",
        width="100%",
    )


# ── Advanced section ─────────────────────────────────────────────────


def _advanced_section() -> rx.Component:
    return rx.vstack(
        _section_heading("Advanced Settings"),
        _field_group(
            _field_label("Database Path"),
            rx.text(
                SettingsState.db_path,
                size="2",
                color="gray.9",
                style={"fontFamily": "var(--font-mono)", "wordBreak": "break-all"},
            ),
        ),
        _field_group(
            _field_label("FAISS Index Path"),
            rx.text(
                SettingsState.faiss_index_path,
                size="2",
                color="gray.9",
                style={"fontFamily": "var(--font-mono)", "wordBreak": "break-all"},
            ),
        ),
        _field_group(
            _field_label("Logging Level"),
            rx.select(
                ["DEBUG", "INFO", "WARNING", "ERROR"],
                value=SettingsState.log_level,
                on_change=SettingsState.set_log_level,
                width="100%",
            ),
        ),
        # Save button (no test for advanced)
        rx.hstack(
            rx.spacer(),
            rx.button(
                "Save",
                on_click=SettingsState.save_all_settings,
                color_scheme="orange",
            ),
            width="100%",
            margin_top="4",
        ),
        _status_text(SettingsState.save_status),
        spacing="4",
        width="100%",
    )


# ── Content switcher (desktop) ───────────────────────────────────────


def _active_section() -> rx.Component:
    """Show the section matching active_tab."""
    return rx.box(
        rx.cond(
            SettingsState.active_tab == "plex",
            _plex_section(),
            rx.cond(
                SettingsState.active_tab == "ai",
                _ai_provider_section(),
                rx.cond(
                    SettingsState.active_tab == "embedding",
                    _embedding_section(),
                    rx.cond(
                        SettingsState.active_tab == "audio",
                        _audio_section(),
                        rx.cond(
                            SettingsState.active_tab == "musicbrainz",
                            _musicbrainz_section(),
                            _advanced_section(),
                        ),
                    ),
                ),
            ),
        ),
        flex="1",
        min_width="0",
        width="100%",
    )


# ── Sticky unsaved-changes bar ───────────────────────────────────────


def _unsaved_changes_bar() -> rx.Component:
    """Sticky bottom bar shown when settings have been modified."""
    return rx.cond(
        SettingsState.has_unsaved_changes,
        rx.hstack(
            rx.hstack(
                rx.box(class_name="status-dot status-dot-warning status-dot-pulse"),
                rx.text("Unsaved changes", size="2", weight="medium", color="gray.11"),
                spacing="2",
                align="center",
            ),
            rx.spacer(),
            rx.button(
                "Save",
                on_click=SettingsState.save_all_settings,
                color_scheme="orange",
                size="2",
            ),
            spacing="3",
            align="center",
            width="100%",
            padding_x="20px",
            padding_y="12px",
            border_radius="var(--radius-lg)",
            background_color="gray.2",
            border="1px solid var(--pm-gray-4)",
            box_shadow="var(--shadow-md)",
            class_name="animate-slide-up",
        ),
        rx.fragment(),
    )


# ══════════════════════════════════════════════════════════════════════
#  Settings Page
# ══════════════════════════════════════════════════════════════════════


def settings() -> rx.Component:
    content = rx.vstack(
        # ── Page header ────────────────────────────────────────────
        rx.vstack(
            rx.heading("Settings", size="8"),
            rx.text("Configure your PlexMix instance", size="3", color="gray.9"),
            spacing="1",
            align="start",
        ),
        # ── Desktop: sidebar + content ─────────────────────────────
        rx.hstack(
            _settings_sidebar(),
            rx.separator(orientation="vertical", size="4"),
            _active_section(),
            spacing="6",
            width="100%",
            align="start",
            class_name="hide-mobile",
        ),
        # ── Mobile: tabs ───────────────────────────────────────────
        rx.box(
            _mobile_tabs(),
            width="100%",
            class_name="hide-desktop",
        ),
        # ── Sticky unsaved bar ─────────────────────────────────────
        _unsaved_changes_bar(),
        spacing="6",
        width="100%",
        class_name="animate-fade-in-up",
    )
    return layout(content)
