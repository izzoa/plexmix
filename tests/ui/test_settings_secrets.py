"""Behavior of the Settings secret fields (webui-secret-handling).

Secret values never live in SettingsState: the state carries value-free status
(``secret_stored`` / ``secret_source`` / ``secret_staged``), typed replacements
are held server-side in ``secret_drafts``, and stored values are resolved on the
server only for tests, discovery and saves. All secrets are fake sentinels.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from plexmix.ui.secret_drafts import secret_drafts
from tests.ui._secrets_support import (
    CUSTOM_AI_CONFIG,
    CUSTOM_AI_KEYRING,
    CUSTOM_EMBED_CONFIG,
    LOCAL_TOKEN_CONFIG,
    OPENAI_KEYRING,
    PLEX_CONFIG,
    PLEX_ENV,
    PLEX_KEYRING,
    GOOGLE_KEYRING,
    FakeKeyring,
    isolate,
    make_state,
    patch_toast,
    read_config,
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
ALL_FIELDS = OLD_SECRET_VARS


@pytest.fixture(autouse=True)
def _clean_drafts():
    yield
    secret_drafts.reap(["tok-A", "tok-B"])


def _base_config(**overrides):
    config = {
        "plex": {"url": "http://localhost:32400", "library_name": "Music"},
        "ai": {"default_provider": "openai"},
        "embedding": {"default_provider": "openai"},
    }
    for section, values in overrides.items():
        config.setdefault(section, {}).update(values)
    return config


def _set_value_refs(events):
    return {e[1] for e in events if isinstance(e, tuple) and e[0] == "set_value"}


def _input_ids(fields):
    """DOM ids of the given secret inputs (Settings renders each one exactly once)."""
    return {f"secret-{field}" for field in fields}


# Secrets that pydantic-settings fills into config keys missing from config.yaml.
ENV_UI_PASSWORD = "SENTINEL-UIPW-ENV-0000"
ENV_CUSTOM_AI = "SENTINEL-CUSTOMAI-ENV-0000"
ENV_LOCAL_TOKEN = "SENTINEL-LOCALTOKEN-ENV-0000"
ENV_CUSTOM_EMBED = "SENTINEL-CUSTOMEMB-ENV-0000"
ENV_SECRETS = {
    "PLEX_TOKEN": PLEX_ENV,
    "PLEXMIX_UI_PASSWORD": ENV_UI_PASSWORD,
    "AI_CUSTOM_API_KEY": ENV_CUSTOM_AI,
    "AI_LOCAL_AUTH_TOKEN": ENV_LOCAL_TOKEN,
    "EMBEDDING_CUSTOM_API_KEY": ENV_CUSTOM_EMBED,
}


# ── 3.3 status vars replace the secret vars ─────────────────────────


class TestLoadSettingsStatusOnly:
    def test_old_secret_vars_are_gone(self):
        from plexmix.ui.states.settings_state import SettingsState

        state = SettingsState()
        for name in OLD_SECRET_VARS:
            assert not hasattr(state, name), name
            assert not hasattr(SettingsState, f"set_{name}"), name
        for name in ("validate_plex_token", "validate_ai_api_key", "validate_embedding_api_key"):
            assert not hasattr(SettingsState, name), name

    def test_load_settings_populates_only_status(self, monkeypatch, tmp_path):
        write_config(
            tmp_path,
            _base_config(
                plex={"token": PLEX_CONFIG},
                ai={"custom_api_key": CUSTOM_AI_CONFIG, "local_auth_token": LOCAL_TOKEN_CONFIG},
            ),
        )
        isolate(
            monkeypatch,
            tmp_path,
            FakeKeyring({"plex_token": PLEX_KEYRING, "openai_api_key": OPENAI_KEYRING}),
        )
        state = make_state()
        state.load_settings()

        assert state.secret_source == {
            "plex_token": "keyring",
            "ai_api_key": "keyring",
            "ai_custom_api_key": "config",
            "ai_local_auth_token": "config",
            "embedding_api_key": "keyring",
            "embedding_custom_api_key": "",
        }
        assert state.secret_stored == {f: bool(state.secret_source[f]) for f in ALL_FIELDS}
        assert state.secret_staged == {f: "" for f in ALL_FIELDS}
        for name in OLD_SECRET_VARS:
            assert name not in vars(state)

    def test_env_var_source_is_named(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path, FakeKeyring({"plex_token": PLEX_KEYRING}))
        monkeypatch.setenv("PLEX_TOKEN", PLEX_ENV)
        state = make_state()
        state.load_settings()
        assert state.secret_source["plex_token"] == "env:PLEX_TOKEN"
        assert state.secret_stored["plex_token"] is True

    def test_nothing_stored(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.load_settings()
        assert state.secret_stored == {f: False for f in ALL_FIELDS}
        assert state.secret_source == {f: "" for f in ALL_FIELDS}

    def test_config_only_plex_token(self, monkeypatch, tmp_path):
        write_config(tmp_path, _base_config(plex={"token": PLEX_CONFIG}))
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.load_settings()
        assert state.secret_source["plex_token"] == "config"

    def test_custom_key_keeps_config_first_order(self, monkeypatch, tmp_path):
        write_config(tmp_path, _base_config(embedding={"custom_api_key": CUSTOM_EMBED_CONFIG}))
        isolate(monkeypatch, tmp_path, FakeKeyring({"custom_ai_api_key": CUSTOM_AI_KEYRING}))
        state = make_state()
        state.load_settings()
        assert state.secret_source["ai_custom_api_key"] == "keyring"
        assert state.secret_source["embedding_custom_api_key"] == "config"

    def test_keyless_provider_has_no_api_key_status(self, monkeypatch, tmp_path):
        write_config(tmp_path, _base_config(ai={"default_provider": "custom"}))
        isolate(monkeypatch, tmp_path, FakeKeyring({"openai_api_key": OPENAI_KEYRING}))
        state = make_state()
        state.load_settings()
        assert state.secret_stored["ai_api_key"] is False
        assert state.secret_source["ai_api_key"] == ""

    def test_unauthenticated_load_gets_no_status(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path, FakeKeyring({"plex_token": PLEX_KEYRING}))
        state = make_state(allowed=False)
        state.load_settings()
        state._refresh_secret_status()
        assert state.secret_stored == {f: False for f in ALL_FIELDS}
        assert state.secret_source == {f: "" for f in ALL_FIELDS}

    def test_load_settings_drops_earlier_drafts(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.stage_secret("plex_token", staged_sentinel("plex_token"))
        state.load_settings()
        assert secret_drafts.action("tok-A", "plex_token") == ""
        assert state.secret_staged["plex_token"] == ""

    def test_load_settings_empties_every_secret_input(self, monkeypatch, tmp_path):
        """The inputs are uncontrolled: a reload must clear text whose draft it dropped."""
        from plexmix.ui.states.settings_state import SECRET_FIELDS

        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.stage_secret("plex_token", staged_sentinel("plex_token"))
        events = state.load_settings()
        assert _set_value_refs(events) == _input_ids(SECRET_FIELDS)
        assert all(e[2] == "" for e in events)
        assert make_state("tok-B", allowed=False).load_settings() is None

    def test_on_load_after_reconnect_empties_inputs(self, monkeypatch, tmp_path):
        """A backgrounded tab reconnects: Reflex re-runs on_load with the inputs mounted."""
        from plexmix.ui.states.settings_state import SECRET_FIELDS

        isolate(monkeypatch, tmp_path)
        state = make_state()
        monkeypatch.setattr(type(state), "check_auth", lambda self: True)
        monkeypatch.setattr(type(state), "load_app_data", lambda self: None)
        state.stage_secret("ai_api_key", staged_sentinel("ai_api_key"))
        events = state.on_load()
        assert _set_value_refs(events) == _input_ids(SECRET_FIELDS)
        assert secret_drafts.staged_fields("tok-A") == {}
        assert state.secret_staged["ai_api_key"] == ""

    def test_on_load_without_auth_returns_no_events(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        monkeypatch.setattr(type(state), "check_auth", lambda self: False)
        assert state.on_load() is None


# ── 3.4 stage / clear / discard events ──────────────────────────────


class TestStageSecret:
    def test_stage_holds_value_server_side(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        value = staged_sentinel("plex_token")
        state.stage_secret("plex_token", value)
        assert secret_drafts.get("tok-A", "plex_token") == value
        assert state.secret_staged["plex_token"] == "replace"
        assert value not in repr(vars(state))

    def test_stage_sanitizes_surrounding_whitespace(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.stage_secret("ai_api_key", f"  {OPENAI_KEYRING}\n")
        assert secret_drafts.get("tok-A", "ai_api_key") == OPENAI_KEYRING

    def test_unknown_field_rejected(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.stage_secret("ui_password", "SENTINEL-X-0000")
        state.stage_secret("plex_url", "SENTINEL-X-0000")
        assert secret_drafts.staged_fields("tok-A") == {}

    def test_unauthenticated_call_rejected(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state(allowed=False)
        state.stage_secret("plex_token", staged_sentinel("plex_token"))
        assert state.clear_secret("ai_custom_api_key") is None
        assert state.discard_secret("plex_token") is None
        assert secret_drafts.staged_fields("tok-A") == {}

    def test_missing_client_token_rejected(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state(token="")
        state.stage_secret("plex_token", staged_sentinel("plex_token"))
        assert secret_drafts.staged_fields("") == {}

    def test_empty_value_discards_the_draft(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.stage_secret("plex_token", staged_sentinel("plex_token"))
        state.stage_secret("plex_token", "")
        assert secret_drafts.action("tok-A", "plex_token") == ""
        assert state.secret_staged["plex_token"] == ""
        state.stage_secret("plex_token", staged_sentinel("plex_token"))
        state.stage_secret("plex_token", "   ")
        assert secret_drafts.action("tok-A", "plex_token") == ""

    def test_empty_blur_keeps_a_staged_clear(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.clear_secret("ai_custom_api_key")
        state.stage_secret("ai_custom_api_key", "")
        assert secret_drafts.action("tok-A", "ai_custom_api_key") == "clear"

    def test_keyless_provider_api_key_not_staged(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.ai_provider = "local"
        state.stage_secret("ai_api_key", staged_sentinel("ai_api_key"))
        assert secret_drafts.action("tok-A", "ai_api_key") == ""

    def test_error_text_never_contains_the_input(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        bad_plex = "SENTINEL-BADPLEX-0000-TOO-LONG!"
        state.stage_secret("plex_token", bad_plex)
        assert state.plex_token_error  # format warning is still shown
        assert bad_plex not in state.plex_token_error
        # The typed length is a fragment of the secret: it must not be reported.
        assert str(len(bad_plex)) not in state.plex_token_error
        assert "got" not in state.plex_token_error

        state.ai_provider = "openai"
        state.stage_secret("ai_api_key", "SENTINEL-NOTSK-0000")
        assert state.ai_api_key_error
        assert "SENTINEL" not in state.ai_api_key_error

        state.embedding_provider = "cohere"
        state.stage_secret("embedding_api_key", "SENTINEL-SHORT")
        assert state.embedding_api_key_error
        assert "SENTINEL" not in state.embedding_api_key_error

    def test_valid_value_clears_error(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.stage_secret("plex_token", "short")
        assert state.plex_token_error
        state.stage_secret("plex_token", "A" * 20)
        assert state.plex_token_error == ""

    def test_clear_only_for_optional_fields(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        for field in ("plex_token", "ai_api_key", "embedding_api_key", "nope"):
            assert state.clear_secret(field) is None
            assert secret_drafts.action("tok-A", field) == ""
        for field in ("ai_custom_api_key", "embedding_custom_api_key", "ai_local_auth_token"):
            events = state.clear_secret(field)
            assert secret_drafts.action("tok-A", field) == "clear"
            assert state.secret_staged[field] == "clear"
            assert _set_value_refs(events) == {f"secret-{field}"}

    def test_discard_secret(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.clear_secret("ai_custom_api_key")
        events = state.discard_secret("ai_custom_api_key")
        assert secret_drafts.action("tok-A", "ai_custom_api_key") == ""
        assert state.secret_staged["ai_custom_api_key"] == ""
        assert "secret-ai_custom_api_key" in _set_value_refs(events)
        assert state.discard_secret("not_a_field") is None

    def test_drafts_are_per_session(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        a, b = make_state("tok-A"), make_state("tok-B")
        a.stage_secret("plex_token", staged_sentinel("plex_token"))
        b._refresh_secret_staged()
        assert a.secret_staged["plex_token"] == "replace"
        assert b.secret_staged["plex_token"] == ""
        assert b._resolve_secret("plex_token") == ""


# ── 3.5 save writes only staged secrets ─────────────────────────────


class TestSaveAllSettings:
    def test_save_without_drafts_writes_no_credentials(self, monkeypatch, tmp_path):
        write_config(
            tmp_path,
            _base_config(
                plex={"token": PLEX_CONFIG},
                ai={"custom_api_key": CUSTOM_AI_CONFIG, "local_auth_token": LOCAL_TOKEN_CONFIG},
                embedding={"custom_api_key": CUSTOM_EMBED_CONFIG},
            ),
        )
        kr = isolate(
            monkeypatch,
            tmp_path,
            FakeKeyring({"plex_token": PLEX_KEYRING, "openai_api_key": OPENAI_KEYRING}),
        )
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.plex_url = "http://plex.example:32400"  # a non-secret change
        from plexmix.config import credentials

        store_names = [n for n in dir(credentials) if n.startswith("store_")]
        mocks = {n: MagicMock(return_value=True) for n in store_names}
        for name, mock in mocks.items():
            monkeypatch.setattr(credentials, name, mock)
        delete_mock = MagicMock(return_value=True)
        monkeypatch.setattr(credentials, "delete_credential", delete_mock)

        events = state.save_all_settings()

        for name, mock in mocks.items():
            mock.assert_not_called()
        delete_mock.assert_not_called()
        assert kr.set_calls == [] and kr.delete_calls == []
        saved = read_config(tmp_path)
        assert saved["plex"]["url"] == "http://plex.example:32400"
        # Every stored secret is unchanged.
        assert saved["plex"]["token"] == PLEX_CONFIG
        assert saved["ai"]["custom_api_key"] == CUSTOM_AI_CONFIG
        assert saved["ai"]["local_auth_token"] == LOCAL_TOKEN_CONFIG
        assert saved["embedding"]["custom_api_key"] == CUSTOM_EMBED_CONFIG
        assert kr.data == {"plex_token": PLEX_KEYRING, "openai_api_key": OPENAI_KEYRING}
        assert events[0][:2] == ("toast", "success")
        assert _set_value_refs(events) == set()

    @pytest.mark.parametrize("keyring_available", [True, False])
    def test_env_plex_token_never_written(self, monkeypatch, tmp_path, keyring_available):
        kr = isolate(monkeypatch, tmp_path)
        if not keyring_available:
            from plexmix.config import credentials

            monkeypatch.setattr(credentials, "_get_keyring", lambda: None)
        monkeypatch.setenv("PLEX_TOKEN", PLEX_ENV)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        assert state.secret_source["plex_token"] == "env:PLEX_TOKEN"
        state.save_all_settings()
        assert kr.set_calls == []
        assert PLEX_ENV not in (tmp_path / "config.yaml").read_text()
        assert "token" not in read_config(tmp_path).get("plex", {})

    @pytest.mark.parametrize("sections_in_file", [False, True], ids=["absent", "present"])
    def test_env_secrets_never_copied_into_config(self, monkeypatch, tmp_path, sections_in_file):
        """No environment secret reaches config.yaml, whether or not its section exists."""
        config = {"plex": {"url": "http://localhost:32400"}}
        if sections_in_file:
            config.update(
                {
                    "ai": {"default_provider": "openai"},
                    "embedding": {"default_provider": "openai"},
                    "ui": {},
                }
            )
        write_config(tmp_path, config)
        kr = isolate(monkeypatch, tmp_path)
        for name, value in ENV_SECRETS.items():
            monkeypatch.setenv(name, value)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.plex_url = "http://plex.example:32400"  # a non-secret change

        events = state.save_all_settings()

        assert events[0][:2] == ("toast", "success")
        text = (tmp_path / "config.yaml").read_text()
        for name, value in ENV_SECRETS.items():
            assert value not in text, f"{name} was copied into config.yaml"
        saved = read_config(tmp_path)
        assert saved["plex"]["url"] == "http://plex.example:32400"
        assert "token" not in saved.get("plex", {})
        assert "custom_api_key" not in saved.get("ai", {})
        assert "local_auth_token" not in saved.get("ai", {})
        assert "custom_api_key" not in saved.get("embedding", {})
        assert "password" not in saved.get("ui", {})
        assert kr.set_calls == []

    def test_ui_password_env_rotation_survives_a_save(self, monkeypatch, tmp_path):
        """Saving Settings must not pin PLEXMIX_UI_PASSWORD into config.yaml."""
        from plexmix.ui.states.app_state import AppState

        isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("PLEXMIX_UI_PASSWORD", "SENTINEL-UIPW-OLD-0000")
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.save_all_settings()

        monkeypatch.setenv("PLEXMIX_UI_PASSWORD", "SENTINEL-UIPW-NEW-0000")
        assert AppState._configured_ui_password() == "SENTINEL-UIPW-NEW-0000"

    def test_file_secrets_kept_when_env_vars_also_set(self, monkeypatch, tmp_path):
        file_password = "SENTINEL-UIPW-FILE-0000"
        write_config(
            tmp_path,
            _base_config(
                plex={"token": PLEX_CONFIG},
                ai={"custom_api_key": CUSTOM_AI_CONFIG, "local_auth_token": LOCAL_TOKEN_CONFIG},
                embedding={"custom_api_key": CUSTOM_EMBED_CONFIG},
                ui={"password": file_password},
            ),
        )
        isolate(monkeypatch, tmp_path)
        for name, value in ENV_SECRETS.items():
            monkeypatch.setenv(name, value)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.save_all_settings()

        saved = read_config(tmp_path)
        assert saved["plex"]["token"] == PLEX_CONFIG
        assert saved["ai"]["custom_api_key"] == CUSTOM_AI_CONFIG
        assert saved["ai"]["local_auth_token"] == LOCAL_TOKEN_CONFIG
        assert saved["embedding"]["custom_api_key"] == CUSTOM_EMBED_CONFIG
        assert saved["ui"]["password"] == file_password

    def test_staged_env_shadowed_secret_is_still_written(self, monkeypatch, tmp_path):
        """Resetting secret keys to the file's values happens before staged changes."""
        isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("AI_LOCAL_AUTH_TOKEN", ENV_LOCAL_TOKEN)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.stage_secret("ai_local_auth_token", staged_sentinel("ai_local_auth_token"))
        state.save_all_settings()
        saved = read_config(tmp_path)
        assert saved["ai"]["local_auth_token"] == staged_sentinel("ai_local_auth_token")
        assert ENV_LOCAL_TOKEN not in (tmp_path / "config.yaml").read_text()

    def test_invalid_config_error_never_echoes_a_stored_value(self, monkeypatch, tmp_path, caplog):
        """A ValidationError embeds the rejected input (here an all-digit token)."""
        import logging

        digits = "98765432109876"
        (tmp_path / "config.yaml").write_text(
            f"plex:\n  url: http://localhost:32400\nai:\n  local_auth_token: {digits}\n"
        )
        isolate(monkeypatch, tmp_path)
        toasts = patch_toast(monkeypatch)
        caplog.set_level(logging.DEBUG)
        state = make_state()
        state.load_settings()
        event = state.save_all_settings()

        assert event[:2] == ("toast", "error")
        assert "ai.local_auth_token" in event[2]
        assert digits not in repr(event)
        assert digits not in repr(toasts.calls)
        assert digits not in caplog.text
        assert "Error loading settings" in caplog.text

    def test_staged_key_is_stored_once(self, monkeypatch, tmp_path):
        write_config(tmp_path, _base_config())
        kr = isolate(monkeypatch, tmp_path, FakeKeyring({"openai_api_key": OPENAI_KEYRING}))
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        new_key = staged_sentinel("ai_api_key")
        state.stage_secret("ai_api_key", new_key)

        from plexmix.config import credentials

        store_openai = MagicMock(wraps=credentials.store_openai_api_key)
        monkeypatch.setattr(credentials, "store_openai_api_key", store_openai)
        events = state.save_all_settings()

        store_openai.assert_called_once_with(new_key)
        assert kr.set_calls == ["openai_api_key"]
        assert kr.data["openai_api_key"] == new_key
        assert new_key not in (tmp_path / "config.yaml").read_text()
        # Drafts gone, the input is emptied, and the field shows a stored key.
        assert secret_drafts.staged_fields("tok-A") == {}
        assert state.secret_staged["ai_api_key"] == ""
        assert state.secret_stored["ai_api_key"] is True
        assert state.secret_source["ai_api_key"] == "keyring"
        assert _set_value_refs(events) == {"secret-ai_api_key"}
        assert all(new_key not in repr(e) for e in events)

    def test_plex_token_keyring_first_drops_config_copy(self, monkeypatch, tmp_path):
        write_config(tmp_path, _base_config(plex={"token": PLEX_CONFIG}))
        kr = isolate(monkeypatch, tmp_path)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.stage_secret("plex_token", staged_sentinel("plex_token"))
        state.save_all_settings()
        assert kr.data["plex_token"] == staged_sentinel("plex_token")
        assert "token" not in read_config(tmp_path)["plex"]
        assert state.secret_source["plex_token"] == "keyring"

    def test_plex_token_falls_back_to_config_without_keyring(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        from plexmix.config import credentials

        monkeypatch.setattr(credentials, "_get_keyring", lambda: None)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.stage_secret("plex_token", staged_sentinel("plex_token"))
        state.save_all_settings()
        assert read_config(tmp_path)["plex"]["token"] == staged_sentinel("plex_token")
        assert state.secret_source["plex_token"] == "config"

    def test_provider_key_without_keyring_warns(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        from plexmix.config import credentials

        monkeypatch.setattr(credentials, "_get_keyring", lambda: None)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.stage_secret("ai_api_key", staged_sentinel("ai_api_key"))
        events = state.save_all_settings()
        warnings = [e for e in events if e[:2] == ("toast", "warning")]
        assert len(warnings) == 1
        assert "keyring" in warnings[0][2]
        assert staged_sentinel("ai_api_key") not in (tmp_path / "config.yaml").read_text()

    def test_clear_removes_key_from_keyring_and_config(self, monkeypatch, tmp_path):
        write_config(
            tmp_path,
            _base_config(
                ai={"default_provider": "custom", "custom_api_key": CUSTOM_AI_CONFIG},
            ),
        )
        kr = isolate(monkeypatch, tmp_path, FakeKeyring({"custom_ai_api_key": CUSTOM_AI_KEYRING}))
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        assert state.secret_stored["ai_custom_api_key"] is True
        state.clear_secret("ai_custom_api_key")
        events = state.save_all_settings()
        assert "custom_ai_api_key" not in kr.data
        assert kr.delete_calls == ["custom_ai_api_key"]
        assert "custom_api_key" not in read_config(tmp_path).get("ai", {})
        assert state.secret_stored["ai_custom_api_key"] is False
        assert state.secret_source["ai_custom_api_key"] == ""
        assert "secret-ai_custom_api_key" in _set_value_refs(events)

    def test_clear_local_token_removes_config_value(self, monkeypatch, tmp_path):
        write_config(tmp_path, _base_config(ai={"local_auth_token": LOCAL_TOKEN_CONFIG}))
        isolate(monkeypatch, tmp_path)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.clear_secret("ai_local_auth_token")
        state.save_all_settings()
        assert "local_auth_token" not in read_config(tmp_path).get("ai", {})

    def test_staged_local_token_saved_to_config(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.stage_secret("ai_local_auth_token", staged_sentinel("ai_local_auth_token"))
        state.save_all_settings()
        saved = read_config(tmp_path)
        assert saved["ai"]["local_auth_token"] == staged_sentinel("ai_local_auth_token")

    def test_failed_save_keeps_drafts_and_redacts(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        toasts = patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        value = staged_sentinel("plex_token")
        state.stage_secret("plex_token", value)
        from plexmix.config.settings import Settings

        def _boom(self, *a, **k):
            raise OSError(f"disk full while writing {value}")

        monkeypatch.setattr(Settings, "save_to_file", _boom)
        event = state.save_all_settings()
        assert event[:2] == ("toast", "error")
        assert value not in repr(event) and value not in repr(toasts.calls)
        assert secret_drafts.action("tok-A", "plex_token") == "replace"

    def test_unauthenticated_save_rejected(self, monkeypatch, tmp_path):
        kr = isolate(monkeypatch, tmp_path)
        patch_toast(monkeypatch)
        before = (tmp_path / "config.yaml").read_text()
        state = make_state(allowed=False)
        event = state.save_all_settings()
        assert event[:2] == ("toast", "error")
        assert (tmp_path / "config.yaml").read_text() == before
        assert kr.set_calls == []


# ── 3.6 provider switches ───────────────────────────────────────────


class TestProviderSwitch:
    def test_key_staged_for_provider_a_not_saved_after_switch(self, monkeypatch, tmp_path):
        write_config(tmp_path, _base_config(ai={"default_provider": "openai"}))
        kr = isolate(monkeypatch, tmp_path)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.stage_secret("ai_api_key", staged_sentinel("ai_api_key"))
        state.set_ai_provider("gemini")
        assert state.secret_staged["ai_api_key"] == ""
        state.save_all_settings()
        assert kr.set_calls == []
        assert staged_sentinel("ai_api_key") not in repr(kr.data)

    def test_embedding_key_staged_then_switch_not_saved(self, monkeypatch, tmp_path):
        kr = isolate(monkeypatch, tmp_path)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.stage_secret("embedding_api_key", staged_sentinel("embedding_api_key"))
        state.set_embedding_provider("cohere")
        state.save_all_settings()
        assert kr.set_calls == []

    def test_switch_away_from_custom_keeps_config_key(self, monkeypatch, tmp_path):
        write_config(
            tmp_path,
            _base_config(
                ai={"default_provider": "custom", "custom_api_key": CUSTOM_AI_CONFIG},
                embedding={"default_provider": "custom", "custom_api_key": CUSTOM_EMBED_CONFIG},
            ),
        )
        isolate(monkeypatch, tmp_path)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.set_ai_provider("openai")
        state.set_embedding_provider("gemini")
        state.save_all_settings()
        saved = read_config(tmp_path)
        assert saved["ai"]["custom_api_key"] == CUSTOM_AI_CONFIG
        assert saved["embedding"]["custom_api_key"] == CUSTOM_EMBED_CONFIG
        assert saved["ai"]["default_provider"] == "openai"

    def test_switch_away_from_local_keeps_local_token(self, monkeypatch, tmp_path):
        write_config(
            tmp_path,
            _base_config(ai={"default_provider": "local", "local_auth_token": LOCAL_TOKEN_CONFIG}),
        )
        isolate(monkeypatch, tmp_path)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.set_ai_provider("openai")
        state.save_all_settings()
        assert read_config(tmp_path)["ai"]["local_auth_token"] == LOCAL_TOKEN_CONFIG

    def test_ai_switch_empties_the_discarded_inputs(self, monkeypatch, tmp_path):
        """Typed text left in a still-mounted input would be staged for the new provider."""
        write_config(tmp_path, _base_config(ai={"default_provider": "openai"}))
        kr = isolate(monkeypatch, tmp_path, FakeKeyring({"google_api_key": GOOGLE_KEYRING}))
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        state.stage_secret("ai_api_key", staged_sentinel("ai_api_key"))

        ai_inputs = _input_ids(("ai_api_key", "ai_custom_api_key", "ai_local_auth_token"))
        events = state.set_ai_provider("gemini")
        assert _set_value_refs(events) == ai_inputs
        assert all(e[2] == "" for e in events)
        assert state.secret_staged["ai_api_key"] == ""
        # The select's on_change goes through the display mapper, which forwards them.
        assert _set_value_refs(state.set_ai_provider_from_display("OpenAI")) == ai_inputs
        state.save_all_settings()
        assert kr.data == {"google_api_key": GOOGLE_KEYRING}

    def test_embedding_switch_empties_the_discarded_inputs(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.load_settings()
        state.stage_secret("embedding_api_key", staged_sentinel("embedding_api_key"))
        embedding_inputs = _input_ids(("embedding_api_key", "embedding_custom_api_key"))
        assert _set_value_refs(state.set_embedding_provider("cohere")) == embedding_inputs
        assert state.secret_staged["embedding_api_key"] == ""
        events = state.set_embedding_provider_from_display("Gemini")
        assert _set_value_refs(events) == embedding_inputs

    def test_switch_shows_new_providers_stored_status(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path, FakeKeyring({"cohere_api_key": "SENTINEL-COHERE-0000"}))
        state = make_state()
        state.load_settings()
        assert state.secret_stored["ai_api_key"] is False
        state.set_ai_provider("cohere")
        assert state.secret_stored["ai_api_key"] is True
        assert state.secret_source["ai_api_key"] == "keyring"
        state.set_embedding_provider("cohere")
        assert state.secret_source["embedding_api_key"] == "keyring"


# ── 3.7 unsaved-changes detection ───────────────────────────────────


class TestUnsavedChanges:
    def test_staged_secret_is_an_unsaved_change(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        assert state.has_unsaved_changes is False
        value = staged_sentinel("plex_token")
        state.stage_secret("plex_token", value)
        assert state.has_unsaved_changes is True
        assert value not in state._get_settings_snapshot()
        state.save_all_settings()
        assert state.has_unsaved_changes is False

    def test_staged_clear_is_an_unsaved_change(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.load_settings()
        state.clear_secret("embedding_custom_api_key")
        assert state.has_unsaved_changes is True
        state.discard_secret("embedding_custom_api_key")
        assert state.has_unsaved_changes is False


# ── 3.8 tests and discovery resolve secrets on the server ───────────


def _run(coro):
    return asyncio.run(coro)


async def _drain(agen):
    return [event async for event in agen]


class TestServerResolvedSecrets:
    def test_plex_test_uses_stored_then_staged_token(self, monkeypatch, tmp_path):
        kr = isolate(monkeypatch, tmp_path, FakeKeyring({"plex_token": PLEX_KEYRING}))
        state = make_state()
        state.load_settings()
        with patch("plexapi.server.PlexServer") as server:
            server.return_value.library.sections.return_value = []
            _run(state.test_plex_connection())
            assert server.call_args.args[1] == PLEX_KEYRING
            state.stage_secret("plex_token", staged_sentinel("plex_token"))
            _run(state.test_plex_connection())
            assert server.call_args.args[1] == staged_sentinel("plex_token")
        assert kr.data["plex_token"] == PLEX_KEYRING  # stored token unchanged
        assert kr.set_calls == []

    def test_ai_test_uses_stored_key_when_nothing_staged(self, monkeypatch, tmp_path):
        write_config(tmp_path, _base_config())  # openai for AI and embeddings
        isolate(monkeypatch, tmp_path, FakeKeyring({"openai_api_key": OPENAI_KEYRING}))
        state = make_state()
        state.load_settings()
        with patch("openai.OpenAI") as client:
            _run(state.test_ai_provider())
        assert client.call_args.kwargs["api_key"] == OPENAI_KEYRING

    def test_ai_test_uses_staged_key_without_storing_it(self, monkeypatch, tmp_path):
        write_config(tmp_path, _base_config())  # openai for AI and embeddings
        kr = isolate(monkeypatch, tmp_path, FakeKeyring({"openai_api_key": OPENAI_KEYRING}))
        state = make_state()
        state.load_settings()
        new_key = staged_sentinel("ai_api_key")
        state.stage_secret("ai_api_key", new_key)
        with patch("openai.OpenAI") as client:
            _run(state.test_ai_provider())
        assert client.call_args.kwargs["api_key"] == new_key
        assert kr.data["openai_api_key"] == OPENAI_KEYRING
        assert kr.set_calls == []

    def test_custom_ai_test_resolves_config_first(self, monkeypatch, tmp_path):
        write_config(
            tmp_path,
            _base_config(
                ai={
                    "default_provider": "custom",
                    "custom_endpoint": "http://llm.local/v1",
                    "custom_model": "m",
                    "custom_api_key": CUSTOM_AI_CONFIG,
                }
            ),
        )
        isolate(monkeypatch, tmp_path, FakeKeyring({"custom_ai_api_key": CUSTOM_AI_KEYRING}))
        state = make_state()
        state.load_settings()
        with patch("openai.OpenAI") as client:
            _run(state.test_ai_provider())
        assert client.call_args.kwargs["api_key"] == CUSTOM_AI_CONFIG

    def test_local_endpoint_test_sends_resolved_token(self, monkeypatch, tmp_path):
        write_config(
            tmp_path,
            _base_config(
                ai={
                    "default_provider": "local",
                    "local_mode": "endpoint",
                    "local_endpoint": "http://llm.local",
                    "local_auth_token": LOCAL_TOKEN_CONFIG,
                }
            ),
        )
        isolate(monkeypatch, tmp_path)
        state = make_state()
        state.load_settings()
        with patch("requests.get") as get:
            get.return_value.status_code = 200
            _run(state.test_ai_provider())
        assert get.call_args.kwargs["headers"]["Authorization"] == f"Bearer {LOCAL_TOKEN_CONFIG}"

    def test_embedding_test_uses_staged_key(self, monkeypatch, tmp_path):
        write_config(tmp_path, _base_config())  # openai for AI and embeddings
        isolate(monkeypatch, tmp_path, FakeKeyring({"openai_api_key": OPENAI_KEYRING}))
        state = make_state()
        state.load_settings()
        with patch("openai.OpenAI") as client:
            _run(state.test_embedding_provider())
            assert client.call_args.kwargs["api_key"] == OPENAI_KEYRING
            state.stage_secret("embedding_api_key", staged_sentinel("embedding_api_key"))
            _run(state.test_embedding_provider())
            assert client.call_args.kwargs["api_key"] == staged_sentinel("embedding_api_key")

    def test_discovery_uses_stored_then_staged_custom_key(self, monkeypatch, tmp_path):
        write_config(
            tmp_path,
            _base_config(ai={"default_provider": "custom", "custom_endpoint": "http://x/v1"}),
        )
        isolate(monkeypatch, tmp_path, FakeKeyring({"custom_ai_api_key": CUSTOM_AI_KEYRING}))
        patch_toast(monkeypatch)
        state = make_state()
        state.load_settings()
        with patch(
            "plexmix.services.providers.discover_endpoint_models", return_value=["m1"]
        ) as discover:
            _run(_drain(state.discover_models()))
            assert discover.call_args.args == ("http://x/v1", CUSTOM_AI_KEYRING)
            state.stage_secret("ai_custom_api_key", staged_sentinel("ai_custom_api_key"))
            _run(_drain(state.discover_models()))
            assert discover.call_args.args[1] == staged_sentinel("ai_custom_api_key")

    def test_staged_clear_tests_without_a_key(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path, FakeKeyring({"custom_ai_api_key": CUSTOM_AI_KEYRING}))
        state = make_state()
        state.load_settings()
        state.clear_secret("ai_custom_api_key")
        assert state._resolve_secret("ai_custom_api_key") == ""

    def test_unauthorized_session_resolves_nothing(self, monkeypatch, tmp_path):
        isolate(monkeypatch, tmp_path, FakeKeyring({"plex_token": PLEX_KEYRING}))
        state = make_state(allowed=False)
        state.plex_url = "http://attacker.example"
        assert state._resolve_secret("plex_token") == ""
        with patch("plexapi.server.PlexServer") as server:
            _run(state.test_plex_connection())
        server.assert_not_called()
        with patch("openai.OpenAI") as client:
            _run(state.test_ai_provider())
            _run(state.test_embedding_provider())
        client.assert_not_called()
        state.ai_provider = "custom"
        state.ai_custom_endpoint = "http://attacker.example/v1"
        with patch("plexmix.services.providers.discover_endpoint_models") as discover:
            _run(_drain(state.discover_models()))
        discover.assert_not_called()
