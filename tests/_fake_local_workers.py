"""Fake local-model worker functions for the worker tests (standard library only).

A test patches ``plexmix.utils.embeddings._local_embedding_worker`` or
``plexmix.ai.local_provider._local_llm_worker`` with one of these, so the real starter
spawns a real child process that runs the fake. No model is loaded, and the child imports
nothing but the standard library (it never imports PlexMix, torch or FAISS).

Each function is a module-level function (a spawned child finds it by name) with the
signature of the worker it stands in for:

* embedding worker: ``(model_name, trust_remote_code, device, conn)``
* local LLM worker: ``(model_name, trust_remote_code, torch_dtype, device_preference, conn)``
"""

from __future__ import annotations

import signal
import time
from typing import Any

# How long a fake that is meant to hang sleeps. The test stops the child long before this;
# the child is a daemon, so it never outlives the test run by much if something goes wrong.
_HANG_SECONDS = 120.0


# ── embedding worker fakes ────────────────────────────────────────────────


def embedding_ok(model_name: str, trust_remote_code: bool, device: str, conn: Any) -> None:
    """Send "ready" (dimension 3), answer "embed" with 3-number vectors, stop on "shutdown"."""
    conn.send({"status": "ready", "dimension": 3})
    while True:
        message = conn.recv()
        cmd = message.get("cmd")
        if cmd == "shutdown":
            break
        if cmd == "embed":
            conn.send({"status": "ok", "embeddings": [[0.1, 0.2, 0.3] for _ in message["texts"]]})
        else:
            conn.send({"status": "error", "error": f"Unknown command: {cmd}"})


def embedding_load_error(model_name: str, trust_remote_code: bool, device: str, conn: Any) -> None:
    """Send a load error as the handshake."""
    conn.send({"status": "error", "error": "no such model file"})


def embedding_missing_module(
    model_name: str, trust_remote_code: bool, device: str, conn: Any
) -> None:
    """Send an import failure for ``torch`` as the handshake."""
    conn.send(
        {
            "status": "error",
            "error": "No module named 'torch'",
            "missing_module": "torch",
        }
    )


def embedding_exits_silently(
    model_name: str, trust_remote_code: bool, device: str, conn: Any
) -> None:
    """Return without sending anything."""


def embedding_sleeps_before_ready(
    model_name: str, trust_remote_code: bool, device: str, conn: Any
) -> None:
    """Sleep instead of sending "ready"."""
    time.sleep(_HANG_SECONDS)


def embedding_ready_then_silent(
    model_name: str, trust_remote_code: bool, device: str, conn: Any
) -> None:
    """Send "ready", then read requests and never answer."""
    conn.send({"status": "ready", "dimension": 3})
    conn.recv()
    time.sleep(_HANG_SECONDS)


def embedding_ready_then_exits(
    model_name: str, trust_remote_code: bool, device: str, conn: Any
) -> None:
    """Send "ready", read one request, then exit without answering."""
    conn.send({"status": "ready", "dimension": 3})
    conn.recv()


def embedding_ignores_terminate(
    model_name: str, trust_remote_code: bool, device: str, conn: Any
) -> None:
    """Ignore SIGTERM, send "ready", then read a request and never answer."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    conn.send({"status": "ready", "dimension": 3})
    conn.recv()
    time.sleep(_HANG_SECONDS)


# ── local LLM worker fakes ────────────────────────────────────────────────


def llm_ok(
    model_name: str,
    trust_remote_code: bool,
    torch_dtype: str,
    device_preference: str,
    conn: Any,
) -> None:
    """Send "ready", answer "generate" with a short text, stop on "shutdown"."""
    conn.send({"status": "ready", "device": "cpu"})
    while True:
        message = conn.recv()
        cmd = message.get("cmd")
        if cmd == "shutdown":
            break
        if cmd == "generate":
            conn.send({"status": "ok", "text": "ok"})
        else:
            conn.send({"status": "error", "error": f"Unknown command {cmd}"})


def llm_load_error(
    model_name: str,
    trust_remote_code: bool,
    torch_dtype: str,
    device_preference: str,
    conn: Any,
) -> None:
    """Send a load error as the handshake."""
    conn.send({"status": "error", "error": "no weights found"})


def llm_generate_error(
    model_name: str,
    trust_remote_code: bool,
    torch_dtype: str,
    device_preference: str,
    conn: Any,
) -> None:
    """Send "ready", then answer the first request with an error."""
    conn.send({"status": "ready", "device": "cpu"})
    conn.recv()
    conn.send({"status": "error", "error": "generation exploded"})


def llm_exits_silently(
    model_name: str,
    trust_remote_code: bool,
    torch_dtype: str,
    device_preference: str,
    conn: Any,
) -> None:
    """Return without sending anything."""


def llm_sleeps_before_ready(
    model_name: str,
    trust_remote_code: bool,
    torch_dtype: str,
    device_preference: str,
    conn: Any,
) -> None:
    """Sleep instead of sending "ready"."""
    time.sleep(_HANG_SECONDS)


def llm_ready_then_silent(
    model_name: str,
    trust_remote_code: bool,
    torch_dtype: str,
    device_preference: str,
    conn: Any,
) -> None:
    """Send "ready", then read a request and never answer."""
    conn.send({"status": "ready", "device": "cpu"})
    conn.recv()
    time.sleep(_HANG_SECONDS)


def llm_ready_then_exits(
    model_name: str,
    trust_remote_code: bool,
    torch_dtype: str,
    device_preference: str,
    conn: Any,
) -> None:
    """Send "ready", read one request, then exit without answering."""
    conn.send({"status": "ready", "device": "cpu"})
    conn.recv()
