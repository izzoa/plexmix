"""Leak-proof test: no credential ever reaches client-visible or persisted state.

Every credential source (env vars, keyring, config file, or the getters
themselves) returns a distinct fake sentinel. Each Settings event is then run
(load, both provider switches, staging, every connection test and discovery
with the network patched, save), and after every step:

- no sentinel appears in ``repr()`` of any state attribute not starting with ``_``
  (what Reflex syncs to the browser),
- no sentinel appears in the pickled instance dict (what Reflex's disk state
  manager writes to ``.states``),
- no sentinel appears in any returned or yielded event / toast,
- no sentinel is logged.
"""

import asyncio
import inspect
import socket
from unittest.mock import MagicMock, patch

import pytest

from plexmix.ui.secret_drafts import secret_drafts
from tests.ui._secrets_support import (
    ANTHROPIC_KEYRING,
    COHERE_KEYRING,
    CUSTOM_AI_CONFIG,
    CUSTOM_AI_KEYRING,
    CUSTOM_EMBED_CONFIG,
    CUSTOM_EMBED_KEYRING,
    FULL_KEYRING,
    GOOGLE_KEYRING,
    LOCAL_TOKEN_CONFIG,
    OPENAI_ENV,
    OPENAI_KEYRING,
    PLEX_CONFIG,
    PLEX_ENV,
    PLEX_KEYRING,
    FakeKeyring,
    assert_no_sentinel,
    assert_state_clean,
    isolate,
    make_state,
    patch_toast,
    staged_sentinel,
    write_config,
)

OLD_SECRET_VARS = (
    "plex_token",
    "ai_api_key",
    "ai_local_auth_token",
    "ai_custom_api_key",
    "embedding_api_key",
    "embedding_custom_api_key",
)
SECRET_FIELDS = OLD_SECRET_VARS
STAGED = {field: staged_sentinel(field) for field in SECRET_FIELDS}

# Distinct sentinels returned by the credential getters in "getters" mode.
GETTER_SENTINELS = {
    "plex_token": "SENTINEL-GETTER-PLEX-0000",
    "google_api_key": "SENTINEL-GETTER-GOOGLE-0000",
    "openai_api_key": "SENTINEL-GETTER-OPENAI-0000",
    "anthropic_api_key": "SENTINEL-GETTER-ANTHROPIC-0000",
    "cohere_api_key": "SENTINEL-GETTER-COHERE-0000",
    "custom_ai_api_key": "SENTINEL-GETTER-CUSTOMAI-0000",
    "custom_embedding_api_key": "SENTINEL-GETTER-CUSTOMEMB-0000",
}
GETTER_NAMES = {
    "get_plex_token": "plex_token",
    "get_google_api_key": "google_api_key",
    "get_openai_api_key": "openai_api_key",
    "get_anthropic_api_key": "anthropic_api_key",
    "get_cohere_api_key": "cohere_api_key",
    "get_custom_ai_api_key": "custom_ai_api_key",
    "get_custom_embedding_api_key": "custom_embedding_api_key",
}

ALL_SENTINELS = [
    PLEX_ENV,
    PLEX_KEYRING,
    PLEX_CONFIG,
    GOOGLE_KEYRING,
    OPENAI_KEYRING,
    OPENAI_ENV,
    ANTHROPIC_KEYRING,
    COHERE_KEYRING,
    CUSTOM_AI_KEYRING,
    CUSTOM_AI_CONFIG,
    CUSTOM_EMBED_KEYRING,
    CUSTOM_EMBED_CONFIG,
    LOCAL_TOKEN_CONFIG,
    *GETTER_SENTINELS.values(),
    *STAGED.values(),
]

AI_PROVIDERS = ("gemini", "openai", "anthropic", "cohere", "custom", "local")
EMBEDDING_PROVIDERS = ("gemini", "openai", "cohere", "custom", "local")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Belt and braces: any real socket connection fails the test loudly."""

    def _refuse(*args, **kwargs):
        raise AssertionError("network access attempted in a unit test")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


@pytest.fixture(autouse=True)
def _clean_drafts():
    yield
    secret_drafts.reap(["tok-leak", "tok-anon"])


def _full_config():
    return {
        "plex": {
            "url": "http://plex.local:32400",
            "library_name": "Music",
            "token": PLEX_CONFIG,
        },
        "ai": {
            "default_provider": "openai",
            "custom_endpoint": "http://llm.local/v1",
            "custom_model": "m",
            "custom_api_key": CUSTOM_AI_CONFIG,
            "local_mode": "endpoint",
            "local_endpoint": "http://llm.local",
            "local_auth_token": LOCAL_TOKEN_CONFIG,
        },
        "embedding": {
            "default_provider": "gemini",
            "custom_endpoint": "http://emb.local/v1",
            "custom_model": "e",
            "custom_api_key": CUSTOM_EMBED_CONFIG,
        },
    }


def _setup_sources(monkeypatch, tmp_path, mode):
    """Make every credential source return a distinct sentinel."""
    from plexmix.config import credentials

    write_config(tmp_path, _full_config())
    if mode == "backend":
        # Real lookup code against fake env + keyring + config file.
        kr = isolate(monkeypatch, tmp_path, FakeKeyring(FULL_KEYRING))
        monkeypatch.setenv("PLEX_TOKEN", PLEX_ENV)
        monkeypatch.setenv("OPENAI_API_KEY", OPENAI_ENV)
        return kr
    # "getters": every credential getter is patched to return its own sentinel.
    kr = isolate(monkeypatch, tmp_path, FakeKeyring())
    monkeypatch.setattr(credentials, "get_credential", lambda key: GETTER_SENTINELS.get(key))
    monkeypatch.setattr(
        credentials,
        "get_credential_source",
        lambda key: "keyring" if key in GETTER_SENTINELS else None,
    )
    for getter, key in GETTER_NAMES.items():
        monkeypatch.setattr(credentials, getter, lambda key=key: GETTER_SENTINELS[key])
    return kr


class _EchoingFailure(Exception):
    pass


def _echo(*parts):
    """An SDK error that (worst case) echoes the credential it was given."""
    return _EchoingFailure("request rejected for credential " + " ".join(map(str, parts)))


def _sdk_patches(fail):
    """Patch every SDK / network entry point a Settings test or discovery uses."""

    def client_factory(**kwargs):
        client = MagicMock()
        key = kwargs.get("api_key")
        if fail:
            err = _echo(key)
            client.chat.completions.create.side_effect = err
            client.embeddings.create.side_effect = err
            client.models.generate_content.side_effect = err
            client.models.embed_content.side_effect = err
            client.messages.create.side_effect = err
            client.chat.side_effect = err
            client.embed.side_effect = err
        return client

    def plex_server(url, token):
        if fail:
            raise _echo(url, token)
        server = MagicMock()
        server.library.sections.return_value = []
        return server

    def requests_get(url, headers=None, timeout=None):
        if fail:
            raise _echo(headers)
        return MagicMock(status_code=200)

    def discover(endpoint, api_key=None, timeout=5):
        if fail:
            raise _echo(api_key)
        return ["model-a"]

    return [
        patch("plexapi.server.PlexServer", side_effect=plex_server),
        patch("openai.OpenAI", side_effect=client_factory),
        patch("anthropic.Anthropic", side_effect=client_factory),
        patch("cohere.ClientV2", side_effect=client_factory),
        patch("google.genai.Client", side_effect=client_factory),
        patch("requests.get", side_effect=requests_get),
        patch("huggingface_hub.try_to_load_from_cache", return_value=None),
        patch("plexmix.services.providers.discover_endpoint_models", side_effect=discover),
    ]


def _check(state, step, events=(), caplog=None):
    assert_state_clean(state, ALL_SENTINELS, step)
    for event in events:
        assert_no_sentinel(event, ALL_SENTINELS, f"event returned by {step}")
    if caplog is not None:
        assert_no_sentinel(caplog.text, ALL_SENTINELS, f"logs during {step}")


def _as_list(result):
    if result is None:
        return []
    return list(result) if isinstance(result, list) else [result]


def _run_handlers(state, step, toasts, caplog, fail):
    """Run every connection test and discovery for every provider."""
    import contextlib

    with contextlib.ExitStack() as stack:
        for p in _sdk_patches(fail):
            stack.enter_context(p)
        for provider in AI_PROVIDERS:
            state.ai_provider = provider
            for local_mode in ("builtin", "endpoint") if provider == "local" else ("builtin",):
                state.ai_local_mode = local_mode
                asyncio.run(state.test_ai_provider())
                _check(state, f"{step}: test_ai_provider[{provider}/{local_mode}]", caplog=caplog)

                async def _discover():
                    return [e async for e in state.discover_models()]

                events = asyncio.run(_discover())
                _check(state, f"{step}: discover_models[{provider}/{local_mode}]", events, caplog)
        for provider in EMBEDDING_PROVIDERS:
            if provider == "local":
                continue  # sentence-transformers is not installed; nothing secret is used
            state.embedding_provider = provider
            asyncio.run(state.test_embedding_provider())
            _check(state, f"{step}: test_embedding_provider[{provider}]", caplog=caplog)
        asyncio.run(state.test_plex_connection())
        _check(state, f"{step}: test_plex_connection", caplog=caplog)
    _check(state, f"{step}: toasts", toasts.calls, caplog)


@pytest.mark.parametrize("mode", ["backend", "getters"])
@pytest.mark.parametrize("fail", [False, True], ids=["sdk-ok", "sdk-echoes-key"])
def test_no_secret_reaches_client_or_persisted_state(monkeypatch, tmp_path, caplog, mode, fail):
    import logging

    caplog.set_level(logging.DEBUG)
    _setup_sources(monkeypatch, tmp_path, mode)
    toasts = patch_toast(monkeypatch)
    state = make_state("tok-leak")

    # 1. Load: only value-free status reaches the state.
    state.on_load()
    _check(state, "on_load", caplog=caplog)
    state.load_settings()
    _check(state, "load_settings", caplog=caplog)
    assert any(state.secret_stored.values())  # the sources really were found

    # 2. Both provider switches, through every provider (and the display mappers).
    for provider in AI_PROVIDERS:
        state.set_ai_provider(provider)
        _check(state, f"set_ai_provider({provider})", caplog=caplog)
    for display in ("Google", "OpenAI", "Anthropic", "Cohere", "Custom (OpenAI-Compatible)"):
        state.set_ai_provider_from_display(display)
        _check(state, f"set_ai_provider_from_display({display})", caplog=caplog)
    for provider in EMBEDDING_PROVIDERS:
        state.set_embedding_provider(provider)
        _check(state, f"set_embedding_provider({provider})", caplog=caplog)
    for display in ("Gemini", "OpenAI", "Cohere", "Custom (OpenAI-Compatible)"):
        state.set_embedding_provider_from_display(display)
        _check(state, f"set_embedding_provider_from_display({display})", caplog=caplog)

    # 3. Every connection test and discovery, using stored secrets.
    _run_handlers(state, "stored", toasts, caplog, fail)

    # 4. Stage a replacement for every field (and clear the optional ones once).
    state.set_ai_provider("openai")
    state.set_embedding_provider("gemini")
    for field in ("ai_custom_api_key", "embedding_custom_api_key", "ai_local_auth_token"):
        events = _as_list(state.clear_secret(field))
        _check(state, f"clear_secret({field})", events, caplog)
        events = _as_list(state.discard_secret(field))
        _check(state, f"discard_secret({field})", events, caplog)
    for field, value in STAGED.items():
        state.stage_secret(field, value)
        _check(state, f"stage_secret({field})", caplog=caplog)
        assert state.secret_staged[field] == "replace"
    assert state.has_unsaved_changes is True

    # 5. Every connection test and discovery, now using the staged drafts.
    state.ai_custom_endpoint = "http://llm.local/v1"
    state.ai_local_endpoint = "http://llm.local"
    state.embedding_custom_endpoint = "http://emb.local/v1"
    _run_handlers(state, "staged", toasts, caplog, fail)
    state.ai_provider = "openai"
    state.embedding_provider = "gemini"

    # 6. Save: secrets go to the credential store / config only, never to the state.
    events = _as_list(state.save_all_settings())
    _check(state, "save_all_settings", events, caplog)
    assert events and events[0][:2] == ("toast", "success")
    assert state.has_unsaved_changes is False
    assert secret_drafts.staged_fields("tok-leak") == {}

    # 7. Reload after save.
    state.load_settings()
    _check(state, "load_settings after save", caplog=caplog)
    _check(state, "all toasts", toasts.calls, caplog)


def test_unauthenticated_client_gets_no_secrets_or_status(monkeypatch, tmp_path, caplog):
    """With a UI password set, an unauthenticated client's Settings events yield nothing."""
    import logging

    caplog.set_level(logging.DEBUG)
    kr = _setup_sources(monkeypatch, tmp_path, "backend")
    toasts = patch_toast(monkeypatch)
    config_before = (tmp_path / "config.yaml").read_text()
    state = make_state("tok-anon", allowed=False)
    blank_stored = {f: False for f in SECRET_FIELDS}
    blank_text = {f: "" for f in SECRET_FIELDS}

    def _check_blank(step, events=()):
        _check(state, step, events, caplog)
        assert state.secret_stored == blank_stored, step
        assert state.secret_source == blank_text, step
        assert state.secret_staged == blank_text, step

    state.on_load()
    _check_blank("on_load")
    state.load_settings()
    _check_blank("load_settings")
    for provider in AI_PROVIDERS:
        state.set_ai_provider(provider)
        _check_blank(f"set_ai_provider({provider})")
    for provider in EMBEDDING_PROVIDERS:
        state.set_embedding_provider(provider)
        _check_blank(f"set_embedding_provider({provider})")
    for field, value in STAGED.items():
        state.stage_secret(field, value)
        _check_blank(f"stage_secret({field})", _as_list(state.clear_secret(field)))
        _check_blank(f"discard_secret({field})", _as_list(state.discard_secret(field)))
    assert secret_drafts.staged_fields("tok-anon") == {}
    for name in GETTER_NAMES:
        assert state._resolve_secret(GETTER_NAMES[name]) == ""
    _run_handlers(state, "unauthenticated", toasts, caplog, fail=False)
    _check_blank("handlers")
    event = state.save_all_settings()
    _check_blank("save_all_settings", [event])
    assert (tmp_path / "config.yaml").read_text() == config_before
    assert kr.set_calls == [] and kr.delete_calls == []


def test_old_secret_vars_are_not_public_annotations():
    """Static check: none of the six secret vars is declared on SettingsState."""
    from plexmix.ui.states.settings_state import SettingsState

    for klass in inspect.getmro(SettingsState):
        annotations = vars(klass).get("__annotations__", {})
        for name in OLD_SECRET_VARS:
            assert name not in annotations, f"{klass.__name__} declares {name}"
    for name in OLD_SECRET_VARS:
        assert not hasattr(SettingsState, name), name
    # The only secret-related public vars are the value-free status dicts.
    annotations = vars(SettingsState)["__annotations__"]
    secretish = sorted(
        n
        for n in annotations
        if not n.startswith("_")
        and any(w in n for w in ("token", "api_key", "secret"))
        and not n.endswith("_error")
    )
    assert secretish == ["secret_source", "secret_staged", "secret_stored"]
