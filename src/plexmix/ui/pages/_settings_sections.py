"""Extracted settings page section builders.

Contains the two largest section builder functions (_ai_provider_section and
_embedding_section) plus the small shared helpers they depend on.  The parent
module (``settings.py``) re-imports the helpers so the remaining sections
(Plex, Audio, Advanced) can keep using them without duplication.

Accessibility contract (webui-keyboard-and-semantics): every control has a
static, namespaced ``id`` (``set-<section>-<field>``, secrets keep
``secret-<field>``) and a visible ``<label for>`` from :func:`_field_label`.
Settings renders each section exactly once (one tabs tree), so the ids are
unique in the DOM. Later changes must keep these ids, the ``aria-*`` wiring and
the persistent ``role="status"`` wrappers from :func:`_status_text`.

One save model (webui-honest-status-and-first-run, D12): sections carry no Save
of their own. ``settings.py`` renders the single Save/Discard bar and the
``set-save-status`` region; :func:`_button_row` holds only a section's Test.
"""

from typing import Any

import reflex as rx

from plexmix.ai.local_provider import LOCAL_LLM_MODELS
from plexmix.services.readiness import LOCAL_REMEDY
from plexmix.ui.components.inputs import filter_input
from plexmix.ui.states.settings_state import (
    AI_MODEL_TEXT_ID,
    SECRET_FIELDS,
    SettingsState,
    secret_input_id,
)

# Calling an event handler on the State class with arguments builds an event spec
# (Reflex syntax); mypy reads it as an unbound method call, so go through Any.
_settings_events: Any = SettingsState


# ── Helpers ──────────────────────────────────────────────────────────


def _field_label(text: str, html_for: str) -> rx.Component:
    """Visible label above a control, tied to it by ``for``/``id``.

    The label is the control's accessible name, so a placeholder or example
    value is never the only name. Clicking it focuses (or toggles) the control.
    """
    return rx.el.label(text, html_for=html_for, class_name="field-label")


def _help_text(text: str, id: str | None = None) -> rx.Component:
    """Muted help text below an input.

    Pass ``id`` and reference it from the control's ``aria_describedby`` so the
    help is announced as the control's description.
    """
    if id is None:
        return rx.text(text, size="1", color="var(--fg-3)")
    return rx.text(text, id=id, size="1", color="var(--fg-3)")


def _status_text(status_var: Any, region_id: str, margin_top: str = "2") -> rx.Component:
    """Inline status message in a persistent polite live region.

    The ``role="status"`` wrapper (``id=region_id``) is always mounted, empty
    until a result is written, so screen readers announce Test/Save results
    without moving focus. Only the content inside it is conditional; its
    wording and level logic belong to the status-message owner.
    """
    return rx.box(
        rx.cond(
            status_var != "",
            rx.cond(
                status_var.contains("✓"),
                # ── Success ──
                rx.hstack(
                    rx.icon("circle-check", size=16, color="var(--success-fg)", flex_shrink="0"),
                    rx.text(status_var, size="2", color="var(--success-fg)"),
                    spacing="2",
                    align="center",
                    padding_x="12px",
                    padding_y="8px",
                    border_radius="var(--radius-md)",
                    background_color="var(--success-bg)",
                    width="100%",
                    margin_top=margin_top,
                    class_name="animate-fade-in",
                ),
                rx.cond(
                    status_var.contains("✗"),
                    # ── Error ──
                    rx.hstack(
                        rx.icon("circle-x", size=16, color="var(--error-fg)", flex_shrink="0"),
                        rx.text(status_var, size="2", color="var(--error-fg)"),
                        spacing="2",
                        align="center",
                        padding_x="12px",
                        padding_y="8px",
                        border_radius="var(--radius-md)",
                        background_color="var(--error-bg)",
                        width="100%",
                        margin_top=margin_top,
                        class_name="animate-fade-in",
                    ),
                    rx.cond(
                        status_var.contains("⚠"),
                        # ── Warning ──
                        rx.hstack(
                            rx.icon(
                                "triangle-alert",
                                size=16,
                                color="var(--warning-fg)",
                                flex_shrink="0",
                            ),
                            rx.text(status_var, size="2", color="var(--warning-fg)"),
                            spacing="2",
                            align="center",
                            padding_x="12px",
                            padding_y="8px",
                            border_radius="var(--radius-md)",
                            background_color="var(--warning-bg)",
                            width="100%",
                            margin_top=margin_top,
                            class_name="animate-fade-in",
                        ),
                        # ── Info / in-progress ──
                        rx.hstack(
                            rx.icon(
                                "loader",
                                size=16,
                                color="var(--fg-2)",
                                flex_shrink="0",
                                class_name="animate-spin",
                            ),
                            rx.text(status_var, size="2", color="var(--fg-2)"),
                            spacing="2",
                            align="center",
                            padding_x="12px",
                            padding_y="8px",
                            border_radius="var(--radius-md)",
                            background_color="var(--surface-sunken)",
                            width="100%",
                            margin_top=margin_top,
                        ),
                    ),
                ),
            ),
            rx.fragment(),
        ),
        id=region_id,
        role="status",
        aria_live="polite",
        aria_atomic="true",
        width="100%",
    )


def _section_heading(text: str) -> rx.Component:
    """Section heading: an h2 under the page's single "Settings" h1."""
    return rx.heading(text, size="5", weight="bold", as_="h2")


def _field_group(*children: rx.Component, **kwargs: Any) -> rx.Component:
    """Group of related fields in a vstack."""
    return rx.vstack(*children, spacing="2", width="100%", **kwargs)


def _input_40(
    id: str | None = None,
    aria_describedby: str | None = None,
    **kwargs: Any,
) -> rx.Component:
    """Standard 40px-height input.

    ``id`` names the input for its ``<label for>``; ``aria_describedby`` points
    at help or error text. Radix's TextField spreads both onto the ``<input>``.
    """
    if id is not None:
        kwargs["id"] = id
    if aria_describedby is not None:
        kwargs["aria_describedby"] = aria_describedby
    return rx.input(height="40px", **kwargs)


def _switch_row(id: str, label: str, checked: rx.Var | bool, on_change: Any) -> rx.Component:
    """A switch with a visible ``<label for>``: the label names it and toggles it on click."""
    return rx.hstack(
        rx.switch(id=id, checked=checked, on_change=on_change),
        rx.el.label(label, html_for=id, class_name="switch-label"),
        spacing="3",
        align="center",
    )


def _model_chip(model: Any, current: Any) -> rx.Component:
    """One discovered model: a real button, pressed when it is the configured model.

    Named "<model> – use this model": the visible text (the model name) stays at
    the start of the name (label in name), followed by what activating it does.
    """
    return rx.el.button(
        model,
        type="button",
        class_name="badge badge-blue chip-btn",
        aria_label=model + " – use this model",
        aria_pressed=rx.cond(current == model, "true", "false"),
        on_click=_settings_events.select_discovered_model(model),
    )


def _model_chips(label_id: str, current: Any) -> rx.Component:
    """Discovered models as a labelled group of toggle buttons.

    ``current`` is the setting the chip writes (``select_discovered_model``):
    the custom model name for the Custom provider, the model for Local.
    """
    return rx.cond(
        SettingsState.discovered_models.length() > 0,
        rx.vstack(
            rx.text("Available models:", id=label_id, size="2", weight="medium"),
            rx.hstack(
                rx.foreach(
                    SettingsState.discovered_models,
                    lambda m: _model_chip(m, current),
                ),
                role="group",
                aria_labelledby=label_id,
                wrap="wrap",
                spacing="2",
            ),
            spacing="2",
            width="100%",
        ),
        rx.fragment(),
    )


# ── Secret inputs (replace-only; values never reach the browser) ─────


def _secret_status(field: str) -> rx.Component:
    """Where the stored secret lives (never its value), or what Save will do."""
    source = SettingsState.secret_source[field]
    staged = SettingsState.secret_staged[field]

    def _line(icon: str, *children: rx.Component | str, color: str = "var(--fg-3)") -> rx.Component:
        return rx.hstack(
            rx.icon(icon, size=14, color=color, flex_shrink="0"),
            rx.text(*children, size="1", color=color),
            spacing="2",
            align="center",
        )

    return rx.box(
        rx.cond(
            staged == "replace",
            _line("pencil", "New value entered. Save to store it.", color="var(--fg-2)"),
            rx.cond(
                staged == "clear",
                _line("eraser", "Will be removed when you save.", color="var(--fg-2)"),
                rx.cond(
                    source.startswith("env:"),
                    _line(
                        "terminal",
                        "Set by environment variable ",
                        rx.el.code(source.replace("env:", ""), font_family="var(--font-mono)"),
                        ". A value saved here is ignored while it is set.",
                        color="var(--fg-2)",
                    ),
                    rx.cond(
                        source == "keyring",
                        _line("key-round", "Stored in system keyring"),
                        rx.cond(
                            source == "config",
                            _line("file-key", "Stored in the settings file"),
                            _line("circle-dashed", "Not set"),
                        ),
                    ),
                ),
            ),
        ),
        id=f"{secret_input_id(field)}-status",
        width="100%",
    )


def _secret_input(
    field: str,
    placeholder: str,
    optional: bool = False,
    error_var: Any = None,
) -> rx.Component:
    """Uncontrolled, replace-only password input for a secret.

    It always starts empty and has no ``value`` binding: the typed text is sent
    once, on blur, to ``SettingsState.stage_secret`` and held on the server. Save
    empties it again via ``rx.set_value("secret-<field>", "")``. Optional secrets
    get a Clear button (and Undo while a clear is staged). The input's id is
    ``secret_input_id(field)``; label it with ``_field_label(..., html_for=<that id>)``.
    """
    input_id = secret_input_id(field)
    label = SECRET_FIELDS[field].label
    stored = SettingsState.secret_stored[field]
    staged = SettingsState.secret_staged[field]
    clear_controls: list[rx.Component] = []
    if optional:
        clear_controls.append(
            rx.cond(
                staged == "clear",
                rx.button(
                    "Undo",
                    on_click=_settings_events.discard_secret(field),
                    variant="soft",
                    color_scheme="gray",
                    height="40px",
                    type="button",
                    custom_attrs={"aria-label": f"Undo clearing the {label}"},
                ),
                rx.cond(
                    stored,
                    rx.button(
                        "Clear",
                        on_click=_settings_events.clear_secret(field),
                        variant="soft",
                        color_scheme="gray",
                        height="40px",
                        type="button",
                        custom_attrs={"aria-label": f"Clear the stored {label} on save"},
                    ),
                    rx.fragment(),
                ),
            )
        )
    children: list[rx.Component] = [
        rx.hstack(
            _input_40(
                type="password",
                default_value="",
                id=input_id,
                placeholder=rx.cond(stored, "Enter a new value to replace it", placeholder),
                # rx.input types auto_complete as bool; pass the HTML token as-is.
                custom_attrs={
                    "autoComplete": "new-password",
                    "aria-describedby": f"{input_id}-status",
                },
                on_blur=lambda value: _settings_events.stage_secret(field, value),
                width="100%",
            ),
            *clear_controls,
            spacing="2",
            align="center",
            width="100%",
        ),
        _secret_status(field),
    ]
    if error_var is not None:
        children.append(
            rx.cond(
                error_var != "",
                rx.text(error_var, size="1", color="var(--warning-fg)"),
                rx.fragment(),
            )
        )
    return rx.vstack(*children, spacing="1", width="100%")


def _button_row(
    test_label: str,
    test_on_click: Any,
    test_loading: Any,
) -> rx.Component:
    """A section's Test button (soft). Saving is the page's single Save/Discard bar.

    While a test runs the Test button shows Radix's spinner but stays enabled:
    ``loading`` alone would also set the native ``disabled``, and disabling the
    focused button drops keyboard focus to ``<body>`` (the result is announced
    while focus stays here). It is marked busy and ``aria-disabled`` instead, and
    the test handlers ignore activation while a test is already running.
    """
    busy = rx.cond(test_loading, "true", "false")
    return rx.hstack(
        rx.button(
            test_label,
            on_click=test_on_click,
            loading=test_loading,
            disabled=False,
            aria_busy=busy,
            aria_disabled=busy,
            variant="soft",
        ),
        spacing="3",
        width="100%",
        margin_top="4",
    )


# ── AI model: curated list, the saved model, or one that isn't listed ──

# Id of the always-mounted note beside the AI model selects ("Not in current list").
AI_MODEL_NOTE_ID = "set-ai-model-note"


def _unlisted_model_note() -> rx.Component:
    """The "Not in current list" badge while the selected model isn't a curated one.

    The wrapper is always mounted, so the selects' ``aria-describedby`` resolves.
    """
    return rx.el.div(
        rx.cond(
            SettingsState.ai_model_unlisted,
            rx.el.span("Not in current list", class_name="badge badge-gray"),
            rx.fragment(),
        ),
        id=AI_MODEL_NOTE_ID,
    )


def _unlisted_model_item() -> rx.Component:
    """An extra select item for the current model when it isn't in the curated list."""
    return rx.cond(
        SettingsState.ai_model_unlisted,
        rx.select.item(
            rx.text(SettingsState.ai_model, size="2", weight="bold"),
            value=SettingsState.ai_model,
        ),
        rx.fragment(),
    )


def _unlisted_model_field() -> rx.Component:
    """The "Use a model that isn't listed" field, client-owned (webui-input-responsiveness).

    It shows the current model only while that model is unlisted, commits 300 ms
    after typing pauses and on blur (``set_ai_model_text``), and never takes a
    ``value=``: server writes reach it through ``rx.set_value(AI_MODEL_TEXT_ID, …)``.
    """
    return rx.fragment(
        _field_label("Use a model that isn't listed", AI_MODEL_TEXT_ID),
        filter_input(
            id=AI_MODEL_TEXT_ID,
            name="ai_model_text",
            value=SettingsState.ai_model_text,
            on_commit=SettingsState.set_ai_model_text,
            placeholder="Model name",
            class_name="input",
            aria_describedby="set-ai-model-custom-help",
        ),
        _help_text(
            "Type any model name your provider offers. It replaces the selection above.",
            id="set-ai-model-custom-help",
        ),
    )


# ── AI Provider section ──────────────────────────────────────────────


def _ai_provider_section() -> rx.Component:
    return rx.vstack(
        _section_heading("AI Provider"),
        _field_group(
            _field_label("Provider", "set-ai-provider"),
            rx.select(
                [
                    "Google",
                    "OpenAI",
                    "Anthropic",
                    "Cohere",
                    "Custom (OpenAI-Compatible)",
                    "Local (Offline)",
                ],
                id="set-ai-provider",
                value=SettingsState.ai_provider_display,
                on_change=SettingsState.set_ai_provider_from_display,
                width="100%",
            ),
        ),
        # Local deps warning
        rx.cond(
            ~SettingsState.local_deps_available & (SettingsState.ai_provider == "local"),
            rx.callout(
                f"Local AI dependencies are not installed. {LOCAL_REMEDY}.",
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
                    _field_label("Endpoint URL", "set-ai-custom-endpoint"),
                    _input_40(
                        id="set-ai-custom-endpoint",
                        placeholder="http://localhost:11434/v1",
                        value=SettingsState.ai_custom_endpoint,
                        on_change=SettingsState.set_ai_custom_endpoint,
                        width="100%",
                    ),
                ),
                _field_group(
                    _field_label("Model Name", "set-ai-custom-model"),
                    _input_40(
                        id="set-ai-custom-model",
                        placeholder="e.g., llama3, mistral, gpt-4o",
                        value=SettingsState.ai_custom_model,
                        on_change=SettingsState.set_ai_custom_model,
                        width="100%",
                    ),
                ),
                _field_group(
                    _field_label("API Key (optional)", secret_input_id("ai_custom_api_key")),
                    _secret_input(
                        "ai_custom_api_key",
                        "Leave empty for local endpoints",
                        optional=True,
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
                _model_chips("set-ai-custom-models-label", SettingsState.ai_custom_model),
                spacing="3",
                width="100%",
            ),
            # Non-custom, non-local: show API key
            rx.cond(
                (SettingsState.ai_provider != "local") & (SettingsState.ai_provider != "custom"),
                _field_group(
                    _field_label("API Key", secret_input_id("ai_api_key")),
                    _secret_input(
                        "ai_api_key",
                        "Enter API key",
                        error_var=SettingsState.ai_api_key_error,
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
                # Each branch labels its own select, so every id stays unique.
                rx.cond(
                    SettingsState.ai_provider == "local",
                    rx.fragment(
                        _field_label("Model", "set-ai-local-model"),
                        rx.select.root(
                            rx.select.trigger(
                                id="set-ai-local-model",
                                placeholder="Select local model",
                                custom_attrs={"aria-describedby": AI_MODEL_NOTE_ID},
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
                                # The saved model when it isn't one of the above
                                # (e.g. one discovered on an endpoint), never a blank.
                                _unlisted_model_item(),
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
                    ),
                    rx.fragment(
                        _field_label("Model", "set-ai-model"),
                        # The curated list plus the saved/entered model when it
                        # isn't in it, so the select never shows a blank value.
                        rx.select(
                            SettingsState.ai_model_choices,
                            id="set-ai-model",
                            value=SettingsState.ai_model,
                            on_change=SettingsState.set_ai_model,
                            placeholder="Select model...",
                            width="100%",
                            custom_attrs={"aria-describedby": AI_MODEL_NOTE_ID},
                        ),
                    ),
                ),
                _unlisted_model_note(),
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
                _unlisted_model_field(),
                spacing="2",
                width="100%",
            ),
            rx.box(),
        ),
        # Temperature (a native range: a Radix slider thumb can't take a name)
        _field_group(
            _field_label("Temperature", "set-ai-temperature"),
            rx.hstack(
                rx.el.input(
                    type="range",
                    id="set-ai-temperature",
                    min="0",
                    max="1",
                    step="0.1",
                    value=SettingsState.ai_temperature,
                    on_change=SettingsState.set_ai_temperature,
                    aria_describedby="set-ai-temperature-help",
                    style={"flex": "1", "minWidth": "0"},
                ),
                rx.text(
                    SettingsState.ai_temperature,
                    size="2",
                    color="var(--fg-2)",
                    font_family="var(--font-mono)",
                    min_width="3ch",
                    text_align="right",
                ),
                spacing="3",
                align="center",
                width="100%",
            ),
            _help_text(
                "Higher = more creative, lower = more consistent", id="set-ai-temperature-help"
            ),
        ),
        # Local execution mode
        rx.cond(
            SettingsState.ai_provider == "local",
            rx.vstack(
                _field_label("Local Execution Mode", "set-ai-local-mode"),
                rx.select.root(
                    rx.select.trigger(id="set-ai-local-mode", placeholder="Choose mode"),
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
                                    aria_label="Local model download progress",
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
                            _field_label("Endpoint URL", "set-ai-local-endpoint"),
                            _input_40(
                                id="set-ai-local-endpoint",
                                aria_describedby="set-ai-local-endpoint-error",
                                aria_invalid=rx.cond(
                                    SettingsState.local_endpoint_error != "", "true", "false"
                                ),
                                placeholder="http://localhost:11434/v1/chat/completions",
                                value=SettingsState.ai_local_endpoint,
                                on_change=SettingsState.validate_local_endpoint,
                                width="100%",
                            ),
                            # Always mounted, so the input's description reference
                            # resolves; it holds the validation message when there is one.
                            rx.box(
                                rx.cond(
                                    SettingsState.local_endpoint_error != "",
                                    rx.text(
                                        SettingsState.local_endpoint_error,
                                        size="1",
                                        color="var(--error-fg)",
                                    ),
                                    rx.fragment(),
                                ),
                                id="set-ai-local-endpoint-error",
                            ),
                        ),
                        _field_group(
                            _field_label("Endpoint Token", secret_input_id("ai_local_auth_token")),
                            _secret_input(
                                "ai_local_auth_token",
                                "Optional bearer token",
                                optional=True,
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
                        _model_chips("set-ai-local-models-label", SettingsState.ai_model),
                        spacing="3",
                        width="100%",
                    ),
                ),
                spacing="3",
                width="100%",
            ),
            rx.box(),
        ),
        # Test (saving is the page's Save/Discard bar)
        _button_row(
            "Test Provider",
            SettingsState.test_ai_provider,
            SettingsState.testing_connection,
        ),
        _status_text(SettingsState.ai_test_status, "set-ai-status"),
        spacing="4",
        width="100%",
    )


# ── Embedding section ────────────────────────────────────────────────

# Persistent region holding the "doesn't fit your search index" warning.
EMBEDDING_WARNING_ID = "set-emb-index-warning"


def _index_mismatch_warning() -> rx.Component:
    """``.callout-warning`` when the selected embeddings don't fit the existing index.

    The wrapper is a persistent polite live region, so switching to a mismatching
    provider or model announces the warning where the user is.
    """
    return rx.el.div(
        rx.cond(
            SettingsState.embedding_change_warning != "",
            rx.el.div(
                rx.icon("triangle-alert", size=16, class_name="c-ico"),
                rx.el.div(
                    rx.el.div("Doesn't match your search index", class_name="c-title"),
                    rx.el.div(SettingsState.embedding_change_warning, class_name="c-body"),
                ),
                class_name="callout callout-warning",
            ),
            rx.fragment(),
        ),
        id=EMBEDDING_WARNING_ID,
        role="status",
        aria_live="polite",
        style={"width": "100%"},
    )


def _embedding_section() -> rx.Component:
    return rx.vstack(
        _section_heading("Embedding Provider"),
        _field_group(
            _field_label("Provider", "set-emb-provider"),
            rx.select(
                ["Gemini", "OpenAI", "Cohere", "Custom (OpenAI-Compatible)", "Local (Offline)"],
                id="set-emb-provider",
                value=SettingsState.embedding_provider_display,
                on_change=SettingsState.set_embedding_provider_from_display,
                width="100%",
            ),
        ),
        # Local deps warning
        rx.cond(
            ~SettingsState.local_deps_available & (SettingsState.embedding_provider == "local"),
            rx.callout(
                f"Local embedding dependencies are not installed. {LOCAL_REMEDY}.",
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
                    _field_label("Endpoint URL", "set-emb-custom-endpoint"),
                    _input_40(
                        id="set-emb-custom-endpoint",
                        placeholder="http://localhost:11434/v1",
                        value=SettingsState.embedding_custom_endpoint,
                        on_change=SettingsState.set_embedding_custom_endpoint,
                        width="100%",
                    ),
                ),
                _field_group(
                    _field_label("Model Name", "set-emb-custom-model"),
                    _input_40(
                        id="set-emb-custom-model",
                        placeholder="e.g., nomic-embed-text, bge-large-en-v1.5",
                        value=SettingsState.embedding_custom_model,
                        on_change=SettingsState.set_embedding_custom_model,
                        width="100%",
                    ),
                ),
                _field_group(
                    _field_label("API Key (optional)", secret_input_id("embedding_custom_api_key")),
                    _secret_input(
                        "embedding_custom_api_key",
                        "Leave empty for local endpoints",
                        optional=True,
                    ),
                ),
                _field_group(
                    _field_label("Embedding Dimension", "set-emb-custom-dimension"),
                    _input_40(
                        id="set-emb-custom-dimension",
                        aria_describedby="set-emb-custom-dimension-help",
                        type="number",
                        value=SettingsState.embedding_custom_dimension,
                        on_change=SettingsState.set_embedding_custom_dimension,
                        width="120px",
                    ),
                    _help_text(
                        "Must match the output dimension of your model",
                        id="set-emb-custom-dimension-help",
                    ),
                ),
                spacing="3",
                width="100%",
            ),
            # Non-custom, non-local: show API key
            rx.cond(
                SettingsState.embedding_provider != "local",
                _field_group(
                    _field_label("API Key", secret_input_id("embedding_api_key")),
                    _secret_input(
                        "embedding_api_key",
                        "Enter API key",
                        error_var=SettingsState.embedding_api_key_error,
                    ),
                ),
                rx.box(),
            ),
        ),
        # Model selection (non-custom)
        rx.cond(
            SettingsState.embedding_provider != "custom",
            rx.vstack(
                _field_label("Model", "set-emb-model"),
                rx.select(
                    SettingsState.embedding_models,
                    id="set-emb-model",
                    value=SettingsState.embedding_model,
                    on_change=SettingsState.set_embedding_model,
                    placeholder="Select model...",
                    width="100%",
                ),
                _field_label("Custom model name", "set-emb-model-custom"),
                _input_40(
                    id="set-emb-model-custom",
                    aria_describedby="set-emb-model-custom-help",
                    placeholder="Or enter a custom model name...",
                    value=SettingsState.embedding_model,
                    on_change=SettingsState.set_embedding_model,
                    width="100%",
                ),
                _help_text(
                    "Type a model name to override the dropdown selection",
                    id="set-emb-model-custom-help",
                ),
                spacing="2",
                width="100%",
            ),
            rx.box(),
        ),
        # Dimension info (read-only: auto-set by the provider)
        rx.hstack(
            rx.text("Embedding Dimension:", size="2", color="var(--fg-2)", weight="medium"),
            rx.text(
                SettingsState.embedding_dimension,
                size="2",
                color="var(--fg-3)",
                style={"fontFamily": "var(--font-mono)"},
            ),
            spacing="2",
            align="center",
        ),
        _help_text("Set by the provider and model."),
        # Replaces the old static "must match existing embeddings" help with the
        # actual consequence, only when the selection doesn't fit the index.
        _index_mismatch_warning(),
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
                            aria_label="Embedding model download progress",
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
        # Test (saving is the page's Save/Discard bar)
        _button_row(
            "Test Embeddings",
            SettingsState.test_embedding_provider,
            SettingsState.testing_connection,
        ),
        _status_text(SettingsState.embedding_test_status, "set-emb-status"),
        spacing="4",
        width="100%",
    )
