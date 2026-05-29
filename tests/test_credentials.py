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
