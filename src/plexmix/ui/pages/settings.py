import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.states.settings_state import SettingsState
from plexmix.ai.local_provider import LOCAL_LLM_MODELS


def plex_tab() -> rx.Component:
    return rx.vstack(
        rx.heading("Plex Configuration", size="6", margin_bottom="4"),
        rx.vstack(
            rx.text("Server URL", size="3", weight="bold"),
            rx.input(
                placeholder="http://localhost:32400",
                value=SettingsState.plex_url,
                on_change=SettingsState.set_plex_url,
                width="100%",
            ),
            rx.text("Plex Token", size="3", weight="bold", margin_top="3"),
            rx.input(
                type="password",
                placeholder="Enter your Plex token",
                value=SettingsState.plex_token,
                on_change=SettingsState.set_plex_token,
                width="100%",
            ),
            rx.text("Music Library", size="3", weight="bold", margin_top="3"),
            rx.select(
                SettingsState.plex_libraries,
                value=SettingsState.plex_library,
                on_change=SettingsState.set_plex_library,
                placeholder="Select library...",
                width="100%",
            ),
            rx.hstack(
                rx.button(
                    "Test Connection",
                    on_click=SettingsState.test_plex_connection,
                    loading=SettingsState.testing_connection,
                    variant="soft",
                ),
                rx.button(
                    "Save",
                    on_click=SettingsState.save_all_settings,
                    color_scheme="green",
                ),
                spacing="3",
                margin_top="4",
            ),
            rx.cond(
                SettingsState.plex_test_status != "",
                rx.text(SettingsState.plex_test_status, size="2", margin_top="3"),
                rx.box(),
            ),
            spacing="2",
            width="100%",
        ),
        spacing="4",
        width="100%",
    )


def ai_provider_tab() -> rx.Component:
    return rx.vstack(
        rx.heading("AI Provider", size="6", margin_bottom="4"),
        rx.vstack(
            rx.text("Provider", size="3", weight="bold"),
            rx.select.root(
                rx.select.trigger(placeholder="Select provider"),
                rx.select.content(
                    rx.select.item("Google", value="gemini"),
                    rx.select.item("OpenAI", value="openai"),
                    rx.select.item("Anthropic", value="anthropic"),
                    rx.select.item("Cohere", value="cohere"),
                    rx.select.item("Local (Offline)", value="local"),
                ),
                value=SettingsState.ai_provider,
                on_change=SettingsState.set_ai_provider,
                width="100%",
            ),
            rx.cond(
                SettingsState.ai_provider != "local",
                rx.vstack(
                    rx.text("API Key", size="3", weight="bold", margin_top="3"),
                    rx.input(
                        type="password",
                        placeholder="Enter API key",
                        value=SettingsState.ai_api_key,
                        on_change=SettingsState.set_ai_api_key,
                        width="100%",
                    ),
                    spacing="2",
                    width="100%",
                ),
                rx.box(),
            ),
            rx.text("Model", size="3", weight="bold", margin_top="3"),
            rx.cond(
                SettingsState.ai_provider == "local",
                rx.select.root(
                    rx.select.trigger(
                        placeholder="Select local model",
                        # Keep the trigger compact and prevent overflow
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
                        # Make the dropdown menu scroll when long, and keep width in bounds
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
            rx.text("Temperature", size="3", weight="bold", margin_top="3"),
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
            rx.cond(
                SettingsState.ai_provider == "local",
                rx.vstack(
                    rx.text("Local Execution Mode", size="3", weight="bold", margin_top="3"),
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
                            rx.text("Endpoint URL", size="3", weight="bold"),
                            rx.input(
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
                            rx.text("Endpoint Token", size="3", weight="bold", margin_top="2"),
                            rx.input(
                                type="password",
                                placeholder="Optional bearer token",
                                value=SettingsState.ai_local_auth_token,
                                on_change=SettingsState.set_ai_local_auth_token,
                                width="100%",
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
            rx.hstack(
                rx.button(
                    "Test Provider",
                    on_click=SettingsState.test_ai_provider,
                    loading=SettingsState.testing_connection,
                    variant="soft",
                ),
                rx.button(
                    "Save",
                    on_click=SettingsState.save_all_settings,
                    color_scheme="green",
                ),
                spacing="3",
                margin_top="4",
            ),
            rx.cond(
                SettingsState.ai_test_status != "",
                rx.text(SettingsState.ai_test_status, size="2", margin_top="3"),
                rx.box(),
            ),
            spacing="2",
            width="100%",
        ),
        spacing="4",
        width="100%",
    )


def embedding_tab() -> rx.Component:
    return rx.vstack(
        rx.heading("Embedding Provider", size="6", margin_bottom="4"),
        rx.vstack(
            rx.text("Provider", size="3", weight="bold"),
            rx.select(
                ["gemini", "openai", "cohere", "local"],
                value=SettingsState.embedding_provider,
                on_change=SettingsState.set_embedding_provider,
                width="100%",
            ),
            rx.cond(
                SettingsState.embedding_provider != "local",
                rx.vstack(
                    rx.text("API Key", size="3", weight="bold"),
                    rx.input(
                        type="password",
                        placeholder="Enter API key",
                        value=SettingsState.embedding_api_key,
                        on_change=SettingsState.set_embedding_api_key,
                        width="100%",
                    ),
                    spacing="2",
                    width="100%",
                ),
                rx.box(),
            ),
            rx.text("Model", size="3", weight="bold", margin_top="3"),
            rx.select(
                SettingsState.embedding_models,
                value=SettingsState.embedding_model,
                on_change=SettingsState.set_embedding_model,
                placeholder="Select model...",
                width="100%",
            ),
            rx.text(f"Embedding Dimension: {SettingsState.embedding_dimension}", size="2", color_scheme="gray", margin_top="2"),
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
            rx.hstack(
                rx.button(
                    "Test Embeddings",
                    on_click=SettingsState.test_embedding_provider,
                    loading=SettingsState.testing_connection,
                    variant="soft",
                ),
                rx.button(
                    "Save",
                    on_click=SettingsState.save_all_settings,
                    color_scheme="green",
                ),
                spacing="3",
                margin_top="4",
            ),
            rx.cond(
                SettingsState.embedding_test_status != "",
                rx.text(SettingsState.embedding_test_status, size="2", margin_top="3"),
                rx.box(),
            ),
            spacing="2",
            width="100%",
        ),
        spacing="4",
        width="100%",
    )


def advanced_tab() -> rx.Component:
    return rx.vstack(
        rx.heading("Advanced Settings", size="6", margin_bottom="4"),
        rx.vstack(
            rx.text("Database Path", size="3", weight="bold"),
            rx.text(SettingsState.db_path, size="2", color_scheme="gray"),
            rx.text("FAISS Index Path", size="3", weight="bold", margin_top="3"),
            rx.text(SettingsState.faiss_index_path, size="2", color_scheme="gray"),
            rx.text("Logging Level", size="3", weight="bold", margin_top="3"),
            rx.select(
                ["DEBUG", "INFO", "WARNING", "ERROR"],
                value=SettingsState.log_level,
                on_change=SettingsState.set_log_level,
                width="100%",
            ),
            rx.button(
                "Save",
                on_click=SettingsState.save_all_settings,
                color_scheme="green",
                margin_top="4",
            ),
            rx.cond(
                SettingsState.save_status != "",
                rx.text(SettingsState.save_status, size="2", margin_top="3"),
                rx.box(),
            ),
            spacing="2",
            width="100%",
        ),
        spacing="4",
        width="100%",
    )


def settings() -> rx.Component:
    content = rx.container(
        rx.vstack(
            rx.heading("Settings", size="8", margin_bottom="6"),
            rx.tabs.root(
                rx.tabs.list(
                    rx.tabs.trigger("Plex", value="plex"),
                    rx.tabs.trigger("AI Provider", value="ai"),
                    rx.tabs.trigger("Embeddings", value="embedding"),
                    rx.tabs.trigger("Advanced", value="advanced"),
                ),
                rx.tabs.content(plex_tab(), value="plex"),
                rx.tabs.content(ai_provider_tab(), value="ai"),
                rx.tabs.content(embedding_tab(), value="embedding"),
                rx.tabs.content(advanced_tab(), value="advanced"),
                default_value="plex",
                width="100%",
            ),
            spacing="4",
            width="100%",
        ),
        size="4",
    )
    return layout(content)
