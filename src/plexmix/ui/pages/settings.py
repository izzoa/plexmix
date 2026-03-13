import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.states.settings_state import SettingsState
from plexmix.ai.local_provider import LOCAL_LLM_MODELS


# ── Helpers ──────────────────────────────────────────────────────────


def _field_label(text: str) -> rx.Component:
    """Standard label above a form input."""
    return rx.text(text, size="2", weight="medium", color="gray.11")


def _help_text(text: str) -> rx.Component:
    """Muted help text below an input."""
    return rx.text(text, size="1", color="gray.9")


def _status_text(status_var, margin_top: str = "2") -> rx.Component:
    """Inline status message with colored icon and background for success/error/warning."""
    return rx.cond(
        status_var != "",
        rx.cond(
            status_var.contains("✓"),
            # ── Success ──
            rx.hstack(
                rx.icon("circle-check", size=16, color="green.11", flex_shrink="0"),
                rx.text(status_var, size="2", color="green.11"),
                spacing="2",
                align="center",
                padding_x="12px",
                padding_y="8px",
                border_radius="var(--radius-md)",
                background_color="green.3",
                width="100%",
                margin_top=margin_top,
                class_name="animate-fade-in",
            ),
            rx.cond(
                status_var.contains("✗"),
                # ── Error ──
                rx.hstack(
                    rx.icon("circle-x", size=16, color="red.11", flex_shrink="0"),
                    rx.text(status_var, size="2", color="red.11"),
                    spacing="2",
                    align="center",
                    padding_x="12px",
                    padding_y="8px",
                    border_radius="var(--radius-md)",
                    background_color="red.3",
                    width="100%",
                    margin_top=margin_top,
                    class_name="animate-fade-in",
                ),
                rx.cond(
                    status_var.contains("⚠"),
                    # ── Warning ──
                    rx.hstack(
                        rx.icon("triangle-alert", size=16, color="yellow.11", flex_shrink="0"),
                        rx.text(status_var, size="2", color="yellow.11"),
                        spacing="2",
                        align="center",
                        padding_x="12px",
                        padding_y="8px",
                        border_radius="var(--radius-md)",
                        background_color="yellow.3",
                        width="100%",
                        margin_top=margin_top,
                        class_name="animate-fade-in",
                    ),
                    # ── Info / in-progress ──
                    rx.hstack(
                        rx.icon("loader", size=16, color="gray.11", flex_shrink="0",
                                class_name="animate-spin"),
                        rx.text(status_var, size="2", color="gray.11"),
                        spacing="2",
                        align="center",
                        padding_x="12px",
                        padding_y="8px",
                        border_radius="var(--radius-md)",
                        background_color="gray.3",
                        width="100%",
                        margin_top=margin_top,
                    ),
                ),
            ),
        ),
        rx.fragment(),
    )


def _section_heading(text: str) -> rx.Component:
    """Section heading: size 5, bold."""
    return rx.heading(text, size="5", weight="bold")


def _field_group(*children, **kwargs) -> rx.Component:
    """Group of related fields in a vstack."""
    return rx.vstack(*children, spacing="2", width="100%", **kwargs)


def _input_40(**kwargs) -> rx.Component:
    """Standard 40px-height input."""
    return rx.input(height="40px", **kwargs)


def _button_row(
    test_label: str,
    test_on_click,
    test_loading,
    save_on_click,
) -> rx.Component:
    """Test (left, soft) + Save (right, solid orange) button row."""
    return rx.hstack(
        rx.button(
            test_label,
            on_click=test_on_click,
            loading=test_loading,
            variant="soft",
        ),
        rx.spacer(),
        rx.button(
            "Save",
            on_click=save_on_click,
            color_scheme="orange",
        ),
        spacing="3",
        width="100%",
        margin_top="4",
    )


# ── Sidebar navigation ──────────────────────────────────────────────

_NAV_ITEMS = [
    ("plex", "server", "Plex Server"),
    ("ai", "brain", "AI Provider"),
    ("embedding", "layers", "Embeddings"),
    ("audio", "audio-waveform", "Audio Analysis"),
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
            rx.tabs.trigger("Adv", value="advanced"),
        ),
        rx.tabs.content(_plex_section(), value="plex"),
        rx.tabs.content(_ai_provider_section(), value="ai"),
        rx.tabs.content(_embedding_section(), value="embedding"),
        rx.tabs.content(_audio_section(), value="audio"),
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


# ── AI Provider section ──────────────────────────────────────────────


def _ai_provider_section() -> rx.Component:
    return rx.vstack(
        _section_heading("AI Provider"),
        _field_group(
            _field_label("Provider"),
            rx.select(
                [
                    "Google",
                    "OpenAI",
                    "Anthropic",
                    "Cohere",
                    "Custom (OpenAI-Compatible)",
                    "Local (Offline)",
                ],
                value=SettingsState.ai_provider_display,
                on_change=SettingsState.set_ai_provider_from_display,
                width="100%",
            ),
        ),
        # Local deps warning
        rx.cond(
            ~SettingsState.local_deps_available & (SettingsState.ai_provider == "local"),
            rx.callout(
                "Local AI dependencies are not installed. Use the :latest-local Docker image or install with: pip install plexmix[local]",
                icon="triangle_alert",
                color_scheme="yellow",
                size="2",
            ),
            rx.fragment(),
        ),
        # Custom provider fields
        rx.cond(
            SettingsState.ai_provider == "custom",
            rx.vstack(
                rx.callout(
                    "Connect to any OpenAI-compatible API (Ollama, LM Studio, vLLM, Together AI, Groq, etc.)",
                    icon="globe",
                    color_scheme="blue",
                    size="2",
                ),
                _field_group(
                    _field_label("Endpoint URL"),
                    _input_40(
                        placeholder="http://localhost:11434/v1",
                        value=SettingsState.ai_custom_endpoint,
                        on_change=SettingsState.set_ai_custom_endpoint,
                        width="100%",
                    ),
                ),
                _field_group(
                    _field_label("Model Name"),
                    _input_40(
                        placeholder="e.g., llama3, mistral, gpt-4o",
                        value=SettingsState.ai_custom_model,
                        on_change=SettingsState.set_ai_custom_model,
                        width="100%",
                    ),
                ),
                _field_group(
                    _field_label("API Key (optional)"),
                    _input_40(
                        type="password",
                        placeholder="Leave empty for local endpoints",
                        value=SettingsState.ai_custom_api_key,
                        on_change=SettingsState.set_ai_custom_api_key,
                        width="100%",
                    ),
                ),
                spacing="3",
                width="100%",
            ),
            # Non-custom, non-local: show API key
            rx.cond(
                (SettingsState.ai_provider != "local") & (SettingsState.ai_provider != "custom"),
                _field_group(
                    _field_label("API Key"),
                    _input_40(
                        type="password",
                        placeholder="Enter API key",
                        value=SettingsState.ai_api_key,
                        on_change=SettingsState.set_ai_api_key,
                        width="100%",
                    ),
                    _help_text("Get your key from the provider's developer dashboard"),
                ),
                rx.box(),
            ),
        ),
        # Model selection (non-custom providers)
        rx.cond(
            SettingsState.ai_provider != "custom",
            rx.vstack(
                _field_label("Model"),
                rx.cond(
                    SettingsState.ai_provider == "local",
                    rx.select.root(
                        rx.select.trigger(
                            placeholder="Select local model",
                            style={
                                "white_space": "nowrap",
                                "text_overflow": "ellipsis",
                                "overflow": "hidden",
                                "max_width": "100%",
                            },
                        ),
                        rx.select.content(
                            *[
                                rx.select.item(
                                    rx.text(
                                        meta["display_name"],
                                        size="2",
                                        weight="bold",
                                        style={
                                            "white_space": "nowrap",
                                            "text_overflow": "ellipsis",
                                            "overflow": "hidden",
                                            "max_width": "340px",
                                            "display": "block",
                                        },
                                    ),
                                    value=model_id,
                                    key=model_id,
                                )
                                for model_id, meta in sorted(
                                    LOCAL_LLM_MODELS.items(),
                                    key=lambda kv: kv[1]["display_name"].lower(),
                                )
                            ],
                            style={
                                "max_width": "380px",
                                "max_height": "280px",
                                "overflow_y": "auto",
                            },
                        ),
                        value=SettingsState.ai_model,
                        on_change=SettingsState.set_ai_model,
                        width="100%",
                        style={"max_width": "420px"},
                    ),
                    rx.select(
                        SettingsState.ai_models,
                        value=SettingsState.ai_model,
                        on_change=SettingsState.set_ai_model,
                        placeholder="Select model...",
                        width="100%",
                    ),
                ),
                rx.cond(
                    SettingsState.ai_provider == "local",
                    rx.text(
                        SettingsState.local_model_capabilities,
                        size="1",
                        color_scheme="gray",
                        margin_top="1",
                        style={"white_space": "normal", "line_height": "1.2"},
                    ),
                    rx.box(),
                ),
                _input_40(
                    placeholder="Or enter a custom model name...",
                    value=SettingsState.ai_model,
                    on_change=SettingsState.set_ai_model,
                    width="100%",
                ),
                _help_text("Type a model name to override the dropdown selection"),
                spacing="2",
                width="100%",
            ),
            rx.box(),
        ),
        # Temperature
        _field_group(
            _field_label("Temperature"),
            rx.hstack(
                rx.slider(
                    default_value=[SettingsState.ai_temperature],
                    on_change=lambda val: SettingsState.set_ai_temperature(val[0]),
                    min=0.0,
                    max=1.0,
                    step=0.1,
                    width="80%",
                ),
                rx.text(SettingsState.ai_temperature, size="3"),
                width="100%",
            ),
            _help_text("Higher = more creative, lower = more consistent"),
        ),
        # Local execution mode
        rx.cond(
            SettingsState.ai_provider == "local",
            rx.vstack(
                _field_label("Local Execution Mode"),
                rx.select.root(
                    rx.select.trigger(placeholder="Choose mode"),
                    rx.select.content(
                        rx.select.item("Managed (Downloaded)", value="builtin"),
                        rx.select.item("Custom Endpoint", value="endpoint"),
                    ),
                    value=SettingsState.ai_local_mode,
                    on_change=SettingsState.set_ai_local_mode,
                    width="100%",
                ),
                rx.cond(
                    SettingsState.ai_local_mode == "builtin",
                    rx.vstack(
                        rx.callout(
                            "Download a pre-set Hugging Face model once and run entirely offline.",
                            icon="cpu",
                            color_scheme="green",
                            size="2",
                        ),
                        rx.button(
                            "Download / Warm Up Model",
                            on_click=SettingsState.download_local_llm_model,
                            loading=SettingsState.is_downloading_local_llm,
                            variant="soft",
                            align_self="start",
                        ),
                        rx.cond(
                            SettingsState.local_llm_download_status != "",
                            rx.vstack(
                                rx.text(
                                    SettingsState.local_llm_download_status,
                                    size="2",
                                    color_scheme="gray",
                                ),
                                rx.progress(
                                    value=SettingsState.local_llm_download_progress,
                                    max=100,
                                ),
                                spacing="2",
                                width="100%",
                            ),
                            rx.box(),
                        ),
                        spacing="3",
                        width="100%",
                    ),
                    rx.vstack(
                        rx.callout(
                            "Point PlexMix at a running OpenAI-compatible HTTP endpoint on your LAN (Ollama, LM Studio, etc).",
                            icon="globe",
                            color_scheme="blue",
                            size="2",
                        ),
                        _field_group(
                            _field_label("Endpoint URL"),
                            _input_40(
                                placeholder="http://localhost:11434/v1/chat/completions",
                                value=SettingsState.ai_local_endpoint,
                                on_change=SettingsState.validate_local_endpoint,
                                width="100%",
                            ),
                            rx.cond(
                                SettingsState.local_endpoint_error != "",
                                rx.text(
                                    SettingsState.local_endpoint_error,
                                    size="1",
                                    color_scheme="red",
                                ),
                                rx.box(),
                            ),
                        ),
                        _field_group(
                            _field_label("Endpoint Token"),
                            _input_40(
                                type="password",
                                placeholder="Optional bearer token",
                                value=SettingsState.ai_local_auth_token,
                                on_change=SettingsState.set_ai_local_auth_token,
                                width="100%",
                            ),
                        ),
                        spacing="3",
                        width="100%",
                    ),
                ),
                spacing="3",
                width="100%",
            ),
            rx.box(),
        ),
        # Test + Save
        _button_row(
            "Test Provider",
            SettingsState.test_ai_provider,
            SettingsState.testing_connection,
            SettingsState.save_all_settings,
        ),
        _status_text(SettingsState.ai_test_status),
        spacing="4",
        width="100%",
    )


# ── Embedding section ────────────────────────────────────────────────


def _embedding_section() -> rx.Component:
    return rx.vstack(
        _section_heading("Embedding Provider"),
        _field_group(
            _field_label("Provider"),
            rx.select(
                ["Gemini", "OpenAI", "Cohere", "Custom (OpenAI-Compatible)", "Local (Offline)"],
                value=SettingsState.embedding_provider_display,
                on_change=SettingsState.set_embedding_provider_from_display,
                width="100%",
            ),
        ),
        # Local deps warning
        rx.cond(
            ~SettingsState.local_deps_available & (SettingsState.embedding_provider == "local"),
            rx.callout(
                "Local embedding dependencies are not installed. Use the :latest-local Docker image or install with: pip install plexmix[local]",
                icon="triangle_alert",
                color_scheme="yellow",
                size="2",
            ),
            rx.fragment(),
        ),
        # Custom embedding fields
        rx.cond(
            SettingsState.embedding_provider == "custom",
            rx.vstack(
                rx.callout(
                    "Connect to any OpenAI-compatible embedding API. You must know the embedding dimension for your model.",
                    icon="globe",
                    color_scheme="blue",
                    size="2",
                ),
                _field_group(
                    _field_label("Endpoint URL"),
                    _input_40(
                        placeholder="http://localhost:11434/v1",
                        value=SettingsState.embedding_custom_endpoint,
                        on_change=SettingsState.set_embedding_custom_endpoint,
                        width="100%",
                    ),
                ),
                _field_group(
                    _field_label("Model Name"),
                    _input_40(
                        placeholder="e.g., nomic-embed-text, bge-large-en-v1.5",
                        value=SettingsState.embedding_custom_model,
                        on_change=SettingsState.set_embedding_custom_model,
                        width="100%",
                    ),
                ),
                _field_group(
                    _field_label("API Key (optional)"),
                    _input_40(
                        type="password",
                        placeholder="Leave empty for local endpoints",
                        value=SettingsState.embedding_custom_api_key,
                        on_change=SettingsState.set_embedding_custom_api_key,
                        width="100%",
                    ),
                ),
                _field_group(
                    _field_label("Embedding Dimension"),
                    _input_40(
                        type="number",
                        value=SettingsState.embedding_custom_dimension,
                        on_change=SettingsState.set_embedding_custom_dimension,
                        width="120px",
                    ),
                    _help_text("Must match the output dimension of your model"),
                ),
                spacing="3",
                width="100%",
            ),
            # Non-custom, non-local: show API key
            rx.cond(
                SettingsState.embedding_provider != "local",
                _field_group(
                    _field_label("API Key"),
                    _input_40(
                        type="password",
                        placeholder="Enter API key",
                        value=SettingsState.embedding_api_key,
                        on_change=SettingsState.set_embedding_api_key,
                        width="100%",
                    ),
                ),
                rx.box(),
            ),
        ),
        # Model selection (non-custom)
        rx.cond(
            SettingsState.embedding_provider != "custom",
            rx.vstack(
                _field_label("Model"),
                rx.select(
                    SettingsState.embedding_models,
                    value=SettingsState.embedding_model,
                    on_change=SettingsState.set_embedding_model,
                    placeholder="Select model...",
                    width="100%",
                ),
                _input_40(
                    placeholder="Or enter a custom model name...",
                    value=SettingsState.embedding_model,
                    on_change=SettingsState.set_embedding_model,
                    width="100%",
                ),
                _help_text("Type a model name to override the dropdown selection"),
                spacing="2",
                width="100%",
            ),
            rx.box(),
        ),
        # Dimension info
        rx.hstack(
            rx.text("Embedding Dimension:", size="2", color="gray.11", weight="medium"),
            rx.text(
                SettingsState.embedding_dimension,
                size="2",
                color="gray.9",
                style={"fontFamily": "var(--font-mono)"},
            ),
            spacing="2",
            align="center",
        ),
        _help_text("Auto-set by provider. Must match existing embeddings or regenerate."),
        # Local model download
        rx.cond(
            SettingsState.embedding_provider == "local",
            rx.vstack(
                rx.callout(
                    "Local models download via Hugging Face when first used. Use the button below to pre-cache them for offline use and watch progress as files download/extract.",
                    icon="info",
                    color_scheme="blue",
                    size="2",
                ),
                rx.hstack(
                    rx.button(
                        "Download / Cache Model",
                        on_click=SettingsState.download_local_embedding_model,
                        loading=SettingsState.is_downloading_local_model,
                        variant="soft",
                    ),
                    align="start",
                ),
                rx.cond(
                    SettingsState.local_download_status != "",
                    rx.vstack(
                        rx.text(SettingsState.local_download_status, size="2", color_scheme="gray"),
                        rx.progress(
                            value=SettingsState.local_download_progress,
                            max=100,
                        ),
                        spacing="2",
                        width="100%",
                    ),
                    rx.box(),
                ),
                spacing="3",
                width="100%",
            ),
            rx.box(),
        ),
        # Test + Save
        _button_row(
            "Test Embeddings",
            SettingsState.test_embedding_provider,
            SettingsState.testing_connection,
            SettingsState.save_all_settings,
        ),
        _status_text(SettingsState.embedding_test_status),
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
                        _advanced_section(),
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
