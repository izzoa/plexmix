"""Tests for the provider registry (services/registry.py)."""

import pytest

from plexmix.services.registry import (
    AI_PROVIDERS,
    AI_PROVIDERS_REQUIRING_KEY,
    EMBEDDING_PROVIDERS,
    EMBEDDING_PROVIDERS_REQUIRING_KEY,
    ai_provider_display_name,
    embedding_provider_display_name,
    get_ai_models,
    get_ai_models_display,
    get_default_ai_model,
    get_default_ai_model_display,
    get_embedding_models,
    get_default_embedding_model,
    get_embedding_dimension,
    requires_ai_api_key,
    requires_embedding_api_key,
)


# ---------------------------------------------------------------------------
# Static registry data
# ---------------------------------------------------------------------------


class TestAIProvidersRegistry:
    def test_all_cloud_providers_present(self):
        assert set(AI_PROVIDERS.keys()) == {"gemini", "openai", "claude", "cohere", "custom"}

    def test_claude_has_anthropic_alias(self):
        assert "anthropic" in AI_PROVIDERS["claude"].aliases

    def test_cloud_providers_require_key(self):
        assert AI_PROVIDERS_REQUIRING_KEY == {"gemini", "openai", "claude", "cohere"}

    def test_custom_does_not_require_key(self):
        assert not AI_PROVIDERS["custom"].requires_api_key

    def test_each_provider_has_default_model(self):
        for pid, p in AI_PROVIDERS.items():
            if pid != "custom":
                assert p.default_model, f"{pid} missing default_model"

    def test_each_provider_has_models(self):
        for pid, p in AI_PROVIDERS.items():
            if pid != "custom":
                assert len(p.models) > 0, f"{pid} has no models"

    def test_default_model_in_models_list(self):
        for pid, p in AI_PROVIDERS.items():
            if pid != "custom":
                assert p.default_model in p.models, f"{pid} default not in models"


class TestEmbeddingProvidersRegistry:
    def test_all_embedding_providers_present(self):
        assert set(EMBEDDING_PROVIDERS.keys()) == {"gemini", "openai", "cohere", "custom"}

    def test_embedding_providers_require_key(self):
        assert EMBEDDING_PROVIDERS_REQUIRING_KEY == {"gemini", "openai", "cohere"}

    def test_each_has_default_dimension(self):
        for pid, p in EMBEDDING_PROVIDERS.items():
            assert p.default_dimension > 0, f"{pid} has no dimension"

    def test_known_dimensions(self):
        assert EMBEDDING_PROVIDERS["gemini"].default_dimension == 3072
        assert EMBEDDING_PROVIDERS["openai"].default_dimension == 1536
        assert EMBEDDING_PROVIDERS["cohere"].default_dimension == 1024


# ---------------------------------------------------------------------------
# Query helpers — AI
# ---------------------------------------------------------------------------


class TestGetAIModels:
    def test_gemini_sorted(self):
        models = get_ai_models("gemini")
        assert models == sorted(models, key=str.lower)
        assert "gemini-2.5-flash" in models

    def test_openai(self):
        models = get_ai_models("openai")
        assert "gpt-5.4-mini" in models

    def test_claude(self):
        models = get_ai_models("claude")
        assert "claude-sonnet-4-6" in models

    def test_cohere(self):
        models = get_ai_models("cohere")
        assert "command-r7b-12-2024" in models

    def test_local_returns_models(self):
        models = get_ai_models("local")
        assert len(models) > 0
        assert "google/gemma-3-1b-it" in models

    def test_custom_empty(self):
        assert get_ai_models("custom") == []

    def test_unknown_empty(self):
        assert get_ai_models("doesnotexist") == []


class TestGetAIModelsDisplay:
    def test_anthropic_alias(self):
        models = get_ai_models_display("anthropic")
        # Should return short display names (no date suffix)
        assert any("claude-sonnet" in m for m in models)
        assert all(not m[-8:].isdigit() for m in models)

    def test_gemini_same_as_regular(self):
        assert get_ai_models_display("gemini") == get_ai_models("gemini")


class TestGetDefaultAIModel:
    def test_gemini(self):
        assert get_default_ai_model("gemini") == "gemini-3.5-flash"

    def test_openai(self):
        assert get_default_ai_model("openai") == "gpt-5.4-mini"

    def test_claude(self):
        assert get_default_ai_model("claude") == "claude-sonnet-4-6"

    def test_cohere(self):
        assert get_default_ai_model("cohere") == "command-a-03-2025"

    def test_local(self):
        assert get_default_ai_model("local") == "google/gemma-3-1b-it"

    def test_unknown_empty(self):
        assert get_default_ai_model("xyz") == ""


# ---------------------------------------------------------------------------
# Query helpers — Embedding
# ---------------------------------------------------------------------------


class TestGetEmbeddingModels:
    def test_gemini(self):
        models = get_embedding_models("gemini")
        assert "gemini-embedding-001" in models

    def test_openai(self):
        models = get_embedding_models("openai")
        assert "text-embedding-3-small" in models

    def test_cohere(self):
        models = get_embedding_models("cohere")
        assert "embed-v4.0" in models

    def test_local(self):
        models = get_embedding_models("local")
        assert "all-MiniLM-L6-v2" in models

    def test_custom_empty(self):
        assert get_embedding_models("custom") == []


class TestGetDefaultEmbeddingModel:
    def test_gemini(self):
        assert get_default_embedding_model("gemini") == "gemini-embedding-001"

    def test_openai(self):
        assert get_default_embedding_model("openai") == "text-embedding-3-small"

    def test_local(self):
        assert get_default_embedding_model("local") == "all-MiniLM-L6-v2"

    def test_unknown_empty(self):
        assert get_default_embedding_model("xyz") == ""


# ---------------------------------------------------------------------------
# Embedding dimensions
# ---------------------------------------------------------------------------


class TestGetEmbeddingDimension:
    def test_gemini(self):
        assert get_embedding_dimension("gemini") == 3072

    def test_openai(self):
        assert get_embedding_dimension("openai") == 1536

    def test_openai_large_model_is_3072(self):
        # A specific model overrides the provider default; large is 3072, not 1536.
        assert get_embedding_dimension("openai", model="text-embedding-3-large") == 3072

    def test_openai_small_model_is_1536(self):
        assert get_embedding_dimension("openai", model="text-embedding-3-small") == 1536

    def test_cohere(self):
        assert get_embedding_dimension("cohere") == 1024

    def test_custom_default(self):
        assert get_embedding_dimension("custom") == 1536

    def test_custom_override(self):
        assert get_embedding_dimension("custom", custom_dimension=768) == 768

    def test_local_known_model(self):
        assert get_embedding_dimension("local", model="all-MiniLM-L6-v2") == 384

    def test_local_known_mxbai(self):
        assert get_embedding_dimension("local", model="mixedbread-ai/mxbai-embed-large-v1") == 1024

    def test_local_unknown_model_default(self):
        assert get_embedding_dimension("local", model="some-unknown") == 384

    def test_local_unknown_model_with_fallback(self):
        assert get_embedding_dimension("local", model="some-unknown", fallback_dimension=512) == 512

    def test_unknown_provider_default(self):
        assert get_embedding_dimension("doesnotexist") == 768

    def test_unknown_provider_with_fallback(self):
        assert get_embedding_dimension("doesnotexist", fallback_dimension=999) == 999


# ---------------------------------------------------------------------------
# Boolean helpers
# ---------------------------------------------------------------------------


class TestRequiresKey:
    def test_ai_cloud_providers(self):
        for p in ("gemini", "openai", "claude", "cohere"):
            assert requires_ai_api_key(p), f"{p} should require key"

    def test_ai_local_custom_no_key(self):
        assert not requires_ai_api_key("local")
        assert not requires_ai_api_key("custom")

    def test_embedding_cloud_providers(self):
        for p in ("gemini", "openai", "cohere"):
            assert requires_embedding_api_key(p), f"{p} should require key"

    def test_embedding_local_custom_no_key(self):
        assert not requires_embedding_api_key("local")
        assert not requires_embedding_api_key("custom")


# ---------------------------------------------------------------------------
# Default model in display form (used by the Settings UI default selection)
# ---------------------------------------------------------------------------


class TestGetDefaultAIModelDisplay:
    def test_gemini(self):
        assert get_default_ai_model_display("gemini") == "gemini-3.5-flash"

    def test_openai(self):
        assert get_default_ai_model_display("openai") == "gpt-5.4-mini"

    def test_claude(self):
        assert get_default_ai_model_display("claude") == "claude-sonnet-4-6"

    def test_anthropic_alias_resolves_to_claude_default(self):
        assert get_default_ai_model_display("anthropic") == "claude-sonnet-4-6"

    def test_default_is_present_in_display_list(self):
        for p in ("gemini", "openai", "claude", "anthropic", "cohere"):
            assert get_default_ai_model_display(p) in get_ai_models_display(p), p


# ---------------------------------------------------------------------------
# Catalog currency — new defaults present, EOL/invalid/deprecated absent
# ---------------------------------------------------------------------------


class TestCatalogCurrency:
    def test_new_default_models(self):
        assert AI_PROVIDERS["gemini"].default_model == "gemini-3.5-flash"
        assert AI_PROVIDERS["openai"].default_model == "gpt-5.4-mini"
        assert AI_PROVIDERS["claude"].default_model == "claude-sonnet-4-6"
        assert AI_PROVIDERS["cohere"].default_model == "command-a-03-2025"

    def test_eol_invalid_and_deprecated_models_absent(self):
        all_models = [m for p in AI_PROVIDERS.values() for m in p.models]
        for dead in (
            "gemini-2.0-flash-001",  # EOL 2026-06-01
            "claude-opus-4-1-20250414",  # invalid ID
            "gpt-5-mini",  # superseded GPT-5.0 tier
            "command-r-plus",  # deprecated undated alias
            "command-r",  # deprecated undated alias
        ):
            assert dead not in all_models, f"{dead} should not be offered"

    def test_embedding_catalog_unchanged(self):
        assert EMBEDDING_PROVIDERS["gemini"].default_model == "gemini-embedding-001"
        assert EMBEDDING_PROVIDERS["gemini"].default_dimension == 3072
        assert EMBEDDING_PROVIDERS["openai"].default_model == "text-embedding-3-small"
        assert EMBEDDING_PROVIDERS["cohere"].default_model == "embed-v4.0"


# ---------------------------------------------------------------------------
# Display names (status surfaces never show "Openai")
# ---------------------------------------------------------------------------


class TestProviderDisplayNames:
    @pytest.mark.parametrize(
        "provider_id,expected",
        [
            ("gemini", "Gemini"),
            ("openai", "OpenAI"),
            ("claude", "Claude"),
            ("anthropic", "Claude"),
            ("cohere", "Cohere"),
            ("custom", "Custom (OpenAI-compatible)"),
            ("local", "Local"),
            ("OpenAI", "OpenAI"),
            (" Anthropic ", "Claude"),
            ("LOCAL", "Local"),
        ],
    )
    def test_ai_display_names(self, provider_id, expected):
        assert ai_provider_display_name(provider_id) == expected

    @pytest.mark.parametrize(
        "provider_id,expected",
        [
            ("gemini", "Gemini"),
            ("openai", "OpenAI"),
            ("cohere", "Cohere"),
            ("custom", "Custom (OpenAI-compatible)"),
            ("local", "Local"),
            ("OPENAI", "OpenAI"),
        ],
    )
    def test_embedding_display_names(self, provider_id, expected):
        assert embedding_provider_display_name(provider_id) == expected

    def test_openai_is_never_title_cased(self):
        assert ai_provider_display_name("openai") == "OpenAI"
        assert embedding_provider_display_name("openai") == "OpenAI"
        assert "Openai" not in (
            ai_provider_display_name("openai"),
            embedding_provider_display_name("openai"),
        )

    def test_display_names_come_from_the_registry(self):
        for pid, entry in AI_PROVIDERS.items():
            assert ai_provider_display_name(pid) == entry.display_name
        for pid, entry in EMBEDDING_PROVIDERS.items():
            assert embedding_provider_display_name(pid) == entry.display_name

    def test_unknown_ids_are_returned_unchanged(self):
        assert ai_provider_display_name("ollama") == "ollama"
        assert ai_provider_display_name("my_Provider") == "my_Provider"
        assert embedding_provider_display_name("voyage") == "voyage"
        # Claude has no embedding provider, so the id is not a known embedding id.
        assert embedding_provider_display_name("claude") == "claude"

    def test_none_and_empty_give_empty_string(self):
        assert ai_provider_display_name(None) == ""
        assert ai_provider_display_name("") == ""
        assert embedding_provider_display_name(None) == ""
        assert embedding_provider_display_name("") == ""
