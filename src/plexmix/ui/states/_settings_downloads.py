"""Extracted download helpers for SettingsState.

These are standalone async functions that accept the SettingsState instance
(``state``) so they can read/write state fields while keeping the main
settings_state module shorter.

The server process never loads a local model runtime (torch, transformers,
sentence-transformers): the warm-up step of each download runs the model in a
short-lived worker process (``warm_up_local_llm`` and ``probe_local_embedding_model``).
Both are time-limited and raise the built-in ``TimeoutError`` when the worker does
not answer, which these handlers word for a local model (never "check the server URL").
"""

import asyncio
import logging
from typing import Any

from plexmix.ai.local_provider import LOCAL_LLM_DEFAULT_MODEL, warm_up_local_llm

logger = logging.getLogger(__name__)

_TIMED_OUT_ADVICE = "The files are downloaded; try again, or pick a smaller model."


async def download_local_llm_impl(state: Any) -> None:
    """Download and warm up the selected local LLM model."""
    if state.ai_provider != "local" or state.ai_local_mode != "builtin":
        return

    model_name = state.ai_model or LOCAL_LLM_DEFAULT_MODEL

    async with state:
        state.is_downloading_local_llm = True
        state.local_llm_download_status = f"Preparing download for {model_name}..."
        state.local_llm_download_progress = 5

    async def update_status(message: str, progress: int):
        async with state:
            state.local_llm_download_status = message
            state.local_llm_download_progress = progress

    try:
        await update_status("Checking local cache...", 10)
        loop = asyncio.get_running_loop()

        def snapshot_download_model():
            from huggingface_hub import snapshot_download

            snapshot_download(model_name, local_files_only=False, resume_download=True)

        await loop.run_in_executor(None, snapshot_download_model)
        await update_status("Initializing model (first warmup may take a while)...", 55)

        def warmup_model():
            # In a short-lived worker process: the server never imports torch.
            warm_up_local_llm(model_name)

        try:
            await loop.run_in_executor(None, warmup_model)
        except TimeoutError as e:
            # Only the warm-up's own time limit: a network timeout from the download
            # step above is not caught here and keeps its "Error downloading" text.
            await update_status(f"Timed out: {e}. {_TIMED_OUT_ADVICE}", 0)
            return
        await update_status("✓ Model cached and ready for offline use", 100)

    except ImportError as e:
        await update_status(f"Missing dependency: {e}", 0)
    except Exception as e:
        await update_status(f"Error downloading {model_name}: {str(e)}", 0)
    finally:
        async with state:
            state.is_downloading_local_llm = False


async def download_local_embedding_impl(state: Any) -> None:
    """Download and warm up the selected local embedding model."""
    if state.embedding_provider != "local":
        return

    model_name = state.embedding_model or "all-MiniLM-L6-v2"
    # huggingface_hub.snapshot_download needs a full repo id. A bare
    # sentence-transformers model name (no "/", e.g. the default all-MiniLM-L6-v2)
    # lives under the sentence-transformers org; without this prefix the download
    # 404'd even though SentenceTransformer() resolves the bare name itself.
    repo_id = model_name if "/" in model_name else f"sentence-transformers/{model_name}"
    async with state:
        state.is_downloading_local_model = True
        state.local_download_status = f"Preparing download for {model_name}..."
        state.local_download_progress = 5

    async def update_status(message: str, progress: int):
        async with state:
            state.local_download_status = message
            state.local_download_progress = progress

    try:
        await update_status("Checking local cache...", 15)
        loop = asyncio.get_running_loop()

        def snapshot_download_model():
            from huggingface_hub import snapshot_download

            snapshot_download(repo_id, local_files_only=False, resume_download=True)

        await loop.run_in_executor(None, snapshot_download_model)
        await update_status("Initializing model (first run may take a minute)...", 70)

        def warmup_model():
            # In a short-lived worker process: the server never imports torch.
            from plexmix.utils.embeddings import probe_local_embedding_model

            probe_local_embedding_model(model_name)

        try:
            await loop.run_in_executor(None, warmup_model)
        except TimeoutError as e:
            await update_status(f"Timed out: {e}. {_TIMED_OUT_ADVICE}", 0)
            return
        await update_status("✓ Model cached and ready for offline use", 100)

    except ImportError as e:
        await update_status(f"Missing dependency: {e}", 0)
    except Exception as e:
        await update_status(f"Error downloading {model_name}: {str(e)}", 0)
    finally:
        async with state:
            state.is_downloading_local_model = False
