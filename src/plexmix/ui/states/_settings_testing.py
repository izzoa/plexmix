"""Extracted testing helpers for SettingsState.

These are standalone async functions that accept the SettingsState instance
(``state``) so they can read/write state fields while keeping the main
settings_state module shorter.
"""

import asyncio
import logging
import re
from typing import Any

from plexmix.ai.local_provider import LOCAL_LLM_DEFAULT_MODEL

logger = logging.getLogger(__name__)


def _friendly_error(e: Exception) -> str:
    """Turn a raw exception into a short, user-friendly error message."""
    msg = str(e)

    # Common patterns -> plain-English rewrites
    if "401" in msg or "Unauthorized" in msg or "invalid.*key" in msg.lower():
        return "Invalid API key. Please check your key and try again."
    if "403" in msg or "Forbidden" in msg:
        return "Access denied. Your API key may lack the required permissions."
    if "404" in msg or "Not Found" in msg:
        return "Endpoint not found. Please check the URL or model name."
    if "429" in msg or "rate" in msg.lower():
        return "Rate limited by the provider. Please wait a moment and try again."
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return "Connection timed out. Check the server URL and your network."
    if "connection" in msg.lower() and ("refused" in msg.lower() or "error" in msg.lower()):
        return "Could not connect to the server. Is the URL correct and the service running?"
    if "resolve" in msg.lower() or "dns" in msg.lower() or "name or service" in msg.lower():
        return "Could not resolve the hostname. Check the server URL."
    if "ssl" in msg.lower() or "certificate" in msg.lower():
        return "SSL/TLS error. The server's certificate may be invalid or expired."
    if "No module named" in msg:
        pkg = msg.split("No module named")[-1].strip(" \"'")
        return f"Missing Python package: {pkg}"

    # Fallback: truncate and clean up raw messages
    # Strip common noise like 'headers: {...}' or JSON blobs
    msg = re.sub(r"\{[^{}]{50,}\}", "{...}", msg)  # collapse long dicts
    msg = re.sub(r"headers:\s*\{[^}]*\}", "", msg)  # strip header dumps
    msg = msg.strip(" ,.")
    if len(msg) > 120:
        msg = msg[:117] + "..."
    return msg or "An unexpected error occurred."


async def test_plex_connection_impl(state: Any) -> None:
    """Test the Plex server connection using the configured URL and token."""
    async with state:
        state.testing_connection = True
        state.plex_test_status = "Testing..."

    try:
        from plexapi.server import PlexServer

        await asyncio.sleep(0.5)

        server = PlexServer(state.plex_url, state.plex_token)
        libraries = [
            section.title for section in server.library.sections() if section.type == "artist"
        ]

        async with state:
            state.plex_libraries = libraries
            state.plex_test_status = f"✓ Connected! Found {len(libraries)} music libraries"
            state.testing_connection = False

    except Exception as e:
        async with state:
            state.plex_test_status = f"✗ {_friendly_error(e)}"
            state.testing_connection = False


async def test_ai_provider_impl(state: Any) -> None:
    """Test the configured AI provider with a simple generation request."""
    async with state:
        state.testing_connection = True
        state.ai_test_status = "Testing AI provider..."

    try:
        loop = asyncio.get_running_loop()
        provider = state.ai_provider
        api_key = state.ai_api_key

        if provider == "gemini":
            async with state:
                state.ai_test_status = "Testing Gemini API..."

            def test_gemini():
                from google import genai

                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=state.ai_model or "gemini-2.5-flash",
                    contents="Say 'test successful' in exactly two words.",
                )
                return response.text

            await loop.run_in_executor(None, test_gemini)

        elif provider == "openai":
            async with state:
                state.ai_test_status = "Testing OpenAI API..."

            def test_openai():
                from openai import OpenAI

                client = OpenAI(api_key=api_key)
                response = client.chat.completions.create(
                    model=state.ai_model or "gpt-5-mini",
                    messages=[{"role": "user", "content": "Say 'test' in one word."}],
                    max_tokens=10,
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
                    model=state.ai_model or "claude-sonnet-4-5",
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
                    model=state.ai_model or "command-r7b-12-2024",
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
                    api_key=state.ai_custom_api_key or "no-key-required",
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
                            if state.ai_local_auth_token:
                                headers["Authorization"] = f"Bearer {state.ai_local_auth_token}"
                            resp = requests.get(
                                f"{endpoint.rstrip('/')}{path}", headers=headers, timeout=10
                            )
                            if resp.status_code < 400:
                                return True
                        except Exception:
                            continue
                    raise ValueError("Could not connect to endpoint")

                await loop.run_in_executor(None, test_endpoint)

        async with state:
            state.ai_test_status = f"✓ {provider.title()} provider test successful"
            state.testing_connection = False

    except ImportError as e:
        async with state:
            state.ai_test_status = f"✗ {_friendly_error(e)}"
            state.testing_connection = False

    except Exception as e:
        async with state:
            state.ai_test_status = f"✗ {_friendly_error(e)}"
            state.testing_connection = False


async def test_embedding_provider_impl(state: Any) -> None:
    """Test the configured embedding provider with a sample text."""
    async with state:
        state.testing_connection = True
        state.embedding_test_status = "Testing embedding provider..."

    try:
        loop = asyncio.get_running_loop()
        provider = state.embedding_provider
        api_key = state.embedding_api_key
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
                    api_key=state.embedding_custom_api_key or "no-key-required",
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
            dim = 0

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
        async with state:
            state.embedding_test_status = f"✗ {_friendly_error(e)}"
            state.testing_connection = False

    except Exception as e:
        async with state:
            state.embedding_test_status = f"✗ {_friendly_error(e)}"
            state.testing_connection = False
