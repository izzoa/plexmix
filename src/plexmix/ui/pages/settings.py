"""Settings page: one keyboard-operable section list at every width.

The sections are a single vertical Radix Tabs tree (``role=tablist/tab/tabpanel``,
arrow keys, Home/End, one Tab stop), so each field exists once in the DOM and
its ``<label for>``/``id`` pair is unique. The selected section is deep-linkable
as ``/settings?section=<id>`` (ids in ``plexmix.ui.utils.a11y.SETTINGS_SECTIONS``);
``SettingsState.set_active_tab`` rewrites the address in place.
"""

from typing import Callable

import reflex as rx

from plexmix.ui.components.navbar import layout
from plexmix.ui.components.seg_radio import seg_radio_group
from plexmix.ui.pages._settings_sections import (
    _ai_provider_section,
    _button_row,
    _embedding_section,
    _field_group,
    _field_label,
    _help_text,
    _input_40,
    _section_heading,
    _secret_input,
    _status_text,
    _switch_row,
)
from plexmix.ui.states.appearance_state import AppearanceState
from plexmix.ui.components.scope_confirm import scope_confirm
from plexmix.ui.states.settings_state import SECTION_LABELS, SettingsState, secret_input_id
from plexmix.ui.utils.a11y import SETTINGS_SECTIONS

# ── Plex Server section ─────────────────────────────────────────────


def _plex_section() -> rx.Component:
    return rx.vstack(
        _section_heading("Plex Server"),
        _field_group(
            _field_label("Server URL", "set-plex-url"),
            _input_40(
                id="set-plex-url",
                aria_describedby="set-plex-url-help",
                placeholder="http://localhost:32400",
                value=SettingsState.plex_url,
                on_change=SettingsState.set_plex_url,
                width="100%",
            ),
            _help_text("e.g., http://localhost:32400", id="set-plex-url-help"),
        ),
        _field_group(
            _field_label("Plex Token", secret_input_id("plex_token")),
            _secret_input(
                "plex_token",
                "Enter your Plex token",
                error_var=SettingsState.plex_token_error,
            ),
            _help_text(
                "Find at app.plex.tv/desktop → Settings → Account, or in Plex app XML settings"
            ),
        ),
        _field_group(
            _field_label("Music Library", "set-plex-library"),
            rx.select(
                SettingsState.plex_libraries,
                id="set-plex-library",
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
        ),
        _status_text(SettingsState.plex_test_status, "set-plex-status"),
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
            _switch_row(
                "set-audio-enabled",
                "Enable Audio Analysis",
                SettingsState.audio_enabled,
                SettingsState.set_audio_enabled,
            ),
            _switch_row(
                "set-audio-analyze-on-sync",
                "Analyze Audio on Sync",
                SettingsState.audio_analyze_on_sync,
                SettingsState.set_audio_analyze_on_sync,
            ),
            _help_text("When enabled, new tracks will be analyzed during sync"),
        ),
        _field_group(
            _field_label("Duration Limit (seconds)", "set-audio-duration-limit"),
            _input_40(
                id="set-audio-duration-limit",
                aria_describedby="set-audio-duration-help",
                type="number",
                value=SettingsState.audio_duration_limit,
                on_change=SettingsState.set_audio_duration_limit,
                width="120px",
            ),
            # Visible help (not a hover-only tooltip), announced as the field's description.
            _help_text(
                "Seconds of audio to analyze per track. 0 = full track.",
                id="set-audio-duration-help",
            ),
        ),
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
            _switch_row(
                "set-mb-enabled",
                "Enable MusicBrainz Enrichment",
                SettingsState.musicbrainz_enabled,
                SettingsState.set_musicbrainz_enabled,
            ),
            _switch_row(
                "set-mb-enrich-on-sync",
                "Enrich on Sync",
                SettingsState.musicbrainz_enrich_on_sync,
                SettingsState.set_musicbrainz_enrich_on_sync,
            ),
            _help_text("When enabled, new tracks will be enriched during sync"),
        ),
        _field_group(
            _field_label("Confidence Threshold", "set-mb-confidence"),
            rx.hstack(
                # A native range: a Radix slider thumb can't take a name.
                rx.el.input(
                    type="range",
                    id="set-mb-confidence",
                    min="0",
                    max="100",
                    step="5",
                    value=SettingsState.musicbrainz_confidence_threshold,
                    on_change=SettingsState.set_musicbrainz_confidence_threshold,
                    aria_describedby="set-mb-confidence-help",
                    style={"width": "200px", "maxWidth": "100%"},
                ),
                rx.text(
                    SettingsState.musicbrainz_confidence_threshold.to(str) + "%",
                    size="2",
                    color="var(--fg-2)",
                    font_family="var(--font-mono)",
                ),
                spacing="3",
                align="center",
            ),
            _help_text(
                "Minimum match score (0-100) for accepting MusicBrainz results",
                id="set-mb-confidence-help",
            ),
        ),
        _field_group(
            _field_label("Contact Email", "set-mb-contact-email"),
            _input_40(
                id="set-mb-contact-email",
                aria_describedby="set-mb-contact-email-help",
                placeholder="your@email.com",
                value=SettingsState.musicbrainz_contact_email,
                on_change=SettingsState.set_musicbrainz_contact_email,
                width="100%",
            ),
            _help_text(
                "Required by MusicBrainz Terms of Service for API usage",
                id="set-mb-contact-email-help",
            ),
        ),
        spacing="4",
        width="100%",
    )


# ── Advanced section ─────────────────────────────────────────────────


def _read_only_value(label: str, value: rx.Var | str) -> rx.Component:
    """A labelled read-only value (a path): not a control, so no ``<label for>``."""
    return _field_group(
        rx.box(label, class_name="field-label"),
        rx.text(
            value,
            size="2",
            color="var(--fg-3)",
            style={"fontFamily": "var(--font-mono)", "wordBreak": "break-all"},
        ),
    )


def _advanced_section() -> rx.Component:
    return rx.vstack(
        _section_heading("Advanced Settings"),
        _read_only_value("Database Path", SettingsState.db_path),
        _read_only_value("FAISS Index Path", SettingsState.faiss_index_path),
        _field_group(
            _field_label("Logging Level", "set-adv-log-level"),
            rx.select(
                ["DEBUG", "INFO", "WARNING", "ERROR"],
                id="set-adv-log-level",
                value=SettingsState.log_level,
                on_change=SettingsState.set_log_level,
                width="100%",
            ),
        ),
        spacing="4",
        width="100%",
    )


# ── Appearance section ───────────────────────────────────────────────


def _appearance_section() -> rx.Component:
    """Theme, density, and accent-intensity controls."""
    return rx.vstack(
        _section_heading("Appearance"),
        # Sync the persisted density/accent from localStorage into state on mount
        # so the radio groups show the correct selection after a reload.
        rx.box(on_mount=AppearanceState.hydrate_appearance, display="none"),
        rx.box(
            rx.box("Theme", class_name="field-label"),
            rx.el.button(
                rx.color_mode_cond(light=rx.icon("moon", size=16), dark=rx.icon("sun", size=16)),
                rx.color_mode_cond(light="Switch to dark", dark="Switch to light"),
                class_name="btn btn-3 btn-soft",
                on_click=rx.toggle_color_mode,
                type="button",
            ),
            rx.box("Light-first; your choice persists across reloads.", class_name="field-help"),
            class_name="tile",
            style={"width": "100%"},
        ),
        rx.box(
            seg_radio_group(
                name="pm-density",
                legend="Density",
                options=[("comfortable", "Comfortable"), ("compact", "Compact")],
                current=AppearanceState.density,
                on_select=AppearanceState.set_density,
                legend_id="set-appearance-density-legend",
                style={"maxWidth": "320px"},
            ),
            class_name="tile",
            style={"width": "100%"},
        ),
        rx.box(
            seg_radio_group(
                name="pm-accent",
                legend="Accent intensity",
                options=[("subtle", "Subtle"), ("balanced", "Balanced"), ("vivid", "Vivid")],
                current=AppearanceState.accent,
                on_select=AppearanceState.set_accent,
                legend_id="set-appearance-accent-legend",
                style={"maxWidth": "320px"},
            ),
            class_name="tile",
            style={"width": "100%"},
        ),
        spacing="4",
        width="100%",
    )


# ── Section tabs (one tree at every width) ───────────────────────────

# (id, icon, full label, panel builder), in tab order. Ids are the deep-link
# contract (``/settings?section=<id>``), so they must match SETTINGS_SECTIONS.
# Labels come from SECTION_LABELS, which the Save/Discard bar also uses to name
# the sections with unsaved changes.
_SECTIONS: list[tuple[str, str, str, Callable[[], rx.Component]]] = [
    ("plex", "server", SECTION_LABELS["plex"], _plex_section),
    ("ai", "brain", SECTION_LABELS["ai"], _ai_provider_section),
    ("embedding", "layers", SECTION_LABELS["embedding"], _embedding_section),
    ("audio", "audio-waveform", SECTION_LABELS["audio"], _audio_section),
    ("musicbrainz", "disc", SECTION_LABELS["musicbrainz"], _musicbrainz_section),
    ("advanced", "settings", SECTION_LABELS["advanced"], _advanced_section),
    ("appearance", "palette", SECTION_LABELS["appearance"], _appearance_section),
]
assert tuple(s[0] for s in _SECTIONS) == SETTINGS_SECTIONS, "Settings tabs != SETTINGS_SECTIONS"


def _settings_tabs() -> rx.Component:
    """The section list and panels as one vertical Radix Tabs tree.

    Radix provides the tablist/tab/tabpanel roles, ``aria-selected``, the
    tab-to-panel labelling, a roving tabindex and arrow/Home/End keys. Panels take
    ``tabIndex=-1`` so Tab from the list lands on the panel's first control (they
    stay focusable by script). At 768px and below, CSS lays the list out as a
    horizontal scroll row above the panel (``assets/styles.css`` §5).
    """
    return rx.tabs.root(
        rx.tabs.list(
            *[
                rx.tabs.trigger(
                    rx.icon(icon, size=16),
                    rx.el.span(label),
                    value=value,
                    class_name="settings-tab",
                )
                for value, icon, label, _ in _SECTIONS
            ],
            class_name="settings-tab-list",
            aria_label="Settings sections",
        ),
        *[
            rx.tabs.content(
                builder(),
                value=value,
                class_name="settings-panel",
                custom_attrs={"tabIndex": -1},
            )
            for value, _, _, builder in _SECTIONS
        ],
        orientation="vertical",
        value=SettingsState.active_tab,
        on_change=SettingsState.set_active_tab,
        class_name="settings-tabs",
        width="100%",
    )


# ── One Save/Discard bar (webui-honest-status-and-first-run, D12) ─────

# Ids for the bar and its result region (Playwright and render tests use them).
SAVEBAR_ID = "set-savebar"
SAVE_STATUS_ID = "set-save-status"
EMBEDDING_CONFIRM_ID = "set-emb-confirm"


def _savebar() -> rx.Component:
    """The single unsaved-changes bar: which sections changed, Discard and Save.

    Rendered only while the form differs from the saved settings. ``.savebar`` is
    ``position: sticky; bottom: 16px`` inside the page column, so it stays in
    view however long the open section is. Save commits every section (it may
    open the embedding-change confirmation first); Discard restores the saved
    values, staged secrets included.
    """
    return rx.cond(
        SettingsState.has_unsaved_changes,
        rx.el.div(
            rx.el.span(class_name="dot dot-warning pulse", aria_hidden="true"),
            rx.el.div(
                rx.el.p(
                    rx.cond(
                        SettingsState.unsaved_from_earlier,
                        "Unsaved changes from earlier",
                        "Unsaved changes",
                    ),
                    class_name="savebar-title",
                ),
                rx.el.p(
                    "In ",
                    SettingsState.changed_sections_label,
                    class_name="savebar-sections",
                ),
                class_name="savebar-text",
            ),
            rx.el.div(
                rx.el.button(
                    "Discard",
                    id="set-discard",
                    type="button",
                    class_name="btn btn-3 btn-soft",
                    on_click=SettingsState.discard_changes,
                ),
                rx.el.button(
                    "Save",
                    id="set-save",
                    type="button",
                    class_name="btn btn-3 btn-primary",
                    on_click=SettingsState.save_all_settings,
                ),
                class_name="savebar-actions",
            ),
            id=SAVEBAR_ID,
            class_name="savebar animate-slide-up",
        ),
        rx.fragment(),
    )


def _save_status_region() -> rx.Component:
    """The one persistent ``role=status`` region for save results.

    It sits beside the bar but outside its condition, so it still exists to
    announce "Settings saved." after the bar disappears.
    """
    return _status_text(SettingsState.save_status_message, SAVE_STATUS_ID, margin_top="0")


def _embedding_change_confirm() -> rx.Component:
    """Confirmation before saving an embedding change that doesn't fit the search index."""
    return scope_confirm(
        open_var=SettingsState.embedding_confirm_open,
        on_open_change=SettingsState.set_embedding_confirm_open,
        title="Save an embedding change that stops search?",
        lines=[
            SettingsState.embedding_change_warning,
            "Saving leaves the current index as it is. Rebuild embeddings from Doctor "
            "when you're ready.",
        ],
        privacy_note=None,
        confirm_label="Save and rebuild later",
        on_confirm=SettingsState.confirm_embedding_change,
        secondary_label="Keep current provider",
        on_secondary=SettingsState.revert_embedding_change,
        id_prefix=EMBEDDING_CONFIRM_ID,
    )


# ══════════════════════════════════════════════════════════════════════
#  Settings Page
# ══════════════════════════════════════════════════════════════════════


def settings() -> rx.Component:
    content = rx.vstack(
        # ── Page header: the page's only h1 (section titles are h2) ─
        rx.vstack(
            rx.heading("Settings", size="8", id="main-heading", tab_index=-1),
            rx.text("Configure your PlexMix instance", size="3", color="var(--fg-3)"),
            spacing="1",
            align="start",
        ),
        # ── Sections: one tab list + panels at every width ─────────
        _settings_tabs(),
        # ── One Save/Discard bar (sticky) + its result region ──────
        _savebar(),
        _save_status_region(),
        _embedding_change_confirm(),
        spacing="6",
        width="100%",
        class_name="animate-fade-in-up",
    )
    return layout(content, heading=False)
