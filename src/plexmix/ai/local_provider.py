from __future__ import annotations

import atexit
import json
import logging
import multiprocessing as mp
import os
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from .base import AIProvider

logger = logging.getLogger(__name__)


LOCAL_LLM_MODELS: Dict[str, Dict[str, Any]] = {
    "google/gemma-3-1b": {
        "display_name": "Gemma 3 1B",
        "capabilities": "1B params • ~8K ctx • CPU friendly, fast drafts",
        "context_window": 8000,
        "max_output_tokens": 768,
        "trust_remote_code": True,
        "torch_dtype": "bfloat16",
        "requires_gpu": False,
    },
    "liquid/lfm2-1.2b": {
        "display_name": "Liquid LFM-2 1.2B",
        "capabilities": "1.2B params • 32K ctx • melodic text/music reasoning",
        "context_window": 32000,
        "max_output_tokens": 1024,
        "trust_remote_code": True,
        "torch_dtype": "float16",
        "requires_gpu": False,
    },
    "NousResearch/Yarn-Mistral-7b-128k": {
        "display_name": "Yarn Mistral 7B 128K",
        "capabilities": "7B params • 128K ctx • best for long playlists",
        "context_window": 128000,
        "max_output_tokens": 2048,
        "trust_remote_code": True,
        "torch_dtype": "bfloat16",
        "requires_gpu": True,
    },
    "google/gemma-3-4b": {
        "display_name": "Gemma 3 4B",
        "capabilities": "4B params • 32K ctx • balanced quality vs speed",
        "context_window": 32000,
        "max_output_tokens": 1536,
        "trust_remote_code": True,
        "torch_dtype": "bfloat16",
        "requires_gpu": True,
    },
}

LOCAL_LLM_DEVICE = os.getenv("PLEXMIX_LOCAL_LLM_DEVICE", "auto")
LOCAL_LLM_DEFAULT_MODEL = "google/gemma-3-1b"


class LocalLLMProvider(AIProvider):
    """Local LLM provider that can use builtin HF models or a custom endpoint."""

    worker_cache: Dict[str, Dict[str, Any]] = {}

    def __init__(
        self,
        model: str,
        temperature: float = 0.7,
        mode: str = "builtin",
        endpoint: Optional[str] = None,
        auth_token: Optional[str] = None,
        max_output_tokens: int = 800,
    ):
        super().__init__(api_key=auth_token or "local", model=model, temperature=temperature)
        self.mode = mode or "builtin"
        self.endpoint = endpoint
        self.auth_token = auth_token
        self.max_output_tokens = max_output_tokens

        if self.mode == "endpoint":
            if not self.endpoint:
                raise ValueError("Local endpoint URL required when using endpoint mode")
            logger.info(f"[LocalLLM] Using custom endpoint {self.endpoint}")
        else:
            self._init_worker(model)

    def complete(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        timeout: int = 30
    ) -> str:
        """Send a prompt to local LLM and return the text response."""
        # Use provided temperature or fall back to instance default
        temp = temperature if temperature is not None else self.temperature
        tokens = min(max_tokens, self.max_output_tokens)

        # Retry with exponential backoff
        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            try:
                if self.mode == "endpoint":
                    return self._call_endpoint_with_params(prompt, temp, tokens, timeout)
                else:
                    return self._generate_with_worker_params(prompt, temp, tokens)

            except Exception as e:
                error_str = str(e).lower()
                is_retryable = any(x in error_str for x in ["timeout", "connection", "pipe"])

                if is_retryable and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"[LocalLLM] Retryable error on attempt {attempt + 1}: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                raise

        raise RuntimeError("Failed to get response from LocalLLM after retries")

    def _call_endpoint_with_params(self, prompt: str, temperature: float, max_tokens: int, timeout: int) -> str:
        """Call endpoint with specific parameters."""
        messages = [{"role": "user", "content": prompt}]
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        response = requests.post(
            self.endpoint,
            data=json.dumps(payload),
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message") or {}
            return message.get("content", "")

        return data.get("content", "") or data.get("text", "")

    def _generate_with_worker_params(self, prompt: str, temperature: float, max_tokens: int) -> str:
        """Generate with worker using specific parameters."""
        if not hasattr(self, "worker_conn"):
            raise RuntimeError("Local LLM worker not initialized")

        payload = {
            "cmd": "generate",
            "prompt": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            with self.worker_lock:
                self.worker_conn.send(payload)
                result = self.worker_conn.recv()
        except (BrokenPipeError, EOFError):
            logger.warning("[LocalLLM] Worker pipe broken. Restarting...")
            self._init_worker(self.model)
            return self._generate_with_worker_params(prompt, temperature, max_tokens)

        if result.get("status") != "ok":
            raise RuntimeError(result.get("error", "Unknown local generation error"))

        return result.get("text", "")

    def generate_playlist(
        self,
        mood_query: str,
        candidate_tracks: List[Dict[str, Any]],
        max_tracks: int = 50,
    ) -> List[int]:
        prompt = self._prepare_prompt(mood_query, candidate_tracks, max_tracks)

        try:
            if self.mode == "endpoint":
                response_text = self._call_endpoint(prompt)
            else:
                response_text = self._generate_with_worker(prompt)

            if not response_text:
                return []

            selections = self._parse_response(response_text)
            validated = self._validate_selections(selections, candidate_tracks)
            return validated[:max_tracks]
        except Exception as exc:
            logger.error(f"[LocalLLM] Failed to generate playlist: {exc}")
            return []

    # ------------------------------------------------------------------
    # Builtin worker handling
    # ------------------------------------------------------------------
    def _init_worker(self, model: str) -> None:
        model_config = LOCAL_LLM_MODELS.get(model, {})
        trust_remote_code = bool(model_config.get("trust_remote_code", False))
        torch_dtype = model_config.get("torch_dtype", "auto")
        cache_key = f"{model}-{torch_dtype}-{LOCAL_LLM_DEVICE}"

        cached = self.worker_cache.get(cache_key)
        if cached and cached["process"].is_alive():
            self.worker_conn = cached["conn"]
            self.worker_process = cached["process"]
            self.worker_lock = cached["lock"]
            logger.info(f"[LocalLLM] Reusing cached worker for {model} on {LOCAL_LLM_DEVICE}")
            return

        if cached:
            logger.warning(f"[LocalLLM] Cached worker for {model} is dead. Restarting...")

        ctx = mp.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe()
        worker = ctx.Process(
            target=_local_llm_worker,
            args=(model, trust_remote_code, torch_dtype, LOCAL_LLM_DEVICE, child_conn),
            daemon=True,
        )
        worker.start()

        try:
            handshake = parent_conn.recv()
        except EOFError as exc:
            raise RuntimeError(f"Failed to start local LLM worker for {model}: {exc}") from exc

        if handshake.get("status") != "ready":
            error = handshake.get("error", "Unknown error")
            raise RuntimeError(f"Failed to load local LLM {model}: {error}")

        self.worker_conn = parent_conn
        self.worker_process = worker
        self.worker_lock = threading.Lock()
        self.worker_cache[cache_key] = {
            "conn": parent_conn,
            "process": worker,
            "lock": self.worker_lock,
        }
        atexit.register(self._shutdown_worker, cache_key)
        logger.info(f"[LocalLLM] Local worker ready for {model}")

    def _generate_with_worker(self, prompt: str) -> str:
        if not hasattr(self, "worker_conn"):
            raise RuntimeError("Local LLM worker not initialized")

        payload = {
            "cmd": "generate",
            "prompt": prompt,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
        }

        try:
            with self.worker_lock:
                self.worker_conn.send(payload)
                result = self.worker_conn.recv()
        except (BrokenPipeError, EOFError):
            logger.warning("[LocalLLM] Worker pipe broken. Restarting...")
            self._init_worker(self.model)
            return self._generate_with_worker(prompt)

        if result.get("status") != "ok":
            raise RuntimeError(result.get("error", "Unknown local generation error"))

        return result.get("text", "")

    def _shutdown_worker(self, cache_key: str) -> None:
        worker_info = self.worker_cache.get(cache_key)
        if not worker_info:
            return

        conn = worker_info.get("conn")
        proc = worker_info.get("process")
        try:
            conn.send({"cmd": "shutdown"})
        except Exception:
            pass
        if proc.is_alive():
            proc.join(timeout=2)
        self.worker_cache.pop(cache_key, None)

    # ------------------------------------------------------------------
    # Endpoint handling
    # ------------------------------------------------------------------
    def _call_endpoint(self, prompt: str) -> str:
        messages = [
            {
                "role": "user",
                "content": prompt,
            }
        ]
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
        }

        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        response = requests.post(
            self.endpoint,
            data=json.dumps(payload),
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()

        # Support OpenAI-compatible responses by default
        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message") or {}
            return message.get("content", "")

        return data.get("content", "") or data.get("text", "")


def _local_llm_worker(
    model_name: str,
    trust_remote_code: bool,
    torch_dtype: str,
    device_preference: str,
    conn,
):
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        dtype = None
        if torch_dtype and torch_dtype != "auto":
            dtype = getattr(torch, torch_dtype)

        tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            trust_remote_code=trust_remote_code,
        )

        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token or tokenizer.eos_token_id

        device_map = "auto" if device_preference == "auto" else None
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=dtype,
            trust_remote_code=trust_remote_code,
            device_map=device_map,
        )

        if device_preference != "auto":
            device = torch.device(device_preference)
            model.to(device)
        else:
            device = next(model.parameters()).device

        conn.send({"status": "ready", "device": str(device)})

        while True:
            message = conn.recv()
            cmd = message.get("cmd")

            if cmd == "shutdown":
                break

            if cmd == "generate":
                prompt = message.get("prompt", "")
                temperature = float(message.get("temperature", 0.7))
                max_tokens = int(message.get("max_tokens", 800))

                inputs = tokenizer(prompt, return_tensors="pt", truncation=True)
                inputs = {k: v.to(device) for k, v in inputs.items()}

                generation = model.generate(
                    **inputs,
                    do_sample=True,
                    temperature=max(0.01, temperature),
                    max_new_tokens=max_tokens,
                    eos_token_id=tokenizer.eos_token_id,
                    pad_token_id=tokenizer.pad_token_id,
                    repetition_penalty=1.05,
                    top_p=0.95,
                )

                new_tokens = generation[:, inputs["input_ids"].shape[-1]:]
                text = tokenizer.batch_decode(new_tokens, skip_special_tokens=True)[0]
                conn.send({"status": "ok", "text": text})
            else:
                conn.send({"status": "error", "error": f"Unknown command {cmd}"})
    except Exception as exc:
        logger.error(f"Local LLM worker failed: {exc}", exc_info=True)
        try:
            conn.send({"status": "error", "error": str(exc)})
        except Exception:
            pass
