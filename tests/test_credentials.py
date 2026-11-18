"""Tests for credential sanitization and API-key validation.

Covers the prevention side of the harden-ai-provider-errors change: surrounding
whitespace is stripped on every path, transport-unsafe keys fail fast with an
actionable message, and unusual-but-sendable keys only warn.
"""

import logging
from unittest.mock import MagicMock

import pytest

from plexmix.config import credentials
from plexmix.config.credentials import (
    sanitize_credential_value,
    validate_api_key,
)

# Reference the exception through the live module: another test
# (test_config) reloads this module, which rebinds the class object, so a
# directly-imported name would no longer match what validate_api_key raises.


class TestSanitizeCredentialValue:
    def test_strips_surrounding_whitespace_and_newlines(self):
        assert sanitize_credential_value("  AIzaABC \n") == "AIzaABC"
        assert sanitize_credential_value("\tsk-abc\r\n") == "sk-abc"

    def test_preserves_interior(self):
        # Only surrounding characters are removed; the interior is untouched.
        assert sanitize_credential_value("  a b\tc  ") == "a b\tc"

    def test_none_passthrough(self):
        assert sanitize_credential_value(None) is None

    def test_clean_value_unchanged(self):
        assert sanitize_credential_value("AIzaClean") == "AIzaClean"


class TestStoreGetSanitize:
    def test_store_strips_before_persisting(self, monkeypatch):
        kr = MagicMock()
        monkeypatch.setattr(credentials, "_get_keyring", lambda: kr)
        credentials.store_credential("google_api_key", "  key123\n")
        kr.set_password.assert_called_once_with(
            credentials.SERVICE_NAME, "google_api_key", "key123"
        )

    def test_get_strips_keyring_value(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        kr = MagicMock()
        kr.get_password.return_value = "  key123\n"
        monkeypatch.setattr(credentials, "_get_keyring", lambda: kr)
        assert credentials.get_credential("google_api_key") == "key123"

    def test_get_strips_env_value(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "  envkey \n")
        assert credentials.get_credential("google_api_key") == "envkey"


class TestValidateApiKey:
    def test_clean_key_ok(self):
        validate_api_key("AIzaSyA-1_b2c3", "gemini")  # must not raise

    @pytest.mark.parametrize(
        "bad",
        ["AIza bad", "AIza\tbad", "AIza\nbad", "AIzacafé", "AIza\x00bad"],
    )
    def test_transport_unsafe_raises(self, bad):
        with pytest.raises(credentials.InvalidAPIKeyError):
            validate_api_key(bad, "gemini")

    def test_empty_or_none_no_raise(self):
        validate_api_key("", "gemini")
        validate_api_key(None, "gemini")

    def test_unusual_but_sendable_warns_but_does_not_raise(self, caplog):
        with caplog.at_level(logging.WARNING):
            validate_api_key("key$with*chars", "openai")  # transport-safe, odd shape
        assert any("unexpected format" in r.getMessage() for r in caplog.records)

    def test_conventional_key_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            validate_api_key("sk-ABCdef0123", "openai")
        assert not [r for r in caplog.records if "unexpected format" in r.getMessage()]

    def test_shape_warning_scoped_to_known_providers(self, caplog):
        # A custom provider may use any key format, so no shape warning.
        with caplog.at_level(logging.WARNING):
            validate_api_key("key$with*chars", "custom")
        assert not [r for r in caplog.records if "unexpected format" in r.getMessage()]


class TestGetCredentialSource:
    """get_credential_source reports where a secret resolves from, never the value."""

    _SECRET = "SENTINEL-SOURCE-0000"

    def _clear_env(self, monkeypatch):
        for var in ("PLEX_TOKEN", "GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)

    def test_env_wins_over_keyring(self, monkeypatch):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("PLEX_TOKEN", self._SECRET)
        kr = MagicMock()
        kr.get_password.return_value = "SENTINEL-KEYRING-0000"
        monkeypatch.setattr(credentials, "_get_keyring", lambda: kr)
        assert credentials.get_credential_source("plex_token") == "env:PLEX_TOKEN"
        kr.get_password.assert_not_called()

    def test_names_the_env_var_that_supplies_the_value(self, monkeypatch):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("GEMINI_API_KEY", self._SECRET)
        monkeypatch.setattr(credentials, "_get_keyring", lambda: None)
        assert credentials.get_credential_source("google_api_key") == "env:GEMINI_API_KEY"

    def test_keyring_only(self, monkeypatch):
        self._clear_env(monkeypatch)
        kr = MagicMock()
        kr.get_password.return_value = self._SECRET
        monkeypatch.setattr(credentials, "_get_keyring", lambda: kr)
        assert credentials.get_credential_source("openai_api_key") == "keyring"
        kr.get_password.assert_called_once_with(credentials.SERVICE_NAME, "openai_api_key")

    def test_nothing_stored(self, monkeypatch):
        self._clear_env(monkeypatch)
        kr = MagicMock()
        kr.get_password.return_value = None
        monkeypatch.setattr(credentials, "_get_keyring", lambda: kr)
        assert credentials.get_credential_source("plex_token") is None

    def test_blank_keyring_value_is_not_stored(self, monkeypatch):
        self._clear_env(monkeypatch)
        kr = MagicMock()
        kr.get_password.return_value = "  \n"
        monkeypatch.setattr(credentials, "_get_keyring", lambda: kr)
        assert credentials.get_credential_source("plex_token") is None

    def test_keyring_unavailable(self, monkeypatch):
        self._clear_env(monkeypatch)
        monkeypatch.setattr(credentials, "_get_keyring", lambda: None)
        assert credentials.get_credential_source("plex_token") is None

    def test_keyring_error_returns_none_and_logs_no_value(self, monkeypatch, caplog):
        self._clear_env(monkeypatch)
        kr = MagicMock()
        kr.get_password.side_effect = RuntimeError(f"backend exploded near {self._SECRET}")
        monkeypatch.setattr(credentials, "_get_keyring", lambda: kr)
        with caplog.at_level(logging.DEBUG):
            assert credentials.get_credential_source("plex_token") is None
        assert self._SECRET not in caplog.text

    def test_unknown_key_falls_back_to_keyring(self, monkeypatch):
        kr = MagicMock()
        kr.get_password.return_value = self._SECRET
        monkeypatch.setattr(credentials, "_get_keyring", lambda: kr)
        assert credentials.get_credential_source("not_a_mapped_key") == "keyring"

    @pytest.mark.parametrize("where", ["env", "keyring"])
    def test_return_value_never_contains_the_secret(self, monkeypatch, caplog, where):
        self._clear_env(monkeypatch)
        kr = MagicMock()
        kr.get_password.return_value = self._SECRET
        monkeypatch.setattr(credentials, "_get_keyring", lambda: kr)
        if where == "env":
            monkeypatch.setenv("PLEX_TOKEN", self._SECRET)
        with caplog.at_level(logging.DEBUG):
            source = credentials.get_credential_source("plex_token")
        assert source is not None
        assert source != self._SECRET
        assert self._SECRET not in source
        assert self._SECRET not in caplog.text

    def test_matches_get_credential_resolution(self, monkeypatch):
        """The reported source agrees with what get_credential actually returns."""
        self._clear_env(monkeypatch)
        kr = MagicMock()
        kr.get_password.return_value = "SENTINEL-KEYRING-0000"
        monkeypatch.setattr(credentials, "_get_keyring", lambda: kr)
        assert credentials.get_credential_source("plex_token") == "keyring"
        assert credentials.get_credential("plex_token") == "SENTINEL-KEYRING-0000"
        monkeypatch.setenv("PLEX_TOKEN", "SENTINEL-ENV-0000")
        assert credentials.get_credential_source("plex_token") == "env:PLEX_TOKEN"
        assert credentials.get_credential("plex_token") == "SENTINEL-ENV-0000"
