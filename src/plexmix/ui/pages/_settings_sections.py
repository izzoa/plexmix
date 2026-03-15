"""Extracted settings page section builders.

Contains the two largest section builder functions (_ai_provider_section and
_embedding_section) plus the small shared helpers they depend on.  The parent
module (``settings.py``) re-imports the helpers so the remaining sections
(Plex, Audio, Advanced) can keep using them without duplication.
"""

import reflex as rx

from plexmix.ai.local_provider import LOCAL_LLM_MODELS
from plexmix.ui.states.settings_state import SettingsState


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
            status_var.contains("\u2713"),
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
                status_var.contains("\u2717"),
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
                    status_var.contains("\u26a0"),
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
                        rx.icon(
                            "loader",
                            size=16,
                            color="gray.11",
                            flex_shrink="0",
                            class_name="animate-spin",
                        ),
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
                rx.hstack(
                    rx.button(
                        rx.icon("radar", size=14),
                        "Discover Models",
                        on_click=SettingsState.discover_models,
                        loading=SettingsState.is_discovering_models,
                        disabled=SettingsState.ai_custom_endpoint == "",
                        variant="soft",
                        size="2",
                        color_scheme="blue",
                    ),
                    spacing="2",
                    align="center",
                ),
                rx.cond(
                    SettingsState.discovered_models.length() > 0,
                    rx.vstack(
                        rx.text("Available models:", size="2", weight="medium"),
                        rx.hstack(
                            rx.foreach(
                                SettingsState.discovered_models,
                                lambda m: rx.badge(
                                    m,
                                    on_click=lambda model=m: SettingsState.select_discovered_model(
                                        model
                                    ),
                                    variant="surface",
                                    color_scheme="blue",
                                    cursor="pointer",
                                    size="1",
                                    _hover={"background_color": "accent.4"},
                                ),
                            ),
                            wrap="wrap",
                            spacing="2",
                        ),
                        spacing="2",
                        width="100%",
                    ),
                    rx.fragment(),
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
                        rx.hstack(
                            rx.button(
                                rx.icon("radar", size=14),
                                "Discover Models",
                                on_click=SettingsState.discover_models,
                                loading=SettingsState.is_discovering_models,
                                disabled=SettingsState.ai_local_endpoint == "",
                                variant="soft",
                                size="2",
                                color_scheme="blue",
                            ),
                            spacing="2",
                            align="center",
                        ),
                        rx.cond(
                            SettingsState.discovered_models.length() > 0,
                            rx.vstack(
                                rx.text("Available models:", size="2", weight="medium"),
                                rx.hstack(
                                    rx.foreach(
                                        SettingsState.discovered_models,
                                        lambda m: rx.badge(
                                            m,
                                            on_click=lambda model=m: SettingsState.select_discovered_model(
                                                model
                                            ),
                                            variant="surface",
                                            color_scheme="blue",
                                            cursor="pointer",
                                            size="1",
                                            _hover={"background_color": "accent.4"},
                                        ),
                                    ),
                                    wrap="wrap",
                                    spacing="2",
                                ),
                                spacing="2",
                                width="100%",
                            ),
                            rx.fragment(),
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
