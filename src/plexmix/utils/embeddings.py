from typing import Any, List, Optional, Tuple, Union, Dict
import importlib.util
import logging
import time
import re
import os
import multiprocessing as mp
import multiprocessing.connection as mp_connection
import threading
import atexit
from abc import ABC, abstractmethod

import numpy as np

from plexmix.config.credentials import sanitize_credential_value, validate_api_key

logger = logging.getLogger(__name__)

LOCAL_EMBEDDING_MODELS: Dict[str, Dict[str, Union[int, bool]]] = {
    "all-MiniLM-L6-v2": {"dimension": 384, "trust_remote_code": False},
    "mixedbread-ai/mxbai-embed-large-v1": {"dimension": 1024, "trust_remote_code": False},
    "google/embeddinggemma-300m": {"dimension": 768, "trust_remote_code": False},
    "nomic-ai/nomic-embed-text-v1.5": {"dimension": 768, "trust_remote_code": True},
}

LOCAL_EMBEDDING_DEVICE = os.getenv("PLEXMIX_LOCAL_EMBEDDING_DEVICE", "cpu")

_LOCAL_DEPS_MISSING = "sentence-transformers not installed. Run: pip install sentence-transformers"

# Time limits for the short-lived checks behind the Settings buttons (``probe_local_embedding_model``).
# The long-lived worker that Generate and the embedding jobs share keeps no limit, so a slow
# first model download during a job is not cut off. Chosen, not measured: 10 minutes leaves room
# for a first load from a slow disk, 5 minutes for one answer.
LOCAL_MODEL_READY_TIMEOUT = 600.0
LOCAL_MODEL_ANSWER_TIMEOUT = 300.0
# How long a worker child gets to leave by itself before it is terminated, and again before
# it is killed.
_WORKER_STOP_WAIT = 5.0


def local_embedding_deps_available() -> bool:
    """Whether ``sentence_transformers`` is installed, WITHOUT importing it.

    Local models run in a worker process (``_local_embedding_worker``). Importing the
    package in the calling process loads torch's and scikit-learn's OpenMP runtimes
    next to FAISS's (a segfault on macOS) and blocks for 10 s or more, so callers
    in the CLI/UI server process must only ever ask ``find_spec``.
    """
    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        return False


class EmbeddingProvider(ABC):
    def __init__(self) -> None:
        self.provider_name = self.__class__.__name__.replace("EmbeddingProvider", "")

    @abstractmethod
    def generate_embedding(self, text: str) -> List[float]:
        pass

    @abstractmethod
    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 100
    ) -> List[List[float]]:
        pass

    @abstractmethod
    def get_dimension(self) -> int:
        pass


class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "gemini-embedding-001"):
        super().__init__()
        try:
            from google import genai

            self.client = genai.Client(api_key=api_key)
            self.model_name = model
            self.dimension = 3072
            logger.info(f"[{self.provider_name}] Initialized embedding provider with model {model}")
        except ImportError:
            raise ImportError("google-genai not installed. Run: pip install google-genai")

    def generate_embedding(self, text: str) -> List[float]:
        max_retries = 5
        base_delay = 2
        backoff_multiplier = 1.5

        for attempt in range(max_retries):
            try:
                result = self.client.models.embed_content(
                    model=self.model_name,
                    contents=text,
                )
                if result.embeddings is None:
                    raise RuntimeError("Gemini returned no embeddings")
                return list(result.embeddings[0].values)  # type: ignore[arg-type]
            except Exception as e:
                error_str = str(e)

                if (
                    "429" in error_str
                    or "quota" in error_str.lower()
                    or "rate" in error_str.lower()
                ):
                    if attempt < max_retries - 1:
                        retry_after = self._extract_retry_delay(error_str)

                        if retry_after:
                            delay = retry_after * backoff_multiplier
                            logger.warning(
                                f"[{self.provider_name}] Rate limit hit. Server suggested {retry_after}s, using {delay:.1f}s with backoff..."
                            )
                        else:
                            delay = base_delay * (2**attempt)
                            logger.warning(
                                f"[{self.provider_name}] Rate limit hit. Retrying in {delay}s..."
                            )

                        time.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"[{self.provider_name}] Rate limit exceeded after {max_retries} attempts: {e}"
                        )
                        raise
                else:
                    logger.error(f"[{self.provider_name}] Failed to generate embedding: {e}")
                    raise

        raise Exception("Max retries exceeded")

    def _extract_retry_delay(self, error_message: str) -> Optional[float]:
        retry_match = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", error_message)
        if retry_match:
            return float(retry_match.group(1))

        retry_after_match = re.search(r"Retry-After:\s*(\d+)", error_message, re.IGNORECASE)
        if retry_after_match:
            return float(retry_after_match.group(1))

        return None

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 100
    ) -> List[List[float]]:
        embeddings = []
        total_batches = (len(texts) + batch_size - 1) // batch_size
        max_retries = 5
        base_delay = 2
        backoff_multiplier = 1.5

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_num = i // batch_size + 1

            for attempt in range(max_retries):
                try:
                    logger.debug(
                        f"[{self.provider_name}] Generating embeddings batch {batch_num}/{total_batches} ({len(batch)} texts)"
                    )

                    result = self.client.models.embed_content(
                        model=self.model_name,
                        contents=batch,  # type: ignore[arg-type]
                    )
                    if result.embeddings is None:
                        raise RuntimeError("Gemini returned no embeddings for batch")
                    batch_embeddings = [list(emb.values) for emb in result.embeddings]  # type: ignore[arg-type]
                    embeddings.extend(batch_embeddings)

                    logger.debug(
                        f"[{self.provider_name}] Completed batch {batch_num}/{total_batches}"
                    )
                    break
                except Exception as e:
                    error_str = str(e)

                    if (
                        "429" in error_str
                        or "quota" in error_str.lower()
                        or "rate" in error_str.lower()
                    ):
                        if attempt < max_retries - 1:
                            retry_after = self._extract_retry_delay(error_str)

                            if retry_after:
                                delay = retry_after * backoff_multiplier
                                logger.warning(
                                    f"[{self.provider_name}] Rate limit hit on batch {batch_num}. Server suggested {retry_after}s, using {delay:.1f}s with backoff..."
                                )
                            else:
                                delay = base_delay * (2**attempt)
                                logger.warning(
                                    f"[{self.provider_name}] Rate limit hit on batch {batch_num} (attempt {attempt + 1}/{max_retries}). Retrying in {delay}s..."
                                )

                            time.sleep(delay)
                            continue
                        else:
                            logger.error(
                                f"[{self.provider_name}] Rate limit exceeded for batch {batch_num} after {max_retries} attempts: {e}"
                            )
                            raise
                    else:
                        logger.error(
                            f"[{self.provider_name}] Failed to generate batch embeddings (batch {batch_num}): {e}"
                        )
                        raise

        return embeddings

    def get_dimension(self) -> int:
        return self.dimension


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        super().__init__()
        try:
            from openai import OpenAI

            self.client = OpenAI(api_key=api_key)
            self.model_name = model
            self.dimension = 1536
            logger.info(f"[{self.provider_name}] Initialized embedding provider with model {model}")
        except ImportError:
            raise ImportError("openai not installed. Run: pip install openai")

    def generate_embedding(self, text: str) -> List[float]:
        try:
            response = self.client.embeddings.create(model=self.model_name, input=text)
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"[{self.provider_name}] Failed to generate embedding: {e}")
            raise

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 100
    ) -> List[List[float]]:
        embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                response = self.client.embeddings.create(model=self.model_name, input=batch)
                batch_embeddings = [item.embedding for item in response.data]
                embeddings.extend(batch_embeddings)
                logger.debug(
                    f"[{self.provider_name}] Generated {len(batch)} embeddings (batch {i//batch_size + 1})"
                )
            except Exception as e:
                logger.error(f"[{self.provider_name}] Failed to generate batch embeddings: {e}")
                raise

        return embeddings

    def get_dimension(self) -> int:
        return self.dimension


class CohereEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "embed-v4.0", output_dimension: int = 1024):
        super().__init__()
        try:
            import cohere

            self.client = cohere.ClientV2(api_key=api_key)
            self.model_name = model
            self.dimension = output_dimension
            logger.info(
                f"[{self.provider_name}] Initialized embedding provider with model {model} (dim: {output_dimension})"
            )
        except ImportError:
            raise ImportError("cohere not installed. Run: pip install cohere")

    def _embed_kwargs(self, texts: List[str]) -> Dict[str, Any]:
        """Build kwargs for client.embed(), adding output_dimension only for v4+ models."""
        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "texts": texts,
            "input_type": "search_document",
            "embedding_types": ["float"],
        }
        if "v4" in self.model_name:
            kwargs["output_dimension"] = self.dimension
        return kwargs

    def generate_embedding(self, text: str) -> List[float]:
        try:
            response = self.client.embed(**self._embed_kwargs([text]))
            if response.embeddings.float_ is None:
                raise RuntimeError("Cohere returned no float embeddings")
            return response.embeddings.float_[0]
        except Exception as e:
            logger.error(f"[{self.provider_name}] Failed to generate embedding: {e}")
            raise

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 96
    ) -> List[List[float]]:
        embeddings: List[List[float]] = []
        total_batches = (len(texts) + batch_size - 1) // batch_size

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_num = i // batch_size + 1

            try:
                logger.debug(
                    f"[{self.provider_name}] Generating embeddings batch {batch_num}/{total_batches} ({len(batch)} texts)"
                )
                response = self.client.embed(**self._embed_kwargs(batch))
                if response.embeddings.float_ is None:
                    raise RuntimeError("Cohere returned no float embeddings for batch")
                embeddings.extend(response.embeddings.float_)
                logger.debug(f"[{self.provider_name}] Completed batch {batch_num}/{total_batches}")
            except Exception as e:
                logger.error(
                    f"[{self.provider_name}] Failed to generate batch embeddings (batch {batch_num}): {e}"
                )
                raise

        return embeddings

    def get_dimension(self) -> int:
        return self.dimension


class LocalEmbeddingProvider(EmbeddingProvider):
    worker_cache: Dict[str, Dict[str, Any]] = {}
    # Serializes worker acquisition so concurrent first-inits can't each spawn a
    # duplicate model process (check-then-act race on worker_cache).
    _cache_lock: threading.Lock = threading.Lock()

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        super().__init__()
        # find_spec only: the package is imported in the worker process, never here.
        if not local_embedding_deps_available():
            raise ImportError(_LOCAL_DEPS_MISSING)

        model_config = LOCAL_EMBEDDING_MODELS.get(model_name, {})
        trust_remote_code = bool(model_config.get("trust_remote_code", False))
        device = LOCAL_EMBEDDING_DEVICE
        cache_key = f"{model_name}-{trust_remote_code}-{device}"

        with LocalEmbeddingProvider._cache_lock:
            cached = self.worker_cache.get(cache_key)
            if cached and cached["process"].is_alive():  # type: ignore[union-attr]
                logger.info(
                    f"[{self.provider_name}] Reusing cached local worker for {model_name} on {device}"
                )
                self.worker_conn: mp_connection.Connection = cached["conn"]
                self.worker_process: mp.process.BaseProcess = cached["process"]
                self.dimension: int = cached["dimension"]
                self.worker_lock: threading.Lock = cached["lock"]
            else:
                if cached:
                    logger.warning(
                        f"[{self.provider_name}] Cached worker for {model_name} is not alive. Restarting..."
                    )

                # Dispose workers for other models before starting a new one — only
                # one embedding model is active at a time, so stale workers from a
                # provider/model switch would otherwise leak loaded processes.
                for stale_key in [k for k in list(self.worker_cache) if k != cache_key]:
                    logger.info(f"[{self.provider_name}] Disposing stale local worker {stale_key}")
                    self._shutdown_worker(stale_key)

                parent_conn, worker, reported_dimension = _start_local_embedding_worker(
                    model_name, trust_remote_code, device
                )

                self.worker_conn = parent_conn
                self.worker_process = worker
                self.dimension = reported_dimension or int(model_config.get("dimension", 384))
                self.worker_lock = threading.Lock()
                self.worker_cache[cache_key] = {
                    "conn": self.worker_conn,
                    "process": self.worker_process,
                    "dimension": self.dimension,
                    "lock": self.worker_lock,
                }

                logger.info(
                    f"[{self.provider_name}] Local embedding worker ready for {model_name} "
                    f"(dim: {self.dimension}, device={device})"
                )

                atexit.register(self._shutdown_worker, cache_key)

    def generate_embedding(self, text: str) -> List[float]:
        try:
            with self.worker_lock:
                self.worker_conn.send({"cmd": "embed", "texts": [text]})
                result = self.worker_conn.recv()
            if result.get("status") != "ok":
                raise RuntimeError(result.get("error", "Unknown error generating embedding"))
            embedding = np.array(result["embeddings"][0], dtype=np.float32)
            truncated: List[float] = self._truncate_vector(embedding).tolist()
            return truncated
        except Exception as e:
            logger.error(f"[{self.provider_name}] Failed to generate embedding: {e}")
            raise

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 32
    ) -> List[List[float]]:
        try:
            with self.worker_lock:
                self.worker_conn.send({"cmd": "embed", "texts": texts, "batch_size": batch_size})
                result = self.worker_conn.recv()
            if result.get("status") != "ok":
                raise RuntimeError(result.get("error", "Unknown error generating batch embeddings"))
            np_embeddings = np.array(result["embeddings"], dtype=np.float32)
            np_embeddings = self._truncate_batch(np_embeddings)
            result_list: List[List[float]] = np_embeddings.tolist()
            return result_list
        except Exception as e:
            logger.error(f"[{self.provider_name}] Failed to generate batch embeddings: {e}")
            raise

    def get_dimension(self) -> int:
        return self.dimension

    def _truncate_vector(self, vector: np.ndarray) -> np.ndarray:
        if self.dimension and vector.shape[-1] > self.dimension:
            return vector[: self.dimension]
        return vector

    def _truncate_batch(self, embeddings: np.ndarray) -> np.ndarray:
        if self.dimension and embeddings.shape[-1] > self.dimension:
            return embeddings[:, : self.dimension]
        return embeddings

    def _shutdown_worker(self, cache_key: str) -> None:
        worker_info = self.worker_cache.get(cache_key)
        if not worker_info:
            return
        conn: Optional[mp_connection.Connection] = worker_info.get("conn")
        process: Optional[mp.process.BaseProcess] = worker_info.get("process")  # type: ignore[assignment]
        try:
            if conn is not None:
                conn.send({"cmd": "shutdown"})
        except Exception:
            pass
        if process is not None and process.is_alive():
            process.join(timeout=2)
        self.worker_cache.pop(cache_key, None)


def stop_worker_process(
    conn: mp_connection.Connection, process: mp.process.BaseProcess, *, wait: bool = True
) -> None:
    """Stop a worker child process and release its pipe. Never raises.

    ``wait=True`` is for a child that is expected to leave on its own (it was told to shut
    down, or it has already sent its last message): it gets up to 5 s. ``wait=False`` is for
    a child that is stuck (loading or computing for too long): it is terminated at once.
    A child that is still alive after that is killed.
    """
    if wait:
        try:
            conn.send({"cmd": "shutdown"})
        except Exception:
            pass
        process.join(timeout=_WORKER_STOP_WAIT)
    try:
        if process.is_alive():
            process.terminate()
            process.join(timeout=_WORKER_STOP_WAIT)
        if process.is_alive():
            process.kill()
            process.join(timeout=_WORKER_STOP_WAIT)
    except Exception as exc:
        logger.warning(f"Could not stop worker process cleanly: {type(exc).__name__}")
    try:
        conn.close()
    except Exception:
        pass


def receive_worker_reply(
    conn: mp_connection.Connection,
    process: mp.process.BaseProcess,
    limit: Optional[float],
    timeout_message: str,
) -> Any:
    """Read one message from a worker, waiting at most ``limit`` seconds.

    ``limit=None`` waits as ``conn.recv()`` does. When the limit passes without a message the
    child is stopped, its pipe is closed and the built-in ``TimeoutError`` is raised with
    ``timeout_message``. ``EOFError`` and ``ConnectionError`` (the child died) propagate; the
    caller words them. ``TimeoutError`` is an ``OSError``: callers that catch ``OSError``
    around this call would swallow it, so they catch ``ConnectionError`` instead.
    """
    if limit is not None and not conn.poll(limit):
        stop_worker_process(conn, process, wait=False)
        raise TimeoutError(timeout_message)
    return conn.recv()


def _start_local_embedding_worker(
    model_name: str,
    trust_remote_code: bool,
    device: str,
    ready_timeout: Optional[float] = None,
) -> Tuple[mp_connection.Connection, mp.process.BaseProcess, int]:
    """Start a worker process for ``model_name`` and wait until its model is loaded.

    Returns ``(connection, process, dimension)``; ``dimension`` is 0 when the worker
    did not report one. Raises ``ImportError`` when the worker could not import
    sentence-transformers (installed but broken, for example a missing torch),
    ``RuntimeError`` when the model failed to load or the worker died and ``TimeoutError``
    when ``ready_timeout`` seconds pass without an answer (the child is stopped first).
    ``ready_timeout=None`` waits as long as the worker takes, which is what the long-lived
    provider does. On every failure the child is joined and the pipe is closed.
    """
    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe()
    worker = ctx.Process(
        target=_local_embedding_worker,
        args=(model_name, trust_remote_code, device, child_conn),
        daemon=True,
    )
    try:
        worker.start()
    except BaseException:
        parent_conn.close()
        child_conn.close()
        raise
    # Close the parent's copy of the child end so recv() raises EOFError
    # if the worker dies instead of blocking forever holding worker_lock.
    child_conn.close()

    timeout_message = (
        ""
        if ready_timeout is None
        else f"Local model {model_name} did not finish loading within {ready_timeout:g} seconds"
    )
    try:
        handshake = receive_worker_reply(parent_conn, worker, ready_timeout, timeout_message)
    except (EOFError, ConnectionError):
        stop_worker_process(parent_conn, worker)
        raise RuntimeError(
            f"Failed to start local embedding worker for {model_name}. See logs for details."
        )

    if handshake.get("status") != "ready":
        stop_worker_process(parent_conn, worker)
        error = handshake.get("error", "Unknown error")
        if "missing_module" in handshake:
            raise ImportError(f"{_LOCAL_DEPS_MISSING} ({error})", name=handshake["missing_module"])
        raise RuntimeError(f"Failed to load local model {model_name}: {error}")

    return parent_conn, worker, int(handshake.get("dimension", 0) or 0)


def probe_local_embedding_model(model_name: str, text: Optional[str] = None) -> int:
    """Load ``model_name`` in a throwaway worker process and return its vector size.

    For the Settings "Test Embeddings" and "Download / Cache Model" buttons, which used to
    load the model in the server process. With ``text`` the model also embeds it once and the
    length of that vector is returned. The worker is always stopped, and the shared worker
    cache (``LocalEmbeddingProvider.worker_cache``) is never read or written.

    Raises ``ImportError`` (package missing or broken, no process started for a missing
    one), ``RuntimeError`` (the model failed to load, or the worker died or failed to answer)
    and ``TimeoutError`` (no "ready" within ``LOCAL_MODEL_READY_TIMEOUT`` or no answer within
    ``LOCAL_MODEL_ANSWER_TIMEOUT``; the child is stopped first).
    """
    if not local_embedding_deps_available():
        raise ImportError(_LOCAL_DEPS_MISSING)
    trust_remote_code = bool(LOCAL_EMBEDDING_MODELS.get(model_name, {}).get("trust_remote_code"))
    conn, worker, dimension = _start_local_embedding_worker(
        model_name,
        trust_remote_code,
        LOCAL_EMBEDDING_DEVICE,
        ready_timeout=LOCAL_MODEL_READY_TIMEOUT,
    )
    try:
        if text is not None:
            try:
                conn.send({"cmd": "embed", "texts": [text]})
                result = receive_worker_reply(
                    conn,
                    worker,
                    LOCAL_MODEL_ANSWER_TIMEOUT,
                    f"Local model {model_name} did not answer within "
                    f"{LOCAL_MODEL_ANSWER_TIMEOUT:g} seconds",
                )
            except (EOFError, ConnectionError):
                raise RuntimeError(f"Local embedding worker for {model_name} stopped unexpectedly")
            if result.get("status") != "ok":
                raise RuntimeError(result.get("error", "Unknown error generating embedding"))
            dimension = len(result["embeddings"][0])
        return dimension
    finally:
        stop_worker_process(conn, worker)


def _local_embedding_worker(
    model_name: str, trust_remote_code: bool, device: str, conn: mp_connection.Connection
) -> None:
    try:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found,import-untyped]
        except ImportError as e:
            # find_spec found the package but it will not import here (broken or
            # partial install): say so, instead of "the model failed to load".
            logger.error(f"Local embedding worker could not import sentence-transformers: {e}")
            conn.send({"status": "error", "error": str(e), "missing_module": e.name or ""})
            return

        model = SentenceTransformer(model_name, trust_remote_code=trust_remote_code, device=device)
        dimension = model.get_sentence_embedding_dimension()
        conn.send({"status": "ready", "dimension": dimension})

        while True:
            message = conn.recv()
            cmd = message.get("cmd")
            if cmd == "shutdown":
                break
            if cmd == "embed":
                texts = message.get("texts", [])
                batch_size = message.get("batch_size", 32)
                embeddings = model.encode(
                    texts,
                    batch_size=batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )
                conn.send({"status": "ok", "embeddings": embeddings.tolist()})
            else:
                conn.send({"status": "error", "error": f"Unknown command: {cmd}"})
    except Exception as e:
        logger.error(f"Local embedding worker failed for {model_name}: {e}", exc_info=True)
        try:
            conn.send({"status": "error", "error": str(e)})
        except Exception:
            pass


class CustomEmbeddingProvider(EmbeddingProvider):
    """Embedding provider for any OpenAI-compatible API endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        dimension: int,
        api_key: Optional[str] = None,
    ):
        super().__init__()
        self.provider_name = "Custom"
        try:
            from openai import OpenAI

            self.client = OpenAI(base_url=base_url, api_key=api_key or "no-key-required")
            self.model_name = model
            self.dimension = dimension
            self._dim_verified = False
            logger.info(
                f"[{self.provider_name}] Initialized custom embedding provider: "
                f"{base_url} model={model} dim={dimension}"
            )
        except ImportError:
            raise ImportError("openai not installed. Run: pip install openai")

    def _verify_dimension(self, vector: List[float]) -> None:
        """Fail fast if the endpoint's real dimension differs from the configured
        one. Otherwise mismatched vectors are stored and only surface as a cryptic
        crash when the FAISS index is built."""
        actual = len(vector)
        if actual != self.dimension:
            raise ValueError(
                f"Custom embedding endpoint returned {actual}-dimensional vectors, but "
                f"the configured custom embedding dimension is {self.dimension}. Set the "
                f"custom embedding dimension to {actual} in Settings and regenerate embeddings."
            )
        self._dim_verified = True

    def generate_embedding(self, text: str) -> List[float]:
        try:
            response = self.client.embeddings.create(
                model=self.model_name,
                input=text,
            )
            vector = response.data[0].embedding
            if not self._dim_verified:
                self._verify_dimension(vector)
            return vector
        except Exception as e:
            logger.error(f"[{self.provider_name}] Failed to generate embedding: {e}")
            raise

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 100
    ) -> List[List[float]]:
        embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                response = self.client.embeddings.create(
                    model=self.model_name,
                    input=batch,
                )
                batch_embeddings = [item.embedding for item in response.data]
                if batch_embeddings and not self._dim_verified:
                    self._verify_dimension(batch_embeddings[0])
                embeddings.extend(batch_embeddings)
                logger.debug(
                    f"[{self.provider_name}] Generated {len(batch)} embeddings "
                    f"(batch {i // batch_size + 1})"
                )
            except Exception as e:
                logger.error(f"[{self.provider_name}] Failed to generate batch embeddings: {e}")
                raise
        return embeddings

    def get_dimension(self) -> int:
        return self.dimension


class EmbeddingGenerator:
    def __init__(
        self,
        provider: str,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        custom_endpoint: Optional[str] = None,
        custom_api_key: Optional[str] = None,
        custom_dimension: int = 1536,
    ):
        self.provider_name = provider.lower()

        # Normalize and validate the key that will actually be used, so a
        # transport-unsafe key fails here with a clear message instead of a
        # cryptic error inside the SDK client.
        api_key = sanitize_credential_value(api_key)
        custom_api_key = sanitize_credential_value(custom_api_key)
        if self.provider_name == "custom":
            validate_api_key(custom_api_key, "custom")
        else:
            validate_api_key(api_key, self.provider_name)

        self.provider: EmbeddingProvider
        if self.provider_name == "gemini":
            if not api_key:
                raise ValueError("API key required for Gemini provider")
            model = model or "gemini-embedding-001"
            self.provider = GeminiEmbeddingProvider(api_key, model)
        elif self.provider_name == "openai":
            if not api_key:
                raise ValueError("API key required for OpenAI provider")
            model = model or "text-embedding-3-small"
            self.provider = OpenAIEmbeddingProvider(api_key, model)
        elif self.provider_name == "cohere":
            if not api_key:
                raise ValueError("API key required for Cohere provider")
            model = model or "embed-v4.0"
            self.provider = CohereEmbeddingProvider(api_key, model, output_dimension=1024)
        elif self.provider_name == "local":
            model = model or "all-MiniLM-L6-v2"
            self.provider = LocalEmbeddingProvider(model)
        elif self.provider_name == "custom":
            if not custom_endpoint:
                raise ValueError("Endpoint URL required for custom embedding provider")
            if not model:
                raise ValueError("Model name required for custom embedding provider")
            self.provider = CustomEmbeddingProvider(
                base_url=custom_endpoint,
                model=model,
                dimension=custom_dimension,
                api_key=custom_api_key,
            )
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def generate_embedding(self, text: str) -> List[float]:
        return self.provider.generate_embedding(text)

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 100
    ) -> List[List[float]]:
        return self.provider.generate_batch_embeddings(texts, batch_size)

    def get_dimension(self) -> int:
        return self.provider.get_dimension()

    def verify_dimension(self) -> tuple[bool, int]:
        """Generate a test embedding and verify the actual dimension matches.

        Returns ``(matches, actual_dimension)``.
        """
        try:
            test_vector = self.generate_embedding("test")
            actual = len(test_vector)
            expected = self.get_dimension()
            return actual == expected, actual
        except Exception as e:
            logger.warning("Dimension verification failed: %s", e)
            return True, self.get_dimension()  # Assume correct if API unreachable


def create_track_text(track_data: dict, audio_features: Optional[dict] = None) -> str:
    title = track_data.get("title", "Unknown")
    artist = track_data.get("artist", "Unknown Artist")
    album = track_data.get("album", "Unknown Album")
    genres = track_data.get("genre", "")
    year = track_data.get("year", "")
    tags = track_data.get("tags", "")
    environments = track_data.get("environments", "")
    instruments = track_data.get("instruments", "")

    text = f"{title} by {artist} from {album}"
    if genres:
        text += f" - {genres}"
    if year:
        text += f" ({year})"
    if tags:
        text += f" | tags: {tags}"
    if environments:
        text += f" | environments: {environments}"
    if instruments:
        text += f" | instruments: {instruments}"

    # MusicBrainz genres and recording type
    mb_genres = track_data.get("musicbrainz_genres", "")
    recording_type = track_data.get("recording_type", "")
    if mb_genres:
        text += f" | mb-genres: {mb_genres}"
    if recording_type:
        text += f" | type: {recording_type}"

    if audio_features:
        audio_parts: List[str] = []
        if audio_features.get("tempo"):
            bpm = audio_features["tempo"]
            pace = "slow" if bpm < 90 else "medium" if bpm < 130 else "fast"
            audio_parts.append(f"{int(bpm)} bpm ({pace})")
        if audio_features.get("key") and audio_features.get("scale"):
            audio_parts.append(f"{audio_features['key']} {audio_features['scale']}")
        if audio_features.get("energy_level"):
            audio_parts.append(f"{audio_features['energy_level']} energy")
        if audio_features.get("danceability") is not None:
            d = audio_features["danceability"]
            label = "very danceable" if d > 0.7 else "danceable" if d > 0.4 else "not danceable"
            audio_parts.append(label)
        if audio_parts:
            text += " | audio: " + ", ".join(audio_parts)

    return text


def embed_track(
    track_data: dict,
    generator: EmbeddingGenerator,
    audio_features: Optional[dict] = None,
) -> List[float]:
    text = create_track_text(track_data, audio_features)
    return generator.generate_embedding(text)


def embed_all_tracks(
    tracks: List[dict],
    generator: EmbeddingGenerator,
    batch_size: int = 100,
    audio_features_map: Optional[Dict[int, dict]] = None,
) -> List[List[float]]:
    texts = []
    for track in tracks:
        af = None
        if audio_features_map:
            track_id = track.get("id")
            if track_id is not None:
                af = audio_features_map.get(int(track_id))
        texts.append(create_track_text(track, af))
    return generator.generate_batch_embeddings(texts, batch_size)
