"""Extracted testing helpers for SettingsState.

These are standalone async functions that accept the SettingsState instance
(``state``) so they can read/write state fields while keeping the main
settings_state module shorter.
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Optional

from plexmix.ai.local_provider import LOCAL_LLM_DEFAULT_MODEL
from plexmix.config.credentials import sanitize_credential_value, validate_api_key

# Moved to plexmix.ui.utils.errors (shared with services and readiness copy);
# re-exported here so existing imports of _settings_testing._friendly_error work.
from plexmix.ui.utils.errors import _friendly_error as _friendly_error

logger = logging.getLogger(__name__)


def _secret(state: Any, field: str) -> str:
    """Server-side secret for a test: the staged draft, else the stored value.

    Secrets are never state vars; ``SettingsState._resolve_secret`` resolves them
    on the server (and returns "" for an unauthorized session). Duck-typed
    stand-ins without a resolver get "" so they never see a secret either.
    """
    resolver = getattr(state, "_resolve_secret", None)
    if not callable(resolver):
        return ""
    try:
        value = resolver(field)
    except Exception as e:  # never leave a test stuck in "Testing..." over a lookup
        logger.error("Could not resolve %s for a connection test: %s", field, type(e).__name__)
        return ""
    return value if isinstance(value, str) else ""


# ── Readiness recording (webui-honest-status-and-first-run D2/D12) ──
# Each test records its real outcome in the process-level readiness store, keyed
# by a fingerprint of the *tested form values* (and a hash of the tested secret),
# so saving exactly those values keeps the verification and any other change
# makes it "not checked" again. Recording never raises and never logs a secret.


def _form_settings(state: Any) -> SimpleNamespace:
    """The tested form values in the shape ``readiness.fingerprint`` reads.

    Mirrors how ``SettingsState.save_all_settings`` writes them to the config,
    so the fingerprint of a tested form equals that of the saved configuration.
    """

    def val(name: str, default: Any = "") -> Any:
        value = getattr(state, name, default)
        return default if value is None else value

    return SimpleNamespace(
        plex=SimpleNamespace(url=val("plex_url"), library_name=val("plex_library")),
        ai=SimpleNamespace(
            default_provider=val("ai_provider"),
            model=val("ai_model"),
            custom_model=val("ai_custom_model") or None,
            custom_endpoint=val("ai_custom_endpoint") or None,
            local_mode=val("ai_local_mode", "builtin"),
            local_endpoint=val("ai_local_endpoint") or None,
        ),
        embedding=SimpleNamespace(
            default_provider=val("embedding_provider"),
            model=val("embedding_model"),
            custom_model=val("embedding_custom_model") or None,
            custom_endpoint=val("embedding_custom_endpoint") or None,
            custom_dimension=val("embedding_custom_dimension", 1536),
        ),
    )


def _form_fingerprint(state: Any, component: str, secret: Optional[str]) -> str:
    """Fingerprint ``component`` from the form values ("" if that fails)."""
    try:
        from plexmix.services.readiness import fingerprint

        # Duck-typed: fingerprint() only reads the attributes _form_settings mirrors.
        form_settings: Any = _form_settings(state)
        return fingerprint(component, form_settings, secret)
    except Exception as e:
        logger.warning("Could not fingerprint the %s form: %s", component, type(e).__name__)
        return ""


def _record(
    component: str,
    level: str,
    cause: Optional[str] = None,
    *,
    message: str = "",
    fingerprint: str = "",
) -> None:
    """Record a test outcome (skipped when the form could not be fingerprinted)."""
    if not fingerprint:
        return
    from plexmix.ui.states.app_state import record_readiness

    record_readiness(component, level, cause, message=message, fingerprint=fingerprint)


def _record_provider_failure(
    component: str, exc: BaseException, fp: str, *, key_required: bool, key: Optional[str]
) -> None:
    from plexmix.ui.states.app_state import provider_failure_cause

    cause, message = provider_failure_cause(exc, key_required=key_required, key=key or "")
    _record(component, "failing", cause, message=message, fingerprint=fp)


async def test_plex_connection_impl(state: Any) -> None:
    """Test the Plex server connection using the configured URL and token."""
    async with state:
        if state.testing_connection:
            return  # another test is running (its button stays enabled; see _button_row)
        state.testing_connection = True
        state.plex_test_status = "Testing..."
        url = state.plex_url
        token = _secret(state, "plex_token")
        library = getattr(state, "plex_library", "") or ""
        # Only a complete configuration can become "ready"; an empty URL, token or
        # library is a local fact readiness derives on its own.
        fp = _form_fingerprint(state, "plex", token) if (url and token and library) else ""

    try:
        from plexapi.server import PlexServer

        def _probe() -> list:
            server = PlexServer(url, token)
            return [s.title for s in server.library.sections() if s.type == "artist"]

        # Run the blocking network calls off the event loop so the whole backend
        # doesn't freeze while the Plex server is contacted.
        loop = asyncio.get_running_loop()
        libraries = await loop.run_in_executor(None, _probe)

        # Ready only when the library being saved is one of the music libraries.
        from plexmix.ui.states.app_state import plex_probe_outcome

        level, cause = plex_probe_outcome(libraries, library)
        _record("plex", level, cause, fingerprint=fp)

        async with state:
            state.plex_libraries = libraries
            state.plex_test_status = f"✓ Connected! Found {len(libraries)} music libraries"
            state.testing_connection = False

    except Exception as e:
        from plexmix.ui.states.app_state import plex_failure_cause

        cause = plex_failure_cause(e)
        message = _friendly_error(e, [token]) if cause == "unknown" else ""
        _record("plex", "failing", cause, message=message, fingerprint=fp)
        async with state:
            state.plex_test_status = f"✗ {_friendly_error(e, [token])}"
            state.testing_connection = False


async def test_ai_provider_impl(state: Any) -> None:
    """Test the configured AI provider with a simple generation request."""
    async with state:
        if state.testing_connection:
            return  # another test is running (its button stays enabled; see _button_row)
        state.testing_connection = True
        state.ai_test_status = "Testing AI provider..."
        # Resolved on the server: a staged replacement wins over the stored value.
        api_key = sanitize_credential_value(_secret(state, "ai_api_key"))
        custom_api_key = _secret(state, "ai_custom_api_key")
        local_auth_token = _secret(state, "ai_local_auth_token")
        ai_id = "claude" if state.ai_provider in ("anthropic", "claude") else state.ai_provider
        tested_secret = {"custom": custom_api_key, "local": local_auth_token}.get(ai_id, api_key)
        ai_fp = _form_fingerprint(state, "ai", tested_secret)
    secrets = [api_key, custom_api_key, local_auth_token]

    try:
        loop = asyncio.get_running_loop()
        # "claude" (the id the CLI setup writes) is an alias for "anthropic"; map it
        # so the real Anthropic test runs instead of falling through to fake success.
        provider = "anthropic" if state.ai_provider == "claude" else state.ai_provider
        if provider in ("gemini", "openai", "anthropic", "cohere"):
            validate_api_key(api_key, provider)

        if provider == "gemini":
            async with state:
                state.ai_test_status = "Testing Gemini API..."

            def test_gemini():
                from google import genai

                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=state.ai_model or "gemini-3.5-flash",
                    contents="Say 'test successful' in exactly two words.",
                )
                return response.text

            await loop.run_in_executor(None, test_gemini)

        elif provider == "openai":
            async with state:
                state.ai_test_status = "Testing OpenAI API..."

            def test_openai():
                from openai import OpenAI

                from plexmix.ai.openai_provider import is_openai_reasoning_model

                client = OpenAI(api_key=api_key)
                model = state.ai_model or "gpt-5.4-mini"
                # GPT-5 reasoning models reject max_tokens/custom temperature;
                # use max_completion_tokens with low effort instead.
                token_kwargs: dict[str, Any] = (
                    {"max_completion_tokens": 64, "reasoning_effort": "low"}
                    if is_openai_reasoning_model(model)
                    else {"max_tokens": 10}
                )
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "Say 'test' in one word."}],
                    **token_kwargs,
                )
                return response.choices[0].message.content

            await loop.run_in_executor(None, test_openai)

        elif provider == "anthropic":
            async with state:
                state.ai_test_status = "Testing Anthropic API..."

            def test_anthropic():
                import anthropic

                client = anthropic.Anthropic(api_key=api_key)
                response = client.messages.create(
                    model=state.ai_model or "claude-sonnet-4-6",
                    max_tokens=10,
                    messages=[{"role": "user", "content": "Say 'test' in one word."}],
                )
                return response.content[0].text

            await loop.run_in_executor(None, test_anthropic)

        elif provider == "cohere":
            async with state:
                state.ai_test_status = "Testing Cohere API..."

            def test_cohere():
                import cohere

                client = cohere.ClientV2(api_key=api_key)
                response = client.chat(
                    model=state.ai_model or "command-a-03-2025",
                    messages=[{"role": "user", "content": "Say 'test' in one word."}],
                    max_tokens=10,
                )
                return response.message.content[0].text

            await loop.run_in_executor(None, test_cohere)

        elif provider == "custom":
            async with state:
                state.ai_test_status = "Testing custom endpoint..."

            def test_custom():
                from openai import OpenAI

                endpoint = state.ai_custom_endpoint
                if not endpoint:
                    raise ValueError("No endpoint URL configured")
                model = state.ai_custom_model
                if not model:
                    raise ValueError("No model name configured")
                client = OpenAI(
                    base_url=endpoint,
                    api_key=custom_api_key or "no-key-required",
                )
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "Say 'test' in one word."}],
                    max_tokens=10,
                )
                return response.choices[0].message.content

            await loop.run_in_executor(None, test_custom)

        elif provider == "local":
            if state.ai_local_mode == "builtin":
                async with state:
                    state.ai_test_status = "Checking local model availability..."

                def check_local_model():
                    from huggingface_hub import try_to_load_from_cache

                    model_name = state.ai_model or LOCAL_LLM_DEFAULT_MODEL
                    # Check if model config exists in cache
                    cached = try_to_load_from_cache(model_name, "config.json")
                    if cached is None:
                        raise ValueError(
                            f"Model {model_name} not cached. Click 'Download Model' first."
                        )
                    return True

                await loop.run_in_executor(None, check_local_model)

            elif state.ai_local_mode == "endpoint":
                async with state:
                    state.ai_test_status = "Testing local endpoint..."

                def test_endpoint():
                    import requests

                    endpoint = state.ai_local_endpoint
                    if not endpoint:
                        raise ValueError("No endpoint configured")
                    # Try common health check endpoints
                    for path in ["", "/health", "/v1/models"]:
                        try:
                            headers = {}
                            if local_auth_token:
                                headers["Authorization"] = f"Bearer {local_auth_token}"
                            resp = requests.get(
                                f"{endpoint.rstrip('/')}{path}", headers=headers, timeout=10
                            )
                            if resp.status_code < 400:
                                return True
                        except Exception:
                            continue
                    raise ValueError("Could not connect to endpoint")

                await loop.run_in_executor(None, test_endpoint)

        else:
            # Never report success for a provider we didn't actually exercise.
            raise ValueError(f"Unknown AI provider '{provider}'")

        _record("ai", "ready", fingerprint=ai_fp)
        from plexmix.services.registry import ai_provider_display_name

        async with state:
            state.ai_test_status = (
                f"✓ {ai_provider_display_name(provider)} provider test successful"
            )
            state.testing_connection = False

    except ImportError as e:
        _record_provider_failure("ai", e, ai_fp, key_required=False, key=tested_secret)
        async with state:
            state.ai_test_status = f"✗ {_friendly_error(e, secrets)}"
            state.testing_connection = False

    except Exception as e:
        from plexmix.services.registry import requires_ai_api_key

        _record_provider_failure(
            "ai", e, ai_fp, key_required=requires_ai_api_key(ai_id), key=tested_secret
        )
        async with state:
            state.ai_test_status = f"✗ {_friendly_error(e, secrets)}"
            state.testing_connection = False


async def test_embedding_provider_impl(state: Any) -> None:
    """Test the configured embedding provider with a sample text."""
    async with state:
        if state.testing_connection:
            return  # another test is running (its button stays enabled; see _button_row)
        state.testing_connection = True
        state.embedding_test_status = "Testing embedding provider..."
        # Resolved on the server: a staged replacement wins over the stored value.
        api_key = sanitize_credential_value(_secret(state, "embedding_api_key"))
        custom_api_key = _secret(state, "embedding_custom_api_key")
        emb_id = state.embedding_provider
        emb_secret = custom_api_key if emb_id == "custom" else api_key
        emb_fp = _form_fingerprint(state, "embeddings", emb_secret)
    secrets = [api_key, custom_api_key]

    try:
        loop = asyncio.get_running_loop()
        provider = state.embedding_provider
        if provider in ("gemini", "openai", "cohere"):
            validate_api_key(api_key, provider)
        test_text = "This is a test string for embedding generation."

        if provider == "gemini":
            async with state:
                state.embedding_test_status = "Testing Gemini embeddings..."

            def test_gemini_embed():
                from google import genai

                client = genai.Client(api_key=api_key)
                result = client.models.embed_content(
                    model=state.embedding_model or "gemini-embedding-001",
                    contents=test_text,
                )
                return len(result.embeddings[0].values)

            dim = await loop.run_in_executor(None, test_gemini_embed)

        elif provider == "openai":
            async with state:
                state.embedding_test_status = "Testing OpenAI embeddings..."

            def test_openai_embed():
                from openai import OpenAI

                client = OpenAI(api_key=api_key)
                response = client.embeddings.create(
                    model=state.embedding_model or "text-embedding-3-small",
                    input=test_text,
                )
                return len(response.data[0].embedding)

            dim = await loop.run_in_executor(None, test_openai_embed)

        elif provider == "cohere":
            async with state:
                state.embedding_test_status = "Testing Cohere embeddings..."

            def test_cohere_embed():
                import cohere

                client = cohere.ClientV2(api_key=api_key)
                embed_model = state.embedding_model or "embed-v4.0"
                embed_kwargs = {
                    "texts": [test_text],
                    "model": embed_model,
                    "input_type": "search_document",
                    "embedding_types": ["float"],
                }
                if "v4" in embed_model:
                    embed_kwargs["output_dimension"] = state.embedding_dimension
                response = client.embed(**embed_kwargs)
                return len(response.embeddings.float_[0])

            dim = await loop.run_in_executor(None, test_cohere_embed)

        elif provider == "custom":
            async with state:
                state.embedding_test_status = "Testing custom embedding endpoint..."

            def test_custom_embed():
                from openai import OpenAI

                endpoint = state.embedding_custom_endpoint
                if not endpoint:
                    raise ValueError("No endpoint URL configured")
                model = state.embedding_custom_model
                if not model:
                    raise ValueError("No model name configured")
                client = OpenAI(
                    base_url=endpoint,
                    api_key=custom_api_key or "no-key-required",
                )
                response = client.embeddings.create(
                    model=model,
                    input=test_text,
                )
                return len(response.data[0].embedding)

            dim = await loop.run_in_executor(None, test_custom_embed)

        elif provider == "local":
            async with state:
                state.embedding_test_status = "Testing local embedding model..."

            def test_local_embed():
                from sentence_transformers import SentenceTransformer

                model_name = state.embedding_model or "all-MiniLM-L6-v2"
                model = SentenceTransformer(model_name)
                embedding = model.encode(test_text)
                return len(embedding)

            dim = await loop.run_in_executor(None, test_local_embed)

        else:
            raise ValueError(f"Unknown embedding provider '{provider}'")

        # The provider produced an embedding: it works with these values. (A size
        # that doesn't match the search index is the index's readiness, not this.)
        _record("embeddings", "ready", fingerprint=emb_fp)

        # Verify dimension matches expected
        expected_dim = state.embedding_dimension
        if dim != expected_dim:
            async with state:
                state.embedding_test_status = f"⚠ Embeddings work, but dimension mismatch (got {dim}, expected {expected_dim}). You may need to regenerate your embeddings."
                state.testing_connection = False
            return

        async with state:
            state.embedding_test_status = f"✓ Embedding test successful (dimension: {dim})"
            state.testing_connection = False

    except ImportError as e:
        _record_provider_failure("embeddings", e, emb_fp, key_required=False, key=emb_secret)
        async with state:
            state.embedding_test_status = f"✗ {_friendly_error(e, secrets)}"
            state.testing_connection = False

    except Exception as e:
        from plexmix.services.registry import requires_embedding_api_key

        _record_provider_failure(
            "embeddings",
            e,
            emb_fp,
            key_required=requires_embedding_api_key(emb_id),
            key=emb_secret,
        )
        async with state:
            state.embedding_test_status = f"✗ {_friendly_error(e, secrets)}"
            state.testing_connection = False
