"""Signed, expiring browser sessions for the optional UI password.

A successful sign-in issues a token that the UI stores in a cookie, so every
tab of the same browser is let in until the token expires, the user logs out,
the UI password changes, or the server key is deleted.

Token format: ``v1.<issued_at>.<nonce>.<sig>`` where ``sig`` is the hex
HMAC-SHA256, under a per-install server key, of
``"v1|<issued_at>|<nonce>|<sha256(password)>"``. Binding the password
fingerprint into the signature means a password change invalidates every
outstanding session without any server-side session table.

The server key is 32 random bytes in ``<data dir>/ui_session.key`` (mode 0600),
created on first use. Deleting that file revokes all sessions. If the file
cannot be written, a key is kept in memory for the life of the process, so
sessions then last only until the next restart.

Neither tokens, keys nor passwords are ever logged.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SESSION_MAX_AGE_S = 7 * 24 * 3600
"""Maximum session age in seconds (7 days); also the cookie ``max_age``."""

COOKIE_NAME = "plexmix_session"
"""Browser cookie that carries the session token."""

KEY_FILENAME = "ui_session.key"
KEY_BYTES = 32

_VERSION = "v1"
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_SIG_RE = re.compile(r"^[0-9a-f]{64}$")
_ISSUED_AT_RE = re.compile(r"^[0-9]{1,12}$")

_lock = threading.Lock()
# Key read from disk, keyed by key-file path, with the (mtime_ns, inode) it was read at.
_file_keys: dict[Path, tuple[tuple[int, int], bytes]] = {}
# In-memory fallback keys for key-file paths that could not be written.
_memory_keys: dict[Path, bytes] = {}


def key_path() -> Path:
    """Return the session key file path (honours ``PLEXMIX_DATA_DIR``)."""
    from plexmix.config.settings import _data_dir

    return _data_dir() / KEY_FILENAME


def _read_key_file(path: Path) -> Optional[bytes]:
    """Return the key stored at ``path`` if it exists and is well-formed, else None.

    Raises OSError when the file exists but cannot be read.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    stamp = (st.st_mtime_ns, st.st_ino)
    cached = _file_keys.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    data = path.read_bytes()
    if len(data) != KEY_BYTES:
        logger.warning("UI session key file is malformed; replacing it")
        return None
    if st.st_mode & 0o077:
        # Tighten a key file that was created or copied with loose permissions.
        try:
            os.chmod(path, 0o600)
        except OSError:
            logger.warning("Could not restrict permissions on the UI session key file")
    _file_keys[path] = (stamp, data)
    return data


def _write_key_file(path: Path) -> bytes:
    """Create a fresh key file at ``path`` with mode 0600 and return the key.

    An existing (malformed) file is replaced atomically. Raises OSError on failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    st = path.stat()
    _file_keys[path] = ((st.st_mtime_ns, st.st_ino), key)
    return key


def _get_key() -> bytes:
    """Return the server signing key, creating the key file on first use.

    Falls back to a process-lifetime in-memory key when the file cannot be
    read or written; never raises.
    """
    path = key_path()
    with _lock:
        try:
            key = _read_key_file(path)
            if key is not None:
                return key
        except OSError:
            logger.warning("UI session key file is unreadable; using an in-memory key")
            return _memory_keys.setdefault(path, secrets.token_bytes(KEY_BYTES))

        memory_key = _memory_keys.get(path)
        if memory_key is not None:
            # Already fell back for this path: keep the same key so sessions survive.
            return memory_key
        try:
            return _write_key_file(path)
        except OSError:
            logger.warning(
                "Could not write the UI session key file; sessions will last "
                "only until the server restarts"
            )
            return _memory_keys.setdefault(path, secrets.token_bytes(KEY_BYTES))


def _password_fingerprint(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _sign(key: bytes, issued_at: str, nonce: str, password: str) -> str:
    message = f"{_VERSION}|{issued_at}|{nonce}|{_password_fingerprint(password)}"
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


def issue(password: str, now: Optional[float] = None) -> str:
    """Issue a session token bound to ``password``, valid for ``SESSION_MAX_AGE_S``.

    ``now`` defaults to the current wall-clock time (seconds since the epoch).
    """
    issued_at = str(int(time.time() if now is None else now))
    nonce = secrets.token_urlsafe(16)
    sig = _sign(_get_key(), issued_at, nonce, password)
    return f"{_VERSION}.{issued_at}.{nonce}.{sig}"


def verify(token: str, password: str, now: Optional[float] = None) -> bool:
    """Return True only if ``token`` is a valid, unexpired session for ``password``.

    Rejects malformed tokens, unknown versions, an ``issued_at`` in the future,
    tokens older than ``SESSION_MAX_AGE_S``, tokens signed for a different
    password, and tokens signed with a different server key. Never raises.
    """
    if not token or not password or not isinstance(token, str):
        return False
    parts = token.split(".")
    if len(parts) != 4:
        return False
    version, issued_at_s, nonce, sig = parts
    if version != _VERSION:
        return False
    if not _ISSUED_AT_RE.match(issued_at_s) or not _NONCE_RE.match(nonce):
        return False
    if not _SIG_RE.match(sig):
        return False
    current = time.time() if now is None else now
    issued_at = int(issued_at_s)
    if issued_at > current:
        return False
    if current - issued_at > SESSION_MAX_AGE_S:
        return False
    expected = _sign(_get_key(), issued_at_s, nonce, password)
    return hmac.compare_digest(expected.encode("ascii"), sig.encode("ascii"))
