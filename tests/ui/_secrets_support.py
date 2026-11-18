"""Shared fixtures for the Settings secret-handling tests.

Every secret here is a fake sentinel. Nothing touches the real keyring or
``~/.plexmix``: the keyring backend is replaced by an in-memory fake, the data
dir points at ``tmp_path`` (with its own ``config.yaml``), and credential env
vars are cleared or set to sentinels.
"""

from __future__ import annotations

import pickle
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import yaml

# One distinct sentinel per credential *source*, so a leak names its origin.
PLEX_ENV = "SENTINEL-PLEX-ENV-0000"
PLEX_KEYRING = "SENTINEL-PLEX-KEYRING-0000"
PLEX_CONFIG = "SENTINEL-PLEX-CONFIG-0000"
GOOGLE_KEYRING = "SENTINEL-GOOGLE-KEYRING-0000"
OPENAI_KEYRING = "SENTINEL-OPENAI-KEYRING-0000"
OPENAI_ENV = "SENTINEL-OPENAI-ENV-0000"
ANTHROPIC_KEYRING = "SENTINEL-ANTHROPIC-KEYRING-0000"
COHERE_KEYRING = "SENTINEL-COHERE-KEYRING-0000"
CUSTOM_AI_KEYRING = "SENTINEL-CUSTOMAI-KEYRING-0000"
CUSTOM_AI_CONFIG = "SENTINEL-CUSTOMAI-CONFIG-0000"
CUSTOM_EMBED_KEYRING = "SENTINEL-CUSTOMEMB-KEYRING-0000"
CUSTOM_EMBED_CONFIG = "SENTINEL-CUSTOMEMB-CONFIG-0000"
LOCAL_TOKEN_CONFIG = "SENTINEL-LOCALTOKEN-CONFIG-0000"
UI_PASSWORD = "SENTINEL-UIPASSWORD-0000"


def staged_sentinel(field: str) -> str:
    return f"SENTINEL-STAGED-{field.upper()}-0000"


CREDENTIAL_ENV_VARS = (
    "PLEX_TOKEN",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "COHERE_API_KEY",
    "CUSTOM_AI_API_KEY",
    "CUSTOM_EMBEDDING_API_KEY",
    # pydantic-settings fills these into config keys missing from config.yaml.
    "AI_CUSTOM_API_KEY",
    "AI_LOCAL_AUTH_TOKEN",
    "EMBEDDING_CUSTOM_API_KEY",
    "PLEXMIX_UI_PASSWORD",
    "PLEX_URL",
    "PLEX_LIBRARY_NAME",
    "AI_DEFAULT_PROVIDER",
    "AI_MODEL",
    "AI_LOCAL_MODE",
    "AI_LOCAL_ENDPOINT",
    "EMBEDDING_DEFAULT_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION",
)


class FakeKeyring:
    """In-memory stand-in for the ``keyring`` module."""

    def __init__(self, data: Optional[Dict[str, str]] = None) -> None:
        self.data: Dict[str, str] = dict(data or {})
        self.set_calls: List[str] = []
        self.delete_calls: List[str] = []

    def get_password(self, service: str, key: str) -> Optional[str]:
        return self.data.get(key)

    def set_password(self, service: str, key: str, value: str) -> None:
        self.set_calls.append(key)
        self.data[key] = value

    def delete_password(self, service: str, key: str) -> None:
        self.delete_calls.append(key)
        self.data.pop(key, None)


FULL_KEYRING = {
    "plex_token": PLEX_KEYRING,
    "google_api_key": GOOGLE_KEYRING,
    "openai_api_key": OPENAI_KEYRING,
    "anthropic_api_key": ANTHROPIC_KEYRING,
    "cohere_api_key": COHERE_KEYRING,
    "custom_ai_api_key": CUSTOM_AI_KEYRING,
    "custom_embedding_api_key": CUSTOM_EMBED_KEYRING,
}


def write_config(data_dir: Any, config: Dict[str, Any]) -> None:
    (data_dir / "config.yaml").write_text(yaml.safe_dump(config))


def read_config(data_dir: Any) -> Dict[str, Any]:
    return yaml.safe_load((data_dir / "config.yaml").read_text()) or {}


def isolate(monkeypatch: Any, tmp_path: Any, keyring: Optional[FakeKeyring] = None) -> FakeKeyring:
    """Point config at ``tmp_path`` and the keyring at an in-memory fake."""
    from plexmix.config import credentials
    from plexmix.config import settings as cfg

    for var in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    monkeypatch.setattr(cfg, "_config_path_override", None)
    if not (tmp_path / "config.yaml").exists():
        write_config(tmp_path, {"plex": {"url": "http://localhost:32400"}})
    kr = keyring if keyring is not None else FakeKeyring()
    monkeypatch.setattr(credentials, "_get_keyring", lambda: kr)
    return kr


class ToastRecorder:
    """Replaces ``rx.toast`` so returned toasts are inspectable tuples."""

    def __init__(self) -> None:
        self.calls: List[Any] = []

    def _record(self, level: str, message: Any, **kwargs: Any) -> Any:
        event = ("toast", level, message, kwargs)
        self.calls.append(event)
        return event

    def success(self, message: Any = "", **kwargs: Any) -> Any:
        return self._record("success", message, **kwargs)

    def error(self, message: Any = "", **kwargs: Any) -> Any:
        return self._record("error", message, **kwargs)

    def warning(self, message: Any = "", **kwargs: Any) -> Any:
        return self._record("warning", message, **kwargs)

    def info(self, message: Any = "", **kwargs: Any) -> Any:
        return self._record("info", message, **kwargs)


def patch_toast(monkeypatch: Any) -> ToastRecorder:
    import reflex as rx  # the MagicMock installed by tests/ui/conftest.py

    recorder = ToastRecorder()
    monkeypatch.setattr(rx, "toast", recorder, raising=False)
    return recorder


def make_state(token: str = "tok-A", *, allowed: bool = True) -> Any:
    """A SettingsState usable with ``async with`` and carrying a client token."""
    from plexmix.ui.states.settings_state import SettingsState

    async def _aenter(self: Any) -> Any:
        return self

    async def _aexit(self: Any, *exc: Any) -> bool:
        return False

    def _access_allowed(self: Any) -> bool:
        return allowed

    cls = type(
        "IsolatedSettingsState",
        (SettingsState,),
        {"__aenter__": _aenter, "__aexit__": _aexit, "access_allowed": _access_allowed},
    )
    state = cls()
    state.router = SimpleNamespace(session=SimpleNamespace(client_token=token))
    return state


def public_attribute_reprs(state: Any) -> Dict[str, str]:
    """``repr()`` of every non-underscore, non-method attribute of ``state``."""
    import inspect

    out: Dict[str, str] = {}
    for name in dir(state):
        if name.startswith("_"):
            continue
        try:
            value = getattr(state, name)
        except Exception as e:  # a computed var that cannot evaluate in tests
            out[name] = f"<error {type(e).__name__}>"
            continue
        if inspect.ismethod(value) or inspect.isfunction(value) or inspect.isbuiltin(value):
            continue
        out[name] = repr(value)
    return out


def assert_no_sentinel(obj: Any, sentinels: Iterable[str], where: str) -> None:
    text = obj if isinstance(obj, str) else repr(obj)
    for sentinel in sentinels:
        assert sentinel not in text, f"{sentinel} leaked via {where}"


def assert_state_clean(state: Any, sentinels: Iterable[str], step: str) -> None:
    """No sentinel in any public attribute, nor in the pickled instance dict."""
    sentinels = list(sentinels)
    for name, text in public_attribute_reprs(state).items():
        assert_no_sentinel(text, sentinels, f"state.{name} after {step}")
    # What Reflex's disk state manager would persist (backend vars included).
    blob = pickle.dumps(dict(vars(state)))
    for sentinel in sentinels:
        assert sentinel.encode() not in blob, f"{sentinel} would be pickled after {step}"
