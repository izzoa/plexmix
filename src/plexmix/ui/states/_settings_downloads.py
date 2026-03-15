"""Extracted download helpers for SettingsState.

These are standalone async functions that accept the SettingsState instance
(``state``) so they can read/write state fields while keeping the main
settings_state module shorter.
"""

import asyncio
import logging
from typing import Any

from plexmix.ai.local_provider import LOCAL_LLM_MODELS, LOCAL_LLM_DEFAULT_MODEL

logger = logging.getLogger(__name__)


async def download_local_llm_impl(state: Any) -> None:
    """Download and warm up the selected local LLM model."""
    if state.ai_provider != "local" or state.ai_local_mode != "builtin":
        return

    model_name = state.ai_model or LOCAL_LLM_DEFAULT_MODEL
    model_info = LOCAL_LLM_MODELS.get(model_name, {})

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
            from transformers import AutoTokenizer, AutoModelForCausalLM
            import torch

            trust_remote_code = bool(model_info.get("trust_remote_code", False))
            tokenizer = AutoTokenizer.from_pretrained(
                model_name, trust_remote_code=trust_remote_code
            )
            model = AutoModelForCausalLM.from_pretrained(
                model_name,
                trust_remote_code=trust_remote_code,
                torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            )
            if tokenizer.pad_token is None and tokenizer.eos_token is not None:
                tokenizer.pad_token = tokenizer.eos_token
            inputs = tokenizer("Warm up playlist curation", return_tensors="pt")
            _ = model.generate(**inputs, max_new_tokens=8)

        await loop.run_in_executor(None, warmup_model)
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

            snapshot_download(model_name, local_files_only=False, resume_download=True)

        await loop.run_in_executor(None, snapshot_download_model)
        await update_status("Initializing model (first run may take a minute)...", 70)

        def warmup_model():
            from sentence_transformers import SentenceTransformer

            SentenceTransformer(model_name)

        await loop.run_in_executor(None, warmup_model)
        await update_status("✓ Model cached and ready for offline use", 100)

    except ImportError as e:
        await update_status(f"Missing dependency: {e}", 0)
    except Exception as e:
        await update_status(f"Error downloading {model_name}: {str(e)}", 0)
    finally:
        async with state:
            state.is_downloading_local_model = False
