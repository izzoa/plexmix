"""Tests for the signed, expiring UI browser session (plexmix.ui.auth_session)."""

import os
import stat
import time
from pathlib import Path

import pytest

from plexmix.ui import auth_session
from plexmix.ui.auth_session import KEY_BYTES, KEY_FILENAME, SESSION_MAX_AGE_S, issue, verify

PASSWORD = "SENTINEL-UIPW-0000"


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the data dir at tmp_path and reset the module's key caches."""
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(auth_session, "_file_keys", {})
    monkeypatch.setattr(auth_session, "_memory_keys", {})
    return tmp_path


class TestIssueVerify:
    def test_round_trip(self) -> None:
        token = issue(PASSWORD)
        assert verify(token, PASSWORD, time.time())

    def test_token_format_and_no_password_in_token(self) -> None:
        token = issue(PASSWORD, now=1_700_000_000)
        version, issued_at, nonce, sig = token.split(".")
        assert version == "v1"
        assert issued_at == "1700000000"
        assert nonce
        assert len(sig) == 64
        assert PASSWORD not in token

    def test_tokens_are_unique(self) -> None:
        assert issue(PASSWORD, now=1_700_000_000) != issue(PASSWORD, now=1_700_000_000)

    def test_tampered_signature(self) -> None:
        token = issue(PASSWORD)
        flipped = token[:-1] + ("0" if token[-1] != "0" else "1")
        assert not verify(flipped, PASSWORD, time.time())

    def test_tampered_issued_at(self) -> None:
        now = time.time()
        version, issued_at, nonce, sig = issue(PASSWORD, now=now - 100).split(".")
        forged = ".".join([version, str(int(issued_at) + 50), nonce, sig])
        assert not verify(forged, PASSWORD, now)

    def test_tampered_nonce(self) -> None:
        version, issued_at, nonce, sig = issue(PASSWORD).split(".")
        other_nonce = ("A" if nonce[0] != "A" else "B") + nonce[1:]
        forged = ".".join([version, issued_at, other_nonce, sig])
        assert not verify(forged, PASSWORD, time.time())

    def test_wrong_password(self) -> None:
        token = issue(PASSWORD)
        assert not verify(token, "SENTINEL-UIPW-1111", time.time())

    def test_empty_password_never_verifies(self) -> None:
        token = issue(PASSWORD)
        assert not verify(token, "", time.time())

    def test_expired_token(self) -> None:
        issued = 1_700_000_000.0
        token = issue(PASSWORD, now=issued)
        assert verify(token, PASSWORD, issued + SESSION_MAX_AGE_S - 1)
        assert verify(token, PASSWORD, issued + SESSION_MAX_AGE_S)
        assert not verify(token, PASSWORD, issued + SESSION_MAX_AGE_S + 1)

    def test_max_age_is_seven_days(self) -> None:
        assert SESSION_MAX_AGE_S == 7 * 24 * 3600

    def test_future_issued_at(self) -> None:
        now = 1_700_000_000.0
        token = issue(PASSWORD, now=now + 3600)
        assert not verify(token, PASSWORD, now)
        token = issue(PASSWORD, now=now + 1)
        assert not verify(token, PASSWORD, now)

    @pytest.mark.parametrize(
        "token",
        [
            "",
            "garbage",
            "v1",
            "v1.123",
            "v1.123.abc",
            "v1.123.nonce-nonce-nonce.sig.extra",
            "v1.-5.nonce-nonce-nonce1." + "0" * 64,
            "v1.12a.nonce-nonce-nonce1." + "0" * 64,
            "v1.123.short." + "0" * 64,
            "v1.123.bad!nonce!bad!nonce." + "0" * 64,
            "v1.123.nonce-nonce-nonce1." + "z" * 64,
            "v1.123.nonce-nonce-nonce1." + "0" * 63,
            "v1.123.nonce-nonce-nonce1." + "A" * 64,
        ],
    )
    def test_malformed_token(self, token: str) -> None:
        assert not verify(token, PASSWORD, time.time())

    def test_unknown_version(self) -> None:
        _, issued_at, nonce, sig = issue(PASSWORD).split(".")
        assert not verify(".".join(["v2", issued_at, nonce, sig]), PASSWORD, time.time())

    def test_non_string_token(self) -> None:
        assert not verify(None, PASSWORD, time.time())  # type: ignore[arg-type]
        assert not verify(123, PASSWORD, time.time())  # type: ignore[arg-type]

    def test_non_ascii_password(self) -> None:
        token = issue("pässwörd-✓")
        assert verify(token, "pässwörd-✓", time.time())
        assert not verify(token, "passwórd-✓", time.time())


class TestKeyFile:
    def test_key_file_created_with_0600(self, data_dir: Path) -> None:
        issue(PASSWORD)
        key_file = data_dir / KEY_FILENAME
        assert key_file.is_file()
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        assert len(key_file.read_bytes()) == KEY_BYTES

    def test_key_path_honours_data_dir(self, data_dir: Path) -> None:
        assert auth_session.key_path() == data_dir / KEY_FILENAME

    def test_key_created_in_missing_data_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        nested = tmp_path / "does" / "not" / "exist"
        monkeypatch.setenv("PLEXMIX_DATA_DIR", str(nested))
        token = issue(PASSWORD)
        assert (nested / KEY_FILENAME).is_file()
        assert verify(token, PASSWORD, time.time())

    def test_key_survives_process_cache_reset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        token = issue(PASSWORD)
        # Simulate a server restart: the key is re-read from disk.
        monkeypatch.setattr(auth_session, "_file_keys", {})
        assert verify(token, PASSWORD, time.time())

    def test_deleting_key_file_revokes_sessions(self, data_dir: Path) -> None:
        token = issue(PASSWORD)
        (data_dir / KEY_FILENAME).unlink()
        assert not verify(token, PASSWORD, time.time())
        # A fresh key file is created and new sessions work.
        assert (data_dir / KEY_FILENAME).is_file()
        assert verify(issue(PASSWORD), PASSWORD, time.time())

    def test_key_from_another_data_dir_does_not_verify(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = issue(PASSWORD)
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv("PLEXMIX_DATA_DIR", str(other))
        assert not verify(token, PASSWORD, time.time())

    def test_malformed_key_file_is_replaced(self, data_dir: Path) -> None:
        key_file = data_dir / KEY_FILENAME
        key_file.write_bytes(b"short")
        token = issue(PASSWORD)
        assert len(key_file.read_bytes()) == KEY_BYTES
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        assert verify(token, PASSWORD, time.time())

    def test_loose_key_file_permissions_are_tightened(self, data_dir: Path) -> None:
        key_file = data_dir / KEY_FILENAME
        key_file.write_bytes(os.urandom(KEY_BYTES))
        os.chmod(key_file, 0o644)
        issue(PASSWORD)
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory permissions"
    )
    def test_unwritable_dir_falls_back_to_memory_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        os.chmod(readonly, 0o500)
        try:
            monkeypatch.setenv("PLEXMIX_DATA_DIR", str(readonly))
            token = issue(PASSWORD)  # must not raise
            assert verify(token, PASSWORD, time.time())
            assert not (readonly / KEY_FILENAME).exists()
            # The in-memory key is stable for the life of the process.
            assert verify(issue(PASSWORD), PASSWORD, time.time())
            assert verify(token, PASSWORD, time.time())
        finally:
            os.chmod(readonly, 0o700)

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory permissions"
    )
    def test_uncreatable_data_dir_falls_back_to_memory_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent = tmp_path / "locked"
        parent.mkdir()
        os.chmod(parent, 0o500)
        try:
            monkeypatch.setenv("PLEXMIX_DATA_DIR", str(parent / "data"))
            token = issue(PASSWORD)
            assert verify(token, PASSWORD, time.time())
        finally:
            os.chmod(parent, 0o700)
