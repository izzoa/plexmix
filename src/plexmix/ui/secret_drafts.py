"""Process-local registry of staged (unsaved) secret values for the Settings page.

Secret inputs in the web UI are replace-only: typing a value *stages* a
replacement that is written to the credential store only on Save. The staged
value must never reach the browser and must never be pickled into Reflex's
``.states`` directory, so it cannot live in Reflex state (backend-only
underscore vars are pickled too). It lives here instead, keyed by the Reflex
client token, and the state only exposes value-free status ("replace",
"clear" or nothing).

The backend runs a single worker (no Redis), so one in-process registry is
shared by every session. Entries are dropped on save, on page load, on
provider switches, and when a session is reaped after it disconnects
(:meth:`SecretDrafts.reap`, driven by
:func:`plexmix.ui.session_liveness.sweep_disconnected_sessions`, which tracks
every token in :meth:`SecretDrafts.tokens`).

Nothing in this module logs or formats a staged value: ``repr()`` and
``str()`` report only counts, and the registry refuses to be pickled or
copied so it cannot be smuggled into persisted state by accident.
"""

from __future__ import annotations

import threading
from typing import Dict, FrozenSet, Iterable, NoReturn, Optional

ACTION_REPLACE = "replace"
ACTION_CLEAR = "clear"


class _Draft:
    """One staged change for one field. Its ``repr`` never shows the value."""

    __slots__ = ("action", "value")

    def __init__(self, action: str, value: Optional[str]) -> None:
        self.action = action
        self.value = value

    def __repr__(self) -> str:
        return f"_Draft(action={self.action!r}, value=<redacted>)"

    __str__ = __repr__

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        raise TypeError("staged secret drafts must not be pickled or copied")


class SecretDrafts:
    """Lock-guarded ``{client_token: {field: draft}}`` map of staged secrets."""

    __slots__ = ("_lock", "_drafts")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._drafts: Dict[str, Dict[str, _Draft]] = {}

    def stage(self, token: str, field: str, value: str) -> None:
        """Stage ``value`` as the replacement for ``field`` in session ``token``."""
        with self._lock:
            self._drafts.setdefault(token, {})[field] = _Draft(ACTION_REPLACE, value)

    def mark_clear(self, token: str, field: str) -> None:
        """Stage removal of the stored value of ``field`` in session ``token``."""
        with self._lock:
            self._drafts.setdefault(token, {})[field] = _Draft(ACTION_CLEAR, None)

    def get(self, token: str, field: str) -> Optional[str]:
        """Return the staged replacement value, or ``None`` (nothing staged, or a clear)."""
        with self._lock:
            draft = self._drafts.get(token, {}).get(field)
            if draft is None or draft.action != ACTION_REPLACE:
                return None
            return draft.value

    def action(self, token: str, field: str) -> str:
        """Return ``"replace"``, ``"clear"`` or ``""`` for ``field`` in session ``token``."""
        with self._lock:
            draft = self._drafts.get(token, {}).get(field)
            return draft.action if draft is not None else ""

    def staged_fields(self, token: str) -> Dict[str, str]:
        """Return ``{field: action}`` for every staged field of ``token`` (no values)."""
        with self._lock:
            return {f: d.action for f, d in self._drafts.get(token, {}).items()}

    def discard(self, token: str, field: Optional[str] = None) -> None:
        """Drop one staged field, or every staged field when ``field`` is ``None``."""
        with self._lock:
            if field is None:
                self._drafts.pop(token, None)
                return
            fields = self._drafts.get(token)
            if fields is None:
                return
            fields.pop(field, None)
            if not fields:
                self._drafts.pop(token, None)

    def tokens(self) -> FrozenSet[str]:
        """Client tokens that currently hold at least one staged field (no values)."""
        with self._lock:
            return frozenset(token for token, fields in self._drafts.items() if fields)

    def reap(self, tokens: Iterable[str]) -> None:
        """Drop every staged field of each session in ``tokens`` (e.g. disconnected tabs)."""
        with self._lock:
            for token in tokens:
                self._drafts.pop(token, None)

    def __len__(self) -> int:
        with self._lock:
            return sum(len(fields) for fields in self._drafts.values())

    def __repr__(self) -> str:
        with self._lock:
            sessions = len(self._drafts)
            staged = sum(len(fields) for fields in self._drafts.values())
        return f"SecretDrafts(sessions={sessions}, staged={staged}, values=<redacted>)"

    __str__ = __repr__

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        raise TypeError("SecretDrafts holds staged secrets and must not be pickled or copied")


secret_drafts = SecretDrafts()
