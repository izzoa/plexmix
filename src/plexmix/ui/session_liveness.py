"""Which browser sessions currently have a live websocket, and the sweep that uses it.

:func:`connected_client_tokens` is the single adapter between PlexMix and Reflex's
connection bookkeeping. It reads ``app.event_namespace.token_to_sid`` (the client
tokens with a linked Socket.IO session), which Reflex 0.8 exposes for backward
compatibility and uses itself from state code. Recheck it on Reflex upgrades.

Any failure — no app yet, no event namespace, a renamed attribute — yields
``None`` ("liveness unknown"), and callers must then cancel nothing.

:func:`sweep_disconnected_sessions` reaps the session-scoped work (job handles in
:data:`plexmix.ui.job_manager.jobs`) and the staged secrets
(:data:`plexmix.ui.secret_drafts.secret_drafts`) of sessions that have been
disconnected for longer than ``SESSION_REAP_GRACE_S``. It runs on every page load
and, once the first page has loaded, every ``SWEEP_INTERVAL_S`` seconds on the
server's event loop (:func:`ensure_periodic_sweep`), so a closed tab is reaped
even when no other tab loads a page, and a reconnect resets its timer promptly.
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# How often the periodic sweep runs. A disconnected session is therefore reaped
# between SESSION_REAP_GRACE_S and SESSION_REAP_GRACE_S + SWEEP_INTERVAL_S after
# its first sighting, and a reconnect is noticed within SWEEP_INTERVAL_S.
SWEEP_INTERVAL_S: float = 30.0

# The resolved ``rx.App``. ``prerequisites.get_app`` prepends cwd to ``sys.path``
# on every call, so the app is looked up once per process (as Reflex itself does
# with ``State._app_ref``); the namespace and its token map are read fresh.
_app: Any = None

# The event loop the periodic sweep is scheduled on (``None`` until the first
# page load on a running loop).
_sweep_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_app() -> Any:
    """Return the running Reflex app instance, resolving it once per process."""
    global _app
    if _app is None:
        from reflex.utils import prerequisites

        _app = prerequisites.get_and_validate_app().app
    return _app


def connected_client_tokens() -> Optional[frozenset[str]]:
    """Return the client tokens that currently have a connected websocket.

    Returns ``None`` when liveness cannot be determined, so that no
    session-scoped work is reaped on a guess.
    """
    try:
        namespace = _get_app().event_namespace
        if namespace is None:
            return None
        token_to_sid = namespace.token_to_sid
        if not isinstance(token_to_sid, Mapping):
            logger.debug("Unexpected token_to_sid type %s; liveness unknown", type(token_to_sid))
            return None
        return frozenset(str(token) for token in token_to_sid)
    except Exception:
        logger.debug("Session liveness unavailable; not reaping any session", exc_info=True)
        return None


def sweep_disconnected_sessions() -> List[str]:
    """Reap the work and staged secrets of sessions disconnected beyond the grace period.

    Every session that owns a job handle *or* a staged secret draft is considered,
    so a Settings tab closed with an unsaved key is reaped too. Activity from one
    session never cancels another's work, and nothing is reaped while liveness is
    unknown. Returns the reaped client tokens.
    """
    from plexmix.ui.job_manager import jobs
    from plexmix.ui.secret_drafts import secret_drafts

    reaped = jobs.reap_disconnected(
        connected_client_tokens(), time.monotonic(), extra_tokens=secret_drafts.tokens()
    )
    if reaped:
        # A reaped session is gone for good: drop any secrets it staged but never saved.
        secret_drafts.reap(reaped)
        logger.debug("Reaped the work of %d disconnected session(s)", len(reaped))
    return reaped


def ensure_periodic_sweep(interval_s: float = SWEEP_INTERVAL_S) -> bool:
    """Schedule :func:`sweep_disconnected_sessions` every *interval_s* seconds.

    Runs on the event loop of the calling thread, once per loop: later calls are
    no-ops. Without a running loop (unit tests, executor threads) nothing is
    scheduled. Returns True when this call scheduled the sweep.
    """
    global _sweep_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    if _sweep_loop is loop and not loop.is_closed():
        return False
    _sweep_loop = loop
    loop.call_later(interval_s, _periodic_sweep, loop, interval_s)
    return True


def _periodic_sweep(loop: asyncio.AbstractEventLoop, interval_s: float) -> None:
    """One periodic sweep; reschedules itself while *loop* is still the sweep loop."""
    try:
        sweep_disconnected_sessions()
    except Exception:
        logger.debug("Periodic session sweep failed", exc_info=True)
    finally:
        if _sweep_loop is loop and not loop.is_closed():
            loop.call_later(interval_s, _periodic_sweep, loop, interval_s)
