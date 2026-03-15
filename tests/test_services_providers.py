"""Tests for the provider service layer at src/plexmix/services/providers.py."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from plexmix.config.settings import Settings
from plexmix.services.providers import (
    build_ai_provider,
    build_embedding_generator,
    canonical_ai_provider,
    local_provider_kwargs,
    resolve_ai_api_key,
    resolve_embedding_api_key,
)


# ---------------------------------------------------------------------------
# Helper: create a minimal YAML config for Settings
# ---------------------------------------------------------------------------


def _make_settings_yaml(tmp_path, extra=""):
    """Create a minimal config YAML with required database fields."""
    config_file = tmp_path / "config.yaml"
    base = (
        "database:\n"
        f"  path: {tmp_path / 'test.db'}\n"
        f"  faiss_index_path: {tmp_path / 'test.index'}\n"
    )
    config_file.write_text(base + extra)
    return str(config_file)


# ===========================================================================
# canonical_ai_provider
# ===========================================================================


class TestCanonicalAiProvider:
    """Tests for canonical_ai_provider()."""

    def test_none_returns_gemini(self):
        assert canonical_ai_provider(None) == "gemini"

    def test_empty_string_returns_gemini(self):
        assert canonical_ai_provider("") == "gemini"

    def test_anthropic_maps_to_claude(self):
        assert canonical_ai_provider("anthropic") == "claude"

    def test_anthropic_uppercase(self):
        assert canonical_ai_provider("ANTHROPIC") == "claude"

    def test_anthropic_mixed_case(self):
        assert canonical_ai_provider("Anthropic") == "claude"

    def test_gemini_passthrough(self):
        assert canonical_ai_provider("gemini") == "gemini"

    def test_openai_passthrough(self):
        assert canonical_ai_provider("openai") == "openai"

    def test_claude_passthrough(self):
        assert canonical_ai_provider("claude") == "claude"

    def test_cohere_passthrough(self):
        assert canonical_ai_provider("cohere") == "cohere"

    def test_custom_passthrough(self):
        assert canonical_ai_provider("custom") == "custom"

    def test_local_passthrough(self):
        assert canonical_ai_provider("local") == "local"

    def test_unknown_provider_passthrough(self):
        assert canonical_ai_provider("some_new_provider") == "some_new_provider"

    def test_uppercase_lowered(self):
        assert canonical_ai_provider("OPENAI") == "openai"

    def test_mixed_case_lowered(self):
        assert canonical_ai_provider("Gemini") == "gemini"


# ===========================================================================
# resolve_ai_api_key
# ===========================================================================


class TestResolveAiApiKey:
    """Tests for resolve_ai_api_key()."""

    @patch("plexmix.services.providers.credentials")
    def test_gemini_delegates_to_google_key(self, mock_creds):
        mock_creds.get_google_api_key.return_value = "gkey"
        assert resolve_ai_api_key("gemini") == "gkey"
        mock_creds.get_google_api_key.assert_called_once()

    @patch("plexmix.services.providers.credentials")
    def test_openai_delegates(self, mock_creds):
        mock_creds.get_openai_api_key.return_value = "okey"
        assert resolve_ai_api_key("openai") == "okey"
        mock_creds.get_openai_api_key.assert_called_once()

    @patch("plexmix.services.providers.credentials")
    def test_claude_delegates_to_anthropic_key(self, mock_creds):
        mock_creds.get_anthropic_api_key.return_value = "akey"
        assert resolve_ai_api_key("claude") == "akey"
        mock_creds.get_anthropic_api_key.assert_called_once()

    @patch("plexmix.services.providers.credentials")
    def test_anthropic_name_maps_to_claude_getter(self, mock_creds):
        """'anthropic' is canonicalized to 'claude' before lookup."""
        mock_creds.get_anthropic_api_key.return_value = "akey2"
        assert resolve_ai_api_key("anthropic") == "akey2"
        mock_creds.get_anthropic_api_key.assert_called_once()

    @patch("plexmix.services.providers.credentials")
    def test_cohere_delegates(self, mock_creds):
        mock_creds.get_cohere_api_key.return_value = "ckey"
        assert resolve_ai_api_key("cohere") == "ckey"
        mock_creds.get_cohere_api_key.assert_called_once()

    @patch("plexmix.services.providers.credentials")
    def test_custom_delegates(self, mock_creds):
        mock_creds.get_custom_ai_api_key.return_value = "customkey"
        assert resolve_ai_api_key("custom") == "customkey"
        mock_creds.get_custom_ai_api_key.assert_called_once()

    @patch("plexmix.services.providers.credentials")
    def test_unknown_provider_returns_none(self, mock_creds):
        assert resolve_ai_api_key("unknown_provider") is None

    @patch("plexmix.services.providers.credentials")
    def test_none_provider_defaults_to_gemini(self, mock_creds):
        """None input is canonicalized to 'gemini'."""
        mock_creds.get_google_api_key.return_value = "gkey_default"
        assert resolve_ai_api_key(None) == "gkey_default"
        mock_creds.get_google_api_key.assert_called_once()

    @patch("plexmix.services.providers.credentials")
    def test_empty_string_defaults_to_gemini(self, mock_creds):
        mock_creds.get_google_api_key.return_value = "gkey_empty"
        assert resolve_ai_api_key("") == "gkey_empty"

    @patch("plexmix.services.providers.credentials")
    def test_local_returns_none(self, mock_creds):
        """Local provider has no credential getter, so returns None."""
        assert resolve_ai_api_key("local") is None

    @patch("plexmix.services.providers.credentials")
    def test_getter_returns_none_propagated(self, mock_creds):
        """If the credential getter itself returns None, that is propagated."""
        mock_creds.get_google_api_key.return_value = None
        assert resolve_ai_api_key("gemini") is None


# ===========================================================================
# resolve_embedding_api_key
# ===========================================================================


class TestResolveEmbeddingApiKey:
    """Tests for resolve_embedding_api_key()."""

    @patch("plexmix.services.providers.credentials")
    def test_gemini_delegates(self, mock_creds):
        mock_creds.get_google_api_key.return_value = "gem_emb_key"
        assert resolve_embedding_api_key("gemini") == "gem_emb_key"

    @patch("plexmix.services.providers.credentials")
    def test_openai_delegates(self, mock_creds):
        mock_creds.get_openai_api_key.return_value = "oai_emb_key"
        assert resolve_embedding_api_key("openai") == "oai_emb_key"

    @patch("plexmix.services.providers.credentials")
    def test_cohere_delegates(self, mock_creds):
        mock_creds.get_cohere_api_key.return_value = "co_emb_key"
        assert resolve_embedding_api_key("cohere") == "co_emb_key"

    @patch("plexmix.services.providers.credentials")
    def test_custom_delegates(self, mock_creds):
        mock_creds.get_custom_embedding_api_key.return_value = "cust_emb_key"
        assert resolve_embedding_api_key("custom") == "cust_emb_key"

    @patch("plexmix.services.providers.credentials")
    def test_local_returns_none(self, mock_creds):
        """Local embeddings don't need an API key."""
        assert resolve_embedding_api_key("local") is None

    @patch("plexmix.services.providers.credentials")
    def test_unknown_returns_none(self, mock_creds):
        assert resolve_embedding_api_key("nonexistent") is None

    @patch("plexmix.services.providers.credentials")
    def test_no_claude_key_for_embeddings(self, mock_creds):
        """Claude is not a valid embedding provider, so returns None."""
        assert resolve_embedding_api_key("claude") is None


# ===========================================================================
# local_provider_kwargs
# ===========================================================================


class TestLocalProviderKwargs:
    """Tests for local_provider_kwargs()."""

    def test_extracts_all_fields(self, tmp_path):
        cfg = _make_settings_yaml(
            tmp_path,
            "ai:\n"
            "  local_mode: endpoint\n"
            "  local_endpoint: http://localhost:8080\n"
            "  local_auth_token: tok123\n"
            "  local_max_output_tokens: 500\n",
        )
        settings = Settings.load_from_file(cfg)
        result = local_provider_kwargs(settings)
        assert result == {
            "local_mode": "endpoint",
            "local_endpoint": "http://localhost:8080",
            "local_auth_token": "tok123",
            "local_max_output_tokens": 500,
        }

    def test_defaults_when_not_configured(self, tmp_path):
        cfg = _make_settings_yaml(tmp_path)
        settings = Settings.load_from_file(cfg)
        result = local_provider_kwargs(settings)
        assert result["local_mode"] == "builtin"
        assert result["local_endpoint"] is None
        assert result["local_auth_token"] is None
        assert result["local_max_output_tokens"] == 800

    def test_partial_override(self, tmp_path):
        cfg = _make_settings_yaml(
            tmp_path,
            "ai:\n  local_max_output_tokens: 1024\n",
        )
        settings = Settings.load_from_file(cfg)
        result = local_provider_kwargs(settings)
        assert result["local_max_output_tokens"] == 1024
        assert result["local_mode"] == "builtin"


# ===========================================================================
# build_ai_provider
# ===========================================================================


class TestBuildAiProvider:
    """Tests for build_ai_provider()."""

    @patch("plexmix.services.providers.get_ai_provider")
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value="testkey")
    def test_success_returns_provider(self, mock_resolve, mock_get, tmp_path):
        mock_provider = MagicMock()
        mock_get.return_value = mock_provider
        settings = Settings.load_from_file(_make_settings_yaml(tmp_path))
        result = build_ai_provider(settings)
        assert result is mock_provider
        mock_get.assert_called_once()

    @patch("plexmix.services.providers.get_ai_provider", side_effect=ValueError("bad config"))
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value=None)
    def test_value_error_returns_none(self, mock_resolve, mock_get, tmp_path):
        settings = Settings.load_from_file(_make_settings_yaml(tmp_path))
        result = build_ai_provider(settings)
        assert result is None

    @patch("plexmix.services.providers.get_ai_provider", side_effect=ValueError("bad"))
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value=None)
    def test_silent_suppresses_warning_log(self, mock_resolve, mock_get, tmp_path, caplog):
        settings = Settings.load_from_file(_make_settings_yaml(tmp_path))
        with caplog.at_level(logging.WARNING, logger="plexmix.services.providers"):
            result = build_ai_provider(settings, silent=True)
        assert result is None
        assert "AI provider init failed" not in caplog.text

    @patch("plexmix.services.providers.get_ai_provider", side_effect=ValueError("bad"))
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value=None)
    def test_non_silent_logs_warning(self, mock_resolve, mock_get, tmp_path, caplog):
        settings = Settings.load_from_file(_make_settings_yaml(tmp_path))
        with caplog.at_level(logging.WARNING, logger="plexmix.services.providers"):
            result = build_ai_provider(settings, silent=False)
        assert result is None
        assert "AI provider init failed" in caplog.text

    @patch("plexmix.services.providers.get_ai_provider")
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value="resolved_key")
    def test_api_key_override_takes_precedence(self, mock_resolve, mock_get, tmp_path):
        mock_get.return_value = MagicMock()
        settings = Settings.load_from_file(_make_settings_yaml(tmp_path))
        build_ai_provider(settings, api_key_override="override_key")
        call_kwargs = mock_get.call_args
        assert call_kwargs.kwargs.get("api_key") == "override_key" or (
            call_kwargs[1].get("api_key") == "override_key"
            if len(call_kwargs) > 1
            else call_kwargs[0][1] == "override_key"
        )

    @patch("plexmix.services.providers.get_ai_provider")
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value="resolved_key")
    def test_uses_resolved_key_when_no_override(self, mock_resolve, mock_get, tmp_path):
        mock_get.return_value = MagicMock()
        settings = Settings.load_from_file(_make_settings_yaml(tmp_path))
        build_ai_provider(settings)
        _, kwargs = mock_get.call_args
        assert kwargs["api_key"] == "resolved_key"

    @patch("plexmix.services.providers.get_ai_provider")
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value="k")
    def test_provider_name_param_overrides_settings(self, mock_resolve, mock_get, tmp_path):
        mock_get.return_value = MagicMock()
        settings = Settings.load_from_file(_make_settings_yaml(tmp_path))
        build_ai_provider(settings, provider_name="openai")
        _, kwargs = mock_get.call_args
        assert kwargs["provider_name"] == "openai"

    @patch("plexmix.services.providers.get_ai_provider")
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value="k")
    def test_defaults_to_settings_provider(self, mock_resolve, mock_get, tmp_path):
        mock_get.return_value = MagicMock()
        cfg = _make_settings_yaml(tmp_path, "ai:\n  default_provider: cohere\n")
        settings = Settings.load_from_file(cfg)
        build_ai_provider(settings)
        _, kwargs = mock_get.call_args
        assert kwargs["provider_name"] == "cohere"

    @patch("plexmix.services.providers.get_ai_provider")
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value="k")
    def test_falls_back_to_gemini_when_no_provider_set(self, mock_resolve, mock_get, tmp_path):
        mock_get.return_value = MagicMock()
        settings = Settings.load_from_file(_make_settings_yaml(tmp_path))
        # default_provider defaults to "gemini" in AISettings
        build_ai_provider(settings)
        _, kwargs = mock_get.call_args
        assert kwargs["provider_name"] == "gemini"

    @patch("plexmix.services.providers.get_ai_provider")
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value="k")
    def test_custom_provider_uses_custom_model(self, mock_resolve, mock_get, tmp_path):
        mock_get.return_value = MagicMock()
        cfg = _make_settings_yaml(
            tmp_path,
            "ai:\n"
            "  default_provider: custom\n"
            "  custom_model: my-llm\n"
            "  custom_endpoint: http://myhost:5000/v1\n",
        )
        settings = Settings.load_from_file(cfg)
        build_ai_provider(settings)
        _, kwargs = mock_get.call_args
        assert kwargs["model"] == "my-llm"

    @patch("plexmix.services.providers.get_ai_provider")
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value="k")
    def test_passes_temperature(self, mock_resolve, mock_get, tmp_path):
        mock_get.return_value = MagicMock()
        cfg = _make_settings_yaml(tmp_path, "ai:\n  temperature: 0.2\n")
        settings = Settings.load_from_file(cfg)
        build_ai_provider(settings)
        _, kwargs = mock_get.call_args
        assert kwargs["temperature"] == pytest.approx(0.2)

    @patch("plexmix.services.providers.get_ai_provider")
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value="k")
    def test_passes_local_kwargs(self, mock_resolve, mock_get, tmp_path):
        mock_get.return_value = MagicMock()
        cfg = _make_settings_yaml(
            tmp_path,
            "ai:\n  local_mode: endpoint\n  local_endpoint: http://localhost:9090\n",
        )
        settings = Settings.load_from_file(cfg)
        build_ai_provider(settings)
        _, kwargs = mock_get.call_args
        assert kwargs["local_mode"] == "endpoint"
        assert kwargs["local_endpoint"] == "http://localhost:9090"

    @patch("plexmix.services.providers.get_ai_provider")
    @patch("plexmix.services.providers.resolve_ai_api_key", return_value="k")
    def test_passes_custom_endpoint(self, mock_resolve, mock_get, tmp_path):
        mock_get.return_value = MagicMock()
        cfg = _make_settings_yaml(
            tmp_path,
            "ai:\n  custom_endpoint: http://custom:1234/v1\n",
        )
        settings = Settings.load_from_file(cfg)
        build_ai_provider(settings)
        _, kwargs = mock_get.call_args
        assert kwargs["custom_endpoint"] == "http://custom:1234/v1"


# ===========================================================================
# build_embedding_generator
# ===========================================================================


class TestBuildEmbeddingGenerator:
    """Tests for build_embedding_generator()."""

    @patch("plexmix.services.providers.resolve_embedding_api_key", return_value="ekey")
    @patch("plexmix.services.providers.EmbeddingGenerator")
    def test_returns_generator_when_key_present(self, mock_gen_cls, mock_resolve, tmp_path):
        mock_instance = MagicMock()
        mock_gen_cls.return_value = mock_instance
        cfg = _make_settings_yaml(tmp_path, "embedding:\n  default_provider: gemini\n")
        settings = Settings.load_from_file(cfg)
        result = build_embedding_generator(settings)
        assert result is mock_instance
        mock_gen_cls.assert_called_once()

    @patch("plexmix.services.providers.resolve_embedding_api_key", return_value=None)
    def test_returns_none_when_gemini_key_missing(self, mock_resolve, tmp_path):
        cfg = _make_settings_yaml(tmp_path, "embedding:\n  default_provider: gemini\n")
        settings = Settings.load_from_file(cfg)
        result = build_embedding_generator(settings)
        assert result is None

    @patch("plexmix.services.providers.resolve_embedding_api_key", return_value=None)
    def test_returns_none_when_openai_key_missing(self, mock_resolve, tmp_path):
        cfg = _make_settings_yaml(tmp_path, "embedding:\n  default_provider: openai\n")
        settings = Settings.load_from_file(cfg)
        result = build_embedding_generator(settings)
        assert result is None

    @patch("plexmix.services.providers.resolve_embedding_api_key", return_value=None)
    def test_returns_none_when_cohere_key_missing(self, mock_resolve, tmp_path):
        cfg = _make_settings_yaml(tmp_path, "embedding:\n  default_provider: cohere\n")
        settings = Settings.load_from_file(cfg)
        result = build_embedding_generator(settings)
        assert result is None

    @patch("plexmix.services.providers.resolve_embedding_api_key", return_value=None)
    @patch("plexmix.services.providers.EmbeddingGenerator")
    def test_local_does_not_require_key(self, mock_gen_cls, mock_resolve, tmp_path):
        """Local embedding provider works without an API key."""
        mock_gen_cls.return_value = MagicMock()
        cfg = _make_settings_yaml(tmp_path, "embedding:\n  default_provider: local\n")
        settings = Settings.load_from_file(cfg)
        result = build_embedding_generator(settings)
        assert result is not None
        mock_gen_cls.assert_called_once()
        _, kwargs = mock_gen_cls.call_args
        assert kwargs["provider"] == "local"
        assert kwargs["api_key"] is None

    @patch("plexmix.services.providers.resolve_embedding_api_key", return_value="ekey")
    @patch("plexmix.services.providers.EmbeddingGenerator")
    def test_passes_model_from_settings(self, mock_gen_cls, mock_resolve, tmp_path):
        mock_gen_cls.return_value = MagicMock()
        cfg = _make_settings_yaml(
            tmp_path,
            "embedding:\n" "  default_provider: openai\n" "  model: text-embedding-3-large\n",
        )
        settings = Settings.load_from_file(cfg)
        build_embedding_generator(settings)
        _, kwargs = mock_gen_cls.call_args
        assert kwargs["model"] == "text-embedding-3-large"

    @patch("plexmix.services.providers.credentials")
    @patch("plexmix.services.providers.resolve_embedding_api_key", return_value="ckey")
    @patch("plexmix.services.providers.EmbeddingGenerator")
    def test_custom_provider_passes_custom_fields(
        self, mock_gen_cls, mock_resolve, mock_creds, tmp_path
    ):
        mock_creds.get_custom_embedding_api_key.return_value = "cust_key_from_creds"
        mock_gen_cls.return_value = MagicMock()
        cfg = _make_settings_yaml(
            tmp_path,
            "embedding:\n"
            "  default_provider: custom\n"
            "  custom_model: my-embed-model\n"
            "  custom_endpoint: http://embed:8080/v1\n"
            "  custom_dimension: 768\n",
        )
        settings = Settings.load_from_file(cfg)
        build_embedding_generator(settings)
        _, kwargs = mock_gen_cls.call_args
        assert kwargs["provider"] == "custom"
        assert kwargs["model"] == "my-embed-model"
        assert kwargs["custom_endpoint"] == "http://embed:8080/v1"
        assert kwargs["custom_dimension"] == 768

    @patch("plexmix.services.providers.credentials")
    @patch("plexmix.services.providers.resolve_embedding_api_key", return_value="ckey")
    @patch("plexmix.services.providers.EmbeddingGenerator")
    def test_custom_provider_falls_back_to_base_model(
        self, mock_gen_cls, mock_resolve, mock_creds, tmp_path
    ):
        """When custom_model is not set, the base model from settings is used."""
        mock_creds.get_custom_embedding_api_key.return_value = "k"
        mock_gen_cls.return_value = MagicMock()
        cfg = _make_settings_yaml(
            tmp_path,
            "embedding:\n" "  default_provider: custom\n" "  model: fallback-model\n",
        )
        settings = Settings.load_from_file(cfg)
        build_embedding_generator(settings)
        _, kwargs = mock_gen_cls.call_args
        assert kwargs["model"] == "fallback-model"

    @patch("plexmix.services.providers.credentials")
    @patch("plexmix.services.providers.resolve_embedding_api_key", return_value="ckey")
    @patch("plexmix.services.providers.EmbeddingGenerator")
    def test_custom_provider_uses_custom_api_key_from_settings(
        self, mock_gen_cls, mock_resolve, mock_creds, tmp_path
    ):
        """When custom_api_key is set in settings, it takes priority over credentials."""
        mock_creds.get_custom_embedding_api_key.return_value = "from_creds"
        mock_gen_cls.return_value = MagicMock()
        cfg = _make_settings_yaml(
            tmp_path,
            "embedding:\n" "  default_provider: custom\n" "  custom_api_key: from_settings\n",
        )
        settings = Settings.load_from_file(cfg)
        build_embedding_generator(settings)
        _, kwargs = mock_gen_cls.call_args
        assert kwargs["custom_api_key"] == "from_settings"

    @patch("plexmix.services.providers.credentials")
    @patch("plexmix.services.providers.resolve_embedding_api_key", return_value="ckey")
    @patch("plexmix.services.providers.EmbeddingGenerator")
    def test_custom_provider_falls_back_to_creds_api_key(
        self, mock_gen_cls, mock_resolve, mock_creds, tmp_path
    ):
        """When custom_api_key is not in settings, falls back to credentials store."""
        mock_creds.get_custom_embedding_api_key.return_value = "from_creds"
        mock_gen_cls.return_value = MagicMock()
        cfg = _make_settings_yaml(
            tmp_path,
            "embedding:\n  default_provider: custom\n",
        )
        settings = Settings.load_from_file(cfg)
        build_embedding_generator(settings)
        _, kwargs = mock_gen_cls.call_args
        assert kwargs["custom_api_key"] == "from_creds"

    @patch("plexmix.services.providers.resolve_embedding_api_key", return_value="ekey")
    @patch("plexmix.services.providers.EmbeddingGenerator")
    def test_non_custom_provider_omits_custom_kwargs(self, mock_gen_cls, mock_resolve, tmp_path):
        """For non-custom providers, custom_endpoint/custom_api_key/custom_dimension are not passed."""
        mock_gen_cls.return_value = MagicMock()
        cfg = _make_settings_yaml(tmp_path, "embedding:\n  default_provider: gemini\n")
        settings = Settings.load_from_file(cfg)
        build_embedding_generator(settings)
        _, kwargs = mock_gen_cls.call_args
        assert "custom_endpoint" not in kwargs
        assert "custom_api_key" not in kwargs
        assert "custom_dimension" not in kwargs
