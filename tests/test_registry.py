"""Tests for the provider registry (services/registry.py)."""

from plexmix.services.registry import (
    AI_PROVIDERS,
    AI_PROVIDERS_REQUIRING_KEY,
    EMBEDDING_PROVIDERS,
    EMBEDDING_PROVIDERS_REQUIRING_KEY,
    get_ai_models,
    get_ai_models_display,
    get_default_ai_model,
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
        assert "gpt-5-mini" in models

    def test_claude(self):
        models = get_ai_models("claude")
        assert "claude-sonnet-4-5-20250929" in models

    def test_cohere(self):
        models = get_ai_models("cohere")
        assert "command-r7b-12-2024" in models

    def test_local_returns_models(self):
        models = get_ai_models("local")
        assert len(models) > 0
        assert "google/gemma-3-1b" in models

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
        assert get_default_ai_model("gemini") == "gemini-2.5-flash"

    def test_openai(self):
        assert get_default_ai_model("openai") == "gpt-5-mini"

    def test_claude(self):
        assert get_default_ai_model("claude") == "claude-sonnet-4-5-20250929"

    def test_cohere(self):
        assert get_default_ai_model("cohere") == "command-r7b-12-2024"

    def test_local(self):
        assert get_default_ai_model("local") == "google/gemma-3-1b"

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
